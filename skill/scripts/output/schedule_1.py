"""Schedule 1 (Form 1040) output renderer — two-layer scaffold.

SCAFFOLD NOTICE
===============
This module is a SCAFFOLD for Schedule 1 (Additional Income and
Adjustments to Income) PDF generation.  It follows the same two-layer
design as ``skill.scripts.output.form_1040``:

* Layer 1 (``compute_schedule_1_fields``) maps a ``CanonicalReturn`` onto
  a frozen dataclass whose field names mirror the TY2025 Schedule 1 line
  structure.  All arithmetic delegates to
  ``skill.scripts.calc.engine`` helpers (``schedule_c_net_profit``,
  ``schedule_e_total_net``, ``_sum_adjustments``,
  ``_sum_part_i_additional_income``) so that Layer 1 is bit-for-bit
  aligned with how the main calc engine treats the same return.

* Layer 2 (``render_schedule_1_pdf``) writes a simple tabular PDF using
  ``reportlab`` that lists every line name and value.  This is NOT a
  filled IRS Schedule 1 — real AcroForm overlay on the IRS fillable PDF
  is a follow-up task.

Schedule 1 structure (TY2025)
-----------------------------
Part I — Additional Income (lines 1-10)
  Line 1  : Taxable refunds of state/local income taxes (1099-G box 2)
  Line 2a : Alimony received (pre-2019 divorce agreements)
  Line 3  : Business income/loss (from Schedule C line 31)
  Line 4  : Other gains/losses (from Form 4797) — DEFERRED
  Line 5  : Rental real estate, royalties, partnerships, S corps
             (Schedule E line 26)
  Line 6  : Farm income/loss (from Schedule F) — DEFERRED
  Line 7  : Unemployment compensation (1099-G box 1)
  Line 8z : Other income (catch-all from canonical other_income dict)
  Line 9  : OBBBA Schedule 1-A total (tips + overtime)
  Line 10 : Total additional income (sum of Part I lines)

Part II — Adjustments to Income (lines 11-26)
  Line 11 : Educator expenses
  Line 12 : Certain business expenses (reservists, performing artists)
             — DEFERRED
  Line 13 : HSA deduction
  Line 14 : Moving expenses for Armed Forces
  Line 15 : Deductible part of self-employment tax (from Schedule SE)
  Line 16 : Self-employed SEP, SIMPLE, qualified plans
  Line 17 : Self-employed health insurance deduction
  Line 18 : Penalty on early withdrawal of savings
  Line 19 : Alimony paid (pre-2019 divorces)
  Line 20 : IRA deduction
  Line 21 : Student loan interest deduction
  Line 22 : Reserved
  Line 23 : Archer MSA deduction
  Line 24z: Other adjustments (catch-all)
  Line 25 : OBBBA adjustments (senior deduction)
  Line 26 : Total adjustments (sum of Part II)

Net = line 10 - line 26 -> flows to Form 1040 line 8.

Simplifications / deferred work
--------------------------------
* Line 1 (taxable refunds): routed from 1099-G box 2 when present.
  The taxability worksheet (were they itemized in the prior year?) is
  not yet modeled — the raw box 2 amount is used.
* Line 2a (alimony received): sourced from ``other_income`` dict key
  ``"alimony_received"`` — no typed model field yet.
* Lines 4 / 6 / 8a-8d / 9 / 12: hard-coded to zero (not yet modeled).
* Line 15 (deductible SE tax): uses the engine's schedule_c_net_profit
  and Schedule SE constants to compute half SE tax, matching the
  Schedule SE renderer's formula.

Sources
-------
* IRS 2024 Schedule 1 (Form 1040) and instructions
  https://www.irs.gov/pub/irs-pdf/f1040s1.pdf
"""
from __future__ import annotations

from dataclasses import dataclass, fields as dc_fields
from decimal import Decimal
from pathlib import Path

from skill.scripts.calc.engine import (
    _sum_adjustments,
    _sum_part_i_additional_income,
    schedule_c_net_profit,
    schedule_e_total_net,
)
from skill.scripts.models import AdjustmentsToIncome, CanonicalReturn


_ZERO = Decimal("0")

# Schedule SE constants re-used for line 15 (deductible half of SE tax).
# Imported here to avoid a circular dependency on schedule_se output module.
_SE_NET_EARNINGS_FRACTION = Decimal("0.9235")
_SE_SS_RATE = Decimal("0.124")
_SE_MEDICARE_RATE = Decimal("0.029")
_SE_HALF = Decimal("0.5")
_SS_WAGE_BASE_TY2025 = Decimal("176100")


