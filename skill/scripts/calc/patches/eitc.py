"""Earned Income Tax Credit (EITC) patch.

Computes the TY2025 federal EITC from a CanonicalReturn plus three numeric
inputs that come from earlier calc-engine stages: AGI, earned income, and
investment income. The engine is responsible for assembling those three
amounts — this patch just applies the phase-in / plateau / phase-out formula.

EITC structure (TY2025):

    Phase-in:   credit = earned_income × phase_in_rate, capped at max_credit
    Plateau:    credit = max_credit while phase_determinant ≤ phase_out_begin
    Phase-out:  credit = max_credit − (phase_determinant − phase_out_begin) × phase_out_rate
    Floor/ceil: clamped to [0, max_credit]

The "phase determinant" is the LARGER of earned income and AGI, so someone
with significant non-earned income gets phased out on their AGI even if their
earned income would have left them on the plateau.

MFJ filers get a higher phase-out begin point to reduce the marriage penalty
(the delta is $7,110 for TY2025 per Rev. Proc. 2024-40).

Sources for TY2025 parameter values:
- IRS Rev. Proc. 2024-40 (inflation adjustments for 2025).
  https://www.irs.gov/pub/irs-drop/rp-24-40.pdf
- Tax Policy Center EITC Parameters table (sourced from Rev. Proc. 2024-40),
  downloaded 10-Apr-2025. Provides phase-in/phase-out rates that the IRS
  newsroom summary omits.
  https://taxpolicycenter.org/sites/default/files/2025-04/eitc_parameters.pdf
- IRS EITC tables:
  https://www.irs.gov/credits-deductions/individuals/earned-income-tax-credit/earned-income-and-earned-income-tax-credit-eitc-tables
- IRS Publication 596, Earned Income Credit.
  https://www.irs.gov/pub/irs-pdf/p596.pdf

TODO (EITC qualifying-child definition differs from CTC):
- EITC treats a child as qualifying if they are under 19 at year end, under 24
  if a full-time student, OR any age if permanently and totally disabled. The
  CTC rule (under 17) is narrower. v1 uses the CanonicalReturn's
  is_qualifying_child flag as-is, which is CTC-centric. Fix this when we have
  an EITC-specific qualifying-child classifier.

TODO (age/dependent-status checks not implemented):
- 0-child EITC requires the filer (or either spouse on MFJ) to be 25–64 at
  year end, not a dependent of another taxpayer, and to have lived in the US
  more than half the year. These checks are deferred to a later pass.
- A taxpayer who can be claimed as a dependent by someone else is disqualified.
- Investment-income composition (interest, dividends, cap gains, royalties,
  passive rental net income) is assumed to be computed upstream; this patch
  just consumes the aggregate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from skill.scripts.calc import constants as C
from skill.scripts.models import CanonicalReturn, FilingStatus

# ---------------------------------------------------------------------------
# Parameter tables — now sourced from ty2025-constants.json via constants.py
# ---------------------------------------------------------------------------

_ChildKey = Literal["0", "1", "2", "3_or_more"]


def _child_key(qualifying_children: int) -> _ChildKey:
    if qualifying_children <= 0:
        return "0"
    if qualifying_children == 1:
        return "1"
    if qualifying_children == 2:
        return "2"
    return "3_or_more"


def _count_qualifying_children(return_: CanonicalReturn) -> int:
    """Count dependents flagged as qualifying children.

    TODO: EITC's qualifying-child age rule is BROADER than CTC's (EITC: under 19,
    under 24 if student, any age if disabled; CTC: under 17). The canonical
    return's is_qualifying_child flag today follows the CTC rule. For v1 we
    reuse that flag as-is, which UNDERCOUNTS qualifying children for EITC when
    a 17-18 year old (or a qualifying student under 24) is present. Replace
    with an EITC-specific classifier once we have one.
    """
    return sum(1 for d in return_.dependents if d.is_qualifying_child)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EITCResult:
    """Output of compute_eitc.

    - ``eitc``: the computed credit amount (Decimal), clamped to [0, max_credit]
    - ``qualifying_children``: count used for lookup (after the EITC-vs-CTC caveat above)
    - ``earned_income``: echoed input
    - ``agi``: echoed input
    - ``phase_determinant``: max(earned_income, agi) — what drives phase-out
    - ``disqualified``: True if a disqualification rule fired; eitc will be 0
    - ``disqualification_reason``: short free-text explanation, or None
    - ``details``: diagnostic dict with the parameters that were applied
    """

    eitc: Decimal
    qualifying_children: int
    earned_income: Decimal
    agi: Decimal
    phase_determinant: Decimal
    disqualified: bool
    disqualification_reason: str | None
    details: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_eitc(
    return_: CanonicalReturn,
    agi: Decimal,
    earned_income: Decimal,
    investment_income: Decimal,
) -> EITCResult:
    """Compute the TY2025 EITC for ``return_`` given the three numeric inputs.

    The engine is responsible for assembling ``agi``, ``earned_income`` and
    ``investment_income`` from the various schedules. We take them as inputs so
    this patch stays decoupled from the AGI / earned-income aggregation logic
    (which lives in its own patch).
    """
    agi = Decimal(agi)
    earned_income = Decimal(earned_income)
    investment_income = Decimal(investment_income)

    qualifying_children = _count_qualifying_children(return_)
    key = _child_key(qualifying_children)
    phase_determinant = max(earned_income, agi)

    max_credit = Decimal(C.eitc_max_credit(qualifying_children))
    phase_in_rate = Decimal(str(C.eitc_phase_in_rate(qualifying_children)))
    phase_out_rate = Decimal(str(C.eitc_phase_out_rate(qualifying_children)))
    status_for_limit = (
        "mfj" if return_.filing_status == FilingStatus.MFJ else "single"
    )
    phase_out_begin = Decimal(C.eitc_phase_out_begin(qualifying_children, status_for_limit))
    earned_income_for_max = Decimal(C.eitc_earned_income_for_max_credit(qualifying_children))

    # Completed-phaseout (AGI limit) — already in constants.
    agi_limit = Decimal(C.eitc_agi_limit(qualifying_children, status_for_limit))
    investment_limit = Decimal(C.eitc_investment_income_disqualifier())

    details: dict = {
        "phase_in_rate": phase_in_rate,
        "phase_out_rate": phase_out_rate,
        "phase_out_begin": phase_out_begin,
        "max_credit": max_credit,
        "earned_income_for_max_credit": earned_income_for_max,
        "agi_limit": agi_limit,
        "investment_income_limit": investment_limit,
        "child_key": key,
        "filing_status": return_.filing_status.value,
    }

    # -- Disqualification checks ------------------------------------------------
    # MFS is generally disqualified from EITC. (There are post-2020 edge cases
    # for separated-but-not-divorced filers; deferred.)
    # TODO: implement the narrow MFS-separated-spouse exception (IRC §32(d)).
    if return_.filing_status == FilingStatus.MFS:
        return EITCResult(
            eitc=Decimal("0"),
            qualifying_children=qualifying_children,
            earned_income=earned_income,
            agi=agi,
            phase_determinant=phase_determinant,
            disqualified=True,
            disqualification_reason="MFS filing status is not eligible for EITC (v1)",
            details=details,
        )

    if investment_income > investment_limit:
        return EITCResult(
            eitc=Decimal("0"),
            qualifying_children=qualifying_children,
            earned_income=earned_income,
            agi=agi,
            phase_determinant=phase_determinant,
            disqualified=True,
            disqualification_reason=(
                f"Investment income ${investment_income} exceeds EITC limit "
                f"${investment_limit}"
            ),
            details=details,
        )

    # TODO: 0-child age (25–64), US-residency, not-a-dependent-of-another, and
    # SSN-valid-for-employment checks.

    # -- Phase-in ---------------------------------------------------------------
    if earned_income <= 0:
        return EITCResult(
            eitc=Decimal("0"),
            qualifying_children=qualifying_children,
            earned_income=earned_income,
            agi=agi,
            phase_determinant=phase_determinant,
            disqualified=False,
            disqualification_reason=None,
            details=details,
        )

    phase_in_credit = earned_income * phase_in_rate
    credit = min(phase_in_credit, max_credit)

    # -- Phase-out --------------------------------------------------------------
    if phase_determinant > phase_out_begin:
        reduction = (phase_determinant - phase_out_begin) * phase_out_rate
        credit = credit - reduction

    # -- Clamp and round --------------------------------------------------------
    if credit < 0:
        credit = Decimal("0")
    if credit > max_credit:
        credit = max_credit

    # EITC is reported in whole-dollar amounts on the 1040. The published IRS
    # EITC tables use a small-bracket lookup that effectively rounds to the
    # nearest integer; round half-up to match.
    credit = credit.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    # Secondary hard ceiling: if AGI is past the statutory completed-phaseout
    # amount, credit is exactly zero. Our linear formula should already produce
    # zero there, but this guards against rounding glitches on the boundary.
    if agi >= agi_limit and phase_determinant >= agi_limit:
        credit = Decimal("0")

    return EITCResult(
        eitc=credit,
        qualifying_children=qualifying_children,
        earned_income=earned_income,
        agi=agi,
        phase_determinant=phase_determinant,
        disqualified=False,
        disqualification_reason=None,
        details=details,
    )
