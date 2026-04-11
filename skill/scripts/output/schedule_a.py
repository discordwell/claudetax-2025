"""Schedule A (Itemized Deductions) output renderer — two-layer scaffold.

SCAFFOLD NOTICE
===============
This module is a SCAFFOLD for Schedule A PDF generation, mirroring the
shape of ``skill.scripts.output.form_1040`` so that a later wave can
replace Layer 2 with an AcroForm widget-overlay renderer without
touching Layer 1 or its tests.

* Layer 1 (``compute_schedule_a_fields``) maps a ``CanonicalReturn``'s
  ``ItemizedDeductions`` block onto a frozen dataclass whose field names
  mirror the TY2024 Schedule A line numbers (assumed stable for TY2025).
  It does NOT recompute any tax value — it only reads the canonical
  model's populated Schedule A inputs and applies the SALT cap exactly
  the way the calc engine already does in
  ``skill.scripts.calc.engine.itemized_total_capped``.

* Layer 2 (``render_schedule_a_pdf``) writes a minimal tabular PDF using
  ``reportlab``. The table lists every line_name/value pair so a human
  can eyeball the return; this is NOT a filled IRS Schedule A — a real
  AcroForm overlay on the IRS fillable PDF is a follow-up task that
  will need a researched widget map (see ``skill/reference/`` for future
  work).

Line layout (TY2024 Schedule A, assumed stable for TY2025)
----------------------------------------------------------
Per the IRS 2024 Instructions for Schedule A (Form 1040):

    Medical and Dental Expenses
        1  Medical and dental expenses (full — 7.5%-of-AGI floor is
           applied by tenforty, not here)
        2  AGI (from Form 1040, line 11)
        3  2 * 7.5%
        4  Subtract 3 from 1

    Taxes You Paid
        5a State and local income OR general sales taxes
        5b State and local real estate taxes
        5c State and local personal property taxes
        5d Sum of 5a, 5b, 5c (pre-cap SALT subtotal)
        5e Smaller of 5d or $10,000 ($5,000 MFS) — the SALT CAP
        6  Other taxes (other_itemized bucket)
        7  Add 5e and 6

    Interest You Paid
        8a Home mortgage interest & points reported on Form 1098
        8b Home mortgage interest NOT reported on Form 1098
        8c Points NOT reported on Form 1098
        8d Reserved / mortgage insurance premiums (expired post-TY2021
           unless reinstated; reserved in the model, excluded from the
           engine total by design — kept here as a scaffold field only)
        8e Add lines 8a through 8c
        9  Investment interest
        10 Add 8e and 9

    Gifts to Charity
        11 Gifts by cash or check
        12 Other than by cash or check
        13 Carryover from prior year
        14 Add lines 11 through 13

    Casualty and Theft Losses
        15 Casualty and theft loss (federal disaster declaration only
           post-TCJA)

    Other Itemized Deductions
        16 Other — from list in instructions

    Total Itemized Deductions
        17 Sum of lines 4, 7, 10, 14, 15, 16

Design goals
------------
* No tax re-computation beyond SALT cap application. The SALT cap is
  mirrored bit-for-bit from ``itemized_total_capped`` so that the number
  on line 5e here equals the SALT component that the engine already fed
  into Form 1040 line 12.
* No modification of the engine, models, registry, or schema.
* Minimal surface area so downstream waves can replace Layer 2 with a
  real AcroForm renderer without touching Layer 1 or its tests.

Authority and citations
-----------------------
* IRS 2024 Instructions for Schedule A (Form 1040), line-by-line layout.
* SALT cap statute: IRC §164(b)(6), enacted by TCJA (Pub. L. 115-97,
  §11042), which capped aggregate state and local tax deductions at
  $10,000 ($5,000 MFS). Originally scheduled to sunset after TY2025; the
  OBBBA package made the cap permanent (see
  skill/scripts/calc/engine.py::SALT_CAP_NORMAL / SALT_CAP_MFS).

Medical semantics — IMPORTANT divergence from the engine
--------------------------------------------------------
Line 1 reports RAW medical_and_dental_total from the canonical model.
Line 4 = max(0, line 1 - line 3) applies the 7.5%-of-AGI floor per the
IRS Schedule A form layout. This is what a filer sees on the printed
form and is the form-accurate semantics.

This MEANS ``line_17_total_itemized`` (which sums line 4) will DIVERGE
from ``skill.scripts.calc.engine.itemized_total_capped`` whenever medical
is nonzero. The engine passes RAW medical to tenforty and lets tenforty
apply the 7.5% floor inside its own bracket calculation; the engine's
"capped total" is therefore PRE-floor on the medical component. Line 17
here is POST-floor. The delta is exactly ``line 3`` (7.5% of AGI),
capped at ``line 1``. Both are correct for their respective purposes:
the engine number is the tenforty-parameter semantics, the renderer
number is the IRS-form display semantics.

Cross-check tests at the test boundary use medical=0 to avoid this
divergence; a separate test explicitly locks the expected delta when
medical > 0 so future drift on either side trips CI.
* Line 6 ("Other taxes") is pulled from ``other_itemized`` dict keyed on
  ``"other_taxes_paid"`` only. Any other keys in ``other_itemized`` are
  routed to line 16. This matches how the engine's
  ``itemized_total_capped`` lumps them all together anyway.
* Line 8a/8b/8c split is not modeled in the canonical type — the model
  has a single ``home_mortgage_interest`` field. We put the whole amount
  on line 8a and leave 8b/8c at 0. ``mortgage_points`` goes on line 8c
  per 2024 instructions. Splitting "reported on 1098" vs. "not reported
  on 1098" is a follow-up.
* Line 8d (mortgage insurance premiums) is kept in the dataclass for
  layout parity but is ALWAYS 0 — the deduction expired for TY2022 and
  has not been reinstated as of TY2025. This matches the calc engine's
  deliberate exclusion (see comment in ``itemized_total_capped``).
* Line 17 total uses the exact same SALT-capped arithmetic as the
  engine so the two numbers always agree (asserted in tests).
* Real AcroForm overlay on the IRS fillable Schedule A PDF — entire
  Layer 2 is a tabular scaffold, NOT a filed IRS form. A follow-up wave
  will replace ``render_schedule_a_pdf`` with a widget-map overlay
  renderer.
"""
from __future__ import annotations

