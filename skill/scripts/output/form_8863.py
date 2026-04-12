"""Form 8863 -- Education Credits (AOTC + LLC) -- compute + render.

Computes the American Opportunity Tax Credit (AOTC) and Lifetime Learning
Credit (LLC) from per-student education expense data on the canonical
return.

Architecture (two-layer, matches :mod:`form_8962`):

* **Layer 1** -- :func:`compute_form_8863_fields` maps a
  :class:`CanonicalReturn` onto a :class:`Form8863Fields` frozen
  dataclass.  The computation follows IRS Form 8863 (TY2025) rules:

    - AOTC: up to $2,500/student (100% of first $2k + 25% of next $2k).
      40% refundable.  First 4 years only.  Must be half-time.  No
      felony drug conviction.  MAGI phase-out: $80k-$90k Single/HOH,
      $160k-$180k MFJ.
    - LLC: 20% of up to $10k total qualified expenses (aggregate, not
      per-student).  Nonrefundable only.  Same MAGI phase-out ranges.
    - Students who fail AOTC eligibility automatically become
      LLC-eligible.

* **Layer 2** -- :func:`render_form_8863_pdf` is a reportlab scaffold
  that emits a human-readable summary PDF.

TY2025 constants
----------------

AOTC phase-out thresholds (MAGI):
    Single / HOH:  $80,000 - $90,000
    MFJ:           $160,000 - $180,000

LLC phase-out thresholds (MAGI):
    Single / HOH:  $80,000 - $90,000
    MFJ:           $160,000 - $180,000

Authority: IRS Form 8863 (TY2025) and Instructions for Form 8863.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from skill.scripts.models import CanonicalReturn, FilingStatus

_ZERO = Decimal("0")
_CENTS = Decimal("0.01")


# ---------------------------------------------------------------------------
# TY2025 phase-out thresholds
# ---------------------------------------------------------------------------

# AOTC and LLC share the same MAGI phase-out ranges for TY2025.
_AOTC_SINGLE_LOWER = Decimal("80000")
_AOTC_SINGLE_UPPER = Decimal("90000")
_AOTC_MFJ_LOWER = Decimal("160000")
_AOTC_MFJ_UPPER = Decimal("180000")

_LLC_SINGLE_LOWER = Decimal("80000")
_LLC_SINGLE_UPPER = Decimal("90000")
_LLC_MFJ_LOWER = Decimal("160000")
_LLC_MFJ_UPPER = Decimal("180000")

# AOTC caps
_AOTC_MAX_PER_STUDENT = Decimal("2500")
_AOTC_FIRST_TIER = Decimal("2000")   # 100% of first $2k
_AOTC_SECOND_TIER = Decimal("2000")  # 25% of next $2k

# LLC caps
_LLC_MAX_EXPENSES = Decimal("10000")  # aggregate cap
_LLC_RATE = Decimal("0.20")           # 20% of qualified expenses


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _q(x: Decimal) -> Decimal:
    """Quantize to two decimal places."""
    return x.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _phase_out_factor(magi: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    """Compute the MAGI phase-out factor.

    Returns a Decimal in [0, 1]:
    - 1 if MAGI <= lower (full credit)
    - 0 if MAGI >= upper (no credit)
    - linear interpolation between
    """
    if magi <= lower:
        return Decimal("1")
    if magi >= upper:
        return _ZERO
    return _q((upper - magi) / (upper - lower))


def _get_phase_out_thresholds(
    filing_status: FilingStatus,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Return (aotc_lower, aotc_upper, llc_lower, llc_upper) for filing status."""
    if filing_status == FilingStatus.MFJ:
        return _AOTC_MFJ_LOWER, _AOTC_MFJ_UPPER, _LLC_MFJ_LOWER, _LLC_MFJ_UPPER
    # Single, HOH, MFS, QSS all use the single thresholds
    return _AOTC_SINGLE_LOWER, _AOTC_SINGLE_UPPER, _LLC_SINGLE_LOWER, _LLC_SINGLE_UPPER


def _compute_aotc_per_student(qualified_expenses: Decimal) -> Decimal:
    """Compute raw (pre-phase-out) AOTC for one student.

    100% of the first $2,000 + 25% of the next $2,000 = max $2,500.
    """
    if qualified_expenses <= _ZERO:
        return _ZERO
    first = min(qualified_expenses, _AOTC_FIRST_TIER)
    remaining = max(_ZERO, qualified_expenses - _AOTC_FIRST_TIER)
    second = min(remaining, _AOTC_SECOND_TIER) * Decimal("0.25")
    return _q(first + second)


