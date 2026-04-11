"""Wisconsin state plugin tests.

Mirrors the OH / NJ / MI plugin test suites. WI is wired up in tenforty
only via the newer graph backend (``wi_form1_2025.json`` ships but the
OTS backend raises ``ValueError: OTS does not support 2025/WI_Form1``),
so the WI plugin passes ``backend='graph'`` to tenforty.evaluate_return.
This test file pins that backend choice.

Reference scenario (verified 2026-04-11 via direct tenforty probe,
graph backend, 2025):
    Single / $65,000 W-2 / Standard
      -> state_total_tax          = 2861.80
         state_adjusted_gross_inc = 65000.00
         state_taxable_income     = 65000.00 (graph backend echoes AGI)
         state_tax_bracket        = 0.0      (graph backend omits)
         state_effective_tax_rate = 0.0      (graph backend omits)

The graph backend's WI form currently echoes AGI into
``state_taxable_income`` rather than subtracting the WI sliding-scale
standard deduction and exemptions; the aggregate ``state_total_tax``
number is nonetheless tenforty's canonical WI output and is what we
lock. TODO(wi-deduction-reconcile) tracks the upstream fix.

Wisconsin brackets (TY2025, Single) per WI DOR Form 1 instructions:
    $0        - $14,320        3.50%
    $14,320   - $28,640        $501.20 + 4.40% of excess
    $28,640   - $315,310       $1,131.28 + 5.30% of excess
    over $315,310              $16,324.79 + 7.65% of excess

Reciprocity: WI has four bilateral partners — IL, IN, KY, MI — per WI
DOR Publication 121 and skill/reference/state-reciprocity.json. MN was a
partner historically but the agreement was terminated effective 2010
and MUST NOT be in the plugin's reciprocity_partners tuple.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

import tenforty

from skill.scripts.models import (
    Address,
    CanonicalReturn,
    FilingStatus,
    Person,
    ResidencyStatus,
    StateReturn,
    W2,
)
from skill.scripts.states._plugin_api import (
    FederalTotals,
    IncomeApportionment,
    ReciprocityTable,
    StatePlugin,
    StateStartingPoint,
    SubmissionChannel,
)
from skill.scripts.states.wi import PLUGIN, WisconsinPlugin


# ---------------------------------------------------------------------------
# Shared fixtures — a Single $65k W-2 return domiciled in Wisconsin
# ---------------------------------------------------------------------------


@pytest.fixture
def single_65k_return() -> CanonicalReturn:
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
            street1="1 W Wilson St", city="Madison", state="WI", zip="53703"
        ),
        w2s=[
            W2(
                employer_name="Acme Corp",
                box1_wages=Decimal("65000"),
                box2_federal_income_tax_withheld=Decimal("0"),
            ),
        ],
    )


@pytest.fixture
def federal_single_65k() -> FederalTotals:
    return FederalTotals(
        filing_status=FilingStatus.SINGLE,
        num_dependents=0,
        adjusted_gross_income=Decimal("65000"),
        taxable_income=Decimal("49250"),
        total_federal_tax=Decimal("5755"),
        federal_income_tax=Decimal("5755"),
        federal_standard_deduction=Decimal("15750"),
        federal_itemized_deductions_total=Decimal("0"),
        deduction_taken=Decimal("15750"),
        federal_withholding_from_w2s=Decimal("0"),
    )


# ---------------------------------------------------------------------------
# Meta / Protocol conformance
# ---------------------------------------------------------------------------


class TestWisconsinPluginMeta:
    def test_meta_fields(self):
        """Consolidated metadata check covering code, name, starting point,
        submission channel, reciprocity_partners, and has_income_tax flag."""
        assert PLUGIN.meta.code == "WI"
        assert PLUGIN.meta.name == "Wisconsin"
        assert PLUGIN.meta.has_income_tax is True
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.FED_STATE_PIGGYBACK
        )
        # Reciprocity: exactly IL, IN, KY, MI — verified against
        # skill/reference/state-reciprocity.json and WI DOR Pub 121.
        assert set(PLUGIN.meta.reciprocity_partners) == {
            "IL",
            "IN",
            "KY",
            "MI",
        }
        assert len(PLUGIN.meta.reciprocity_partners) == 4

    def test_plugin_is_state_plugin_protocol(self):
        """runtime_checkable Protocol must recognize our concrete plugin."""
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_wisconsin_plugin_instance(self):
        assert isinstance(PLUGIN, WisconsinPlugin)

    def test_meta_urls(self):
        assert "revenue.wi.gov" in PLUGIN.meta.dor_url
        assert PLUGIN.meta.free_efile_url is not None
        assert "revenue.wi.gov" in PLUGIN.meta.free_efile_url
        # Explicit WisTax surface in free_efile_url
        assert "WisTax" in PLUGIN.meta.free_efile_url

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_graduated_brackets(self):
        """Notes should document the TY2025 graduated bracket structure."""
        notes = PLUGIN.meta.notes
        # Bracket boundary and top rate are load-bearing for humans
        # reading the plugin metadata.
        assert "14,320" in notes or "14320" in notes
        assert "3.50" in notes
        assert "7.65" in notes

    def test_meta_notes_mentions_tenforty(self):
        assert "tenforty" in PLUGIN.meta.notes.lower()

    def test_meta_notes_mentions_graph_backend(self):
        """The graph-backend caveat is critical — document it in notes so
        humans reading registry output understand why WI is different."""
        assert "graph" in PLUGIN.meta.notes.lower()

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "NY"  # type: ignore[misc]

    @pytest.mark.parametrize("partner", ["IL", "IN", "KY", "MI"])
    def test_meta_reciprocity_contains_each_partner(self, partner):
        assert partner in PLUGIN.meta.reciprocity_partners

    def test_meta_reciprocity_excludes_non_partners(self):
        """WI-MN reciprocity was terminated effective 2010 — MN MUST NOT
        be in the partner set. A few common neighbors also excluded."""
        for not_partner in ("MN", "CA", "NY", "FL", "IA", "OH", "WI"):
            assert not_partner not in PLUGIN.meta.reciprocity_partners

    def test_meta_reciprocity_does_not_include_minnesota(self):
        """Explicit regression guard for the 2010 WI-MN termination.
        If this test fails, someone has (re)added MN — verify against
        WI DOR Publication 121 before modifying."""
        assert "MN" not in PLUGIN.meta.reciprocity_partners


# ---------------------------------------------------------------------------
# compute() — resident case matches tenforty reference numbers
# ---------------------------------------------------------------------------


class TestWisconsinPluginComputeResident:
    def test_compute_returns_state_return(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert isinstance(result, StateReturn)

    def test_state_code_is_wi(self, single_65k_return, federal_single_65k):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state == "WI"

    def test_residency_preserved(self, single_65k_return, federal_single_65k):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.residency == ResidencyStatus.RESIDENT

    def test_days_in_state_preserved(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.days_in_state == 365

    def test_resident_65k_single_lock(
        self, single_65k_return, federal_single_65k
    ):
        """WRAP-CORRECTNESS LOCK: Single / $65k W-2 / Standard
        -> WI state_total_tax = $2,861.80 (tenforty graph backend, 2025).

        Pin the plugin's result bit-for-bit against an independent direct
        tenforty call so OpenTaxSolver schedule drift fails this test.
        This is the load-bearing regression guard for the WI plugin —
        the rest of the compute tests derive from the same scenario."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        assert state_tax == Decimal("2861.80")

        # Cross-check: direct tenforty probe (graph backend) must agree
        # bit-for-bit with the plugin's wrapped result.
        direct = tenforty.evaluate_return(
            year=2025,
            state="WI",
            filing_status="Single",
            w2_income=65000,
            standard_or_itemized="Standard",
            backend="graph",
        )
        assert Decimal(str(direct.state_total_tax)).quantize(
            Decimal("0.01")
        ) == Decimal("2861.80")

    def test_state_taxable_income_matches_tenforty(
        self, single_65k_return, federal_single_65k
    ):
        """The WI graph-backend currently echoes AGI into
        state_taxable_income (it does not yet apply the WI sliding-scale
        standard deduction on the output side). Pin the value so drift
        fails CI; see module docstring TODO(wi-deduction-reconcile)."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal(
            "65000.00"
        )

    def test_state_agi_matches_federal_agi(
        self, single_65k_return, federal_single_65k
    ):
        """WI starting point is federal AGI."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_adjusted_gross_income"] == Decimal(
            "65000.00"
        )

    def test_state_tax_bracket_graph_backend_zero(
        self, single_65k_return, federal_single_65k
    ):
        """The tenforty graph backend does not populate state_tax_bracket
        for WI; it reports 0.0. We surface whatever the backend returns,
        as Decimal. Pin it so any future upstream change (populating a
        real bracket) is caught and the plugin can be updated
        deliberately."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        bracket = result.state_specific["state_tax_bracket"]
        assert isinstance(bracket, Decimal)
        assert bracket == Decimal("0.0")

    def test_state_effective_tax_rate_graph_backend_zero(
        self, single_65k_return, federal_single_65k
    ):
        """Same caveat as state_tax_bracket: graph backend reports 0.0
        for WI effective rate. Pinned to catch upstream changes."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        eff = result.state_specific["state_effective_tax_rate"]
        assert isinstance(eff, Decimal)
        assert eff == Decimal("0.0")

    def test_state_specific_all_decimal_fields(
        self, single_65k_return, federal_single_65k
    ):
        """Every numeric value in state_specific must be Decimal (no floats)."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        numeric_keys = [
            "state_adjusted_gross_income",
            "state_taxable_income",
            "state_total_tax",
            "state_total_tax_resident_basis",
            "state_tax_bracket",
            "state_effective_tax_rate",
            "apportionment_fraction",
        ]
        for key in numeric_keys:
            assert key in result.state_specific, f"missing {key}"
            assert isinstance(
                result.state_specific[key], Decimal
            ), f"{key} is not Decimal"

    def test_resident_apportionment_is_one(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["apportionment_fraction"] == Decimal("1")

    def test_resident_basis_equals_apportioned_for_resident(
        self, single_65k_return, federal_single_65k
    ):
        """For a full-year resident the resident-basis tax and the
        apportioned tax must be identical."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert (
            result.state_specific["state_total_tax"]
            == result.state_specific["state_total_tax_resident_basis"]
        )

    def test_state_return_validates_via_pydantic(
        self, single_65k_return, federal_single_65k
    ):
        """Round-trip through Pydantic JSON to confirm the returned StateReturn
        validates under the canonical model contract."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        dumped = result.model_dump(mode="json")
        rehydrated = StateReturn.model_validate(dumped)
        assert rehydrated.state == "WI"
        assert rehydrated.residency == ResidencyStatus.RESIDENT


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment
# ---------------------------------------------------------------------------


class TestWisconsinPluginComputeNonresident:
    def test_nonresident_half_year_prorates_tax(
        self, single_65k_return, federal_single_65k
    ):
        """NONRESIDENT with days_in_state=182 should yield 182/365 of the
        resident-basis tax via day-based proration."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=182,
        )
        full = result.state_specific["state_total_tax_resident_basis"]
        apportioned = result.state_specific["state_total_tax"]
        assert full == Decimal("2861.80")
        assert apportioned < full
        expected = (full * Decimal(182) / Decimal(365)).quantize(
            Decimal("0.01")
        )
        assert apportioned == expected
        # Sanity: "roughly half" of $2,861.80 ≈ $1,426.86.
        assert Decimal("1400") < apportioned < Decimal("1450")

    def test_nonresident_residency_preserved(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=182,
        )
        assert result.residency == ResidencyStatus.NONRESIDENT
        assert result.days_in_state == 182

    def test_part_year_apportionment_fraction(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.PART_YEAR,
            days_in_state=91,
        )
        expected_fraction = Decimal(91) / Decimal(365)
        assert (
            result.state_specific["apportionment_fraction"]
            == expected_fraction
        )

    def test_zero_days_yields_zero_tax(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=0,
        )
        assert result.state_specific["state_total_tax"] == Decimal("0.00")
        # Resident-basis tax is unchanged (it's the full-year liability).
        assert result.state_specific[
            "state_total_tax_resident_basis"
        ] == Decimal("2861.80")

    def test_full_year_nonresident_equals_resident_tax(
        self, single_65k_return, federal_single_65k
    ):
        """A nonresident with days_in_state=365 prorates to 365/365 = 1,
        i.e. the full resident-basis tax. (Physically unusual, but the
        day-proration helper clamps correctly.)"""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_total_tax"] == Decimal("2861.80")


