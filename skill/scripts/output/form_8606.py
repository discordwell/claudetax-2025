"""Form 8606 — Nondeductible IRAs — compute + render.

Tracks basis in traditional IRAs from nondeductible contributions.
Required when a taxpayer:

* Makes nondeductible contributions to a traditional IRA
* Takes distributions from a traditional IRA with basis
  (nondeductible contributions)
* Converts a traditional IRA to a Roth IRA (with basis)

Public surface (two layers)
---------------------------

* **Layer 1** — :func:`compute_form_8606_fields` maps a
  :class:`CanonicalReturn` (with ``ira_info`` populated) onto a frozen
  :class:`Form8606Fields` dataclass whose attribute names mirror the
  Form 8606 line numbers.

  Part I (lines 1-14): Nondeductible contributions and basis tracking.
  Part II (line 16): Roth conversion taxable amount.
  Part III: Roth IRA distributions — deferred to a later wave (zeros).

* **Layer 2** — :func:`render_form_8606_pdf` writes a reportlab scaffold
  PDF listing every line name and value in a human-readable table.

Authority
---------
IRS Form 8606 (TY2025): https://www.irs.gov/pub/irs-pdf/f8606.pdf
IRS Instructions for Form 8606 (TY2025):
https://www.irs.gov/pub/irs-pdf/i8606.pdf
"""
from __future__ import annotations

from dataclasses import dataclass, fields as dc_fields
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from skill.scripts.models import CanonicalReturn

_ZERO = Decimal("0")
_CENTS = Decimal("0.01")
_ONE = Decimal("1.000")


# ---------------------------------------------------------------------------
# Layer 1 — field dataclass and computation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Form8606Fields:
    """Frozen snapshot of Form 8606 line values, ready for rendering.

    Field names follow the TY2025 Form 8606 line numbers. All numeric
    fields are :class:`Decimal`. Part III (Roth IRA distributions) is
    populated with zeros — deferred to a later wave.
    """

    # Header
    taxpayer_name: str = ""
    taxpayer_ssn: str = ""

    # Part I — Nondeductible Contributions and Basis
    line_1_nondeductible_contributions: Decimal = _ZERO
    line_2_prior_year_basis: Decimal = _ZERO
    line_3_add_1_and_2: Decimal = _ZERO
    line_4_contributions_withdrawn: Decimal = _ZERO
    line_5_subtract_4_from_3: Decimal = _ZERO
    line_6_ira_value_year_end: Decimal = _ZERO
    line_7_distributions: Decimal = _ZERO
    line_8_roth_conversions: Decimal = _ZERO
    line_9_total_ira_value_base: Decimal = _ZERO
    line_10_nontaxable_percentage: Decimal = _ZERO
    line_11_nontaxable_distributions: Decimal = _ZERO
    line_12_nontaxable_conversions: Decimal = _ZERO
    line_13_taxable_distributions: Decimal = _ZERO
    line_14_remaining_basis: Decimal = _ZERO

    # Part II — Roth Conversions
    line_16_taxable_conversion: Decimal = _ZERO

    # Part III — Roth IRA Distributions (deferred — all zeros)
    line_19_roth_contributions: Decimal = _ZERO
    line_20_roth_basis_from_conversions: Decimal = _ZERO
    line_21_roth_total_basis: Decimal = _ZERO
    line_22_roth_distributions: Decimal = _ZERO
    line_23_roth_nontaxable: Decimal = _ZERO
    line_24_roth_taxable: Decimal = _ZERO
    line_25_roth_early_distribution_taxable: Decimal = _ZERO


