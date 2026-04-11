"""Alabama state plugin tests — TY2025.

Covers the hand-rolled ``AlabamaPlugin`` Form 40 calc. The tenforty
graph backend (the only backend with TY2025 AL coverage) materially
understates the AL liability because it omits the federal income
tax deduction, the AL sliding-scale standard deduction, and the
personal exemption — see ``skill/scripts/states/al.py`` module
docstring for the full decision rationale.

TY2025 structure (per AL DOR Form 40 booklet):

- Brackets per ALA. CODE § 40-18-5 (top rate capped at 5% by AL
  Const. Amendment 25):
    Single / HOH / MFS:  2% / 4% / 5% with breakpoints at $500/$3,000
    Married Filing Joint: 2% / 4% / 5% with breakpoints at $1,000/$6,000
- Standard deduction (sliding-scale phase-down):
    Single   $3,000 (AGI ≤ $23,000) → $2,500 (AGI ≥ $30,500)
    MFJ      $8,500 (AGI ≤ $23,500) → $4,000 (AGI ≥ $33,500)
- Personal exemption: $1,500 Single/MFS, $3,000 MFJ/HOH
- Dependent exemption: $1,000 / $500 / $300 (AGI tier)
- Federal income tax deduction (Form 40 line 9): full federal income
  tax liability per ALA. CODE § 40-18-15(a)(2)

Source: 2025 AL Form 40 booklet (revenue.alabama.gov/forms/).
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
from skill.scripts.states.al import (
    AL_TY2025_BRACKETS_MFJ,
    AL_TY2025_BRACKETS_SINGLE,
    AL_TY2025_DEPENDENT_HIGH,
    AL_TY2025_DEPENDENT_LOW,
    AL_TY2025_DEPENDENT_MID,
    AL_TY2025_PERSONAL_EXEMPTION_MFJ,
    AL_TY2025_PERSONAL_EXEMPTION_SINGLE,
    AL_TY2025_STD_DED_MFJ_MAX,
    AL_TY2025_STD_DED_MFJ_MIN,
    AL_TY2025_STD_DED_SINGLE_MAX,
    AL_TY2025_STD_DED_SINGLE_MIN,
    AL_V1_LIMITATIONS,
    AlabamaPlugin,
    LOCK_VALUE,
    PLUGIN,
    al_dependent_exemption,
    al_personal_exemption,
    al_standard_deduction,
    al_tax_from_schedule,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def single_65k_return() -> CanonicalReturn:
    """A Single $65k W-2 AL resident from Montgomery (state capital)."""
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=Person(
            first_name="Heart",
            last_name="Dixie",
            ssn="111-22-3333",
            date_of_birth=dt.date(1990, 1, 1),
        ),
        address=Address(
            street1="600 Dexter Ave",
            city="Montgomery",
            state="AL",
            zip="36104",
        ),
        w2s=[
            W2(
                employer_name="Yellowhammer Corp",
                box1_wages=Decimal("65000"),
                box2_federal_income_tax_withheld=Decimal("0"),
            ),
        ],
    )


@pytest.fixture
def federal_single_65k() -> FederalTotals:
    """$65k AGI Single / OBBBA std ded $15,750 / federal taxable $49,250."""
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
            first_name="Helen",
            last_name="Keller",
            ssn="111-22-3333",
            date_of_birth=dt.date(1985, 6, 27),
        ),
        spouse=Person(
            first_name="Annie",
            last_name="Sullivan",
            ssn="222-33-4444",
            date_of_birth=dt.date(1984, 4, 14),
        ),
        address=Address(
            street1="300 N 20th St",
            city="Birmingham",
            state="AL",
            zip="35203",
        ),
        w2s=[
            W2(
                employer_name="Steel City LLC",
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


class TestAlabamaPluginMeta:
    def test_meta_fields(self):
        assert PLUGIN.meta.code == "AL"
        assert PLUGIN.meta.name == "Alabama"
        assert PLUGIN.meta.has_income_tax is True
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_meta_submission_channel_is_state_dor_free_portal(self):
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )

    def test_meta_dor_url_is_revenue_alabama_gov(self):
        assert "revenue.alabama.gov" in PLUGIN.meta.dor_url

    def test_meta_free_efile_url_is_my_alabama_taxes(self):
        assert PLUGIN.meta.free_efile_url is not None
        assert "myalabamataxes" in PLUGIN.meta.free_efile_url

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_form_40(self):
        assert "Form 40" in PLUGIN.meta.notes

    def test_meta_notes_mention_federal_income_tax_deduction(self):
        """The FIT-deduction quirk is what makes AL distinctive."""
        notes = PLUGIN.meta.notes.lower()
        assert "federal income tax deduction" in notes

    def test_meta_notes_mention_my_alabama_taxes(self):
        notes = PLUGIN.meta.notes
        assert "Alabama Taxes" in notes or "MAT" in notes

    def test_meta_notes_mention_no_reciprocity(self):
        assert "reciprocity" in PLUGIN.meta.notes.lower()

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "AR"  # type: ignore[misc]

    def test_plugin_is_state_plugin_protocol(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_alabama_plugin_instance(self):
        assert isinstance(PLUGIN, AlabamaPlugin)


# ---------------------------------------------------------------------------
# Reciprocity invariants
# ---------------------------------------------------------------------------


class TestAlabamaNoReciprocity:
    """Alabama has no bilateral reciprocity agreements with any state."""

    def test_no_reciprocity_partners_in_meta(self):
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_no_reciprocity_via_reciprocity_table(self):
        table = ReciprocityTable.load()
        assert table.partners_of("AL") == frozenset()
        assert table.has_income_tax("AL") is True

    def test_not_reciprocal_with_neighbors(self):
        """AL borders TN, MS, GA, FL. None share reciprocity."""
        table = ReciprocityTable.load()
        for neighbor in ("TN", "MS", "GA", "FL"):
            assert table.are_reciprocal("AL", neighbor) is False


# ---------------------------------------------------------------------------
# Standard deduction phase-down
# ---------------------------------------------------------------------------


class TestAlabamaStandardDeduction:
    """AL Standard Deduction Chart phase-down (Form 40 instructions p11)."""

    def test_single_below_phase_start_is_max(self):
        assert al_standard_deduction(
            FilingStatus.SINGLE, Decimal("10000")
        ) == AL_TY2025_STD_DED_SINGLE_MAX
        assert al_standard_deduction(
            FilingStatus.SINGLE, Decimal("23000")
        ) == AL_TY2025_STD_DED_SINGLE_MAX

    def test_single_above_phase_end_is_min(self):
        assert al_standard_deduction(
            FilingStatus.SINGLE, Decimal("30500")
        ) == AL_TY2025_STD_DED_SINGLE_MIN
        assert al_standard_deduction(
            FilingStatus.SINGLE, Decimal("65000")
        ) == AL_TY2025_STD_DED_SINGLE_MIN
        assert al_standard_deduction(
            FilingStatus.SINGLE, Decimal("250000")
        ) == AL_TY2025_STD_DED_SINGLE_MIN

    def test_single_phase_window_decreases(self):
        """Phase-down values strictly decrease with AGI inside the window."""
        prev = AL_TY2025_STD_DED_SINGLE_MAX
        for agi in [Decimal("24000"), Decimal("26000"), Decimal("28000"), Decimal("30000")]:
            cur = al_standard_deduction(FilingStatus.SINGLE, agi)
            assert cur <= prev
            prev = cur

    def test_mfj_below_phase_start_is_max(self):
        assert al_standard_deduction(
            FilingStatus.MFJ, Decimal("23500")
        ) == AL_TY2025_STD_DED_MFJ_MAX

    def test_mfj_above_phase_end_is_min(self):
        assert al_standard_deduction(
            FilingStatus.MFJ, Decimal("33500")
        ) == AL_TY2025_STD_DED_MFJ_MIN
        assert al_standard_deduction(
            FilingStatus.MFJ, Decimal("120000")
        ) == AL_TY2025_STD_DED_MFJ_MIN

    def test_hoh_uses_single_schedule(self):
        """AL HOH uses the Single phase-down (max $3,000)."""
        assert al_standard_deduction(
            FilingStatus.HOH, Decimal("65000")
        ) == AL_TY2025_STD_DED_SINGLE_MIN

    def test_mfs_uses_single_schedule(self):
        assert al_standard_deduction(
            FilingStatus.MFS, Decimal("65000")
        ) == AL_TY2025_STD_DED_SINGLE_MIN


# ---------------------------------------------------------------------------
# Personal and dependent exemption helpers
# ---------------------------------------------------------------------------


class TestAlabamaPersonalAndDependentExemption:
    def test_personal_exemption_single(self):
        assert (
            al_personal_exemption(FilingStatus.SINGLE)
            == AL_TY2025_PERSONAL_EXEMPTION_SINGLE
        )
        assert al_personal_exemption(FilingStatus.SINGLE) == Decimal("1500")

    def test_personal_exemption_mfj(self):
        assert (
            al_personal_exemption(FilingStatus.MFJ)
            == AL_TY2025_PERSONAL_EXEMPTION_MFJ
        )
        assert al_personal_exemption(FilingStatus.MFJ) == Decimal("3000")

    def test_personal_exemption_mfs(self):
        assert al_personal_exemption(FilingStatus.MFS) == Decimal("1500")

    def test_personal_exemption_hoh(self):
        """AL Head of Family takes the MFJ-equivalent $3,000 personal exemption."""
        assert al_personal_exemption(FilingStatus.HOH) == Decimal("3000")

    def test_dependent_exemption_high_tier(self):
        """AGI ≤ $20,000: $1,000 per dependent."""
        assert al_dependent_exemption(Decimal("15000"), 2) == Decimal("2000")
        assert al_dependent_exemption(Decimal("20000"), 1) == AL_TY2025_DEPENDENT_HIGH

    def test_dependent_exemption_mid_tier(self):
        """$20k < AGI ≤ $100k: $500 per dependent."""
        assert al_dependent_exemption(Decimal("65000"), 2) == Decimal("1000")
        assert al_dependent_exemption(Decimal("100000"), 1) == AL_TY2025_DEPENDENT_MID

    def test_dependent_exemption_low_tier(self):
        """AGI > $100,000: $300 per dependent."""
        assert al_dependent_exemption(Decimal("150000"), 2) == Decimal("600")
        assert al_dependent_exemption(Decimal("200000"), 1) == AL_TY2025_DEPENDENT_LOW

    def test_dependent_exemption_zero_dependents(self):
        assert al_dependent_exemption(Decimal("65000"), 0) == Decimal("0")

    def test_dependent_exemption_negative_clamps_to_zero(self):
        assert al_dependent_exemption(Decimal("65000"), -3) == Decimal("0")


# ---------------------------------------------------------------------------
# Tax rate schedule
# ---------------------------------------------------------------------------


class TestAlabamaTaxRateSchedule:
    """ALA. CODE § 40-18-5 rate schedule (capped at 5% by Amendment 25)."""

    def test_zero_taxable_income(self):
        assert al_tax_from_schedule(Decimal("0"), FilingStatus.SINGLE) == Decimal("0.00")
        assert al_tax_from_schedule(Decimal("0"), FilingStatus.MFJ) == Decimal("0.00")

    def test_negative_taxable_income_clamps_to_zero(self):
        assert al_tax_from_schedule(Decimal("-1000"), FilingStatus.SINGLE) == Decimal("0.00")

    def test_single_at_first_bracket_top(self):
        """$500 Single: 2% × 500 = $10."""
        assert al_tax_from_schedule(Decimal("500"), FilingStatus.SINGLE) == Decimal("10.00")

    def test_single_at_second_bracket_top(self):
        """$3,000 Single: $10 + 4% × $2,500 = $110."""
        assert al_tax_from_schedule(Decimal("3000"), FilingStatus.SINGLE) == Decimal("110.00")

    def test_single_top_bracket(self):
        """$10,000 Single: $110 + 5% × $7,000 = $460."""
        assert al_tax_from_schedule(Decimal("10000"), FilingStatus.SINGLE) == Decimal("460.00")

    def test_mfj_at_first_bracket_top(self):
        """$1,000 MFJ: 2% × 1000 = $20."""
        assert al_tax_from_schedule(Decimal("1000"), FilingStatus.MFJ) == Decimal("20.00")

    def test_mfj_at_second_bracket_top(self):
        """$6,000 MFJ: $20 + 4% × $5,000 = $220."""
        assert al_tax_from_schedule(Decimal("6000"), FilingStatus.MFJ) == Decimal("220.00")

    def test_mfj_top_bracket(self):
        """$10,000 MFJ: $220 + 5% × $4,000 = $420."""
        assert al_tax_from_schedule(Decimal("10000"), FilingStatus.MFJ) == Decimal("420.00")

    def test_hoh_uses_single_brackets(self):
        """AL Head of Family uses the Single rate schedule."""
        ti = Decimal("30000")
        assert al_tax_from_schedule(
            ti, FilingStatus.HOH
        ) == al_tax_from_schedule(ti, FilingStatus.SINGLE)

    def test_mfs_uses_single_brackets(self):
        ti = Decimal("30000")
        assert al_tax_from_schedule(
            ti, FilingStatus.MFS
        ) == al_tax_from_schedule(ti, FilingStatus.SINGLE)

    def test_qss_uses_mfj_brackets(self):
        ti = Decimal("30000")
        assert al_tax_from_schedule(
            ti, FilingStatus.QSS
        ) == al_tax_from_schedule(ti, FilingStatus.MFJ)

    def test_constants_match_brackets(self):
        """Top rate is 5% per AL Const. Amendment 25."""
        assert AL_TY2025_BRACKETS_SINGLE[-1].rate == Decimal("0.05")
        assert AL_TY2025_BRACKETS_MFJ[-1].rate == Decimal("0.05")
        assert AL_TY2025_BRACKETS_SINGLE[0].rate == Decimal("0.02")
        assert AL_TY2025_BRACKETS_MFJ[0].rate == Decimal("0.02")


# ---------------------------------------------------------------------------
# compute() — resident scenarios
# ---------------------------------------------------------------------------


class TestAlabamaPluginComputeResident:
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
        assert result.state == "AL"
        assert result.residency == ResidencyStatus.RESIDENT
        assert result.days_in_state == 365


class TestAlabamaTaxLockSingle65k:
    """**SPEC-MANDATED $65k Single TAX LOCK** for Alabama.

    Hand trace from AL Form 40 (TY2025):

        Line 5/7  AL AGI                            = $65,000.00
        Line 9   Federal income tax deduction       = $5,755.00
        Line 10  AL standard deduction (Single,
                 sliding scale at AGI=$65k → min)   = $2,500.00
        Line 13  Personal exemption (Single)        = $1,500.00
        Line 14  Dependent exemption (0 deps)       = $0.00
        Line 16  AL taxable income
                 = 65,000 - 5,755 - 2,500 - 1,500   = $55,245.00
        Line 17  Tax via Single rate schedule:
                   2% × $500    = $10.00
                   4% × $2,500  = $100.00
                   5% × $52,245 = $2,612.25
                   Sum                              = $2,722.25

    The tenforty graph backend reports $3,210 (rate-schedule × full
    AGI with no deductions). Hand-rolling closes the $487.75 gap.
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
        assert ss["state_federal_income_tax_deduction"] == Decimal("5755.00")
        assert ss["state_standard_deduction"] == Decimal("2500.00")
        assert ss["state_personal_exemption"] == Decimal("1500.00")
        assert ss["state_dependent_exemption"] == Decimal("0.00")
        assert ss["state_total_deductions"] == Decimal("9755.00")
        assert ss["state_taxable_income"] == Decimal("55245.00")


