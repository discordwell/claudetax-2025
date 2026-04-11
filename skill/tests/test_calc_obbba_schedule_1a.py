"""Tests for the OBBBA Schedule 1-A tips and overtime deductions patch.

This test module LOCKS the current assumed parameter values so a future
session can update them trivially when IRS publishes final instructions.
Every numeric assumption lives in one of the ``test_documented_assumption_*``
tests below — change those values (and the matching constants inside
``skill/scripts/calc/patches/obbba_schedule_1a.py``) when Schedule 1-A final
instructions become available.

Sources consulted at implementation time:
- IRS "One Big Beautiful Bill Act: tax deductions for working Americans and
  seniors" newsroom page
  (https://www.irs.gov/newsroom/one-big-beautiful-bill-act-tax-deductions-for-working-americans-and-seniors)
  — confirms $25k tips cap, $12.5k single / $25k MFJ overtime cap,
  $150k single / $300k MFJ MAGI phase-out start, effective 2025-2028.
- IRS draft-forms page (https://www.irs.gov/draft-tax-forms) — Schedule 1-A
  draft not yet listed at implementation time (pending).
- Phase-out RATE ($100 per $1,000 of excess MAGI) was NOT retrievable from the
  IRS pages WebFetch could reach. This patch ASSUMES the common OBBBA
  drafting convention of $100 per $1,000 of excess MAGI (same mechanic as
  the senior deduction and several other OBBBA phase-outs). MUST be
  re-verified against final IRS Schedule 1-A instructions before shipping.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from skill.scripts.calc.patches.obbba_schedule_1a import (
    OVERTIME_CAP_MFJ,
    OVERTIME_CAP_SINGLE,
    PHASE_OUT_REDUCTION_PER_1000,
    PHASE_OUT_START_MFJ,
    PHASE_OUT_START_SINGLE,
    TIPS_CAP,
    Schedule1AResult,
    compute_schedule_1a,
)
from skill.scripts.models import (
    Address,
    CanonicalReturn,
    FilingStatus,
    Person,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _addr() -> Address:
    return Address(street1="1 Main St", city="Anywhere", state="CA", zip="90001")


def _person(
    first: str = "Pat",
    last: str = "Taxpayer",
    ssn: str = "123-45-6789",
    dob: dt.date = dt.date(1985, 6, 15),
) -> Person:
    return Person(first_name=first, last_name=last, ssn=ssn, date_of_birth=dob)


def _return(
    *,
    status: FilingStatus = FilingStatus.SINGLE,
    tax_year: int = 2025,
) -> CanonicalReturn:
    spouse: Person | None = None
    if status in (FilingStatus.MFJ, FilingStatus.MFS, FilingStatus.QSS):
        spouse = _person(first="Alex", ssn="987-65-4321", dob=dt.date(1986, 1, 1))
    return CanonicalReturn(
        tax_year=tax_year,
        filing_status=status,
        taxpayer=_person(),
        spouse=spouse,
        address=_addr(),
    )


# ---------------------------------------------------------------------------
# Zero / no-input cases
# ---------------------------------------------------------------------------


class TestZeroCases:
    def test_zero_tips_and_zero_overtime(self):
        ret = _return()
        result = compute_schedule_1a(
            ret,
            magi=Decimal("50000"),
            qualified_tips_input=Decimal("0"),
            qualified_overtime_input=Decimal("0"),
        )
        assert isinstance(result, Schedule1AResult)
        assert result.tips_deduction == Decimal("0")
        assert result.overtime_deduction == Decimal("0")
        assert result.total_deduction == Decimal("0")
        assert result.tips_cap_applied is False
        assert result.overtime_cap_applied is False
        assert result.phase_out_reduction == Decimal("0")

    def test_negative_inputs_are_clamped_to_zero(self):
        """Defensive: a caller should not pass negative amounts, but if they
        slip through we clamp to zero rather than producing a negative
        deduction."""
        ret = _return()
        result = compute_schedule_1a(
            ret,
            magi=Decimal("50000"),
            qualified_tips_input=Decimal("-500"),
            qualified_overtime_input=Decimal("-200"),
        )
        assert result.tips_deduction == Decimal("0")
        assert result.overtime_deduction == Decimal("0")
        assert result.total_deduction == Decimal("0")


# ---------------------------------------------------------------------------
# Tips deduction: cap behavior
# ---------------------------------------------------------------------------


class TestTipsDeduction:
    def test_tips_under_cap_pass_through(self):
        ret = _return()
        result = compute_schedule_1a(
            ret,
            magi=Decimal("60000"),
            qualified_tips_input=Decimal("1000"),
            qualified_overtime_input=Decimal("0"),
        )
        assert result.tips_deduction == Decimal("1000")
        assert result.tips_cap_applied is False
        assert result.overtime_deduction == Decimal("0")
        assert result.total_deduction == Decimal("1000")

    def test_tips_exactly_at_cap(self):
        ret = _return()
        result = compute_schedule_1a(
            ret,
            magi=Decimal("60000"),
            qualified_tips_input=TIPS_CAP,
            qualified_overtime_input=Decimal("0"),
        )
        assert result.tips_deduction == TIPS_CAP
        # Exactly at cap is not "over" the cap — convention: cap only applies
        # when we had to chop something off.
        assert result.tips_cap_applied is False

    def test_tips_over_cap_gets_capped(self):
        ret = _return()
        result = compute_schedule_1a(
            ret,
            magi=Decimal("60000"),
            qualified_tips_input=Decimal("40000"),  # > $25,000 cap
            qualified_overtime_input=Decimal("0"),
        )
        assert result.tips_deduction == TIPS_CAP
        assert result.tips_cap_applied is True
        assert result.total_deduction == TIPS_CAP

    def test_tips_cap_is_same_for_mfj_and_single(self):
        """Per IRS fact sheet, tips cap is $25k regardless of filing status."""
        single = compute_schedule_1a(
            _return(status=FilingStatus.SINGLE),
            magi=Decimal("60000"),
            qualified_tips_input=Decimal("100000"),
            qualified_overtime_input=Decimal("0"),
        )
        mfj = compute_schedule_1a(
            _return(status=FilingStatus.MFJ),
            magi=Decimal("60000"),
            qualified_tips_input=Decimal("100000"),
            qualified_overtime_input=Decimal("0"),
        )
        assert single.tips_deduction == mfj.tips_deduction == TIPS_CAP


# ---------------------------------------------------------------------------
# Overtime deduction: cap behavior (cap varies by filing status)
# ---------------------------------------------------------------------------


class TestOvertimeDeduction:
    def test_overtime_under_cap_pass_through(self):
        ret = _return()
        result = compute_schedule_1a(
            ret,
            magi=Decimal("60000"),
            qualified_tips_input=Decimal("0"),
            qualified_overtime_input=Decimal("3000"),
        )
        assert result.overtime_deduction == Decimal("3000")
        assert result.overtime_cap_applied is False

    def test_overtime_over_cap_single(self):
        ret = _return(status=FilingStatus.SINGLE)
        result = compute_schedule_1a(
            ret,
            magi=Decimal("60000"),
            qualified_tips_input=Decimal("0"),
            qualified_overtime_input=Decimal("20000"),  # > $12,500
        )
        assert result.overtime_deduction == OVERTIME_CAP_SINGLE
        assert result.overtime_cap_applied is True

    def test_overtime_over_cap_mfj_has_higher_cap(self):
        ret = _return(status=FilingStatus.MFJ)
        result = compute_schedule_1a(
            ret,
            magi=Decimal("60000"),
            qualified_tips_input=Decimal("0"),
            qualified_overtime_input=Decimal("30000"),  # > $25,000
        )
        assert result.overtime_deduction == OVERTIME_CAP_MFJ
        assert result.overtime_cap_applied is True

    def test_overtime_between_single_and_mfj_cap(self):
        """$20,000 overtime is over the $12.5k single cap but under the $25k
        MFJ cap. Single should cap; MFJ should pass through."""
        single = compute_schedule_1a(
            _return(status=FilingStatus.SINGLE),
            magi=Decimal("60000"),
            qualified_tips_input=Decimal("0"),
            qualified_overtime_input=Decimal("20000"),
        )
        mfj = compute_schedule_1a(
            _return(status=FilingStatus.MFJ),
            magi=Decimal("60000"),
            qualified_tips_input=Decimal("0"),
            qualified_overtime_input=Decimal("20000"),
        )
        assert single.overtime_deduction == OVERTIME_CAP_SINGLE
        assert single.overtime_cap_applied is True
        assert mfj.overtime_deduction == Decimal("20000")
        assert mfj.overtime_cap_applied is False

    def test_hoh_uses_single_overtime_cap(self):
        """HoH uses the single-filer $12,500 overtime cap (not the MFJ
        $25,000). This is the convention per the fact sheet wording that
        only MFJ gets the higher number."""
        ret = _return(status=FilingStatus.HOH)
        result = compute_schedule_1a(
            ret,
            magi=Decimal("60000"),
            qualified_tips_input=Decimal("0"),
            qualified_overtime_input=Decimal("20000"),
        )
        assert result.overtime_deduction == OVERTIME_CAP_SINGLE
        assert result.overtime_cap_applied is True


# ---------------------------------------------------------------------------
# Phase-out behavior
# ---------------------------------------------------------------------------


class TestPhaseOut:
    def test_magi_below_phase_out_no_reduction(self):
        ret = _return()
        result = compute_schedule_1a(
            ret,
            magi=Decimal("100000"),  # well below $150k
            qualified_tips_input=Decimal("5000"),
            qualified_overtime_input=Decimal("3000"),
        )
        assert result.phase_out_reduction == Decimal("0")
        assert result.tips_deduction == Decimal("5000")
        assert result.overtime_deduction == Decimal("3000")
        assert result.total_deduction == Decimal("8000")

    def test_magi_exactly_at_threshold_no_reduction(self):
        ret = _return()
        result = compute_schedule_1a(
            ret,
            magi=PHASE_OUT_START_SINGLE,
            qualified_tips_input=Decimal("5000"),
            qualified_overtime_input=Decimal("3000"),
        )
        assert result.phase_out_reduction == Decimal("0")
        assert result.total_deduction == Decimal("8000")

    def test_magi_just_over_threshold_partial_reduction(self):
        """$10,000 over threshold → 10 × $100 = $1,000 reduction."""
        ret = _return()
        result = compute_schedule_1a(
            ret,
            magi=PHASE_OUT_START_SINGLE + Decimal("10000"),
            qualified_tips_input=Decimal("10000"),
            qualified_overtime_input=Decimal("0"),
        )
        expected_reduction = Decimal("10") * PHASE_OUT_REDUCTION_PER_1000
        assert result.phase_out_reduction == expected_reduction
        assert result.total_deduction == Decimal("10000") - expected_reduction

    def test_magi_partial_thousand_rounds_up(self):
        """$500 over threshold → ceil to 1 × $100 = $100 reduction. This
        follows the 'or fraction thereof' convention of CTC / senior
        deduction. Re-verify when IRS Schedule 1-A instructions finalize."""
        ret = _return()
        result = compute_schedule_1a(
            ret,
            magi=PHASE_OUT_START_SINGLE + Decimal("500"),
            qualified_tips_input=Decimal("10000"),
            qualified_overtime_input=Decimal("0"),
        )
        assert result.phase_out_reduction == PHASE_OUT_REDUCTION_PER_1000
        assert result.total_deduction == Decimal("10000") - PHASE_OUT_REDUCTION_PER_1000

    def test_mfj_phase_out_threshold_is_higher(self):
        """MFJ filers start phase-out at $300k, not $150k."""
        ret = _return(status=FilingStatus.MFJ)
        # $200k MAGI: well over single threshold but well under MFJ threshold
        result = compute_schedule_1a(
            ret,
            magi=Decimal("200000"),
            qualified_tips_input=Decimal("5000"),
            qualified_overtime_input=Decimal("3000"),
        )
        assert result.phase_out_reduction == Decimal("0")
        assert result.total_deduction == Decimal("8000")

    def test_magi_far_above_threshold_fully_phases_out(self):
        """At MAGI high enough, reduction exceeds base and deduction → $0."""
        ret = _return()
        # Tips cap is $25k. Reduction at $100/$1k means $250k over threshold
        # would wipe out the $25k. Pick $500k MAGI (= $350k over) to be safe.
        result = compute_schedule_1a(
            ret,
            magi=Decimal("500000"),
            qualified_tips_input=Decimal("20000"),
            qualified_overtime_input=Decimal("10000"),
        )
        assert result.total_deduction == Decimal("0")
        # phase_out_reduction is clamped to the pre-reduction amount
        assert result.phase_out_reduction <= Decimal("30000")
        assert result.tips_deduction == Decimal("0")
        assert result.overtime_deduction == Decimal("0")

    def test_phase_out_applied_pro_rata_between_tips_and_overtime(self):
        """When the phase-out partially reduces the combined amount, the
        reduction is apportioned so the total ends up correct. Check the
        sum, not the individual split (split is an implementation detail
        documented in the module)."""
        ret = _return()
        # $50,000 over threshold → $5,000 reduction
        result = compute_schedule_1a(
            ret,
            magi=PHASE_OUT_START_SINGLE + Decimal("50000"),
            qualified_tips_input=Decimal("10000"),
            qualified_overtime_input=Decimal("5000"),
        )
        assert result.phase_out_reduction == Decimal("5000")
        assert result.total_deduction == Decimal("10000")
        assert result.tips_deduction + result.overtime_deduction == result.total_deduction


# ---------------------------------------------------------------------------
# Combined tips + overtime
# ---------------------------------------------------------------------------


class TestCombined:
    def test_combined_tips_and_overtime_sum_correctly(self):
        ret = _return()
        result = compute_schedule_1a(
            ret,
            magi=Decimal("60000"),
            qualified_tips_input=Decimal("2000"),
            qualified_overtime_input=Decimal("1500"),
        )
        assert result.tips_deduction == Decimal("2000")
        assert result.overtime_deduction == Decimal("1500")
        assert result.total_deduction == Decimal("3500")

    def test_combined_both_capped(self):
        ret = _return(status=FilingStatus.MFJ)
        result = compute_schedule_1a(
            ret,
            magi=Decimal("60000"),
            qualified_tips_input=Decimal("50000"),
            qualified_overtime_input=Decimal("40000"),
        )
        assert result.tips_deduction == TIPS_CAP
        assert result.overtime_deduction == OVERTIME_CAP_MFJ
        assert result.tips_cap_applied is True
        assert result.overtime_cap_applied is True
        assert result.total_deduction == TIPS_CAP + OVERTIME_CAP_MFJ


# ---------------------------------------------------------------------------
# Year-window enforcement (TY2025-2028 only)
# ---------------------------------------------------------------------------


class TestYearWindow:
    def test_year_2024_returns_zero(self):
        """OBBBA Schedule 1-A only applies starting TY2025."""
        ret = _return(tax_year=2024)
        result = compute_schedule_1a(
            ret,
            magi=Decimal("60000"),
            qualified_tips_input=Decimal("10000"),
            qualified_overtime_input=Decimal("5000"),
        )
        assert result.total_deduction == Decimal("0")
        assert result.tips_deduction == Decimal("0")
        assert result.overtime_deduction == Decimal("0")
        assert result.details.get("year_out_of_window") is True

    def test_year_2028_still_applies(self):
        ret = _return(tax_year=2028)
        result = compute_schedule_1a(
            ret,
            magi=Decimal("60000"),
            qualified_tips_input=Decimal("1000"),
            qualified_overtime_input=Decimal("500"),
        )
        assert result.total_deduction == Decimal("1500")
        assert result.details.get("year_out_of_window") is not True

    def test_year_2029_returns_zero_sunset(self):
        """OBBBA provision sunsets after TY2028."""
        ret = _return(tax_year=2029)
        result = compute_schedule_1a(
            ret,
            magi=Decimal("60000"),
            qualified_tips_input=Decimal("10000"),
            qualified_overtime_input=Decimal("5000"),
        )
        assert result.total_deduction == Decimal("0")
        assert result.details.get("year_out_of_window") is True


# ---------------------------------------------------------------------------
# Documented assumptions — update these values AND the matching module
# constants when IRS publishes final Schedule 1-A instructions.
#
# Naming convention: every test in this class starts with
# ``test_documented_assumption_*`` so a future maintainer can run
# ``pytest -k documented_assumption`` to see the current assumption surface.
# ---------------------------------------------------------------------------


class TestDocumentedAssumptions:
    def test_documented_assumption_tips_cap(self):
        """ASSUMPTION: Qualified tips deduction cap is $25,000.

        Source: IRS newsroom page "One Big Beautiful Bill Act: tax
        deductions for working Americans and seniors"
        (https://www.irs.gov/newsroom/one-big-beautiful-bill-act-tax-deductions-for-working-americans-and-seniors)
        — "Maximum annual deduction is $25,000".
        """
        assert TIPS_CAP == Decimal("25000")

    def test_documented_assumption_overtime_cap_single(self):
        """ASSUMPTION: Qualified overtime deduction cap for single/HoH/MFS
        filers is $12,500. Source: same IRS newsroom page.
        """
        assert OVERTIME_CAP_SINGLE == Decimal("12500")

    def test_documented_assumption_overtime_cap_mfj(self):
        """ASSUMPTION: Qualified overtime deduction cap for MFJ/QSS filers
        is $25,000. Source: same IRS newsroom page.
        """
        assert OVERTIME_CAP_MFJ == Decimal("25000")

    def test_documented_assumption_phase_out_start_single(self):
        """ASSUMPTION: Phase-out begins at $150,000 MAGI for single/HoH/MFS
        filers. Source: same IRS newsroom page.
        """
        assert PHASE_OUT_START_SINGLE == Decimal("150000")

    def test_documented_assumption_phase_out_start_mfj(self):
        """ASSUMPTION: Phase-out begins at $300,000 MAGI for MFJ/QSS filers.
        Source: same IRS newsroom page.
        """
        assert PHASE_OUT_START_MFJ == Decimal("300000")

    def test_documented_assumption_phase_out_rate(self):
        """ASSUMPTION (UNVERIFIED): Phase-out rate is $100 per $1,000 of MAGI
        in excess of the threshold. This matches the common OBBBA drafting
        convention used by the senior deduction and several other
        provisions, but the IRS newsroom page and draft-forms page do NOT
        state the Schedule 1-A phase-out rate explicitly, and the final
        Schedule 1-A instructions had not been published at implementation
        time. MUST be re-verified against final IRS guidance before
        shipping to real filers.
        """
        assert PHASE_OUT_REDUCTION_PER_1000 == Decimal("100")

    def test_documented_assumption_result_details_surface_list(self):
        """Every Schedule1AResult should list the assumptions it depended
        on in details['assumptions'] so downstream consumers can flag them
        in audit logs."""
        ret = _return()
        result = compute_schedule_1a(
            ret,
            magi=Decimal("60000"),
            qualified_tips_input=Decimal("1000"),
            qualified_overtime_input=Decimal("500"),
        )
        assumptions = result.details.get("assumptions")
        assert isinstance(assumptions, list)
        assert len(assumptions) > 0
        # At least one assumption should reference the $100/$1,000 phase-out
        # rate being unverified.
        joined = " ".join(assumptions).lower()
        assert "phase" in joined
        assert "unverified" in joined or "pending" in joined or "re-verify" in joined
