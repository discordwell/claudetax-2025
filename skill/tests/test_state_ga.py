"""Georgia (GA) state plugin tests.

Covers the GeorgiaPlugin hand-rolled flat-rate calc. GA is NOT supported by
tenforty / OpenTaxSolver, so the plugin computes everything in-house off
federal AGI using the TY2025 5.19% flat rate, $12,000/$24,000 personal
exemption, and $4,000-per-dependent exemption from the 2025 IT-511 booklet.

All TY2025 numbers are verified against the official DOR source (see module
docstring in skill/scripts/states/ga.py for the direct quotes and URLs).

Test structure mirrors test_state_pa.py plus GA-specific assertions:

- Meta fields: code="GA", starting_point=FEDERAL_AGI, reciprocity=()
- Resident single $65k -> exemption $12,000, taxable $53,000, tax $2,750.70
- Resident MFJ $120k + 2 deps -> exemption $32,000, taxable $88,000,
  tax $4,567.20
- Nonresident half-year day-based proration
- apportion_income() resident & nonresident
- Plugin satisfies StatePlugin runtime protocol
- v1 limitations surfaced on state_specific
- Flat rate stored in state_specific matches the module-level constant
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.models import (
    Address,
    CanonicalReturn,
    Dependent,
    DependentRelationship,
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
from skill.scripts.states.ga import (
    GA_TY2025_DEPENDENT_EXEMPTION,
    GA_TY2025_FLAT_RATE,
    GA_TY2025_PERSONAL_EXEMPTION,
    PLUGIN,
    V1_LIMITATIONS,
    GeorgiaPlugin,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _person(first: str, last: str, ssn: str, year: int = 1990) -> Person:
    return Person(
        first_name=first,
        last_name=last,
        ssn=ssn,
        date_of_birth=dt.date(year, 1, 1),
    )


@pytest.fixture
def single_65k_return() -> CanonicalReturn:
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=_person("Peach", "Taxpayer", "111-22-3333"),
        address=Address(
            street1="100 Peachtree St NE", city="Atlanta", state="GA", zip="30303"
        ),
        w2s=[
            W2(
                employer_name="Coca-Cola Co",
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


@pytest.fixture
def mfj_120k_two_deps_return() -> CanonicalReturn:
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.MFJ,
        taxpayer=_person("Georgia", "Taxpayer", "111-22-3333"),
        spouse=_person("Spouse", "Taxpayer", "222-33-4444"),
        address=Address(
            street1="200 Marietta St", city="Atlanta", state="GA", zip="30303"
        ),
        dependents=[
            Dependent(
                person=_person("Kid", "One", "333-44-5555", year=2015),
                relationship=DependentRelationship.DAUGHTER,
                months_lived_with_taxpayer=12,
                is_qualifying_child=True,
                is_qualifying_relative=False,
            ),
            Dependent(
                person=_person("Kid", "Two", "444-55-6666", year=2017),
                relationship=DependentRelationship.SON,
                months_lived_with_taxpayer=12,
                is_qualifying_child=True,
                is_qualifying_relative=False,
            ),
        ],
        w2s=[
            W2(
                employer_name="Delta Air Lines",
                box1_wages=Decimal("120000"),
                box2_federal_income_tax_withheld=Decimal("0"),
            ),
        ],
    )


@pytest.fixture
def federal_mfj_120k_two_deps() -> FederalTotals:
    return FederalTotals(
        filing_status=FilingStatus.MFJ,
        num_dependents=2,
        adjusted_gross_income=Decimal("120000"),
        taxable_income=Decimal("88500"),
        total_federal_tax=Decimal("10000"),
        federal_income_tax=Decimal("10000"),
        federal_standard_deduction=Decimal("31500"),
        federal_itemized_deductions_total=Decimal("0"),
        deduction_taken=Decimal("31500"),
        federal_withholding_from_w2s=Decimal("0"),
    )


# ---------------------------------------------------------------------------
# Meta fields + protocol conformance
# ---------------------------------------------------------------------------


class TestMetaFields:
    def test_meta_fields(self):
        """Single consolidated assertion per the task spec."""
        meta = PLUGIN.meta
        assert meta.code == "GA"
        assert meta.name == "Georgia"
        assert meta.has_income_tax is True
        assert meta.starting_point == StateStartingPoint.FEDERAL_AGI
        assert meta.reciprocity_partners == ()
        assert meta.submission_channel == SubmissionChannel.FED_STATE_PIGGYBACK
        assert 2025 in meta.supported_tax_years

    def test_dor_url_is_official(self):
        assert "dor.georgia.gov" in PLUGIN.meta.dor_url

    def test_free_efile_url_is_gtc(self):
        """Georgia Tax Center is the state DOR's free portal."""
        assert PLUGIN.meta.free_efile_url is not None
        assert "gtc.dor.ga.gov" in PLUGIN.meta.free_efile_url

    def test_notes_mention_flat_rate_and_hand_roll(self):
        notes = PLUGIN.meta.notes
        assert "5.19" in notes
        assert "hand-rolled" in notes

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "NY"  # type: ignore[misc]


