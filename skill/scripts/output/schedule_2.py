"""Schedule 2 (Additional Taxes) output renderer — two-layer scaffold.

SCAFFOLD NOTICE
===============
This module is a SCAFFOLD for IRS Schedule 2 (Form 1040) PDF generation,
mirroring the two-layer design of ``skill.scripts.output.form_1040``:

* Layer 1 (``compute_schedule_2_fields``) is a pure mapping from the
  canonical return's ``OtherTaxes`` and ``ComputedTotals`` blocks onto
  the Schedule 2 structure.  It DOES NOT recompute any tax — it just
  routes values onto Schedule 2 line names.

* Layer 2 (``render_schedule_2_pdf``) writes a minimal reportlab
  text-tabular PDF for human eyeballing.  It is NOT a filled IRS
  Schedule 2 — real AcroForm overlay on the IRS fillable PDF is a
  follow-up task.

Schedule 2 structure (TY2025, assumed stable from TY2024)
---------------------------------------------------------
Part I — Tax (lines 1-3)
  Line 1 : AMT (from Form 6251, line 11)
  Line 2 : Excess advance premium tax credit repayment (Form 8962)
  Line 3 : Add lines 1 and 2 -> flows to Form 1040 line 17

Part II — Other Taxes (lines 6-21)
  Line 6 : Self-employment tax (Schedule SE, line 12)
  Line 7 : Unreported tip income tax (Form 4137) — DEFERRED
  Line 8 : Additional tax on IRAs / tax-favored accounts (Form 5329)
  Line 10: Additional Medicare tax (Form 8959)
  Line 11: Net investment income tax (Form 8960)
  Lines 12-17: Various recapture taxes — DEFERRED (rare)
  Line 18: Section 965 net tax liability installment — DEFERRED
  Line 19: Other taxes from other forms — DEFERRED
  Line 21: Total additional taxes (sum of Part II) -> Form 1040 line 23

Simplifications loudly deferred
-------------------------------
* Line 2 (excess advance PTC repayment, Form 8962) is always zero —
  Form 8962 is not yet modeled.
* Line 7 (unreported tip income, Form 4137) is always zero — Form 4137
  is not yet modeled.
* Lines 12-17 (recapture taxes) are always zero — these recapture
  forms are extremely rare and not modeled.
* Line 18 (section 965 installment) is always zero — not modeled.
* Line 19 (other taxes from other forms) is always zero — not modeled.

Sources
-------
* IRS 2024 Schedule 2 (Form 1040) — line-by-line layout.
* IRS 2024 Instructions for Schedule 2.
* IRS Form 1040, line 17 ("Schedule 2, Part I, line 3") and line 23
  ("Schedule 2, Part II, line 21").
"""
from __future__ import annotations

from dataclasses import dataclass, fields as dc_fields
from decimal import Decimal
from pathlib import Path

from skill.scripts.models import CanonicalReturn

_ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Layer 1: field dataclass and computation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Schedule2Fields:
    """Frozen snapshot of Schedule 2 line values, ready for rendering.

    Field names follow the TY2024 Schedule 2 (Form 1040) line numbers
    (assumed stable for TY2025).  All numeric fields are ``Decimal``.
    """

    # Header
    taxpayer_name: str = ""
    taxpayer_ssn: str = ""

    # Part I — Tax
    line_1_amt: Decimal = _ZERO
    line_2_excess_aptc: Decimal = _ZERO
    line_3_part_i_total: Decimal = _ZERO

    # Part II — Other Taxes
    line_6_se_tax: Decimal = _ZERO
    line_7_unreported_tip_tax: Decimal = _ZERO
    line_8_early_distribution: Decimal = _ZERO
    line_10_additional_medicare: Decimal = _ZERO
    line_11_niit: Decimal = _ZERO
    line_17_recapture_taxes: Decimal = _ZERO
    line_18_section_965: Decimal = _ZERO
    line_19_other_taxes: Decimal = _ZERO
    line_21_part_ii_total: Decimal = _ZERO


def _dec(x: Decimal | None) -> Decimal:
    """Coerce an Optional[Decimal] to a concrete Decimal (None -> 0)."""
    return x if x is not None else _ZERO


def compute_schedule_2_fields(
    canonical: CanonicalReturn,
) -> Schedule2Fields:
    """Map a CanonicalReturn onto a ``Schedule2Fields`` dataclass.

    This is a pure, side-effect-free routing of the return's
    ``other_taxes`` and ``computed`` blocks onto Schedule 2 line names.
    It does NOT recompute any tax amount.
    """
    # -- header ---------------------------------------------------------
    taxpayer_name = (
        f"{canonical.taxpayer.first_name} {canonical.taxpayer.last_name}"
    )
    taxpayer_ssn = canonical.taxpayer.ssn or ""

    # -- Part I — Tax ---------------------------------------------------
    line_1 = _dec(canonical.computed.alternative_minimum_tax)
    line_2 = _ZERO  # Form 8962 not modeled
    line_3 = line_1 + line_2

    # -- Part II — Other Taxes ------------------------------------------
    line_6 = _dec(canonical.other_taxes.self_employment_tax)
    line_7 = _ZERO  # Form 4137 not modeled
    line_8 = _dec(canonical.other_taxes.early_distribution_penalty)
    line_10 = _dec(canonical.other_taxes.additional_medicare_tax)
    line_11 = _dec(canonical.other_taxes.net_investment_income_tax)
    line_17 = _ZERO  # recapture taxes not modeled
    line_18 = _ZERO  # section 965 not modeled
    line_19 = _ZERO  # other taxes from other forms not modeled

    line_21 = line_6 + line_7 + line_8 + line_10 + line_11 + line_17 + line_18 + line_19

    return Schedule2Fields(
        taxpayer_name=taxpayer_name,
        taxpayer_ssn=taxpayer_ssn,
        line_1_amt=line_1,
        line_2_excess_aptc=line_2,
        line_3_part_i_total=line_3,
        line_6_se_tax=line_6,
        line_7_unreported_tip_tax=line_7,
        line_8_early_distribution=line_8,
        line_10_additional_medicare=line_10,
        line_11_niit=line_11,
        line_17_recapture_taxes=line_17,
        line_18_section_965=line_18,
        line_19_other_taxes=line_19,
        line_21_part_ii_total=line_21,
    )


