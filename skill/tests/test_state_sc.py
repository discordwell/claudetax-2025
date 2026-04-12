"""South Carolina state plugin tests — TY2025.

Wraps tenforty's graph backend for SC1040; the graph value at the $65k
Single scenario reconciles within $0.30 of the DOR primary source
(SC1040TT row $49,200-$49,300 prints whole-dollar $2,313). Mirrors the
WI / NM plugin test shape.

Reference scenario (verified 2026-04-11 via direct tenforty probe,
graph backend, 2025):

    Single / $65,000 W-2 / Standard
      -> state_total_tax              = 2313.30
         state_taxable_income (graph) = 49250.00
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
from skill.scripts.states.sc import PLUGIN, SouthCarolinaPlugin


@pytest.fixture
def single_65k_return() -> CanonicalReturn:
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=Person(
            first_name="Carolina",
            last_name="Palmetto",
            ssn="111-22-3333",
            date_of_birth=dt.date(1990, 1, 1),
        ),
        address=Address(
            street1="1205 Pendleton St",
            city="Columbia",
            state="SC",
            zip="29201",
        ),
        w2s=[
            W2(
                employer_name="Palmetto State Co",
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


class TestSouthCarolinaPluginMeta:
    def test_meta_fields(self):
        assert PLUGIN.meta.code == "SC"
        assert PLUGIN.meta.name == "South Carolina"
        assert PLUGIN.meta.has_income_tax is True

    def test_meta_starting_point_is_federal_taxable_income(self):
        """SC1040 line 1 is federal taxable income (not federal AGI)."""
        assert (
            PLUGIN.meta.starting_point
            == StateStartingPoint.FEDERAL_TAXABLE_INCOME
        )

    def test_meta_no_reciprocity(self):
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_meta_submission_channel_is_dor_free_portal(self):
        """SC operates MyDORWAY as a free DOR-direct portal."""
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )

    def test_meta_dor_url(self):
        assert "dor.sc.gov" in PLUGIN.meta.dor_url

    def test_meta_free_efile_url_is_mydorway(self):
        assert PLUGIN.meta.free_efile_url is not None
        assert "mydorway" in PLUGIN.meta.free_efile_url.lower()

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_top_rate_6(self):
        notes = PLUGIN.meta.notes
        assert "6" in notes

    def test_meta_notes_mention_graph_backend(self):
        assert "graph" in PLUGIN.meta.notes.lower()

    def test_meta_notes_mention_mydorway(self):
        assert "MyDORWAY" in PLUGIN.meta.notes

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "NC"  # type: ignore[misc]

    def test_plugin_is_state_plugin_protocol(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_south_carolina_plugin_instance(self):
        assert isinstance(PLUGIN, SouthCarolinaPlugin)


# ---------------------------------------------------------------------------
# Reciprocity
# ---------------------------------------------------------------------------


class TestSouthCarolinaNoReciprocity:
    def test_partners_empty(self):
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_no_reciprocity_via_table(self):
        table = ReciprocityTable.load()
        assert table.partners_of("SC") == frozenset()
        assert table.has_income_tax("SC") is True

    def test_not_reciprocal_with_neighbors(self):
        """SC borders NC and GA. Neither shares an SC reciprocity agreement."""
        table = ReciprocityTable.load()
        for neighbor in ("NC", "GA"):
            assert table.are_reciprocal("SC", neighbor) is False


# ---------------------------------------------------------------------------
# compute() — resident
# ---------------------------------------------------------------------------


class TestSouthCarolinaPluginComputeResident:
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
        assert result.state == "SC"

    def test_resident_single_65k_lock(
        self, single_65k_return, federal_single_65k
    ):
        """**SPEC-MANDATED $65k Single LOCK**: SC tax = $2,313.30.

        Verified against the SC1040TT (Rev. 6/17/25): row $49,200-$49,300
        prints whole-dollar $2,313. The graph backend's $2,313.30 is the
        continuous-formula equivalent and is within $0.30 of the printed
        whole-dollar table value (well inside the ±$5 wrap window).
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_total_tax"] == Decimal("2313.30")

    def test_resident_65k_matches_tenforty_graph_directly(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        direct = tenforty.evaluate_return(
            year=2025,
            state="SC",
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
        assert rehydrated.state == "SC"


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year
# ---------------------------------------------------------------------------


class TestSouthCarolinaPluginComputeNonresident:
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
        assert full == Decimal("2313.30")
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


class TestSouthCarolinaApportionIncome:
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


class TestSouthCarolinaFormIds:
    def test_form_ids(self):
        assert PLUGIN.form_ids() == ["SC Form SC1040"]

    def test_render_pdfs_empty(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """SCDOR SC1040 PDF is flattened (no AcroForm widgets).
        render_pdfs() correctly returns [] until a scaffold renderer
        is implemented."""
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
# Gatekeeper
# ---------------------------------------------------------------------------


class TestSouthCarolinaTenfortyGapGatekeeper:
    """When tenforty fixes the default backend for SC, the second
    assertion below STARTS FAILING and the next state agent should
    decide whether to convert this graph-wrapper to an OTS wrapper.
    The first assertion locks the graph value to drift.
    """

    def test_tenforty_default_backend_still_raises_for_sc(self):
        with pytest.raises(ValueError, match="OTS does not support"):
            tenforty.evaluate_return(
                year=2025,
                state="SC",
                filing_status="Single",
                w2_income=65000,
                num_dependents=0,
                standard_or_itemized="Standard",
                itemized_deductions=0,
            )

    def test_tenforty_graph_backend_returns_lock_value(self):
        r = tenforty.evaluate_return(
            year=2025,
            state="SC",
            filing_status="Single",
            w2_income=65000,
            num_dependents=0,
            standard_or_itemized="Standard",
            itemized_deductions=0,
            backend="graph",
        )
        assert Decimal(str(r.state_total_tax)).quantize(
            Decimal("0.01")
        ) == Decimal("2313.30")
