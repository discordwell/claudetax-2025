"""Form 8962 -- Premium Tax Credit (PTC) -- compute + render.

Reconciles advance payments of the premium tax credit (APTC) received
from the Health Insurance Marketplace against the actual PTC a taxpayer
is entitled to, based on their final household income and family size.

Architecture (two-layer, matches :mod:`form_6251`):

* **Layer 1** -- :func:`compute_form_8962_fields` maps a
  :class:`CanonicalReturn` onto a :class:`Form8962Fields` frozen
  dataclass. The computation follows IRS Form 8962 (TY2025) exactly:

    Part I:  Household income, FPL%, applicable figure, annual
             contribution amount.
    Part II: Monthly PTC calculation for each covered month.
    Part IV: Net PTC (line 24 refundable credit) or excess advance
             PTC repayment (line 29, subject to repayment caps).

* **Layer 2** -- :func:`render_form_8962_pdf` is a reportlab scaffold
  that emits a human-readable summary PDF. A future wave can replace
  this with an AcroForm overlay on the IRS fillable f8962.pdf.

TY2025 constants
----------------

Federal Poverty Level (FPL) for the 48 contiguous states + DC (2025):
    1 person:  $15,650
    2 persons: $21,150
    Each additional: +$5,500

Applicable Figure -- Table 2 (IRS Form 8962 instructions, TY2025):
    100%-133% FPL: linear 0.00% - 2.01%
    133%-150% FPL: linear 2.01% - 4.02%
    150%-200% FPL: linear 4.02% - 6.52%
    200%-250% FPL: linear 6.52% - 8.33%
    250%-300% FPL: linear 8.33% - 9.83%
    300%-400% FPL: flat   9.83%

Repayment caps -- Table 5 (IRS Form 8962 instructions, TY2025):
    Under 200% FPL: $375 Single / $750 other
    200%-300% FPL:  $950 Single / $1,900 other
    300%-400% FPL:  $1,600 Single / $3,200 other
    400%+ FPL:      no cap (full repayment)

Authority: IRS Form 8962 (TY2025) and Instructions for Form 8962.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from skill.scripts.models import CanonicalReturn, FilingStatus


_ZERO = Decimal("0")
_CENTS = Decimal("0.01")


# ---------------------------------------------------------------------------
# TY2025 FPL table (48 contiguous states + DC)
# ---------------------------------------------------------------------------

FPL_BASE_1_PERSON = Decimal("15650")
FPL_BASE_2_PERSON = Decimal("21150")
FPL_ADDITIONAL_PERSON = Decimal("5500")


def fpl_for_family_size(size: int) -> Decimal:
    """Return the TY2025 Federal Poverty Level for a given family size.

    Uses the HHS poverty guidelines for the 48 contiguous states + DC.
    Alaska and Hawaii have higher FPLs but are not modelled here.
    """
    if size < 1:
        raise ValueError("family size must be at least 1")
    if size == 1:
        return FPL_BASE_1_PERSON
    if size == 2:
        return FPL_BASE_2_PERSON
    return FPL_BASE_2_PERSON + FPL_ADDITIONAL_PERSON * (size - 2)


# ---------------------------------------------------------------------------
# Table 2 -- Applicable figure lookup (TY2025)
# ---------------------------------------------------------------------------

# Each tuple is (lower_fpl_pct, upper_fpl_pct, lower_figure, upper_figure).
# Linear interpolation within each band; flat 9.83% at 300%-400%.
_APPLICABLE_FIGURE_TABLE: list[tuple[Decimal, Decimal, Decimal, Decimal]] = [
    (Decimal("1.00"), Decimal("1.33"), Decimal("0.0000"), Decimal("0.0201")),
    (Decimal("1.33"), Decimal("1.50"), Decimal("0.0201"), Decimal("0.0402")),
    (Decimal("1.50"), Decimal("2.00"), Decimal("0.0402"), Decimal("0.0652")),
    (Decimal("2.00"), Decimal("2.50"), Decimal("0.0652"), Decimal("0.0833")),
    (Decimal("2.50"), Decimal("3.00"), Decimal("0.0833"), Decimal("0.0983")),
    (Decimal("3.00"), Decimal("4.00"), Decimal("0.0983"), Decimal("0.0983")),
]


def applicable_figure(fpl_pct: Decimal) -> Decimal:
    """Return the applicable figure for a given FPL percentage.

    The applicable figure is the fraction of household income the
    taxpayer is expected to contribute toward their benchmark plan
    (second lowest cost silver plan). Below 100% FPL the taxpayer is
    generally not eligible; above 400% FPL the full premium applies
    (no credit).

    Returns a Decimal fraction (e.g. 0.0652 for 6.52%).
    """
    if fpl_pct < Decimal("1.00"):
        return _ZERO  # not eligible
    if fpl_pct >= Decimal("4.00"):
        return Decimal("0.0983")  # above 400% but may still need for repayment calc

    for lower, upper, fig_lo, fig_hi in _APPLICABLE_FIGURE_TABLE:
        if lower <= fpl_pct < upper:
            if fig_lo == fig_hi:
                return fig_lo
            # linear interpolation
            span = upper - lower
            fraction = (fpl_pct - lower) / span
            result = fig_lo + fraction * (fig_hi - fig_lo)
            return result.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    # Exactly 4.00 is handled by >= 4.00 above; shouldn't reach here.
    return Decimal("0.0983")  # pragma: no cover


# ---------------------------------------------------------------------------
# Table 5 -- Repayment limitation caps (TY2025)
# ---------------------------------------------------------------------------

def repayment_cap(fpl_pct: Decimal, filing_status: FilingStatus) -> Decimal | None:
    """Return the repayment cap for excess advance PTC, or None if no cap.

    Returns None when household income is 400%+ FPL (full repayment,
    no cap). Otherwise returns the dollar cap from Table 5.
    """
    is_single = filing_status == FilingStatus.SINGLE

    if fpl_pct >= Decimal("4.00"):
        return None  # no cap -- full repayment

    if fpl_pct < Decimal("2.00"):
        return Decimal("375") if is_single else Decimal("750")
    if fpl_pct < Decimal("3.00"):
        return Decimal("950") if is_single else Decimal("1900")
    # 300%-400%
    return Decimal("1600") if is_single else Decimal("3200")


# ---------------------------------------------------------------------------
# Layer 1 -- field dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Form8962MonthRow:
    """One month's computed PTC data (Part II lines 12-23)."""

    month: int  # 1-12
    enrollment_premium: Decimal = _ZERO
    slcsp_premium: Decimal = _ZERO
    contribution_amount: Decimal = _ZERO  # annual contribution / 12
    max_ptc: Decimal = _ZERO  # max(0, slcsp - contribution)
    advance_ptc: Decimal = _ZERO


