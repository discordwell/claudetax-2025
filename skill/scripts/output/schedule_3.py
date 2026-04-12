"""Schedule 3 (Additional Credits and Payments) output renderer — two-layer scaffold.

SCAFFOLD NOTICE
===============
This module is a SCAFFOLD for IRS Schedule 3 (Form 1040) PDF generation,
mirroring the two-layer design of ``skill.scripts.output.form_1040``:

* Layer 1 (``compute_schedule_3_fields``) is a pure mapping from the
  canonical return's ``Credits`` and ``Payments`` blocks onto the
  Schedule 3 structure. It DOES NOT recompute any credit — it just
  routes values onto Schedule 3 line names.

* Layer 2 (``render_schedule_3_pdf``) writes a minimal reportlab
  text-tabular PDF for human eyeballing. It is NOT a filled IRS
  Schedule 3 — real AcroForm overlay on the IRS fillable PDF is a
  follow-up task.

Schedule 3 structure (TY2025, assumed stable from TY2024)
---------------------------------------------------------
Part I — Nonrefundable Credits (lines 1-8)
  Line 1 : Foreign tax credit (Form 1116 or direct entry)
  Line 2 : Child and dependent care credit (Form 2441)
  Line 3 : Education credits (Form 8863, nonrefundable part)
  Line 4 : Retirement savings credit (Form 8880)
  Line 5a: Residential clean energy credit (Form 5695 Part I)
  Line 5b: Energy efficient home improvement credit (Form 5695 Part II)
  Line 6 : Other nonrefundable credits (aggregated from 6d/6g/6i/6z)
  Line 7 : Total other credits (sum of lines 1-6) -> Form 1040 line 20
  Line 8 : Total nonrefundable credits = line 7

Part II — Other Payments and Refundable Credits (lines 9-15)
  Line 9 : Net premium tax credit (Form 8962)
  Line 10: Amount paid with request for extension (Form 4868)
  Line 11: Excess Social Security tax withheld
  Line 12: Credits for federal tax on fuels (Form 4136) — DEFERRED
  Line 13: Other payments/refundable credits (13a-13z) — DEFERRED
  Line 14: AOTC refundable part (Form 8863)
  Line 15: Total other payments and refundable credits -> Form 1040 line 31

Simplifications loudly deferred
-------------------------------
* Line 5b (energy efficient home improvement credit, Form 5695 Part II)
  is not separately modeled — the canonical Credits model has a single
  ``residential_energy_credits`` field.  We put the whole amount on
  line 5a and leave 5b at zero. If a future wave splits Form 5695 into
  Part I / Part II, update the mapping here.
* Line 6 sub-lines (6d elderly/disabled, 6g plug-in vehicle, 6i
  alternative motor vehicle, 6z other) are drawn from
  ``credits.other_credits`` dict. Each supported sub-line key maps to
  the IRS line; unrecognized keys are aggregated into line 6z.
* Line 12 (federal fuel tax credits, Form 4136) is always zero — the
  canonical model does not have a fuel tax credit field.
* Line 13 sub-lines (13a Form 2439, 13b tax collected at source, 13c
  Form 8885, 13d qualified sick/family leave, 13z other) are always
  zero — the canonical model does not currently track these.

Sources
-------
* IRS 2024 Instructions for Schedule 3 (Form 1040), line-by-line.
* IRS 2024 Form 1040, line 20 ("Schedule 3, line 8") and line 31
  ("Schedule 3, line 15").
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
class Schedule3Fields:
    """Frozen snapshot of Schedule 3 line values, ready for rendering.

    Field names follow the TY2024 Schedule 3 (Form 1040) line numbers
    (assumed stable for TY2025). All numeric fields are ``Decimal``.
    """

    # Header
    taxpayer_name: str = ""
    taxpayer_ssn: str = ""

    # Part I — Nonrefundable Credits
    line_1_foreign_tax_credit: Decimal = _ZERO
    line_2_dependent_care_credit: Decimal = _ZERO
    line_3_education_credits: Decimal = _ZERO
    line_4_retirement_savings_credit: Decimal = _ZERO
    line_5a_residential_clean_energy_credit: Decimal = _ZERO
    line_5b_energy_efficient_home_improvement: Decimal = _ZERO
    line_6d_elderly_or_disabled_credit: Decimal = _ZERO
    line_6g_plug_in_vehicle_credit: Decimal = _ZERO
    line_6i_alternative_motor_vehicle_credit: Decimal = _ZERO
    line_6z_other_nonrefundable: Decimal = _ZERO
    line_6_total_other_nonrefundable: Decimal = _ZERO
    line_7_total_other_credits: Decimal = _ZERO
    line_8_total_nonrefundable_credits: Decimal = _ZERO

    # Part II — Other Payments and Refundable Credits
    line_9_net_premium_tax_credit: Decimal = _ZERO
    line_10_amount_paid_with_extension: Decimal = _ZERO
    line_11_excess_social_security_withheld: Decimal = _ZERO
    line_12_fuel_tax_credits: Decimal = _ZERO
    line_13a_form_2439: Decimal = _ZERO
    line_13b_tax_collected_at_source: Decimal = _ZERO
    line_13c_form_8885: Decimal = _ZERO
    line_13d_qualified_sick_family_leave: Decimal = _ZERO
    line_13z_other_payments: Decimal = _ZERO
    line_13_total_other_payments: Decimal = _ZERO
    line_14_aotc_refundable: Decimal = _ZERO
    line_15_total_other_payments_and_refundable: Decimal = _ZERO

    # Whether Schedule 3 is required for this return.
    is_required: bool = False


# Well-known other_credits keys that map to specific Schedule 3 sub-lines.
_LINE_6_KEY_MAP: dict[str, str] = {
    "elderly_or_disabled": "6d",
    "plug_in_vehicle": "6g",
    "alternative_motor_vehicle": "6i",
}


def compute_schedule_3_fields(return_: CanonicalReturn) -> Schedule3Fields:
    """Map a CanonicalReturn onto a ``Schedule3Fields`` dataclass.

    This is a pure, side-effect-free routing of the return's ``credits``
    and ``payments`` blocks onto Schedule 3 line names. It does NOT
    recompute any credit amount.
    """
    credits = return_.credits
    payments = return_.payments

    # -- header ---------------------------------------------------------
    taxpayer_name = f"{return_.taxpayer.first_name} {return_.taxpayer.last_name}"
    taxpayer_ssn = return_.taxpayer.ssn or ""

    # -- Part I ---------------------------------------------------------
    line_1 = credits.foreign_tax_credit
    line_2 = credits.dependent_care_credit
    line_3 = credits.education_credits_nonrefundable
    line_4 = credits.retirement_savings_credit
    line_5a = credits.residential_energy_credits
    line_5b = _ZERO  # Part II of Form 5695 — not separately modeled

    # Line 6 sub-lines from other_credits dict
    line_6d = credits.other_credits.get("elderly_or_disabled", _ZERO)
    line_6g = credits.other_credits.get("plug_in_vehicle", _ZERO)
    line_6i = credits.other_credits.get("alternative_motor_vehicle", _ZERO)
    # Everything not mapped to a known sub-line goes into 6z.
    line_6z = sum(
        (v for k, v in credits.other_credits.items() if k not in _LINE_6_KEY_MAP),
        start=_ZERO,
    )
    line_6 = line_6d + line_6g + line_6i + line_6z

    line_7 = line_1 + line_2 + line_3 + line_4 + line_5a + line_5b + line_6
    line_8 = line_7

    # -- Part II --------------------------------------------------------
    line_9 = credits.premium_tax_credit_net
    line_10 = payments.amount_paid_with_4868_extension
    line_11 = payments.excess_social_security_tax_withheld
    line_12 = _ZERO  # Form 4136 not modeled
    line_13a = _ZERO  # Form 2439 not modeled
    line_13b = _ZERO  # tax collected at source not modeled
    line_13c = _ZERO  # Form 8885 not modeled
    line_13d = _ZERO  # qualified sick/family leave not modeled
    line_13z = _ZERO  # other payments not modeled
    line_13 = line_13a + line_13b + line_13c + line_13d + line_13z
    line_14 = credits.education_credits_refundable

    line_15 = line_9 + line_10 + line_11 + line_12 + line_13 + line_14

    # -- Required? -------------------------------------------------------
    is_required = _is_schedule_3_required(return_)

    return Schedule3Fields(
        taxpayer_name=taxpayer_name,
        taxpayer_ssn=taxpayer_ssn,
        line_1_foreign_tax_credit=line_1,
        line_2_dependent_care_credit=line_2,
        line_3_education_credits=line_3,
        line_4_retirement_savings_credit=line_4,
        line_5a_residential_clean_energy_credit=line_5a,
        line_5b_energy_efficient_home_improvement=line_5b,
        line_6d_elderly_or_disabled_credit=line_6d,
        line_6g_plug_in_vehicle_credit=line_6g,
        line_6i_alternative_motor_vehicle_credit=line_6i,
        line_6z_other_nonrefundable=line_6z,
        line_6_total_other_nonrefundable=line_6,
        line_7_total_other_credits=line_7,
        line_8_total_nonrefundable_credits=line_8,
        line_9_net_premium_tax_credit=line_9,
        line_10_amount_paid_with_extension=line_10,
        line_11_excess_social_security_withheld=line_11,
        line_12_fuel_tax_credits=line_12,
        line_13a_form_2439=line_13a,
        line_13b_tax_collected_at_source=line_13b,
        line_13c_form_8885=line_13c,
        line_13d_qualified_sick_family_leave=line_13d,
        line_13z_other_payments=line_13z,
        line_13_total_other_payments=line_13,
        line_14_aotc_refundable=line_14,
        line_15_total_other_payments_and_refundable=line_15,
        is_required=is_required,
    )


def _is_schedule_3_required(return_: CanonicalReturn) -> bool:
    """Return True if the return needs a Schedule 3.

    Schedule 3 is required whenever any of its line inputs are nonzero.
    """
    credits = return_.credits
    payments = return_.payments

    if credits.foreign_tax_credit > 0:
        return True
    if credits.dependent_care_credit > 0:
        return True
    if credits.education_credits_nonrefundable > 0:
        return True
    if credits.education_credits_refundable > 0:
        return True
    if credits.retirement_savings_credit > 0:
        return True
    if credits.residential_energy_credits > 0:
        return True
    if credits.premium_tax_credit_net > 0:
        return True
    if credits.other_credits:
        # Any nonzero value in other_credits triggers.
        if any(v > 0 for v in credits.other_credits.values()):
            return True
    if payments.amount_paid_with_4868_extension > 0:
        return True
    if payments.excess_social_security_tax_withheld > 0:
        return True
    if payments.estimated_tax_payments_2025 > 0:
        return True
    return False


def schedule_3_required(return_: CanonicalReturn) -> bool:
    """Public helper: is Schedule 3 REQUIRED for this canonical return?

    Intended to be called by the pipeline to decide whether to render
    Schedule 3.
    """
    return _is_schedule_3_required(return_)


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


def render_schedule_3_pdf(fields: Schedule3Fields, out_path: Path) -> Path:
    """Render a Schedule 3 scaffold PDF using reportlab.

    This is a minimal tabular layout listing every line name and value
    so a human can eyeball the return. It is NOT a filled IRS Schedule 3.
    A real AcroForm overlay on the IRS fillable PDF is a follow-up task.

    Returns ``out_path`` for convenience.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen.canvas import Canvas

    c = Canvas(str(out_path), pagesize=letter)
    width, height = letter

    # Title
    c.setFont("Helvetica-Bold", 14)
    c.drawString(1 * inch, height - 1 * inch, "Schedule 3 (Form 1040) — Additional Credits and Payments")
    c.setFont("Helvetica", 10)
    c.drawString(1 * inch, height - 1.3 * inch, f"Name: {fields.taxpayer_name}")
    c.drawString(4.5 * inch, height - 1.3 * inch, f"SSN: {fields.taxpayer_ssn}")

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
    c.drawString(1 * inch, y, "Part I — Nonrefundable Credits")
    y -= 18

    _line("1  Foreign tax credit", _format_decimal(fields.line_1_foreign_tax_credit))
    _line("2  Child and dependent care credit", _format_decimal(fields.line_2_dependent_care_credit))
    _line("3  Education credits (nonrefundable)", _format_decimal(fields.line_3_education_credits))
    _line("4  Retirement savings credit", _format_decimal(fields.line_4_retirement_savings_credit))
    _line("5a Residential clean energy credit", _format_decimal(fields.line_5a_residential_clean_energy_credit))
    _line("5b Energy efficient home improvement", _format_decimal(fields.line_5b_energy_efficient_home_improvement))
    _line("6d Elderly or disabled credit", _format_decimal(fields.line_6d_elderly_or_disabled_credit))
    _line("6g Plug-in vehicle credit", _format_decimal(fields.line_6g_plug_in_vehicle_credit))
    _line("6i Alternative motor vehicle credit", _format_decimal(fields.line_6i_alternative_motor_vehicle_credit))
    _line("6z Other nonrefundable credits", _format_decimal(fields.line_6z_other_nonrefundable))
    _line("6  Total other nonrefundable credits", _format_decimal(fields.line_6_total_other_nonrefundable))
    _line("7  Total other credits (sum 1-6)", _format_decimal(fields.line_7_total_other_credits))
    _line("8  Total nonrefundable credits", _format_decimal(fields.line_8_total_nonrefundable_credits))

    y -= 10
    c.setFont("Helvetica-Bold", 11)
    c.drawString(1 * inch, y, "Part II — Other Payments and Refundable Credits")
    y -= 18

    _line("9  Net premium tax credit", _format_decimal(fields.line_9_net_premium_tax_credit))
    _line("10 Amount paid with extension", _format_decimal(fields.line_10_amount_paid_with_extension))
    _line("11 Excess Social Security tax withheld", _format_decimal(fields.line_11_excess_social_security_withheld))
    _line("12 Credits for federal tax on fuels", _format_decimal(fields.line_12_fuel_tax_credits))
    _line("13a Form 2439", _format_decimal(fields.line_13a_form_2439))
    _line("13b Tax collected at source", _format_decimal(fields.line_13b_tax_collected_at_source))
    _line("13c Form 8885", _format_decimal(fields.line_13c_form_8885))
    _line("13d Qualified sick/family leave", _format_decimal(fields.line_13d_qualified_sick_family_leave))
    _line("13z Other payments", _format_decimal(fields.line_13z_other_payments))
    _line("13 Total other payments", _format_decimal(fields.line_13_total_other_payments))
    _line("14 AOTC refundable", _format_decimal(fields.line_14_aotc_refundable))
    _line("15 Total other payments and refundable", _format_decimal(fields.line_15_total_other_payments_and_refundable))

    c.save()
    return Path(out_path)


__all__ = [
    "Schedule3Fields",
    "compute_schedule_3_fields",
    "render_schedule_3_pdf",
    "schedule_3_required",
]
