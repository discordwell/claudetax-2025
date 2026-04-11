"""Tests for Form 8829 compute — home-office deduction (simplified + regular).

Three layers under test:

* **Layer 0 (dispatcher)** — ``compute_home_office_deduction`` picks
  simplified ($5/sq ft, $1,500 cap) vs regular (Form 8829 line 36) and
  honors the gross-income cap.

* **Layer 1 (regular method fields)** — ``compute_form_8829_fields``
  walks Parts I-IV for a single home office + Schedule C. Tests cover
  business-use percentage, direct/indirect split, the gross-income
  limit, operating-expense carryovers, first-year mid-month
  depreciation, and Part IV carryforward emission.

* **Model validation** — ``HomeOffice`` rejects illegal square-footage
  configurations (negative, business > total).

* **Engine integration** — ``calc.engine.compute`` honors the
  home-office deduction via the pre-compute dispatcher hook so
  downstream SE tax and AGI reflect the correct Sch C net profit.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from skill.scripts.calc.engine import compute, schedule_c_net_profit
from skill.scripts.models import (
    CanonicalReturn,
    HomeOffice,
    ScheduleC,
)
from skill.scripts.output.form_8829 import (
    SIMPLIFIED_METHOD_CAP,
    SIMPLIFIED_METHOD_RATE,
    apply_home_office_deductions,
    compute_form_8829_fields,
    compute_home_office_deduction,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_sch_c(**overrides) -> ScheduleC:
    base = {
        "business_name": "Test Co",
        "principal_business_or_profession": "Widgets",
    }
    base.update(overrides)
    return ScheduleC.model_validate(base)


def _canonical_with_sc(sc: ScheduleC) -> CanonicalReturn:
    return CanonicalReturn.model_validate(
        {
            "schema_version": "0.1.0",
            "tax_year": 2025,
            "filing_status": "single",
            "taxpayer": {
                "first_name": "Harriet",
                "last_name": "Hom",
                "ssn": "111-22-3333",
                "date_of_birth": "1985-01-01",
                "is_blind": False,
                "is_age_65_or_older": False,
            },
            "address": {
                "street1": "1 Main",
                "city": "Austin",
                "state": "TX",
                "zip": "78701",
            },
            "schedules_c": [sc.model_dump(mode="json")],
            "itemize_deductions": False,
        }
    )


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------


def test_home_office_rejects_business_larger_than_home() -> None:
    with pytest.raises(ValueError, match="business_sq_ft cannot exceed total"):
        HomeOffice(
            method="simplified",
            business_sq_ft=Decimal("500"),
            total_home_sq_ft=Decimal("400"),
        )


def test_home_office_rejects_negative_sq_ft() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        HomeOffice(
            method="simplified",
            business_sq_ft=Decimal("-100"),
            total_home_sq_ft=Decimal("2000"),
        )


# ---------------------------------------------------------------------------
# Layer 0 — simplified method
# ---------------------------------------------------------------------------


def test_simplified_200_sq_ft_produces_1000() -> None:
    """200 sq ft x $5 = $1,000 (under the 300 sq ft cap)."""
    sc = _minimal_sch_c(line1_gross_receipts="100000.00")
    ho = HomeOffice(
        method="simplified",
        business_sq_ft=Decimal("200"),
        total_home_sq_ft=Decimal("2000"),
    )
    assert compute_home_office_deduction(ho, sc) == Decimal("1000.00")


def test_simplified_400_sq_ft_capped_at_1500() -> None:
    """400 sq ft → capped at 300 sq ft / $1,500."""
    sc = _minimal_sch_c(line1_gross_receipts="100000.00")
    ho = HomeOffice(
        method="simplified",
        business_sq_ft=Decimal("400"),
        total_home_sq_ft=Decimal("2000"),
    )
    assert compute_home_office_deduction(ho, sc) == Decimal("1500.00")


def test_simplified_exactly_300_sq_ft_produces_1500() -> None:
    sc = _minimal_sch_c(line1_gross_receipts="100000.00")
    ho = HomeOffice(
        method="simplified",
        business_sq_ft=Decimal("300"),
        total_home_sq_ft=Decimal("2000"),
    )
    assert compute_home_office_deduction(ho, sc) == Decimal("1500.00")


def test_simplified_method_respects_gross_income_limit() -> None:
    """Simplified method cannot push Sch C into a bigger loss."""
    sc = _minimal_sch_c(
        line1_gross_receipts="2000.00",
        expenses={"line22_supplies": "1800.00"},  # tentative profit = 200
    )
    ho = HomeOffice(
        method="simplified",
        business_sq_ft=Decimal("200"),
        total_home_sq_ft=Decimal("2000"),
    )
    # Would be $1,000 uncapped; tentative profit is $200, so capped there.
    assert compute_home_office_deduction(ho, sc) == Decimal("200.00")


def test_simplified_constants() -> None:
    """Sanity — the skill-level constants match the IRS TY2025 statute."""
    assert SIMPLIFIED_METHOD_RATE == Decimal("5.00")
    assert SIMPLIFIED_METHOD_CAP == Decimal("1500.00")


# ---------------------------------------------------------------------------
# Layer 1 — regular method, full year, no depreciation
# ---------------------------------------------------------------------------


def test_regular_method_full_year_task_spec_fixture() -> None:
    """The task spec: 10% business, $12k mortgage interest, $4k utilities,
    $800 insurance → $1,680 base (pre-depreciation).
    """
    sc = _minimal_sch_c(line1_gross_receipts="100000.00")
    ho = HomeOffice(
        method="regular",
        business_sq_ft=Decimal("200"),
        total_home_sq_ft=Decimal("2000"),
        mortgage_interest_total=Decimal("12000"),
        utilities_total=Decimal("4000"),
        insurance_total=Decimal("800"),
    )
    fields = compute_form_8829_fields(ho, sc)
    # Part I
    assert fields.line_3_area_percentage == Decimal("10.00")
    assert fields.line_7_business_percentage == Decimal("10.00")
    # Part II gross-income line 8 = Sch C line 29 tentative profit
    assert fields.line_8_sch_c_line_29_tentative_profit == Decimal("100000.00")
    # Line 12b = 12k (mortgage interest indirect only)
    assert fields.line_12b_sum_9_10_11_indirect == Decimal("12000.00")
    # Line 13 = 12k × 10% = 1200
    assert fields.line_13_line_12b_times_line_7 == Decimal("1200.00")
    # Line 14 = 0 direct + 1200 apportioned = 1200
    assert fields.line_14_sum_12a_plus_13 == Decimal("1200.00")
    # Line 15 = 100000 - 1200 = 98800 (remaining gross-income cap)
    assert fields.line_15_subtract_14_from_8 == Decimal("98800.00")
    # Line 23b = 4000 utilities + 800 insurance = 4800
    assert fields.line_23b_sum_16_through_22_indirect == Decimal("4800.00")
    # Line 24 = 4800 × 10% = 480
    assert fields.line_24_line_23b_times_line_7 == Decimal("480.00")
    # Line 26 = 480 apportioned + 0 direct + 0 carryover = 480
    assert fields.line_26_sum_23a_24_25 == Decimal("480.00")
    # Line 27 = min(98800, 480) = 480
    assert fields.line_27_allowable_operating_expenses == Decimal("480.00")
    # Line 34 = 1200 + 480 + 0 (no depreciation) = 1680
    assert fields.line_34_sum_14_27_33 == Decimal("1680.00")
    assert fields.line_36_allowable_expenses_to_sch_c_line_30 == Decimal("1680.00")


def test_regular_method_no_carryover_when_under_cap() -> None:
    """When operating expenses < line 15 cap, Part IV carryover = 0."""
    sc = _minimal_sch_c(line1_gross_receipts="100000.00")
    ho = HomeOffice(
        method="regular",
        business_sq_ft=Decimal("200"),
        total_home_sq_ft=Decimal("2000"),
        utilities_total=Decimal("4000"),
    )
    fields = compute_form_8829_fields(ho, sc)
    assert fields.line_43_carryover_operating_expenses == Decimal("0")
    assert fields.line_44_carryover_excess_casualty_depreciation == Decimal("0")


# ---------------------------------------------------------------------------
# Layer 1 — gross-income limitation + carryforward
# ---------------------------------------------------------------------------


def test_regular_method_gross_income_limit_creates_carryover() -> None:
    """Spec case: Sch C net $500 with $2k of potential 8829 → limited to
    $500, $1,500 carries.

    We model this as: Sch C tentative profit (line 29) = $500 after
    expenses, with $2k of home-office operating expenses. Line 15 =
    $500 (no mortgage/RE taxes), line 26 = $2000, line 27 = min(500,
    2000) = 500, line 43 carryover = 1500.
    """
    sc = _minimal_sch_c(
        line1_gross_receipts="10000.00",
        expenses={"line22_supplies": "9500.00"},  # tentative profit = 500
    )
    ho = HomeOffice(
        method="regular",
        business_sq_ft=Decimal("200"),
        total_home_sq_ft=Decimal("2000"),
        # 20000 × 10% = $2000 apportioned operating expenses
        utilities_total=Decimal("20000"),
    )
    fields = compute_form_8829_fields(ho, sc)
    assert fields.line_8_sch_c_line_29_tentative_profit == Decimal("500.00")
    assert fields.line_15_subtract_14_from_8 == Decimal("500.00")
    assert fields.line_26_sum_23a_24_25 == Decimal("2000.00")
    assert fields.line_27_allowable_operating_expenses == Decimal("500.00")
    # Deduction is capped at tentative profit.
    assert fields.line_36_allowable_expenses_to_sch_c_line_30 == Decimal("500.00")
    # Carryover to next year = 2000 - 500 = 1500
    assert fields.line_43_carryover_operating_expenses == Decimal("1500.00")


def test_regular_method_prior_year_carryover_picked_up_on_line_25() -> None:
    """Prior-year carryover is picked up on line 25 and adds into
    line 26 total operating expenses.
    """
    sc = _minimal_sch_c(line1_gross_receipts="100000.00")
    ho = HomeOffice(
        method="regular",
        business_sq_ft=Decimal("200"),
        total_home_sq_ft=Decimal("2000"),
        utilities_total=Decimal("4000"),
        prior_year_operating_carryover=Decimal("750"),
    )
    fields = compute_form_8829_fields(ho, sc)
    assert fields.line_25_carryover_prior_year_operating == Decimal("750.00")
    # Line 26 = 0 direct + 400 apportioned + 750 carry = 1150
    assert fields.line_26_sum_23a_24_25 == Decimal("1150.00")
    # Line 27 = min(100000, 1150) = 1150 (not gross-income capped)
    assert fields.line_27_allowable_operating_expenses == Decimal("1150.00")
    # Line 36 includes the carryover
    assert fields.line_36_allowable_expenses_to_sch_c_line_30 == Decimal("1150.00")


def test_regular_method_prior_year_excess_casualty_carryover_on_line_31() -> None:
    sc = _minimal_sch_c(line1_gross_receipts="100000.00")
    ho = HomeOffice(
        method="regular",
        business_sq_ft=Decimal("200"),
        total_home_sq_ft=Decimal("2000"),
        prior_year_excess_casualty_depreciation_carryover=Decimal("300"),
    )
    fields = compute_form_8829_fields(ho, sc)
    assert fields.line_31_carryover_prior_year_excess_casualty_depreciation == Decimal(
        "300.00"
    )
    assert fields.line_32_sum_29_30_31 == Decimal("300.00")


# ---------------------------------------------------------------------------
# Layer 1 — Part III depreciation
# ---------------------------------------------------------------------------


def test_regular_method_first_year_mid_month_june_depreciation() -> None:
    """Mid-year purchase: June → month 6 → 1.391% first-year percentage.

    Basis = 300,000 (home price) - 60,000 (land) = 240,000
    Business basis = 240,000 × 10% = 24,000
    Line 42 = 24,000 × 0.01391 = 333.84
    """
    sc = _minimal_sch_c(line1_gross_receipts="100000.00")
    ho = HomeOffice(
        method="regular",
        business_sq_ft=Decimal("200"),
        total_home_sq_ft=Decimal("2000"),
        home_purchase_price=Decimal("300000"),
        home_land_value=Decimal("60000"),
        home_purchase_date=date(2025, 6, 1),
    )
    fields = compute_form_8829_fields(ho, sc)
    assert fields.line_37_smaller_of_basis_or_fmv == Decimal("300000.00")
    assert fields.line_38_value_of_land == Decimal("60000.00")
    assert fields.line_39_basis_of_building == Decimal("240000.00")
    assert fields.line_40_business_basis_of_building == Decimal("24000.00")
    # June first-year = 1.391%
    assert fields.line_41_depreciation_percentage == Decimal("1.391")
    assert fields.line_42_depreciation_allowable == Decimal("333.84")
    # Line 30 on Part II copies line 42
    assert fields.line_30_depreciation_of_home == Decimal("333.84")


def test_regular_method_no_purchase_price_skips_depreciation() -> None:
    """When the filer doesn't supply home_purchase_price, Part III is
    all zeros and line 30 depreciation is $0 (operating expenses only).
    """
    sc = _minimal_sch_c(line1_gross_receipts="100000.00")
    ho = HomeOffice(
        method="regular",
        business_sq_ft=Decimal("200"),
        total_home_sq_ft=Decimal("2000"),
        utilities_total=Decimal("4000"),
    )
    fields = compute_form_8829_fields(ho, sc)
    assert fields.line_37_smaller_of_basis_or_fmv == Decimal("0")
    assert fields.line_42_depreciation_allowable == Decimal("0")
    assert fields.line_30_depreciation_of_home == Decimal("0")


def test_regular_method_january_mid_month_depreciation() -> None:
    """January first-year = 2.461% (the highest of the 12 first-year
    percentages because the half-month convention gives the most days
    of service)."""
    sc = _minimal_sch_c(line1_gross_receipts="100000.00")
    ho = HomeOffice(
        method="regular",
        business_sq_ft=Decimal("100"),
        total_home_sq_ft=Decimal("1000"),
        home_purchase_price=Decimal("400000"),
        home_land_value=Decimal("80000"),
        home_purchase_date=date(2025, 1, 15),
    )
    fields = compute_form_8829_fields(ho, sc)
    # business basis of building = (400000 - 80000) × 10% = 32000
    assert fields.line_40_business_basis_of_building == Decimal("32000.00")
    assert fields.line_41_depreciation_percentage == Decimal("2.461")
    # 32000 × 0.02461 = 787.52
    assert fields.line_42_depreciation_allowable == Decimal("787.52")


def test_regular_method_depreciation_flows_into_line_36_subject_to_cap() -> None:
    """Depreciation (line 30) + operating (line 27) are both subject to
    the line 15 / line 28 gross-income caps; the final allowable
    deduction is line 36 = line 34 - line 35.
    """
    sc = _minimal_sch_c(line1_gross_receipts="100000.00")
    ho = HomeOffice(
        method="regular",
        business_sq_ft=Decimal("200"),
        total_home_sq_ft=Decimal("2000"),
        utilities_total=Decimal("4000"),
        home_purchase_price=Decimal("300000"),
        home_land_value=Decimal("60000"),
        # January first year → 2.461%
        home_purchase_date=date(2025, 1, 1),
    )
    fields = compute_form_8829_fields(ho, sc)
    # Operating line 27 = 400
    # Line 28 = 100000 - 0 - 400 = 99600 (remaining cap)
    # Depreciation = 24000 × 0.02461 = 590.64
    # Line 33 = min(99600, 590.64) = 590.64
    # Line 34 = 0 + 400 + 590.64 = 990.64
    assert fields.line_42_depreciation_allowable == Decimal("590.64")
    assert fields.line_30_depreciation_of_home == Decimal("590.64")
    assert fields.line_27_allowable_operating_expenses == Decimal("400.00")
    assert fields.line_36_allowable_expenses_to_sch_c_line_30 == Decimal("990.64")


# ---------------------------------------------------------------------------
# Layer 0 — dispatcher routes regular → compute_form_8829_fields
# ---------------------------------------------------------------------------


def test_dispatcher_regular_method_returns_line_36() -> None:
    sc = _minimal_sch_c(line1_gross_receipts="100000.00")
    ho = HomeOffice(
        method="regular",
        business_sq_ft=Decimal("200"),
        total_home_sq_ft=Decimal("2000"),
        mortgage_interest_total=Decimal("12000"),
        utilities_total=Decimal("4000"),
        insurance_total=Decimal("800"),
    )
    fields = compute_form_8829_fields(ho, sc)
    assert (
        compute_home_office_deduction(ho, sc)
        == fields.line_36_allowable_expenses_to_sch_c_line_30
    )


def test_compute_form_8829_fields_rejects_simplified() -> None:
    sc = _minimal_sch_c(line1_gross_receipts="100000.00")
    ho = HomeOffice(
        method="simplified",
        business_sq_ft=Decimal("200"),
        total_home_sq_ft=Decimal("2000"),
    )
    with pytest.raises(ValueError, match="regular method"):
        compute_form_8829_fields(ho, sc)


# ---------------------------------------------------------------------------
# apply_home_office_deductions — in-place mutation helper
# ---------------------------------------------------------------------------


def test_apply_home_office_deductions_mutates_line_30() -> None:
    sc = _minimal_sch_c(line1_gross_receipts="100000.00")
    sc.home_office = HomeOffice(
        method="simplified",
        business_sq_ft=Decimal("200"),
        total_home_sq_ft=Decimal("2000"),
    )
    return_ = _canonical_with_sc(sc)
    assert return_.schedules_c[0].line30_home_office_expense == Decimal("0")
    apply_home_office_deductions(return_)
    assert return_.schedules_c[0].line30_home_office_expense == Decimal("1000.00")


def test_apply_home_office_deductions_idempotent() -> None:
    sc = _minimal_sch_c(line1_gross_receipts="100000.00")
    sc.home_office = HomeOffice(
        method="simplified",
        business_sq_ft=Decimal("400"),
        total_home_sq_ft=Decimal("2000"),
    )
    return_ = _canonical_with_sc(sc)
    apply_home_office_deductions(return_)
    apply_home_office_deductions(return_)
    assert return_.schedules_c[0].line30_home_office_expense == Decimal("1500.00")


def test_apply_home_office_deductions_no_op_without_home_office_block() -> None:
    """Legacy callers who set line30_home_office_expense directly (wave 5
    behavior) are not clobbered when `home_office` is None."""
    sc = _minimal_sch_c(
        line1_gross_receipts="100000.00",
        line30_home_office_expense="3000.00",
    )
    assert sc.home_office is None
    return_ = _canonical_with_sc(sc)
    apply_home_office_deductions(return_)
    assert return_.schedules_c[0].line30_home_office_expense == Decimal("3000.00")


# ---------------------------------------------------------------------------
# Engine integration — compute() honors the home-office deduction
# ---------------------------------------------------------------------------


def test_engine_compute_applies_simplified_home_office_to_net_profit() -> None:
    sc = _minimal_sch_c(
        line1_gross_receipts="100000.00",
        expenses={"line22_supplies": "10000.00"},
    )
    sc.home_office = HomeOffice(
        method="simplified",
        business_sq_ft=Decimal("200"),
        total_home_sq_ft=Decimal("2000"),
    )
    return_ = _canonical_with_sc(sc)
    computed = compute(return_)

    # line 31 after engine = 100000 - 10000 - 1000 = 89000
    assert schedule_c_net_profit(computed.schedules_c[0]) == Decimal("89000.00")
    assert computed.schedules_c[0].line30_home_office_expense == Decimal("1000.00")


def test_engine_compute_applies_regular_home_office_to_net_profit() -> None:
    sc = _minimal_sch_c(
        line1_gross_receipts="100000.00",
        expenses={"line22_supplies": "10000.00"},
    )
    sc.home_office = HomeOffice(
        method="regular",
        business_sq_ft=Decimal("200"),
        total_home_sq_ft=Decimal("2000"),
        mortgage_interest_total=Decimal("12000"),
        utilities_total=Decimal("4000"),
        insurance_total=Decimal("800"),
    )
    return_ = _canonical_with_sc(sc)
    computed = compute(return_)

    # 1680 from the fixture test
    assert computed.schedules_c[0].line30_home_office_expense == Decimal("1680.00")
    # Net profit = 100000 - 10000 - 1680 = 88320
    assert schedule_c_net_profit(computed.schedules_c[0]) == Decimal("88320.00")
