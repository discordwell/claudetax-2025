"""North Carolina state plugin tests.

Covers the NorthCarolinaPlugin wrapping tenforty's NC pass-through. Reference
scenario mirrors CP4 shape: Single / $65k W-2 / Standard. tenforty returns
for NC at this scenario:

    state_total_tax = 2220.62
    state_tax_bracket = 0.0  (NC runs a 4.25% flat rate in TY2025)
    state_taxable_income = 52250.0  (federal AGI - NC standard deduction 12,750)
    state_adjusted_gross_income = 65000.0
    state_effective_tax_rate = 0.0

Math check: 65,000 - 12,750 = 52,250; 52,250 * 0.0425 = 2,220.625 → $2,220.62.

Test structure mirrors `test_state_az.py`.
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
    StatePlugin,
    StateStartingPoint,
    SubmissionChannel,
)
from skill.scripts.states.nc import PLUGIN, NorthCarolinaPlugin


# ---------------------------------------------------------------------------
# Shared fixtures — a CP4-style Single $65k W-2 return
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
            street1="1 Bicentennial Plaza",
            city="Raleigh",
            state="NC",
            zip="27601",
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
    # Matches the CP4 Single $65k W-2 scenario.
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


class TestNorthCarolinaPluginMeta:
    def test_plugin_is_state_plugin_protocol(self):
        """runtime_checkable Protocol must recognize our concrete plugin."""
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_north_carolina_plugin_instance(self):
        assert isinstance(PLUGIN, NorthCarolinaPlugin)

    def test_meta_fields(self):
        """Bundled assertion on the core meta fields per task spec."""
        assert PLUGIN.meta.code == "NC"
        assert PLUGIN.meta.name == "North Carolina"
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.FED_STATE_PIGGYBACK
        )
        assert PLUGIN.meta.reciprocity_partners == ()
        assert PLUGIN.meta.has_income_tax is True
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_urls(self):
        assert "ncdor.gov" in PLUGIN.meta.dor_url
        assert PLUGIN.meta.free_efile_url is not None
        assert "ncdor.gov" in PLUGIN.meta.free_efile_url

    def test_meta_notes_mentions_tenforty(self):
        assert "tenforty" in PLUGIN.meta.notes.lower()

    def test_meta_notes_mentions_flat_rate(self):
        assert "4.25" in PLUGIN.meta.notes

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "NY"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# compute() — resident case
# ---------------------------------------------------------------------------


class TestNorthCarolinaPluginComputeResident:
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

    def test_state_code_is_nc(self, single_65k_return, federal_single_65k):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state == "NC"

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

    def test_resident_65k_single(
        self, single_65k_return, federal_single_65k
    ):
        """tenforty reference: Single $65k W-2 → NC state_total_tax = $2,220.62.

        NC adopted a 4.25% flat rate in TY2025 (per NCDOR tax-rate schedules
        https://www.ncdor.gov/taxes-forms/tax-rate-schedules). NC single
        standard deduction is $12,750, so NC TI = 65,000 - 12,750 = 52,250
        and NC tax = 52,250 * 0.0425 = 2,220.625 → $2,220.62.

        Bit-for-bit match against tenforty via `_d(tf_result.state_total_tax)`.
        """
        tf_result = tenforty.evaluate_return(
            year=2025,
            state="NC",
            filing_status="Single",
            w2_income=65000,
            standard_or_itemized="Standard",
        )
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        from skill.scripts.states.nc import _d

        expected = _d(tf_result.state_total_tax).quantize(Decimal("0.01"))
        assert result.state_specific["state_total_tax"] == expected
        # Also pin the absolute value to guard against tenforty drift.
        assert result.state_specific["state_total_tax"] == Decimal("2220.62")

    def test_state_taxable_income_matches_reference(
        self, single_65k_return, federal_single_65k
    ):
        """tenforty reference: NC state_taxable_income = $52,250 (= 65,000 - 12,750)."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal(
            "52250.00"
        )

    def test_state_agi_matches_federal_agi(
        self, single_65k_return, federal_single_65k
    ):
        """NC starting point is federal AGI (D-400 Line 6)."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_adjusted_gross_income"] == Decimal(
            "65000.00"
        )

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
        assert rehydrated.state == "NC"
        assert rehydrated.residency == ResidencyStatus.RESIDENT


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment
# ---------------------------------------------------------------------------


class TestNorthCarolinaPluginComputeNonresident:
    def test_nonresident_half_year_prorates_tax(
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
        expected = (full * Decimal(182) / Decimal(365)).quantize(Decimal("0.01"))
        assert apportioned == expected
        # Sanity: "roughly half" of 2220.62 ~= 1107.15
        assert Decimal("1050") < apportioned < Decimal("1150")

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
        assert result.state_specific["apportionment_fraction"] == expected_fraction

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


class TestNorthCarolinaPluginApportionIncome:
    def test_apportion_income_resident_full_amounts(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert isinstance(app, IncomeApportionment)
        assert app.state_source_wages == Decimal("65000.00")
        # No interest/dividends/etc on this return.
        assert app.state_source_interest == Decimal("0")
        assert app.state_source_dividends == Decimal("0")
        assert app.state_source_capital_gains == Decimal("0")
        assert app.state_source_self_employment == Decimal("0")
        assert app.state_source_rental == Decimal("0")
        assert app.state_source_total == Decimal("65000.00")

    def test_apportion_income_nonresident_prorates(self, single_65k_return):
        """Day-fraction applied to every category for nonresident."""
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.NONRESIDENT, days_in_state=182
        )
        expected_wages = (
            Decimal("65000") * Decimal(182) / Decimal(365)
        ).quantize(Decimal("0.01"))
        assert app.state_source_wages == expected_wages
        # Zero categories stay zero regardless of fraction.
        assert app.state_source_interest == Decimal("0.00")
        assert app.state_source_dividends == Decimal("0.00")


# ---------------------------------------------------------------------------
# render_pdfs() and form_ids()
# ---------------------------------------------------------------------------


class TestNorthCarolinaPluginFormIds:
    def test_form_ids(self):
        """form_ids must include 'NC Form D-400'."""
        ids = PLUGIN.form_ids()
        assert "NC Form D-400" in ids
        assert ids == ["NC Form D-400"]

    def test_render_pdfs_produces_d400(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """render_pdfs should produce a filled NC D-400 PDF."""
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
        assert pdfs[0].name == "NC_D-400.pdf"
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
        # NC D-400 source has 3 pages
        assert len(reader.pages) >= 2

    def test_render_pdfs_field_values(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Verify that rendered NC D-400 PDF contains correct field values."""
        try:
            from pypdf import PdfReader
        except BaseException:
            pytest.skip("pypdf unavailable")

        state_return = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        pdfs = PLUGIN.render_pdfs(state_return, tmp_path)
        reader = PdfReader(str(pdfs[0]))
        fields = reader.get_fields()
        assert fields is not None

        # Widget "y_d400wf_li13_page1_good" maps to NC income tax (line 13)
        assert fields["y_d400wf_li13_page1_good"].get("/V") == "2220.62"
        # Widget "y_d400wf_li12b_pg1_good" maps to NC taxable income (line 12b)
        assert fields["y_d400wf_li12b_pg1_good"].get("/V") == "52250.00"
        # Widget "y_d400wf_li6_good" maps to federal AGI (line 6)
        assert fields["y_d400wf_li6_good"].get("/V") == "65000.00"