class TestAlabamaPluginComputeOtherResidents:
    def test_resident_mfj_120k(self, mfj_120k_return, federal_mfj_120k):
        """MFJ $120k, federal income tax = $10,173.

            AL AGI            = $120,000
            Line 9 FIT        = $10,173
            Line 10 std (MFJ
                @ AGI > $33,500) = $4,000
            Line 13 personal  = $3,000
            Line 16 AL TI     = 120,000 - 10,173 - 4,000 - 3,000 = $102,827
            Line 17 (MFJ): $20 + 4%×$5,000 + 5%×($102,827 - 6,000)
                         = $20 + $200 + $4,841.35 = $5,061.35
        """
        result = PLUGIN.compute(
            mfj_120k_return,
            federal_mfj_120k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = result.state_specific
        assert ss["state_federal_income_tax_deduction"] == Decimal("10173.00")
        assert ss["state_standard_deduction"] == Decimal("4000.00")
        assert ss["state_personal_exemption"] == Decimal("3000.00")
        assert ss["state_taxable_income"] == Decimal("102827.00")
        assert ss["state_total_tax"] == Decimal("5061.35")

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
            address=Address(street1="1 Main", city="Mobile", state="AL", zip="36602"),
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
            "state_federal_income_tax_deduction",
            "state_standard_deduction",
            "state_personal_exemption",
            "state_dependent_exemption",
            "state_total_deductions",
            "state_taxable_income",
            "state_total_tax",
            "state_total_tax_resident_basis",
            "apportionment_fraction",
            "al_modifications_applied",
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
        assert rehydrated.state == "AL"

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


class TestAlabamaPluginComputeNonresident:
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


class TestAlabamaPluginApportionIncome:
    def test_resident_full_amounts(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert isinstance(app, IncomeApportionment)
        assert app.state_source_wages == Decimal("65000.00")
        assert app.state_source_interest == Decimal("0")
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


class TestAlabamaPluginFormIds:
    def test_form_ids(self):
        assert PLUGIN.form_ids() == ["AL Form 40"]

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


class TestAlabamaV1Limitations:
    def test_limitations_is_tuple(self):
        assert isinstance(AL_V1_LIMITATIONS, tuple)
        assert len(AL_V1_LIMITATIONS) >= 5

    def test_limitations_mention_form_40nr(self):
        joined = " ".join(AL_V1_LIMITATIONS)
        assert "40NR" in joined or "nonresident" in joined.lower()

    def test_limitations_mention_credit_for_taxes_paid(self):
        joined = " ".join(AL_V1_LIMITATIONS).lower()
        assert "credit" in joined and "other states" in joined


# ---------------------------------------------------------------------------
# Tenforty gap gatekeeper — pinned discrepancy on the graph backend
# ---------------------------------------------------------------------------


class TestTenfortyStillHasGapOnAL:
    """Re-probes tenforty's graph backend for AL and asserts the gap.

    When the tenforty graph backend is fixed to apply the AL federal
    income tax deduction, AL standard deduction, and personal exemption,
    THIS TEST WILL FAIL — at which point the AL plugin can be revisited
    (potentially converted to a graph wrapper, copying the WI pattern,
    and this test deleted).

    Locks the discrepancy at the CP8-B reference value of $3,210 (graph
    backend; rate schedule applied to gross AGI with no deductions)
    versus the hand-rolled value of $2,722.25.
    """

    GRAPH_BACKEND_REFERENCE_TAX = Decimal("3210.00")
    HAND_ROLLED_LOCK_VALUE = Decimal("2722.25")

    def test_graph_backend_still_omits_deductions(self):
        try:
            import tenforty  # type: ignore[import-not-found]
        except ImportError:
            pytest.skip("tenforty not installed")

        try:
            tf = tenforty.evaluate_return(
                year=2025,
                state="AL",
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
        # If this assertion ever STARTS FAILING, tenforty has shipped a
        # fix to the graph backend's AL form definition. Re-verify
        # against AL DOR primary source and consider converting al.py
        # to a graph wrapper following the WI pattern.
        assert graph_tax == self.GRAPH_BACKEND_REFERENCE_TAX, (
            f"Graph backend tax for AL Single $65k changed: "
            f"expected ${self.GRAPH_BACKEND_REFERENCE_TAX} (CP8-B "
            f"probe value, no deductions applied) but got ${graph_tax}. "
            f"Re-verify the AL hand-roll vs the new graph behavior."
        )

    def test_hand_roll_diverges_from_graph_by_at_least_400(
        self, single_65k_return, federal_single_65k
    ):
        """Hand-roll is materially less than the graph backend."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        hand = result.state_specific["state_total_tax"]
        assert hand == self.HAND_ROLLED_LOCK_VALUE
        delta = self.GRAPH_BACKEND_REFERENCE_TAX - hand
        # The gap is ~$487.75 (graph omits ~$9,755 of deductions × 5%).
        assert delta > Decimal("400")