def _q(x: Decimal) -> Decimal:
    """Quantize a Decimal to two decimal places."""
    return x.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _q3(x: Decimal) -> Decimal:
    """Quantize a Decimal to three decimal places (for the percentage)."""
    return x.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def compute_form_8606_fields(
    canonical: CanonicalReturn,
) -> Form8606Fields:
    """Map a CanonicalReturn onto a Form8606Fields dataclass.

    Walks Part I lines 1-14 and Part II line 16 per the IRS Form 8606
    instructions. Part III is skipped (zeros returned).

    Parameters
    ----------
    canonical
        The canonical return; ``canonical.ira_info`` must be populated.

    Returns
    -------
    Form8606Fields
        A frozen dataclass with every line populated.

    Raises
    ------
    ValueError
        If ``canonical.ira_info`` is None.
    """
    if canonical.ira_info is None:
        raise ValueError(
            "compute_form_8606_fields requires canonical.ira_info to be populated"
        )

    ira = canonical.ira_info

    # -- header -----------------------------------------------------------
    taxpayer_name = f"{canonical.taxpayer.first_name} {canonical.taxpayer.last_name}"
    taxpayer_ssn = canonical.taxpayer.ssn

    # -- Part I: Nondeductible Contributions and Basis --------------------
    line_1 = _q(ira.nondeductible_contributions_current_year)
    line_2 = _q(ira.prior_year_basis)
    line_3 = _q(line_1 + line_2)
    line_4 = _q(ira.contributions_withdrawn_by_due_date)
    line_5 = _q(max(_ZERO, line_3 - line_4))

    line_6 = _q(ira.total_ira_value_year_end)
    line_7 = _q(ira.distributions_received)
    line_8 = _q(ira.roth_conversions)

    # Line 9: total IRA value base (denominator for nontaxable %)
    line_9 = _q(line_6 + line_7 + line_8)

    # Line 10: nontaxable percentage = line_5 / line_9, capped at 1.000
    if line_9 > _ZERO:
        raw_pct = line_5 / line_9
        line_10 = min(_q3(raw_pct), _ONE)
    else:
        # If total IRA value base is zero, there's nothing to distribute
        # against — nontaxable percentage is 0 (or could be 1.0 if basis
        # also zero; either way, distributions and conversions are zero).
        line_10 = _ZERO

    # Line 11: nontaxable portion of distributions
    line_11 = _q(line_7 * line_10)

    # Line 12: nontaxable portion of conversions
    line_12 = _q(line_8 * line_10)

    # Line 13: taxable portion of distributions
    line_13 = _q(max(_ZERO, line_7 - line_11))

    # Line 14: remaining basis carryforward
    line_14 = _q(max(_ZERO, line_5 - (line_11 + line_12)))

    # -- Part II: Roth Conversions ----------------------------------------
    # Line 16: taxable conversion amount = line 8 - line 12
    line_16 = _q(max(_ZERO, line_8 - line_12))

    return Form8606Fields(
        taxpayer_name=taxpayer_name,
        taxpayer_ssn=taxpayer_ssn,
        # Part I
        line_1_nondeductible_contributions=line_1,
        line_2_prior_year_basis=line_2,
        line_3_add_1_and_2=line_3,
        line_4_contributions_withdrawn=line_4,
        line_5_subtract_4_from_3=line_5,
        line_6_ira_value_year_end=line_6,
        line_7_distributions=line_7,
        line_8_roth_conversions=line_8,
        line_9_total_ira_value_base=line_9,
        line_10_nontaxable_percentage=line_10,
        line_11_nontaxable_distributions=line_11,
        line_12_nontaxable_conversions=line_12,
        line_13_taxable_distributions=line_13,
        line_14_remaining_basis=line_14,
        # Part II
        line_16_taxable_conversion=line_16,
    )


# ---------------------------------------------------------------------------
# Layer 2 — reportlab scaffold PDF rendering
# ---------------------------------------------------------------------------

# Human-readable labels for each numeric field, in display order.
_LINE_LABELS: list[tuple[str, str]] = [
    ("line_1_nondeductible_contributions", "Line 1 — Nondeductible contributions for the year"),
    ("line_2_prior_year_basis", "Line 2 — Prior year basis (from last year's 8606 line 14)"),
    ("line_3_add_1_and_2", "Line 3 — Add lines 1 and 2"),
    ("line_4_contributions_withdrawn", "Line 4 — Contributions withdrawn by due date"),
    ("line_5_subtract_4_from_3", "Line 5 — Subtract line 4 from line 3"),
    ("line_6_ira_value_year_end", "Line 6 — Value of all traditional IRAs at year end"),
    ("line_7_distributions", "Line 7 — Distributions from traditional IRAs"),
    ("line_8_roth_conversions", "Line 8 — Net conversions to Roth IRA"),
    ("line_9_total_ira_value_base", "Line 9 — Add lines 6, 7, and 8"),
    ("line_10_nontaxable_percentage", "Line 10 — Nontaxable percentage (line 5 / line 9)"),
    ("line_11_nontaxable_distributions", "Line 11 — Nontaxable portion of distributions"),
    ("line_12_nontaxable_conversions", "Line 12 — Nontaxable portion of conversions"),
    ("line_13_taxable_distributions", "Line 13 — Taxable portion of distributions"),
    ("line_14_remaining_basis", "Line 14 — Remaining basis carryforward"),
    ("line_16_taxable_conversion", "Line 16 — Taxable conversion amount (Part II)"),
]


def _format_decimal(value: Decimal, is_percentage: bool = False) -> str:
    """Format a Decimal for display in the scaffold table.

    Percentages show three decimal places; money shows two. Zero
    collapses to ``"0.00"`` (or ``"0.000"`` for percentages) rather
    than blank, since this is a diagnostic scaffold.
    """
    if is_percentage:
        return f"{value:.3f}"
    q = value.quantize(_CENTS, rounding=ROUND_HALF_UP)
    return f"{q:.2f}"


def render_form_8606_pdf(fields: Form8606Fields, out_path: Path) -> Path:
    """Render a Form 8606 scaffold PDF via reportlab.

    Writes a single-page table listing every Form 8606 line label and
    its computed value. This is NOT a filled IRS form — it is a
    human-readable diagnostic scaffold suitable for review.

    Parameters
    ----------
    fields
        The Layer 1 output from :func:`compute_form_8606_fields`.
    out_path
        Destination path for the PDF.

    Returns
    -------
    Path
        ``out_path`` for convenience.
    """
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
        title="Form 8606 — Nondeductible IRAs",
    )
    styles = getSampleStyleSheet()
    story: list = []

    # Header
    story.append(
        Paragraph("Form 8606 — Nondeductible IRAs (TY2025)", styles["Title"])
    )
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            f"Taxpayer: {fields.taxpayer_name} &nbsp;&nbsp; SSN: {fields.taxpayer_ssn}",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 12))

    # Build data rows
    table_data = [["Line", "Amount"]]
    for attr_name, label in _LINE_LABELS:
        value = getattr(fields, attr_name)
        is_pct = "percentage" in attr_name
        formatted = _format_decimal(value, is_percentage=is_pct)
        table_data.append([label, formatted])

    table = Table(table_data, colWidths=[400, 100])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(table)

    doc.build(story)
    return out_path