# ---------------------------------------------------------------------------
# Layer 1: field dataclass and computation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Schedule1Fields:
    """Frozen snapshot of Schedule 1 line values, ready for rendering.

    Field names follow the TY2025 Schedule 1 (Form 1040) line numbers.
    All numeric fields are ``Decimal``; header fields are ``str``.
    """

    # Header
    taxpayer_name: str = ""
    taxpayer_ssn: str = ""

    # ------------------------------------------------------------------
    # Part I — Additional Income
    # ------------------------------------------------------------------
    line_1_taxable_refunds: Decimal = _ZERO
    line_2a_alimony_received: Decimal = _ZERO
    line_3_business_income: Decimal = _ZERO
    line_4_other_gains: Decimal = _ZERO
    line_5_rental_real_estate: Decimal = _ZERO
    line_6_farm_income: Decimal = _ZERO
    line_7_unemployment: Decimal = _ZERO
    line_8z_other_income: Decimal = _ZERO
    line_9_obbba_schedule_1a: Decimal = _ZERO
    line_10_total_additional_income: Decimal = _ZERO

    # ------------------------------------------------------------------
    # Part II — Adjustments to Income
    # ------------------------------------------------------------------
    line_11_educator_expenses: Decimal = _ZERO
    line_12_business_expenses_reservists: Decimal = _ZERO
    line_13_hsa_deduction: Decimal = _ZERO
    line_14_moving_expenses_military: Decimal = _ZERO
    line_15_deductible_se_tax: Decimal = _ZERO
    line_16_se_retirement_plans: Decimal = _ZERO
    line_17_se_health_insurance: Decimal = _ZERO
    line_18_penalty_early_withdrawal: Decimal = _ZERO
    line_19_alimony_paid: Decimal = _ZERO
    line_20_ira_deduction: Decimal = _ZERO
    line_21_student_loan_interest: Decimal = _ZERO
    line_22_reserved: Decimal = _ZERO
    line_23_archer_msa: Decimal = _ZERO
    line_24z_other_adjustments: Decimal = _ZERO
    line_25_obbba_adjustments: Decimal = _ZERO
    line_26_total_adjustments: Decimal = _ZERO

    # ------------------------------------------------------------------
    # Net: line 10 - line 26 -> Form 1040 line 8
    # ------------------------------------------------------------------
    schedule_1_net: Decimal = _ZERO


def _dec(x) -> Decimal:
    """Coerce an optional/numeric value to Decimal (None -> 0)."""
    if x is None:
        return _ZERO
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def _compute_deductible_half_se_tax(return_: CanonicalReturn) -> Decimal:
    """Compute the deductible half of SE tax (Schedule 1 line 15).

    This mirrors the Schedule SE computation from
    ``skill.scripts.output.schedule_se.compute_schedule_se_fields`` but
    only produces the line 13 (= half SE tax) result.  We replicate the
    formula here to avoid a module-level import cycle between schedule_1
    and schedule_se.

    The engine's ``AdjustmentsToIncome.deductible_se_tax`` is computed by
    tenforty and excluded from ``_sum_adjustments`` to avoid double-counting.
    We independently compute it here for the Schedule 1 line display.
    """
    se_net_profit = sum(
        (schedule_c_net_profit(sc) for sc in return_.schedules_c),
        start=_ZERO,
    )
    if se_net_profit <= _ZERO:
        return _ZERO

    line_4a = se_net_profit * _SE_NET_EARNINGS_FRACTION
    line_6 = max(line_4a, _ZERO)

    # W-2 SS wages for wage-base cap
    w2_ss = _ZERO
    for w2 in return_.w2s:
        w2_ss += _dec(w2.box3_social_security_wages)
        w2_ss += _dec(w2.box7_social_security_tips)

    remaining_ss_base = max(_ZERO, _SS_WAGE_BASE_TY2025 - w2_ss)
    ss_portion = min(line_6, remaining_ss_base) * _SE_SS_RATE
    if ss_portion < _ZERO:
        ss_portion = _ZERO
    medicare_portion = line_6 * _SE_MEDICARE_RATE
    se_tax = ss_portion + medicare_portion
    return se_tax * _SE_HALF


