"""Tests for ``skill.scripts.states._hand_rolled_base``.

Locks the shared helpers that wave-5 hand-rolled state plugins will
import. Existing wave-3/wave-4 plugins are NOT refactored to use these;
they already have their own (equivalent) implementations.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

import datetime as dt

from skill.scripts.models import (
    Address,
    CanonicalReturn,
    FilingStatus,
    Person,
    ScheduleC,
    W2,
    W2StateRow,
)
from skill.scripts.states._hand_rolled_base import (
    CENT,
    GraduatedBracket,
    cents,
    d,
    day_prorate,
    graduated_tax,
    sourced_or_prorated_schedule_c,
    sourced_or_prorated_wages,
    state_has_w2_state_rows,
    state_source_schedule_c,
    state_source_wages_from_w2s,
)


# ---------------------------------------------------------------------------
# d() — Decimal coercion
# ---------------------------------------------------------------------------


class TestD:
    def test_none_becomes_zero(self):
        assert d(None) == Decimal("0")

    def test_decimal_passes_through(self):
        v = Decimal("123.45")
        assert d(v) is v

    def test_int_becomes_decimal(self):
        assert d(100) == Decimal("100")

    def test_str_becomes_decimal(self):
        assert d("65000.00") == Decimal("65000.00")

    def test_float_routed_via_str_not_direct_decimal(self):
        """Never call Decimal(float) directly — binary-float noise leaks."""
        v = d(1.1)
        # Decimal("1.1") is exact; Decimal(1.1) is 1.1000000000000000888...
        assert v == Decimal("1.1")

    def test_zero_coerces_to_decimal_zero(self):
        assert d(0) == Decimal("0")
        assert d("0") == Decimal("0")

    def test_negative_preserved(self):
        assert d("-500") == Decimal("-500")


# ---------------------------------------------------------------------------
# cents() — round to 2 decimals, half-up
# ---------------------------------------------------------------------------


class TestCents:
    def test_already_cents_passes(self):
        assert cents(Decimal("123.45")) == Decimal("123.45")

    def test_rounds_half_up(self):
        assert cents(Decimal("0.005")) == Decimal("0.01")
        assert cents(Decimal("0.004")) == Decimal("0.00")

    def test_rounds_half_up_for_negative(self):
        # -0.005 half-up rounds away from zero by Decimal convention
        assert cents(Decimal("-0.005")) == Decimal("-0.01")

    def test_truncates_long_fractional(self):
        assert cents(Decimal("100.12345")) == Decimal("100.12")

    def test_none_becomes_zero_cents(self):
        assert cents(None) == Decimal("0.00")

    def test_int_coerces_with_trailing_zero_cents(self):
        assert cents(500) == Decimal("500.00")

    def test_uses_same_quantizer_as_module_cent(self):
        """Locks that CENT is exposed and consistent with cents()."""
        assert CENT == Decimal("0.01")
        assert cents(Decimal("99.999")).as_tuple().exponent == CENT.as_tuple().exponent


# ---------------------------------------------------------------------------
# day_prorate()
# ---------------------------------------------------------------------------


class TestDayProrate:
    def test_full_year_resident_returns_cents_of_amount(self):
        """days_in_state == total_days short-circuits to avoid
        Decimal division noise."""
        assert day_prorate(Decimal("65000"), days_in_state=365) == Decimal("65000.00")

    def test_full_year_leap_year(self):
        """366 days pass-through when total_days == 366."""
        assert day_prorate(Decimal("65000"), days_in_state=366, total_days=366) == Decimal("65000.00")

    def test_zero_days_returns_zero(self):
        assert day_prorate(Decimal("100000"), days_in_state=0) == Decimal("0.00")

    def test_negative_days_returns_zero(self):
        assert day_prorate(Decimal("100000"), days_in_state=-5) == Decimal("0.00")

    def test_half_year_prorates_half(self):
        """183 days on 365 ≈ 0.5014 — locks exact Decimal quantization."""
        result = day_prorate(Decimal("100000"), days_in_state=183)
        # 100000 * 183/365 = 50136.986...
        assert result == Decimal("50136.99")

    def test_quarter_year(self):
        result = day_prorate(Decimal("120000"), days_in_state=90)
        # 120000 * 90/365 = 29589.041...
        assert result == Decimal("29589.04")

    def test_accepts_int_amount(self):
        """int amounts are coerced via d()."""
        assert day_prorate(60000, days_in_state=365) == Decimal("60000.00")

    def test_days_exceed_total_days_treated_as_full_year(self):
        """days > total_days shouldn't over-prorate."""
        assert day_prorate(Decimal("50000"), days_in_state=500) == Decimal("50000.00")

    def test_custom_total_days_leap_year_mid_year(self):
        result = day_prorate(Decimal("100000"), days_in_state=183, total_days=366)
        # 100000 * 183/366 = 50000.00
        assert result == Decimal("50000.00")


