"""New Jersey state plugin tests.

Mirrors the California test suite (test_state_ca.py) since the NJ plugin
wraps tenforty/OpenTaxSolver the same way. Reference numbers were captured
by running `tenforty.evaluate_return(year=2025, state='NJ', ...)` with the
Single / $65k W-2 / Standard scenario:

    state_total_tax=2042.0, state_tax_bracket=5.5,
    state_taxable_income=64000.0, state_adjusted_gross_income=65000.0,
    state_effective_tax_rate=3.2

NJ's only bilateral reciprocity partner is PA — that assertion is
load-bearing for the multi-state handling logic elsewhere in the skill.
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
from skill.scripts.states.nj import (
    NJ1040Fields,
    PLUGIN,
    NewJerseyPlugin,
    _build_nj1040_fields,
    _split_money_to_digits,
)


# ---------------------------------------------------------------------------
# Shared fixtures — a Single $65k W-2 NJ resident return
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
            street1="1 Broad St", city="Newark", state="NJ", zip="07102"
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
    # Matches the CP4 Single $65k W-2 scenario used in the CA suite.
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


class TestNewJerseyPluginMeta:
    def test_protocol_satisfied_at_runtime(self):
        """runtime_checkable Protocol must recognize our concrete plugin."""
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_new_jersey_plugin_instance(self):
        assert isinstance(PLUGIN, NewJerseyPlugin)

    def test_meta_code_is_nj(self):
        assert PLUGIN.meta.code == "NJ"

    def test_meta_name(self):
        assert PLUGIN.meta.name == "New Jersey"

    def test_meta_has_income_tax(self):
        assert PLUGIN.meta.has_income_tax is True

    def test_meta_starting_point_is_federal_agi(self):
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI

    def test_meta_submission_channel(self):
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )

    def test_meta_dor_url(self):
        assert "nj.gov/treasury/taxation" in PLUGIN.meta.dor_url

    def test_meta_free_efile_url_present(self):
        assert PLUGIN.meta.free_efile_url is not None
        assert PLUGIN.meta.free_efile_url.startswith("http")

    def test_meta_reciprocity_partners_is_pa_only(self):
        """NJ's single bilateral reciprocity partner is Pennsylvania — this
        assertion is load-bearing for the skill's multi-state logic."""
        assert PLUGIN.meta.reciprocity_partners == ("PA",)

    def test_meta_reciprocity_is_single_partner(self):
        """Double-check: exactly one partner, not zero and not more."""
        assert len(PLUGIN.meta.reciprocity_partners) == 1

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_pa_commuter(self):
        """Notes should flag the NJ-PA commuter exemption pathway."""
        assert "PA" in PLUGIN.meta.notes

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "NY"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# compute() — resident case matches NJ tenforty reference numbers
# ---------------------------------------------------------------------------


