"""Tests for the OBBBA Senior Deduction patch.

TY2025 / OBBBA / §63(f)(3) as enhanced by OBBBA (P.L. 119-21):
- +$6,000 per filer age 65+ (taxpayer and/or spouse on MFJ/QSS)
- Phase-out begins at MAGI of $75,000 (single/HoH/MFS) or $150,000 (MFJ/QSS)
- Phase-out rate: 6% of MAGI over threshold (i.e. $0.06 per $1)
- Fully phased out at $175k single / $250k MFJ for a one-filer base ($6k)
- For an MFJ two-filer base ($12k), fully phased out at $350k MFJ
  (base / rate + threshold = 12000/0.06 + 150000 = 350000)
- Years applicable: 2025, 2026, 2027, 2028 only — before/after = $0
- Stacks ON TOP of the regular §63(f) age-65 additional standard deduction

Phase-out rate sourced from:
  - https://taxfoundation.org/blog/obbba-senior-deduction-tax-relief/
  - https://www.hrblock.com/tax-center/irs/tax-law-and-policy/one-big-beautiful-bill-senior-tax-deduction/
  - https://welchgroup.com/tax-relief-for-seniors-a-new-deduction-available/
The 6% rate is NOT yet in skill/reference/ty2025-constants.json; it is
hardcoded in obbba_senior_deduction.py with a TODO to move into constants.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from skill.scripts.calc.patches.obbba_senior_deduction import (
    SeniorDeductionResult,
    compute_senior_deduction,
)
from skill.scripts.models import (
    Address,
    CanonicalReturn,
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


def _make_return(
    status: FilingStatus,
    taxpayer_dob: dt.date,
    spouse_dob: dt.date | None = None,
    tax_year: int = 2025,
    wages: Decimal = Decimal("60000"),
) -> CanonicalReturn:
    taxpayer = _person("Alex", "Doe", "111-11-1111", taxpayer_dob)
    spouse: Person | None = None
    if status in (FilingStatus.MFJ, FilingStatus.MFS):
        # Spouse required for these filings
        assert spouse_dob is not None, "spouse_dob required for mfj/mfs tests"
        spouse = _person("Sam", "Doe", "222-22-2222", spouse_dob)
    elif status == FilingStatus.QSS and spouse_dob is not None:
        # QSS requires a deceased spouse; we don't use QSS here, but document
        spouse = _person(
            "Sam",
            "Doe",
            "222-22-2222",
            spouse_dob,
        )
    return CanonicalReturn(
        tax_year=tax_year,
        filing_status=status,
        taxpayer=taxpayer,
        spouse=spouse,
        address=_addr(),
        w2s=[
            W2(employer_name="Acme", employer_ein="12-3456789", box1_wages=wages),
        ],
    )


# ---------------------------------------------------------------------------
# Ineligibility by age
# ---------------------------------------------------------------------------


def test_single_age_30_not_eligible() -> None:
    """Under 65 = no deduction, no matter the MAGI."""
    ret = _make_return(FilingStatus.SINGLE, taxpayer_dob=dt.date(1995, 6, 15))
    result = compute_senior_deduction(return_=ret, magi=Decimal("40000"))

    assert isinstance(result, SeniorDeductionResult)
    assert result.num_filers_age_65_plus == 0
    assert result.base_deduction == Decimal("0")
    assert result.phase_out_reduction == Decimal("0")
    assert result.deduction == Decimal("0")


# ---------------------------------------------------------------------------
# Single filer, under phase-out
# ---------------------------------------------------------------------------


def test_single_age_65_under_phaseout_full_deduction() -> None:
    """Single, age 65 at year end, MAGI $40k: full $6,000 deduction."""
    # Born 1960-06-15 -> on 2025-12-31 is age 65
    ret = _make_return(FilingStatus.SINGLE, taxpayer_dob=dt.date(1960, 6, 15))
    result = compute_senior_deduction(return_=ret, magi=Decimal("40000"))

    assert result.num_filers_age_65_plus == 1
    assert result.base_deduction == Decimal("6000")
    assert result.phase_out_reduction == Decimal("0")
    assert result.deduction == Decimal("6000")


def test_single_age_70_under_phaseout_full_deduction() -> None:
    """Single, age 70 at year end, MAGI $60k: full $6,000 deduction."""
    ret = _make_return(FilingStatus.SINGLE, taxpayer_dob=dt.date(1955, 1, 1))
    result = compute_senior_deduction(return_=ret, magi=Decimal("60000"))

    assert result.num_filers_age_65_plus == 1
    assert result.base_deduction == Decimal("6000")
    assert result.phase_out_reduction == Decimal("0")
    assert result.deduction == Decimal("6000")


def test_single_exactly_at_threshold_no_reduction() -> None:
    """MAGI exactly at $75,000 threshold: no phase-out yet."""
    ret = _make_return(FilingStatus.SINGLE, taxpayer_dob=dt.date(1955, 1, 1))
    result = compute_senior_deduction(return_=ret, magi=Decimal("75000"))

    assert result.num_filers_age_65_plus == 1
    assert result.base_deduction == Decimal("6000")
    assert result.phase_out_reduction == Decimal("0")
    assert result.deduction == Decimal("6000")


# ---------------------------------------------------------------------------
# Single filer, partial phase-out
# ---------------------------------------------------------------------------


def test_single_age_65_partial_phaseout_at_150k() -> None:
    """Single, age 65, MAGI $150k: excess = $75k, reduction = 0.06 * $75k = $4,500.
    Deduction = $6,000 - $4,500 = $1,500."""
    ret = _make_return(FilingStatus.SINGLE, taxpayer_dob=dt.date(1960, 6, 15))
    result = compute_senior_deduction(return_=ret, magi=Decimal("150000"))

    assert result.num_filers_age_65_plus == 1
    assert result.base_deduction == Decimal("6000")
    assert result.phase_out_reduction == Decimal("4500")
    assert result.deduction == Decimal("1500")


def test_single_age_65_partial_phaseout_at_100k() -> None:
    """Single, age 65, MAGI $100k: excess = $25k, reduction = 0.06 * $25k = $1,500.
    Deduction = $6,000 - $1,500 = $4,500."""
    ret = _make_return(FilingStatus.SINGLE, taxpayer_dob=dt.date(1960, 6, 15))
    result = compute_senior_deduction(return_=ret, magi=Decimal("100000"))

    assert result.num_filers_age_65_plus == 1
    assert result.base_deduction == Decimal("6000")
    assert result.phase_out_reduction == Decimal("1500")
    assert result.deduction == Decimal("4500")


# ---------------------------------------------------------------------------
# Single filer, fully phased out
# ---------------------------------------------------------------------------


def test_single_age_65_fully_phased_out_at_250k() -> None:
    """Single, age 65, MAGI $250k: excess = $175k, raw reduction = $10,500 > base.
    Clamped to base: deduction = 0."""
    ret = _make_return(FilingStatus.SINGLE, taxpayer_dob=dt.date(1960, 6, 15))
    result = compute_senior_deduction(return_=ret, magi=Decimal("250000"))

    assert result.num_filers_age_65_plus == 1
    assert result.base_deduction == Decimal("6000")
    # Reduction is clamped to base deduction (we never return negative)
    assert result.phase_out_reduction == Decimal("6000")
    assert result.deduction == Decimal("0")


def test_single_age_65_exactly_fully_phased_out_at_175k() -> None:
    """Single, $175k: excess = $100k, reduction = 0.06 * $100k = $6,000. Deduction = 0."""
    ret = _make_return(FilingStatus.SINGLE, taxpayer_dob=dt.date(1960, 6, 15))
    result = compute_senior_deduction(return_=ret, magi=Decimal("175000"))

    assert result.num_filers_age_65_plus == 1
    assert result.phase_out_reduction == Decimal("6000")
    assert result.deduction == Decimal("0")


# ---------------------------------------------------------------------------
# MFJ both age 65+
# ---------------------------------------------------------------------------


def test_mfj_both_70_under_phaseout_twelve_thousand() -> None:
    """MFJ, both age 70, MAGI $100k (< $150k threshold): full $12,000."""
    ret = _make_return(
        FilingStatus.MFJ,
        taxpayer_dob=dt.date(1955, 1, 1),
        spouse_dob=dt.date(1954, 2, 2),
    )
    result = compute_senior_deduction(return_=ret, magi=Decimal("100000"))

    assert result.num_filers_age_65_plus == 2
    assert result.base_deduction == Decimal("12000")
    assert result.phase_out_reduction == Decimal("0")
    assert result.deduction == Decimal("12000")


def test_mfj_one_qualifier_only() -> None:
    """MFJ, taxpayer 65, spouse 60, MAGI $100k (< $150k): only 1 * $6,000 = $6,000."""
    ret = _make_return(
        FilingStatus.MFJ,
        taxpayer_dob=dt.date(1960, 6, 15),
        spouse_dob=dt.date(1965, 8, 8),
    )
    result = compute_senior_deduction(return_=ret, magi=Decimal("100000"))

    assert result.num_filers_age_65_plus == 1
    assert result.base_deduction == Decimal("6000")
    assert result.phase_out_reduction == Decimal("0")
    assert result.deduction == Decimal("6000")


def test_mfj_both_70_partial_phaseout_at_200k() -> None:
    """MFJ, both 70, MAGI $200k: excess over $150k = $50k; reduction = 0.06 * $50k = $3,000.
    Base = $12,000. Deduction = $12,000 - $3,000 = $9,000."""
    ret = _make_return(
        FilingStatus.MFJ,
        taxpayer_dob=dt.date(1955, 1, 1),
        spouse_dob=dt.date(1954, 2, 2),
    )
    result = compute_senior_deduction(return_=ret, magi=Decimal("200000"))

    assert result.num_filers_age_65_plus == 2
    assert result.base_deduction == Decimal("12000")
    assert result.phase_out_reduction == Decimal("3000")
    assert result.deduction == Decimal("9000")


def test_mfj_both_70_fully_phased_out_at_400k() -> None:
    """MFJ, both 70, MAGI $400k: excess = $250k; raw reduction = $15,000 > $12k base.
    Clamped to $12k; deduction = 0."""
    ret = _make_return(
        FilingStatus.MFJ,
        taxpayer_dob=dt.date(1955, 1, 1),
        spouse_dob=dt.date(1954, 2, 2),
    )
    result = compute_senior_deduction(return_=ret, magi=Decimal("400000"))

    assert result.num_filers_age_65_plus == 2
    assert result.base_deduction == Decimal("12000")
    assert result.phase_out_reduction == Decimal("12000")
    assert result.deduction == Decimal("0")


# ---------------------------------------------------------------------------
# HoH
# ---------------------------------------------------------------------------


def test_hoh_age_65_under_phaseout() -> None:
    """HoH, age 65, MAGI $60k (< $75k): full $6,000."""
    ret = _make_return(FilingStatus.HOH, taxpayer_dob=dt.date(1960, 6, 15))
    result = compute_senior_deduction(return_=ret, magi=Decimal("60000"))

    assert result.num_filers_age_65_plus == 1
    assert result.base_deduction == Decimal("6000")
    assert result.deduction == Decimal("6000")


def test_hoh_age_65_partial_phaseout() -> None:
    """HoH uses single/HoH $75k threshold. MAGI $125k -> excess $50k -> $3,000 reduction."""
    ret = _make_return(FilingStatus.HOH, taxpayer_dob=dt.date(1960, 6, 15))
    result = compute_senior_deduction(return_=ret, magi=Decimal("125000"))

    assert result.num_filers_age_65_plus == 1
    assert result.phase_out_reduction == Decimal("3000")
    assert result.deduction == Decimal("3000")


# ---------------------------------------------------------------------------
# MFS: same phase-out threshold as single ($75k), not MFJ
# ---------------------------------------------------------------------------


def test_mfs_uses_single_threshold() -> None:
    """MFS, age 65, MAGI $100k: excess over $75k = $25k; reduction = $1,500; deduction = $4,500."""
    ret = _make_return(
        FilingStatus.MFS,
        taxpayer_dob=dt.date(1960, 6, 15),
        spouse_dob=dt.date(1985, 1, 1),
    )
    result = compute_senior_deduction(return_=ret, magi=Decimal("100000"))

    # MFS is single-filer from deduction standpoint; spouse age irrelevant
    assert result.num_filers_age_65_plus == 1
    assert result.base_deduction == Decimal("6000")
    assert result.phase_out_reduction == Decimal("1500")
    assert result.deduction == Decimal("4500")


def test_mfs_spouse_65_does_not_count() -> None:
    """On an MFS return the taxpayer cannot claim the spouse's senior bonus —
    the spouse would claim their own on a separate return."""
    ret = _make_return(
        FilingStatus.MFS,
        taxpayer_dob=dt.date(1985, 1, 1),  # under 65
        spouse_dob=dt.date(1955, 1, 1),    # 65+
    )
    result = compute_senior_deduction(return_=ret, magi=Decimal("50000"))

    assert result.num_filers_age_65_plus == 0
    assert result.deduction == Decimal("0")


# ---------------------------------------------------------------------------
# Year gating
# ---------------------------------------------------------------------------


def test_year_2024_returns_zero() -> None:
    """Before OBBBA's TY2025 effective year: no deduction."""
    ret = _make_return(
        FilingStatus.SINGLE,
        taxpayer_dob=dt.date(1955, 1, 1),
        tax_year=2024,
    )
    result = compute_senior_deduction(return_=ret, magi=Decimal("40000"))

    assert result.deduction == Decimal("0")
    # Even age is still 65+; the gate is the year, not eligibility
    assert result.num_filers_age_65_plus == 1
    assert result.base_deduction == Decimal("0")
    assert result.details.get("year_gated") is True


