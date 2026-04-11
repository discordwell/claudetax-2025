"""Schedule B (Interest and Ordinary Dividends) output renderer — two-layer scaffold.

SCAFFOLD NOTICE
===============
This module is a SCAFFOLD for IRS Schedule B (TY2024 layout, assumed
stable for TY2025). It mirrors the two-layer design of
``skill.scripts.output.form_1040``:

* Layer 1 (``compute_schedule_b_fields``) is a pure mapping from the
  canonical return's ``forms_1099_int`` and ``forms_1099_div`` lists onto
  the Schedule B structure. It DOES NOT recompute tax or re-derive
  anything the engine already produced — it just routes values onto
  Schedule B line names.

* Layer 2 (``render_schedule_b_pdf``) writes a minimal reportlab
  text-tabular PDF for human eyeballing. It is NOT a filled IRS Schedule
  B. Real AcroForm overlay on the IRS fillable PDF is a follow-up task.

Schedule B structure (TY2024 instructions)
------------------------------------------
Part I — Interest
  Line 1 : Payer / amount rows. Per the IRS instructions, each 1099-INT
           box 1 (interest) and box 3 (US savings bond & Treasury
           interest) contribution from the same payer is listed with
           the payer's name. We merge duplicate payer names into a
           single row per payer.
  Line 2 : Add the amounts on line 1.
  Line 3 : Excludable interest on series EE/I US savings bonds issued
           after 1989 from Form 8815. *** DEFERRED — left at 0. ***
  Line 4 : Line 2 minus line 3. This amount flows to Form 1040 line 2b.

Part II — Ordinary Dividends
  Line 5 : Payer / amount rows from 1099-DIV box 1a (ordinary
           dividends). Duplicate payer names are merged.
  Line 6 : Add the amounts on line 5. Flows to Form 1040 line 3b.

Part III — Foreign Accounts and Trusts
  Line 7a: At any time during 2025, did you have a financial interest
           in or signature authority over a foreign financial account?
  Line 7a (FinCEN 114 required?)
  Line 7b: Country name (if 7a is yes and FinCEN 114 is required).
  Line 8 : During 2025, did you receive a distribution from, or were
           you the grantor of, or transferor to, a foreign trust?

Simplifications loudly deferred (TODO for later waves)
------------------------------------------------------
* Line 3 (Form 8815 excludable savings bond interest) is always 0. The
  8815 interview / worksheet is not yet in the canonical model. Callers
  who need this should patch Layer 1 once the model exposes it.
* Part III foreign account / trust flags: **wired in CP8-D** to the
  canonical return fields ``has_foreign_financial_account_over_10k``,
  ``has_foreign_trust_transaction``, and ``foreign_account_countries``.
  ``schedule_b_required`` now correctly flags foreign-based filing
  triggers independent of the $1,500 interest/dividend threshold.
  Line 7b country renders the first entry of ``foreign_account_countries``;
  multi-country enumeration is handled by FinCEN Form 114 (FBAR), not
  Schedule B itself.
* Line 1 "nominee" adjustment lines (the IRS instructions allow a
  subtotal followed by a negative "nominee distribution" line) are not
  modeled. Nominee handling is a follow-up.
* The required-filing threshold is $1,500 per the 2024 IRS instructions
  ("You must file Schedule B (Form 1040) if any of the following
   apply ... (a) You had over $1,500 of taxable interest or ordinary
   dividends."). The threshold is applied strictly ``> 1500`` — exactly
  $1,500 does not require Schedule B.

Sources
-------
* IRS 2024 Instructions for Schedule B (Form 1040), "Who Must File" and
  Part I / Part II / Part III line instructions.
* IRS 2024 Form 1099-INT instructions — box 1 (interest income) and
  box 3 (US savings bond and Treasury interest) both land on Schedule B
  line 1.
* IRS 2024 Form 1099-DIV instructions — box 1a (total ordinary
  dividends) lands on Schedule B line 5.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from skill.scripts.models import CanonicalReturn


_ZERO = Decimal("0")

# IRS 2024 Schedule B filing threshold. See module docstring.
SCHEDULE_B_THRESHOLD = Decimal("1500")


# ---------------------------------------------------------------------------
# Layer 1: field dataclass and computation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleBPayerRow:
    """A single payer / amount row on Schedule B line 1 or line 5."""

    payer_name: str
    amount: Decimal


@dataclass(frozen=True)
class ScheduleBFields:
    """Frozen snapshot of Schedule B line values, ready for rendering.

    Field names follow the TY2024 Schedule B (Form 1040) line numbers
    (assumed stable for TY2025). All amounts are ``Decimal``.
    """

    # Header
    taxpayer_name: str = ""
    taxpayer_ssn: str = ""

    # Part I — Interest
    part_i_line_1_rows: tuple[ScheduleBPayerRow, ...] = ()
    part_i_line_2_total_interest: Decimal = _ZERO
    part_i_line_3_excludable_savings_bond_interest: Decimal = _ZERO
    part_i_line_4_taxable_interest: Decimal = _ZERO

    # Part II — Ordinary Dividends
    part_ii_line_5_rows: tuple[ScheduleBPayerRow, ...] = ()
    part_ii_line_6_total_ordinary_dividends: Decimal = _ZERO

    # Part III — Foreign Accounts and Trusts
    # All default False and carry a loud TODO — see module docstring.
    part_iii_line_7a_foreign_account: bool = False
    part_iii_line_7a_fincen114_required: bool = False
    part_iii_line_7b_fincen114_country: str = ""
    part_iii_line_8_foreign_trust: bool = False

    # Whether Schedule B is REQUIRED for this return.
    is_required: bool = False


def _dec(x: Decimal | None) -> Decimal:
    """Coerce an Optional[Decimal] to a concrete Decimal (None -> 0)."""
    return x if x is not None else _ZERO


def _merge_rows(
    pairs: list[tuple[str, Decimal]],
) -> tuple[ScheduleBPayerRow, ...]:
    """Merge (payer_name, amount) pairs by payer_name, preserving first-seen order.

    Zero-amount entries are dropped, so a payer whose contributions net
    to exactly zero is omitted entirely from the line list.
    """
    order: list[str] = []
    totals: dict[str, Decimal] = {}
    for name, amount in pairs:
        if name not in totals:
            order.append(name)
            totals[name] = _ZERO
        totals[name] = totals[name] + amount
    return tuple(
        ScheduleBPayerRow(payer_name=name, amount=totals[name])
        for name in order
        if totals[name] != _ZERO
    )


def compute_schedule_b_fields(return_: CanonicalReturn) -> ScheduleBFields:
    """Map a CanonicalReturn onto a ScheduleBFields dataclass.

    This is a pure, side-effect-free routing of the return's
    ``forms_1099_int`` and ``forms_1099_div`` lists onto Schedule B line
    names. It does NOT require the return to have been passed through
    ``skill.scripts.calc.engine.compute`` — Schedule B line 4 and line 6
    are derived directly from the canonical 1099 forms, not from
    ``ComputedTotals``.
    """
    # -- header ---------------------------------------------------------
    taxpayer_name = f"{return_.taxpayer.first_name} {return_.taxpayer.last_name}"
    taxpayer_ssn = return_.taxpayer.ssn or ""

    # -- Part I line 1 rows --------------------------------------------
    # Per the IRS 2024 Schedule B instructions, line 1 lists each payer
    # of taxable interest. 1099-INT box 1 (interest income) and box 3
    # (US savings bond and Treasury interest) are BOTH taxable interest
    # that lands on Schedule B line 1. We therefore sum box1 + box3 per
    # payer before listing.
    int_pairs: list[tuple[str, Decimal]] = []
    for f in return_.forms_1099_int:
        amount = _dec(f.box1_interest_income) + _dec(
            f.box3_us_savings_bond_and_treasury_interest
        )
        int_pairs.append((f.payer_name, amount))
    line_1_rows = _merge_rows(int_pairs)

    # -- Part I line 2 -------------------------------------------------
    line_2 = sum((row.amount for row in line_1_rows), start=_ZERO)

    # -- Part I line 3 -------------------------------------------------
    # TODO: Form 8815 excludable savings bond interest is not yet in the
    # canonical model. Always 0 for now.
    line_3 = _ZERO

    # -- Part I line 4 -------------------------------------------------
    line_4 = line_2 - line_3

    # -- Part II line 5 rows -------------------------------------------
    div_pairs: list[tuple[str, Decimal]] = []
    for f in return_.forms_1099_div:
        div_pairs.append((f.payer_name, _dec(f.box1a_ordinary_dividends)))
    line_5_rows = _merge_rows(div_pairs)

    # -- Part II line 6 ------------------------------------------------
    line_6 = sum((row.amount for row in line_5_rows), start=_ZERO)

    # -- Part III ------------------------------------------------------
    # CP8-D: canonical model now carries the Schedule B Part III flags.
    # Line 7a: "At any time during YYYY, did you have a financial
    # interest in or signature authority over a financial account
    # located in a foreign country?" — maps to
    # has_foreign_financial_account_over_10k.
    # Line 7a FinCEN 114 requirement: same flag (true => FBAR required).
    # Line 7b country: first country from foreign_account_countries
    # (renderer shows a single country; multi-country reporting is
    # deferred — FinCEN 114 itself handles the enumeration).
    # Line 8: maps to has_foreign_trust_transaction.
    line_7a_foreign_account = return_.has_foreign_financial_account_over_10k
    line_7a_fincen114_required = return_.has_foreign_financial_account_over_10k
    line_7b_country = (
        return_.foreign_account_countries[0]
        if return_.foreign_account_countries
        else ""
    )
    line_8_foreign_trust = return_.has_foreign_trust_transaction

    # -- Required? -----------------------------------------------------
    required = _is_schedule_b_required(
        total_interest=line_4,
        total_dividends=line_6,
        foreign_account=line_7a_foreign_account,
        foreign_trust=line_8_foreign_trust,
    )

    return ScheduleBFields(
        taxpayer_name=taxpayer_name,
        taxpayer_ssn=taxpayer_ssn,
        part_i_line_1_rows=line_1_rows,
        part_i_line_2_total_interest=line_2,
        part_i_line_3_excludable_savings_bond_interest=line_3,
        part_i_line_4_taxable_interest=line_4,
        part_ii_line_5_rows=line_5_rows,
        part_ii_line_6_total_ordinary_dividends=line_6,
        part_iii_line_7a_foreign_account=line_7a_foreign_account,
        part_iii_line_7a_fincen114_required=line_7a_fincen114_required,
        part_iii_line_7b_fincen114_country=line_7b_country,
        part_iii_line_8_foreign_trust=line_8_foreign_trust,
        is_required=required,
    )


def _is_schedule_b_required(
    total_interest: Decimal,
    total_dividends: Decimal,
    foreign_account: bool,
    foreign_trust: bool,
) -> bool:
    """Pure-argument form of the Schedule B must-file rule.

    Schedule B is required if EITHER:
      * taxable interest (line 4) exceeds $1,500, OR
      * ordinary dividends (line 6) exceeds $1,500, OR
      * any Part III foreign account / trust flag is True.

    The dollar threshold is strict (``> 1500``), per the IRS 2024
    instructions language "You had over $1,500 of ...". Exactly $1,500
    does NOT require Schedule B.

    Note: this is not an exhaustive list of every Schedule B trigger
    from the IRS instructions — it covers the three triggers the
    canonical model can currently observe. Triggers we do NOT model
    (nominee interest, accrued bond interest, seller-financed mortgage
    interest, excludable savings bond interest claim, etc.) are
    follow-ups.
    """
    if total_interest > SCHEDULE_B_THRESHOLD:
        return True
    if total_dividends > SCHEDULE_B_THRESHOLD:
        return True
    if foreign_account or foreign_trust:
        return True
    return False


def schedule_b_required(return_: CanonicalReturn) -> bool:
    """Public helper: is Schedule B REQUIRED for this canonical return?

    Intended to be called by the engine / output pipeline to decide
    whether to render Schedule B at all. Uses the same threshold rule
    as ``compute_schedule_b_fields``.
    """
    fields = compute_schedule_b_fields(return_)
    return fields.is_required


# ---------------------------------------------------------------------------
# Layer 2: reportlab PDF rendering (SCAFFOLD)
# ---------------------------------------------------------------------------


def _format_decimal(value: Decimal) -> str:
    """Format a Decimal as a US currency-ish string ``1,500.00``."""
    q = value.quantize(Decimal("0.01"))
    return f"{q:,.2f}"


def render_schedule_b_pdf(fields: ScheduleBFields, out_path: Path) -> Path:
    """Render a Schedule B SCAFFOLD PDF using reportlab.

    This writes a tabular PDF listing Part I, Part II, and Part III. It
    is NOT a filled IRS Schedule B — real AcroForm overlay on the IRS
    fillable PDF is a follow-up task that will need a researched widget
    map.

    Returns ``out_path`` for convenience.
    """
    # Lazy import so Layer 1 can be exercised without reportlab.
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
        title="Schedule B (TY2025 SCAFFOLD)",
    )
    styles = getSampleStyleSheet()

    story: list = []
    story.append(
        Paragraph("Schedule B (Form 1040) - TY2025 SCAFFOLD", styles["Title"])
    )
    story.append(
        Paragraph(
            "This is a scaffold rendering, not a filed IRS form.",
            styles["Italic"],
        )
    )
    story.append(Spacer(1, 12))

    # Header
    header_rows = [
        ["Taxpayer", fields.taxpayer_name],
        ["SSN", fields.taxpayer_ssn],
        ["Required?", "Yes" if fields.is_required else "No"],
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

    # Part I — Interest
    story.append(Paragraph("Part I - Interest", styles["Heading2"]))
    part_i_rows: list[list[str]] = [["Line", "Payer / Description", "Amount"]]
    if fields.part_i_line_1_rows:
        for row in fields.part_i_line_1_rows:
            part_i_rows.append(["1", row.payer_name, _format_decimal(row.amount)])
    else:
        part_i_rows.append(["1", "(no interest reported)", _format_decimal(_ZERO)])
    part_i_rows.append(
        ["2", "Add the amounts on line 1",
         _format_decimal(fields.part_i_line_2_total_interest)]
    )
    part_i_rows.append(
        ["3", "Excludable US savings bond interest (Form 8815) [DEFERRED]",
         _format_decimal(fields.part_i_line_3_excludable_savings_bond_interest)]
    )
    part_i_rows.append(
        ["4", "Subtract line 3 from line 2 (to Form 1040 line 2b)",
         _format_decimal(fields.part_i_line_4_taxable_interest)]
    )
    part_i_table = Table(part_i_rows, colWidths=[40, 360, 100])
    part_i_table.setStyle(
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
    story.append(part_i_table)
    story.append(Spacer(1, 12))

    # Part II — Ordinary Dividends
    story.append(Paragraph("Part II - Ordinary Dividends", styles["Heading2"]))
    part_ii_rows: list[list[str]] = [["Line", "Payer / Description", "Amount"]]
    if fields.part_ii_line_5_rows:
        for row in fields.part_ii_line_5_rows:
            part_ii_rows.append(["5", row.payer_name, _format_decimal(row.amount)])
    else:
        part_ii_rows.append(
            ["5", "(no ordinary dividends reported)", _format_decimal(_ZERO)]
        )
    part_ii_rows.append(
        ["6", "Add the amounts on line 5 (to Form 1040 line 3b)",
         _format_decimal(fields.part_ii_line_6_total_ordinary_dividends)]
    )
    part_ii_table = Table(part_ii_rows, colWidths=[40, 360, 100])
    part_ii_table.setStyle(
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
    story.append(part_ii_table)
    story.append(Spacer(1, 12))

    # Part III — Foreign Accounts and Trusts
    story.append(
        Paragraph("Part III - Foreign Accounts and Trusts", styles["Heading2"])
    )
    story.append(
        Paragraph(
            "SCAFFOLD: canonical model does not yet carry foreign account "
            "flags; all Part III values default to No.",
            styles["Italic"],
        )
    )
    part_iii_rows: list[list[str]] = [
        ["Line", "Question", "Answer"],
        [
            "7a",
            "Foreign financial account at any time?",
            "Yes" if fields.part_iii_line_7a_foreign_account else "No",
        ],
        [
            "7a",
            "FinCEN Form 114 required?",
            "Yes" if fields.part_iii_line_7a_fincen114_required else "No",
        ],
        [
            "7b",
            "FinCEN 114 country name",
            fields.part_iii_line_7b_fincen114_country or "-",
        ],
        [
            "8",
            "Foreign trust distribution / grantor / transferor?",
            "Yes" if fields.part_iii_line_8_foreign_trust else "No",
        ],
    ]
    part_iii_table = Table(part_iii_rows, colWidths=[40, 360, 100])
    part_iii_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (2, 1), (2, -1), "LEFT"),
            ]
        )
    )
    story.append(part_iii_table)

    doc.build(story)
    return out_path


__all__ = [
    "SCHEDULE_B_THRESHOLD",
    "ScheduleBFields",
    "ScheduleBPayerRow",
    "compute_schedule_b_fields",
    "render_schedule_b_pdf",
    "schedule_b_required",
]