class TestNewJerseyPluginComputeResident:
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

    def test_state_code_is_nj(self, single_65k_return, federal_single_65k):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state == "NJ"

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
        """Tenforty reference: Single $65k W-2 → NJ state_total_tax = $2,042."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        assert state_tax == Decimal("2042.00")

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

    def test_state_tax_bracket_5_5pct(
        self, single_65k_return, federal_single_65k
    ):
        """Tenforty reference: NJ bracket is 5.5 at $65k Single."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_tax_bracket"] == Decimal("5.5")

    def test_state_taxable_income_matches_tenforty(
        self, single_65k_return, federal_single_65k
    ):
        """Tenforty reference: NJ state_taxable_income = $64,000."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal(
            "64000.00"
        )

    def test_state_agi_matches_federal_agi(
        self, single_65k_return, federal_single_65k
    ):
        """NJ starting point is federal AGI."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_adjusted_gross_income"] == Decimal(
            "65000.00"
        )

    def test_state_effective_rate_present(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        # ~3.2% effective rate at this income level per tenforty.
        assert result.state_specific["state_effective_tax_rate"] > Decimal("0")

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
        assert rehydrated.state == "NJ"
        assert rehydrated.residency == ResidencyStatus.RESIDENT


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment
# ---------------------------------------------------------------------------


class TestNewJerseyPluginComputeNonresident:
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
        # 182/365 ≈ 0.4986. Full is 2042.00, so apportioned ≈ 1018.14.
        assert apportioned < full
        expected = (full * Decimal(182) / Decimal(365)).quantize(Decimal("0.01"))
        assert apportioned == expected
        # Sanity: "roughly half"
        assert Decimal("980") < apportioned < Decimal("1060")

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


class TestNewJerseyPluginApportionIncome:
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


class TestNewJerseyPluginFormIds:
    def test_form_ids_returns_nj_1040(self):
        assert PLUGIN.form_ids() == ["NJ Form NJ-1040"]

    def test_render_pdfs_produces_pdf(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """NJ-1040 AcroForm digit-by-digit fill produces a non-empty PDF."""
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
        assert paths[0].name == "nj_1040.pdf"

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

    def test_render_pdfs_field_values(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Verify that rendered NJ-1040 PDF contains correct digit values.

        Line 15 (Gross Income = $65,000.00) should fill the 10 digit cells
        with the individual characters of '0006500000' (8 dollar + 2 cent
        digits), with leading zeros blanked.
        """
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
        fields = reader.get_fields()
        assert fields is not None

        # Line 15 cells for $65,000.00:
        # 10 cells = 8 dollar + 2 cent
        # Dollar digits: "00065000" -> positions 0-7
        # Cent digits: "00" -> positions 8-9
        # Positions 0-2 are leading zeros (blanked)
        # Position 3 (undefined_38) = "6"
        # Position 4 (Text100) = "5"
        # Positions 5-7 = "000"
        # Positions 8-9 = "00" (cents)
        assert fields["undefined_38"].get("/V") == "6"
        assert fields["Text100"].get("/V") == "5"
        assert fields["Text106"].get("/V") == "0"  # last cent digit


# ---------------------------------------------------------------------------
# _split_money_to_digits() unit tests
# ---------------------------------------------------------------------------


class TestSplitMoneyToDigits:
    """Unit tests for the digit-by-digit helper that powers NJ-1040
    rendering. The NJ PDF uses single-character text widgets for all
    monetary amounts."""

    def test_basic_split_10_cells(self):
        """$65,000.00 into 10 cells: 8 dollar + 2 cent."""
        cells = ["c0", "c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9"]
        result = _split_money_to_digits(Decimal("65000.00"), cells)
        # 8 dollar digits: 00065000, 2 cent digits: 00
        # Leading zeros blanked
        assert result["c0"] == ""       # leading zero blanked
        assert result["c1"] == ""       # leading zero blanked
        assert result["c2"] == ""       # leading zero blanked
        assert result["c3"] == "6"
        assert result["c4"] == "5"
        assert result["c5"] == "0"
        assert result["c6"] == "0"
        assert result["c7"] == "0"
        assert result["c8"] == "0"      # cents
        assert result["c9"] == "0"      # cents

    def test_zero_amount_all_blank(self):
        """Zero amounts should produce empty strings for all cells."""
        cells = ["a", "b", "c", "d", "e"]
        result = _split_money_to_digits(Decimal("0"), cells)
        assert all(v == "" for v in result.values())

    def test_none_amount_all_blank(self):
        """None amounts should produce empty strings for all cells."""
        cells = ["a", "b", "c"]
        result = _split_money_to_digits(None, cells)
        assert all(v == "" for v in result.values())

    def test_cents_present(self):
        """$2042.50 should show 50 in the cents cells."""
        cells = ["d0", "d1", "d2", "d3", "d4", "d5", "d6", "d7", "c0", "c1"]
        result = _split_money_to_digits(Decimal("2042.50"), cells)
        assert result["d4"] == "2"
        assert result["d5"] == "0"
        assert result["d6"] == "4"
        assert result["d7"] == "2"
        assert result["c0"] == "5"
        assert result["c1"] == "0"

    def test_small_amount(self):
        """$1.23 into 5 cells (3 dollar + 2 cent)."""
        cells = ["a", "b", "c", "d", "e"]
        result = _split_money_to_digits(Decimal("1.23"), cells)
        assert result["a"] == ""       # leading zero
        assert result["b"] == ""       # leading zero
        assert result["c"] == "1"
        assert result["d"] == "2"      # cents
        assert result["e"] == "3"      # cents

    def test_returns_dict_with_all_keys(self):
        """Every widget name must appear in the result dict."""
        cells = ["x1", "x2", "x3", "x4"]
        result = _split_money_to_digits(Decimal("99.99"), cells)
        assert set(result.keys()) == set(cells)


# ---------------------------------------------------------------------------
# NJ1040Fields dataclass
# ---------------------------------------------------------------------------


class TestNJ1040Fields:
    def test_build_from_state_return(
        self, single_65k_return, federal_single_65k
    ):
        state_return = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        fields = _build_nj1040_fields(state_return)
        assert isinstance(fields, NJ1040Fields)
        assert fields.state_adjusted_gross_income == Decimal("65000.00")
        assert fields.state_taxable_income == Decimal("64000.00")
        assert fields.state_total_tax == Decimal("2042.00")