def test_plugin_is_state_plugin_protocol():
    """runtime_checkable Protocol must recognize our concrete plugin."""
    assert isinstance(PLUGIN, StatePlugin)


def test_plugin_is_georgia_plugin_instance():
    assert isinstance(PLUGIN, GeorgiaPlugin)


# ---------------------------------------------------------------------------
# compute() - resident cases
# ---------------------------------------------------------------------------


class TestResidentCompute:
    def test_resident_single_65k(self, single_65k_return, federal_single_65k):
        """Single $65k resident.

        Exemption: $12,000 (GA personal exemption S/MFS/HOH/QSS, TY2025)
        Taxable:   $65,000 - $12,000 = $53,000
        Tax:       $53,000 * 5.19% = $2,750.70
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert isinstance(result, StateReturn)
        assert result.state == "GA"
        ss = result.state_specific
        assert ss["state_base_income_approx"] == Decimal("65000.00")
        assert ss["state_exemption_total"] == Decimal("12000.00")
        assert ss["state_taxable_income"] == Decimal("53000.00")
        expected_tax = (
            Decimal("53000") * GA_TY2025_FLAT_RATE
        ).quantize(Decimal("0.01"))
        assert ss["state_total_tax"] == expected_tax
        # Independently: verify the hand-computed literal value too.
        assert ss["state_total_tax"] == Decimal("2750.70")

    def test_resident_mfj_120k_two_deps(
        self, mfj_120k_two_deps_return, federal_mfj_120k_two_deps
    ):
        """MFJ $120k with two dependents.

        Exemption: $24,000 (MFJ personal) + 2 * $4,000 (deps) = $32,000
        Taxable:   $120,000 - $32,000 = $88,000
        Tax:       $88,000 * 5.19% = $4,567.20
        """
        result = PLUGIN.compute(
            mfj_120k_two_deps_return,
            federal_mfj_120k_two_deps,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = result.state_specific
        assert ss["state_base_income_approx"] == Decimal("120000.00")
        assert ss["state_exemption_total"] == Decimal("32000.00")
        assert ss["state_taxable_income"] == Decimal("88000.00")
        expected_tax = (
            Decimal("88000") * GA_TY2025_FLAT_RATE
        ).quantize(Decimal("0.01"))
        assert ss["state_total_tax"] == expected_tax
        assert ss["state_total_tax"] == Decimal("4567.20")

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

    def test_resident_total_tax_equals_resident_basis(
        self, single_65k_return, federal_single_65k
    ):
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

    def test_resident_state_return_pydantic_roundtrip(
        self, single_65k_return, federal_single_65k
    ):
        """Round-trip through Pydantic JSON to confirm the returned
        StateReturn validates under the canonical model contract."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        dumped = result.model_dump(mode="json")
        rehydrated = StateReturn.model_validate(dumped)
        assert rehydrated.state == "GA"
        assert rehydrated.residency == ResidencyStatus.RESIDENT

    def test_exemption_floor_prevents_negative_taxable(self):
        """A low-income resident whose AGI is below the personal exemption
        should see taxable income and tax pinned to zero, not go negative."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person("Low", "Income", "555-66-7777"),
            address=Address(
                street1="1 Pine St", city="Macon", state="GA", zip="31201"
            ),
            w2s=[
                W2(
                    employer_name="Small Employer",
                    box1_wages=Decimal("8000"),
                ),
            ],
        )
        fed = FederalTotals(
            filing_status=FilingStatus.SINGLE,
            num_dependents=0,
            adjusted_gross_income=Decimal("8000"),
            taxable_income=Decimal("0"),
            total_federal_tax=Decimal("0"),
            federal_income_tax=Decimal("0"),
            federal_standard_deduction=Decimal("15750"),
            federal_itemized_deductions_total=Decimal("0"),
            deduction_taken=Decimal("15750"),
        )
        result = PLUGIN.compute(
            ret, fed, ResidencyStatus.RESIDENT, days_in_state=365
        )
        ss = result.state_specific
        assert ss["state_taxable_income"] == Decimal("0.00")
        assert ss["state_total_tax"] == Decimal("0.00")


# ---------------------------------------------------------------------------
# compute() - nonresident / part-year
# ---------------------------------------------------------------------------


class TestNonresidentCompute:
    def test_nonresident_half_year_prorates(
        self, single_65k_return, federal_single_65k
    ):
        """NONRESIDENT with days_in_state=182 should yield (182/365) of the
        full-year resident-basis tax via day-based proration. TODO: real
        GA-500 Schedule 3 income-ratio sourcing."""
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
        # The full-year basis is $2,750.70 (from the resident case); the
        # 182/365 proration lands at $1,371.58.
        assert full == Decimal("2750.70")
        assert apportioned == Decimal("1371.58")

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
        assert result.state_specific["apportionment_fraction"] == (
            Decimal(91) / Decimal(365)
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

    def test_full_year_nonresident_equals_resident_tax(
        self, single_65k_return, federal_single_65k
    ):
        """365-day nonresident with day-based proration should equal the
        full-year resident tax. Proration boundary sanity check."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=365,
        )
        assert (
            result.state_specific["state_total_tax"]
            == result.state_specific["state_total_tax_resident_basis"]
        )


