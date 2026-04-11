"""New Mexico state plugin tests — TY2025.

Wraps tenforty's graph backend for NM Form PIT-1; the graph value at the
$65k Single scenario reconciles bit-for-bit against the DOR primary
source. Mirrors the WI plugin test shape.

Reference scenario (verified 2026-04-11 via direct tenforty probe,
graph backend, 2025):

    Single / $65,000 W-2 / Standard
      -> state_total_tax            = 1905.75
         state_adjusted_gross_inc   = 65000.00
         state_taxable_income       = 49250.00 (graph echoes federal TI)

DOR hand-calc on the same scenario:
    NM TI = $49,250
    1.5%  *  5,500 =    82.50
    3.2%  * 11,000 =   352.00
    4.3%  * 17,000 =   731.00
    4.7%  * 15,750 =   740.25
                    --------
    Total              1,905.75   ✓ matches graph
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
from skill.scripts.states.nm import PLUGIN, NewMexicoPlugin


@pytest.fixture
def single_65k_return() -> CanonicalReturn:
    """A Single $65k W-2 NM resident — the spec's wrap-correctness lock."""
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=Person(
            first_name="Lucinda",
            last_name="Nuevo",
            ssn="111-22-3333",
            date_of_birth=dt.date(1990, 1, 1),
        ),
        address=Address(
            street1="1100 St Francis Dr",
            city="Santa Fe",
            state="NM",
            zip="87505",
        ),
        w2s=[
            W2(
                employer_name="Land of Enchantment Co",
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
# Meta + Protocol
# ---------------------------------------------------------------------------


class TestNewMexicoPluginMeta:
    def test_meta_fields(self):
        assert PLUGIN.meta.code == "NM"
        assert PLUGIN.meta.name == "New Mexico"
        assert PLUGIN.meta.has_income_tax is True
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI

    def test_meta_no_reciprocity(self):
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_meta_submission_channel(self):
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.FED_STATE_PIGGYBACK
        )

    def test_meta_dor_url(self):
        assert "tax.newmexico.gov" in PLUGIN.meta.dor_url

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_brackets(self):
        notes = PLUGIN.meta.notes
        assert "1.5" in notes
        assert "5.9" in notes

    def test_meta_notes_mention_graph_backend(self):
        assert "graph" in PLUGIN.meta.notes.lower()

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "AZ"  # type: ignore[misc]

    def test_plugin_is_state_plugin_protocol(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_new_mexico_plugin_instance(self):
        assert isinstance(PLUGIN, NewMexicoPlugin)


# ---------------------------------------------------------------------------
# Reciprocity
# ---------------------------------------------------------------------------


class TestNewMexicoNoReciprocity:
    def test_meta_partners_empty(self):
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_no_reciprocity_via_table(self):
        table = ReciprocityTable.load()
        assert table.partners_of("NM") == frozenset()
        assert table.has_income_tax("NM") is True

    def test_not_reciprocal_with_neighbors(self):
        """NM borders TX, OK, AZ, UT, CO. Spot-check none are reciprocal."""
        table = ReciprocityTable.load()
        for neighbor in ("TX", "OK", "AZ", "UT", "CO"):
            assert table.are_reciprocal("NM", neighbor) is False


# ---------------------------------------------------------------------------
# compute() — resident
# ---------------------------------------------------------------------------


class TestNewMexicoPluginComputeResident:
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
        assert result.state == "NM"
        assert result.residency == ResidencyStatus.RESIDENT
        assert result.days_in_state == 365

    def test_resident_single_65k_lock(
        self, single_65k_return, federal_single_65k
    ):
        """**SPEC-MANDATED $65k Single LOCK**: NM tax = $1,905.75.

        Verified against the DOR primary source (NM TRD PIT-1
        instructions; HB 252 brackets) by hand:

            NM TI = $49,250
            1.5%  *  5,500 =    82.50
            3.2%  * 11,000 =   352.00
            4.3%  * 17,000 =   731.00
            4.7%  * 15,750 =   740.25
                              --------
                              1,905.75
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_total_tax"] == Decimal("1905.75")
        assert result.state_specific[
            "state_total_tax_resident_basis"
        ] == Decimal("1905.75")

    def test_resident_65k_matches_tenforty_graph_directly(
        self, single_65k_return, federal_single_65k
    ):
        """Cross-check the wrap against a direct graph-backend probe."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        direct = tenforty.evaluate_return(
            year=2025,
            state="NM",
            filing_status="Single",
            w2_income=65000,
            standard_or_itemized="Standard",
            backend="graph",
        )
        assert Decimal(str(direct.state_total_tax)).quantize(
            Decimal("0.01")
        ) == result.state_specific["state_total_tax"]

    def test_state_specific_decimal_fields(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        for k in (
            "state_adjusted_gross_income",
            "state_taxable_income",
            "state_total_tax",
            "state_total_tax_resident_basis",
            "state_tax_bracket",
            "state_effective_tax_rate",
            "apportionment_fraction",
        ):
            assert k in result.state_specific
            assert isinstance(result.state_specific[k], Decimal)

    def test_apportionment_one_for_resident(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["apportionment_fraction"] == Decimal("1")

    def test_state_return_validates(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        rehydrated = StateReturn.model_validate(
            result.model_dump(mode="json")
        )
        assert rehydrated.state == "NM"


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year
# ---------------------------------------------------------------------------


class TestNewMexicoPluginComputeNonresident:
    def test_nonresident_half_year_prorates(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=182,
        )
        full = result.state_specific["state_total_tax_resident_basis"]
        apportioned = result.state_specific["state_total_tax"]
        assert full == Decimal("1905.75")
        expected = (full * Decimal(182) / Decimal(365)).quantize(
            Decimal("0.01")
        )
        assert apportioned == expected

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


# ---------------------------------------------------------------------------
# apportion_income()
# ---------------------------------------------------------------------------


class TestNewMexicoApportionIncome:
    def test_resident_full_amounts(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert isinstance(app, IncomeApportionment)
        assert app.state_source_wages == Decimal("65000.00")
        assert app.state_source_total == Decimal("65000.00")

    def test_nonresident_prorates(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.NONRESIDENT, days_in_state=182
        )
        expected = (
            Decimal("65000") * Decimal(182) / Decimal(365)
        ).quantize(Decimal("0.01"))
        assert app.state_source_wages == expected


# ---------------------------------------------------------------------------
# render_pdfs / form_ids
# ---------------------------------------------------------------------------


class TestNewMexicoFormIds:
    def test_form_ids(self):
        assert PLUGIN.form_ids() == ["NM Form PIT-1"]

    def test_render_pdfs_empty(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        sr = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert PLUGIN.render_pdfs(sr, tmp_path) == []

    def test_render_pdfs_accepts_path(
        self, single_65k_return, federal_single_65k
    ):
        sr = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert PLUGIN.render_pdfs(sr, Path("/tmp")) == []


# ---------------------------------------------------------------------------
# Gatekeeper — pin the OTS-default-backend FAIL invariant
# ---------------------------------------------------------------------------


class TestNewMexicoTenfortyGapGatekeeper:
    """When tenforty fixes the default backend for NM, the second
    assertion below STARTS FAILING and the next state agent must
    decide whether to convert this graph-wrapper to an OTS-backend
    wrapper. The first assertion locks the graph value to drift.
    """

    def test_tenforty_default_backend_still_raises_for_nm(self):
        with pytest.raises(ValueError, match="OTS does not support"):
            tenforty.evaluate_return(
                year=2025,
                state="NM",
                filing_status="Single",
                w2_income=65000,
                num_dependents=0,
                standard_or_itemized="Standard",
                itemized_deductions=0,
            )

    def test_tenforty_graph_backend_returns_lock_value(self):
        r = tenforty.evaluate_return(
            year=2025,
            state="NM",
            filing_status="Single",
            w2_income=65000,
            num_dependents=0,
            standard_or_itemized="Standard",
            itemized_deductions=0,
            backend="graph",
        )
        assert Decimal(str(r.state_total_tax)).quantize(
            Decimal("0.01")
        ) == Decimal("1905.75")
