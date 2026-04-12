"""CP5 — state plugin API, reciprocity table, and no-income-tax reference plugin.

Tests cover:
- Reciprocity table loads, has 30 bilateral pairs, is symmetric, names the right states
- No-income-tax plugin implements the StatePlugin Protocol
- Registry discovers all 8 no-income-tax plugins
- Plugin compute() on a canonical return returns the expected state-free StateReturn
- Protocol shape is stable (runtime_checkable passes for our reference impl)
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from skill.scripts.models import (
    Address,
    CanonicalReturn,
    FilingStatus,
    Person,
    ResidencyStatus,
    W2,
)
from skill.scripts.states._no_income_tax import (
    ALL_NO_TAX_PLUGINS,
    NoIncomeTaxPlugin,
)
from skill.scripts.states._plugin_api import (
    FederalTotals,
    IncomeApportionment,
    ReciprocityTable,
    StatePlugin,
    StatePluginMeta,
    StateStartingPoint,
    SubmissionChannel,
)
from skill.scripts.states._registry import registry


# ---------------------------------------------------------------------------
# Reciprocity table
# ---------------------------------------------------------------------------


class TestReciprocityTable:
    """The 30 bilateral agreements from Tax Foundation must load correctly."""

    @pytest.fixture
    def table(self) -> ReciprocityTable:
        return ReciprocityTable.load()

    def test_loads_thirty_pairs(self, table):
        assert len(table.pairs) == 30

    def test_bilateral_symmetry_il_ky(self, table):
        assert table.are_reciprocal("IL", "KY")
        assert table.are_reciprocal("KY", "IL")

    def test_self_is_never_reciprocal(self, table):
        assert not table.are_reciprocal("IL", "IL")

    def test_ny_has_no_reciprocity_partners(self, table):
        """NY explicitly has no reciprocity agreements."""
        assert table.partners_of("NY") == frozenset()

    def test_ca_has_no_reciprocity_partners(self, table):
        assert table.partners_of("CA") == frozenset()

    def test_ma_has_no_reciprocity_partners(self, table):
        assert table.partners_of("MA") == frozenset()

    def test_ky_partners(self, table):
        """Kentucky has the most agreements: IL, IN, MI, OH, VA, WI, WV."""
        assert table.partners_of("KY") == frozenset(["IL", "IN", "MI", "OH", "VA", "WI", "WV"])

    def test_pa_partners(self, table):
        """PA: IN, MD, NJ, OH, VA, WV."""
        assert table.partners_of("PA") == frozenset(["IN", "MD", "NJ", "OH", "VA", "WV"])

    def test_nj_only_partner_is_pa(self, table):
        """NJ has exactly one reciprocity agreement (with PA)."""
        assert table.partners_of("NJ") == frozenset(["PA"])

    def test_md_partners_include_dc(self, table):
        """MD's reciprocity with DC is one of the 30 bilateral pairs."""
        assert "DC" in table.partners_of("MD")

    def test_va_partners_include_dc(self, table):
        assert "DC" in table.partners_of("VA")

    def test_no_income_tax_states_list(self, table):
        """Eight states have no individual income tax."""
        expected = frozenset(["AK", "FL", "NV", "NH", "SD", "TN", "TX", "WY"])
        assert table.no_income_tax_states == expected

    def test_wa_is_capital_gains_only(self, table):
        """Washington has a capital gains tax but no broad income tax."""
        assert "WA" in table.capital_gains_only_states
        assert table.taxes_only_capital_gains("WA")

    def test_has_income_tax(self, table):
        assert not table.has_income_tax("FL")
        assert not table.has_income_tax("TX")
        assert table.has_income_tax("CA")
        assert table.has_income_tax("NY")

    def test_dc_universal_exemption(self, table):
        assert table.dc_universal_exemption
        assert table.dc_exempts_nonresident_employee("DC")
        assert not table.dc_exempts_nonresident_employee("CA")


# ---------------------------------------------------------------------------
# StatePlugin Protocol + NoIncomeTax reference implementation
# ---------------------------------------------------------------------------