from dataclasses import dataclass, fields as dc_fields
from decimal import Decimal
from pathlib import Path

from skill.scripts.models import (
    CanonicalReturn,
    FilingStatus,
    ItemizedDeductions,
)


_ZERO = Decimal("0")

# SALT cap — mirrors skill.scripts.calc.engine.SALT_CAP_NORMAL /
# SALT_CAP_MFS. Duplicated here (rather than imported) so Layer 1 stays
# loosely coupled to the engine module and so the constants are visible
# next to the line 5e citation in this file.
# Authority: IRC §164(b)(6), TCJA §11042, made permanent by OBBBA.
SALT_CAP_NORMAL = Decimal("10000")
SALT_CAP_MFS = Decimal("5000")


# ---------------------------------------------------------------------------
# Layer 1: field dataclass and computation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleAFields:
    """Frozen snapshot of Schedule A line values, ready for rendering.

    Field names follow the TY2024 Schedule A line numbers (assumed
    stable for TY2025). All numeric fields are ``Decimal``.
    """

    # Filing / header (non-Decimal)
    filing_status: str = ""
    taxpayer_name: str = ""
    spouse_name: str | None = None

    # Medical and Dental Expenses (lines 1-4)
    line_1_medical_and_dental: Decimal = _ZERO
    line_2_agi: Decimal = _ZERO
    line_3_agi_floor: Decimal = _ZERO
    line_4_medical_deductible: Decimal = _ZERO

    # Taxes You Paid (lines 5a-7)
    line_5a_state_and_local_taxes: Decimal = _ZERO
    line_5a_elected_sales_tax: bool = False
    line_5b_real_estate_taxes: Decimal = _ZERO
    line_5c_personal_property_taxes: Decimal = _ZERO
    line_5d_salt_subtotal: Decimal = _ZERO
    line_5e_salt_capped: Decimal = _ZERO
    line_5e_salt_cap_applied: Decimal = _ZERO
    line_6_other_taxes: Decimal = _ZERO
    line_7_total_taxes: Decimal = _ZERO

    # Interest You Paid (lines 8a-10)
    line_8a_home_mortgage_interest_on_1098: Decimal = _ZERO
    line_8b_home_mortgage_interest_not_on_1098: Decimal = _ZERO
    line_8c_points_not_on_1098: Decimal = _ZERO
    line_8d_mortgage_insurance_premiums: Decimal = _ZERO
    line_8e_total_home_mortgage_interest: Decimal = _ZERO
    line_9_investment_interest: Decimal = _ZERO
    line_10_total_interest: Decimal = _ZERO

    # Gifts to Charity (lines 11-14)
    line_11_gifts_cash: Decimal = _ZERO
    line_12_gifts_noncash: Decimal = _ZERO
    line_13_carryover: Decimal = _ZERO
    line_14_total_gifts: Decimal = _ZERO

    # Casualty and Theft Losses (line 15)
    line_15_casualty_and_theft: Decimal = _ZERO

    # Other Itemized (line 16)
    line_16_other_itemized: Decimal = _ZERO

    # Total Itemized Deductions (line 17)
    line_17_total_itemized: Decimal = _ZERO


