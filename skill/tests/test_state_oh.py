"""Ohio state plugin tests.

Mirrors the AZ / MI plugin test suites. OH is one of the ~10 states tenforty
/ OpenTaxSolver supports natively, so the plugin is a thin wrapper around
``tenforty.evaluate_return(..., state='OH')``.

Reference scenario (verified via direct tenforty probe, 2025):
    Single / $65,000 W-2 / Standard
      -> state_total_tax          = 1413.12
         state_adjusted_gross_inc = 65000.00
         state_taxable_income     = 65000.00
         state_tax_bracket        = 2.8  (graduated: 2.75% bracket at this income)
         state_effective_tax_rate = 2.2  (graduated effective rate)

Unlike flat-rate states (AZ, MI), Ohio runs a graduated bracket schedule for
TY2025:
    $0        - $26,050        0.000%
    $26,050   - $100,000       $342.00 plus 2.750% of excess over $26,050
    over $100,000              $2,394.32 plus 3.125% of excess over $100,000
Source: 2025 Ohio IT 1040 / SD 100 instruction booklet, page 18.

Reciprocity: OH has five reciprocity partners — IN, KY, MI, PA, WV —
verified against skill/reference/state-reciprocity.json.
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
from skill.scripts.states.oh import PLUGIN, OhioPlugin


# ---------------------------------------------------------------------------
# Shared fixtures — a Single $65k W-2 return domiciled in Ohio
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
            street1="30 E Broad St", city="Columbus", state="OH", zip="43215"
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
# Meta / Protocol conformance
# ---------------------------------------------------------------------------


class TestOhioPluginMeta:
    def test_meta_fields(self):
        """Consolidated metadata check covering code, name, starting point,
        submission channel, reciprocity_partners, and has_income_tax flag."""
        assert PLUGIN.meta.code == "OH"
        assert PLUGIN.meta.name == "Ohio"
        assert PLUGIN.meta.has_income_tax is True
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.FED_STATE_PIGGYBACK
        )
        # Reciprocity: exactly IN, KY, MI, PA, WV — verified against
        # skill/reference/state-reciprocity.json.
        assert set(PLUGIN.meta.reciprocity_partners) == {
            "IN",
            "KY",
            "MI",
            "PA",
            "WV",
        }
        assert len(PLUGIN.meta.reciprocity_partners) == 5

    def test_plugin_is_state_plugin_protocol(self):
        """runtime_checkable Protocol must recognize our concrete plugin."""
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_ohio_plugin_instance(self):
        assert isinstance(PLUGIN, OhioPlugin)

    def test_meta_urls(self):
        assert "tax.ohio.gov" in PLUGIN.meta.dor_url
        assert PLUGIN.meta.free_efile_url is not None
        assert "tax.ohio.gov" in PLUGIN.meta.free_efile_url

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_graduated_brackets(self):
        """Notes should document the TY2025 graduated bracket structure."""
        notes = PLUGIN.meta.notes.lower()
        assert "26,050" in PLUGIN.meta.notes or "26050" in PLUGIN.meta.notes
        assert "2.75" in notes
        assert "3.125" in notes

    def test_meta_notes_mentions_tenforty(self):
        assert "tenforty" in PLUGIN.meta.notes.lower()

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "NY"  # type: ignore[misc]

    @pytest.mark.parametrize("partner", ["IN", "KY", "MI", "PA", "WV"])
    def test_meta_reciprocity_contains_each_partner(self, partner):
        assert partner in PLUGIN.meta.reciprocity_partners

    def test_meta_reciprocity_excludes_non_partners(self):
        """A few common neighbors that are NOT OH reciprocity partners."""
        for not_partner in ("CA", "NY", "IL", "FL", "OH"):
            assert not_partner not in PLUGIN.meta.reciprocity_partners


# ---------------------------------------------------------------------------
# compute() — resident case matches tenforty reference numbers
# ---------------------------------------------------------------------------


class TestOhioPluginComputeResident:
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

    def test_state_code_is_oh(self, single_65k_return, federal_single_65k):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state == "OH"

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
        """Verified directly against tenforty: Single / $65k W-2 / Standard
        -> OH state_total_tax = $1,413.12. Pin the plugin's result against
        an independent tenforty call so OpenTaxSolver schedule drift fails
        this test."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        assert state_tax == Decimal("1413.12")

        # Cross-check: direct tenforty probe should agree with the plugin's
        # wrapped result (plugin == tenforty round-tripped through Decimal).
        direct = tenforty.evaluate_return(
            year=2025,
            state="OH",
            filing_status="Single",
            w2_income=65000,
            standard_or_itemized="Standard",
        )
        assert Decimal(str(direct.state_total_tax)) == Decimal("1413.12")

    def test_state_taxable_income_matches_tenforty(
        self, single_65k_return, federal_single_65k
    ):
        """tenforty reports OH state_taxable_income == $65,000 for a simple
        Single $65k W-2 / Standard scenario (Ohio layers its own exemption
        on top of federal AGI, which tenforty's resident path handles
        internally). Keep this pinned so drift fails CI."""
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
        """OH starting point is federal AGI."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_adjusted_gross_income"] == Decimal(
            "65000.00"
        )

    def test_state_tax_bracket_surfaced(
        self, single_65k_return, federal_single_65k
    ):
        """OH has graduated brackets — unlike AZ/MI (flat), tenforty reports
        a nonzero bracket/effective-rate for OH. We pin the bracket value
        reported by the direct tenforty probe (2.8) so the plugin's
        surfacing behavior matches."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        bracket = result.state_specific["state_tax_bracket"]
        assert isinstance(bracket, Decimal)
        assert bracket == Decimal("2.8")

    def test_state_effective_tax_rate_surfaced(
        self, single_65k_return, federal_single_65k
    ):
        """OH effective rate should be the nonzero value tenforty computes
        for the graduated bracket schedule."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        eff = result.state_specific["state_effective_tax_rate"]
        assert isinstance(eff, Decimal)
        assert eff == Decimal("2.2")

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
        assert rehydrated.state == "OH"
        assert rehydrated.residency == ResidencyStatus.RESIDENT


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment
# ---------------------------------------------------------------------------


class TestOhioPluginComputeNonresident:
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
        assert full == Decimal("1413.12")
        assert apportioned < full
        expected = (full * Decimal(182) / Decimal(365)).quantize(
            Decimal("0.01")
        )
        assert apportioned == expected
        # Sanity: "roughly half" of $1,413.12 ≈ $704.55.
        assert Decimal("680") < apportioned < Decimal("730")

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


class TestOhioPluginApportionIncome:
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


class TestOhioPluginFormIds:
    def test_form_ids(self):
        """form_ids() must include the canonical OH IT-1040 identifier."""
        form_ids = PLUGIN.form_ids()
        assert "OH Form IT-1040" in form_ids
        assert form_ids == ["OH Form IT-1040"]

    def test_render_pdfs_returns_empty_list(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Ohio IT-1040 PDF is flattened (0 AcroForm fields, 13 pages).
        Both the original bundle (1040-bundle.pdf) and amended bundle
        have zero fillable fields. render_pdfs returns [] because
        AcroForm filling is not possible. Ohio pushes taxpayers to
        OH|TAX eServices for e-filing.
        See skill/reference/oh-it1040-acroform-map.json for details."""
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
        # Ohio PDF is flattened — render is a no-op.
        assert PLUGIN.render_pdfs(state_return, Path("/tmp")) == []


# ---------------------------------------------------------------------------
# ReciprocityTable consistency
# ---------------------------------------------------------------------------


def test_reciprocity_matches_json():
    """ReciprocityTable.load().partners_of('OH') must equal the plugin's
    meta.reciprocity_partners as a frozenset. This catches drift between
    skill/reference/state-reciprocity.json and the OH plugin."""
    table = ReciprocityTable.load()
    oh_partners_from_table = table.partners_of("OH")
    assert oh_partners_from_table == frozenset({"IN", "KY", "MI", "PA", "WV"})
    # The plugin must expose exactly the same set.
    assert frozenset(PLUGIN.meta.reciprocity_partners) == oh_partners_from_table
