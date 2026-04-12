"""Multi-state golden fixture: PA -> IL part-year mover (Wave 6 upgrade).

Originally landed in wave 5 as the first multi-state golden, with v1
day-prorated tax outputs locked. Wave 6 replaced day-proration with real
W-2 state-row sourcing on the wage side for PA and IL, so this test now
locks the sourced-tax numbers instead.

Federal (unchanged from simple_w2_standard golden, both at $65k AGI
Single TY2025 standard deduction):
    AGI = $65,000, Std ded = $15,750, TI = $49,250, Fed tax = $5,755,
    Withheld = $6,500, Refund = $745.

PA (Wave 6 W-2 state-row sourced):
    Sourced wages (state_rows[PA].state_wages)   = $30,000
    Sourced tax (30000 * 0.0307)                 = $921.00
    Resident-basis hypothetical (still stamped)  = $1,995.50
    Withheld                                     = $921.00 -> $0 owed

IL (Wave 6 W-2 state-row sourced):
    Sourced wages (state_rows[IL].state_wages)   = $35,000
    Personal exemption                           = $2,850 (Single)
    Sourced taxable                              = $32,150
    Sourced tax (32150 * 0.0495)                 = $1,591.43
    Resident-basis hypothetical (still stamped)  = $3,076.43
    Withheld                                     = $1,732.50 -> $141.07 REFUND

Wave 6 still runs the IL adds/subs (Line 2 muni, Line 5 SS+retirement,
Sch M Line 22 US Treasury) on the resident-basis hypothetical; they
evaluate to zero on this fixture because there are no 1099-INT, 1099-DIV,
SSA-1099, or 1099-R forms attached. ``test_il_adds_subs_all_zero`` still
pins that no-op against future regressions.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.calc.engine import compute
from skill.scripts.models import (
    CanonicalReturn,
    FilingStatus,
    ResidencyStatus,
    StateReturn,
)
from skill.scripts.states._plugin_api import FederalTotals
from skill.scripts.states._registry import registry


GOLDEN_NAME = "multi_state_part_year_pa_il"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def golden_dir(fixtures_dir: Path) -> Path:
    return fixtures_dir / GOLDEN_NAME


@pytest.fixture
def input_return(golden_dir: Path) -> CanonicalReturn:
    data = json.loads((golden_dir / "input.json").read_text())
    return CanonicalReturn.model_validate(data)


@pytest.fixture
def expected(golden_dir: Path) -> dict:
    return json.loads((golden_dir / "expected.json").read_text())


@pytest.fixture
def computed_return(input_return: CanonicalReturn) -> CanonicalReturn:
    """Compute once per test to amortize the tenforty call."""
    return compute(input_return)


@pytest.fixture
def federal_totals(computed_return: CanonicalReturn) -> FederalTotals:
    """Build the FederalTotals dataclass that state plugins consume.

    This is the same shape the wave-6 pipeline dispatcher will pass to
    every plugin. Building it from a *computed* CanonicalReturn ensures the
    state plugins see exactly what the federal engine produced.
    """
    c = computed_return.computed
    assert c.adjusted_gross_income is not None
    assert c.taxable_income is not None
    assert c.total_tax is not None
    assert c.tentative_tax is not None
    assert c.deduction_taken is not None
    return FederalTotals(
        filing_status=FilingStatus.SINGLE,
        num_dependents=0,
        adjusted_gross_income=c.adjusted_gross_income,
        taxable_income=c.taxable_income,
        total_federal_tax=c.total_tax,
        federal_income_tax=c.tentative_tax,
        federal_standard_deduction=Decimal("15750"),
        federal_itemized_deductions_total=Decimal("0"),
        deduction_taken=c.deduction_taken,
        federal_withholding_from_w2s=Decimal("6500"),
    )


def _d(s: str | None) -> Decimal | None:
    return Decimal(s) if s is not None else None


# ---------------------------------------------------------------------------
# Step 0 — input shape
# ---------------------------------------------------------------------------


@pytest.mark.golden
class TestInputShape:
    def test_input_loads_as_canonical_return(self, input_return: CanonicalReturn):
        """Validate that the fixture input deserializes cleanly."""
        assert isinstance(input_return, CanonicalReturn)
        assert input_return.tax_year == 2025
        assert input_return.filing_status == FilingStatus.SINGLE

    def test_input_has_two_w2s(self, input_return: CanonicalReturn):
        assert len(input_return.w2s) == 2

    def test_w2_state_rows_pa_and_il(self, input_return: CanonicalReturn):
        """Each W-2 has exactly one state row keyed to PA and IL respectively.

        This is the load-bearing assertion for any future per-state
        sourcing implementation: the dispatcher will read state_rows to
        decide which W-2 contributes to which state's source income.
        """
        philly = input_return.w2s[0]
        chicago = input_return.w2s[1]
        assert len(philly.state_rows) == 1
        assert philly.state_rows[0].state == "PA"
        assert philly.state_rows[0].state_wages == Decimal("30000.00")
        assert philly.state_rows[0].state_tax_withheld == Decimal("921.00")
        assert len(chicago.state_rows) == 1
        assert chicago.state_rows[0].state == "IL"
        assert chicago.state_rows[0].state_wages == Decimal("35000.00")
        assert chicago.state_rows[0].state_tax_withheld == Decimal("1732.50")

    def test_input_no_dependents(self, input_return: CanonicalReturn):
        assert input_return.dependents == []

    def test_input_no_obbba_triggers(self, input_return: CanonicalReturn):
        """No tips, no overtime, no senior age, no Form 4547 — the
        federal compute() must take the single-pass hot path."""
        assert input_return.taxpayer.is_age_65_or_older is False
        a = input_return.adjustments
        assert a.qualified_tips_deduction_schedule_1a == Decimal("0")
        assert a.qualified_overtime_deduction_schedule_1a == Decimal("0")
        assert a.senior_deduction_obbba == Decimal("0")
        assert a.trump_account_deduction_form_4547 == Decimal("0")

    def test_input_uses_standard_deduction(self, input_return: CanonicalReturn):
        assert input_return.itemize_deductions is False
        assert input_return.itemized is None


# ---------------------------------------------------------------------------
# Step 1 — federal compute
# ---------------------------------------------------------------------------


@pytest.mark.golden
class TestFederalCompute:
    def test_total_income(self, computed_return: CanonicalReturn, expected: dict):
        assert computed_return.computed.total_income == _d(
            expected["expected_computed_totals"]["total_income"]
        )

    def test_adjusted_gross_income(
        self, computed_return: CanonicalReturn, expected: dict
    ):
        assert computed_return.computed.adjusted_gross_income == _d(
            expected["expected_computed_totals"]["adjusted_gross_income"]
        )

    def test_deduction_taken_is_obbba_standard(
        self, computed_return: CanonicalReturn, expected: dict
    ):
        assert computed_return.computed.deduction_taken == _d(
            expected["expected_computed_totals"]["deduction_taken"]
        )
        # OBBBA-locked: TY2025 Single standard deduction is $15,750.
        assert computed_return.computed.deduction_taken == Decimal("15750.00")

    def test_taxable_income(
        self, computed_return: CanonicalReturn, expected: dict
    ):
        assert computed_return.computed.taxable_income == _d(
            expected["expected_computed_totals"]["taxable_income"]
        )

    def test_federal_income_tax_locked_to_simple_w2_golden(
        self, computed_return: CanonicalReturn, expected: dict
    ):
        """Federal tax must equal $5,755 — same as simple_w2_standard
        golden because both fixtures land at $49,250 TI Single TY2025."""
        assert computed_return.computed.tentative_tax == Decimal("5755.00")
        assert computed_return.computed.tentative_tax == _d(
            expected["expected_computed_totals"]["tentative_tax"]
        )

    def test_total_tax(self, computed_return: CanonicalReturn, expected: dict):
        assert computed_return.computed.total_tax == _d(
            expected["expected_computed_totals"]["total_tax"]
        )

    def test_total_payments_sums_w2_box2(
        self, computed_return: CanonicalReturn, expected: dict
    ):
        """Total payments = sum of W-2 box 2 = $2,500 + $4,000 = $6,500."""
        assert computed_return.computed.total_payments == Decimal("6500.00")
        assert computed_return.computed.total_payments == _d(
            expected["expected_computed_totals"]["total_payments"]
        )

    def test_refund(self, computed_return: CanonicalReturn, expected: dict):
        assert computed_return.computed.refund == _d(
            expected["expected_computed_totals"]["refund"]
        )
        assert computed_return.computed.amount_owed is None

    def test_marginal_rate(
        self, computed_return: CanonicalReturn, expected: dict
    ):
        assert (
            computed_return.computed.marginal_rate
            == expected["expected_computed_totals"]["marginal_rate"]
        )

    def test_all_federal_fields_match(
        self, computed_return: CanonicalReturn, expected: dict
    ):
        """Catch-all federal diff. Cross-check every locked field in one go."""
        exp = expected["expected_computed_totals"]
        c = computed_return.computed

        mismatches: list[str] = []

        def _check(name: str, actual, expected_val) -> None:
            if isinstance(expected_val, str):
                expected_decimal = _d(expected_val)
                if actual != expected_decimal:
                    mismatches.append(
                        f"{name}: actual={actual!r} expected={expected_decimal!r}"
                    )
            elif expected_val is None:
                if actual is not None:
                    mismatches.append(f"{name}: actual={actual!r} expected=None")
            else:
                if actual != expected_val:
                    mismatches.append(
                        f"{name}: actual={actual!r} expected={expected_val!r}"
                    )

        _check("total_income", c.total_income, exp["total_income"])
        _check("adjusted_gross_income", c.adjusted_gross_income, exp["adjusted_gross_income"])
        _check("deduction_taken", c.deduction_taken, exp["deduction_taken"])
        _check("taxable_income", c.taxable_income, exp["taxable_income"])
        _check("tentative_tax", c.tentative_tax, exp["tentative_tax"])
        _check("total_tax", c.total_tax, exp["total_tax"])
        _check("total_payments", c.total_payments, exp["total_payments"])
        _check("refund", c.refund, exp["refund"])
        _check("amount_owed", c.amount_owed, exp["amount_owed"])
        _check("marginal_rate", c.marginal_rate, exp["marginal_rate"])

        if mismatches:
            pytest.fail("Federal golden diff failures:\n" + "\n".join(mismatches))


# ---------------------------------------------------------------------------
# Step 2 — Pennsylvania state plugin dispatch
# ---------------------------------------------------------------------------


@pytest.mark.golden
class TestPennsylvaniaPluginDispatch:
    def test_pa_plugin_registered(self):
        """Wave-2 wired PA into the registry; smoke-check the lookup."""
        assert registry.has("PA")
        plugin = registry.get("PA")
        assert plugin.meta.code == "PA"

    def test_pa_compute_returns_state_return(
        self, input_return: CanonicalReturn, federal_totals: FederalTotals
    ):
        plugin = registry.get("PA")
        result = plugin.compute(
            input_return,
            federal_totals,
            ResidencyStatus.PART_YEAR,
            days_in_state=181,
        )
        assert isinstance(result, StateReturn)
        assert result.state == "PA"
        assert result.residency == ResidencyStatus.PART_YEAR
        assert result.days_in_state == 181

    def test_pa_state_taxable_income(
        self,
        input_return: CanonicalReturn,
        federal_totals: FederalTotals,
        expected: dict,
    ):
        """PA-40 base for a wage-only filer is the gross W-2 sum (BOTH
        W-2s), because PA's tenforty path marshals the canonical
        ``w2_income`` and PA does not separate by state row at this layer.
        That's exactly the v1 limitation that the resident-basis-prorate
        approach makes visible."""
        plugin = registry.get("PA")
        result = plugin.compute(
            input_return,
            federal_totals,
            ResidencyStatus.PART_YEAR,
            days_in_state=181,
        )
        exp = expected["expected_pa_state_return"]["state_specific"]
        assert result.state_specific["state_taxable_income"] == _d(
            exp["state_taxable_income"]
        )

    def test_pa_resident_basis_tax_locked(
        self,
        input_return: CanonicalReturn,
        federal_totals: FederalTotals,
        expected: dict,
    ):
        """PA flat 3.07% on the full $65k canonical w2_income = $1,995.50."""
        plugin = registry.get("PA")
        result = plugin.compute(
            input_return,
            federal_totals,
            ResidencyStatus.PART_YEAR,
            days_in_state=181,
        )
        exp = expected["expected_pa_state_return"]["state_specific"]
        assert result.state_specific["state_total_tax_resident_basis"] == _d(
            exp["state_total_tax_resident_basis"]
        )
        assert result.state_specific["state_total_tax_resident_basis"] == Decimal(
            "1995.50"
        )

    def test_pa_sourced_tax_locked_to_the_cent(
        self,
        input_return: CanonicalReturn,
        federal_totals: FederalTotals,
        expected: dict,
    ):
        """Wave 6: PA plugin sources wages from W-2 state_rows[PA].state_wages
        and taxes them at 3.07% directly. $30,000 * 0.0307 = $921.00 exactly.
        """
        plugin = registry.get("PA")
        result = plugin.compute(
            input_return,
            federal_totals,
            ResidencyStatus.PART_YEAR,
            days_in_state=181,
        )
        exp = expected["expected_pa_state_return"]["state_specific"]
        assert result.state_specific["state_total_tax"] == _d(
            exp["state_total_tax"]
        )
        assert result.state_specific["state_total_tax"] == Decimal("921.00")

    def test_pa_sourced_wages_from_w2_state_rows(
        self, input_return: CanonicalReturn, federal_totals: FederalTotals
    ):
        """Wave 6 invariant: PA plugin must report the sourced wages
        from W-2 state_rows under the canonical key. $30,000 exactly.
        """
        plugin = registry.get("PA")
        result = plugin.compute(
            input_return,
            federal_totals,
            ResidencyStatus.PART_YEAR,
            days_in_state=181,
        )
        assert result.state_specific[
            "pa_sourced_wages_from_w2_state_rows"
        ] == Decimal("30000.00")
        assert result.state_specific["pa_state_rows_present"] is True
        # The W-2 state-row path bypasses day-proration entirely.
        assert result.state_specific["apportionment_fraction"] == Decimal("1")

    def test_pa_state_return_validates_via_pydantic(
        self, input_return: CanonicalReturn, federal_totals: FederalTotals
    ):
        """Round-trip through Pydantic JSON to confirm the StateReturn
        validates under the canonical model contract."""
        plugin = registry.get("PA")
        result = plugin.compute(
            input_return,
            federal_totals,
            ResidencyStatus.PART_YEAR,
            days_in_state=181,
        )
        dumped = result.model_dump(mode="json")
        rehydrated = StateReturn.model_validate(dumped)
        assert rehydrated.state == "PA"
        assert rehydrated.residency == ResidencyStatus.PART_YEAR
        assert rehydrated.days_in_state == 181


# ---------------------------------------------------------------------------
# Step 3 — Illinois state plugin dispatch
# ---------------------------------------------------------------------------


@pytest.mark.golden
class TestIllinoisPluginDispatch:
    def test_il_plugin_registered(self):
        assert registry.has("IL")
        plugin = registry.get("IL")
        assert plugin.meta.code == "IL"

    def test_il_compute_returns_state_return(
        self, input_return: CanonicalReturn, federal_totals: FederalTotals
    ):
        plugin = registry.get("IL")
        result = plugin.compute(
            input_return,
            federal_totals,
            ResidencyStatus.PART_YEAR,
            days_in_state=184,
        )
        assert isinstance(result, StateReturn)
        assert result.state == "IL"
        assert result.residency == ResidencyStatus.PART_YEAR
        assert result.days_in_state == 184

    def test_il_base_income_equals_federal_agi(
        self,
        input_return: CanonicalReturn,
        federal_totals: FederalTotals,
        expected: dict,
    ):
        """v1 IL plugin starts from federal AGI (= $65,000) for the
        IL-1040 line 1 base. CP8-D adds/subs evaluate to zero on this
        fixture (no muni / SS / Treasury / retirement income), so the
        adjusted base equals the unadjusted base."""
        plugin = registry.get("IL")
        result = plugin.compute(
            input_return,
            federal_totals,
            ResidencyStatus.PART_YEAR,
            days_in_state=184,
        )
        exp = expected["expected_il_state_return"]["state_specific"]
        assert result.state_specific["state_base_income_approx"] == _d(
            exp["state_base_income_approx"]
        )
        assert result.state_specific[
            "state_base_income_after_adjustments"
        ] == _d(exp["state_base_income_after_adjustments"])

    def test_il_adds_subs_all_zero(
        self, input_return: CanonicalReturn, federal_totals: FederalTotals
    ):
        """CP8-D regression lock: with no 1099-INT box 8/box 3, no
        1099-DIV box 11, no SSA-1099, and no 1099-R, every IL-1040
        Line 2 / Line 5 / Sch M Line 22 number must be exactly $0.00.

        If this test starts failing, the most likely cause is that
        ``_il_additions`` or ``_il_subtractions`` started picking up an
        unexpected source field — investigate before re-blessing.
        """
        plugin = registry.get("IL")
        result = plugin.compute(
            input_return,
            federal_totals,
            ResidencyStatus.PART_YEAR,
            days_in_state=184,
        )
        ss = result.state_specific
        assert ss["il_additions_total"] == Decimal("0.00")
        assert ss["il_subtractions_total"] == Decimal("0.00")
        adds = ss["il_additions"]
        assert adds["il_1040_line2_tax_exempt_interest_addback"] == Decimal(
            "0.00"
        )
        subs = ss["il_subtractions"]
        assert subs["il_1040_line5_social_security_subtraction"] == Decimal(
            "0.00"
        )
        assert subs["il_1040_line5_retirement_income_subtraction"] == Decimal(
            "0.00"
        )
        assert subs["il_schedule_m_line22_us_treasury_subtraction"] == Decimal(
            "0.00"
        )

    def test_il_exemption_count_and_total(
        self, input_return: CanonicalReturn, federal_totals: FederalTotals
    ):
        """Single, no dependents -> 1 exemption * $2,850 = $2,850 (TY2025)."""
        plugin = registry.get("IL")
        result = plugin.compute(
            input_return,
            federal_totals,
            ResidencyStatus.PART_YEAR,
            days_in_state=184,
        )
        assert result.state_specific["state_exemption_count"] == 1
        assert result.state_specific["state_exemption_per_person"] == Decimal(
            "2850"
        )
        assert result.state_specific["state_exemption_total"] == Decimal(
            "2850.00"
        )

    def test_il_state_taxable_income(
        self,
        input_return: CanonicalReturn,
        federal_totals: FederalTotals,
        expected: dict,
    ):
        """65,000 - 2,850 = 62,150."""
        plugin = registry.get("IL")
        result = plugin.compute(
            input_return,
            federal_totals,
            ResidencyStatus.PART_YEAR,
            days_in_state=184,
        )
        exp = expected["expected_il_state_return"]["state_specific"]
        assert result.state_specific["state_taxable_income"] == _d(
            exp["state_taxable_income"]
        )
        assert result.state_specific["state_taxable_income"] == Decimal(
            "62150.00"
        )

    def test_il_resident_basis_tax_locked(
        self,
        input_return: CanonicalReturn,
        federal_totals: FederalTotals,
        expected: dict,
    ):
        """62,150 * 0.0495 = 3,076.4250 -> 3,076.43 (ROUND_HALF_UP)."""
        plugin = registry.get("IL")
        result = plugin.compute(
            input_return,
            federal_totals,
            ResidencyStatus.PART_YEAR,
            days_in_state=184,
        )
        exp = expected["expected_il_state_return"]["state_specific"]
        assert result.state_specific["state_total_tax_resident_basis"] == _d(
            exp["state_total_tax_resident_basis"]
        )
        assert result.state_specific[
            "state_total_tax_resident_basis"
        ] == Decimal("3076.43")

    def test_il_sourced_tax_locked_to_the_cent(
        self,
        input_return: CanonicalReturn,
        federal_totals: FederalTotals,
        expected: dict,
    ):
        """Wave 8C Schedule NR: IL plugin sources wages from W-2 state rows
        and prorates the exemption by the IL-source ratio.
        ratio = 35000/65000 = 0.5385...; prorated exemption = 2850 * ratio = 1534.62
        ($35,000 - $1,534.62) * 0.0495 = $1,656.5363... -> $1,656.54 ROUND_HALF_UP.
        """
        plugin = registry.get("IL")
        result = plugin.compute(
            input_return,
            federal_totals,
            ResidencyStatus.PART_YEAR,
            days_in_state=184,
        )
        exp = expected["expected_il_state_return"]["state_specific"]
        assert result.state_specific["state_total_tax"] == _d(
            exp["state_total_tax"]
        )
        assert result.state_specific["state_total_tax"] == Decimal("1656.54")

    def test_il_sourced_wages_from_w2_state_rows(
        self, input_return: CanonicalReturn, federal_totals: FederalTotals
    ):
        """Wave 6 invariant: IL plugin must report the sourced wages
        from W-2 state_rows under the canonical key. $35,000 exactly.
        """
        plugin = registry.get("IL")
        result = plugin.compute(
            input_return,
            federal_totals,
            ResidencyStatus.PART_YEAR,
            days_in_state=184,
        )
        assert result.state_specific[
            "il_sourced_wages_from_w2_state_rows"
        ] == Decimal("35000.00")
        assert result.state_specific["il_state_rows_present"] is True
        # The W-2 state-row path bypasses day-proration entirely.
        assert result.state_specific["apportionment_fraction"] == Decimal("1")

    def test_il_v1_limitations_present(
        self, input_return: CanonicalReturn, federal_totals: FederalTotals
    ):
        """The IL plugin must echo its v1 limitations list so downstream
        consumers can see what is NOT modeled. Lock the existence and
        type — exact text is asserted in test_state_il.py."""
        plugin = registry.get("IL")
        result = plugin.compute(
            input_return,
            federal_totals,
            ResidencyStatus.PART_YEAR,
            days_in_state=184,
        )
        assert "v1_limitations" in result.state_specific
        v1 = result.state_specific["v1_limitations"]
        assert isinstance(v1, list)
        assert len(v1) >= 1

    def test_il_state_return_validates_via_pydantic(
        self, input_return: CanonicalReturn, federal_totals: FederalTotals
    ):
        plugin = registry.get("IL")
        result = plugin.compute(
            input_return,
            federal_totals,
            ResidencyStatus.PART_YEAR,
            days_in_state=184,
        )
        dumped = result.model_dump(mode="json")
        rehydrated = StateReturn.model_validate(dumped)
        assert rehydrated.state == "IL"
        assert rehydrated.residency == ResidencyStatus.PART_YEAR
        assert rehydrated.days_in_state == 184


# ---------------------------------------------------------------------------
# Step 4 — cross-state invariants (the part the brief calls out as the
# whole point of the fixture)
# ---------------------------------------------------------------------------


@pytest.mark.golden
class TestCrossStateInvariants:
    def test_pa_il_not_reciprocal(self):
        """PA and IL are NOT in mutual reciprocity. Both states tax the
        income earned while a resident. This is the pre-condition for the
        whole fixture: if PA and IL ever became reciprocal, the dispatch
        logic would have to change and this fixture would have to be
        reworked. Lock the assumption."""
        from skill.scripts.states._plugin_api import ReciprocityTable

        table = ReciprocityTable.load()
        assert table.are_reciprocal("PA", "IL") is False
        assert table.are_reciprocal("IL", "PA") is False
        # Sanity-check the partner sets while we're here.
        assert "IL" not in table.partners_of("PA")
        assert "PA" not in table.partners_of("IL")

    def test_residency_days_sum_to_year(
        self, expected: dict
    ):
        """181 + 184 = 365. The fixture's day apportionment must cover
        the full year exactly — no gap, no double-count."""
        days = expected["scenario"]
        assert days["pa_residency_days"] + days["il_residency_days"] == days[
            "total_days"
        ]
        assert days["total_days"] == 365

    def test_dispatch_both_plugins_via_registry(
        self, input_return: CanonicalReturn, federal_totals: FederalTotals
    ):
        """Smoke-check that registry.get(...) works for both PA and IL
        and the resulting StateReturns can coexist in a single
        list[StateReturn] for downstream consumers (the wave-6 dispatch
        layer will append both to canonical_return.state_returns)."""
        pa_plugin = registry.get("PA")
        il_plugin = registry.get("IL")
        pa_result = pa_plugin.compute(
            input_return,
            federal_totals,
            ResidencyStatus.PART_YEAR,
            days_in_state=181,
        )
        il_result = il_plugin.compute(
            input_return,
            federal_totals,
            ResidencyStatus.PART_YEAR,
            days_in_state=184,
        )
        state_returns: list[StateReturn] = [pa_result, il_result]
        assert len(state_returns) == 2
        assert {sr.state for sr in state_returns} == {"PA", "IL"}
        assert all(sr.residency == ResidencyStatus.PART_YEAR for sr in state_returns)

    def test_neither_state_doubles_federal_withholding(
        self, computed_return: CanonicalReturn
    ):
        """Federal total_payments must reflect ONLY box 2 (federal w/h),
        not the state-row state_tax_withheld amounts. PA $921 and IL
        $1,732.50 must NOT inflate the federal payments."""
        assert computed_return.computed.total_payments == Decimal("6500.00")
        # If state_tax_withheld leaked into federal payments, this would be
        # 6500 + 921 + 1732.50 = 9153.50 (catastrophic).
        assert computed_return.computed.total_payments != Decimal("9153.50")