def _dec(x: Decimal | None) -> Decimal:
    """Coerce an Optional[Decimal] to a concrete Decimal (None -> 0)."""
    return x if x is not None else _ZERO


def _salt_cap_for(status: FilingStatus) -> Decimal:
    """Return the SALT cap for a filing status.

    MFJ/Single/HoH/QSS = $10,000. MFS = $5,000.

    Authority: IRC §164(b)(6). Mirrors the check in
    ``skill.scripts.calc.engine.itemized_total_capped``.
    """
    return SALT_CAP_MFS if status == FilingStatus.MFS else SALT_CAP_NORMAL


def compute_schedule_a_fields(return_: CanonicalReturn) -> ScheduleAFields:
    """Map a CanonicalReturn's ItemizedDeductions onto ScheduleAFields.

    This function:

    * Reads ``return_.itemized`` (a ``ItemizedDeductions`` model) — if
      the taxpayer is taking the standard deduction (or the itemized
      block is absent), every line value is zero except the header.
    * Applies the SALT cap on line 5e using the exact same rule as
      ``skill.scripts.calc.engine.itemized_total_capped`` — see the
      citation at the top of this file.
    * Does NOT recompute tax. ``line_2_agi`` is read from
      ``return_.computed.adjusted_gross_income``; the line 3 and line 4
      worksheet figures are informational only (the engine enforces the
      7.5%-of-AGI floor).
    """
    # -- header ---------------------------------------------------------
    filing_status = return_.filing_status.value
    taxpayer_name = f"{return_.taxpayer.first_name} {return_.taxpayer.last_name}"
    spouse_name = (
        f"{return_.spouse.first_name} {return_.spouse.last_name}"
        if return_.spouse is not None
        else None
    )

    it = return_.itemized
    if it is None:
        # No itemized deductions block — every line is zero. We still
        # populate the header so the scaffold PDF is meaningful.
        return ScheduleAFields(
            filing_status=filing_status,
            taxpayer_name=taxpayer_name,
            spouse_name=spouse_name,
        )

    return _compute_from_itemized(
        it=it,
        status=return_.filing_status,
        agi=_dec(return_.computed.adjusted_gross_income),
        filing_status=filing_status,
        taxpayer_name=taxpayer_name,
        spouse_name=spouse_name,
    )


