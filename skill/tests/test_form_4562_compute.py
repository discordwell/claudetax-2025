"""Tests for Form 4562 Layer 1 compute.

Covers:
* §179 election and carryforward (business-income limitation)
* §179 phase-out at $3.13M threshold
* Bonus depreciation (40% for TY2025)
* MACRS 5-year and 7-year half-year convention
* §280F auto depreciation cap
* Pipeline integration with Schedule C line 13 depreciation flow
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from skill.scripts.calc.engine import schedule_c_net_profit
from skill.scripts.models import (
    CanonicalReturn,
    DepreciableAsset,
    ScheduleC,
    ScheduleCExpenses,
)
from skill.scripts.output.form_4562 import (
    AUTO_DEPR_CAPS_TY2025,
    BONUS_DEPRECIATION_PCT_TY2025,
    SECTION_179_DOLLAR_LIMIT_TY2025,
    SECTION_179_INVESTMENT_THRESHOLD_TY2025,
    SECTION_179_SUV_CAP_TY2025,
    compute_bonus_depreciation,
    compute_form_4562_fields,
    compute_form_4562_fields_for_schedule_c,
    compute_macrs_depreciation,
    section_179_phase_out_limit,
    total_depreciation_for_schedule_c,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_canonical(schedules_c: list[ScheduleC]) -> CanonicalReturn:
    return CanonicalReturn.model_validate(
        {
            "schema_version": "0.1.0",
            "tax_year": 2025,
            "filing_status": "single",
            "taxpayer": {
                "first_name": "Pat",
                "last_name": "Depreciator",
                "ssn": "123-45-6789",
                "date_of_birth": "1980-01-01",
                "is_blind": False,
                "is_age_65_or_older": False,
            },
            "address": {
                "street1": "1 Depr Way",
                "city": "Austin",
                "state": "TX",
                "zip": "78701",
            },
            "schedules_c": [
                sc.model_dump(mode="json") for sc in schedules_c
            ],
        }
    )


def _simple_sc(
    *,
    gross: Decimal = Decimal("200000"),
    assets: list[DepreciableAsset] | None = None,
    section_179_carryover: Decimal = Decimal("0"),
) -> ScheduleC:
    return ScheduleC(
        business_name="Acme Depr",
        principal_business_or_profession="Depreciation testing",
        line1_gross_receipts=gross,
        depreciable_assets=assets or [],
        section_179_carryover_from_prior_year=section_179_carryover,
    )


# ---------------------------------------------------------------------------
# TY2025 constants
# ---------------------------------------------------------------------------


class TestTY2025Constants:
    def test_section_179_dollar_limit(self) -> None:
        assert SECTION_179_DOLLAR_LIMIT_TY2025 == Decimal("1250000")

    def test_section_179_phase_out_threshold(self) -> None:
        assert SECTION_179_INVESTMENT_THRESHOLD_TY2025 == Decimal("3130000")

    def test_section_179_suv_cap(self) -> None:
        assert SECTION_179_SUV_CAP_TY2025 == Decimal("31300")

    def test_bonus_depreciation_is_40_percent_for_ty2025(self) -> None:
        """OBBBA did NOT restore 100%; TCJA phase-down lands at 40% in TY2025."""
        assert BONUS_DEPRECIATION_PCT_TY2025 == Decimal("0.40")


# ---------------------------------------------------------------------------
# MACRS single-asset depreciation (pipeline through the 4562 compute)
# ---------------------------------------------------------------------------


class TestMACRSComputation:
    def test_5_year_computer_year_1_is_2000(self) -> None:
        """$10,000 computer placed in 2025, MACRS 5-year HY, bonus OFF."""
        asset = DepreciableAsset(
            description="Dev laptop",
            date_placed_in_service=dt.date(2025, 3, 15),
            cost=Decimal("10000"),
            macrs_class="5",
            bonus_depreciation_elected=False,
        )
        sc = _simple_sc(assets=[asset])
        ret = _make_canonical([sc])
        fields = compute_form_4562_fields(ret, 0)
        # Year 1 MACRS = 20% of $10,000 = $2,000
        assert fields.line_22_total_depreciation == Decimal("2000.00")

    def test_7_year_equipment_year_1_is_7145(self) -> None:
        """$50,000 equipment placed in 2025, MACRS 7-year HY, bonus OFF.

        Using the Pub 946 rounded percentage (14.29%), year-1 = $7,145.
        The task description says "$7,143 (14.29%)" which is an arithmetic
        mismatch: 14.29% × 50000 = 7145 exactly. We match the table.
        """
        asset = DepreciableAsset(
            description="Lathe",
            date_placed_in_service=dt.date(2025, 1, 1),
            cost=Decimal("50000"),
            macrs_class="7",
            bonus_depreciation_elected=False,
        )
        sc = _simple_sc(assets=[asset])
        ret = _make_canonical([sc])
        fields = compute_form_4562_fields(ret, 0)
        assert fields.line_22_total_depreciation == Decimal("7145.00")

    def test_macrs_helper_direct(self) -> None:
        asset = DepreciableAsset(
            description="Widget",
            date_placed_in_service=dt.date(2025, 1, 1),
            cost=Decimal("1000"),
            macrs_class="5",
        )
        result = compute_macrs_depreciation(
            asset, tax_year=2025, depreciable_basis=Decimal("1000")
        )
        assert result == Decimal("200.00")


# ---------------------------------------------------------------------------
# §179 election, business-income limit, carryforward
# ---------------------------------------------------------------------------


class TestSection179:
    def test_carryforward_when_business_income_is_binding(self) -> None:
        """$800k §179 election, business income $200k → current $200k, carryforward $600k.

        Build a Schedule C whose pre-depreciation profit is exactly
        $200,000 and elect $800,000 of §179 on a single equipment asset.
        The tentative §179 (line 9) is $800k; the business-income limit
        (line 11) is $200k; line 12 = min(line 9, line 11) = $200k;
        line 13 carryforward = $600k.
        """
        asset = DepreciableAsset(
            description="Big machine",
            date_placed_in_service=dt.date(2025, 1, 1),
            cost=Decimal("800000"),
            macrs_class="7",
            section_179_elected=Decimal("800000"),
            bonus_depreciation_elected=False,
        )
        sc = ScheduleC(
            business_name="Big Biz",
            principal_business_or_profession="Machining",
            line1_gross_receipts=Decimal("200000"),
            depreciable_assets=[asset],
        )
        ret = _make_canonical([sc])
        fields = compute_form_4562_fields(ret, 0)
        assert fields.line_11_business_income_limitation == Decimal("200000")
        assert fields.line_12_section_179_deduction_current_year == Decimal("200000")
        assert fields.line_13_carryover_to_next_year == Decimal("600000")

    def test_phase_out_at_3_5m_total_assets(self) -> None:
        """$3.5M of §179 property → phased limit.

        Line 2 = $3,500,000. Line 4 = $3.5M − $3.13M = $370,000.
        Line 5 = max(0, $1,250,000 − $370,000) = $880,000.
        """
        # One asset sized at $3.5M to trigger the phase-out
        asset = DepreciableAsset(
            description="Fleet",
            date_placed_in_service=dt.date(2025, 1, 1),
            cost=Decimal("3500000"),
            macrs_class="7",
            section_179_elected=Decimal("1250000"),
            bonus_depreciation_elected=False,
        )
        sc = ScheduleC(
            business_name="Fleet Co",
            principal_business_or_profession="Fleet ops",
            line1_gross_receipts=Decimal("5000000"),
            depreciable_assets=[asset],
        )
        ret = _make_canonical([sc])
        fields = compute_form_4562_fields(ret, 0)
        assert fields.line_2_total_cost_section_179_property == Decimal("3500000")
        assert fields.line_4_reduction_in_limitation == Decimal("370000")
        assert fields.line_5_dollar_limit_after_reduction == Decimal("880000")
        # §179 expense is capped to the phased limit
        assert fields.line_9_tentative_deduction == Decimal("880000")

    def test_section_179_phase_out_helper_pure(self) -> None:
        assert section_179_phase_out_limit(Decimal("2000000")) == Decimal("1250000")
        assert section_179_phase_out_limit(Decimal("3130000")) == Decimal("1250000")
        assert section_179_phase_out_limit(Decimal("3500000")) == Decimal("880000")
        assert section_179_phase_out_limit(Decimal("4400000")) == Decimal("0")


# ---------------------------------------------------------------------------
# Bonus depreciation (§168(k))
# ---------------------------------------------------------------------------


class TestBonusDepreciation:
    def test_40_percent_of_cost_on_new_asset(self) -> None:
        asset = DepreciableAsset(
            description="Printer",
            date_placed_in_service=dt.date(2025, 1, 1),
            cost=Decimal("5000"),
            macrs_class="5",
            bonus_depreciation_elected=True,
        )
        bonus = compute_bonus_depreciation(asset, post_179_basis=Decimal("5000"))
        assert bonus == Decimal("2000.00")  # 40% × $5,000

    def test_elect_out_returns_zero(self) -> None:
        asset = DepreciableAsset(
            description="Printer",
            date_placed_in_service=dt.date(2025, 1, 1),
            cost=Decimal("5000"),
            macrs_class="5",
            bonus_depreciation_elected=False,
        )
        bonus = compute_bonus_depreciation(asset, post_179_basis=Decimal("5000"))
        assert bonus == Decimal("0")

    def test_full_pipeline_bonus_then_macrs(self) -> None:
        """$5k 5-yr asset, bonus ON, no §179:
        bonus 40% = $2,000; remaining basis $3,000; MACRS year 1 20% = $600.
        Line 22 total = $2,000 bonus + $600 MACRS = $2,600.
        """
        asset = DepreciableAsset(
            description="Printer",
            date_placed_in_service=dt.date(2025, 1, 1),
            cost=Decimal("5000"),
            macrs_class="5",
            bonus_depreciation_elected=True,
        )
        sc = _simple_sc(assets=[asset])
        ret = _make_canonical([sc])
        fields = compute_form_4562_fields(ret, 0)
        assert fields.line_14_special_depreciation_allowance == Decimal("2000.00")
        assert fields.line_22_total_depreciation == Decimal("2600.00")


# ---------------------------------------------------------------------------
# §280F auto depreciation cap
# ---------------------------------------------------------------------------


class TestSection280FAutoCap:
    def test_luxury_auto_year_one_cap_is_20400(self) -> None:
        """§280F first-year cap (with bonus) for TY2025: $20,400.

        A $60,000 listed-property auto with §179 $0 and bonus ON would
        otherwise produce 40% × $60,000 = $24,000 bonus + MACRS on the
        remainder. Cap limits the total to $20,400.
        """
        asset = DepreciableAsset(
            description="Luxury sedan",
            date_placed_in_service=dt.date(2025, 1, 1),
            cost=Decimal("60000"),
            macrs_class="5",
            bonus_depreciation_elected=True,
            is_listed_property=True,
            business_use_pct=Decimal("100"),
        )
        sc = _simple_sc(assets=[asset])
        ret = _make_canonical([sc])
        fields = compute_form_4562_fields(ret, 0)
        # Listed property rolls to line 21, which also rolls into line 22
        assert fields.line_21_listed_property_total == Decimal("20400.00")
        assert fields.line_22_total_depreciation == Decimal("20400.00")

    def test_auto_caps_are_populated(self) -> None:
        assert AUTO_DEPR_CAPS_TY2025[0] == Decimal("20400")
        assert AUTO_DEPR_CAPS_TY2025[1] == Decimal("19800")
        assert AUTO_DEPR_CAPS_TY2025[2] == Decimal("11900")
        assert AUTO_DEPR_CAPS_TY2025[3] == Decimal("7160")


# ---------------------------------------------------------------------------
# Pipeline: Schedule C line 13 flow
# ---------------------------------------------------------------------------


class TestPipelineIntegration:
    def test_schedule_c_net_profit_uses_4562_total(self) -> None:
        """Sch C with a $10k computer → net profit reduced by $2,000 depreciation."""
        asset = DepreciableAsset(
            description="Computer",
            date_placed_in_service=dt.date(2025, 1, 1),
            cost=Decimal("10000"),
            macrs_class="5",
            bonus_depreciation_elected=False,
        )
        sc = ScheduleC(
            business_name="Net Test",
            principal_business_or_profession="Dev",
            line1_gross_receipts=Decimal("50000"),
            depreciable_assets=[asset],
        )
        # Without the asset, net profit would be $50,000
        # With $2,000 MACRS line-13 depreciation, net profit = $48,000
        assert schedule_c_net_profit(sc) == Decimal("48000.00")

    def test_caller_supplied_line_13_is_overridden_by_assets(self) -> None:
        """When depreciable_assets is non-empty, caller's line_13 is ignored."""
        asset = DepreciableAsset(
            description="Computer",
            date_placed_in_service=dt.date(2025, 1, 1),
            cost=Decimal("10000"),
            macrs_class="5",
            bonus_depreciation_elected=False,
        )
        # Caller sets an inflated line_13 that should be overridden
        expenses = ScheduleCExpenses(line13_depreciation=Decimal("99999"))
        sc = ScheduleC(
            business_name="Override Test",
            principal_business_or_profession="Dev",
            line1_gross_receipts=Decimal("50000"),
            expenses=expenses,
            depreciable_assets=[asset],
        )
        # Should use $2,000 from the asset, not $99,999 from the field
        assert schedule_c_net_profit(sc) == Decimal("48000.00")

    def test_no_assets_preserves_caller_line_13(self) -> None:
        """Backwards compatibility: no assets → caller's line_13 is used."""
        expenses = ScheduleCExpenses(line13_depreciation=Decimal("1500"))
        sc = ScheduleC(
            business_name="Legacy",
            principal_business_or_profession="Legacy",
            line1_gross_receipts=Decimal("10000"),
            expenses=expenses,
        )
        assert schedule_c_net_profit(sc) == Decimal("8500")

    def test_total_depreciation_helper_is_zero_without_assets(self) -> None:
        sc = _simple_sc()
        ret = _make_canonical([sc])
        assert total_depreciation_for_schedule_c(ret, 0) == Decimal("0")

    def test_compute_standalone_from_schedule_c(self) -> None:
        """compute_form_4562_fields_for_schedule_c works with no CanonicalReturn."""
        asset = DepreciableAsset(
            description="PC",
            date_placed_in_service=dt.date(2025, 1, 1),
            cost=Decimal("4000"),
            macrs_class="5",
            bonus_depreciation_elected=False,
        )
        sc = _simple_sc(assets=[asset])
        fields = compute_form_4562_fields_for_schedule_c(
            sc,
            tax_year=2025,
            taxpayer_name="Test T",
            identifying_number="111-22-3333",
        )
        # 20% × $4,000 = $800
        assert fields.line_22_total_depreciation == Decimal("800.00")