# ---------------------------------------------------------------------------
# Layer 1 -- field dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StudentDetail:
    """Computed credit detail for one student."""
    name: str = ""
    ssn: str = ""
    institution_name: str = ""
    qualified_expenses: Decimal = _ZERO
    credit_type: str = ""  # "AOTC" or "LLC"
    raw_credit: Decimal = _ZERO  # before phase-out
    phased_credit: Decimal = _ZERO  # after phase-out
    nonrefundable: Decimal = _ZERO
    refundable: Decimal = _ZERO


@dataclass(frozen=True)
class Form8863Fields:
    """Frozen snapshot of Form 8863 line values, ready for rendering."""

    # Header
    taxpayer_name: str = ""
    taxpayer_ssn: str = ""

    # MAGI and phase-out
    magi: Decimal = _ZERO
    filing_status: str = ""
    aotc_phase_out_factor: Decimal = _ZERO
    llc_phase_out_factor: Decimal = _ZERO

    # Per-student details
    students: tuple[StudentDetail, ...] = ()

    # AOTC totals (after phase-out)
    total_aotc_nonrefundable: Decimal = _ZERO  # 60% of phased AOTC
    total_aotc_refundable: Decimal = _ZERO     # 40% of phased AOTC

    # LLC totals
    llc_qualified_expenses: Decimal = _ZERO  # aggregate, capped at $10k
    llc_raw_credit: Decimal = _ZERO          # 20% of expenses
    llc_phased_credit: Decimal = _ZERO       # after phase-out (nonrefundable)

    # Grand totals
    total_nonrefundable: Decimal = _ZERO  # AOTC nonrefundable + LLC
    total_refundable: Decimal = _ZERO     # AOTC refundable only

    warnings: list[str] = field(default_factory=list)