def compute_schedule_1_fields(canonical: CanonicalReturn) -> Schedule1Fields:
    """Map a ``CanonicalReturn`` onto a ``Schedule1Fields`` dataclass.

    This function delegates to engine helpers for the heavy lifting and
    does NOT re-derive values the engine already computes.  It is safe to
    call on any ``CanonicalReturn`` — even one that has not been passed
    through ``engine.compute``.
    """
    adj = canonical.adjustments

    # -- header --------------------------------------------------------
    taxpayer_name = f"{canonical.taxpayer.first_name} {canonical.taxpayer.last_name}"
    taxpayer_ssn = canonical.taxpayer.ssn or ""

    # ------------------------------------------------------------------
    # Part I — Additional Income
    # ------------------------------------------------------------------

    # Line 1: taxable refunds from 1099-G box 2
    line_1 = sum(
        (f.box2_state_or_local_income_tax_refund for f in canonical.forms_1099_g),
        start=_ZERO,
    )

    # Line 2a: alimony received (from other_income escape hatch)
    line_2a = _dec(canonical.other_income.get("alimony_received", _ZERO))

    # Line 3: business income/loss (Schedule C net profit, summed)
    line_3 = sum(
        (schedule_c_net_profit(sc) for sc in canonical.schedules_c),
        start=_ZERO,
    )

    # Line 4: other gains/losses (Form 4797) — DEFERRED
    line_4 = _ZERO

    # Line 5: rental real estate (Schedule E total net, summed)
    line_5 = sum(
        (schedule_e_total_net(se) for se in canonical.schedules_e),
        start=_ZERO,
    )

    # Line 6: farm income — DEFERRED
    line_6 = _ZERO

    # Line 7: unemployment compensation (1099-G box 1)
    line_7 = sum(
        (f.box1_unemployment_compensation for f in canonical.forms_1099_g),
        start=_ZERO,
    )

    # Line 8z: other income (catch-all)
    # Sum numeric values from the other_income dict, excluding
    # alimony_received which is already on line 2a.
    line_8z = _ZERO
    for key, val in canonical.other_income.items():
        if key == "alimony_received":
            continue
        try:
            line_8z += _dec(val)
        except (TypeError, ValueError, ArithmeticError):
            pass

    # Line 9: OBBBA Schedule 1-A (tips + overtime deductions)
    # These are adjustments (reduce AGI) but appear on Schedule 1-A
    # which feeds into Schedule 1 line 9.
    line_9 = (
        adj.qualified_tips_deduction_schedule_1a
        + adj.qualified_overtime_deduction_schedule_1a
    )

    # Line 10: total additional income
    line_10 = (
        line_1 + line_2a + line_3 + line_4 + line_5
        + line_6 + line_7 + line_8z + line_9
    )

    # ------------------------------------------------------------------
    # Part II — Adjustments to Income
    # ------------------------------------------------------------------

    line_11 = adj.educator_expenses
    line_12 = _ZERO  # reservists / performing artists — DEFERRED
    line_13 = adj.hsa_deduction
    line_14 = adj.moving_expenses_military
    line_15 = _compute_deductible_half_se_tax(canonical)
    line_16 = adj.se_retirement_plans
    line_17 = adj.se_health_insurance
    line_18 = adj.penalty_on_early_withdrawal_of_savings
    line_19 = adj.alimony_paid
    line_20 = adj.ira_deduction
    line_21 = adj.student_loan_interest
    line_22 = _ZERO  # reserved
    line_23 = adj.archer_msa_deduction

    # Line 24z: other adjustments (catch-all dict)
    line_24z = sum(adj.other_adjustments.values(), start=_ZERO)

    # Line 25: OBBBA adjustments (senior deduction + Form 4547)
    # Form 4547 is excluded per engine policy (always $0).
    line_25 = adj.senior_deduction_obbba

    # Line 26: total adjustments
    line_26 = (
        line_11 + line_12 + line_13 + line_14 + line_15
        + line_16 + line_17 + line_18 + line_19 + line_20
        + line_21 + line_22 + line_23 + line_24z + line_25
    )

    # Net: line 10 - line 26
    net = line_10 - line_26

    return Schedule1Fields(
        taxpayer_name=taxpayer_name,
        taxpayer_ssn=taxpayer_ssn,
        # Part I
        line_1_taxable_refunds=line_1,
        line_2a_alimony_received=line_2a,
        line_3_business_income=line_3,
        line_4_other_gains=line_4,
        line_5_rental_real_estate=line_5,
        line_6_farm_income=line_6,
        line_7_unemployment=line_7,
        line_8z_other_income=line_8z,
        line_9_obbba_schedule_1a=line_9,
        line_10_total_additional_income=line_10,
        # Part II
        line_11_educator_expenses=line_11,
        line_12_business_expenses_reservists=line_12,
        line_13_hsa_deduction=line_13,
        line_14_moving_expenses_military=line_14,
        line_15_deductible_se_tax=line_15,
        line_16_se_retirement_plans=line_16,
        line_17_se_health_insurance=line_17,
        line_18_penalty_early_withdrawal=line_18,
        line_19_alimony_paid=line_19,
        line_20_ira_deduction=line_20,
        line_21_student_loan_interest=line_21,
        line_22_reserved=line_22,
        line_23_archer_msa=line_23,
        line_24z_other_adjustments=line_24z,
        line_25_obbba_adjustments=line_25,
        line_26_total_adjustments=line_26,
        schedule_1_net=net,
    )