# ---------------------------------------------------------------------------
# apportion_income()
# ---------------------------------------------------------------------------


class TestWisconsinPluginApportionIncome:
    def test_apportion_income_resident_full_amounts(self, single_65k_return):
        """Residents get full amounts for every canonical income category.
        With a Single $65k W-2 return: wages = $65,000, everything else 0."""
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert isinstance(app, IncomeApportionment)
        assert app.state_source_wages == Decimal("65000.00")
        assert app.state_source_interest == Decimal("0")
        assert app.state_source_dividends == Decimal("0")
        assert app.state_source_capital_gains == Decimal("0")
        assert app.state_source_self_employment == Decimal("0")
        assert app.state_source_rental == Decimal("0")
        assert app.state_source_total == Decimal("65000.00")

    def test_apportion_income_nonresident_prorates(self, single_65k_return):
        """Nonresidents with days_in_state=182 get wages * 182/365."""
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.NONRESIDENT, days_in_state=182
        )
        expected = (
            Decimal("65000") * Decimal(182) / Decimal(365)
        ).quantize(Decimal("0.01"))
        assert app.state_source_wages == expected


# ---------------------------------------------------------------------------
# render_pdfs() and form_ids()
# ---------------------------------------------------------------------------


class TestWisconsinPluginFormIds:
    def test_form_ids(self):
        """form_ids() must include the canonical WI Form 1 identifier."""
        form_ids = PLUGIN.form_ids()
        assert "WI Form 1" in form_ids
        assert form_ids == ["WI Form 1"]

    def test_render_pdfs_returns_empty_list(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Fan-out follow-up: actual Form 1 fill is not yet implemented."""
        state_return = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert PLUGIN.render_pdfs(state_return, tmp_path) == []

    def test_render_pdfs_accepts_path(
        self, single_65k_return, federal_single_65k
    ):
        state_return = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        # Even with a nonexistent path, a no-op render should not raise.
        assert PLUGIN.render_pdfs(state_return, Path("/tmp")) == []


# ---------------------------------------------------------------------------
# ReciprocityTable consistency
# ---------------------------------------------------------------------------


def test_reciprocity_matches_json():
    """ReciprocityTable.load().partners_of('WI') must equal the plugin's
    meta.reciprocity_partners as a frozenset. This catches drift between
    skill/reference/state-reciprocity.json and the WI plugin."""
    table = ReciprocityTable.load()
    wi_partners_from_table = table.partners_of("WI")
    assert wi_partners_from_table == frozenset({"IL", "IN", "KY", "MI"})
    # The plugin must expose exactly the same set.
    assert (
        frozenset(PLUGIN.meta.reciprocity_partners) == wi_partners_from_table
    )


def test_reciprocity_table_recognizes_wi_pairs():
    """Each of the four WI partners must satisfy are_reciprocal('WI', X)."""
    table = ReciprocityTable.load()
    for partner in ("IL", "IN", "KY", "MI"):
        assert table.are_reciprocal("WI", partner) is True
    # And MN (terminated 2010) must NOT be reciprocal.
    assert table.are_reciprocal("WI", "MN") is False
