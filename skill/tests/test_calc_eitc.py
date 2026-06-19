"""Tests for the EITC patch (skill/scripts/calc/patches/eitc.py).

Sources for expected values:
- Rev. Proc. 2024-40 (IRS inflation adjustments for 2025)
- Tax Policy Center EITC Parameters table (2025), sourced from Rev. Proc. 2024-40
  https://taxpolicycenter.org/sites/default/files/2025-04/eitc_parameters.pdf
- IRS EITC tables:
  https://www.irs.gov/credits-deductions/individuals/earned-income-tax-credit/earned-income-and-earned-income-tax-credit-eitc-tables

TY2025 parameters (from Rev. Proc. 2024-40 via Tax Policy Center):

  Kids | Credit rate | Max earnings | Max credit | Phaseout rate | PO begin (other) | PO end (other) | PO begin (MFJ) | PO end (MFJ)
  0    | 7.65%       | $8,490       | $649       | 7.65%         | $10,620          | $19,104        | $17,730        | $26,214
  1    | 34%         | $12,730      | $4,328     | 15.98%        | $23,350          | $50,434        | $30,470        | $57,554
  2    | 40%         | $17,880      | $7,152     | 21.06%        | $23,350          | $57,310        | $30,470        | $64,430
  3+   | 45%         | $17,880      | $8,046     | 21.06%        | $23,350          | $61,555        | $30,470        | $68,675

Investment income disqualifier: $11,950.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from skill.scripts.calc.patches.eitc import EITCResult, compute_eitc
from skill.scripts.models import (
    Address,
    CanonicalReturn,
    Dependent,
    DependentRelationship,
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


def _child(idx: int, age_years: int = 8) -> Dependent:
    # Child born `age_years` years before the TY2025 return
    dob = dt.date(2025 - age_years, 3, 10)
    ssn = f"111-22-{3000 + idx:04d}"
    return Dependent(
        person=Person(
            first_name=f"Kid{idx}",
            last_name="Taxpayer",
            ssn=ssn,
            date_of_birth=dob,
        ),
        relationship=DependentRelationship.SON,
        months_lived_with_taxpayer=12,
        is_qualifying_child=True,
        is_qualifying_relative=False,
    )


def _return(
    *,
    status: FilingStatus = FilingStatus.SINGLE,
    num_children: int = 0,
    with_spouse: bool = False,
) -> CanonicalReturn:
    dependents = [_child(i) for i in range(num_children)]
    spouse: Person | None = None
    if with_spouse or status in (FilingStatus.MFJ, FilingStatus.MFS):
        spouse = _person(first="Alex", ssn="987-65-4321", dob=dt.date(1986, 1, 1))
    return CanonicalReturn(
        tax_year=2025,
        filing_status=status,
        taxpayer=_person(),
        spouse=spouse,
        address=_addr(),
        dependents=dependents,
    )


# ---------------------------------------------------------------------------
# Disqualification cases
# ---------------------------------------------------------------------------


class TestDisqualification:
    def test_mfs_is_disqualified(self):
        """MFS filers generally cannot claim EITC (pre-TCJA rule; special post-2020
        cases exist but are not handled in v1)."""
        ret = _return(status=FilingStatus.MFS, num_children=2)
        result = compute_eitc(
            ret,
            agi=Decimal("25000"),
            earned_income=Decimal("25000"),
            investment_income=Decimal("0"),
        )
        assert result.eitc == Decimal("0")
        assert result.disqualified is True
        assert result.disqualification_reason is not None
        assert "mfs" in result.disqualification_reason.lower()

    def test_investment_income_over_limit_disqualifies(self):
        """Investment income > $11,950 → no EITC."""
        ret = _return(status=FilingStatus.SINGLE, num_children=1)
        result = compute_eitc(
            ret,
            agi=Decimal("20000"),
            earned_income=Decimal("20000"),
            investment_income=Decimal("12000"),
        )
        assert result.eitc == Decimal("0")
        assert result.disqualified is True
        assert result.disqualification_reason is not None
        assert "investment" in result.disqualification_reason.lower()

    def test_investment_income_at_limit_is_ok(self):
        """Investment income exactly at $11,950 is NOT disqualifying (> test)."""
        ret = _return(status=FilingStatus.SINGLE, num_children=0)
        result = compute_eitc(
            ret,
            agi=Decimal("8490"),
            earned_income=Decimal("8490"),
            investment_income=Decimal("11950"),
        )
        assert result.disqualified is False
        assert result.eitc > Decimal("0")


# ---------------------------------------------------------------------------
# Happy path: plateau values
# ---------------------------------------------------------------------------


class TestPlateauCredits:
    """At the 'earned income for max credit' threshold (end of phase-in, start of
    plateau), the credit should equal the max credit exactly, provided AGI also sits
    on the plateau (i.e., below the phase-out begin point)."""

    def test_zero_earned_income_gives_zero(self):
        ret = _return(status=FilingStatus.SINGLE, num_children=2)
        result = compute_eitc(
            ret,
            agi=Decimal("0"),
            earned_income=Decimal("0"),
            investment_income=Decimal("0"),
        )
        assert result.eitc == Decimal("0")
        assert result.disqualified is False

    def test_zero_kids_plateau(self):
        """0 kids, $8,490 earnings → $649."""
        ret = _return(status=FilingStatus.SINGLE, num_children=0)
        result = compute_eitc(
            ret,
            agi=Decimal("8490"),
            earned_income=Decimal("8490"),
            investment_income=Decimal("0"),
        )
        assert result.eitc == pytest.approx(Decimal("649"), abs=Decimal("2"))
        assert result.qualifying_children == 0

    def test_one_kid_plateau(self):
        """1 kid, $12,730 earnings → $4,328."""
        ret = _return(status=FilingStatus.SINGLE, num_children=1)
        result = compute_eitc(
            ret,
            agi=Decimal("12730"),
            earned_income=Decimal("12730"),
            investment_income=Decimal("0"),
        )
        assert result.eitc == pytest.approx(Decimal("4328"), abs=Decimal("2"))
        assert result.qualifying_children == 1

    def test_two_kids_plateau(self):
        """2 kids, $17,880 earnings → $7,152."""
        ret = _return(status=FilingStatus.SINGLE, num_children=2)
        result = compute_eitc(
            ret,
            agi=Decimal("17880"),
            earned_income=Decimal("17880"),
            investment_income=Decimal("0"),
        )
        assert result.eitc == pytest.approx(Decimal("7152"), abs=Decimal("2"))
        assert result.qualifying_children == 2

    def test_three_kids_plateau(self):
        """3 kids, $17,880 earnings → $8,046."""
        ret = _return(status=FilingStatus.SINGLE, num_children=3)
        result = compute_eitc(
            ret,
            agi=Decimal("17880"),
            earned_income=Decimal("17880"),
            investment_income=Decimal("0"),
        )
        assert result.eitc == pytest.approx(Decimal("8046"), abs=Decimal("2"))
        assert result.qualifying_children == 3

    def test_four_kids_uses_three_plus_bracket(self):
        """4+ kids still tops out at the 3+ max ($8,046)."""
        ret = _return(status=FilingStatus.SINGLE, num_children=4)
        result = compute_eitc(
            ret,
            agi=Decimal("17880"),
            earned_income=Decimal("17880"),
            investment_income=Decimal("0"),
        )
        assert result.eitc == pytest.approx(Decimal("8046"), abs=Decimal("2"))
        assert result.qualifying_children == 4


# ---------------------------------------------------------------------------
# Phase-in slope
# ---------------------------------------------------------------------------


class TestPhaseIn:
    @pytest.mark.parametrize(
        "kids,half_earnings,half_max",
        [
            (0, Decimal("4245"), Decimal("324.5")),  # $8,490 / 2, $649 / 2
            (1, Decimal("6365"), Decimal("2164")),  # $12,730 / 2, $4,328 / 2
            (2, Decimal("8940"), Decimal("3576")),  # $17,880 / 2, $7,152 / 2
            (3, Decimal("8940"), Decimal("4023")),  # $17,880 / 2, $8,046 / 2
        ],
    )
    def test_half_earnings_gives_half_max(self, kids, half_earnings, half_max):
        """Along the phase-in ramp, credit is linear in earned income.
        Half of the max-earnings threshold should give half the max credit
        (within rounding tolerance)."""
        ret = _return(status=FilingStatus.SINGLE, num_children=kids)
        result = compute_eitc(
            ret,
            agi=half_earnings,
            earned_income=half_earnings,
            investment_income=Decimal("0"),
        )
        assert result.eitc == pytest.approx(half_max, abs=Decimal("2"))


# ---------------------------------------------------------------------------
# Phase-out and AGI limit
# ---------------------------------------------------------------------------


class TestPhaseOut:
    def test_agi_above_disqualifier_zero_kids(self):
        """0 kids, AGI past the completed-phaseout threshold → $0."""
        ret = _return(status=FilingStatus.SINGLE, num_children=0)
        result = compute_eitc(
            ret,
            agi=Decimal("25000"),
            earned_income=Decimal("25000"),
            investment_income=Decimal("0"),
        )
        assert result.eitc == Decimal("0")

    def test_agi_above_disqualifier_three_kids(self):
        ret = _return(status=FilingStatus.SINGLE, num_children=3)
        result = compute_eitc(
            ret,
            agi=Decimal("65000"),
            earned_income=Decimal("65000"),
            investment_income=Decimal("0"),
        )
        assert result.eitc == Decimal("0")

    def test_zero_kids_mid_phaseout_reduces_credit(self):
        """0 kids, single, AGI between $10,620 (phaseout begin) and $19,104 (end).
        At AGI = $15,000, reduction = ($15,000 - $10,620) × 7.65% ≈ $335.
        Credit ≈ $649 − $335 = $314."""
        ret = _return(status=FilingStatus.SINGLE, num_children=0)
        result = compute_eitc(
            ret,
            agi=Decimal("15000"),
            earned_income=Decimal("15000"),
            investment_income=Decimal("0"),
        )
        # Phase-in already at max ($8,490 plateau), so only phase-out applies
        expected = Decimal("649") - (Decimal("15000") - Decimal("10620")) * Decimal("0.0765")
        assert result.eitc == pytest.approx(expected, abs=Decimal("2"))
        assert result.eitc > Decimal("0")
        assert result.eitc < Decimal("649")

    def test_one_kid_mid_phaseout_reduces_credit(self):
        """1 kid, single. AGI = $35,000. Phaseout rate 15.98%, begin $23,350.
        Reduction = ($35,000 − $23,350) × 0.1598 ≈ $1,861.67.
        Credit ≈ $4,328 − $1,862 ≈ $2,466."""
        ret = _return(status=FilingStatus.SINGLE, num_children=1)
        result = compute_eitc(
            ret,
            agi=Decimal("35000"),
            earned_income=Decimal("35000"),
            investment_income=Decimal("0"),
        )
        expected = Decimal("4328") - (Decimal("35000") - Decimal("23350")) * Decimal("0.1598")
        assert result.eitc == pytest.approx(expected, abs=Decimal("2"))

    def test_mfj_uses_higher_phaseout_begin_than_single(self):
        """For 0 kids at AGI = $16,000:
        - Single: phaseout begin = $10,620 → credit is reduced
        - MFJ:    phaseout begin = $17,730 → credit still at max ($649)
        MFJ should get strictly more credit than Single at this AGI."""
        single_ret = _return(status=FilingStatus.SINGLE, num_children=0)
        mfj_ret = _return(status=FilingStatus.MFJ, num_children=0)
        single_result = compute_eitc(
            single_ret,
            agi=Decimal("16000"),
            earned_income=Decimal("16000"),
            investment_income=Decimal("0"),
        )
        mfj_result = compute_eitc(
            mfj_ret,
            agi=Decimal("16000"),
            earned_income=Decimal("16000"),
            investment_income=Decimal("0"),
        )
        assert mfj_result.eitc > single_result.eitc
        # MFJ phaseout begin = $17,730 > $16,000, so no phase-out reduction for MFJ
        assert mfj_result.eitc == pytest.approx(Decimal("649"), abs=Decimal("2"))

    def test_phase_determinant_uses_max_of_earnings_and_agi(self):
        """Phase-out is driven by the LARGER of earned income and AGI. Construct a
        case where earned income alone would not trigger phase-out but AGI does.

        Single, 1 kid:
        - Earned income = $23,000 (below $23,350 phaseout begin → max credit if
          earned income alone drove the phase-out)
        - AGI = $40,000 (above phaseout begin → should trigger reduction)
        Expected reduction = ($40,000 − $23,350) × 0.1598 ≈ $2,660.67.
        Expected credit ≈ $4,328 − $2,661 ≈ $1,667.
        """
        ret = _return(status=FilingStatus.SINGLE, num_children=1)
        result = compute_eitc(
            ret,
            agi=Decimal("40000"),
            earned_income=Decimal("23000"),
            investment_income=Decimal("0"),
        )
        # Phase-in on earned income: 23,000 * 0.34 = 7,820, capped at 4,328 max
        # Phase-out driven by max(23,000, 40,000) = 40,000
        expected = Decimal("4328") - (Decimal("40000") - Decimal("23350")) * Decimal("0.1598")
        assert result.eitc == pytest.approx(expected, abs=Decimal("2"))
        assert result.phase_determinant == Decimal("40000")

        # Sanity: if AGI equaled earned income at $23,000, credit would be max ($4,328)
        ret_no_po = _return(status=FilingStatus.SINGLE, num_children=1)
        result_no_po = compute_eitc(
            ret_no_po,
            agi=Decimal("23000"),
            earned_income=Decimal("23000"),
            investment_income=Decimal("0"),
        )
        # At $23,000 earned, phase-in = 23,000 * 0.34 = 7,820 capped to 4,328 → still at max
        assert result_no_po.eitc == pytest.approx(Decimal("4328"), abs=Decimal("2"))


# ---------------------------------------------------------------------------
# Phase-in ramp combined with a high AGI (IRS Pub. 596 Worksheet A)
# ---------------------------------------------------------------------------


class TestPhaseInRampWithHighAGI:
    """The EITC is the SMALLER of the table amount keyed on EARNED INCOME and the
    table amount keyed on AGI (Pub. 596 Worksheet A, lines 1-6). The phase-out
    reduction applies to the *max (plateau) credit*, never to a still-phasing-in
    amount.

    The earlier code computed ``min(earned*rate, max) - agi_phase_out_reduction``
    — subtracting the AGI-based phase-out from the earned-income phase-in figure.
    That double-penalized any filer simultaneously on the phase-in ramp (low
    earned income) and past the phase-out threshold on AGI, e.g. a filer with
    modest wages plus a pension / IRA / unemployment distribution that lifts AGI
    without being "earned" or EITC investment income. Every prior test used
    ``earned == AGI`` (or earned already at the plateau), where the two formulas
    coincide, so the bug went undetected. These cases all FAIL against the old
    formula.
    """

    def test_one_kid_phase_in_earned_with_high_agi(self):
        """1 kid: earned $5,000 (phase-in, 5,000 × 0.34 = $1,700 < $4,328 max),
        AGI $30,000 (past the $23,350 phase-out begin).
        table(earned) = $1,700 ; table(AGI) = 4,328 − (30,000 − 23,350) × 0.1598
        = $3,265. Credit = min($1,700, $3,265) = $1,700.
        The buggy formula gave $1,700 − $1,063 = $637."""
        ret = _return(status=FilingStatus.SINGLE, num_children=1)
        result = compute_eitc(
            ret,
            agi=Decimal("30000"),
            earned_income=Decimal("5000"),
            investment_income=Decimal("0"),
        )
        assert result.eitc == Decimal("1700")
        # Guard against the specific regression: the old result was $637.
        assert result.eitc != Decimal("637")

    def test_zero_kids_phase_in_high_agi_not_driven_negative(self):
        """0 kids: earned $3,000 (phase-in, 3,000 × 0.0765 = $229.5), AGI $14,000
        (past $10,620 begin). table(earned) = $230 ; table(AGI) = 649 − (14,000 −
        10,620) × 0.0765 = $390. Credit = min($230, $390) = $230.
        The buggy formula gave 229.5 − 258.57 < 0 → clamped to $0 (the whole
        refundable credit was wiped out)."""
        ret = _return(status=FilingStatus.SINGLE, num_children=0)
        result = compute_eitc(
            ret,
            agi=Decimal("14000"),
            earned_income=Decimal("3000"),
            investment_income=Decimal("0"),
        )
        assert result.eitc == Decimal("230")
        assert result.eitc > Decimal("0")

    def test_two_kids_phase_in_high_agi(self):
        """2 kids: earned $8,000 (phase-in, 8,000 × 0.40 = $3,200 < $7,152 max),
        AGI $30,000. table(earned) = $3,200 is the smaller of the two lookups, so
        the credit is $3,200. The buggy formula gave $3,200 − $1,400 = $1,800."""
        ret = _return(status=FilingStatus.SINGLE, num_children=2)
        result = compute_eitc(
            ret,
            agi=Decimal("30000"),
            earned_income=Decimal("8000"),
            investment_income=Decimal("0"),
        )
        assert result.eitc == pytest.approx(Decimal("3200"), abs=Decimal("2"))

    def test_agi_below_earned_uses_smaller_agi_lookup(self):
        """The reverse case: large above-the-line adjustments push AGI ($5,000)
        BELOW earned income ($30,000, at the plateau). Worksheet A still takes the
        smaller lookup: table(earned) = $4,328 (plateau), table(AGI) = 5,000 ×
        0.34 = $1,700. Credit = min($4,328, $1,700) = $1,700."""
        ret = _return(status=FilingStatus.SINGLE, num_children=1)
        result = compute_eitc(
            ret,
            agi=Decimal("5000"),
            earned_income=Decimal("30000"),
            investment_income=Decimal("0"),
        )
        assert result.eitc == Decimal("1700")

    def test_both_earned_and_agi_on_phase_in_ramp(self):
        """The subtlest divergence: BOTH earned income and AGI sit on the phase-in
        ramp, with AGI below earned (adjustments-heavy filer). 1 kid: earned
        $10,000 (10,000 × 0.34 = $3,400), AGI $6,000 (6,000 × 0.34 = $2,040, the
        smaller). Credit = min($3,400, $2,040) = $2,040. The buggy formula keyed
        the phase-out off max(earned, AGI) = $10,000 (still below the $23,350
        phase-out begin → no reduction) and returned the earned-income figure
        $3,400 — overstating the credit by $1,360."""
        ret = _return(status=FilingStatus.SINGLE, num_children=1)
        result = compute_eitc(
            ret,
            agi=Decimal("6000"),
            earned_income=Decimal("10000"),
            investment_income=Decimal("0"),
        )
        assert result.eitc == Decimal("2040")


# ---------------------------------------------------------------------------
# EITCResult shape
# ---------------------------------------------------------------------------


class TestResultShape:
    def test_result_type_and_fields(self):
        ret = _return(status=FilingStatus.SINGLE, num_children=1)
        result = compute_eitc(
            ret,
            agi=Decimal("12730"),
            earned_income=Decimal("12730"),
            investment_income=Decimal("0"),
        )
        assert isinstance(result, EITCResult)
        assert isinstance(result.eitc, Decimal)
        assert result.qualifying_children == 1
        assert result.earned_income == Decimal("12730")
        assert result.agi == Decimal("12730")
        assert result.phase_determinant == Decimal("12730")
        assert result.disqualified is False
        assert result.disqualification_reason is None
        assert isinstance(result.details, dict)
        assert "phase_in_rate" in result.details
        assert "phase_out_rate" in result.details
        assert "phase_out_begin" in result.details
        assert "max_credit" in result.details

    def test_result_is_nonnegative_and_clamped_to_max(self):
        ret = _return(status=FilingStatus.MFJ, num_children=2)
        result = compute_eitc(
            ret,
            agi=Decimal("20000"),
            earned_income=Decimal("20000"),
            investment_income=Decimal("0"),
        )
        assert Decimal("0") <= result.eitc <= Decimal("7152") + Decimal("2")
