"""Delaware state plugin tests — TY2025.

Covers the hand-rolled ``DelawarePlugin`` Form 200-01 calc. The
tenforty graph backend correctly applies the DE rate schedule and
the DE standard deduction ($3,250 Single) but it OMITS the
$110/exemption personal credit (DE Form 200-01 line 28). For a
Single $65k filer the graph reports $3,059 (line 27, before
credits) and the correct line-31 amount (after $110 credit) is
$2,949 — see ``skill/scripts/states/de.py`` module docstring for
the full decision rationale.

TY2025 structure (per DE Form 200-01 booklet):
- Seven brackets identical for ALL filing statuses (DE does NOT
  double brackets for MFJ): 0% / 2.2% / 3.9% / 4.8% / 5.2% / 5.55%
  / 6.6%, top rate begins at $60,000
- Standard deduction: $3,250 Single, $6,500 MFJ
- Personal credit (line 28): $110 per personal exemption per
  30 Del. C. § 1110

Source: DE Form 200-01 Resident Booklet 2025
(revenue.delaware.gov/file-individual-income-tax/).
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
    ReciprocityTable,
    StatePlugin,
    StateStartingPoint,
    SubmissionChannel,
)
from skill.scripts.states.de import (
    DE_TY2025_BRACKETS,
    DE_TY2025_PERSONAL_CREDIT_PER_EXEMPTION,
    DE_TY2025_STD_DED_MFJ,
    DE_TY2025_STD_DED_SINGLE,
    DE_V1_LIMITATIONS,
    DelawarePlugin,
    LOCK_VALUE,
    PLUGIN,
    de_personal_credit,
    de_personal_credit_count,
    de_standard_deduction,
    de_tax_from_schedule,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def single_65k_return() -> CanonicalReturn:
    """A Single $65k W-2 DE resident from Wilmington."""
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=Person(
            first_name="Carney",
            last_name="Blue",
            ssn="111-22-3333",
            date_of_birth=dt.date(1990, 1, 1),
        ),
        address=Address(
            street1="800 N French St",
            city="Wilmington",
            state="DE",
            zip="19801",
        ),
        w2s=[
            W2(
                employer_name="First State Corp",
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
def mfj_120k_return() -> CanonicalReturn:
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.MFJ,
        taxpayer=Person(
            first_name="Joe",
            last_name="Biden",
            ssn="111-22-3333",
            date_of_birth=dt.date(1942, 11, 20),
        ),
        spouse=Person(
            first_name="Jill",
            last_name="Biden",
            ssn="222-33-4444",
            date_of_birth=dt.date(1951, 6, 3),
        ),
        address=Address(
            street1="1 Main St",
            city="Greenville",
            state="DE",
            zip="19807",
        ),
        w2s=[
            W2(
                employer_name="Biden LLC",
                box1_wages=Decimal("120000"),
                box2_federal_income_tax_withheld=Decimal("0"),
            ),
        ],
    )


@pytest.fixture
def federal_mfj_120k() -> FederalTotals:
    return FederalTotals(
        filing_status=FilingStatus.MFJ,
        num_dependents=0,
        adjusted_gross_income=Decimal("120000"),
        taxable_income=Decimal("88500"),
        total_federal_tax=Decimal("10173"),
        federal_income_tax=Decimal("10173"),
        federal_standard_deduction=Decimal("31500"),
        federal_itemized_deductions_total=Decimal("0"),
        deduction_taken=Decimal("31500"),
        federal_withholding_from_w2s=Decimal("0"),
    )


# ---------------------------------------------------------------------------
# Meta + Protocol conformance
# ---------------------------------------------------------------------------


class TestDelawarePluginMeta:
    def test_meta_fields(self):
        assert PLUGIN.meta.code == "DE"
        assert PLUGIN.meta.name == "Delaware"
        assert PLUGIN.meta.has_income_tax is True
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_meta_submission_channel_is_state_dor_free_portal(self):
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )

    def test_meta_dor_url_is_revenue_delaware_gov(self):
        assert "revenue.delaware.gov" in PLUGIN.meta.dor_url

    def test_meta_free_efile_url_present(self):
        assert PLUGIN.meta.free_efile_url is not None
        assert "delaware" in PLUGIN.meta.free_efile_url.lower()

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_form_200_01(self):
        assert "200-01" in PLUGIN.meta.notes

    def test_meta_notes_mention_personal_credit(self):
        notes = PLUGIN.meta.notes.lower()
        assert "personal credit" in notes or "$110" in notes

    def test_meta_notes_mention_top_rate_6_6(self):
        """The DE top rate is 6.6% — load-bearing for plugin metadata."""
        assert "6.6" in PLUGIN.meta.notes

    def test_meta_notes_mention_no_reciprocity(self):
        assert "reciprocity" in PLUGIN.meta.notes.lower()

    def test_meta_notes_mention_pa_or_nj_commuter(self):
        """DE-PA and DE-NJ are notable non-reciprocity borders."""
        notes = PLUGIN.meta.notes
        assert "PA" in notes or "NJ" in notes

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "AL"  # type: ignore[misc]

    def test_plugin_is_state_plugin_protocol(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_delaware_plugin_instance(self):
        assert isinstance(PLUGIN, DelawarePlugin)


# ---------------------------------------------------------------------------
# Reciprocity invariants
# ---------------------------------------------------------------------------


class TestDelawareNoReciprocity:
    """Delaware has no bilateral reciprocity agreements."""

    def test_no_reciprocity_partners_in_meta(self):
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_no_reciprocity_via_reciprocity_table(self):
        table = ReciprocityTable.load()
        assert table.partners_of("DE") == frozenset()
        assert table.has_income_tax("DE") is True

    def test_not_reciprocal_with_pa_nj_md(self):
        """DE-PA, DE-NJ, DE-MD are notable non-reciprocity borders."""
        table = ReciprocityTable.load()
        for neighbor in ("PA", "NJ", "MD"):
            assert table.are_reciprocal("DE", neighbor) is False


# ---------------------------------------------------------------------------
# Standard deduction helper
# ---------------------------------------------------------------------------


class TestDelawareStandardDeduction:
    """DE Form 200-01 line 18 standard deduction."""

    def test_single(self):
        assert de_standard_deduction(FilingStatus.SINGLE) == Decimal("3250")

    def test_mfj(self):
        assert de_standard_deduction(FilingStatus.MFJ) == Decimal("6500")

    def test_hoh(self):
        assert de_standard_deduction(FilingStatus.HOH) == Decimal("3250")

    def test_mfs(self):
        assert de_standard_deduction(FilingStatus.MFS) == Decimal("3250")

    def test_qss_mirrors_mfj(self):
        assert de_standard_deduction(FilingStatus.QSS) == Decimal("6500")

    def test_constants_match_helpers(self):
        assert DE_TY2025_STD_DED_SINGLE == Decimal("3250")
        assert DE_TY2025_STD_DED_MFJ == Decimal("6500")


# ---------------------------------------------------------------------------
# Personal credit helper
# ---------------------------------------------------------------------------


class TestDelawarePersonalCredit:
    """DE Form 200-01 line 28 personal credit ($110 per exemption)."""

    def test_per_exemption_constant(self):
        assert DE_TY2025_PERSONAL_CREDIT_PER_EXEMPTION == Decimal("110")

    def test_single_zero_deps(self):
        assert de_personal_credit_count(FilingStatus.SINGLE, 0) == 1
        assert de_personal_credit(FilingStatus.SINGLE, 0) == Decimal("110")

    def test_single_two_deps(self):
        assert de_personal_credit_count(FilingStatus.SINGLE, 2) == 3
        assert de_personal_credit(FilingStatus.SINGLE, 2) == Decimal("330")

    def test_mfj_zero_deps(self):
        """MFJ gets 2 base exemptions ($220)."""
        assert de_personal_credit_count(FilingStatus.MFJ, 0) == 2
        assert de_personal_credit(FilingStatus.MFJ, 0) == Decimal("220")

    def test_mfj_three_deps(self):
        """MFJ + 3 deps = 2 base + 3 = 5 exemptions ($550)."""
        assert de_personal_credit_count(FilingStatus.MFJ, 3) == 5
        assert de_personal_credit(FilingStatus.MFJ, 3) == Decimal("550")

    def test_qss_mirrors_mfj(self):
        assert de_personal_credit(FilingStatus.QSS, 0) == Decimal("220")

    def test_hoh_uses_single_count(self):
        """HOH gets 1 base credit (matches Single)."""
        assert de_personal_credit_count(FilingStatus.HOH, 0) == 1

    def test_negative_deps_clamps_to_zero(self):
        assert de_personal_credit_count(FilingStatus.SINGLE, -3) == 1


# ---------------------------------------------------------------------------
# Tax rate schedule
# ---------------------------------------------------------------------------


class TestDelawareTaxRateSchedule:
    """DE Form 200-01 line 27 — 30 Del. C. § 1102 rate schedule."""

    def test_zero_taxable_income(self):
        assert de_tax_from_schedule(Decimal("0")) == Decimal("0.00")

    def test_negative_taxable_income_clamps_to_zero(self):
        assert de_tax_from_schedule(Decimal("-1000")) == Decimal("0.00")

    def test_below_first_bracket_no_tax(self):
        """TI < $2,000 → 0% bracket → no tax."""
        assert de_tax_from_schedule(Decimal("1500")) == Decimal("0.00")
        assert de_tax_from_schedule(Decimal("2000")) == Decimal("0.00")

    def test_at_2_2_bracket_top(self):
        """TI = $5,000: 0% × $2,000 + 2.2% × $3,000 = $66."""
        assert de_tax_from_schedule(Decimal("5000")) == Decimal("66.00")

    def test_at_3_9_bracket_top(self):
        """TI = $10,000: $66 + 3.9% × $5,000 = $66 + $195 = $261."""
        assert de_tax_from_schedule(Decimal("10000")) == Decimal("261.00")

    def test_at_4_8_bracket_top(self):
        """TI = $20,000: $261 + 4.8% × $10,000 = $261 + $480 = $741."""
        assert de_tax_from_schedule(Decimal("20000")) == Decimal("741.00")

    def test_at_5_2_bracket_top(self):
        """TI = $25,000: $741 + 5.2% × $5,000 = $741 + $260 = $1,001."""
        assert de_tax_from_schedule(Decimal("25000")) == Decimal("1001.00")

    def test_at_5_55_bracket_top(self):
        """TI = $60,000: $1,001 + 5.55% × $35,000 = $1,001 + $1,942.50 = $2,943.50."""
        assert de_tax_from_schedule(Decimal("60000")) == Decimal("2943.50")

    def test_top_bracket_6_6_at_61750(self):
        """TI = $61,750: $2,943.50 + 6.6% × $1,750 = $2,943.50 + $115.50 = $3,059."""
        assert de_tax_from_schedule(Decimal("61750")) == Decimal("3059.00")

    def test_brackets_constant_count(self):
        """DE has 7 graduated brackets."""
        assert len(DE_TY2025_BRACKETS) == 7

    def test_top_rate_is_6_6_percent(self):
        assert DE_TY2025_BRACKETS[-1].rate == Decimal("0.066")
        assert DE_TY2025_BRACKETS[-1].high is None

    def test_first_bracket_zero_rate(self):
        assert DE_TY2025_BRACKETS[0].rate == Decimal("0.0")

    def test_brackets_are_same_for_all_filing_statuses(self):
        """DE does NOT double brackets for MFJ — confirmed by 30 Del. C.
        § 1102 and DE Form 200-01 instructions."""
        # The de_tax_from_schedule helper takes only TI (not status),
        # so this is a structural property of the plugin: there is no
        # per-status bracket table.
        assert len(DE_TY2025_BRACKETS) == 7


# ---------------------------------------------------------------------------
# compute() — resident scenarios
# ---------------------------------------------------------------------------


class TestDelawarePluginComputeResident:
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
        assert result.state == "DE"
        assert result.residency == ResidencyStatus.RESIDENT


class TestDelawareTaxLockSingle65k:
    """**SPEC-MANDATED $65k Single TAX LOCK** for Delaware.

    Hand trace from DE Form 200-01 (TY2025):

        Line 1/16: DE AGI                          = $65,000
        Line 18:   DE std deduction (Single)       = $3,250
        Line 26:   DE TI                           = $61,750
        Line 27:   Tax via DE rate schedule
                   2.2% × $3,000     = $66.00
                   3.9% × $5,000     = $195.00
                   4.8% × $10,000    = $480.00
                   5.2% × $5,000     = $260.00
                   5.55% × $35,000   = $1,942.50
                   6.6% × $1,750     = $115.50
                   Sum                              = $3,059.00
        Line 28:   Personal credit (1 × $110)      = -$110.00
        Line 31:   Tax after credits                = $2,949.00

    Tenforty graph backend reports $3,059 (omits the personal
    credit). Hand-rolling closes the $110 gap.
    """

    def test_resident_single_65k_tax_lock(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_total_tax"] == LOCK_VALUE
        assert (
            result.state_specific["state_total_tax_resident_basis"]
            == LOCK_VALUE
        )

    def test_lock_value_breakdown(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = result.state_specific
        assert ss["state_federal_agi"] == Decimal("65000.00")
        assert ss["state_adjusted_gross_income"] == Decimal("65000.00")
        assert ss["state_standard_deduction"] == Decimal("3250.00")
        assert ss["state_taxable_income"] == Decimal("61750.00")
        assert ss["state_tax_before_credits"] == Decimal("3059.00")
        assert ss["state_personal_credit"] == Decimal("110.00")


class TestDelawarePluginComputeOtherResidents:
    def test_resident_mfj_120k(self, mfj_120k_return, federal_mfj_120k):
        """MFJ $120k:

            DE AGI            = $120,000
            DE std (MFJ)      = $6,500
            DE TI             = $113,500
            Rate schedule:
                0% × $2,000      = $0
                2.2% × $3,000    = $66
                3.9% × $5,000    = $195
                4.8% × $10,000   = $480
                5.2% × $5,000    = $260
                5.55% × $35,000  = $1,942.50
                6.6% × $53,500   = $3,531
                Sum               = $6,474.50
            Personal credit (2 base × $110)      = $220
            Line 31 tax after credits             = $6,254.50
        """
        result = PLUGIN.compute(
            mfj_120k_return,
            federal_mfj_120k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = result.state_specific
        assert ss["state_standard_deduction"] == Decimal("6500.00")
        assert ss["state_taxable_income"] == Decimal("113500.00")
        assert ss["state_personal_credit"] == Decimal("220.00")
        assert ss["state_tax_before_credits"] == Decimal("6474.50")
        assert ss["state_total_tax"] == Decimal("6254.50")

    def test_zero_income_yields_zero_tax(self):
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="Zero",
                last_name="Income",
                ssn="999-88-7777",
                date_of_birth=dt.date(1990, 1, 1),
            ),
            address=Address(street1="1 Main", city="Dover", state="DE", zip="19901"),
        )
        fed = FederalTotals(
            filing_status=FilingStatus.SINGLE,
            num_dependents=0,
            adjusted_gross_income=Decimal("0"),
            taxable_income=Decimal("0"),
            total_federal_tax=Decimal("0"),
            federal_income_tax=Decimal("0"),
            federal_standard_deduction=Decimal("15750"),
            federal_itemized_deductions_total=Decimal("0"),
            deduction_taken=Decimal("15750"),
        )
        result = PLUGIN.compute(ret, fed, ResidencyStatus.RESIDENT, 365)
        assert result.state_specific["state_taxable_income"] == Decimal("0.00")
        # Tax before credits = 0; personal credit = $110, but tax is
        # floored at zero (the credit cannot create a refund — DE
        # personal credit is nonrefundable per Form 200-01 line 28).
        assert result.state_specific["state_total_tax"] == Decimal("0.00")

    def test_personal_credit_floors_tax_at_zero(self):
        """A very-low income filer whose tax-before-credits is less
        than $110 should not get a refund — DE personal credit is
        nonrefundable."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="Low",
                last_name="Income",
                ssn="999-88-1111",
                date_of_birth=dt.date(1990, 1, 1),
            ),
            address=Address(street1="1 Main", city="Dover", state="DE", zip="19901"),
        )
        fed = FederalTotals(
            filing_status=FilingStatus.SINGLE,
            num_dependents=0,
            adjusted_gross_income=Decimal("5500"),
            taxable_income=Decimal("0"),
            total_federal_tax=Decimal("0"),
            federal_income_tax=Decimal("0"),
            federal_standard_deduction=Decimal("15750"),
            federal_itemized_deductions_total=Decimal("0"),
            deduction_taken=Decimal("15750"),
        )
        result = PLUGIN.compute(ret, fed, ResidencyStatus.RESIDENT, 365)
        # TI = $5,500 - $3,250 = $2,250 → tax = 2.2% × $250 = $5.50
        # less $110 credit → max($5.50 - $110, 0) = 0
        assert result.state_specific["state_total_tax"] == Decimal("0.00")

    def test_state_specific_numerics_are_decimal(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        decimal_keys = [
            "state_federal_agi",
            "state_adjusted_gross_income",
            "state_standard_deduction",
            "state_taxable_income",
            "state_tax_before_credits",
            "state_personal_credit",
            "state_total_tax",
            "state_total_tax_resident_basis",
            "apportionment_fraction",
            "de_modifications_applied",
        ]
        for key in decimal_keys:
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

    def test_starting_point_is_federal_agi(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["starting_point"] == "federal_agi"

    def test_state_return_validates_via_pydantic(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        dumped = result.model_dump(mode="json")
        rehydrated = StateReturn.model_validate(dumped)
        assert rehydrated.state == "DE"

    def test_v1_limitations_in_state_specific(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        lims = result.state_specific["v1_limitations"]
        assert isinstance(lims, list)
        assert len(lims) >= 5


# ---------------------------------------------------------------------------
# Nonresident / part-year
# ---------------------------------------------------------------------------


class TestDelawarePluginComputeNonresident:
    def test_nonresident_half_year_prorates_tax(
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
        assert apportioned < full
        expected = (full * Decimal(182) / Decimal(365)).quantize(
            Decimal("0.01")
        )
        assert apportioned == expected

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

    def test_part_year_apportionment_fraction(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.PART_YEAR,
            days_in_state=91,
        )
        expected = Decimal(91) / Decimal("365")
        assert result.state_specific["apportionment_fraction"] == expected

    def test_resident_basis_invariant(
        self, single_65k_return, federal_single_65k
    ):
        res = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        nr = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=100,
        )
        assert (
            res.state_specific["state_total_tax_resident_basis"]
            == nr.state_specific["state_total_tax_resident_basis"]
        )


# ---------------------------------------------------------------------------
# apportion_income()
# ---------------------------------------------------------------------------


class TestDelawarePluginApportionIncome:
    def test_resident_full_amounts(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert isinstance(app, IncomeApportionment)
        assert app.state_source_wages == Decimal("65000.00")
        assert app.state_source_total == Decimal("65000.00")

    def test_nonresident_prorates(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return,
            ResidencyStatus.NONRESIDENT,
            days_in_state=182,
        )
        expected = (
            Decimal("65000") * Decimal(182) / Decimal(365)
        ).quantize(Decimal("0.01"))
        assert app.state_source_wages == expected


# ---------------------------------------------------------------------------
# render_pdfs / form_ids
# ---------------------------------------------------------------------------


class TestDelawarePluginFormIds:
    def test_form_ids(self):
        assert PLUGIN.form_ids() == ["DE Form 200-01"]

    def test_render_pdfs_returns_empty_list(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        state_return = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert PLUGIN.render_pdfs(state_return, tmp_path) == []


# ---------------------------------------------------------------------------
# V1 limitations module
# ---------------------------------------------------------------------------


class TestDelawareV1Limitations:
    def test_limitations_is_tuple(self):
        assert isinstance(DE_V1_LIMITATIONS, tuple)
        assert len(DE_V1_LIMITATIONS) >= 5

    def test_limitations_mention_form_200_02_nonresident(self):
        joined = " ".join(DE_V1_LIMITATIONS)
        assert "200-02" in joined or "nonresident" in joined.lower()

    def test_limitations_mention_credit_for_taxes_paid(self):
        joined = " ".join(DE_V1_LIMITATIONS).lower()
        assert "credit" in joined and "other states" in joined

    def test_limitations_mention_pension_exclusion(self):
        joined = " ".join(DE_V1_LIMITATIONS).lower()
        assert "pension" in joined


# ---------------------------------------------------------------------------
# Tenforty gap gatekeeper — DE personal credit omitted by graph backend
# ---------------------------------------------------------------------------


class TestTenfortyStillHasGapOnDE:
    """Re-probes tenforty's graph backend for DE and asserts the gap.

    The graph backend correctly applies the rate schedule and the
    standard deduction but OMITS the $110/exemption personal credit
    (DE Form 200-01 line 28). When this is fixed upstream, this
    test fails — at which point DE can be revisited (potentially
    converted to a graph wrapper following the WI pattern, and this
    test deleted).
    """

    GRAPH_BACKEND_REFERENCE_TAX = Decimal("3059.00")
    HAND_ROLLED_LOCK_VALUE = Decimal("2949.00")
    PERSONAL_CREDIT_DELTA = Decimal("110.00")

    def test_graph_backend_still_omits_personal_credit(self):
        try:
            import tenforty  # type: ignore[import-not-found]
        except ImportError:
            pytest.skip("tenforty not installed")

        try:
            tf = tenforty.evaluate_return(
                year=2025,
                state="DE",
                backend="graph",
                filing_status="Single",
                w2_income=65000,
                taxable_interest=0,
                qualified_dividends=0,
                ordinary_dividends=0,
                short_term_capital_gains=0,
                long_term_capital_gains=0,
                self_employment_income=0,
                rental_income=0,
                schedule_1_income=0,
                standard_or_itemized="Standard",
                itemized_deductions=0,
                num_dependents=0,
            )
        except Exception as exc:
            pytest.skip(f"tenforty graph backend probe failed: {exc}")

        graph_tax = Decimal(str(tf.state_total_tax)).quantize(Decimal("0.01"))
        assert graph_tax == self.GRAPH_BACKEND_REFERENCE_TAX, (
            f"Graph backend tax for DE Single $65k changed: "
            f"expected ${self.GRAPH_BACKEND_REFERENCE_TAX} (CP8-B "
            f"probe value, no personal credit applied) but got "
            f"${graph_tax}. If this is a personal-credit fix, "
            f"convert de.py to a graph wrapper following WI's pattern."
        )

    def test_hand_roll_diverges_from_graph_by_exactly_personal_credit(
        self, single_65k_return, federal_single_65k
    ):
        """Hand-roll vs graph delta = $110 personal credit (Single)."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        hand = result.state_specific["state_total_tax"]
        assert hand == self.HAND_ROLLED_LOCK_VALUE
        delta = self.GRAPH_BACKEND_REFERENCE_TAX - hand
        assert delta == self.PERSONAL_CREDIT_DELTA

    def test_graph_backend_at_10k_single_locks(self):
        """At $10k Single, graph backend reports $134.25 (rate-schedule
        × ($10k - $3,250) std ded with no personal credit). Pin so
        upstream changes are caught."""
        try:
            import tenforty  # type: ignore[import-not-found]
        except ImportError:
            pytest.skip("tenforty not installed")
        try:
            tf = tenforty.evaluate_return(
                year=2025,
                state="DE",
                backend="graph",
                filing_status="Single",
                w2_income=10000,
                standard_or_itemized="Standard",
            )
        except Exception as exc:
            pytest.skip(f"tenforty graph backend probe failed: {exc}")
        assert Decimal(str(tf.state_total_tax)).quantize(
            Decimal("0.01")
        ) == Decimal("134.25")