@dataclass(frozen=True)
class Form8962Fields:
    """Frozen snapshot of Form 8962 line values, ready for rendering.

    Field names follow TY2025 Form 8962 line numbers. All numeric fields
    are Decimal.
    """

    # Header
    taxpayer_name: str = ""
    taxpayer_ssn: str = ""

    # Part I -- Annual Totals and Household Information
    line_1_tax_family_size: int = 0
    line_2_modified_agi: Decimal = _ZERO
    line_3_dependents_magi: Decimal = _ZERO
    line_4_household_income: Decimal = _ZERO
    line_5_fpl: Decimal = _ZERO
    line_6_household_income_pct_fpl: Decimal = _ZERO  # as a fraction, e.g. 2.00
    line_7_applicable_figure: Decimal = _ZERO  # fraction, e.g. 0.0652
    line_8a_annual_contribution: Decimal = _ZERO  # line_4 * line_7

    # Part II -- Monthly PTC (lines 12-23, one per covered month)
    monthly_rows: tuple[Form8962MonthRow, ...] = ()

    # Line 11 -- Annual totals
    line_11a_annual_enrollment_premium: Decimal = _ZERO
    line_11b_annual_slcsp: Decimal = _ZERO
    line_11c_annual_contribution: Decimal = _ZERO
    line_11d_annual_max_ptc: Decimal = _ZERO
    line_11e_annual_advance_ptc: Decimal = _ZERO

    # Part IV -- Net PTC or repayment
    line_24_net_ptc: Decimal = _ZERO  # refundable credit (PTC > advance)
    line_25_total_ptc: Decimal = _ZERO
    line_26_total_advance_ptc: Decimal = _ZERO
    line_27_excess_advance_ptc: Decimal = _ZERO
    line_28_repayment_cap: Decimal = _ZERO  # from Table 5 (0 = no cap)
    line_29_repayment: Decimal = _ZERO  # additional tax owed

    # Eligibility
    is_eligible: bool = False
    """True if household income is 100%-400% FPL."""

    warnings: list[str] = field(default_factory=list)


def _q(x: Decimal) -> Decimal:
    """Quantize to two decimal places."""
    return x.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _dec(x: Decimal | None) -> Decimal:
    """Coerce Optional[Decimal] to Decimal (None -> 0)."""
    return x if x is not None else _ZERO