def compute_form_8863_fields(
    canonical: CanonicalReturn,
) -> Form8863Fields:
    """Compute all Form 8863 fields from a CanonicalReturn.

    The return should have been run through ``engine.compute`` first so
    that ``computed.adjusted_gross_income`` is populated.  If no education
    data is present, returns a default (zeroed) Form8863Fields.
    """
    warnings: list[str] = []

    if canonical.education is None or not canonical.education.students:
        return Form8863Fields(warnings=["No education credit data present"])

    # MAGI = AGI for education credit purposes (simplified; Form 8863
    # instructions say MAGI = AGI + certain foreign income exclusions,
    # but those are not modelled yet).
    agi = canonical.computed.adjusted_gross_income
    magi = agi if agi is not None else _ZERO

    aotc_lower, aotc_upper, llc_lower, llc_upper = _get_phase_out_thresholds(
        canonical.filing_status
    )
    aotc_factor = _phase_out_factor(magi, aotc_lower, aotc_upper)
    llc_factor = _phase_out_factor(magi, llc_lower, llc_upper)

    # Classify students
    aotc_students: list[StudentDetail] = []
    llc_expense_pool = _ZERO  # aggregate for LLC
    student_details: list[StudentDetail] = []

    for stu in canonical.education.students:
        is_aotc = (
            stu.is_aotc_eligible
            and not stu.completed_4_years
            and stu.half_time_student
            and not stu.felony_drug_conviction
        )

        if is_aotc:
            raw = _compute_aotc_per_student(stu.qualified_expenses)
            phased = _q(raw * aotc_factor)
            nonref = _q(phased * Decimal("0.60"))
            ref = _q(phased * Decimal("0.40"))
            detail = StudentDetail(
                name=stu.name,
                ssn=stu.ssn,
                institution_name=stu.institution_name,
                qualified_expenses=stu.qualified_expenses,
                credit_type="AOTC",
                raw_credit=raw,
                phased_credit=phased,
                nonrefundable=nonref,
                refundable=ref,
            )
            aotc_students.append(detail)
            student_details.append(detail)
        else:
            # Falls to LLC
            llc_expense_pool += stu.qualified_expenses
            detail = StudentDetail(
                name=stu.name,
                ssn=stu.ssn,
                institution_name=stu.institution_name,
                qualified_expenses=stu.qualified_expenses,
                credit_type="LLC",
                raw_credit=_ZERO,  # filled at aggregate level
                phased_credit=_ZERO,
                nonrefundable=_ZERO,
                refundable=_ZERO,
            )
            student_details.append(detail)

    # AOTC totals
    total_aotc_nonref = sum(
        (s.nonrefundable for s in aotc_students), _ZERO
    )
    total_aotc_ref = sum(
        (s.refundable for s in aotc_students), _ZERO
    )

    # LLC computation (aggregate)
    llc_expenses_capped = min(llc_expense_pool, _LLC_MAX_EXPENSES)
    llc_raw = _q(llc_expenses_capped * _LLC_RATE)
    llc_phased = _q(llc_raw * llc_factor)

    # Grand totals
    total_nonref = _q(total_aotc_nonref + llc_phased)
    total_ref = total_aotc_ref

    taxpayer_name = f"{canonical.taxpayer.first_name} {canonical.taxpayer.last_name}"

    return Form8863Fields(
        taxpayer_name=taxpayer_name,
        taxpayer_ssn=canonical.taxpayer.ssn,
        magi=magi,
        filing_status=canonical.filing_status.value,
        aotc_phase_out_factor=aotc_factor,
        llc_phase_out_factor=llc_factor,
        students=tuple(student_details),
        total_aotc_nonrefundable=total_aotc_nonref,
        total_aotc_refundable=total_aotc_ref,
        llc_qualified_expenses=llc_expenses_capped,
        llc_raw_credit=llc_raw,
        llc_phased_credit=llc_phased,
        total_nonrefundable=total_nonref,
        total_refundable=total_ref,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Layer 2 -- reportlab scaffold PDF rendering
# ---------------------------------------------------------------------------


def render_form_8863_pdf(fields: Form8863Fields, out_path: Path) -> Path:
    """Render a Form 8863 summary PDF using reportlab.

    This is a scaffold renderer that produces a human-readable summary.
    A future wave can replace it with an AcroForm overlay on the IRS
    fillable f8863.pdf when a widget map is created.

    Returns ``out_path`` for convenience.
    """
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.pdfgen import canvas
    except ImportError:
        # If reportlab is not available, write a minimal placeholder.
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"%PDF-1.4 placeholder - reportlab not installed")
        return out_path

    out_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(out_path), pagesize=letter)
    width, height = letter

    y = height - 1 * inch

    def _line(text: str, font: str = "Helvetica", size: int = 10) -> None:
        nonlocal y
        c.setFont(font, size)
        c.drawString(1 * inch, y, text)
        y -= size + 4

    _line("Form 8863 -- Education Credits (AOTC + LLC)", "Helvetica-Bold", 14)
    _line(f"Taxpayer: {fields.taxpayer_name}    SSN: {fields.taxpayer_ssn}")
    _line(f"Filing Status: {fields.filing_status}    MAGI: ${fields.magi:,.2f}")
    _line("")

    _line("Phase-Out Factors", "Helvetica-Bold", 11)
    _line(f"  AOTC phase-out factor: {fields.aotc_phase_out_factor}")
    _line(f"  LLC  phase-out factor: {fields.llc_phase_out_factor}")
    _line("")

    _line("Student Details", "Helvetica-Bold", 11)
    for stu in fields.students:
        _line(
            f"  {stu.name} (SSN: {stu.ssn}) -- {stu.credit_type}",
            "Helvetica-Bold",
            9,
        )
        _line(f"    Institution: {stu.institution_name}", size=9)
        _line(f"    Qualified expenses: ${stu.qualified_expenses:,.2f}", size=9)
        if stu.credit_type == "AOTC":
            _line(
                f"    Raw AOTC: ${stu.raw_credit:,.2f}  "
                f"Phased: ${stu.phased_credit:,.2f}  "
                f"Nonrefundable: ${stu.nonrefundable:,.2f}  "
                f"Refundable: ${stu.refundable:,.2f}",
                size=9,
            )
        else:
            _line("    (Expenses included in LLC aggregate)", size=9)
    _line("")

    _line("Credit Totals", "Helvetica-Bold", 11)
    _line(f"  AOTC nonrefundable (60%): ${fields.total_aotc_nonrefundable:,.2f}")
    _line(f"  AOTC refundable (40%):    ${fields.total_aotc_refundable:,.2f}")
    _line("")
    _line(f"  LLC qualified expenses:   ${fields.llc_qualified_expenses:,.2f}")
    _line(f"  LLC raw credit (20%):     ${fields.llc_raw_credit:,.2f}")
    _line(f"  LLC phased credit:        ${fields.llc_phased_credit:,.2f}")
    _line("")
    _line(f"  Total nonrefundable:      ${fields.total_nonrefundable:,.2f}")
    _line(f"  Total refundable:         ${fields.total_refundable:,.2f}")

    if fields.warnings:
        _line("")
        _line("Warnings:", "Helvetica-Bold", 10)
        for w in fields.warnings:
            _line(f"  - {w}", size=9)

    c.save()
    return out_path