class TestNoIncomeTaxPlugin:
    @pytest.fixture
    def canonical_return(self) -> CanonicalReturn:
        return CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="Alex",
                last_name="Doe",
                ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
            ),
            address=Address(street1="1 A", city="B", state="FL", zip="33101"),
            w2s=[
                W2(employer_name="Acme", box1_wages=Decimal("60000")),
            ],
        )

    @pytest.fixture
    def federal(self) -> FederalTotals:
        return FederalTotals(
            filing_status=FilingStatus.SINGLE,
            num_dependents=0,
            adjusted_gross_income=Decimal("60000"),
            taxable_income=Decimal("44250"),
            total_federal_tax=Decimal("5075"),
            federal_income_tax=Decimal("5075"),
            federal_standard_deduction=Decimal("15750"),
            federal_itemized_deductions_total=Decimal("0"),
            deduction_taken=Decimal("15750"),
            federal_withholding_from_w2s=Decimal("5000"),
        )

    def test_protocol_satisfied_at_runtime(self):
        """runtime_checkable Protocol must recognize our concrete plugin."""
        plugin = ALL_NO_TAX_PLUGINS["FL"]
        assert isinstance(plugin, StatePlugin)

    def test_all_eight_no_tax_states_present(self):
        """AK, FL, NV, NH, SD, TN, TX, WY — 8 plugins."""
        assert len(ALL_NO_TAX_PLUGINS) == 8
        expected = {"AK", "FL", "NV", "NH", "SD", "TN", "TX", "WY"}
        assert set(ALL_NO_TAX_PLUGINS.keys()) == expected

    def test_wa_is_not_in_no_tax_batch(self):
        """Washington has a capital gains tax and needs its own plugin."""
        assert "WA" not in ALL_NO_TAX_PLUGINS

    def test_plugin_meta_shape(self):
        plugin = ALL_NO_TAX_PLUGINS["TX"]
        assert plugin.meta.code == "TX"
        assert plugin.meta.name == "Texas"
        assert plugin.meta.has_income_tax is False
        assert plugin.meta.starting_point == StateStartingPoint.NONE
        assert plugin.meta.submission_channel == SubmissionChannel.NO_RETURN_REQUIRED
        assert plugin.meta.reciprocity_partners == ()
        assert 2025 in plugin.meta.supported_tax_years

    def test_compute_returns_zero_tax(self, canonical_return, federal):
        plugin = ALL_NO_TAX_PLUGINS["FL"]
        state_return = plugin.compute(
            canonical_return, federal, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert state_return.state == "FL"
        assert state_return.residency == ResidencyStatus.RESIDENT
        assert state_return.state_specific["state_total_tax"] == 0
        assert state_return.state_specific["no_return_required"] is True

    def test_compute_nonresident(self, canonical_return, federal):
        """A non-FL resident working in FL still owes no FL tax."""
        plugin = ALL_NO_TAX_PLUGINS["FL"]
        state_return = plugin.compute(
            canonical_return, federal, ResidencyStatus.NONRESIDENT, days_in_state=180
        )
        assert state_return.residency == ResidencyStatus.NONRESIDENT
        assert state_return.days_in_state == 180
        assert state_return.state_specific["state_total_tax"] == 0

    def test_apportion_income_all_zero(self, canonical_return):
        plugin = ALL_NO_TAX_PLUGINS["FL"]
        apportionment = plugin.apportion_income(
            canonical_return, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert apportionment.state_source_total == Decimal("0")

    def test_render_pdfs_returns_empty(self, canonical_return, federal):
        plugin = ALL_NO_TAX_PLUGINS["FL"]
        state_return = plugin.compute(
            canonical_return, federal, ResidencyStatus.RESIDENT, days_in_state=365
        )
        # Use a dummy path — no files written for no-tax states
        from pathlib import Path

        assert plugin.render_pdfs(state_return, Path("/tmp")) == []

    def test_form_ids_returns_empty(self):
        plugin = ALL_NO_TAX_PLUGINS["AK"]
        assert plugin.form_ids() == []


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestStateRegistry:
    def test_eight_no_tax_states_registered(self):
        for code in ("AK", "FL", "NV", "NH", "SD", "TN", "TX", "WY"):
            assert registry.has(code), f"{code} should be registered"

    def test_fanout_wave_1_taxing_states_registered(self):
        """Fan-out wave 1 landed CA, NY, WA, DC as state plugins."""
        for code in ("CA", "NY", "WA", "DC"):
            assert registry.has(code), f"{code} should be registered"

    def test_fanout_wave_2_taxing_states_registered(self):
        """Fan-out wave 2 landed AZ, MA, MI, NJ, PA, VA as state plugins."""
        for code in ("AZ", "MA", "MI", "NJ", "PA", "VA"):
            assert registry.has(code), f"{code} should be registered"

    def test_unregistered_state_still_raises(self):
        """States not yet implemented should still raise cleanly."""
        with pytest.raises(KeyError, match="no state plugin registered"):
            registry.get("TX_UNKNOWN")  # type: ignore[arg-type]

    def test_get_returns_plugin_instance(self):
        plugin = registry.get("FL")
        assert plugin.meta.code == "FL"

    def test_get_taxing_state_plugin(self):
        plugin = registry.get("CA")
        assert plugin.meta.code == "CA"
        assert plugin.meta.has_income_tax

    def test_codes_are_sorted(self):
        codes = registry.codes()
        assert codes == sorted(codes)

    def test_registry_len(self):
        """8 no-tax + 4 wave-1 + 6 wave-2 + 6 wave-3 + 6 wave-4 + 21 wave-5
        = 51 registered plugins.

        Wave 5 completed coverage of all 50 states + DC. Split: 10 graph-
        backend wraps (AR/HI/IA/ID/LA/MS/MT/NM/SC/VT) where tenforty's
        graph backend reconciled to DOR primary sources, and 11 hand-
        rolled plugins (AL/DE/IN/ME/MO/ND/NE/OK/RI/UT/WV) where the
        graph backend systematically omitted state-specific personal
        exemptions or credits. See skill/reference/tenforty-ty2025-gap.md
        for the per-state probe table and the probe-then-verify-then-
        decide decision rubric.
        """
        assert len(registry) == 51


# ---------------------------------------------------------------------------
# Plugin meta type checks
# ---------------------------------------------------------------------------


class TestPluginMeta:
    def test_meta_is_frozen(self):
        plugin = ALL_NO_TAX_PLUGINS["FL"]
        with pytest.raises(Exception):
            plugin.meta.code = "CA"  # type: ignore[misc]

    def test_submission_channel_enum_values(self):
        assert SubmissionChannel.STATE_DOR_FREE_PORTAL.value == "state_dor_free_portal"
        assert SubmissionChannel.FED_STATE_PIGGYBACK.value == "fed_state_piggyback"
        assert SubmissionChannel.PAPER_ONLY.value == "paper_only"
        assert SubmissionChannel.NO_RETURN_REQUIRED.value == "no_return_required"
