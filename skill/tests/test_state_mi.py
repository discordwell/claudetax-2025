"""Michigan state plugin tests.

Mirrors the CA plugin test suite. MI is one of the 10 states tenforty /
OpenTaxSolver supports natively, so the plugin is a thin wrapper around
`tenforty.evaluate_return(..., state='MI')`.

Reference scenario (verified via direct tenforty probe, 2025):
    Single / $65,000 W-2 / Standard
      -> state_total_tax        = 2762.50
         state_adjusted_gross   = 65000.00
         state_taxable_income   = 65000.00
         state_tax_bracket      = 0.0   (MI is flat-rate, no brackets)
         state_effective_tax    = 0.0   (tenforty does not surface this for MI)

MI is a flat 4.25% state; $65,000 * 4.25% = $2,762.50.

Reciprocity: MI has six reciprocity partners — IL, IN, KY, MN, OH, WI —
verified against skill/reference/state-reciprocity.json.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

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
    StatePlugin,
    StateStartingPoint,
    SubmissionChannel,
)
from skill.scripts.states.mi import PLUGIN, MichiganPlugin


# ---------------------------------------------------------------------------
# Shared fixtures — a Single $65k W-2 return domiciled in Michigan
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
            street1="1 Woodward Ave", city="Detroit", state="MI", zip="48226"
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
    # Matches the CP4 Single $65k W-2 federal scenario.
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
# Protocol conformance + metadata
# ---------------------------------------------------------------------------


class TestMichiganPluginMeta:
    def test_protocol_satisfied_at_runtime(self):
        """runtime_checkable Protocol must recognize our concrete plugin."""
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_michigan_plugin_instance(self):
        assert isinstance(PLUGIN, MichiganPlugin)

    def test_meta_code_is_mi(self):
        assert PLUGIN.meta.code == "MI"

    def test_meta_name(self):
        assert PLUGIN.meta.name == "Michigan"

    def test_meta_has_income_tax(self):
        assert PLUGIN.meta.has_income_tax is True

    def test_meta_starting_point_is_federal_agi(self):
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI

    def test_meta_submission_channel(self):
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )

    def test_meta_urls(self):
        assert "michigan.gov" in PLUGIN.meta.dor_url
        assert PLUGIN.meta.free_efile_url is not None
        assert "michigan.gov" in PLUGIN.meta.free_efile_url

    def test_meta_reciprocity_partners_exact_set(self):
        """MI has six reciprocity partners per state-reciprocity.json:
        IL, IN, KY, MN, OH, WI. This is load-bearing for the multi-state
        filing workflow and must not drift."""
        assert set(PLUGIN.meta.reciprocity_partners) == {
            "IL",
            "IN",
            "KY",
            "MN",
            "OH",
            "WI",
        }
        # Exactly six — catch accidental additions too.
        assert len(PLUGIN.meta.reciprocity_partners) == 6

    @pytest.mark.parametrize("partner", ["IL", "IN", "KY", "MN", "OH", "WI"])
    def test_meta_reciprocity_contains_each_partner(self, partner):
        assert partner in PLUGIN.meta.reciprocity_partners

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_flat_rate(self):
        """Notes should document that MI uses a flat rate so downstream
        consumers can surface it without re-deriving."""
        assert "4.25" in PLUGIN.meta.notes or "flat" in PLUGIN.meta.notes.lower()

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "NY"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# compute() — resident case matches tenforty reference numbers
# ---------------------------------------------------------------------------


class TestMichiganPluginComputeResident:
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

    def test_state_code_is_mi(self, single_65k_return, federal_single_65k):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state == "MI"

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

    def test_state_total_tax_matches_tenforty_reference(
        self, single_65k_return, federal_single_65k
    ):
        """Verified directly against tenforty: Single $65k W-2 ->
        MI state_total_tax = $2,762.50 (flat 4.25% on $65k)."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        assert state_tax == Decimal("2762.50")

    def test_state_tax_is_positive(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_total_tax"] > Decimal("0")

    def test_state_taxable_income_matches_tenforty(
        self, single_65k_return, federal_single_65k
    ):
        """MI has no state standard deduction in this scenario; tenforty
        reports state_taxable_income == AGI."""
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
        """MI starting point is federal AGI."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_adjusted_gross_income"] == Decimal(
            "65000.00"
        )

    def test_state_tax_bracket_key_present(
        self, single_65k_return, federal_single_65k
    ):
        """MI is flat rate — tenforty reports bracket 0.0 because there are
        no brackets. Still expose the key for consumer uniformity."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert "state_tax_bracket" in result.state_specific
        assert isinstance(
            result.state_specific["state_tax_bracket"], Decimal
        )

    def test_state_effective_rate_key_present(
        self, single_65k_return, federal_single_65k
    ):
        """Tenforty does not always compute an effective rate for flat-rate
        states — we still surface the key for schema uniformity."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert "state_effective_tax_rate" in result.state_specific
        assert isinstance(
            result.state_specific["state_effective_tax_rate"], Decimal
        )

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
        assert rehydrated.state == "MI"
        assert rehydrated.residency == ResidencyStatus.RESIDENT


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment
# ---------------------------------------------------------------------------


class TestMichiganPluginComputeNonresident:
    def test_nonresident_half_year_tax_roughly_half(
        self, single_65k_return, federal_single_65k
    ):
        """NONRESIDENT with days_in_state=182 should yield ~1/2 the
        resident-basis tax via day-based proration."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=182,
        )
        full = result.state_specific["state_total_tax_resident_basis"]
        apportioned = result.state_specific["state_total_tax"]
        assert apportioned < full
        expected = (full * Decimal(182) / Decimal(365)).quantize(
            Decimal("0.01")
        )
        assert apportioned == expected
        # Sanity: "roughly half" of $2,762.50 ≈ $1,377.34.
        assert Decimal("1300") < apportioned < Decimal("1450")

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


