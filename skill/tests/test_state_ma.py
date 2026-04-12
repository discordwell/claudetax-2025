"""Massachusetts state plugin tests.

Covers the MassachusettsPlugin wrapping tenforty's MA pass-through. MA is
unusual: its distinguishing feature is that it does NOT start from federal
AGI — it computes its own gross base (Part A/B/C income). tenforty handles
this internally; our plugin surfaces the `starting_point == STATE_GROSS`
metadata so downstream consumers can branch.

Reference numbers are taken from a direct probe against tenforty's TY2025 MA
path for the Single / $65k W-2 / Standard scenario:

    tenforty.evaluate_return(
        year=2025, state='MA', filing_status='Single',
        w2_income=65000, standard_or_itemized='Standard',
    )
    -> state_total_tax=3030.0, state_taxable_income=60600.0,
       state_adjusted_gross_income=65000.0

Test structure mirrors `test_state_ca.py`.
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
from skill.scripts.states.ma import PLUGIN, MassachusettsPlugin


# ---------------------------------------------------------------------------
# Shared fixtures — a Single $65k W-2 return (mirror the CA fixture shape)
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
            street1="1 Beacon St", city="Boston", state="MA", zip="02108"
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
    # Mirrors the shared CP4 Single $65k W-2 scenario.
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


class TestMassachusettsPluginMeta:
    def test_protocol_satisfied_at_runtime(self):
        """runtime_checkable Protocol must recognize our concrete plugin."""
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_massachusetts_plugin_instance(self):
        assert isinstance(PLUGIN, MassachusettsPlugin)

    def test_meta_code_is_ma(self):
        assert PLUGIN.meta.code == "MA"

    def test_meta_name(self):
        assert PLUGIN.meta.name == "Massachusetts"

    def test_meta_has_income_tax(self):
        assert PLUGIN.meta.has_income_tax is True

    def test_meta_starting_point_is_state_gross(self):
        """MA's distinguishing feature among tenforty-backed states: it does
        not conform to federal AGI. It computes its own Part A/B/C base."""
        assert PLUGIN.meta.starting_point == StateStartingPoint.STATE_GROSS

    def test_meta_submission_channel(self):
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )

    def test_meta_dor_url(self):
        assert "mass.gov" in PLUGIN.meta.dor_url

    def test_meta_free_efile_url(self):
        assert PLUGIN.meta.free_efile_url is not None
        # MassTaxConnect lives at mtc.dor.state.ma.us
        assert "mtc.dor.state.ma.us" in PLUGIN.meta.free_efile_url

    def test_meta_no_reciprocity_partners(self):
        """MA has no reciprocity agreements — verified in state-reciprocity.json."""
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_part_abc(self):
        """Notes should document MA's Part A / B / C income structure so
        downstream readers understand why starting_point is STATE_GROSS."""
        notes = PLUGIN.meta.notes
        assert "Part A" in notes
        assert "Part B" in notes
        assert "Part C" in notes

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "NY"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# compute() — resident case matches tenforty reference numbers
# ---------------------------------------------------------------------------


class TestMassachusettsPluginComputeResident:
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

    def test_state_code_is_ma(self, single_65k_return, federal_single_65k):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state == "MA"

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
        """tenforty probe: Single $65k W-2 → MA state_total_tax = $3,030."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        assert state_tax == Decimal("3030.00")

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
        """tenforty probe: MA state_taxable_income = $60,600 (Part B after
        personal exemption / deductions)."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal(
            "60600.00"
        )

    def test_state_agi_present(
        self, single_65k_return, federal_single_65k
    ):
        """MA's 'state AGI' in tenforty's output is the state gross base.
        For this scenario tenforty reports 65000."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_adjusted_gross_income"] == Decimal(
            "65000.00"
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
        assert rehydrated.state == "MA"
        assert rehydrated.residency == ResidencyStatus.RESIDENT


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment
# ---------------------------------------------------------------------------


class TestMassachusettsPluginComputeNonresident:
    def test_nonresident_half_year_tax_roughly_half(
        self, single_65k_return, federal_single_65k
    ):
        """NONRESIDENT with days_in_state=182 should yield ~1/2 the
        resident-basis tax via day-based proration (MA Form 1-NR/PY proper
        sourcing is a TODO)."""
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
        # Sanity: "roughly half" of 3030 ≈ 1510.
        assert Decimal("1450") < apportioned < Decimal("1575")

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


class TestMassachusettsPluginApportionIncome:
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
        expected = (Decimal("65000") * Decimal(182) / Decimal(365)).quantize(
            Decimal("0.01")
        )
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


class TestMassachusettsPluginFormIds:
    def test_form_ids_returns_form_1(self):
        assert PLUGIN.form_ids() == ["MA Form 1"]

    def test_render_pdfs_produces_filled_pdf(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """render_pdfs should produce a filled MA Form 1 PDF."""
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
        result = PLUGIN.render_pdfs(state_return, tmp_path)
        assert len(result) == 1
        pdf_path = result[0]
        assert pdf_path.exists()
        assert pdf_path.stat().st_size > 0
        assert pdf_path.name == "MA-Form-1.pdf"

    def test_render_pdfs_output_has_form_fields(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Rendered PDF should still have AcroForm fields (not flattened)."""
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
        result = PLUGIN.render_pdfs(state_return, tmp_path)
        reader = PdfReader(str(result[0]))
        fields = reader.get_fields()
        assert fields is not None
        assert len(fields) > 0
