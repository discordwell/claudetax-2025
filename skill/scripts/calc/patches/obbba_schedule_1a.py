"""OBBBA Schedule 1-A: qualified tips and qualified overtime deductions.

==============================================================================
!! STUB-LEVEL PATCH — NUMBERS NOT YET FULLY FINALIZED BY THE IRS AT TIME OF  !!
!! IMPLEMENTATION. DO NOT SHIP THIS FOR REAL FILERS WITHOUT RE-VERIFYING      !!
!! EVERY PARAMETER AGAINST THE FINAL IRS SCHEDULE 1-A INSTRUCTIONS.           !!
==============================================================================

Background
----------
The One Big Beautiful Bill Act (OBBBA, P.L. 119-21, signed 2025-07-04) created
two new *temporary* above-the-line deductions effective for TY2025 through
TY2028:

1. **Qualified tips deduction** — tip income received from customers by
   employees working in traditionally-tipped occupations. Employer attestation
   is required; not every dollar of W-2 box 7 tips is "qualified" under the
   OBBBA definition. This patch takes the qualified-tips amount as an
   explicit caller input; determining it from W-2 data is a separate
   interview-flow responsibility (out of scope for v1).

2. **Qualified overtime compensation deduction** — the *premium portion* of
   FLSA overtime pay, i.e. the extra half of time-and-a-half. The straight-
   time portion of overtime hours is NOT deductible. Again, this patch takes
   the qualified-overtime amount as a caller input.

Both deductions reduce taxable income (they flow through Schedule 1 Part II
"Adjustments to Income", i.e. above-the-line) but have no effect on what was
already withheld — withholding is unchanged by these deductions.

Parameter values
----------------
At implementation time (2026-04-10), the IRS had published only a high-level
fact sheet at
https://www.irs.gov/newsroom/one-big-beautiful-bill-act-tax-deductions-for-working-americans-and-seniors
that confirms:

- Qualified tips: $25,000 cap per filer (no MFJ/single differentiation stated)
- Qualified overtime: $12,500 cap for non-joint filers; $25,000 for MFJ
- Phase-out begins at MAGI > $150,000 (non-joint) / $300,000 (MFJ)
- Effective 2025 through 2028

The fact sheet does NOT state the phase-out RATE. The Schedule 1-A draft form
itself had not appeared on https://www.irs.gov/draft-tax-forms at the time of
this implementation. We ASSUME a reduction of $100 per $1,000 of MAGI in
excess of the threshold, matching the common OBBBA drafting convention used
by the senior deduction and several other provisions. This is labelled as
UNVERIFIED in the result details so downstream consumers can surface a
warning until the final Schedule 1-A instructions are published.

We also assume the phase-out uses the same "or fraction thereof" rounding
(ceiling to the next $1,000) as CTC and the senior deduction. Same caveat.

For HoH and MFS filers, we conservatively use the $12,500 overtime cap (the
non-MFJ number). Re-verify: it is plausible that HoH gets the higher MFJ
number or some middle value under the final rules.

Phase-out application
---------------------
We apply the cap FIRST (to each of tips and overtime independently), then
apply the phase-out to the combined post-cap amount. The phase-out reduction
is then distributed proportionally across tips and overtime so the result
dict still reports a tips_deduction and overtime_deduction that sum to
total_deduction.

API
---
compute_schedule_1a(return_, magi, qualified_tips_input, qualified_overtime_input)
-> Schedule1AResult

The caller supplies the qualified tips and overtime amounts explicitly; this
patch does NOT inspect W-2 box 7 or attempt to classify FLSA-qualifying
overtime. That logic belongs in a separate interview-flow module because
it requires employer attestation data that is not modelled in
CanonicalReturn v0.1.0.

Year window: this function returns zero deductions (with ``details[
"year_out_of_window"] = True``) for any tax year outside 2025-2028. The
statute sunsets after TY2028.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from skill.scripts.models import CanonicalReturn, FilingStatus

# ---------------------------------------------------------------------------
# Module-level parameter constants. These are DUPLICATED in
# test_calc_obbba_schedule_1a.py's TestDocumentedAssumptions class — update
# both in lockstep when the final IRS Schedule 1-A instructions are
# published. Do NOT move these into constants.py until that happens, because
# the values are currently assumptions, not published IRS figures we can
# source from ty2025-constants.json.
# ---------------------------------------------------------------------------

TIPS_CAP: Decimal = Decimal("25000")
"""Qualified tips deduction cap. Source: IRS newsroom OBBBA fact sheet."""

OVERTIME_CAP_SINGLE: Decimal = Decimal("12500")
"""Qualified overtime deduction cap for non-joint filers (single, HoH, MFS)."""

OVERTIME_CAP_MFJ: Decimal = Decimal("25000")
"""Qualified overtime deduction cap for MFJ/QSS filers."""

PHASE_OUT_START_SINGLE: Decimal = Decimal("150000")
"""MAGI phase-out start for non-joint filers."""

PHASE_OUT_START_MFJ: Decimal = Decimal("300000")
"""MAGI phase-out start for MFJ/QSS filers."""

PHASE_OUT_REDUCTION_PER_1000: Decimal = Decimal("100")
"""UNVERIFIED ASSUMPTION: $100 per $1,000 of MAGI over the threshold.

