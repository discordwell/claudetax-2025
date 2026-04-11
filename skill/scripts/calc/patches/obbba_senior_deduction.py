"""OBBBA Senior Deduction (§63(f)(3) as enhanced by OBBBA) patch.

TY2025-2028 / OBBBA P.L. 119-21. This patch computes the NEW +$6,000 senior
deduction that OBBBA layered on top of the pre-existing §63(f) age-65
additional standard deduction. The two coexist; this module computes ONLY
the OBBBA $6,000 addition — the pre-existing age-65 additional is handled
by the standard deduction path in the main engine.

Design:
- Base: $6,000 per filer age 65 or older at end of tax year (12/31).
- Eligibility:
    * Taxpayer: always considered.
    * Spouse: ONLY considered on MFJ or QSS (the joint-return bucket).
      On MFS the spouse would claim their own senior deduction on their own
      return, so we do not count the spouse here.
- Phase-out:
    * Begins at MAGI $75,000 (single/HoH/MFS) or $150,000 (MFJ/QSS).
      Thresholds come from C.obbba_senior_deduction().
    * Rate: 6% of MAGI over the threshold (i.e. $0.06 per $1). Reduction
      is clamped to the base so the deduction is never negative.
    * Source for rate (not yet in constants JSON): Tax Foundation OBBBA
      analysis, H&R Block OBBBA senior-deduction explainer, Welch Group
      OBBBA senior-deduction briefing. All corroborate the 6% rate with
      full phase-out at $175k single / $250k MFJ (one-filer $6k base).
      Because the MFJ base can be $12k (two 65+ filers), the MFJ two-
      filer return does not fully phase out until MAGI = $350k
      ($12,000 / 0.06 + $150,000).
- Year gating:
    * OBBBA years_applicable = [2025, 2026, 2027, 2028] per the constants
      JSON. Any other year returns a SeniorDeductionResult with
      deduction=0 and details["year_gated"]=True. num_filers_age_65_plus
      is still reported for audit visibility, but base_deduction is 0.

TODO(constants): the 6% phase-out rate is currently a module-level
literal because skill/reference/ty2025-constants.json only documents the
thresholds, not the rate. A follow-up patch authorized to touch the
constants JSON should add:
    "standard_deduction.senior_deduction_obbba.phase_out_rate": 0.06
along with a citation (IRS OBBBA guidance FS-2025-08 or the statute text
of P.L. 119-21 §70103). Until then this literal MUST be kept in sync
with any official update.

References:
- https://www.irs.gov/newsroom/one-big-beautiful-bill-act-tax-deductions-for-working-americans-and-seniors
- https://taxfoundation.org/blog/obbba-senior-deduction-tax-relief/
- https://www.hrblock.com/tax-center/irs/tax-law-and-policy/one-big-beautiful-bill-senior-tax-deduction/
- https://welchgroup.com/tax-relief-for-seniors-a-new-deduction-available/
- skill/reference/ty2025-landscape.md section 10a
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from skill.scripts.calc import constants as C
from skill.scripts.models import CanonicalReturn, FilingStatus, Person

# ---------------------------------------------------------------------------
# TODO(constants): move to ty2025-constants.json
# ---------------------------------------------------------------------------
_PHASE_OUT_RATE: Decimal = Decimal("0.06")
"""OBBBA senior-deduction phase-out rate: 6% of MAGI over threshold.

