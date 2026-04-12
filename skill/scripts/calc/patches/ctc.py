"""Child Tax Credit + Additional Child Tax Credit + Credit for Other Dependents.

TY2025 / OBBBA / IRC §24. This patch fills the gap documented in
skill/reference/cp4-tenforty-verification.md section 10c: tenforty does NOT
apply CTC from num_dependents. The calc engine calls compute_ctc(...) after
tenforty returns, and folds the result into Credits + Payments.

All numeric parameters (per-child amount, refundable cap, phase-out thresholds,
phase-out step) are loaded from C.ctc_params(status) — do NOT hardcode.

ODC $500 and ACTC floor/rate are loaded from ty2025-constants.json via
the constants module (child_tax_credit.amount_per_other_dependent_odc,
actc_earned_income_floor, actc_earned_income_rate).

Phase-out ordering: we apply the $50-per-$1,000 reduction to the COMBINED
base (CTC + ODC), then subtract from ODC first and CTC second. This tracks
§24 and the Schedule 8812 worksheet, and keeps the $1,700/child ACTC cap
aligned with the CTC slice (not the ODC slice, which is never refundable).
See test_phaseout_ordering_locked_combined in test_calc_ctc.py for the lock.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from skill.scripts.calc import constants as C
from skill.scripts.models import CanonicalReturn, Dependent, FilingStatus

# ODC, ACTC floor, and ACTC rate loaded from ty2025-constants.json.
_ODC_PER_DEPENDENT: Decimal = Decimal(C.odc_per_dependent())
_ACTC_EARNED_INCOME_FLOOR: Decimal = Decimal(C.actc_earned_income_floor())
_ACTC_EARNED_INCOME_RATE: Decimal = Decimal(str(C.actc_earned_income_rate()))


@dataclass(frozen=True)
class CTCResult:
    """Result of CTC + ACTC + ODC computation.

    Attributes:
        nonrefundable_ctc: CTC amount that offsets tax_before_credits. Never
            refunded. Flows to Credits.child_tax_credit.
        refundable_actc: ACTC refundable portion, capped at $1,700 per child
            (TY2025 OBBBA) and limited by 15% of earned income over $2,500.
            Flows to Credits.additional_child_tax_credit_refundable AND
            Payments.additional_child_tax_credit_refundable.
        credit_for_other_dependents: $500/dependent, nonrefundable only.
            Flows to Credits.credit_for_other_dependents.
        num_qualifying_children: Count used for $2,200/child and $1,700/child
            refundable cap. Must be under 17 at end of tax year.
        num_other_dependents: Count used for $500/each ODC. Includes
            qualifying relatives and qualifying children aged 17+.
        phase_out_applied: Dollar amount of phase-out reduction, clamped to
            the combined base credit.
        details: Diagnostic breakdown for audit / explainability.
    """

    nonrefundable_ctc: Decimal
    refundable_actc: Decimal
    credit_for_other_dependents: Decimal
    num_qualifying_children: int
    num_other_dependents: int
    phase_out_applied: Decimal
    details: dict[str, Any] = field(default_factory=dict)


def _age_at_end_of_year(dob: dt.date, tax_year: int) -> int:
    """Age on 12/31 of the tax year.

    A child born 12/31/2008 and tax_year=2025 is age 17 on 12/31/2025 and
    therefore NOT under 17 at year end (fails the §24 under-17 test).
    """
    end_of_year = dt.date(tax_year, 12, 31)
    years = end_of_year.year - dob.year
    # Subtract 1 if birthday has not yet occurred by 12/31 (impossible for
    # 12/31 end-of-year, but covers the general form).
    if (end_of_year.month, end_of_year.day) < (dob.month, dob.day):
        years -= 1
    return years


def _classify_dependents(
    dependents: list[Dependent], tax_year: int
) -> tuple[int, int]:
    """Split dependents into (qualifying children under 17, other dependents).

    A dependent marked is_qualifying_child but aged 17+ at year end drops to
    ODC. A dependent marked is_qualifying_relative is always ODC.
    """
    qc = 0
    odc = 0
    for dep in dependents:
        age = _age_at_end_of_year(dep.person.date_of_birth, tax_year)
        if dep.is_qualifying_child and age < 17:
            qc += 1
        else:
            odc += 1
    return qc, odc


def _phase_out_reduction(
    magi: Decimal,
    threshold: int,
    step: int,
) -> Decimal:
    """Compute the phase-out reduction.

    $50 per $1,000 (or fraction) of MAGI over the threshold. IRC §24(b)(1).
    The step value from constants is the dollar reduction per $1,000 of
    excess (typically $50).
    """
    if magi <= Decimal(threshold):
        return Decimal("0")
    excess = magi - Decimal(threshold)
    # ceil to the next $1,000 — "or fraction thereof"
    thousands = math.ceil(excess / Decimal("1000"))
    return Decimal(step) * Decimal(thousands)


def compute_ctc(
    return_: CanonicalReturn,
    magi: Decimal,
    tax_before_credits: Decimal,
    earned_income: Decimal,
) -> CTCResult:
    """Compute CTC + ACTC + ODC for a canonical return.

    Args:
        return_: The canonical tax return. Supplies dependents, filing status,
            and tax_year for age calculation.
        magi: Modified AGI for the CTC phase-out. For most filers this is
            AGI; filers with foreign income exclusions add those back. The
            calc engine is responsible for computing MAGI upstream.
        tax_before_credits: Tax liability the nonrefundable CTC can offset.
            Typically 1040 line 16 + Schedule 2 line 1a (AMT), before
            subtracting any nonrefundable credits.
        earned_income: Earned income for the ACTC 15% floor. Wages + net SE
            earnings (minus deductible half SE tax) + nontaxable combat pay
            if elected. Coordinator computes this upstream.

    Returns:
        CTCResult with nonrefundable CTC, refundable ACTC, ODC, counts,
        phase-out amount, and an audit trail in .details.
    """
    status_str = return_.filing_status.value  # "single" / "mfj" / ...
    # Cast from pydantic enum value back to the literal constants accepts
    params = C.ctc_params(status_str)  # type: ignore[arg-type]

    num_qc, num_odc = _classify_dependents(return_.dependents, return_.tax_year)

    ctc_base = Decimal(params.amount_per_child) * Decimal(num_qc)
    odc_base = _ODC_PER_DEPENDENT * Decimal(num_odc)
    combined_base = ctc_base + odc_base

    # Short-circuit: nothing to compute
    if combined_base == Decimal("0"):
        return CTCResult(
            nonrefundable_ctc=Decimal("0"),
            refundable_actc=Decimal("0"),
            credit_for_other_dependents=Decimal("0"),
            num_qualifying_children=num_qc,
            num_other_dependents=num_odc,
            phase_out_applied=Decimal("0"),
            details={
                "ctc_base": str(ctc_base),
                "odc_base": str(odc_base),
                "combined_base": str(combined_base),
                "filing_status": status_str,
            },
        )

    # Phase-out against the COMBINED base (CTC + ODC). See module docstring
    # for the ordering rationale. Reduction is clamped to the base so we
    # never produce a negative credit.
    raw_reduction = _phase_out_reduction(
        magi=magi,
        threshold=params.phase_out_start,
        step=params.phase_out_reduction_per_1000_over,
    )
    phase_out_applied = min(raw_reduction, combined_base)

    # Allocate reduction: ODC first, then CTC. This preserves the CTC slice
    # (and its $1,700/child refundable cap) as long as possible.
    reduction_remaining = phase_out_applied
    odc_after = odc_base
    ctc_after = ctc_base
    if reduction_remaining > Decimal("0"):
        absorbed_by_odc = min(odc_after, reduction_remaining)
        odc_after -= absorbed_by_odc
        reduction_remaining -= absorbed_by_odc
    if reduction_remaining > Decimal("0"):
        absorbed_by_ctc = min(ctc_after, reduction_remaining)
        ctc_after -= absorbed_by_ctc
        reduction_remaining -= absorbed_by_ctc

    # ODC is entirely nonrefundable; limited by remaining tax.
    # CTC is limited by tax first (nonrefundable), then the leftover rolls
    # into ACTC subject to the $1,700/child cap and the earned income floor.
    #
    # Order on the 8812 worksheet: CTC + ODC are applied together as a
    # combined nonrefundable credit against tax. We model that by totaling
    # (ctc_after + odc_after) and taking min(tax, total) — then we split
    # the absorbed amount back into CTC vs ODC, CTC first (because the
    # overflow becomes ACTC which is CTC-only).
    combined_after_phaseout = ctc_after + odc_after
    nonref_used_total = min(tax_before_credits, combined_after_phaseout)

    # Split nonref_used_total: CTC first (so overflow → ACTC), then ODC.
    nonref_ctc = min(ctc_after, nonref_used_total)
    nonref_odc = nonref_used_total - nonref_ctc  # ≤ odc_after by construction

    # Leftover CTC after tax absorption is the ACTC candidate. ODC never
    # becomes refundable so the CTC slice alone feeds ACTC.
    ctc_leftover = ctc_after - nonref_ctc

    # ACTC: min of
    #   a) leftover CTC
    #   b) $1,700 * num_qc (refundable cap from OBBBA)
    #   c) 15% * max(0, earned_income - $2,500) (earned income floor)
    refundable_cap = Decimal(params.refundable_max_actc) * Decimal(num_qc)
    earned_floor_base = max(Decimal("0"), earned_income - _ACTC_EARNED_INCOME_FLOOR)
    earned_floor_limit = (earned_floor_base * _ACTC_EARNED_INCOME_RATE).quantize(
        Decimal("0.01")
    )
    actc = min(ctc_leftover, refundable_cap, earned_floor_limit)
    if actc < Decimal("0"):
        actc = Decimal("0")

    details: dict[str, Any] = {
        "filing_status": status_str,
        "num_qualifying_children": num_qc,
        "num_other_dependents": num_odc,
        "ctc_base": str(ctc_base),
        "odc_base": str(odc_base),
        "combined_base": str(combined_base),
        "phase_out_threshold": params.phase_out_start,
        "phase_out_raw_reduction": str(raw_reduction),
        "phase_out_applied": str(phase_out_applied),
        "ctc_after_phaseout": str(ctc_after),
        "odc_after_phaseout": str(odc_after),
        "tax_before_credits": str(tax_before_credits),
        "nonref_ctc": str(nonref_ctc),
        "nonref_odc": str(nonref_odc),
        "ctc_leftover_for_actc": str(ctc_leftover),
        "refundable_cap": str(refundable_cap),
        "earned_income": str(earned_income),
        "earned_floor_limit": str(earned_floor_limit),
        "actc": str(actc),
    }

    return CTCResult(
        nonrefundable_ctc=nonref_ctc,
        refundable_actc=actc,
        credit_for_other_dependents=nonref_odc,
        num_qualifying_children=num_qc,
        num_other_dependents=num_odc,
        phase_out_applied=phase_out_applied,
        details=details,
    )