The IRS newsroom fact sheet omits the phase-out rate. We default to the
common OBBBA drafting convention. MUST be re-verified against the final
Schedule 1-A instructions before shipping.
"""

OBBBA_SCHEDULE_1A_FIRST_YEAR: int = 2025
OBBBA_SCHEDULE_1A_LAST_YEAR: int = 2028

# The loud assumptions list that gets copied into every result dict.
_ASSUMPTIONS: tuple[str, ...] = (
    "TIPS_CAP=$25,000 (IRS newsroom fact sheet, "
    "https://www.irs.gov/newsroom/one-big-beautiful-bill-act-tax-deductions-for-working-americans-and-seniors)",
    "OVERTIME_CAP_SINGLE=$12,500, OVERTIME_CAP_MFJ=$25,000 (same source)",
    "PHASE_OUT_START_SINGLE=$150,000, PHASE_OUT_START_MFJ=$300,000 (same source)",
    "PHASE_OUT_REDUCTION_PER_1000=$100 — UNVERIFIED assumption; IRS fact "
    "sheet omits the phase-out rate. Re-verify against final Schedule 1-A "
    "instructions before shipping.",
    "Phase-out rounding uses 'or fraction thereof' ceiling to next $1,000 — "
    "UNVERIFIED assumption matching CTC/senior-deduction convention. "
    "Re-verify.",
    "HoH and MFS use the $12,500 non-joint overtime cap — UNVERIFIED "
    "assumption; final IRS rules may differ. Re-verify.",
    "Schedule 1-A draft form not yet listed at "
    "https://www.irs.gov/draft-tax-forms as of 2026-04-10. Re-check.",
    "Year window TY2025-2028 per statute; re-verify extension/sunset when "
    "final instructions are published.",
)


@dataclass(frozen=True)
class Schedule1AResult:
    """Result of the OBBBA Schedule 1-A tips + overtime deduction computation.

    Attributes:
        tips_deduction: Post-cap, post-phase-out qualified tips deduction.
        overtime_deduction: Post-cap, post-phase-out qualified overtime
            deduction.
        total_deduction: tips_deduction + overtime_deduction.
        tips_cap_applied: True iff the caller's tips input exceeded the cap
            and had to be chopped.
        overtime_cap_applied: True iff the caller's overtime input exceeded
            the filing-status-specific cap.
        phase_out_reduction: Total dollars removed by the phase-out,
            distributed across tips and overtime.
        details: Audit-trail dict. Contains ``assumptions`` (a list of
            strings describing every unverified or stub-level assumption
            this computation made) and diagnostic breakdowns of each step.
    """

    tips_deduction: Decimal
    overtime_deduction: Decimal
    total_deduction: Decimal
    tips_cap_applied: bool
    overtime_cap_applied: bool
    phase_out_reduction: Decimal
    details: dict[str, Any] = field(default_factory=dict)


def _overtime_cap_for_status(status: FilingStatus) -> Decimal:
    """Return the overtime cap for a given filing status.

    MFJ and QSS → $25,000. Everyone else → $12,500.
    """
    if status in (FilingStatus.MFJ, FilingStatus.QSS):
        return OVERTIME_CAP_MFJ
    return OVERTIME_CAP_SINGLE


def _phase_out_start_for_status(status: FilingStatus) -> Decimal:
    """Return the MAGI phase-out start for a given filing status.

    MFJ and QSS → $300,000. Everyone else → $150,000.
    """
    if status in (FilingStatus.MFJ, FilingStatus.QSS):
        return PHASE_OUT_START_MFJ
    return PHASE_OUT_START_SINGLE


def _phase_out_reduction(magi: Decimal, threshold: Decimal) -> Decimal:
    """Return the phase-out reduction in dollars.

    $100 (PHASE_OUT_REDUCTION_PER_1000) per $1,000 or fraction thereof of
    MAGI in excess of the threshold. Returns $0 when MAGI <= threshold.
    """
    if magi <= threshold:
        return Decimal("0")
    excess = magi - threshold
    thousands = math.ceil(excess / Decimal("1000"))
    return PHASE_OUT_REDUCTION_PER_1000 * Decimal(thousands)


def _zero_result(*, year_out_of_window: bool = False) -> Schedule1AResult:
    """Build a zero-deduction result, used for early exits."""
    details: dict[str, Any] = {"assumptions": list(_ASSUMPTIONS)}
    if year_out_of_window:
        details["year_out_of_window"] = True
    return Schedule1AResult(
        tips_deduction=Decimal("0"),
        overtime_deduction=Decimal("0"),
        total_deduction=Decimal("0"),
        tips_cap_applied=False,
        overtime_cap_applied=False,
        phase_out_reduction=Decimal("0"),
        details=details,
    )


def compute_schedule_1a(
    return_: CanonicalReturn,
    magi: Decimal,
    qualified_tips_input: Decimal,
    qualified_overtime_input: Decimal,
) -> Schedule1AResult:
    """Compute the OBBBA Schedule 1-A tips and overtime deductions.

    Args:
        return_: The canonical tax return. Used for filing_status (determines
            overtime cap and phase-out start) and tax_year (enforces the
            TY2025-2028 window).
        magi: Modified Adjusted Gross Income used for the phase-out. The
            calc engine is responsible for computing MAGI upstream; for
            most filers this equals AGI.
        qualified_tips_input: The dollar amount of tips the filer claims as
            "qualified tips" under the OBBBA definition. Caller is
            responsible for verifying employer attestation; this patch does
            NOT scan W-2 box 7. Negative inputs are clamped to zero.
        qualified_overtime_input: The dollar amount of overtime premium the
            filer claims as "qualified overtime compensation" under the
            OBBBA definition (the extra half of time-and-a-half, NOT the
            straight-time portion). Negative inputs clamped to zero.

    Returns:
        Schedule1AResult with tips_deduction, overtime_deduction,
        total_deduction, cap flags, phase_out_reduction, and a details dict
        that always includes ``details["assumptions"]`` listing every
        unverified assumption baked into the computation.
    """
    # --- Year window gate ------------------------------------------------
    if (
        return_.tax_year < OBBBA_SCHEDULE_1A_FIRST_YEAR
        or return_.tax_year > OBBBA_SCHEDULE_1A_LAST_YEAR
    ):
        return _zero_result(year_out_of_window=True)

    # --- Defensive input clamp -------------------------------------------
    tips_raw = max(Decimal("0"), Decimal(qualified_tips_input))
    overtime_raw = max(Decimal("0"), Decimal(qualified_overtime_input))
    magi_d = Decimal(magi)

    if tips_raw == Decimal("0") and overtime_raw == Decimal("0"):
        return _zero_result()

    status = return_.filing_status
    overtime_cap = _overtime_cap_for_status(status)
    phase_out_start = _phase_out_start_for_status(status)

    # --- Step 1: apply caps ---------------------------------------------
    tips_capped = min(tips_raw, TIPS_CAP)
    tips_cap_applied = tips_raw > TIPS_CAP

    overtime_capped = min(overtime_raw, overtime_cap)
    overtime_cap_applied = overtime_raw > overtime_cap

    combined_capped = tips_capped + overtime_capped

    # --- Step 2: apply phase-out to combined total ----------------------
    raw_reduction = _phase_out_reduction(magi_d, phase_out_start)
    phase_out_applied = min(raw_reduction, combined_capped)

    # --- Step 3: distribute reduction proportionally --------------------
    # We chose pro-rata so both tips and overtime shrink by the same
    # fraction. If combined_capped is zero we already returned above.
    if phase_out_applied == Decimal("0") or combined_capped == Decimal("0"):
        tips_final = tips_capped
        overtime_final = overtime_capped
    else:
        # Scale each slice by (1 - reduction/combined).
        keep_fraction = (combined_capped - phase_out_applied) / combined_capped
        # Quantize to the penny to keep sums tidy. Any residual rounding
        # difference (at most 1 cent) is shoved back onto tips so the sum
        # still matches total_deduction exactly.
        tips_final = (tips_capped * keep_fraction).quantize(Decimal("0.01"))
        overtime_final = (overtime_capped * keep_fraction).quantize(Decimal("0.01"))
        expected_total = combined_capped - phase_out_applied
        drift = expected_total - (tips_final + overtime_final)
        if drift != Decimal("0"):
            tips_final += drift

    total = tips_final + overtime_final

    details: dict[str, Any] = {
        "assumptions": list(_ASSUMPTIONS),
        "filing_status": status.value,
        "tax_year": return_.tax_year,
        "magi": str(magi_d),
        "tips_input": str(tips_raw),
        "overtime_input": str(overtime_raw),
        "tips_cap": str(TIPS_CAP),
        "overtime_cap": str(overtime_cap),
        "tips_capped": str(tips_capped),
        "overtime_capped": str(overtime_capped),
        "combined_capped": str(combined_capped),
        "phase_out_start": str(phase_out_start),
        "phase_out_raw_reduction": str(raw_reduction),
        "phase_out_applied": str(phase_out_applied),
        "phase_out_reduction_per_1000": str(PHASE_OUT_REDUCTION_PER_1000),
        "tips_final": str(tips_final),
        "overtime_final": str(overtime_final),
        "total": str(total),
    }

    return Schedule1AResult(
        tips_deduction=tips_final,
        overtime_deduction=overtime_final,
        total_deduction=total,
        tips_cap_applied=tips_cap_applied,
        overtime_cap_applied=overtime_cap_applied,
        phase_out_reduction=phase_out_applied,
        details=details,
    )