Not yet in ty2025-constants.json (the JSON only holds the thresholds).
Verified from Tax Foundation, H&R Block, and Welch Group OBBBA analyses.
See module docstring for citations and the follow-up task.
"""


@dataclass(frozen=True)
class SeniorDeductionResult:
    """Result of the OBBBA senior-deduction computation.

    Attributes
    ----------
    deduction : Decimal
        Final dollar amount to subtract from AGI (after phase-out and
        zero-clamp). This flows to
        ``AdjustmentsToIncome.senior_deduction_obbba`` in the canonical
        return.
    num_filers_age_65_plus : int
        0, 1, or 2. Includes taxpayer and (for MFJ/QSS only) spouse.
    base_deduction : Decimal
        num_filers_age_65_plus * $6,000, before phase-out. Zero for years
        outside the OBBBA window (2025-2028).
    phase_out_reduction : Decimal
        Dollar amount of phase-out reduction, clamped to ``base_deduction``
        so we never report more reduction than there is to take.
    details : dict
        Audit trail with filing status, threshold, rate, MAGI, and any
        special gates triggered (year_gated, etc).
    """

    deduction: Decimal
    num_filers_age_65_plus: int
    base_deduction: Decimal
    phase_out_reduction: Decimal
    details: dict[str, Any] = field(default_factory=dict)


def _age_at_end_of_year(dob: dt.date, tax_year: int) -> int:
    """Age on 12/31 of the tax year.

    Matches the CTC patch's convention: a person born 1960-12-31 is
    exactly 65 on 2025-12-31 and qualifies; a person born 1961-01-02 is
    64 on 2025-12-31 and does not.
    """
    end_of_year = dt.date(tax_year, 12, 31)
    years = end_of_year.year - dob.year
    if (end_of_year.month, end_of_year.day) < (dob.month, dob.day):
        years -= 1
    return years


def _is_age_65_or_older(person: Person, tax_year: int) -> bool:
    return _age_at_end_of_year(person.date_of_birth, tax_year) >= 65


def _count_qualifying_filers(
    return_: CanonicalReturn,
) -> int:
    """Count filers age 65+ at end of tax year.

    - Taxpayer is always considered.
    - Spouse is considered ONLY on MFJ or QSS (the joint-return statuses).
      On MFS the spouse files separately and claims their own senior
      deduction on their own return; we do not double-count here. On
      single/HoH there is no spouse.
    """
    tax_year = return_.tax_year
    count = 0
    if _is_age_65_or_older(return_.taxpayer, tax_year):
        count += 1
    if (
        return_.filing_status in (FilingStatus.MFJ, FilingStatus.QSS)
        and return_.spouse is not None
        and _is_age_65_or_older(return_.spouse, tax_year)
    ):
        count += 1
    return count


def _threshold_for(status: FilingStatus, params: dict) -> Decimal:
    """Select the phase-out threshold for a filing status.

    MFS uses the single threshold ($75k), NOT the MFJ threshold.
    """
    if status in (FilingStatus.MFJ, FilingStatus.QSS):
        return Decimal(str(params["phase_out_start_mfj_qss"]))
    return Decimal(str(params["phase_out_start_single_hoh_mfs"]))


def compute_senior_deduction(
    return_: CanonicalReturn,
    magi: Decimal,
) -> SeniorDeductionResult:
    """Compute the OBBBA +$6,000 senior deduction for a canonical return.

    Parameters
    ----------
    return_ : CanonicalReturn
        The canonical tax return. Supplies filing status, taxpayer,
        optional spouse, and tax year.
    magi : Decimal
        Modified AGI for the OBBBA senior-deduction phase-out. For most
        filers this equals AGI; callers that must add back foreign
        earned-income exclusions should do so upstream.

    Returns
    -------
    SeniorDeductionResult
        Frozen dataclass with the final deduction, qualifying-filer count,
        base deduction, phase-out reduction, and an audit trail.
    """
    params = C.obbba_senior_deduction()
    amount_per_filer = Decimal(str(params["amount"]))
    years_applicable: list[int] = list(params["years_applicable"])

    num_qualifiers = _count_qualifying_filers(return_)

    # Year gate: before 2025 or after 2028, no deduction regardless of age.
    if return_.tax_year not in years_applicable:
        return SeniorDeductionResult(
            deduction=Decimal("0"),
            num_filers_age_65_plus=num_qualifiers,
            base_deduction=Decimal("0"),
            phase_out_reduction=Decimal("0"),
            details={
                "filing_status": return_.filing_status.value,
                "tax_year": return_.tax_year,
                "years_applicable": years_applicable,
                "year_gated": True,
                "magi": str(magi),
                "amount_per_filer": str(amount_per_filer),
                "num_filers_age_65_plus": num_qualifiers,
            },
        )

    base_deduction = amount_per_filer * Decimal(num_qualifiers)

    threshold = _threshold_for(return_.filing_status, params)

    # Phase-out: 6% of MAGI over threshold, clamped at base.
    excess = max(Decimal("0"), magi - threshold)
    raw_reduction = (excess * _PHASE_OUT_RATE).quantize(Decimal("0.01"))
    phase_out_reduction = min(raw_reduction, base_deduction)

    deduction = base_deduction - phase_out_reduction
    if deduction < Decimal("0"):
        # Defensive: clamp shouldn't be reachable because we just min()'d above.
        deduction = Decimal("0")

    details: dict[str, Any] = {
        "filing_status": return_.filing_status.value,
        "tax_year": return_.tax_year,
        "years_applicable": years_applicable,
        "year_gated": False,
        "magi": str(magi),
        "threshold": str(threshold),
        "phase_out_rate": str(_PHASE_OUT_RATE),
        "amount_per_filer": str(amount_per_filer),
        "num_filers_age_65_plus": num_qualifiers,
        "base_deduction": str(base_deduction),
        "excess_over_threshold": str(excess),
        "raw_phase_out_reduction": str(raw_reduction),
        "phase_out_reduction_applied": str(phase_out_reduction),
        "deduction": str(deduction),
        "todo_phase_out_rate_in_constants": (
            "6% rate is hardcoded; move to ty2025-constants.json "
            "standard_deduction.senior_deduction_obbba.phase_out_rate "
            "with an IRS/statute citation."
        ),
    }

    return SeniorDeductionResult(
        deduction=deduction,
        num_filers_age_65_plus=num_qualifiers,
        base_deduction=base_deduction,
        phase_out_reduction=phase_out_reduction,
        details=details,
    )
