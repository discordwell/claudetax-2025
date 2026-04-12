"""FFFF (Free File Fillable Forms) entry-map builder.

For returns that pass the ``ffff_limits`` compatibility check, the
taxpayer can file for free by typing every Form 1040 / schedule line
into the freefillableforms.com UI. This module translates a computed
``CanonicalReturn`` into a field-by-field transcript of every entry the
user must make — form by form, line by line, in the order the FFFF UI
presents them.

It does NOT render a PDF. The output is a list of
``FFFFEntry(form, line, value, description, note)`` objects wrapped in a
frozen ``FFFFEntryMap`` dataclass with two serializers:

* ``to_text()`` — human-readable transcript a user can follow
  keystroke-by-keystroke.
* ``to_json()`` — machine-readable JSON for downstream tools.

Design notes
------------
* **No tax recomputation.** Layer 1 reuses the numbers already on the
  schedule-specific field dataclasses (``Form1040Fields``,
  ``ScheduleAFields``, ``ScheduleBFields``, ``ScheduleCFields``,
  ``ScheduleSEFields``). If those numbers are wrong, the renderers are
  wrong in the same way — a deliberate single-source-of-truth choice.
* **Form naming.** FFFF groups entries by on-screen form. We mirror that
  with a canonical ``form`` string per entry:

    - ``"1040"``           — primary Form 1040 lines
    - ``"1040-SA"``        — Schedule A (itemized deductions)
    - ``"1040-SB"``        — Schedule B (interest & dividends)
    - ``"1040-SC-<idx>"``  — per-business Schedule C (1-based index)
    - ``"1040-SSE-<idx>"`` — per-business Schedule SE (1-based index)

  The index suffix mirrors the per-business ordering in
  ``canonical_return.schedules_c``. It is 1-based to match how the FFFF
  UI numbers additional copies.
* **Zero suppression.** Zero-valued money lines are still emitted when
  they're on the "must transcribe" list for Form 1040 (1a, 1z, 2b, 9,
  10, 11, 12, 15, 16, 22, 24, 25a, 25d, 33, 34, 37). Schedule A/B/C/SE
  lines with zero are emitted to preserve line-by-line parity with the
  on-screen form. This matches how a human user fills the FFFF UI —
  they fill every line they see, zero or not, because FFFF is not
  smart enough to infer which lines matter.
* **No Schedule D / Form 8949 / Form 6251 yet.** Wave-6 agents 2 and 3
  are building those renderers in parallel. This module imports them
  lazily inside guarded ``try`` blocks so a missing renderer degrades
  gracefully to "skipped" rather than a hard import error. When those
  renderers land, the optional-import blocks can be deleted and the
  builder will start emitting their entries automatically.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from skill.scripts.models import CanonicalReturn, FilingStatus
from skill.scripts.output.form_1040 import (
    Form1040Fields,
    compute_form_1040_fields,
)
from skill.scripts.output.schedule_a import (
    ScheduleAFields,
    compute_schedule_a_fields,
)
from skill.scripts.output.schedule_b import (
    ScheduleBFields,
    compute_schedule_b_fields,
    schedule_b_required,
)
from skill.scripts.output.schedule_c import (
    ScheduleCFields,
    compute_schedule_c_fields,
)
from skill.scripts.output.schedule_se import (
    ScheduleSEFields,
    compute_schedule_se_fields,
    schedule_se_required,
)
from skill.scripts.output.schedule_e import (
    ScheduleEFields,
    ScheduleEPropertyFields,
    compute_schedule_e_fields,
)
from skill.scripts.output.schedule_1 import (
    Schedule1Fields,
    compute_schedule_1_fields,
    schedule_1_required,
)
from skill.scripts.output.schedule_2 import (
    Schedule2Fields,
    compute_schedule_2_fields,
    schedule_2_required,
)
from skill.scripts.output.schedule_3 import (
    Schedule3Fields,
    compute_schedule_3_fields,
    schedule_3_required,
)
from skill.scripts.output.form_2441 import (
    Form2441Fields,
    compute_form_2441_fields,
)
from skill.scripts.output.form_8863 import (
    Form8863Fields,
    compute_form_8863_fields,
)
from skill.scripts.output.form_8962 import (
    Form8962Fields,
    compute_form_8962_fields,
)
from skill.scripts.output.form_8606 import (
    Form8606Fields,
    compute_form_8606_fields,
)
from skill.scripts.output.form_4797 import (
    Form4797Fields,
    compute_form_4797_fields,
    form_4797_required,
)


_ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_money(value: Decimal) -> str:
    """Format a Decimal as a plain ``"65,000.00"`` string with commas.

    Unlike the AcroForm renderers (which suppress zeros to leave widgets
    blank), this transcript always prints the number because the human
    typing into FFFF needs to see ``0`` next to every line they ought to
    enter — even if that value is "0".
    """
    q = value.quantize(Decimal("0.01"))
    # str formatting with commas + 2 decimals — works for negative values.
    return f"{q:,.2f}"


def _format_filing_status(status: FilingStatus) -> str:
    return {
        FilingStatus.SINGLE: "Single",
        FilingStatus.MFJ: "Married filing jointly",
        FilingStatus.MFS: "Married filing separately",
        FilingStatus.HOH: "Head of household",
        FilingStatus.QSS: "Qualifying surviving spouse",
    }.get(status, status.value)


# ---------------------------------------------------------------------------
# FFFFEntry / FFFFEntryMap dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FFFFEntry:
    """A single field the taxpayer must type into freefillableforms.com.

    Attributes
    ----------
    form
        Canonical form identifier. See module docstring for the list.
    line
        Line label as the form prints it (e.g. ``"1a"``, ``"12"``,
        ``"27a"``). Header / filing-status rows use descriptive labels
        like ``"header.name"`` or ``"filing_status"`` so downstream
        serializers can group them.
    value
        Formatted value the user should type. Money is formatted via
        :func:`_format_money`; strings are passed through unchanged.
    description
        Human-readable label for the line, taken from the form.
    note
        Optional cautionary guidance (e.g. "this figure assumes
        standard deduction; toggle in interview if itemizing"). ``None``
        for the usual case.
    """

    form: str
    line: str
    value: str
    description: str
    note: str | None = None


@dataclass(frozen=True)
class FFFFEntryMap:
    """Immutable collection of every FFFF entry for a return.

    Produced by :func:`build_ffff_entry_map`. Two serializers:

    * :meth:`to_text` — a human-readable transcript grouped by form.
    * :meth:`to_json` — a JSON string with the full entry list + a
      small metadata header.
    """

    entries: tuple[FFFFEntry, ...]
    taxpayer_name: str = ""
    tax_year: int = 0
    filing_status: str = ""

    def to_text(self) -> str:
        """Render a human-readable transcript.

        Groups entries by ``form`` in the order they appear in
        ``entries`` (first-seen-wins for ordering). Each group starts
        with a heading, then rows of ``Line <line> — <description>:
        <value>``, with notes inset on a following line.

        The output is plain ASCII so copy-paste into a browser never
        mangles currency symbols or accented text.
        """
        lines: list[str] = []

        # Header block
        lines.append("=" * 72)
        lines.append(f" FFFF Entry Transcript - Tax Year {self.tax_year}")
        if self.taxpayer_name:
            lines.append(f" Taxpayer: {self.taxpayer_name}")
        if self.filing_status:
            lines.append(f" Filing status: {self.filing_status}")
        lines.append("=" * 72)
        lines.append("")
        lines.append(
            "Type each value below into the matching line on "
            "freefillableforms.com."
        )
        lines.append(
            "Zero-dollar lines are still listed so you know which fields to "
            "touch (type 0 or leave blank per the UI)."
        )
        lines.append("")

        # Group by form, preserving first-seen order
        form_order: list[str] = []
        grouped: dict[str, list[FFFFEntry]] = {}
        for entry in self.entries:
            if entry.form not in grouped:
                form_order.append(entry.form)
                grouped[entry.form] = []
            grouped[entry.form].append(entry)

        for form in form_order:
            heading = self._heading_for_form(form)
            lines.append("-" * 72)
            lines.append(f" {heading}")
            lines.append("-" * 72)
            for entry in grouped[form]:
                lines.append(
                    f"  Line {entry.line:<6} {entry.description}: {entry.value}"
                )
                if entry.note:
                    lines.append(f"      note: {entry.note}")
            lines.append("")

        return "\n".join(lines)

    def to_json(self) -> str:
        """Render a JSON string of the full entry list.

        The top-level object has::

            {
              "tax_year": 2025,
              "taxpayer_name": "Alex Doe",
              "filing_status": "Single",
              "entries": [
                {"form": "1040", "line": "1a", "value": "65,000.00",
                 "description": "Total W-2 box 1 wages", "note": null},
                ...
              ]
            }
        """
        payload = {
            "tax_year": self.tax_year,
            "taxpayer_name": self.taxpayer_name,
            "filing_status": self.filing_status,
            "entries": [
                {
                    "form": e.form,
                    "line": e.line,
                    "value": e.value,
                    "description": e.description,
                    "note": e.note,
                }
                for e in self.entries
            ],
        }
        return json.dumps(payload, indent=2)

    @staticmethod
    def _heading_for_form(form: str) -> str:
        """Pretty heading for a form identifier."""
        if form == "1040":
            return "Form 1040"
        if form == "1040-SA":
            return "Schedule A (Itemized Deductions)"
        if form == "1040-SB":
            return "Schedule B (Interest and Ordinary Dividends)"
        if form.startswith("1040-SC-"):
            idx = form.rsplit("-", 1)[-1]
            return f"Schedule C #{idx} (Profit or Loss From Business)"
        if form.startswith("1040-SSE-"):
            idx = form.rsplit("-", 1)[-1]
            return f"Schedule SE #{idx} (Self-Employment Tax)"
        if form.startswith("1040-SE-"):
            idx = form.rsplit("-", 1)[-1]
            return f"Schedule E #{idx} (Supplemental Income and Loss)"
        if form == "1040-S1":
            return "Schedule 1 (Additional Income and Adjustments)"
        if form == "1040-S2":
            return "Schedule 2 (Additional Taxes)"
        if form == "1040-S3":
            return "Schedule 3 (Additional Credits and Payments)"
        if form == "2441":
            return "Form 2441 (Child and Dependent Care Expenses)"
        if form == "8863":
            return "Form 8863 (Education Credits)"
        if form == "8962":
            return "Form 8962 (Premium Tax Credit)"
        if form == "8606":
            return "Form 8606 (Nondeductible IRAs)"
        if form == "4797":
            return "Form 4797 (Sales of Business Property)"
        return form


# ---------------------------------------------------------------------------
# Builders — one per form
# ---------------------------------------------------------------------------


def _form_1040_entries(
    return_: CanonicalReturn, fields: Form1040Fields
) -> list[FFFFEntry]:
    """Emit Form 1040 entries in FFFF UI order (header → ... → line 37)."""
    entries: list[FFFFEntry] = []

    # -- Header ---------------------------------------------------------
    entries.append(
        FFFFEntry(
            form="1040",
            line="header.name",
            value=fields.taxpayer_name,
            description="Your first name and last name",
        )
    )
    entries.append(
        FFFFEntry(
            form="1040",
            line="header.ssn",
            value=return_.taxpayer.ssn,
            description="Your social security number",
        )
    )
    if fields.spouse_name:
        entries.append(
            FFFFEntry(
                form="1040",
                line="header.spouse_name",
                value=fields.spouse_name,
                description="Spouse's first name and last name",
            )
        )
        if return_.spouse is not None:
            entries.append(
                FFFFEntry(
                    form="1040",
                    line="header.spouse_ssn",
                    value=return_.spouse.ssn,
                    description="Spouse's social security number",
                )
            )

    entries.append(
        FFFFEntry(
            form="1040",
            line="filing_status",
            value=_format_filing_status(return_.filing_status),
            description="Filing status (check exactly one box)",
        )
    )

    addr = return_.address
    addr_value = f"{addr.street1}, {addr.city}, {addr.state} {addr.zip}"
    entries.append(
        FFFFEntry(
            form="1040",
            line="header.address",
            value=addr_value,
            description="Home address (number, street, city, state, ZIP)",
        )
    )

    # -- Income ---------------------------------------------------------
    income_lines: list[tuple[str, Decimal, str, str | None]] = [
        ("1a", fields.line_1a_total_w2_box1,
         "Total W-2 box 1 wages", None),
        ("1z", fields.line_1z_total_wages,
         "Total wages (lines 1a through 1h)", None),
        ("2a", fields.line_2a_tax_exempt_interest,
         "Tax-exempt interest",
         "Leave blank if none"),
        ("2b", fields.line_2b_taxable_interest,
         "Taxable interest", None),
        ("3a", fields.line_3a_qualified_dividends,
         "Qualified dividends", None),
        ("3b", fields.line_3b_ordinary_dividends,
         "Ordinary dividends", None),
        ("4a", fields.line_4a_ira_distributions,
         "IRA distributions", None),
        ("4b", fields.line_4b_ira_taxable_amount,
         "IRA distributions - taxable amount", None),
        ("5a", fields.line_5a_pensions_and_annuities,
         "Pensions and annuities", None),
        ("5b", fields.line_5b_pensions_taxable_amount,
         "Pensions - taxable amount", None),
        ("6a", fields.line_6a_social_security_benefits,
         "Social Security benefits", None),
        ("6b", fields.line_6b_ss_taxable_amount,
         "Social Security - taxable amount",
         "SS-benefits worksheet not yet automated; verify"),
        ("7", fields.line_7_capital_gain_or_loss,
         "Capital gain or (loss)",
         "If Schedule D is required, that's a separate FFFF form"),
        ("8", fields.line_8_additional_income_from_sch_1,
         "Additional income from Schedule 1", None),
        ("9", fields.line_9_total_income,
         "Total income", None),
        ("10", fields.line_10_adjustments_from_sch_1,
         "Adjustments to income from Schedule 1", None),
        ("11", fields.line_11_adjusted_gross_income,
         "Adjusted gross income (AGI)", None),
        ("12", fields.line_12_standard_or_itemized_deduction,
         "Standard or itemized deduction",
         None if return_.itemize_deductions
         else "This figure is the standard deduction; toggle in interview if "
              "itemizing"),
        ("13", fields.line_13_qbi_deduction,
         "QBI deduction from Form 8995 or 8995-A",
         "QBI deduction is not yet automated; verify"),
        ("14", fields.line_14_sum_12_13,
         "Add lines 12 and 13", None),
        ("15", fields.line_15_taxable_income,
         "Taxable income", None),
    ]

    # -- Tax & credits --------------------------------------------------
    tax_lines: list[tuple[str, Decimal, str, str | None]] = [
        ("16", fields.line_16_tax,
         "Tax (from tax table / computation)", None),
        ("17", fields.line_17_amount_from_sch_2_line_3,
         "Amount from Schedule 2 line 3", None),
        ("18", fields.line_18_sum_16_17,
         "Add lines 16 and 17", None),
        ("19", fields.line_19_child_tax_credit_and_odc,
         "Child tax credit / credit for other dependents", None),
        ("20", fields.line_20_amount_from_sch_3_line_8,
         "Amount from Schedule 3 line 8", None),
        ("21", fields.line_21_sum_19_20,
         "Add lines 19 and 20", None),
        ("22", fields.line_22_subtract_21_from_18,
         "Subtract line 21 from line 18", None),
        ("23", fields.line_23_other_taxes_from_sch_2_line_21,
         "Other taxes from Schedule 2 line 21", None),
        ("24", fields.line_24_total_tax,
         "Total tax", None),
    ]

    # -- Payments -------------------------------------------------------
    payment_lines: list[tuple[str, Decimal, str, str | None]] = [
        ("25a", fields.line_25a_w2_withholding,
         "Federal income tax withheld from W-2s", None),
        ("25b", fields.line_25b_1099_withholding,
         "Federal income tax withheld from 1099s", None),
        ("25c", fields.line_25c_other_withholding,
         "Other federal withholding", None),
        ("25d", fields.line_25d_total_withholding,
         "Total federal income tax withheld", None),
        ("26", fields.line_26_estimated_and_prior_year_applied,
         "2025 estimated tax payments and prior-year overpayment applied",
         None),
        ("27", fields.line_27_earned_income_credit,
         "Earned income credit (EIC)", None),
        ("28", fields.line_28_additional_child_tax_credit,
         "Additional child tax credit", None),
        ("29", fields.line_29_american_opportunity_credit_refundable,
         "American Opportunity credit - refundable", None),
        ("31", fields.line_31_amount_from_sch_3_line_15,
         "Amount from Schedule 3 line 15", None),
        ("32", fields.line_32_sum_27_through_31,
         "Add lines 27, 28, 29, and 31", None),
        ("33", fields.line_33_total_payments,
         "Total payments", None),
    ]

    # -- Refund / owed --------------------------------------------------
    refund_lines: list[tuple[str, Decimal, str, str | None]] = [
        ("34", fields.line_34_overpayment,
         "Overpayment (line 33 - line 24 if line 33 > line 24)", None),
        ("35a", fields.line_35a_refund_requested,
         "Refund amount requested",
         "FFFF direct-deposit fields (routing/account) come next"),
        ("37", fields.line_37_amount_you_owe,
         "Amount you owe", None),
    ]

    for line_set in (income_lines, tax_lines, payment_lines, refund_lines):
        for line, value, desc, note in line_set:
            entries.append(
                FFFFEntry(
                    form="1040",
                    line=line,
                    value=_format_money(value),
                    description=desc,
                    note=note,
                )
            )

    return entries


def _schedule_a_entries(fields: ScheduleAFields) -> list[FFFFEntry]:
    """Emit Schedule A entries in on-screen order (lines 1-17)."""
    entries: list[FFFFEntry] = []
    form = "1040-SA"

    rows: list[tuple[str, Decimal, str, str | None]] = [
        ("1", fields.line_1_medical_and_dental,
         "Medical and dental expenses (raw total)", None),
        ("2", fields.line_2_agi,
         "AGI from Form 1040 line 11", None),
        ("3", fields.line_3_agi_floor,
         "Multiply line 2 by 7.5%", None),
        ("4", fields.line_4_medical_deductible,
         "Medical deductible (line 1 - line 3, not less than 0)", None),
        ("5a", fields.line_5a_state_and_local_taxes,
         "State and local taxes (income OR sales tax - check box if sales)",
         "Check the line-5a sales-tax box if electing sales tax"
         if fields.line_5a_elected_sales_tax else None),
        ("5b", fields.line_5b_real_estate_taxes,
         "Real estate taxes", None),
        ("5c", fields.line_5c_personal_property_taxes,
         "Personal property taxes", None),
        ("5d", fields.line_5d_salt_subtotal,
         "Add lines 5a, 5b, 5c (pre-cap SALT)", None),
        ("5e", fields.line_5e_salt_capped,
         "Smaller of line 5d or SALT cap", None),
        ("6", fields.line_6_other_taxes,
         "Other taxes", None),
        ("7", fields.line_7_total_taxes,
         "Total taxes (line 5e + line 6)", None),
        ("8a", fields.line_8a_home_mortgage_interest_on_1098,
         "Home mortgage interest & points (Form 1098)", None),
        ("8b", fields.line_8b_home_mortgage_interest_not_on_1098,
         "Home mortgage interest NOT on 1098", None),
        ("8c", fields.line_8c_points_not_on_1098,
         "Points not reported on 1098", None),
        ("8e", fields.line_8e_total_home_mortgage_interest,
         "Add lines 8a through 8c", None),
        ("9", fields.line_9_investment_interest,
         "Investment interest (Form 4952 if required)", None),
        ("10", fields.line_10_total_interest,
         "Total interest", None),
        ("11", fields.line_11_gifts_cash,
         "Gifts to charity - cash or check", None),
        ("12", fields.line_12_gifts_noncash,
         "Gifts to charity - other than cash", None),
        ("13", fields.line_13_carryover,
         "Carryover from prior year", None),
        ("14", fields.line_14_total_gifts,
         "Total gifts (lines 11 + 12 + 13)", None),
        ("15", fields.line_15_casualty_and_theft,
         "Casualty and theft losses (federal disaster only)", None),
        ("16", fields.line_16_other_itemized,
         "Other itemized deductions", None),
        ("17", fields.line_17_total_itemized,
         "Total itemized deductions (flows to Form 1040 line 12)", None),
    ]

    for line, value, desc, note in rows:
        entries.append(
            FFFFEntry(
                form=form,
                line=line,
                value=_format_money(value),
                description=desc,
                note=note,
            )
        )

    return entries


def _schedule_b_entries(fields: ScheduleBFields) -> list[FFFFEntry]:
    """Emit Schedule B entries including repeating payer rows."""
    entries: list[FFFFEntry] = []
    form = "1040-SB"

    # Part I payer rows
    for i, row in enumerate(fields.part_i_line_1_rows, start=1):
        entries.append(
            FFFFEntry(
                form=form,
                line=f"1.{i}.payer",
                value=row.payer_name,
                description=f"Part I line 1 row {i} - payer name",
            )
        )
        entries.append(
            FFFFEntry(
                form=form,
                line=f"1.{i}.amount",
                value=_format_money(row.amount),
                description=f"Part I line 1 row {i} - amount",
            )
        )

    entries.append(
        FFFFEntry(
            form=form,
            line="2",
            value=_format_money(fields.part_i_line_2_total_interest),
            description="Add the amounts on line 1",
        )
    )
    entries.append(
        FFFFEntry(
            form=form,
            line="3",
            value=_format_money(fields.part_i_line_3_excludable_savings_bond_interest),
            description="Excludable savings bond interest (Form 8815)",
            note="0 unless you claimed Series EE/I bond exclusion on Form 8815",
        )
    )
    entries.append(
        FFFFEntry(
            form=form,
            line="4",
            value=_format_money(fields.part_i_line_4_taxable_interest),
            description="Taxable interest (flows to Form 1040 line 2b)",
        )
    )

    # Part II payer rows
    for i, row in enumerate(fields.part_ii_line_5_rows, start=1):
        entries.append(
            FFFFEntry(
                form=form,
                line=f"5.{i}.payer",
                value=row.payer_name,
                description=f"Part II line 5 row {i} - payer name",
            )
        )
        entries.append(
            FFFFEntry(
                form=form,
                line=f"5.{i}.amount",
                value=_format_money(row.amount),
                description=f"Part II line 5 row {i} - amount",
            )
        )

    entries.append(
        FFFFEntry(
            form=form,
            line="6",
            value=_format_money(fields.part_ii_line_6_total_ordinary_dividends),
            description="Add the amounts on line 5 (flows to Form 1040 line 3b)",
        )
    )

    # Part III — foreign flags
    entries.append(
        FFFFEntry(
            form=form,
            line="7a",
            value="Yes" if fields.part_iii_line_7a_foreign_account else "No",
            description="Foreign financial account (FinCEN 114 trigger)",
        )
    )
    if fields.part_iii_line_7b_fincen114_country:
        entries.append(
            FFFFEntry(
                form=form,
                line="7b",
                value=fields.part_iii_line_7b_fincen114_country,
                description="Foreign account country",
            )
        )
    entries.append(
        FFFFEntry(
            form=form,
            line="8",
            value="Yes" if fields.part_iii_line_8_foreign_trust else "No",
            description="Received distribution from or grantor of foreign trust",
        )
    )

    return entries


def _schedule_c_entries(
    fields: ScheduleCFields, idx: int
) -> list[FFFFEntry]:
    """Emit Schedule C entries for a single business (1-based index)."""
    entries: list[FFFFEntry] = []
    form = f"1040-SC-{idx}"

    # -- Header ---------------------------------------------------------
    header_rows: list[tuple[str, str, str]] = [
        ("A", fields.line_a_principal_business_or_profession,
         "Principal business or profession"),
        ("B", fields.line_b_principal_business_code or "",
         "Principal business code (NAICS)"),
        ("C", fields.line_c_business_name,
         "Business name"),
        ("D", fields.line_d_ein or "",
         "Employer ID number (EIN)"),
        ("E", fields.line_e_business_address,
         "Business address"),
        ("F", fields.line_f_accounting_method,
         "Accounting method (cash/accrual/other)"),
    ]
    for line, value, desc in header_rows:
        entries.append(
            FFFFEntry(form=form, line=line, value=value, description=desc)
        )
    entries.append(
        FFFFEntry(
            form=form,
            line="G",
            value="Yes" if fields.line_g_material_participation else "No",
            description="Materially participated",
        )
    )

    # -- Part I / II / Part V -----------------------------------------
    numeric_rows: list[tuple[str, Decimal, str]] = [
        ("1", fields.line_1_gross_receipts, "Gross receipts"),
        ("2", fields.line_2_returns_and_allowances, "Returns and allowances"),
        ("3", fields.line_3_net_receipts, "Net receipts (line 1 - line 2)"),
        ("4", fields.line_4_cost_of_goods_sold, "Cost of goods sold"),
        ("5", fields.line_5_gross_profit, "Gross profit"),
        ("6", fields.line_6_other_income, "Other income"),
        ("7", fields.line_7_gross_income, "Gross income (line 5 + line 6)"),
        ("8", fields.line_8_advertising, "Advertising"),
        ("9", fields.line_9_car_and_truck, "Car and truck expenses"),
        ("10", fields.line_10_commissions_and_fees, "Commissions and fees"),
        ("11", fields.line_11_contract_labor, "Contract labor"),
        ("12", fields.line_12_depletion, "Depletion"),
        ("13", fields.line_13_depreciation_section_179,
         "Depreciation / section 179"),
        ("14", fields.line_14_employee_benefit_programs,
         "Employee benefit programs"),
        ("15", fields.line_15_insurance_not_health,
         "Insurance (other than health)"),
        ("16a", fields.line_16a_mortgage_interest, "Mortgage interest"),
        ("16b", fields.line_16b_other_interest, "Other interest"),
        ("17", fields.line_17_legal_and_professional,
         "Legal and professional services"),
        ("18", fields.line_18_office_expense, "Office expense"),
        ("19", fields.line_19_pension_and_profit_sharing,
         "Pension and profit-sharing plans"),
        ("20a", fields.line_20a_rent_vehicles_machinery_equipment,
         "Rent - vehicles/machinery/equipment"),
        ("20b", fields.line_20b_rent_other_business_property,
         "Rent - other business property"),
        ("21", fields.line_21_repairs_and_maintenance,
         "Repairs and maintenance"),
        ("22", fields.line_22_supplies, "Supplies"),
        ("23", fields.line_23_taxes_and_licenses, "Taxes and licenses"),
        ("24a", fields.line_24a_travel, "Travel"),
        ("24b", fields.line_24b_meals_50pct_deductible,
         "Meals (50% deductible)"),
        ("25", fields.line_25_utilities, "Utilities"),
        ("26", fields.line_26_wages_less_employment_credits,
         "Wages (less employment credits)"),
        ("27a", fields.line_27a_other_expenses, "Other expenses"),
        ("28", fields.line_28_total_expenses, "Total expenses"),
        ("29", fields.line_29_tentative_profit_or_loss,
         "Tentative profit or (loss)"),
        ("30", fields.line_30_home_office_expense,
         "Home office expense (Form 8829)"),
        ("31", fields.line_31_net_profit_or_loss,
         "Net profit or (loss) - flows to Schedule 1 / Schedule SE"),
    ]
    for line, value, desc in numeric_rows:
        entries.append(
            FFFFEntry(
                form=form,
                line=line,
                value=_format_money(value),
                description=desc,
            )
        )

    # Part V other-expenses detail
    for i, (label, amount) in enumerate(fields.part_v_other_expenses, start=1):
        entries.append(
            FFFFEntry(
                form=form,
                line=f"V.{i}",
                value=_format_money(amount),
                description=f"Part V row {i} - {label}",
            )
        )
    if fields.part_v_other_expenses:
        entries.append(
            FFFFEntry(
                form=form,
                line="V.total",
                value=_format_money(fields.part_v_total),
                description="Part V total (flows to line 27a)",
            )
        )

    return entries


def _schedule_se_entries(
    fields: ScheduleSEFields, idx: int
) -> list[FFFFEntry]:
    """Emit Schedule SE entries (Part I lines 1a-13)."""
    form = f"1040-SSE-{idx}"
    rows: list[tuple[str, Decimal, str]] = [
        ("1a", fields.line_1a_net_farm_profit, "Net farm profit"),
        ("1b", fields.line_1b_ss_farm_optional, "Farm SS optional method"),
        ("2", fields.line_2_net_profit_schedule_c,
         "Net profit from Schedule C"),
        ("3", fields.line_3_combine_1a_1b_2, "Combine lines 1a, 1b, and 2"),
        ("4a", fields.line_4a_net_earnings_times_9235, "Line 3 x 92.35%"),
        ("4b", fields.line_4b_optional_methods, "Optional methods"),
        ("4c", fields.line_4c_combine_4a_4b, "Combine 4a + 4b"),
        ("5a", fields.line_5a_church_employee_income, "Church employee income"),
        ("5b", fields.line_5b_church_times_9235, "Line 5a x 92.35%"),
        ("6", fields.line_6_net_earnings_from_se,
         "Net earnings from self-employment"),
        ("7", fields.line_7_ss_wage_base, "Max SS wage base"),
        ("8a", fields.line_8a_w2_ss_wages_and_tips, "W-2 SS wages + tips"),
        ("8b", fields.line_8b_unreported_tips, "Unreported tips (Form 4137)"),
        ("8c", fields.line_8c_wages_8919, "Wages from Form 8919"),
        ("8d", fields.line_8d_sum_8a_8b_8c, "Add 8a + 8b + 8c"),
        ("9", fields.line_9_subtract_8d_from_7,
         "Subtract line 8d from line 7"),
        ("10", fields.line_10_ss_portion,
         "Social Security portion (min(6,9) x 12.4%)"),
        ("11", fields.line_11_medicare_portion,
         "Medicare portion (line 6 x 2.9%)"),
        ("12", fields.line_12_se_tax, "Self-employment tax (line 10 + 11)"),
        ("13", fields.line_13_deductible_half_se_tax,
         "Deduction for 1/2 SE tax (flows to Schedule 1)"),
    ]
    return [
        FFFFEntry(
            form=form,
            line=line,
            value=_format_money(value),
            description=desc,
        )
        for line, value, desc in rows
    ]


def _schedule_e_entries(
    fields: ScheduleEFields, idx: int
) -> list[FFFFEntry]:
    """Emit Schedule E entries for a single schedule page (1-based index).

    Covers per-property rents/expenses/net and Part I totals.
    """
    form = f"1040-SE-{idx}"
    entries: list[FFFFEntry] = []

    for pi, prop in enumerate(fields.properties, start=1):
        col = chr(64 + pi)  # A, B, C
        entries.append(
            FFFFEntry(
                form=form,
                line=f"1{col.lower()}",
                value=prop.address,
                description=f"Property {col} address",
            )
        )
        entries.append(
            FFFFEntry(
                form=form,
                line=f"1{col.lower()}.type",
                value=prop.property_type,
                description=f"Property {col} type",
            )
        )
        entries.append(
            FFFFEntry(
                form=form,
                line=f"3{col.lower()}",
                value=_format_money(prop.line_3_rents_received),
                description=f"Property {col} rents received",
            )
        )
        # Key expense lines
        expense_rows: list[tuple[str, Decimal, str]] = [
            (f"5{col.lower()}", prop.line_5_advertising, "Advertising"),
            (f"7{col.lower()}", prop.line_7_cleaning_and_maintenance, "Cleaning and maintenance"),
            (f"9{col.lower()}", prop.line_9_insurance, "Insurance"),
            (f"12{col.lower()}", prop.line_12_mortgage_interest_to_banks, "Mortgage interest"),
            (f"14{col.lower()}", prop.line_14_repairs, "Repairs"),
            (f"16{col.lower()}", prop.line_16_taxes, "Taxes"),
            (f"17{col.lower()}", prop.line_17_utilities, "Utilities"),
            (f"18{col.lower()}", prop.line_18_depreciation, "Depreciation"),
            (f"19{col.lower()}", prop.line_19_other_expenses, "Other expenses"),
            (f"20{col.lower()}", prop.line_20_total_expenses, "Total expenses"),
            (f"21{col.lower()}", prop.line_21_net_income_or_loss, "Net income or (loss)"),
        ]
        for line, value, desc in expense_rows:
            entries.append(
                FFFFEntry(
                    form=form,
                    line=line,
                    value=_format_money(value),
                    description=f"Property {col} {desc}",
                )
            )

    # Part I summary
    summary_rows: list[tuple[str, Decimal, str]] = [
        ("23a", fields.line_23a_total_rental_income, "Total rental/real estate income"),
        ("23b", fields.line_23b_total_rental_losses, "Total rental/real estate losses"),
        ("24", fields.line_24_income, "Income"),
        ("25", fields.line_25_losses, "Losses"),
        ("26", fields.line_26_total_rental_royalty_income_or_loss,
         "Total rental real estate and royalty income or (loss)"),
    ]
    for line, value, desc in summary_rows:
        entries.append(
            FFFFEntry(
                form=form,
                line=line,
                value=_format_money(value),
                description=desc,
            )
        )

    return entries


def _schedule_1_entries(fields: Schedule1Fields) -> list[FFFFEntry]:
    """Emit Schedule 1 entries for key lines a human would type into FFFF."""
    form = "1040-S1"
    rows: list[tuple[str, Decimal, str, str | None]] = [
        ("3", fields.line_3_business_income,
         "Business income or (loss) from Schedule C", None),
        ("4", fields.line_4_other_gains,
         "Other gains or (losses) from Form 4797", None),
        ("5", fields.line_5_rental_real_estate,
         "Rental real estate, royalties, partnerships, S corps", None),
        ("7", fields.line_7_unemployment,
         "Unemployment compensation", None),
        ("10", fields.line_10_total_additional_income,
         "Total additional income", None),
        ("15", fields.line_15_deductible_se_tax,
         "Deductible part of self-employment tax", None),
        ("25", fields.line_25_obbba_adjustments,
         "OBBBA adjustments (senior deduction)", None),
        ("26", fields.line_26_total_adjustments,
         "Total adjustments to income", None),
        ("net", fields.schedule_1_net,
         "Schedule 1 net (line 10 - line 26) -> Form 1040 line 8", None),
    ]
    return [
        FFFFEntry(
            form=form,
            line=line,
            value=_format_money(value),
            description=desc,
            note=note,
        )
        for line, value, desc, note in rows
    ]


def _schedule_2_entries(fields: Schedule2Fields) -> list[FFFFEntry]:
    """Emit Schedule 2 entries (AMT, SE tax, total)."""
    form = "1040-S2"
    rows: list[tuple[str, Decimal, str]] = [
        ("1", fields.line_1_amt, "AMT (Form 6251)"),
        ("2", fields.line_2_excess_aptc,
         "Excess advance premium tax credit repayment"),
        ("3", fields.line_3_part_i_total,
         "Part I total (lines 1 + 2) -> Form 1040 line 17"),
        ("6", fields.line_6_se_tax,
         "Self-employment tax (Schedule SE)"),
        ("10", fields.line_10_additional_medicare,
         "Additional Medicare tax"),
        ("11", fields.line_11_niit,
         "Net investment income tax"),
        ("21", fields.line_21_part_ii_total,
         "Total additional taxes -> Form 1040 line 23"),
    ]
    return [
        FFFFEntry(
            form=form,
            line=line,
            value=_format_money(value),
            description=desc,
        )
        for line, value, desc in rows
    ]


def _schedule_3_entries(fields: Schedule3Fields) -> list[FFFFEntry]:
    """Emit Schedule 3 entries (education credits, child care, totals)."""
    form = "1040-S3"
    rows: list[tuple[str, Decimal, str]] = [
        ("1", fields.line_1_foreign_tax_credit, "Foreign tax credit"),
        ("2", fields.line_2_dependent_care_credit,
         "Child and dependent care credit (Form 2441)"),
        ("3", fields.line_3_education_credits,
         "Education credits (Form 8863, nonrefundable)"),
        ("7", fields.line_7_total_other_credits,
         "Total other credits (sum of lines 1-6)"),
        ("8", fields.line_8_total_nonrefundable_credits,
         "Total nonrefundable credits -> Form 1040 line 20"),
        ("9", fields.line_9_net_premium_tax_credit,
         "Net premium tax credit (Form 8962)"),
        ("14", fields.line_14_aotc_refundable,
         "AOTC refundable portion (Form 8863)"),
        ("15", fields.line_15_total_other_payments_and_refundable,
         "Total other payments and refundable credits -> Form 1040 line 31"),
    ]
    return [
        FFFFEntry(
            form=form,
            line=line,
            value=_format_money(value),
            description=desc,
        )
        for line, value, desc in rows
    ]


def _form_2441_entries(fields: Form2441Fields) -> list[FFFFEntry]:
    """Emit Form 2441 entries (qualifying expenses, credit rate, credit)."""
    form = "2441"
    entries: list[FFFFEntry] = []

    entries.append(
        FFFFEntry(
            form=form,
            line="3",
            value=str(fields.line_3_num_qualifying_persons),
            description="Number of qualifying persons",
        )
    )
    rows: list[tuple[str, Decimal, str]] = [
        ("4", fields.line_4_qualified_expenses,
         "Qualified expenses (after $3k/$6k cap)"),
        ("5", fields.line_5_earned_income_taxpayer,
         "Earned income - taxpayer"),
        ("6", fields.line_6_earned_income_spouse,
         "Earned income - spouse"),
        ("7", fields.line_7_smallest_of_4_5_6,
         "Smallest of lines 4, 5, 6"),
    ]
    for line, value, desc in rows:
        entries.append(
            FFFFEntry(form=form, line=line,
                      value=_format_money(value), description=desc)
        )
    entries.append(
        FFFFEntry(
            form=form,
            line="9",
            value=f"{fields.line_9_credit_rate_pct}%",
            description="Credit rate percentage",
        )
    )
    entries.append(
        FFFFEntry(
            form=form,
            line="10",
            value=_format_money(fields.line_10_credit),
            description="Credit amount -> Schedule 3 line 2",
        )
    )
    return entries


def _form_8863_entries(fields: Form8863Fields) -> list[FFFFEntry]:
    """Emit Form 8863 entries (per-student AOTC/LLC, totals)."""
    form = "8863"
    entries: list[FFFFEntry] = []

    for i, stu in enumerate(fields.students, start=1):
        entries.append(
            FFFFEntry(
                form=form,
                line=f"stu.{i}.name",
                value=stu.name,
                description=f"Student {i} name",
            )
        )
        entries.append(
            FFFFEntry(
                form=form,
                line=f"stu.{i}.ssn",
                value=stu.ssn,
                description=f"Student {i} SSN",
            )
        )
        entries.append(
            FFFFEntry(
                form=form,
                line=f"stu.{i}.expenses",
                value=_format_money(stu.qualified_expenses),
                description=f"Student {i} qualified expenses",
            )
        )
        entries.append(
            FFFFEntry(
                form=form,
                line=f"stu.{i}.type",
                value=stu.credit_type,
                description=f"Student {i} credit type (AOTC or LLC)",
            )
        )
        if stu.credit_type == "AOTC":
            entries.append(
                FFFFEntry(
                    form=form,
                    line=f"stu.{i}.credit",
                    value=_format_money(stu.phased_credit),
                    description=f"Student {i} AOTC (after phase-out)",
                )
            )

    # Totals
    total_rows: list[tuple[str, Decimal, str]] = [
        ("nonref", fields.total_nonrefundable,
         "Total nonrefundable credits (AOTC 60% + LLC) -> Schedule 3 line 3"),
        ("ref", fields.total_refundable,
         "Total refundable credits (AOTC 40%) -> Schedule 3 line 14"),
    ]
    for line, value, desc in total_rows:
        entries.append(
            FFFFEntry(form=form, line=line,
                      value=_format_money(value), description=desc)
        )
    return entries


def _form_8962_entries(fields: Form8962Fields) -> list[FFFFEntry]:
    """Emit Form 8962 entries (monthly PTC, net PTC, excess repayment)."""
    form = "8962"
    entries: list[FFFFEntry] = []

    # Part I key lines
    entries.append(
        FFFFEntry(
            form=form,
            line="1",
            value=str(fields.line_1_tax_family_size),
            description="Tax family size",
        )
    )
    part_i_rows: list[tuple[str, Decimal, str]] = [
        ("4", fields.line_4_household_income, "Household income"),
        ("5", fields.line_5_fpl, "Federal poverty level"),
        ("8a", fields.line_8a_annual_contribution, "Annual contribution amount"),
    ]
    for line, value, desc in part_i_rows:
        entries.append(
            FFFFEntry(form=form, line=line,
                      value=_format_money(value), description=desc)
        )

    # Monthly rows
    month_names = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    for row in fields.monthly_rows:
        m = month_names[row.month - 1] if 1 <= row.month <= 12 else f"M{row.month}"
        entries.append(
            FFFFEntry(
                form=form,
                line=f"{m}.ptc",
                value=_format_money(row.max_ptc),
                description=f"{m} max premium tax credit",
            )
        )
        entries.append(
            FFFFEntry(
                form=form,
                line=f"{m}.adv",
                value=_format_money(row.advance_ptc),
                description=f"{m} advance PTC received",
            )
        )

    # Part IV key lines
    part_iv_rows: list[tuple[str, Decimal, str]] = [
        ("24", fields.line_24_net_ptc,
         "Net premium tax credit (refundable)"),
        ("27", fields.line_27_excess_advance_ptc,
         "Excess advance PTC"),
        ("29", fields.line_29_repayment,
         "Repayment of excess advance PTC (additional tax)"),
    ]
    for line, value, desc in part_iv_rows:
        entries.append(
            FFFFEntry(form=form, line=line,
                      value=_format_money(value), description=desc)
        )
    return entries


def _form_8606_entries(fields: Form8606Fields) -> list[FFFFEntry]:
    """Emit Form 8606 entries (nondeductible contributions, basis, nontaxable)."""
    form = "8606"
    rows: list[tuple[str, Decimal, str]] = [
        ("1", fields.line_1_nondeductible_contributions,
         "Nondeductible contributions for the year"),
        ("2", fields.line_2_prior_year_basis,
         "Prior year basis (from last year's 8606 line 14)"),
        ("3", fields.line_3_add_1_and_2,
         "Add lines 1 and 2"),
        ("5", fields.line_5_subtract_4_from_3,
         "Subtract line 4 from line 3"),
        ("6", fields.line_6_ira_value_year_end,
         "Value of all traditional IRAs at year end"),
        ("7", fields.line_7_distributions,
         "Distributions from traditional IRAs"),
        ("8", fields.line_8_roth_conversions,
         "Net conversions to Roth IRA"),
        ("11", fields.line_11_nontaxable_distributions,
         "Nontaxable portion of distributions"),
        ("13", fields.line_13_taxable_distributions,
         "Taxable portion of distributions"),
        ("14", fields.line_14_remaining_basis,
         "Remaining basis carryforward"),
        ("16", fields.line_16_taxable_conversion,
         "Taxable conversion amount (Part II)"),
    ]
    return [
        FFFFEntry(
            form=form,
            line=line,
            value=_format_money(value),
            description=desc,
        )
        for line, value, desc in rows
    ]


def _form_4797_entries(fields: Form4797Fields) -> list[FFFFEntry]:
    """Emit Form 4797 entries (sales of business property)."""
    form = "4797"
    entries: list[FFFFEntry] = []

    # Part I sales
    for r in fields.part_i_sales:
        entries.append(FFFFEntry(
            form=form,
            line="I",
            value=f"{r.sale.description}: {_format_money(r.section_1231_gain_or_loss)}",
            description="Part I - Section 1231 gain or (loss)",
        ))
    entries.append(FFFFEntry(
        form=form,
        line="I-net",
        value=_format_money(fields.part_i_net_gain_or_loss),
        description="Net section 1231 gain or (loss)",
    ))

    # Part II ordinary
    if fields.part_ii_ordinary_gain_or_loss != _ZERO:
        for r in fields.part_ii_sales:
            entries.append(FFFFEntry(
                form=form,
                line="II",
                value=f"{r.sale.description}: {_format_money(r.ordinary_gain)}",
                description="Part II - Ordinary gain (depreciation recapture)",
            ))
        entries.append(FFFFEntry(
            form=form,
            line="II-net",
            value=_format_money(fields.part_ii_ordinary_gain_or_loss),
            description="Net ordinary gain or (loss)",
        ))

    # Part III unrecaptured 1250
    if fields.part_iii_total_unrecaptured_1250_gain != _ZERO:
        for r in fields.part_iii_sales:
            entries.append(FFFFEntry(
                form=form,
                line="III",
                value=f"{r.sale.description}: {_format_money(r.unrecaptured_1250_gain)}",
                description="Part III - Unrecaptured section 1250 gain",
            ))
        entries.append(FFFFEntry(
            form=form,
            line="III-total",
            value=_format_money(fields.part_iii_total_unrecaptured_1250_gain),
            description="Total unrecaptured section 1250 gain",
        ))

    # Flow-through lines
    entries.append(FFFFEntry(
        form=form,
        line="S1-L4",
        value=_format_money(fields.schedule_1_line_4),
        description="-> Schedule 1 line 4 (other gains/losses)",
    ))
    if fields.schedule_d_line_11 != _ZERO:
        entries.append(FFFFEntry(
            form=form,
            line="SD-L11",
            value=_format_money(fields.schedule_d_line_11),
            description="-> Schedule D line 11 (section 1231 gain)",
        ))
    if fields.schedule_d_line_19 != _ZERO:
        entries.append(FFFFEntry(
            form=form,
            line="SD-L19",
            value=_format_money(fields.schedule_d_line_19),
            description="-> Schedule D line 19 (unrecaptured section 1250 gain)",
        ))

    return entries


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def build_ffff_entry_map(canonical_return: CanonicalReturn) -> FFFFEntryMap:
    """Build a full FFFF entry map from a computed ``CanonicalReturn``.

    Emit order (matches the FFFF UI left-nav):

    1. Form 1040 header → income → adjustments → tax → credits →
       payments → refund/owed
    2. Schedule A (if ``itemize_deductions`` is True)
    3. Schedule B (if ``schedule_b_required`` returns True)
    4. Schedule C (one block per ``schedules_c`` entry)
    5. Schedule SE (one block per SE-required ``schedules_c`` entry —
       gating on ``schedule_se_required``)
    6. (Future) Schedule D / Form 8949 / Form 6251 — gated on the
       optional presence of wave-6 agent 2 / agent 3 renderers. Today
       these blocks are skipped because the renderers haven't landed.

    The input ``canonical_return`` SHOULD have been passed through
    ``skill.scripts.calc.engine.compute`` so that the Form 1040 lines
    have meaningful values; otherwise the transcript will show zeros.
    """
    entries: list[FFFFEntry] = []

    # -- Form 1040 ------------------------------------------------------
    f1040 = compute_form_1040_fields(canonical_return)
    entries.extend(_form_1040_entries(canonical_return, f1040))

    # -- Schedule A -----------------------------------------------------
    if canonical_return.itemize_deductions and canonical_return.itemized is not None:
        sa = compute_schedule_a_fields(canonical_return)
        entries.extend(_schedule_a_entries(sa))

    # -- Schedule B -----------------------------------------------------
    if schedule_b_required(canonical_return):
        sb = compute_schedule_b_fields(canonical_return)
        entries.extend(_schedule_b_entries(sb))

    # -- Schedule C (per business) -------------------------------------
    for idx, sc in enumerate(canonical_return.schedules_c, start=1):
        sc_fields = compute_schedule_c_fields(sc)
        entries.extend(_schedule_c_entries(sc_fields, idx))

    # -- Schedule SE (per SE-required business) ------------------------
    # Schedule SE is per-person, not per-business — the canonical skill
    # aggregates every taxpayer-owned Schedule C's net profit onto one
    # Schedule SE. To match how FFFF groups SE (one copy per person with
    # an SE liability), we emit a single ``1040-SSE-1`` block if SE is
    # required at all. Spouse-side SE is a follow-up.
    if schedule_se_required(canonical_return):
        sse = compute_schedule_se_fields(canonical_return)
        entries.extend(_schedule_se_entries(sse, idx=1))

    # -- Schedule E (per schedule page) --------------------------------
    for idx_e, _se in enumerate(canonical_return.schedules_e, start=1):
        se_fields = compute_schedule_e_fields(canonical_return, schedule_idx=idx_e - 1)
        entries.extend(_schedule_e_entries(se_fields, idx_e))

    # -- Schedule 1 (Additional Income and Adjustments) ----------------
    if schedule_1_required(canonical_return):
        s1 = compute_schedule_1_fields(canonical_return)
        entries.extend(_schedule_1_entries(s1))

    # -- Schedule 2 (Additional Taxes) ---------------------------------
    if schedule_2_required(canonical_return):
        s2 = compute_schedule_2_fields(canonical_return)
        entries.extend(_schedule_2_entries(s2))

    # -- Schedule 3 (Additional Credits and Payments) ------------------
    if schedule_3_required(canonical_return):
        s3 = compute_schedule_3_fields(canonical_return)
        entries.extend(_schedule_3_entries(s3))

    # -- Form 2441 (Child and Dependent Care Expenses) -----------------
    if canonical_return.dependent_care is not None:
        f2441 = compute_form_2441_fields(canonical_return)
        if f2441.line_10_credit > _ZERO or f2441.line_4_qualified_expenses > _ZERO:
            entries.extend(_form_2441_entries(f2441))

    # -- Form 8863 (Education Credits) ---------------------------------
    if canonical_return.education is not None and canonical_return.education.students:
        f8863 = compute_form_8863_fields(canonical_return)
        entries.extend(_form_8863_entries(f8863))

    # -- Form 8962 (Premium Tax Credit) --------------------------------
    if canonical_return.forms_1095_a:
        f8962 = compute_form_8962_fields(canonical_return)
        entries.extend(_form_8962_entries(f8962))

    # -- Form 8606 (Nondeductible IRAs) --------------------------------
    if canonical_return.ira_info is not None:
        f8606 = compute_form_8606_fields(canonical_return)
        entries.extend(_form_8606_entries(f8606))

    # -- Form 4797 (Sales of Business Property) -------------------------
    if form_4797_required(canonical_return):
        f4797 = compute_form_4797_fields(canonical_return)
        entries.extend(_form_4797_entries(f4797))

    # -- Optional wave-6 forms (Schedule D, Form 6251) -----------------
    # Wave-6 agents 2 and 3 are building these renderers in parallel.
    # We import them lazily so a missing module degrades to "skip" and
    # doesn't break the builder.
    try:
        from skill.scripts.output import schedule_d as _sd  # type: ignore  # noqa: F401
        # When agent 2 lands, replace this pass with a real call to
        # ``_schedule_d_entries`` that mirrors the schedule-a pattern.
        pass
    except ImportError:
        pass

    try:
        from skill.scripts.output import form_6251 as _f6251  # type: ignore  # noqa: F401
        pass
    except ImportError:
        pass

    # -- Metadata ------------------------------------------------------
    taxpayer_name = (
        f"{canonical_return.taxpayer.first_name} "
        f"{canonical_return.taxpayer.last_name}"
    )
    filing_status = _format_filing_status(canonical_return.filing_status)

    return FFFFEntryMap(
        entries=tuple(entries),
        taxpayer_name=taxpayer_name,
        tax_year=canonical_return.tax_year,
        filing_status=filing_status,
    )


__all__ = [
    "FFFFEntry",
    "FFFFEntryMap",
    "build_ffff_entry_map",
]
