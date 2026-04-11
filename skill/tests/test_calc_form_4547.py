"""Tests for the OBBBA Form 4547 Trump Account patch.

TY2025-2028 / OBBBA P.L. 119-21 / IRC §§128, 219, 530A.

Primary IRS sources (researched 2026-04-11):
  - https://www.irs.gov/forms-pubs/about-form-4547
  - https://www.irs.gov/instructions/i4547 (December 2025)
  - https://www.irs.gov/pub/irs-pdf/f4547.pdf (December 2025)
  - https://www.irs.gov/newsroom/treasury-irs-issue-guidance-on-trump-accounts-established-under-the-working-families-tax-cuts-notice-announces-upcoming-regulations
  - https://www.federalregister.gov/documents/2026/03/09/2026-04533/trump-accounts

KEY FINDING: Form 4547 is an ELECTION form, not a deduction form. IRC §219
explicitly disallows any individual deduction for Trump Account contributions.
Therefore:

  - ``deduction`` is ALWAYS $0
  - ``phase_out_reduction`` is ALWAYS $0
  - ``num_qualifying_children`` counts children eligible to receive the
    $1,000 Pilot Program Contribution election (born 2025-2028, under 18)

These tests lock the $0 behavior and the qualifying-child counting. Every
assertion that depends on an UNVERIFIED assumption is tagged with

    # LOCKED: pending final Form 4547 instructions per P.L. 119-21

so the tests tell you exactly what to re-verify when IRS issues final
Treasury regulations (NPRM docket 2026-04533).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from skill.scripts.calc.patches.form_4547_trump_account import (
    TrumpAccountResult,
    compute_trump_account_deduction,
)
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
    first: str = "Alex",
    last: str = "Doe",
    ssn: str = "111-11-1111",
    dob: dt.date = dt.date(1985, 5, 5),
) -> Person:
    return Person(first_name=first, last_name=last, ssn=ssn, date_of_birth=dob)


def _addr() -> Address:
    return Address(street1="1 Elm", city="Springfield", state="IL", zip="62701")


def _qualifying_child(
    first: str,
    ssn: str,
    dob: dt.date,
    months_lived: int = 12,
    relationship: DependentRelationship = DependentRelationship.SON,
) -> Dependent:
    """Build a Dependent that is a qualifying child."""
    return Dependent(
        person=_person(first=first, last="Doe", ssn=ssn, dob=dob),
        relationship=relationship,
        months_lived_with_taxpayer=months_lived,
        is_qualifying_child=True,
        is_qualifying_relative=False,
    )


def _qualifying_relative(
    first: str,
    ssn: str,
    dob: dt.date,
) -> Dependent:
    """Build a Dependent that is a qualifying relative (NOT a qualifying child)."""
    return Dependent(
        person=_person(first=first, last="Doe", ssn=ssn, dob=dob),
        relationship=DependentRelationship.OTHER,
        months_lived_with_taxpayer=12,
        is_qualifying_child=False,
        is_qualifying_relative=True,
    )


def _make_return(
    dependents: list[Dependent] | None = None,
    tax_year: int = 2025,
    status: FilingStatus = FilingStatus.MFJ,
    wages: Decimal = Decimal("80000"),
) -> CanonicalReturn:
    taxpayer = _person("Parent1", "Doe", "111-11-1111", dt.date(1990, 3, 3))
    spouse: Person | None = None
    if status in (FilingStatus.MFJ, FilingStatus.MFS):
        spouse = _person("Parent2", "Doe", "222-22-2222", dt.date(1991, 4, 4))
    return CanonicalReturn(
        tax_year=tax_year,
        filing_status=status,
        taxpayer=taxpayer,
        spouse=spouse,
        address=_addr(),
        dependents=dependents or [],
        w2s=[
            W2(employer_name="Acme", employer_ein="12-3456789", box1_wages=wages),
        ],
    )


# ---------------------------------------------------------------------------
# Deduction is ALWAYS $0 (primary finding)
# ---------------------------------------------------------------------------


def test_no_qualifying_children_returns_zero() -> None:
    """No dependents: deduction is $0 and count is 0."""
    ret = _make_return(dependents=[])
    result = compute_trump_account_deduction(return_=ret, magi=Decimal("80000"))

    assert isinstance(result, TrumpAccountResult)
    assert result.num_qualifying_children == 0
    assert result.base_deduction == Decimal("0")
    assert result.phase_out_reduction == Decimal("0")
    assert result.deduction == Decimal("0")


def test_one_qualifying_child_no_phase_out() -> None:
    """Born 2026 (inside pilot window), under 18, marked qualifying child.

    The deduction is still $0 per IRC §219, but the count is 1.
    LOCKED: pending final Form 4547 instructions per P.L. 119-21.
    """
    child = _qualifying_child(
        "Baby",
        ssn="333-33-3333",
        dob=dt.date(2026, 6, 15),
    )
    ret = _make_return(dependents=[child], tax_year=2026)
    result = compute_trump_account_deduction(return_=ret, magi=Decimal("80000"))

    # LOCKED: pending final Form 4547 instructions per P.L. 119-21
    assert result.num_qualifying_children == 1
    # The deduction is $0 — VERIFIED per IRC §219 (IRS instructions explicit)
    assert result.base_deduction == Decimal("0")
    assert result.phase_out_reduction == Decimal("0")
    assert result.deduction == Decimal("0")


def test_two_qualifying_children_partial_phase_out() -> None:
    """Two eligible children, high MAGI.

    There is NO phase-out on Trump Account eligibility in current IRS
    guidance (UNVERIFIED U4). Count stays 2; deduction stays $0.
    LOCKED: pending final Form 4547 instructions per P.L. 119-21.
    """
    child1 = _qualifying_child(
        "Baby1",
        ssn="333-33-3333",
        dob=dt.date(2025, 3, 10),
    )
    child2 = _qualifying_child(
        "Baby2",
        ssn="444-44-4444",
        dob=dt.date(2027, 9, 20),
    )
    ret = _make_return(dependents=[child1, child2], tax_year=2027)
    # High MAGI — if a phase-out existed at all it would trigger here.
    result = compute_trump_account_deduction(
        return_=ret, magi=Decimal("500000")
    )

    # LOCKED: pending final Form 4547 instructions per P.L. 119-21
    assert result.num_qualifying_children == 2
    # LOCKED: no phase-out in current IRS guidance (UNVERIFIED U4)
    assert result.phase_out_reduction == Decimal("0")
    # VERIFIED: deduction is always $0 per IRC §219
    assert result.base_deduction == Decimal("0")
    assert result.deduction == Decimal("0")


# ---------------------------------------------------------------------------
# Year gating
# ---------------------------------------------------------------------------


def test_year_gated_2029_returns_zero() -> None:
    """TY2029 is outside the OBBBA Form 4547 window.

    LOCKED: pending final Form 4547 instructions per P.L. 119-21.
    The statute's pilot-contribution window is children born 2025-2028;
    we lock years_applicable to the same window. Re-verify when Treasury
    issues final regulations.
    """
    child = _qualifying_child(
        "Baby",
        ssn="333-33-3333",
        dob=dt.date(2028, 12, 1),
    )
    ret = _make_return(dependents=[child], tax_year=2029)
    result = compute_trump_account_deduction(return_=ret, magi=Decimal("80000"))

    assert result.deduction == Decimal("0")
    assert result.base_deduction == Decimal("0")
    assert result.phase_out_reduction == Decimal("0")
    # LOCKED: pending final Form 4547 instructions per P.L. 119-21
    assert result.details.get("year_gated") is True


def test_year_gated_2024_returns_zero() -> None:
    """Before OBBBA's TY2025 effective year: election not available.

    LOCKED: pending final Form 4547 instructions per P.L. 119-21.
    """
    child = _qualifying_child(
        "Baby",
        ssn="333-33-3333",
        dob=dt.date(2025, 1, 5),
    )
    # TY2024 is pre-OBBBA. Model allows tax_year >= 2024.
    ret = _make_return(dependents=[child], tax_year=2024)
    result = compute_trump_account_deduction(return_=ret, magi=Decimal("80000"))

    assert result.deduction == Decimal("0")
    assert result.base_deduction == Decimal("0")
    # LOCKED: pending final Form 4547 instructions per P.L. 119-21
    assert result.details.get("year_gated") is True


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


def test_audit_trail_details_populated() -> None:
    """details dict must expose counts, caps, and assumption flags."""
    child = _qualifying_child(
        "Baby",
        ssn="333-33-3333",
        dob=dt.date(2026, 6, 15),
    )
    ret = _make_return(dependents=[child], tax_year=2026)
    result = compute_trump_account_deduction(
        return_=ret, magi=Decimal("120000")
    )

    d = result.details
    assert "filing_status" in d
    assert "tax_year" in d
    assert "years_applicable" in d
    assert "magi" in d
    assert "num_qualifying_children" in d
    assert "annual_contribution_cap" in d
    assert "pilot_contribution_amount_per_child" in d
    assert "pilot_contribution_total_eligible" in d
    assert "beneficiary_max_age" in d
    assert "pilot_birth_window_start" in d
    assert "pilot_birth_window_end" in d
    assert "deduction_note" in d

    # VERIFIED values
    assert Decimal(d["annual_contribution_cap"]) == Decimal("5000")
    assert Decimal(d["pilot_contribution_amount_per_child"]) == Decimal("1000")
    assert Decimal(d["pilot_contribution_total_eligible"]) == Decimal("1000")
    assert d["beneficiary_max_age"] == 18
    assert d["pilot_birth_window_start"] == "2025-01-01"
    assert d["pilot_birth_window_end"] == "2028-12-31"
    assert d["tax_year"] == 2026
    assert d["year_gated"] is False

    # Assumption flags must be present so a reviewer can find them.
    assert "VERIFIED_assumptions" in d
    assert "UNVERIFIED_assumptions" in d
    assert any("§219" in v for v in d["VERIFIED_assumptions"])
    assert any("U1" in u for u in d["UNVERIFIED_assumptions"])
    # LOCKED: pending final Form 4547 instructions per P.L. 119-21
    assert any("U4" in u for u in d["UNVERIFIED_assumptions"])

    # Loud warning about §219 non-deductibility must be in the audit trail.
    assert "§219" in d["deduction_note"]
    assert "$0" in d["deduction_note"]


# ---------------------------------------------------------------------------
# Qualifying-child eligibility edge cases
# ---------------------------------------------------------------------------


def test_child_born_before_pilot_window_not_counted() -> None:
    """Born 2024-12-31: before the pilot window — does not count.

    LOCKED: pending final Form 4547 instructions per P.L. 119-21.
    Current instructions say "born after December 31, 2024" — we model
    this as a closed window starting 2025-01-01.
    """
    child = _qualifying_child(
        "Toddler",
        ssn="333-33-3333",
        dob=dt.date(2024, 12, 31),
    )
    ret = _make_return(dependents=[child], tax_year=2025)
    result = compute_trump_account_deduction(return_=ret, magi=Decimal("80000"))

    # LOCKED: pending final Form 4547 instructions per P.L. 119-21
    assert result.num_qualifying_children == 0
    assert result.deduction == Decimal("0")


def test_child_born_after_pilot_window_not_counted() -> None:
    """Born 2029-01-01: after the pilot window — does not count.

    LOCKED: pending final Form 4547 instructions per P.L. 119-21.
    Current instructions say "before January 1, 2029" — we model as a
    closed window ending 2028-12-31.

    Note: we use tax_year=2028 (within the OBBBA window) to isolate the
    birth-window check from the year-gate check.
    """
    # A child born 2029-01-01 can't actually exist on a TY2028 return,
    # but this test documents the boundary programmatically.
    child = _qualifying_child(
        "FutureBaby",
        ssn="333-33-3333",
        dob=dt.date(2029, 1, 1),
    )
    ret = _make_return(dependents=[child], tax_year=2028)
    result = compute_trump_account_deduction(return_=ret, magi=Decimal("80000"))

    # LOCKED: pending final Form 4547 instructions per P.L. 119-21
    assert result.num_qualifying_children == 0
    assert result.deduction == Decimal("0")


def test_qualifying_relative_not_counted() -> None:
    """A qualifying relative (not a qualifying child) is not eligible.

    LOCKED: pending final Form 4547 instructions per P.L. 119-21.
    Uses the Dependent.is_qualifying_child flag as proxy for the
    "anticipated qualifying child" language in the instructions (U2).
    """
    rel = _qualifying_relative(
        "Uncle",
        ssn="333-33-3333",
        dob=dt.date(2026, 6, 15),  # in pilot window but not a child
    )
    ret = _make_return(dependents=[rel], tax_year=2026)
    result = compute_trump_account_deduction(return_=ret, magi=Decimal("80000"))

    # LOCKED: pending final Form 4547 instructions per P.L. 119-21
    assert result.num_qualifying_children == 0
    assert result.deduction == Decimal("0")


def test_result_is_frozen_dataclass() -> None:
    """TrumpAccountResult must be immutable."""
    ret = _make_return(dependents=[])
    result = compute_trump_account_deduction(return_=ret, magi=Decimal("80000"))
    with pytest.raises(Exception):
        result.deduction = Decimal("999")  # type: ignore[misc]


def test_high_magi_does_not_trigger_phase_out() -> None:
    """No phase-out on Trump Account eligibility in current IRS guidance.

    LOCKED: pending final Form 4547 instructions per P.L. 119-21.
    If final regulations add a phase-out (UNVERIFIED U4), this test will
    need to be updated with real thresholds.
    """
    child = _qualifying_child(
        "Baby",
        ssn="333-33-3333",
        dob=dt.date(2026, 6, 15),
    )
    ret = _make_return(dependents=[child], tax_year=2026)
    # $10,000,000 MAGI — if any phase-out existed, it would zero out here.
    result = compute_trump_account_deduction(
        return_=ret, magi=Decimal("10000000")
    )

    # LOCKED: pending final Form 4547 instructions per P.L. 119-21
    assert result.num_qualifying_children == 1
    assert result.phase_out_reduction == Decimal("0")
    # Deduction is $0 regardless of MAGI — VERIFIED
    assert result.deduction == Decimal("0")


def test_magi_passed_through_to_audit_trail() -> None:
    """MAGI should be stored in details even though it is not used
    arithmetically, so future re-verification has the full context."""
    ret = _make_return(dependents=[], tax_year=2025)
    result = compute_trump_account_deduction(
        return_=ret, magi=Decimal("123456.78")
    )

    assert result.details["magi"] == "123456.78"


def test_deduction_note_warns_about_section_219() -> None:
    """The audit trail must contain a loud warning about §219 non-deductibility
    so any reviewer inspecting `details` will see it."""
    ret = _make_return(dependents=[], tax_year=2025)
    result = compute_trump_account_deduction(return_=ret, magi=Decimal("80000"))

    note = result.details["deduction_note"]
    assert "§219" in note
    assert "NOT deductible" in note
    assert "$0" in note