# ---------------------------------------------------------------------------
# apportion_income()
# ---------------------------------------------------------------------------


class TestApportionIncome:
    def test_apportion_income_resident(self, single_65k_return):
        """Resident: all federal income becomes GA-source 1:1."""
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
        """Nonresident half-year prorates wages by days_in_state / 365."""
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.NONRESIDENT, days_in_state=182
        )
        expected_wages = (
            Decimal("65000") * Decimal(182) / Decimal(365)
        ).quantize(Decimal("0.01"))
        assert app.state_source_wages == expected_wages
        assert app.state_source_total == expected_wages


# ---------------------------------------------------------------------------
# v1 limitations + rate verification
# ---------------------------------------------------------------------------


class TestV1LimitationsAndRate:
    def test_v1_limitations_documented(
        self, single_65k_return, federal_single_65k
    ):
        """Every StateReturn from this plugin must surface the v1 limitations
        tuple on state_specific so downstream consumers can inspect what is
        and is not modeled without having to crack open the module."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        limitations = result.state_specific["v1_limitations"]
        assert limitations == V1_LIMITATIONS
        # Spot-check: the LOUD list must mention the most dangerous gaps.
        joined = " ".join(limitations)
        assert "schedule_1" in joined
        assert "retirement_income_exclusion" in joined
        assert "low_income_credit" in joined
        assert "hb_1302_surplus_refund" in joined
        assert "itemized" in joined

    def test_flat_rate_matches_verified_source(
        self, single_65k_return, federal_single_65k
    ):
        """The flat rate stored in state_specific must equal the
        module-level GA_TY2025_FLAT_RATE constant, which is itself locked
        to 0.0519 from the 2025 IT-511 booklet. Any drift between the
        constant and what the plugin reports is a CI-fail-worthy bug."""
        assert GA_TY2025_FLAT_RATE == Decimal("0.0519")
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["flat_rate"] == GA_TY2025_FLAT_RATE
        assert result.state_specific["flat_rate"] == Decimal("0.0519")

    def test_personal_exemption_constants(self):
        """Lock the verified TY2025 exemption amounts.

        Source: 2025 IT-511 Individual Income Tax Instructions Booklet,
        Line 11 standard deduction ($12,000 S/MFS/HOH/QSS, $24,000 MFJ)
        and Line 14 dependent exemption ($4,000 per dependent).
        """
        assert GA_TY2025_PERSONAL_EXEMPTION[FilingStatus.SINGLE] == Decimal("12000")
        assert GA_TY2025_PERSONAL_EXEMPTION[FilingStatus.MFS] == Decimal("12000")
        assert GA_TY2025_PERSONAL_EXEMPTION[FilingStatus.HOH] == Decimal("12000")
        assert GA_TY2025_PERSONAL_EXEMPTION[FilingStatus.QSS] == Decimal("12000")
        assert GA_TY2025_PERSONAL_EXEMPTION[FilingStatus.MFJ] == Decimal("24000")
        assert GA_TY2025_DEPENDENT_EXEMPTION == Decimal("4000")


# ---------------------------------------------------------------------------
# form_ids + render_pdfs
# ---------------------------------------------------------------------------


class TestFormIdsAndRender:
    def test_form_ids_returns_ga_500(self):
        assert PLUGIN.form_ids() == ["GA Form 500"]

    def test_render_pdfs_returns_empty_list(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Fan-out follow-up: actual GA Form 500 PDF fill not yet implemented."""
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
        """Even with a nonexistent path, a no-op render should not raise."""
        state_return = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert PLUGIN.render_pdfs(state_return, Path("/tmp")) == []