# ---------------------------------------------------------------------------
# apportion_income()
# ---------------------------------------------------------------------------


class TestMichiganPluginApportionIncome:
    def test_resident_gets_full_wages(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert isinstance(app, IncomeApportionment)
        assert app.state_source_wages == Decimal("65000.00")

    def test_nonresident_half_wages(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.NONRESIDENT, days_in_state=182
        )
        expected = (
            Decimal("65000") * Decimal(182) / Decimal(365)
        ).quantize(Decimal("0.01"))
        assert app.state_source_wages == expected

    def test_resident_no_interest_or_dividends(self, single_65k_return):
        """No 1099-INT/DIV on this return — apportionment should be zero."""
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert app.state_source_interest == Decimal("0")
        assert app.state_source_dividends == Decimal("0")
        assert app.state_source_capital_gains == Decimal("0")
        assert app.state_source_self_employment == Decimal("0")
        assert app.state_source_rental == Decimal("0")

    def test_resident_total_equals_wages(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert app.state_source_total == Decimal("65000.00")


# ---------------------------------------------------------------------------
# render_pdfs() and form_ids()
# ---------------------------------------------------------------------------


class TestMichiganPluginFormIds:
    def test_form_ids_returns_mi_1040(self):
        assert PLUGIN.form_ids() == ["MI Form MI-1040"]

    def test_render_pdfs_produces_mi1040(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """render_pdfs should produce a filled MI-1040 PDF."""
        state_return = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        try:
            from pypdf import PdfReader
        except BaseException:
            pytest.skip("pypdf unavailable")
        pdfs = PLUGIN.render_pdfs(state_return, tmp_path)
        assert len(pdfs) == 1
        assert pdfs[0].name == "MI_MI-1040.pdf"
        assert pdfs[0].stat().st_size > 0

    def test_render_pdfs_output_is_valid_pdf(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """The output PDF should be a valid PDF that pypdf can open."""
        state_return = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        try:
            from pypdf import PdfReader
        except BaseException:
            pytest.skip("pypdf unavailable")
        pdfs = PLUGIN.render_pdfs(state_return, tmp_path)
        reader = PdfReader(str(pdfs[0]))
        # MI-1040 source has 3 pages
        assert len(reader.pages) >= 2