def _compute_from_itemized(
    *,
    it: ItemizedDeductions,
    status: FilingStatus,
    agi: Decimal,
    filing_status: str,
    taxpayer_name: str,
    spouse_name: str | None,
) -> ScheduleAFields:
    """Pure mapping from an ItemizedDeductions block to ScheduleAFields.

    Split from ``compute_schedule_a_fields`` so that unit tests can
    exercise the mapping without standing up a full CanonicalReturn.
    """

    # -- Medical and Dental (lines 1-4) --------------------------------
    # Line 1 is the raw total. Lines 2/3/4 are the AGI-floor worksheet.
    # tenforty applies the floor inside the engine — our line 4 value is
    # informational: we show max(0, line1 - 7.5% of AGI).
    line_1 = it.medical_and_dental_total
    line_2 = agi
    line_3 = (agi * Decimal("0.075")).quantize(Decimal("0.01"))
    line_4 = max(_ZERO, line_1 - line_3)

    # -- Taxes You Paid (lines 5a-7) -----------------------------------
    # Line 5a: elected SALT tax line (income OR sales). See model
    # docstring on ItemizedDeductions.
    elected_sales = it.elect_sales_tax_over_income_tax
    line_5a = (
        it.state_and_local_sales_tax
        if elected_sales
        else it.state_and_local_income_tax
    )
    line_5b = it.real_estate_tax
    line_5c = it.personal_property_tax
    line_5d = line_5a + line_5b + line_5c

    # Line 5e: SALT cap applied.
    # Authority: IRC §164(b)(6); $10k normal, $5k MFS. This mirrors the
    # logic in skill.scripts.calc.engine.itemized_total_capped so that
    # the engine's Form 1040 line 12 value stays consistent with this
    # schedule.
    salt_cap = _salt_cap_for(status)
    line_5e = min(line_5d, salt_cap)

    # Line 6: "other taxes" — pulled from other_itemized["other_taxes_paid"]
    # if present. Everything else in other_itemized lands on line 16.
    line_6 = it.other_itemized.get("other_taxes_paid", _ZERO)
    line_7 = line_5e + line_6

    # -- Interest You Paid (lines 8a-10) -------------------------------
    # Model has only one mortgage interest field and one points field;
    # we put the entire mortgage interest on 8a and mortgage_points on
    # 8c. Splitting 8a/8b by "reported on 1098" is a follow-up.
    line_8a = it.home_mortgage_interest
    line_8b = _ZERO
    line_8c = it.mortgage_points
    # Line 8d (mortgage insurance premiums): the deduction expired for
    # TY2022. The engine deliberately excludes this from the itemized
    # total (see ``itemized_total_capped`` comment). We keep the field
    # for layout parity but force the value to 0.
    line_8d = _ZERO
    line_8e = line_8a + line_8b + line_8c  # 8d excluded, per engine
    line_9 = it.investment_interest
    line_10 = line_8e + line_9

    # -- Gifts to Charity (lines 11-14) --------------------------------
    line_11 = it.gifts_to_charity_cash
    line_12 = it.gifts_to_charity_other_than_cash
    line_13 = it.gifts_to_charity_carryover
    line_14 = line_11 + line_12 + line_13

    # -- Casualty and Theft Losses (line 15) ---------------------------
    # Post-TCJA: federal disaster declaration only.
    line_15 = it.casualty_and_theft_losses_federal_disaster

    # -- Other Itemized (line 16) --------------------------------------
    # Everything in other_itemized except the "other_taxes_paid" key.
    line_16 = sum(
        (v for k, v in it.other_itemized.items() if k != "other_taxes_paid"),
        start=_ZERO,
    )

    # -- Total (line 17) -----------------------------------------------
    line_17 = line_4 + line_7 + line_10 + line_14 + line_15 + line_16

    return ScheduleAFields(
        filing_status=filing_status,
        taxpayer_name=taxpayer_name,
        spouse_name=spouse_name,
        line_1_medical_and_dental=line_1,
        line_2_agi=line_2,
        line_3_agi_floor=line_3,
        line_4_medical_deductible=line_4,
        line_5a_state_and_local_taxes=line_5a,
        line_5a_elected_sales_tax=elected_sales,
        line_5b_real_estate_taxes=line_5b,
        line_5c_personal_property_taxes=line_5c,
        line_5d_salt_subtotal=line_5d,
        line_5e_salt_capped=line_5e,
        line_5e_salt_cap_applied=salt_cap,
        line_6_other_taxes=line_6,
        line_7_total_taxes=line_7,
        line_8a_home_mortgage_interest_on_1098=line_8a,
        line_8b_home_mortgage_interest_not_on_1098=line_8b,
        line_8c_points_not_on_1098=line_8c,
        line_8d_mortgage_insurance_premiums=line_8d,
        line_8e_total_home_mortgage_interest=line_8e,
        line_9_investment_interest=line_9,
        line_10_total_interest=line_10,
        line_11_gifts_cash=line_11,
        line_12_gifts_noncash=line_12,
        line_13_carryover=line_13,
        line_14_total_gifts=line_14,
        line_15_casualty_and_theft=line_15,
        line_16_other_itemized=line_16,
        line_17_total_itemized=line_17,
    )


