"""Form 2441 -- Child and Dependent Care Expenses -- compute + render.

This module implements the two-layer renderer pattern for Form 2441:

* **Layer 1** -- :func:`compute_form_2441_fields` maps a
  :class:`CanonicalReturn` onto a :class:`Form2441Fields` frozen
  dataclass whose attribute names mirror the Form 2441 (TY2025 layout)
  line numbers. The computation follows the IRS Form 2441 Part II credit
  worksheet exactly.

* **Layer 2** -- :func:`render_form_2441_pdf` writes a minimal tabular
  PDF using ``reportlab``. The table lists every line_name/value pair so
  a human can eyeball the return; this is NOT a filled IRS Form 2441 --
  a real AcroForm overlay on the IRS fillable PDF is a follow-up task.

Authority and citations
-----------------------

* IRS Form 2441 (TY2025): https://www.irs.gov/pub/irs-pdf/f2441.pdf
* IRS Instructions for Form 2441 (TY2025):
  https://www.irs.gov/pub/irs-pdf/i2441.pdf

TY2025 rules (Form 2441):

* Maximum qualifying expenses:
    * $3,000 for one qualifying person
    * $6,000 for two or more qualifying persons

* Credit rate: 35% of qualifying expenses, reduced by 1 percentage
  point for each $2,000 (or fraction thereof) of AGI over $15,000.
  Minimum rate is 20%.

* Credit rate formula:
    max(20, 35 - max(0, ceil((AGI - 15000) / 2000))) percent

* Both spouses must have earned income for MFJ. Lower earner's earned
  income caps qualifying expenses.

* Full-time student or disabled spouse treated as having $250/month
  (1 qualifying person) or $500/month (2+ qualifying persons) earned
  income.

* The credit is NONREFUNDABLE -- cannot exceed tax liability.

* Flows to Form 1040 Schedule 3, line 2.

Scope
-----

**Included**
    * Part II credit computation (lines 2-10)
    * Part III employer-provided benefits subtraction
    * Care provider pass-through (Part I)

**Excluded (TODO)**
    * AcroForm overlay on IRS fillable f2441.pdf
    * Full Part III lines 12-27 worksheet (simplified to a single
      subtraction of employer benefits from qualifying expenses)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from skill.scripts.models import (
    CanonicalReturn,
    FilingStatus,
)


_ZERO = Decimal("0")
_CENTS = Decimal("0.01")

# ---------------------------------------------------------------------------
# TY2025 Form 2441 constants
# ---------------------------------------------------------------------------

MAX_EXPENSES_ONE_PERSON = Decimal("3000")
MAX_EXPENSES_TWO_OR_MORE = Decimal("6000")

# Credit rate phase-down
BASE_CREDIT_RATE = 35  # percent
MIN_CREDIT_RATE = 20  # percent
AGI_PHASE_DOWN_START = Decimal("15000")
AGI_PHASE_DOWN_STEP = Decimal("2000")

# Full-time student / disabled spouse imputed earned income
IMPUTED_EARNED_INCOME_ONE = Decimal("250")  # per month, 1 qualifying person
IMPUTED_EARNED_INCOME_TWO_PLUS = Decimal("500")  # per month, 2+ qualifying persons
IMPUTED_MONTHS = 12  # full tax year


# ---------------------------------------------------------------------------
# Credit rate computation
# ---------------------------------------------------------------------------


def compute_credit_rate(agi: Decimal) -> int:
    """Compute the Form 2441 credit percentage based on AGI.

    Returns an integer percentage between 20 and 35 inclusive.

    Formula: max(20, 35 - max(0, ceil((AGI - 15000) / 2000)))

    Authority: IRS Form 2441 TY2025, line 9 table.
    """
    if agi <= AGI_PHASE_DOWN_START:
        return BASE_CREDIT_RATE
    excess = agi - AGI_PHASE_DOWN_START
    # ceil(excess / 2000) -- number of $2,000 increments (or fraction)
    reduction = math.ceil(float(excess) / float(AGI_PHASE_DOWN_STEP))
    return max(MIN_CREDIT_RATE, BASE_CREDIT_RATE - reduction)


# ---------------------------------------------------------------------------
# Layer 1 -- field dataclass and computation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Form2441Fields:
    """Frozen snapshot of Form 2441 line values, ready for rendering.

    Field names follow the TY2025 Form 2441 line numbers. All numeric
    fields are :class:`Decimal`.
    """

    # Header
    taxpayer_name: str = ""
    taxpayer_ssn: str = ""
    filing_status: str = ""

    # Part I -- Care provider info (pass-through)
    care_providers: tuple[dict[str, Any], ...] = ()

    # Part II -- Credit computation
    line_2_qualifying_persons: int = 0
    line_3_num_qualifying_persons: int = 0
    line_4_qualified_expenses: Decimal = _ZERO
    """Expenses subject to the $3k/$6k cap."""
    line_5_earned_income_taxpayer: Decimal = _ZERO
    line_6_earned_income_spouse: Decimal = _ZERO
    line_7_smallest_of_4_5_6: Decimal = _ZERO
    line_8_agi: Decimal = _ZERO
    line_9_credit_rate_pct: int = 0
    line_10_credit: Decimal = _ZERO

    # Part III -- Employer-provided benefits (simplified)
    line_12_employer_benefits: Decimal = _ZERO
    line_24_adjusted_expenses: Decimal = _ZERO
    """Qualifying expenses after subtracting employer benefits."""


def _earned_income_for_taxpayer(canonical: CanonicalReturn) -> Decimal:
    """Sum W-2 wages and Schedule C net profit for the primary taxpayer."""
    total = _ZERO
    for w2 in canonical.w2s:
        if w2.employee_is_taxpayer:
            total += w2.box1_wages
    for sc in canonical.schedules_c:
        if getattr(sc, "belongs_to_spouse", False):
            continue
        net = getattr(sc, "net_profit_or_loss", None)
        if net is not None and net > _ZERO:
            total += net
    return total


def _earned_income_for_spouse(canonical: CanonicalReturn) -> Decimal:
    """Sum W-2 wages and Schedule C net profit for the spouse."""
    total = _ZERO
    for w2 in canonical.w2s:
        if not w2.employee_is_taxpayer:
            total += w2.box1_wages
    for sc in canonical.schedules_c:
        if getattr(sc, "belongs_to_spouse", False):
            net = getattr(sc, "net_profit_or_loss", None)
            if net is not None and net > _ZERO:
                total += net
    return total


def compute_form_2441_fields(
    canonical: CanonicalReturn,
) -> Form2441Fields:
    """Map a CanonicalReturn onto a Form2441Fields dataclass.

    The input ``canonical`` should have been passed through
    :func:`skill.scripts.calc.engine.compute` first so that
    ``computed.adjusted_gross_income`` is populated. If AGI is not yet
    computed, this function uses zero.

    Returns a frozen :class:`Form2441Fields` instance.
    """
    dc = canonical.dependent_care
    if dc is None:
        return Form2441Fields()

    # Header
    tp_name = f"{canonical.taxpayer.first_name} {canonical.taxpayer.last_name}"
    tp_ssn = canonical.taxpayer.ssn
    fs = canonical.filing_status.value

    # Part I -- care providers (pass-through)
    providers = tuple(dc.care_providers)

    # Part II
    num_qualifying = dc.qualifying_persons

    # Line 4: expense cap
    expense_cap = (
        MAX_EXPENSES_ONE_PERSON if num_qualifying <= 1 else MAX_EXPENSES_TWO_OR_MORE
    )
    # Subtract employer benefits from total expenses first
    employer_benefits = dc.employer_benefits_excluded
    net_expenses = max(_ZERO, dc.total_expenses_paid - employer_benefits)

    # Cap at $3k/$6k
    qualified_expenses = min(net_expenses, expense_cap)

    # Lines 5-6: earned income
    earned_tp = _earned_income_for_taxpayer(canonical)
    earned_sp = _ZERO

    is_joint = canonical.filing_status == FilingStatus.MFJ
    if is_joint:
        earned_sp = _earned_income_for_spouse(canonical)

    # Line 7: smallest of line 4, 5, 6 (line 6 only for MFJ)
    if is_joint:
        line_7 = min(qualified_expenses, earned_tp, earned_sp)
    else:
        line_7 = min(qualified_expenses, earned_tp)

    # Line 8: AGI
    agi = canonical.computed.adjusted_gross_income or _ZERO

    # Line 9: credit percentage
    credit_rate = compute_credit_rate(agi)

    # Line 10: credit amount
    credit = (line_7 * Decimal(credit_rate) / Decimal(100)).quantize(
        _CENTS, rounding=ROUND_HALF_UP
    )

    return Form2441Fields(
        taxpayer_name=tp_name,
        taxpayer_ssn=tp_ssn,
        filing_status=fs,
        care_providers=providers,
        line_2_qualifying_persons=num_qualifying,
        line_3_num_qualifying_persons=num_qualifying,
        line_4_qualified_expenses=qualified_expenses,
        line_5_earned_income_taxpayer=earned_tp,
        line_6_earned_income_spouse=earned_sp,
        line_7_smallest_of_4_5_6=line_7,
        line_8_agi=agi,
        line_9_credit_rate_pct=credit_rate,
        line_10_credit=credit,
        line_12_employer_benefits=employer_benefits,
        line_24_adjusted_expenses=qualified_expenses,
    )


# ---------------------------------------------------------------------------
# Layer 2 -- reportlab scaffold renderer
# ---------------------------------------------------------------------------


def render_form_2441_pdf(fields: Form2441Fields, out_path: Path) -> Path:
    """Render a Form 2441 PDF scaffold using reportlab.

    Produces a simple tabular PDF listing every field and its value.
    This is a human-readable scaffold, not a filled IRS form. A future
    AcroForm overlay will replace this once the widget map for f2441.pdf
    is researched.

    Returns ``out_path`` for convenience.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    c = canvas.Canvas(str(out_path), pagesize=letter)
    width, height = letter

    # Title
    c.setFont("Helvetica-Bold", 14)
    c.drawString(
        1 * inch, height - 1 * inch,
        "Form 2441 -- Child and Dependent Care Expenses (TY2025)",
    )

    c.setFont("Helvetica", 10)
    y = height - 1.5 * inch

    # Header
    header_lines = [
        ("Taxpayer", fields.taxpayer_name),
        ("SSN", fields.taxpayer_ssn),
        ("Filing Status", fields.filing_status),
    ]
    for label, value in header_lines:
        c.drawString(1 * inch, y, f"{label}: {value}")
        y -= 0.25 * inch

    y -= 0.25 * inch

    # Part I -- Care Providers
    c.setFont("Helvetica-Bold", 11)
    c.drawString(1 * inch, y, "Part I -- Care Providers")
    y -= 0.3 * inch
    c.setFont("Helvetica", 9)
    for i, provider in enumerate(fields.care_providers):
        name = provider.get("name", "N/A")
        amount = provider.get("amount_paid", "N/A")
        c.drawString(1.2 * inch, y, f"  Provider {i + 1}: {name} -- ${amount}")
        y -= 0.2 * inch
    if not fields.care_providers:
        c.drawString(1.2 * inch, y, "  (none listed)")
        y -= 0.2 * inch

    y -= 0.25 * inch

    # Part II -- Credit Computation
    c.setFont("Helvetica-Bold", 11)
    c.drawString(1 * inch, y, "Part II -- Credit Computation")
    y -= 0.3 * inch
    c.setFont("Helvetica", 10)

    part_ii_lines = [
        ("Line 2/3", f"Qualifying persons: {fields.line_3_num_qualifying_persons}"),
        ("Line 4", f"Qualified expenses (after cap): ${fields.line_4_qualified_expenses}"),
        ("Line 5", f"Earned income (taxpayer): ${fields.line_5_earned_income_taxpayer}"),
        ("Line 6", f"Earned income (spouse): ${fields.line_6_earned_income_spouse}"),
        ("Line 7", f"Smallest of 4, 5, 6: ${fields.line_7_smallest_of_4_5_6}"),
        ("Line 8", f"AGI: ${fields.line_8_agi}"),
        ("Line 9", f"Credit rate: {fields.line_9_credit_rate_pct}%"),
        ("Line 10", f"Credit amount: ${fields.line_10_credit}"),
    ]
    for label, value in part_ii_lines:
        c.drawString(1.2 * inch, y, f"{label}  {value}")
        y -= 0.25 * inch

    y -= 0.25 * inch

    # Part III -- Employer Benefits
    c.setFont("Helvetica-Bold", 11)
    c.drawString(1 * inch, y, "Part III -- Employer-Provided Benefits")
    y -= 0.3 * inch
    c.setFont("Helvetica", 10)

    part_iii_lines = [
        ("Line 12", f"Employer benefits excluded: ${fields.line_12_employer_benefits}"),
        ("Line 24", f"Adjusted expenses: ${fields.line_24_adjusted_expenses}"),
    ]
    for label, value in part_iii_lines:
        c.drawString(1.2 * inch, y, f"{label}  {value}")
        y -= 0.25 * inch

    # Footer
    y -= 0.5 * inch
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(
        1 * inch, y,
        "SCAFFOLD -- This is a human-readable summary, not a filled IRS Form 2441.",
    )

    c.save()
    return out_path


__all__ = [
    "Form2441Fields",
    "compute_credit_rate",
    "compute_form_2441_fields",
    "render_form_2441_pdf",
    "MAX_EXPENSES_ONE_PERSON",
    "MAX_EXPENSES_TWO_OR_MORE",
    "BASE_CREDIT_RATE",
    "MIN_CREDIT_RATE",
    "AGI_PHASE_DOWN_START",
    "AGI_PHASE_DOWN_STEP",
]