def test_year_2029_returns_zero_sunset() -> None:
    """After TY2028 sunset: no deduction."""
    ret = _make_return(
        FilingStatus.SINGLE,
        taxpayer_dob=dt.date(1955, 1, 1),
        tax_year=2029,
    )
    result = compute_senior_deduction(return_=ret, magi=Decimal("40000"))

    assert result.deduction == Decimal("0")
    assert result.num_filers_age_65_plus == 1
    assert result.base_deduction == Decimal("0")
    assert result.details.get("year_gated") is True


# ---------------------------------------------------------------------------
# Edge cases / age boundaries
# ---------------------------------------------------------------------------


def test_age_64_on_dec_31_not_eligible() -> None:
    """Born 1961-01-02: on 2025-12-31 they are 64 (birthday on 1/2/26)."""
    ret = _make_return(
        FilingStatus.SINGLE,
        taxpayer_dob=dt.date(1961, 1, 2),
    )
    result = compute_senior_deduction(return_=ret, magi=Decimal("40000"))

    assert result.num_filers_age_65_plus == 0
    assert result.deduction == Decimal("0")


def test_age_65_on_dec_31_eligible() -> None:
    """Born 1960-12-31: on 2025-12-31 they turn exactly 65 — qualifies."""
    ret = _make_return(
        FilingStatus.SINGLE,
        taxpayer_dob=dt.date(1960, 12, 31),
    )
    result = compute_senior_deduction(return_=ret, magi=Decimal("40000"))

    assert result.num_filers_age_65_plus == 1
    assert result.deduction == Decimal("6000")


def test_result_is_frozen_dataclass() -> None:
    """SeniorDeductionResult must be immutable."""
    ret = _make_return(FilingStatus.SINGLE, taxpayer_dob=dt.date(1960, 6, 15))
    result = compute_senior_deduction(return_=ret, magi=Decimal("40000"))
    with pytest.raises(Exception):
        result.deduction = Decimal("999")  # type: ignore[misc]


def test_details_contains_audit_trail() -> None:
    """details dict should expose intermediate values for audit."""
    ret = _make_return(FilingStatus.SINGLE, taxpayer_dob=dt.date(1960, 6, 15))
    result = compute_senior_deduction(return_=ret, magi=Decimal("100000"))

    assert "filing_status" in result.details
    assert "threshold" in result.details
    assert "phase_out_rate" in result.details
    assert "magi" in result.details
    assert "tax_year" in result.details
    assert result.details["filing_status"] == "single"
    assert Decimal(str(result.details["threshold"])) == Decimal("75000")
