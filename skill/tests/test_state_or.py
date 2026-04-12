"""Oregon state plugin tests.

Covers the OregonPlugin wrapping tenforty's OR pass-through. Reference
scenario mirrors CP4 shape: Single / $65k W-2 / Standard. tenforty returns
for OR at this scenario:

    state_total_tax = 4370.00
    state_tax_bracket = 8.8  (OR is graduated 4.75/6.75/8.75/9.9% TY2025)
    state_taxable_income = 56410.00
    state_adjusted_gross_income = 65000.00
    state_effective_tax_rate = 8.2

Also exercises the filename-underscore convention: `or` is a Python keyword
so the module lives at `skill/scripts/states/or_.py`.

Test structure mirrors `test_state_az.py`.
"""
from __future__ import annotations

import datetime as dt
import importlib
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
from skill.scripts.states.or_ import PLUGIN, OregonPlugin


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
            street1="1 SW Main St", city="Portland", state="OR", zip="97204"
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
# Metadata and protocol conformance
# ---------------------------------------------------------------------------


class TestOregonPluginMeta:
    def test_meta_fields(self):
        """Core meta contract: code, starting point, reciprocity."""
        assert PLUGIN.meta.code == "OR"
        assert PLUGIN.meta.name == "Oregon"
        assert PLUGIN.meta.has_income_tax is True
        assert (
            PLUGIN.meta.starting_point
            == StateStartingPoint.FEDERAL_TAXABLE_INCOME
        )
        assert PLUGIN.meta.reciprocity_partners == ()
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.FED_STATE_PIGGYBACK
        )
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_urls(self):
        assert "oregon.gov" in PLUGIN.meta.dor_url
        assert PLUGIN.meta.free_efile_url is not None
        assert "oregon.gov" in PLUGIN.meta.free_efile_url

    def test_meta_notes_mentions_tenforty(self):
        assert "tenforty" in PLUGIN.meta.notes.lower()

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "CA"  # type: ignore[misc]

    def test_plugin_is_state_plugin_protocol(self):
        """runtime_checkable Protocol must recognize our concrete plugin."""
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_oregon_plugin_instance(self):
        assert isinstance(PLUGIN, OregonPlugin)


# ---------------------------------------------------------------------------
# compute() — resident case
# ---------------------------------------------------------------------------


class TestOregonPluginComputeResident:
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
        assert result.state == "OR"
        assert result.residency == ResidencyStatus.RESIDENT
        assert result.days_in_state == 365

    def test_resident_65k_single(self, single_65k_return, federal_single_65k):
        """tenforty reference: Single $65k W-2 → OR state_total_tax = $4,370.00."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_total_tax"] == Decimal("4370.00")

    def test_state_taxable_income_matches_reference(
        self, single_65k_return, federal_single_65k
    ):
        """tenforty reference: OR state_taxable_income = $56,410.00."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal(
            "56410.00"
        )

    def test_state_agi_matches_federal_agi(
        self, single_65k_return, federal_single_65k
    ):
        """OR state AGI mirrors federal AGI on this simple scenario."""
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
        assert rehydrated.state == "OR"
        assert rehydrated.residency == ResidencyStatus.RESIDENT


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment
# ---------------------------------------------------------------------------


class TestOregonPluginComputeNonresident:
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
        assert full == Decimal("4370.00")
        assert apportioned < full
        expected = (full * Decimal(182) / Decimal(365)).quantize(Decimal("0.01"))
        assert apportioned == expected
        # Sanity: "roughly half" of 4370.00 ~= 2179.00
        assert Decimal("2100") < apportioned < Decimal("2250")

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


class TestOregonPluginApportionIncome:
    def test_apportion_income_resident(self, single_65k_return):
        """Residents: full wages sourced to OR; other categories zero on
        this wage-only return."""
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

    def test_apportion_income_nonresident(self, single_65k_return):
        """Nonresident with 182 days → wages prorated 182/365."""
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.NONRESIDENT, days_in_state=182
        )
        expected = (Decimal("65000") * Decimal(182) / Decimal(365)).quantize(
            Decimal("0.01")
        )
        assert app.state_source_wages == expected
        # Other categories still zero on this return.
        assert app.state_source_interest == Decimal("0")


# ---------------------------------------------------------------------------
# render_pdfs() and form_ids()
# ---------------------------------------------------------------------------


class TestOregonPluginFormIds:
    def test_form_ids(self):
        assert PLUGIN.form_ids() == ["OR Form OR-40"]

    def test_render_pdfs_produces_pdf(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """OR Form OR-40 AcroForm fill produces a non-empty PDF."""
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
        paths = PLUGIN.render_pdfs(state_return, tmp_path)
        assert len(paths) == 1
        assert paths[0].exists()
        assert paths[0].stat().st_size > 0
        assert paths[0].name == "or_40.pdf"

    def test_render_pdfs_output_is_valid_pdf(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """The rendered PDF must be openable by pypdf."""
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
        paths = PLUGIN.render_pdfs(state_return, tmp_path)
        reader = PdfReader(str(paths[0]))
        assert len(reader.pages) >= 1


# ---------------------------------------------------------------------------
# Filename-underscore convention
# ---------------------------------------------------------------------------


class TestOregonFilenameConvention:
    def test_filename_underscore_convention(self):
        """`or` is a Python reserved keyword, so the module is named `or_`.

        Verify the module is importable under its canonical dotted path and
        that __name__ reports the same path.
        """
        module = importlib.import_module("skill.scripts.states.or_")
        assert module.__name__ == "skill.scripts.states.or_"
        # PLUGIN and OregonPlugin must be re-discoverable through importlib too.
        assert hasattr(module, "PLUGIN")
        assert hasattr(module, "OregonPlugin")
        assert module.PLUGIN is PLUGIN
        assert module.OregonPlugin is OregonPlugin