# ---------------------------------------------------------------------------
# GraduatedBracket primitives
# ---------------------------------------------------------------------------


class TestGraduatedBracket:
    def test_applies_to_below_low_returns_false(self):
        b = GraduatedBracket(Decimal("10000"), Decimal("50000"), Decimal("0.04"))
        assert b.applies_to(Decimal("5000")) is False
        assert b.applies_to(Decimal("10000")) is False  # exactly at low — excluded

    def test_applies_to_above_low_returns_true(self):
        b = GraduatedBracket(Decimal("10000"), Decimal("50000"), Decimal("0.04"))
        assert b.applies_to(Decimal("10000.01")) is True

    def test_tier_amount_below_low(self):
        b = GraduatedBracket(Decimal("10000"), Decimal("50000"), Decimal("0.04"))
        assert b.tier_amount(Decimal("5000")) == Decimal("0")

    def test_tier_amount_mid_bracket(self):
        b = GraduatedBracket(Decimal("10000"), Decimal("50000"), Decimal("0.04"))
        assert b.tier_amount(Decimal("25000")) == Decimal("15000")

    def test_tier_amount_above_high(self):
        b = GraduatedBracket(Decimal("10000"), Decimal("50000"), Decimal("0.04"))
        assert b.tier_amount(Decimal("65000")) == Decimal("40000")

    def test_tier_amount_unbounded_top_bracket(self):
        b = GraduatedBracket(Decimal("100000"), None, Decimal("0.10"))
        assert b.tier_amount(Decimal("500000")) == Decimal("400000")

    def test_frozen_dataclass_immutable(self):
        b = GraduatedBracket(Decimal("0"), Decimal("10000"), Decimal("0.02"))
        with pytest.raises(AttributeError):
            b.rate = Decimal("0.05")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# graduated_tax() — sum-of-tiers
# ---------------------------------------------------------------------------


# Example brackets: MN TY2025 Single (truncated to first 3 tiers for tests)
_MN_SINGLE = (
    GraduatedBracket(Decimal("0"),      Decimal("32570"),  Decimal("0.0535")),
    GraduatedBracket(Decimal("32570"),  Decimal("106990"), Decimal("0.0680")),
    GraduatedBracket(Decimal("106990"), Decimal("198630"), Decimal("0.0785")),
    GraduatedBracket(Decimal("198630"), None,              Decimal("0.0985")),
)