def schedule_2_required(canonical: CanonicalReturn) -> bool:
    """Return ``True`` iff Schedule 2 is required for this return.

    Schedule 2 is required whenever any of its source fields are
    nonzero:  AMT, SE tax, additional Medicare tax, NIIT, or
    early-distribution penalty.
    """
    if (
        canonical.computed.alternative_minimum_tax is not None
        and canonical.computed.alternative_minimum_tax > 0
    ):
        return True
    ot = canonical.other_taxes
    if ot.self_employment_tax > 0:
        return True
    if ot.additional_medicare_tax > 0:
        return True
    if ot.net_investment_income_tax > 0:
        return True
    if ot.early_distribution_penalty > 0:
        return True
    return False


# ---------------------------------------------------------------------------
# Layer 2: reportlab scaffold PDF rendering
# ---------------------------------------------------------------------------


def _format_decimal(value: Decimal) -> str:
    """Format a Decimal as ``"1500.00"`` for display.

    Zero is rendered as the empty string so the scaffold PDF stays
    visually clean for cells the filer doesn't use.
    """
    q = value.quantize(Decimal("0.01"))
    if q == Decimal("0.00"):
        return ""
    return f"{q:.2f}"


def render_schedule_2_pdf(fields: Schedule2Fields, out_path: Path) -> Path:
    """Render a Schedule 2 scaffold PDF using reportlab.

    This is a minimal tabular layout listing every line name and value
    so a human can eyeball the return.  It is NOT a filled IRS Schedule 2.
    A real AcroForm overlay on the IRS fillable PDF is a follow-up task.

    Returns ``out_path`` for convenience.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen.canvas import Canvas

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    c = Canvas(str(out_path), pagesize=letter)
    width, height = letter

    # Title
    c.setFont("Helvetica-Bold", 14)
    c.drawString(
        1 * inch,
        height - 1 * inch,
        "Schedule 2 (Form 1040) — Additional Taxes",
    )
    c.setFont("Helvetica", 10)
    c.drawString(
        1 * inch, height - 1.3 * inch, f"Name: {fields.taxpayer_name}"
    )
    c.drawString(
        4.5 * inch, height - 1.3 * inch, f"SSN: {fields.taxpayer_ssn}"
    )

    y = height - 1.8 * inch

    def _line(label: str, value: str) -> None:
        nonlocal y
        c.setFont("Helvetica", 9)
        c.drawString(1 * inch, y, label)
        c.drawRightString(7.5 * inch, y, value)
        y -= 14
        if y < 1 * inch:
            c.showPage()
            y = height - 1 * inch

    # Part I header
    c.setFont("Helvetica-Bold", 11)
    c.drawString(1 * inch, y, "Part I — Tax")
    y -= 18

    _line("1  AMT (Form 6251)", _format_decimal(fields.line_1_amt))
    _line(
        "2  Excess advance premium tax credit repayment",
        _format_decimal(fields.line_2_excess_aptc),
    )
    _line(
        "3  Add lines 1 and 2 (to Form 1040, line 17)",
        _format_decimal(fields.line_3_part_i_total),
    )

    y -= 10
    c.setFont("Helvetica-Bold", 11)
    c.drawString(1 * inch, y, "Part II — Other Taxes")
    y -= 18

    _line(
        "6  Self-employment tax (Schedule SE)",
        _format_decimal(fields.line_6_se_tax),
    )
    _line(
        "7  Unreported tip income tax (Form 4137)",
        _format_decimal(fields.line_7_unreported_tip_tax),
    )
    _line(
        "8  Additional tax on IRAs/tax-favored accounts (Form 5329)",
        _format_decimal(fields.line_8_early_distribution),
    )
    _line(
        "10 Additional Medicare tax (Form 8959)",
        _format_decimal(fields.line_10_additional_medicare),
    )
    _line(
        "11 Net investment income tax (Form 8960)",
        _format_decimal(fields.line_11_niit),
    )
    _line(
        "17 Recapture taxes",
        _format_decimal(fields.line_17_recapture_taxes),
    )
    _line(
        "18 Section 965 net tax liability installment",
        _format_decimal(fields.line_18_section_965),
    )
    _line(
        "19 Tax from other forms",
        _format_decimal(fields.line_19_other_taxes),
    )
    _line(
        "21 Total additional taxes (to Form 1040, line 23)",
        _format_decimal(fields.line_21_part_ii_total),
    )

    c.save()
    return out_path


__all__ = [
    "Schedule2Fields",
    "compute_schedule_2_fields",
    "render_schedule_2_pdf",
    "schedule_2_required",
]