def schedule_1_required(canonical: CanonicalReturn) -> bool:
    """Return ``True`` if Schedule 1 must be attached to the return.

    Schedule 1 is required whenever there is any additional income
    (Part I) or any adjustment (Part II).  The trigger checks the
    canonical return for the presence of forms/fields that would produce
    a nonzero line on Schedule 1.
    """
    # Quick structural checks (avoid computing fields when possible)
    if canonical.schedules_c:
        return True
    if canonical.schedules_e:
        return True
    if canonical.forms_1099_g:
        return True
    if canonical.other_income:
        return True
    if canonical.adjustments != AdjustmentsToIncome():
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


def render_schedule_1_pdf(fields: Schedule1Fields, out_path: Path) -> Path:
    """Render a Schedule 1 scaffold PDF using reportlab.

    This is a minimal tabular layout listing every line name and value
    so a human can eyeball the return.  It is NOT a filled IRS Schedule 1.
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
        1 * inch, height - 1 * inch,
        "Schedule 1 (Form 1040) — Additional Income and Adjustments",
    )
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
    c.drawString(1 * inch, y, "Part I — Additional Income")
    y -= 18

    _line("1   Taxable refunds of state/local taxes", _format_decimal(fields.line_1_taxable_refunds))
    _line("2a  Alimony received", _format_decimal(fields.line_2a_alimony_received))
    _line("3   Business income or (loss) (Schedule C)", _format_decimal(fields.line_3_business_income))
    _line("4   Other gains or (losses) (Form 4797)", _format_decimal(fields.line_4_other_gains))
    _line("5   Rental real estate, royalties, partnerships, S corps", _format_decimal(fields.line_5_rental_real_estate))
    _line("6   Farm income or (loss) (Schedule F)", _format_decimal(fields.line_6_farm_income))
    _line("7   Unemployment compensation", _format_decimal(fields.line_7_unemployment))
    _line("8z  Other income", _format_decimal(fields.line_8z_other_income))
    _line("9   OBBBA Schedule 1-A total", _format_decimal(fields.line_9_obbba_schedule_1a))
    _line("10  Total additional income (sum of Part I)", _format_decimal(fields.line_10_total_additional_income))

    y -= 10
    c.setFont("Helvetica-Bold", 11)
    c.drawString(1 * inch, y, "Part II — Adjustments to Income")
    y -= 18

    _line("11  Educator expenses", _format_decimal(fields.line_11_educator_expenses))
    _line("12  Business expenses of reservists, etc.", _format_decimal(fields.line_12_business_expenses_reservists))
    _line("13  HSA deduction", _format_decimal(fields.line_13_hsa_deduction))
    _line("14  Moving expenses for Armed Forces", _format_decimal(fields.line_14_moving_expenses_military))
    _line("15  Deductible part of self-employment tax", _format_decimal(fields.line_15_deductible_se_tax))
    _line("16  Self-employed SEP, SIMPLE, qualified plans", _format_decimal(fields.line_16_se_retirement_plans))
    _line("17  Self-employed health insurance deduction", _format_decimal(fields.line_17_se_health_insurance))
    _line("18  Penalty on early withdrawal of savings", _format_decimal(fields.line_18_penalty_early_withdrawal))
    _line("19  Alimony paid", _format_decimal(fields.line_19_alimony_paid))
    _line("20  IRA deduction", _format_decimal(fields.line_20_ira_deduction))
    _line("21  Student loan interest deduction", _format_decimal(fields.line_21_student_loan_interest))
    _line("22  Reserved", _format_decimal(fields.line_22_reserved))
    _line("23  Archer MSA deduction", _format_decimal(fields.line_23_archer_msa))
    _line("24z Other adjustments", _format_decimal(fields.line_24z_other_adjustments))
    _line("25  OBBBA adjustments (senior deduction)", _format_decimal(fields.line_25_obbba_adjustments))
    _line("26  Total adjustments (sum of Part II)", _format_decimal(fields.line_26_total_adjustments))

    y -= 10
    c.setFont("Helvetica-Bold", 11)
    c.drawString(1 * inch, y, "Schedule 1 Net (line 10 - line 26) -> Form 1040 line 8")
    y -= 18
    _line("Net", _format_decimal(fields.schedule_1_net))

    c.save()
    return out_path


__all__ = [
    "Schedule1Fields",
    "compute_schedule_1_fields",
    "render_schedule_1_pdf",
    "schedule_1_required",
]