class TestGraduatedTax:
    def test_zero_income_returns_zero(self):
        assert graduated_tax(Decimal("0"), _MN_SINGLE) == Decimal("0.00")

    def test_negative_income_returns_zero(self):
        assert graduated_tax(Decimal("-5000"), _MN_SINGLE) == Decimal("0.00")

    def test_fully_in_first_bracket(self):
        """$20,000 → all in 5.35% tier → $1,070.00."""
        assert graduated_tax(Decimal("20000"), _MN_SINGLE) == Decimal("1070.00")

    def test_exactly_at_first_bracket_high(self):
        """$32,570 → all in 5.35% tier → $1,742.495 → $1,742.50."""
        # 32570 * 0.0535 = 1742.495 → half-up → 1742.50
        assert graduated_tax(Decimal("32570"), _MN_SINGLE) == Decimal("1742.50")

    def test_spans_two_brackets_mn_65k(self):
        """$65,000 → MN Single $50,050 TI case from wave 4.

        Exactly replicates the wave-4 MN plugin's hand-rolled number for
        a $65k AGI with $14,950 std ded → $50,050 TI:
        - 32,570 * 5.35% = 1742.495
        - (50,050 - 32,570) * 6.80% = 17,480 * 0.068 = 1188.64
        - total = 2931.135 → 2931.14
        """
        # Note: this test uses $50,050 directly as taxable income, not
        # the AGI. wave-4 mn.py derives TI = AGI - std_ded - exemptions.
        assert graduated_tax(Decimal("50050"), _MN_SINGLE) == Decimal("2931.14")

    def test_spans_all_four_brackets_250k(self):
        """$250,000 TI spans all four brackets.

        32570 * 5.35% = 1742.495
        (106990 - 32570) * 6.80% = 74420 * 0.068 = 5060.56
        (198630 - 106990) * 7.85% = 91640 * 0.0785 = 7193.74
        (250000 - 198630) * 9.85% = 51370 * 0.0985 = 5059.945
        total = 19056.74
        """
        assert graduated_tax(Decimal("250000"), _MN_SINGLE) == Decimal("19056.74")

    def test_round_each_tier_ct_style(self):
        """round_each_tier quantizes each tier before summing — CT
        TCS Table B convention. Tests locks the flag works at all.

        With $20,000 (single-tier), there's no difference between
        rounded and unrounded — this just exercises the flag path.
        """
        assert (
            graduated_tax(Decimal("20000"), _MN_SINGLE, round_each_tier=True)
            == Decimal("1070.00")
        )

    def test_top_bracket_unbounded(self):
        """Top bracket with high=None includes all excess over low."""
        brackets = (
            GraduatedBracket(Decimal("0"),     Decimal("10000"), Decimal("0.02")),
            GraduatedBracket(Decimal("10000"), None,             Decimal("0.05")),
        )
        # 10000 * 0.02 + 90000 * 0.05 = 200 + 4500 = 4700
        assert graduated_tax(Decimal("100000"), brackets) == Decimal("4700.00")


# ---------------------------------------------------------------------------
# Wave 6 — W-2 state-row sourcing helpers
# ---------------------------------------------------------------------------


def _return_with_w2s(w2s: list[W2]) -> CanonicalReturn:
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=Person(
            first_name="Alex",
            last_name="Doe",
            ssn="111-22-3333",
            date_of_birth=dt.date(1990, 1, 1),
        ),
        address=Address(
            street1="1 A", city="B", state="CA", zip="90001"
        ),
        w2s=w2s,
    )


class TestStateHasW2StateRows:
    def test_no_w2s(self):
        r = _return_with_w2s([])
        assert state_has_w2_state_rows(r, "CA") is False

    def test_no_rows_on_w2(self):
        r = _return_with_w2s([
            W2(employer_name="Acme", box1_wages=Decimal("50000")),
        ])
        assert state_has_w2_state_rows(r, "CA") is False

    def test_row_present_matching(self):
        r = _return_with_w2s([
            W2(
                employer_name="Acme",
                box1_wages=Decimal("50000"),
                state_rows=[
                    W2StateRow(state="CA", state_wages=Decimal("50000")),
                ],
            ),
        ])
        assert state_has_w2_state_rows(r, "CA") is True
        assert state_has_w2_state_rows(r, "NY") is False

    def test_multi_row_multi_state(self):
        r = _return_with_w2s([
            W2(
                employer_name="Acme",
                box1_wages=Decimal("50000"),
                state_rows=[
                    W2StateRow(state="CA", state_wages=Decimal("20000")),
                    W2StateRow(state="NY", state_wages=Decimal("30000")),
                ],
            ),
        ])
        assert state_has_w2_state_rows(r, "CA")
        assert state_has_w2_state_rows(r, "NY")
        assert not state_has_w2_state_rows(r, "PA")


class TestStateSourceWagesFromW2s:
    def test_single_row(self):
        r = _return_with_w2s([
            W2(
                employer_name="Acme",
                box1_wages=Decimal("50000"),
                state_rows=[
                    W2StateRow(state="CA", state_wages=Decimal("50000")),
                ],
            ),
        ])
        assert state_source_wages_from_w2s(r, "CA") == Decimal("50000.00")
        assert state_source_wages_from_w2s(r, "NY") == Decimal("0.00")

    def test_sum_across_multiple_w2s(self):
        r = _return_with_w2s([
            W2(
                employer_name="Acme",
                box1_wages=Decimal("50000"),
                state_rows=[
                    W2StateRow(state="CA", state_wages=Decimal("50000")),
                ],
            ),
            W2(
                employer_name="Omega",
                box1_wages=Decimal("30000"),
                state_rows=[
                    W2StateRow(state="CA", state_wages=Decimal("20000")),
                    W2StateRow(state="NY", state_wages=Decimal("10000")),
                ],
            ),
        ])
        assert state_source_wages_from_w2s(r, "CA") == Decimal("70000.00")
        assert state_source_wages_from_w2s(r, "NY") == Decimal("10000.00")

    def test_empty_returns_zero(self):
        r = _return_with_w2s([])
        assert state_source_wages_from_w2s(r, "CA") == Decimal("0.00")