# ---------------------------------------------------------------------------
# Amortization (Part VI)
# ---------------------------------------------------------------------------


class TestAmortization:
    def test_intangible_lands_on_part_vi(self) -> None:
        """Asset with macrs_class=None is treated as §197 amortization.

        The calc module currently passes the full cost as line 44 when
        no MACRS class is present — a full §197 15-year schedule is a
        follow-up. For wave 6 the row just needs to exist on Part VI.
        """
        asset = DepreciableAsset(
            description="Goodwill",
            date_placed_in_service=dt.date(2025, 1, 1),
            cost=Decimal("15000"),
            macrs_class=None,
            bonus_depreciation_elected=False,
        )
        sc = _simple_sc(assets=[asset])
        ret = _make_canonical([sc])
        fields = compute_form_4562_fields(ret, 0)
        assert len(fields.part_vi_amortization_rows) == 1
        assert fields.part_vi_amortization_rows[0].description == "Goodwill"


# ---------------------------------------------------------------------------
# Multi-business index validation
# ---------------------------------------------------------------------------


class TestIndexValidation:
    def test_out_of_range_index_raises(self) -> None:
        sc = _simple_sc()
        ret = _make_canonical([sc])
        with pytest.raises(ValueError, match="out of range"):
            compute_form_4562_fields(ret, 5)

    def test_negative_index_raises(self) -> None:
        sc = _simple_sc()
        ret = _make_canonical([sc])
        with pytest.raises(ValueError, match="out of range"):
            compute_form_4562_fields(ret, -1)