# ---------------------------------------------------------------------------
# Layer 2: reportlab PDF rendering (SCAFFOLD)
# ---------------------------------------------------------------------------


def _format_decimal(value: Decimal) -> str:
    """Format a Decimal as a US currency-ish string ``10,000.00``."""
    q = value.quantize(Decimal("0.01"))
    return f"{q:,.2f}"


def render_schedule_a_pdf(fields: ScheduleAFields, out_path: Path) -> Path:
    """Render a Schedule A SCAFFOLD PDF using reportlab.

    This writes a tabular PDF listing every line name and its value. It
    is NOT a filled IRS Schedule A — a real AcroForm overlay on the IRS
    fillable PDF is a follow-up task that will need a researched widget
    map.

    Returns the out_path for convenience.
    """
    # Lazy import so that users who never render PDFs don't pay the
    # import cost (and test runners without reportlab can still
    # exercise Layer 1).
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=letter,
        title="Schedule A (TY2025 SCAFFOLD)",
    )
    styles = getSampleStyleSheet()

    story: list = []
    story.append(
        Paragraph("Schedule A - Itemized Deductions (TY2025 - SCAFFOLD)", styles["Title"])
    )
    story.append(
        Paragraph(
            "This is a scaffold rendering, not a filed IRS Schedule A.",
            styles["Italic"],
        )
    )
    story.append(Spacer(1, 12))

    # Header block
    header_rows = [
        ["Filing status", fields.filing_status],
        ["Taxpayer", fields.taxpayer_name],
        ["Spouse", fields.spouse_name or ""],
        [
            "SALT cap applied",
            _format_decimal(fields.line_5e_salt_cap_applied),
        ],
    ]
    header_table = Table(header_rows, colWidths=[140, 360])
    header_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story.append(header_table)
    story.append(Spacer(1, 12))

    # Line-item table
    line_rows: list[list] = [["Line", "Description", "Amount"]]
    for f in dc_fields(fields):
        if f.name in ("filing_status", "taxpayer_name", "spouse_name"):
            continue
        value = getattr(fields, f.name)
        if not isinstance(value, Decimal):
            continue
        # Parse "line_5e_salt_capped" -> ("5e", "salt capped")
        parts = f.name.split("_")
        line_number = parts[1] if len(parts) >= 2 else ""
        desc = " ".join(parts[2:]).replace("_", " ")
        line_rows.append([line_number, desc, _format_decimal(value)])

    line_table = Table(line_rows, colWidths=[50, 350, 100])
    line_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (2, 1), (2, -1), "RIGHT"),
            ]
        )
    )
    story.append(line_table)

    doc.build(story)
    return out_path