class TestSourcedOrProratedWages:
    def test_state_rows_take_precedence(self):
        r = _return_with_w2s([
            W2(
                employer_name="Acme",
                box1_wages=Decimal("60000"),
                state_rows=[
                    W2StateRow(state="CA", state_wages=Decimal("40000")),
                ],
            ),
        ])
        # Even though full-year wages are $60k and only 182 days,
        # state_row sum wins.
        assert sourced_or_prorated_wages(
            r, "CA", Decimal("60000"), 182
        ) == Decimal("40000.00")

    def test_fallback_to_day_prorate(self):
        r = _return_with_w2s([
            W2(employer_name="Acme", box1_wages=Decimal("60000")),
        ])
        expected = day_prorate(Decimal("60000"), 182)
        assert sourced_or_prorated_wages(
            r, "CA", Decimal("60000"), 182
        ) == expected

    def test_different_state_fallback(self):
        r = _return_with_w2s([
            W2(
                employer_name="Acme",
                box1_wages=Decimal("60000"),
                state_rows=[
                    W2StateRow(state="NY", state_wages=Decimal("40000")),
                ],
            ),
        ])
        # Asking for CA when only NY rows exist falls back to
        # day-prorate of full-year wages.
        expected = day_prorate(Decimal("60000"), 182)
        assert sourced_or_prorated_wages(
            r, "CA", Decimal("60000"), 182
        ) == expected


class TestStateSourceScheduleC:
    def test_business_location_state_match(self):
        sc = ScheduleC(
            business_name="Test Co",
            principal_business_or_profession="Consulting",
            line1_gross_receipts=Decimal("50000"),
            business_location_state="CA",
        )
        r = _return_with_w2s([])
        r = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="Alex",
                last_name="Doe",
                ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
            ),
            address=Address(
                street1="1 A", city="B", state="CA", zip="90001"
            ),
            schedules_c=[sc],
        )
        # Net profit = gross - expenses = 50000 (no expenses).
        assert state_source_schedule_c(r, "CA") == Decimal("50000.00")
        assert state_source_schedule_c(r, "NY") == Decimal("0.00")

    def test_business_location_state_none_not_sourced(self):
        sc = ScheduleC(
            business_name="Test Co",
            principal_business_or_profession="Consulting",
            line1_gross_receipts=Decimal("50000"),
        )
        r = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="Alex",
                last_name="Doe",
                ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
            ),
            address=Address(
                street1="1 A", city="B", state="CA", zip="90001"
            ),
            schedules_c=[sc],
        )
        # No business_location_state set → not included.
        assert state_source_schedule_c(r, "CA") == Decimal("0.00")


class TestSourcedOrProratedScheduleC:
    def test_sourced_wins_when_business_matches(self):
        sc = ScheduleC(
            business_name="Test Co",
            principal_business_or_profession="Consulting",
            line1_gross_receipts=Decimal("50000"),
            business_location_state="CA",
        )
        r = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="Alex",
                last_name="Doe",
                ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
            ),
            address=Address(
                street1="1 A", city="B", state="CA", zip="90001"
            ),
            schedules_c=[sc],
        )
        assert sourced_or_prorated_schedule_c(
            r, "CA", Decimal("50000"), 91
        ) == Decimal("50000.00")

    def test_falls_back_to_day_prorate_when_no_match(self):
        sc = ScheduleC(
            business_name="Test Co",
            principal_business_or_profession="Consulting",
            line1_gross_receipts=Decimal("50000"),
            # No business_location_state.
        )
        r = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="Alex",
                last_name="Doe",
                ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
            ),
            address=Address(
                street1="1 A", city="B", state="CA", zip="90001"
            ),
            schedules_c=[sc],
        )
        expected = day_prorate(Decimal("50000"), 91)
        assert sourced_or_prorated_schedule_c(
            r, "CA", Decimal("50000"), 91
        ) == expected
