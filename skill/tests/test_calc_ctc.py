"""Tests for the Child Tax Credit + ACTC + ODC patch.

TY2025 / OBBBA / IRC §24:
- $2,200 per qualifying child under 17
- $1,700 refundable max per child (ACTC)
- $500 per other dependent (ODC) — nonrefundable only
- Phase-out $50 per $1,000 (or fraction) of MAGI over threshold:
    $200k single/HoH/MFS, $400k MFJ/QSS
- ACTC floor: 15% x max(0, earned_income - $2,500)

Phase-out ordering: the patch reduces the COMBINED CTC+ODC base credit
in a single sweep (OBBBA/§24 approach). See the phaseout_ordering test
docstring below for the full rationale.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from skill.scripts.calc.patches.ctc import CTCResult, compute_ctc
from skill.scripts.models import (
    Address,
    CanonicalReturn,
    Dependent,
    DependentRelationship,
    FilingStatus,
    Person,
    W2,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _person(
    first: str,
    last: str,
    ssn: str,
    dob: dt.date,
) -> Person:
    return Person(
        first_name=first,
        last_name=last,
        ssn=ssn,
        date_of_birth=dob,
    )


def _qualifying_child(
    first: str,
    ssn: str,
    dob: dt.date,
) -> Dependent:
    return Dependent(
        person=_person(first, "Kid", ssn, dob),
        relationship=DependentRelationship.SON,
        months_lived_with_taxpayer=12,
        is_qualifying_child=True,
        is_qualifying_relative=False,
    )


def _other_dependent(
    first: str,
    ssn: str,
    dob: dt.date,
    relationship: DependentRelationship = DependentRelationship.PARENT,
) -> Dependent:
    return Dependent(
        person=_person(first, "Rel", ssn, dob),
        relationship=relationship,
        months_lived_with_taxpayer=12,
        is_qualifying_child=False,
        is_qualifying_relative=True,
    )


def _make_return(
    status: FilingStatus,
    dependents: list[Dependent],
    tax_year: int = 2025,
    wages: Decimal = Decimal("60000"),
) -> CanonicalReturn:
    taxpayer = _person("Alex", "Doe", "111-11-1111", dt.date(1985, 5, 5))
    spouse = (
        _person("Sam", "Doe", "222-22-2222", dt.date(1986, 6, 6))
        if status in (FilingStatus.MFJ, FilingStatus.MFS)
        else None
    )
    return CanonicalReturn(
        tax_year=tax_year,
        filing_status=status,
        taxpayer=taxpayer,
        spouse=spouse,
        address=Address(
            street1="1 Elm",
            city="Springfield",
            state="IL",
            zip="62701",
        ),
        dependents=dependents,
        w2s=[
            W2(
                employer_name="Acme",
                employer_ein="12-3456789",
                box1_wages=wages,
            )
        ],
    )


# ---------------------------------------------------------------------------
# No dependents
# ---------------------------------------------------------------------------


def test_no_dependents_zero_everything() -> None:
    ret = _make_return(FilingStatus.SINGLE, dependents=[])
    result = compute_ctc(
        return_=ret,
        magi=Decimal("60000"),
        tax_before_credits=Decimal("5000"),
        earned_income=Decimal("60000"),
    )
    assert isinstance(result, CTCResult)
    assert result.num_qualifying_children == 0
    assert result.num_other_dependents == 0
    assert result.nonrefundable_ctc == Decimal("0")
    assert result.refundable_actc == Decimal("0")
    assert result.credit_for_other_dependents == Decimal("0")
    assert result.phase_out_applied == Decimal("0")


# ---------------------------------------------------------------------------
# Basic nonrefundable absorption
# ---------------------------------------------------------------------------


def test_single_one_child_tax_absorbs_full_credit() -> None:
    """1 child, $60k MAGI (well below phase-out), $4k tax — nonref CTC fills first."""
    child = _qualifying_child("Kid1", "555-00-0001", dt.date(2018, 3, 4))
    ret = _make_return(FilingStatus.SINGLE, dependents=[child])

    result = compute_ctc(
        return_=ret,
        magi=Decimal("60000"),
        tax_before_credits=Decimal("4000"),
        earned_income=Decimal("60000"),
    )

    assert result.num_qualifying_children == 1
    assert result.nonrefundable_ctc == Decimal("2200")
    assert result.refundable_actc == Decimal("0")
    assert result.credit_for_other_dependents == Decimal("0")
    assert result.phase_out_applied == Decimal("0")


def test_single_one_child_low_tax_actc_kicks_in() -> None:
    """1 child, $500 tax — nonref $500, remaining $1,700 → ACTC = $1,700 (capped)."""
    child = _qualifying_child("Kid1", "555-00-0001", dt.date(2018, 3, 4))
    ret = _make_return(FilingStatus.SINGLE, dependents=[child])

    result = compute_ctc(
        return_=ret,
        magi=Decimal("60000"),
        tax_before_credits=Decimal("500"),
        earned_income=Decimal("60000"),
    )

    assert result.num_qualifying_children == 1
    assert result.nonrefundable_ctc == Decimal("500")
    # 15% * (60000 - 2500) = 0.15 * 57500 = 8625, capped at $1,700/child
    assert result.refundable_actc == Decimal("1700")


def test_single_two_children_low_tax_actc() -> None:
    """2 children, $500 tax — nonref $500, remaining $3,900.
    ACTC cap = 2 * $1,700 = $3,400; 15% earned floor = 0.15 * 57500 = $8,625.
    ACTC = min($3,400, $8,625) = $3,400. Remaining credit $500 unused."""
    child1 = _qualifying_child("Kid1", "555-00-0001", dt.date(2018, 3, 4))
    child2 = _qualifying_child("Kid2", "555-00-0002", dt.date(2020, 5, 6))
    ret = _make_return(FilingStatus.SINGLE, dependents=[child1, child2])

    result = compute_ctc(
        return_=ret,
        magi=Decimal("60000"),
        tax_before_credits=Decimal("500"),
        earned_income=Decimal("60000"),
    )

    assert result.num_qualifying_children == 2
    assert result.nonrefundable_ctc == Decimal("500")
    assert result.refundable_actc == Decimal("3400")


# ---------------------------------------------------------------------------
# Phase-out
# ---------------------------------------------------------------------------


def test_mfj_phaseout_wipes_credit() -> None:
    """MFJ threshold $400k; at $500k, excess = $100k → phase-out = $50 * 100 = $5,000.
    Base = 2 * $2,200 = $4,400. Phase-out exceeds base → credit = $0."""
    child1 = _qualifying_child("Kid1", "555-00-0001", dt.date(2018, 3, 4))
    child2 = _qualifying_child("Kid2", "555-00-0002", dt.date(2020, 5, 6))
    ret = _make_return(FilingStatus.MFJ, dependents=[child1, child2])

    result = compute_ctc(
        return_=ret,
        magi=Decimal("500000"),
        tax_before_credits=Decimal("80000"),
        earned_income=Decimal("500000"),
    )

    assert result.num_qualifying_children == 2
    assert result.nonrefundable_ctc == Decimal("0")
    assert result.refundable_actc == Decimal("0")
    assert result.credit_for_other_dependents == Decimal("0")
    assert result.phase_out_applied == Decimal("4400")  # clamped to base


def test_single_phaseout_partial() -> None:
    """Single threshold $200k; at $250k, excess = $50k → phase-out = $50 * 50 = $2,500.
    Base = 2 * $2,200 = $4,400. Remaining = $1,900 (all CTC, no ODC)."""
    child1 = _qualifying_child("Kid1", "555-00-0001", dt.date(2018, 3, 4))
    child2 = _qualifying_child("Kid2", "555-00-0002", dt.date(2020, 5, 6))
    ret = _make_return(FilingStatus.SINGLE, dependents=[child1, child2])

    result = compute_ctc(
        return_=ret,
        magi=Decimal("250000"),
        tax_before_credits=Decimal("40000"),
        earned_income=Decimal("250000"),
    )

    assert result.num_qualifying_children == 2
    assert result.phase_out_applied == Decimal("2500")
    # Remaining $1,900 fits within tax → fully nonrefundable
    assert result.nonrefundable_ctc == Decimal("1900")
    assert result.refundable_actc == Decimal("0")


def test_phaseout_rounds_fraction_up() -> None:
    """MAGI $200,001 single → excess $1, ceil/1000 = 1 → reduction = $50."""
    child = _qualifying_child("Kid1", "555-00-0001", dt.date(2018, 3, 4))
    ret = _make_return(FilingStatus.SINGLE, dependents=[child])

    result = compute_ctc(
        return_=ret,
        magi=Decimal("200001"),
        tax_before_credits=Decimal("30000"),
        earned_income=Decimal("200001"),
    )
    assert result.phase_out_applied == Decimal("50")
    assert result.nonrefundable_ctc == Decimal("2150")


# ---------------------------------------------------------------------------
# Age cutoff
# ---------------------------------------------------------------------------


def test_child_turns_17_on_dec31_is_not_qualifying() -> None:
    """Child born 12/31/2008; tax year 2025 → child turns 17 on 12/31/2025.
    §24 requires UNDER 17 at end of year → this child does NOT qualify as CTC,
    but counts as an ODC ($500)."""
    child = Dependent(
        person=_person("Teen", "Doe", "555-00-0017", dt.date(2008, 12, 31)),
        relationship=DependentRelationship.SON,
        months_lived_with_taxpayer=12,
        is_qualifying_child=True,
        is_qualifying_relative=False,
    )
    ret = _make_return(FilingStatus.SINGLE, dependents=[child])

    result = compute_ctc(
        return_=ret,
        magi=Decimal("60000"),
        tax_before_credits=Decimal("5000"),
        earned_income=Decimal("60000"),
    )

    assert result.num_qualifying_children == 0
    assert result.num_other_dependents == 1
    assert result.nonrefundable_ctc == Decimal("0")
    assert result.credit_for_other_dependents == Decimal("500")
    assert result.refundable_actc == Decimal("0")


def test_child_born_jan1_next_year_boundary() -> None:
    """Child born 1/1/2009; tax year 2025 → is 16 all of 2025 → qualifying."""
    child = _qualifying_child("Kid1", "555-00-0001", dt.date(2009, 1, 1))
    ret = _make_return(FilingStatus.SINGLE, dependents=[child])
    result = compute_ctc(
        return_=ret,
        magi=Decimal("60000"),
        tax_before_credits=Decimal("5000"),
        earned_income=Decimal("60000"),
    )
    assert result.num_qualifying_children == 1
    assert result.nonrefundable_ctc == Decimal("2200")


# ---------------------------------------------------------------------------
# ODC
# ---------------------------------------------------------------------------


def test_one_child_plus_one_other_dependent() -> None:
    """1 qualifying child ($2,200) + 1 qualifying relative ($500)."""
    child = _qualifying_child("Kid1", "555-00-0001", dt.date(2018, 3, 4))
    parent = _other_dependent(
        "Grandma",
        "555-00-0099",
        dt.date(1955, 6, 15),
        relationship=DependentRelationship.PARENT,
    )
    ret = _make_return(FilingStatus.SINGLE, dependents=[child, parent])

    result = compute_ctc(
        return_=ret,
        magi=Decimal("60000"),
        tax_before_credits=Decimal("10000"),
        earned_income=Decimal("60000"),
    )

    assert result.num_qualifying_children == 1
    assert result.num_other_dependents == 1
    assert result.nonrefundable_ctc == Decimal("2200")
    assert result.credit_for_other_dependents == Decimal("500")
    assert result.refundable_actc == Decimal("0")


# ---------------------------------------------------------------------------
# ACTC earned income floor
# ---------------------------------------------------------------------------


def test_actc_zero_when_no_earned_income() -> None:
    child = _qualifying_child("Kid1", "555-00-0001", dt.date(2018, 3, 4))
    ret = _make_return(FilingStatus.SINGLE, dependents=[child])

    result = compute_ctc(
        return_=ret,
        magi=Decimal("60000"),
        tax_before_credits=Decimal("0"),
        earned_income=Decimal("0"),
    )
    assert result.nonrefundable_ctc == Decimal("0")
    assert result.refundable_actc == Decimal("0")


def test_actc_capped_by_15pct_earned_income_floor() -> None:
    """Earned income $3,000 → 15% * ($3,000 - $2,500) = 15% * $500 = $75.
    Tax = 0, so remaining credit = $2,200. ACTC = min($1,700, $75) = $75."""
    child = _qualifying_child("Kid1", "555-00-0001", dt.date(2018, 3, 4))
    ret = _make_return(FilingStatus.SINGLE, dependents=[child])

    result = compute_ctc(
        return_=ret,
        magi=Decimal("60000"),
        tax_before_credits=Decimal("0"),
        earned_income=Decimal("3000"),
    )
    assert result.nonrefundable_ctc == Decimal("0")
    assert result.refundable_actc == Decimal("75")


def test_actc_earned_income_below_2500_floor() -> None:
    """Earned income $2,000 → below $2,500 floor → ACTC = 0."""
    child = _qualifying_child("Kid1", "555-00-0001", dt.date(2018, 3, 4))
    ret = _make_return(FilingStatus.SINGLE, dependents=[child])

    result = compute_ctc(
        return_=ret,
        magi=Decimal("60000"),
        tax_before_credits=Decimal("0"),
        earned_income=Decimal("2000"),
    )
    assert result.refundable_actc == Decimal("0")


# ---------------------------------------------------------------------------
# Phase-out ordering lock
# ---------------------------------------------------------------------------


def test_phaseout_ordering_locked_combined() -> None:
    """LOCK IN: the phase-out is applied to the COMBINED CTC + ODC base.

    §24 treats the $50-per-$1,000 reduction as applying to the aggregate
    "child tax credit and credit for other dependents" (a single line on the
    CTC worksheet). We therefore:
      1. Sum base = CTC_base + ODC_base
      2. Subtract the phase-out reduction from the sum
      3. Allocate what remains proportionally: CTC first, then ODC, because
         IRS pub 972 / Schedule 8812 worksheets reduce ODC before CTC for
         refundable purposes (the $1,700 ACTC cap must track the CTC slice,
         not the ODC slice).

    Alternative rejected: applying the phase-out to CTC first then ODC
    separately. This would overcount ODC in high-MAGI scenarios.

    This test exercises the edge case where the phase-out exceeds the ODC
    portion but not the combined total: MFJ $420k MAGI (excess $20k →
    reduction $1,000), 1 child + 1 other dep (base $2,700). After phase-out:
    $1,700 remaining. Under combined ordering, ODC is reduced first so
    CTC retains the full $2,200 (capped by remaining $1,700) and ODC = $0.
    """
    child = _qualifying_child("Kid1", "555-00-0001", dt.date(2018, 3, 4))
    parent = _other_dependent("Grandma", "555-00-0099", dt.date(1955, 6, 15))
    ret = _make_return(FilingStatus.MFJ, dependents=[child, parent])

    result = compute_ctc(
        return_=ret,
        magi=Decimal("420000"),
        tax_before_credits=Decimal("50000"),
        earned_income=Decimal("420000"),
    )

    # base 2700, reduction 1000, combined remaining 1700
    assert result.phase_out_applied == Decimal("1000")
    # ODC is reduced first; CTC slice gets remaining after ODC is emptied
    # ODC starts at 500, absorbs $500 of reduction → ODC = 0
    # CTC starts at 2200, absorbs remaining $500 → CTC = 1700
    assert result.credit_for_other_dependents == Decimal("0")
    assert result.nonrefundable_ctc == Decimal("1700")