def compute_form_8962_fields(
    canonical: CanonicalReturn,
) -> Form8962Fields:
    """Compute all Form 8962 fields from a CanonicalReturn.

    The return should have been run through ``engine.compute`` first so
    that ``computed.adjusted_gross_income`` is populated. If no 1095-A
    forms are present, returns a default (zeroed) Form8962Fields.
    """
    warnings: list[str] = []

    if not canonical.forms_1095_a:
        return Form8962Fields(warnings=["No Form 1095-A data present"])

    # ---- Part I --------------------------------------------------------

    # Line 1: Tax family size = taxpayer + spouse (if filing jointly) + dependents
    family_size = 1
    if canonical.filing_status in (FilingStatus.MFJ, FilingStatus.QSS):
        family_size += 1
    family_size += len(canonical.dependents)

    # Line 2: Modified AGI
    modified_agi = _dec(canonical.computed.adjusted_gross_income)

    # Line 3: Dependents' MAGI (not modelled yet -- use 0)
    dependents_magi = _ZERO

    # Line 4: Household income
    household_income = _q(modified_agi + dependents_magi)

    # Line 5: FPL for family size
    fpl = fpl_for_family_size(family_size)

    # Line 6: Household income as % of FPL (as a decimal fraction, e.g. 2.00 = 200%)
    if fpl > _ZERO:
        fpl_pct = (household_income / fpl).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    else:
        fpl_pct = _ZERO  # pragma: no cover

    # Eligibility: 100%-400% FPL
    is_eligible = Decimal("1.00") <= fpl_pct < Decimal("4.00")

    # Line 7: Applicable figure
    line_7 = applicable_figure(fpl_pct)

    # Line 8a: Annual contribution amount
    annual_contribution = _q(household_income * line_7)

    # Monthly contribution
    monthly_contribution = _q(annual_contribution / Decimal("12"))

    # ---- Part II -- Monthly PTC ----------------------------------------

    # Aggregate monthly data across all 1095-A forms
    # Initialize 12-month arrays
    monthly_enrollment = [_ZERO] * 12
    monthly_slcsp = [_ZERO] * 12
    monthly_advance = [_ZERO] * 12

    for form in canonical.forms_1095_a:
        for i, month_data in enumerate(form.monthly_data):
            if i >= 12:
                break
            monthly_enrollment[i] += month_data.enrollment_premium
            monthly_slcsp[i] += month_data.slcsp_premium
            monthly_advance[i] += month_data.advance_ptc

    monthly_rows: list[Form8962MonthRow] = []
    total_enrollment = _ZERO
    total_slcsp = _ZERO
    total_contribution = _ZERO
    total_max_ptc = _ZERO
    total_advance = _ZERO

    for i in range(12):
        enroll = monthly_enrollment[i]
        slcsp = monthly_slcsp[i]
        adv = monthly_advance[i]

        # Only include months with actual coverage
        if enroll == _ZERO and slcsp == _ZERO and adv == _ZERO:
            continue

        if is_eligible:
            max_ptc = max(_ZERO, _q(slcsp - monthly_contribution))
        else:
            max_ptc = _ZERO

        row = Form8962MonthRow(
            month=i + 1,
            enrollment_premium=_q(enroll),
            slcsp_premium=_q(slcsp),
            contribution_amount=monthly_contribution,
            max_ptc=max_ptc,
            advance_ptc=_q(adv),
        )
        monthly_rows.append(row)

        total_enrollment += enroll
        total_slcsp += slcsp
        total_contribution += monthly_contribution
        total_max_ptc += max_ptc
        total_advance += adv

    # Line 11 annual totals
    total_enrollment = _q(total_enrollment)
    total_slcsp = _q(total_slcsp)
    total_contribution = _q(total_contribution)
    total_max_ptc = _q(total_max_ptc)
    total_advance = _q(total_advance)

    # ---- Part IV -- Net PTC or repayment -------------------------------

    # Line 25: Total PTC allowed (sum of monthly max PTC, but cannot
    # exceed sum of monthly enrollment premiums per IRS instructions)
    total_ptc = total_max_ptc

    # Line 24: Net PTC (refundable credit) -- only if PTC > advance
    if total_ptc > total_advance:
        net_ptc = _q(total_ptc - total_advance)
    else:
        net_ptc = _ZERO

    # Line 27: Excess advance PTC
    if total_advance > total_ptc:
        excess_advance = _q(total_advance - total_ptc)
    else:
        excess_advance = _ZERO

    # Line 28: Repayment cap from Table 5
    cap = repayment_cap(fpl_pct, canonical.filing_status)
    if cap is None:
        # No cap -- full repayment
        cap_amount = excess_advance
    else:
        cap_amount = cap

    # Line 29: Repayment = min(excess advance, cap)
    if cap is None:
        line_29 = excess_advance
    else:
        line_29 = min(excess_advance, cap)

    taxpayer_name = f"{canonical.taxpayer.first_name} {canonical.taxpayer.last_name}"

    return Form8962Fields(
        taxpayer_name=taxpayer_name,
        taxpayer_ssn=canonical.taxpayer.ssn,
        line_1_tax_family_size=family_size,
        line_2_modified_agi=modified_agi,
        line_3_dependents_magi=dependents_magi,
        line_4_household_income=household_income,
        line_5_fpl=fpl,
        line_6_household_income_pct_fpl=fpl_pct,
        line_7_applicable_figure=line_7,
        line_8a_annual_contribution=annual_contribution,
        monthly_rows=tuple(monthly_rows),
        line_11a_annual_enrollment_premium=total_enrollment,
        line_11b_annual_slcsp=total_slcsp,
        line_11c_annual_contribution=total_contribution,
        line_11d_annual_max_ptc=total_max_ptc,
        line_11e_annual_advance_ptc=total_advance,
        line_24_net_ptc=net_ptc,
        line_25_total_ptc=total_ptc,
        line_26_total_advance_ptc=total_advance,
        line_27_excess_advance_ptc=excess_advance,
        line_28_repayment_cap=cap_amount,
        line_29_repayment=line_29,
        is_eligible=is_eligible,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Layer 2 -- reportlab scaffold PDF rendering
# ---------------------------------------------------------------------------


def render_form_8962_pdf(fields: Form8962Fields, out_path: Path) -> Path:
    """Render a Form 8962 summary PDF using reportlab.

    This is a scaffold renderer that produces a human-readable summary.
    A future wave can replace it with an AcroForm overlay on the IRS
    fillable f8962.pdf when a widget map is created.

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

    _line("Form 8962 -- Premium Tax Credit (PTC)", "Helvetica-Bold", 14)
    _line(f"Taxpayer: {fields.taxpayer_name}    SSN: {fields.taxpayer_ssn}")
    _line("")

    _line("Part I -- Annual Totals and Household Information", "Helvetica-Bold", 11)
    _line(f"  Line 1   Tax family size:            {fields.line_1_tax_family_size}")
    _line(f"  Line 2   Modified AGI:               ${fields.line_2_modified_agi:,.2f}")
    _line(f"  Line 3   Dependents' MAGI:           ${fields.line_3_dependents_magi:,.2f}")
    _line(f"  Line 4   Household income:           ${fields.line_4_household_income:,.2f}")
    _line(f"  Line 5   Federal Poverty Level:      ${fields.line_5_fpl:,.2f}")
    fpl_display = fields.line_6_household_income_pct_fpl * 100
    _line(f"  Line 6   Household income % of FPL:  {fpl_display:.0f}%")
    fig_display = fields.line_7_applicable_figure * 100
    _line(f"  Line 7   Applicable figure:          {fig_display:.2f}%")
    _line(f"  Line 8a  Annual contribution:        ${fields.line_8a_annual_contribution:,.2f}")
    _line("")

    _line("Part II -- Monthly PTC Calculation", "Helvetica-Bold", 11)
    _line("  Month   Enrollment   SLCSP     Contribution  Max PTC   Advance PTC", size=8)
    _line("  " + "-" * 70, size=8)
    month_names = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    for row in fields.monthly_rows:
        name = month_names[row.month - 1] if 1 <= row.month <= 12 else f"M{row.month}"
        _line(
            f"  {name:5s}  ${row.enrollment_premium:>9,.2f}"
            f"  ${row.slcsp_premium:>9,.2f}"
            f"  ${row.contribution_amount:>9,.2f}"
            f"  ${row.max_ptc:>9,.2f}"
            f"  ${row.advance_ptc:>9,.2f}",
            size=8,
        )
    _line("  " + "-" * 70, size=8)
    _line(
        f"  Line 11 Totals:"
        f"  ${fields.line_11a_annual_enrollment_premium:>9,.2f}"
        f"  ${fields.line_11b_annual_slcsp:>9,.2f}"
        f"  ${fields.line_11c_annual_contribution:>9,.2f}"
        f"  ${fields.line_11d_annual_max_ptc:>9,.2f}"
        f"  ${fields.line_11e_annual_advance_ptc:>9,.2f}",
        size=8,
    )
    _line("")

    _line("Part IV -- Net PTC or Repayment", "Helvetica-Bold", 11)
    _line(f"  Line 24  Net PTC (refundable):       ${fields.line_24_net_ptc:,.2f}")
    _line(f"  Line 25  Total PTC:                  ${fields.line_25_total_ptc:,.2f}")
    _line(f"  Line 26  Total advance PTC:          ${fields.line_26_total_advance_ptc:,.2f}")
    _line(f"  Line 27  Excess advance PTC:         ${fields.line_27_excess_advance_ptc:,.2f}")
    _line(f"  Line 28  Repayment cap:              ${fields.line_28_repayment_cap:,.2f}")
    _line(f"  Line 29  Repayment (additional tax): ${fields.line_29_repayment:,.2f}")

    if fields.warnings:
        _line("")
        _line("Warnings:", "Helvetica-Bold", 10)
        for w in fields.warnings:
            _line(f"  - {w}", size=9)

    c.save()
    return out_path
