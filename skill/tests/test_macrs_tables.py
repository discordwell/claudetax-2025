"""Tests for skill.scripts.calc.macrs_tables.

Verifies the half-year convention tables for 3/5/7/10/15/20-year
property and the mid-month convention tables for 27.5-year residential
rental and 39-year nonresidential real property.

Source: IRS Publication 946 (rev. 2024), Appendix A
https://www.irs.gov/pub/irs-pdf/p946.pdf
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from skill.scripts.calc.macrs_tables import (
    MACRS_HALF_YEAR,
    MACRS_MID_MONTH,
    macrs_depreciation_percentage,
    macrs_full_depreciation,
)


class TestHalfYearTables:
    def test_three_year_sums_to_one(self) -> None:
        total = sum(MACRS_HALF_YEAR["3"], start=Decimal("0"))
        assert total == Decimal("1.0000")

    def test_five_year_sums_to_one(self) -> None:
        total = sum(MACRS_HALF_YEAR["5"], start=Decimal("0"))
        assert total == Decimal("1.0000")

    def test_seven_year_sums_to_one(self) -> None:
        total = sum(MACRS_HALF_YEAR["7"], start=Decimal("0"))
        assert total == Decimal("1.0000")

    def test_ten_year_sums_to_one(self) -> None:
        total = sum(MACRS_HALF_YEAR["10"], start=Decimal("0"))
        assert total == Decimal("1.0000")

    def test_five_year_year_one_is_twenty_percent(self) -> None:
        """Pub 946 Table A-1, 5-year column, year 1 = 20.00%."""
        assert MACRS_HALF_YEAR["5"][0] == Decimal("0.2000")

    def test_seven_year_year_one_is_fourteen_twenty_nine(self) -> None:
        """Pub 946 Table A-1, 7-year column, year 1 = 14.29%."""
        assert MACRS_HALF_YEAR["7"][0] == Decimal("0.1429")

    def test_three_year_year_one_is_thirty_three_thirty_three(self) -> None:
        """Pub 946 Table A-1, 3-year column, year 1 = 33.33%."""
        assert MACRS_HALF_YEAR["3"][0] == Decimal("0.3333")

    def test_fifteen_year_year_one_is_five_percent(self) -> None:
        """Pub 946 Table A-1, 15-year column, year 1 = 5.00%."""
        assert MACRS_HALF_YEAR["15"][0] == Decimal("0.0500")

    def test_twenty_year_year_one_is_three_seventy_five(self) -> None:
        """Pub 946 Table A-1, 20-year column, year 1 = 3.75%."""
        assert MACRS_HALF_YEAR["20"][0] == Decimal("0.0375")

    def test_five_year_recovers_over_six_years(self) -> None:
        """HY convention stretches 5-year property over 6 tax years."""
        assert len(MACRS_HALF_YEAR["5"]) == 6


class TestMidMonthTables:
    def test_27_5_year_first_month_is_3_485_percent(self) -> None:
        """Pub 946 Table A-6, month 1 column, year 1 = 3.485%."""
        assert MACRS_MID_MONTH["27.5"][0] == Decimal("0.03485")

    def test_39_year_first_month_is_2_461_percent(self) -> None:
        """Pub 946 Table A-7a, month 1 column, year 1 = 2.461%."""
        assert MACRS_MID_MONTH["39"][0] == Decimal("0.02461")

    def test_27_5_middle_years_equal_3_636(self) -> None:
        """Pub 946 Table A-6, years 2-28 are 3.636% each."""
        for i in range(1, 28):
            assert MACRS_MID_MONTH["27.5"][i] == Decimal("0.03636")

    def test_39_year_middle_years_equal_2_564(self) -> None:
        """Pub 946 Table A-7a, years 2-39 are 2.564% each."""
        for i in range(1, 39):
            assert MACRS_MID_MONTH["39"][i] == Decimal("0.02564")


class TestDepreciationPercentageLookup:
    def test_five_year_year_one(self) -> None:
        assert macrs_depreciation_percentage("5", 0) == Decimal("0.2000")

    def test_seven_year_year_one(self) -> None:
        assert macrs_depreciation_percentage("7", 0) == Decimal("0.1429")

    def test_past_end_of_schedule_returns_zero(self) -> None:
        """After full recovery, returns 0."""
        assert macrs_depreciation_percentage("5", 100) == Decimal("0")

    def test_unknown_class_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown MACRS class"):
            macrs_depreciation_percentage("foo", 0)

    def test_negative_year_raises(self) -> None:
        with pytest.raises(ValueError, match="must be >= 0"):
            macrs_depreciation_percentage("5", -1)


class TestMacrsFullDepreciation:
    def test_five_year_ten_thousand_year_one(self) -> None:
        """$10,000 5-year property, year 1 = $2,000."""
        result = macrs_full_depreciation("5", Decimal("10000"), 0)
        assert result == Decimal("2000.00")

    def test_seven_year_fifty_thousand_year_one(self) -> None:
        """$50,000 7-year property, year 1 = $7,145 (14.29% × $50,000)."""
        result = macrs_full_depreciation("7", Decimal("50000"), 0)
        assert result == Decimal("7145.00")

    def test_fractional_basis_rounds_to_cents(self) -> None:
        """Quantization to cents."""
        result = macrs_full_depreciation("5", Decimal("1234.56"), 0)
        # 1234.56 × 0.2000 = 246.912 → 246.91
        assert result == Decimal("246.91")
