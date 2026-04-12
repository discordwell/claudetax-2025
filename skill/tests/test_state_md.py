"""Maryland state plugin tests.

Covers :class:`MarylandPlugin`, a **hand-rolled** TY2025 Form 502 calc
(tenforty does NOT support MD_502 — big wave-4 finding; see module docstring
in ``skill/scripts/states/md.py``).

Locked reference scenarios (verified against Maryland Comptroller primary
sources — Withholding Tax Facts 2025 and the 2025 Tax Alert on deduction /
rate changes):

    Scenario A  Single $65k W-2 resident, 2.25% nonresident default local:
        MD AGI                          $65,000
        Standard deduction              $3,350      (new flat TY2025 value)
        Personal exemption (1x)         $3,200
        MD taxable net income           $58,450
        State tax (Schedule I, 4.75%)   $2,723.88  = $90 + 4.75% * $55,450
        Local tax (2.25% default)       $1,315.13  = $58,450 * 0.0225
        Total                           $4,039.01

    Scenario B  Single $65k W-2 resident, Baltimore City @ 3.20%:
        Same MD taxable net income      $58,450
        State tax                       $2,723.88
        Local tax (3.20%)               $1,870.40  = $58,450 * 0.0320
        Total                           $4,594.28

    Scenario C  Single $65k W-2 resident, Anne Arundel (progressive):
        State tax                       $2,723.88
        Local tax                       $1,598.43
          = $50,000 * 0.0270 + $8,450 * 0.0294
          = $1,350.00 + $248.43
        Total                           $4,322.31

    Scenario D  Single $65k W-2 resident, Frederick (progressive, 5-tier):
        State tax                       $2,723.88
        Local tax                       $1,500.12
          = $25,000 * 0.0225 + $25,000 * 0.0275 + $8,450 * 0.0296
          = $562.50 + $687.50 + $250.12
        Total                           $4,224.00

IMPORTANT (locked in TestMarylandTenfortyLimitation): tenforty does NOT
include MD local tax because tenforty does not support MD at all — calling
``tenforty.evaluate_return(..., state='MD')`` raises
``ValueError: OTS does not support 2025/MD_502`` for every year it ships.
This plugin is the only code that produces MD numbers in the skill.
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
from skill.scripts.states.md import (
    LOCK_VALUE,
    MD_TY2025_ANNE_ARUNDEL_BRACKETS_SCHEDULE_I,
    MD_TY2025_ANNE_ARUNDEL_BRACKETS_SCHEDULE_II,
    MD_TY2025_BRACKETS_SCHEDULE_I,
    MD_TY2025_BRACKETS_SCHEDULE_II,
    MD_TY2025_FLAT_COUNTY_RATES,
    MD_TY2025_FREDERICK_BRACKETS_SCHEDULE_I,
    MD_TY2025_FREDERICK_BRACKETS_SCHEDULE_II,
    MD_TY2025_NONRESIDENT_DEFAULT_LOCAL_RATE,
    MD_TY2025_PERSONAL_EXEMPTION_WITHHOLDING_DEFAULT,
    MD_TY2025_STANDARD_DEDUCTION,
    MD_V1_LIMITATIONS,
    PLUGIN,
    MarylandPlugin,
    _md_local_tax,
    _md_state_tax,
    _normalize_county,
)


# ---------------------------------------------------------------------------
# Fixtures — Single $65k W-2 resident + matching FederalTotals
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
            street1="1 Main St", city="Baltimore", state="MD", zip="21201"
        ),
        w2s=[
            W2(
                employer_name="Acme Corp",
                box1_wages=Decimal("65000"),
                box2_federal_income_tax_withheld=Decimal("0"),
            )
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
            first_name="Pat",
            last_name="Roe",
            ssn="111-22-4444",
            date_of_birth=dt.date(1985, 1, 1),
        ),
        spouse=Person(
            first_name="Sam",
            last_name="Roe",
            ssn="111-22-5555",
            date_of_birth=dt.date(1986, 2, 2),
        ),
        address=Address(
            street1="2 Oak Rd",
            city="Rockville",
            state="MD",
            zip="20850",
        ),
        w2s=[
            W2(
                employer_name="Acme",
                box1_wages=Decimal("120000"),
                box2_federal_income_tax_withheld=Decimal("0"),
            )
        ],
    )


@pytest.fixture
def federal_mfj_120k() -> FederalTotals:
    return FederalTotals(
        filing_status=FilingStatus.MFJ,
        num_dependents=0,
        adjusted_gross_income=Decimal("120000"),
        taxable_income=Decimal("88500"),
        total_federal_tax=Decimal("9735"),
        federal_income_tax=Decimal("9735"),
        federal_standard_deduction=Decimal("31500"),
        federal_itemized_deductions_total=Decimal("0"),
        deduction_taken=Decimal("31500"),
        federal_withholding_from_w2s=Decimal("0"),
    )


# ---------------------------------------------------------------------------
# Protocol conformance + metadata
# ---------------------------------------------------------------------------


class TestMarylandPluginMeta:
    def test_protocol_satisfied_at_runtime(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_maryland_plugin_instance(self):
        assert isinstance(PLUGIN, MarylandPlugin)

    def test_meta_code(self):
        assert PLUGIN.meta.code == "MD"

    def test_meta_name(self):
        assert PLUGIN.meta.name == "Maryland"

    def test_meta_has_income_tax(self):
        assert PLUGIN.meta.has_income_tax is True

    def test_meta_starting_point_is_federal_agi(self):
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI

    def test_meta_submission_channel_is_state_dor_free_portal(self):
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )

    def test_meta_dor_url(self):
        assert "marylandtaxes.gov" in PLUGIN.meta.dor_url

    def test_meta_free_efile_url_is_ifile(self):
        assert PLUGIN.meta.free_efile_url is not None
        assert "marylandtaxes.gov" in PLUGIN.meta.free_efile_url
        assert "iFile" in PLUGIN.meta.free_efile_url

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_hand_rolled(self):
        notes = PLUGIN.meta.notes.lower()
        assert "hand-rolled" in notes

    def test_meta_notes_mention_tenforty_limitation(self):
        """The MD plugin is unusual — note must call out the tenforty gap."""
        assert "tenforty" in PLUGIN.meta.notes.lower()

    def test_meta_notes_mention_new_brackets(self):
        notes = PLUGIN.meta.notes
        assert "6.25" in notes or "6.50" in notes

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "NY"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Reciprocity — MD has exactly 4 bilateral partners: DC, PA, VA, WV
# ---------------------------------------------------------------------------


class TestMarylandReciprocity:
    def test_meta_reciprocity_partners_contains_all_four(self):
        partners = set(PLUGIN.meta.reciprocity_partners)
        assert partners == {"DC", "PA", "VA", "WV"}

    def test_meta_reciprocity_partners_individual_dc(self):
        assert "DC" in set(PLUGIN.meta.reciprocity_partners)

    def test_meta_reciprocity_partners_individual_pa(self):
        assert "PA" in set(PLUGIN.meta.reciprocity_partners)

    def test_meta_reciprocity_partners_individual_va(self):
        assert "VA" in set(PLUGIN.meta.reciprocity_partners)

    def test_meta_reciprocity_partners_individual_wv(self):
        assert "WV" in set(PLUGIN.meta.reciprocity_partners)

    def test_meta_reciprocity_partners_is_tuple(self):
        assert isinstance(PLUGIN.meta.reciprocity_partners, tuple)

    def test_reciprocity_table_matches_plugin_meta(self):
        """The bilateral pairs in state-reciprocity.json must match the
        plugin's advertised ``reciprocity_partners`` exactly. Both sides of
        each pair must list the other."""
        table = ReciprocityTable.load()
        assert table.partners_of("MD") == frozenset({"DC", "PA", "VA", "WV"})

    def test_md_dc_reciprocity(self):
        assert ReciprocityTable.load().are_reciprocal("MD", "DC")

    def test_md_pa_reciprocity(self):
        assert ReciprocityTable.load().are_reciprocal("MD", "PA")

    def test_md_va_reciprocity(self):
        assert ReciprocityTable.load().are_reciprocal("MD", "VA")

    def test_md_wv_reciprocity(self):
        assert ReciprocityTable.load().are_reciprocal("MD", "WV")

    def test_md_ny_not_reciprocal(self):
        """Negative control — MD has no agreement with NY."""
        assert not ReciprocityTable.load().are_reciprocal("MD", "NY")


# ---------------------------------------------------------------------------
# compute() — Single $65k resident, locked reference scenarios
# ---------------------------------------------------------------------------


class TestMarylandComputeSingle65kResident:
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

    def test_state_code_is_md(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
        )
        assert result.state == "MD"

    def test_md_standard_deduction_is_3350_flat(
        self, single_65k_return, federal_single_65k
    ):
        """TY2025 Single std deduction is the new flat $3,350, NOT the old
        income-based 15%-of-MD-AGI formula."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
        )
        assert result.state_specific["md_standard_deduction"] == Decimal("3350.00")

    def test_md_personal_exemption_is_3200(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
        )
        assert result.state_specific["md_personal_exemption_total"] == Decimal(
            "3200.00"
        )

    def test_md_taxable_net_income_is_58450(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
        )
        assert result.state_specific["md_taxable_net_income"] == Decimal(
            "58450.00"
        )

    def test_locked_state_tax_only_is_2723_88(
        self, single_65k_return, federal_single_65k
    ):
        """LOCK: $90 + 4.75% * $55,450 = $2,723.875 → $2,723.88.

        This is the MD STATE tax only — NOT including local county tax.
        Tenforty does not compute this at all; this number is produced
        entirely by the hand-rolled bracket schedule in ``md.py``.
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
        )
        assert result.state_specific["state_tax_only"] == Decimal("2723.88")

    def test_locked_default_local_tax_2_25_percent(
        self, single_65k_return, federal_single_65k
    ):
        """Default MD address has no county → 2.25% nonresident default."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
        )
        assert result.state_specific["local_tax"] == Decimal("1315.13")

    def test_resident_single_65k_tax_lock(
        self, single_65k_return, federal_single_65k
    ):
        """LOCK: state_total_tax = $2,723.88 + $1,315.13 = $4,039.01.

        This is the PLUGIN's total MD tax with the 2.25% default local rate
        — it is the locked $65k number for the wave-4 deliverable. Tenforty
        does NOT reproduce this because tenforty does not support MD."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
        )
        assert result.state_specific["state_total_tax"] == LOCK_VALUE

    def test_default_local_tax_note_flags_default(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
        )
        note = result.state_specific["local_tax_note"]
        assert "2.25" in note or "default" in note.lower()

    def test_resident_apportionment_is_one(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
        )
        assert result.state_specific["apportionment_fraction"] == Decimal("1")

    def test_md_agi_equals_federal_agi(
        self, single_65k_return, federal_single_65k
    ):
        """v1 approximates MD AGI as federal AGI (flagged in limitations)."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
        )
        assert result.state_specific["md_adjusted_gross_income"] == Decimal(
            "65000.00"
        )

    def test_starting_point_federal_agi(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
        )
        assert result.state_specific["starting_point"] == "federal_agi"

    def test_state_return_roundtrips_via_pydantic(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
        )
        dumped = result.model_dump(mode="json")
        rehydrated = StateReturn.model_validate(dumped)
        assert rehydrated.state == "MD"
        assert rehydrated.residency == ResidencyStatus.RESIDENT

    def test_days_in_state_preserved(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
        )
        assert result.days_in_state == 365


# ---------------------------------------------------------------------------
# compute_with_county() — overrides and per-county locked scenarios
# ---------------------------------------------------------------------------


class TestMarylandComputeWithCounty:
    def test_baltimore_city_locked_local_tax(
        self, single_65k_return, federal_single_65k
    ):
        """LOCK: $58,450 * 0.0320 = $1,870.40."""
        result = PLUGIN.compute_with_county(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
            county="Baltimore City",
        )
        assert result.state_specific["local_tax"] == Decimal("1870.40")

    def test_baltimore_city_locked_total(
        self, single_65k_return, federal_single_65k
    ):
        """LOCK: $2,723.88 + $1,870.40 = $4,594.28 (Baltimore City 3.20%)."""
        result = PLUGIN.compute_with_county(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
            county="Baltimore City",
        )
        assert result.state_specific["state_total_tax"] == Decimal("4594.28")

    def test_baltimore_county_distinct_from_baltimore_city(
        self, single_65k_return, federal_single_65k
    ):
        """Baltimore County != Baltimore City; both happen to be 3.20% in 2025
        but callers should still be able to distinguish them."""
        result = PLUGIN.compute_with_county(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
            county="Baltimore County",
        )
        assert result.state_specific["local_county_rate_effective"] == Decimal(
            "0.0320"
        )
        assert result.state_specific["local_tax"] == Decimal("1870.40")

    def test_dorchester_retroactive_3_30_hike(
        self, single_65k_return, federal_single_65k
    ):
        """LOCK: Dorchester retroactively hiked to 3.30% for TY2025.
        $58,450 * 0.0330 = $1,928.85."""
        result = PLUGIN.compute_with_county(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
            county="Dorchester",
        )
        assert result.state_specific["local_county_rate_effective"] == Decimal(
            "0.0330"
        )
        assert result.state_specific["local_tax"] == Decimal("1928.85")

    def test_worcester_low_rate(
        self, single_65k_return, federal_single_65k
    ):
        """Worcester = 2.25% (lowest county flat rate in TY2025)."""
        result = PLUGIN.compute_with_county(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
            county="Worcester",
        )
        assert result.state_specific["local_county_rate_effective"] == Decimal(
            "0.0225"
        )

    def test_talbot_2_40(self, single_65k_return, federal_single_65k):
        """Talbot = 2.40% flat."""
        result = PLUGIN.compute_with_county(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
            county="Talbot",
        )
        assert result.state_specific["local_county_rate_effective"] == Decimal(
            "0.0240"
        )

    def test_garrett_2_65(self, single_65k_return, federal_single_65k):
        """Garrett = 2.65% flat."""
        result = PLUGIN.compute_with_county(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
            county="Garrett",
        )
        assert result.state_specific["local_county_rate_effective"] == Decimal(
            "0.0265"
        )

    def test_cecil_2_74(self, single_65k_return, federal_single_65k):
        """Cecil = 2.74% flat."""
        result = PLUGIN.compute_with_county(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
            county="Cecil",
        )
        assert result.state_specific["local_county_rate_effective"] == Decimal(
            "0.0274"
        )

    def test_anne_arundel_progressive_locked(
        self, single_65k_return, federal_single_65k
    ):
        """LOCK: Anne Arundel Single at $58,450 MD TI =
        $50,000*.0270 + $8,450*.0294 = $1,350 + $248.43 = $1,598.43."""
        result = PLUGIN.compute_with_county(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
            county="Anne Arundel",
        )
        assert result.state_specific["local_tax"] == Decimal("1598.43")

    def test_frederick_progressive_locked(
        self, single_65k_return, federal_single_65k
    ):
        """LOCK: Frederick Single at $58,450 MD TI =
        $25,000*.0225 + $25,000*.0275 + $8,450*.0296
        = $562.50 + $687.50 + $250.12 = $1,500.12."""
        result = PLUGIN.compute_with_county(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
            county="Frederick",
        )
        assert result.state_specific["local_tax"] == Decimal("1500.12")

    def test_unknown_county_falls_back_to_default(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute_with_county(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
            county="Gotham",
        )
        assert result.state_specific["local_county_rate_effective"] == Decimal(
            "0.0225"
        )
        assert "unknown county" in result.state_specific["local_tax_note"].lower()

    def test_case_insensitive_county(
        self, single_65k_return, federal_single_65k
    ):
        """County lookup normalizes case."""
        r1 = PLUGIN.compute_with_county(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
            county="HOWARD",
        )
        r2 = PLUGIN.compute_with_county(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
            county="howard",
        )
        assert r1.state_specific["local_tax"] == r2.state_specific["local_tax"]

    def test_county_suffix_stripped(
        self, single_65k_return, federal_single_65k
    ):
        """'Howard County' and 'Howard' resolve to the same rate."""
        r1 = PLUGIN.compute_with_county(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
            county="Howard County",
        )
        r2 = PLUGIN.compute_with_county(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
            county="Howard",
        )
        assert r1.state_specific["local_tax"] == r2.state_specific["local_tax"]


# ---------------------------------------------------------------------------
# compute() — MFJ Schedule II
# ---------------------------------------------------------------------------


class TestMarylandComputeMfj:
    def test_mfj_std_deduction_is_6700(self, mfj_120k_return, federal_mfj_120k):
        result = PLUGIN.compute(
            mfj_120k_return,
            federal_mfj_120k,
            ResidencyStatus.RESIDENT,
            365,
        )
        assert result.state_specific["md_standard_deduction"] == Decimal(
            "6700.00"
        )

    def test_mfj_two_personal_exemptions(
        self, mfj_120k_return, federal_mfj_120k
    ):
        """MFJ with 0 dependents = 2 exemptions (taxpayer + spouse)."""
        result = PLUGIN.compute(
            mfj_120k_return,
            federal_mfj_120k,
            ResidencyStatus.RESIDENT,
            365,
        )
        assert result.state_specific["md_personal_exemptions"] == 2
        assert result.state_specific["md_personal_exemption_total"] == Decimal(
            "6400.00"
        )

    def test_mfj_120k_taxable_net_income(
        self, mfj_120k_return, federal_mfj_120k
    ):
        """MFJ $120k - $6,700 std - $6,400 PE = $106,900 MD TI."""
        result = PLUGIN.compute(
            mfj_120k_return,
            federal_mfj_120k,
            ResidencyStatus.RESIDENT,
            365,
        )
        assert result.state_specific["md_taxable_net_income"] == Decimal(
            "106900.00"
        )

    def test_mfj_120k_state_tax(self, mfj_120k_return, federal_mfj_120k):
        """MFJ $106,900 MD TI uses Schedule II 4.75% bracket (extends to
        $150k for joint filers): $90 + 4.75% * $103,900 = $5,025.25."""
        result = PLUGIN.compute(
            mfj_120k_return,
            federal_mfj_120k,
            ResidencyStatus.RESIDENT,
            365,
        )
        expected = Decimal("90") + Decimal("103900") * Decimal("0.0475")
        assert result.state_specific["state_tax_only"] == expected.quantize(
            Decimal("0.01")
        )


# ---------------------------------------------------------------------------
# Nonresident / part-year apportionment
# ---------------------------------------------------------------------------


class TestMarylandComputeNonresident:
    def test_nonresident_half_year_proration(
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
        # Proration ~ 182/365
        ratio = Decimal(182) / Decimal(365)
        expected = (full * ratio).quantize(Decimal("0.01"))
        assert apportioned == expected

    def test_nonresident_apportionment_fraction(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=100,
        )
        assert result.state_specific["apportionment_fraction"] == Decimal(
            100
        ) / Decimal(365)

    def test_nonresident_zero_days_yields_zero_tax(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=0,
        )
        assert result.state_specific["state_total_tax"] == Decimal("0.00")

    def test_part_year_apportionment(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.PART_YEAR,
            days_in_state=91,
        )
        assert result.state_specific["apportionment_fraction"] == Decimal(
            91
        ) / Decimal(365)

    def test_nonresident_preserves_residency(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=182,
        )
        assert result.residency == ResidencyStatus.NONRESIDENT


# ---------------------------------------------------------------------------
# Bracket schedule sanity — Schedule I and Schedule II coverage
# ---------------------------------------------------------------------------


class TestMarylandBracketSchedules:
    def test_schedule_i_first_bracket_2_percent(self):
        """Schedule I: $500 TI → $500 * 2% = $10."""
        tax = _md_state_tax(Decimal("500"), FilingStatus.SINGLE)
        assert tax == Decimal("10.00")

    def test_schedule_i_third_bracket_base_tax_exact(self):
        """Schedule I: $2,500 TI → $50 + $500 * 4% = $70."""
        tax = _md_state_tax(Decimal("2500"), FilingStatus.SINGLE)
        assert tax == Decimal("70.00")

    def test_schedule_i_4_75_percent_bracket(self):
        """Schedule I: $50,000 TI → $90 + $47,000 * 4.75% = $2,322.50."""
        tax = _md_state_tax(Decimal("50000"), FilingStatus.SINGLE)
        assert tax == Decimal("2322.50")

    def test_schedule_i_top_bracket_6_50_percent(self):
        """TY2025 NEW top bracket: $2M TI → $58,385 + $1M * 6.50% = $123,385."""
        tax = _md_state_tax(Decimal("2000000"), FilingStatus.SINGLE)
        assert tax == Decimal("123385.00")

    def test_schedule_i_new_6_25_bracket(self):
        """TY2025 NEW 6.25% bracket: $750k TI Single →
        $27,135 + $250k * 6.25% = $42,760."""
        tax = _md_state_tax(Decimal("750000"), FilingStatus.SINGLE)
        assert tax == Decimal("42760.00")

    def test_schedule_ii_uses_same_2_percent_floor(self):
        """Schedule II first bracket: $500 → $10 (same as Schedule I)."""
        tax = _md_state_tax(Decimal("500"), FilingStatus.MFJ)
        assert tax == Decimal("10.00")

    def test_schedule_ii_4_75_extends_to_150k(self):
        """Schedule II: $150,000 TI (MFJ) hits the edge of the 4.75% bracket:
        $90 + $147,000 * 4.75% = $7,072.50."""
        tax = _md_state_tax(Decimal("150000"), FilingStatus.MFJ)
        assert tax == Decimal("7072.50")

    def test_schedule_ii_new_top_bracket(self):
        """Schedule II top bracket (TY2025+): $1.5M MFJ →
        $69,822.50 + $300k * 6.50% = $89,322.50."""
        tax = _md_state_tax(Decimal("1500000"), FilingStatus.MFJ)
        assert tax == Decimal("89322.50")

    def test_zero_income_zero_tax(self):
        """Zero income → zero tax (both schedules)."""
        assert _md_state_tax(Decimal("0"), FilingStatus.SINGLE) == Decimal("0")
        assert _md_state_tax(Decimal("0"), FilingStatus.MFJ) == Decimal("0")

    def test_negative_income_zero_tax(self):
        assert _md_state_tax(Decimal("-1000"), FilingStatus.SINGLE) == Decimal(
            "0"
        )

    def test_schedule_i_bracket_count(self):
        """Schedule I has exactly 10 brackets (8 old + 2 new for TY2025)."""
        assert len(MD_TY2025_BRACKETS_SCHEDULE_I) == 10

    def test_schedule_ii_bracket_count(self):
        """Schedule II has exactly 10 brackets."""
        assert len(MD_TY2025_BRACKETS_SCHEDULE_II) == 10

    def test_schedule_i_last_bracket_has_no_upper_bound(self):
        assert MD_TY2025_BRACKETS_SCHEDULE_I[-1][0] is None

    def test_schedule_ii_last_bracket_has_no_upper_bound(self):
        assert MD_TY2025_BRACKETS_SCHEDULE_II[-1][0] is None


# ---------------------------------------------------------------------------
# County tax table coverage and internals
# ---------------------------------------------------------------------------


class TestMarylandCountyTax:
    def test_all_23_counties_plus_baltimore_city_represented(self):
        """Withholding Tax Facts 2025 lists 24 jurisdictions. v1 flat table
        covers 22 flat-rate jurisdictions (Anne Arundel and Frederick are
        handled as progressive-bracket counties, not flat). Smoke test:
        Baltimore City, Baltimore County, Howard, Kent, Montgomery, Prince
        George's, Queen Anne's, Somerset, St. Mary's, Talbot, Washington,
        Wicomico, Worcester, Allegany, Calvert, Caroline, Carroll, Cecil,
        Charles, Dorchester, Garrett, Harford = 22 keys."""
        normalized = {
            _normalize_county(k)
            for k in [
                "Allegany",
                "Baltimore City",
                "Baltimore County",
                "Calvert",
                "Caroline",
                "Carroll",
                "Cecil",
                "Charles",
                "Dorchester",
                "Garrett",
                "Harford",
                "Howard",
                "Kent",
                "Montgomery",
                "Prince George's",
                "Queen Anne's",
                "Somerset",
                "St. Mary's",
                "Talbot",
                "Washington",
                "Wicomico",
                "Worcester",
            ]
        }
        for n in normalized:
            assert n in MD_TY2025_FLAT_COUNTY_RATES, f"missing {n}"

    def test_nonresident_default_rate_is_2_25(self):
        assert MD_TY2025_NONRESIDENT_DEFAULT_LOCAL_RATE == Decimal("0.0225")

    def test_all_flat_rates_in_range(self):
        """Every flat county rate must be in [2.25%, 3.30%] — the TY2025 cap
        set by the Budget Reconciliation and Financing Act of 2025."""
        for key, rate in MD_TY2025_FLAT_COUNTY_RATES.items():
            assert Decimal("0.0225") <= rate <= Decimal("0.0330"), (
                f"{key} rate {rate} out of range"
            )

    def test_dorchester_hiked_to_3_30(self):
        assert MD_TY2025_FLAT_COUNTY_RATES["dorchester"] == Decimal("0.0330")

    def test_worcester_is_2_25_lowest(self):
        assert MD_TY2025_FLAT_COUNTY_RATES["worcester"] == Decimal("0.0225")

    def test_baltimore_city_3_20(self):
        assert MD_TY2025_FLAT_COUNTY_RATES["baltimore city"] == Decimal("0.0320")

    def test_montgomery_3_20(self):
        assert MD_TY2025_FLAT_COUNTY_RATES["montgomery"] == Decimal("0.0320")

    def test_cecil_2_74(self):
        assert MD_TY2025_FLAT_COUNTY_RATES["cecil"] == Decimal("0.0274")

    def test_normalize_county_strips_whitespace(self):
        assert _normalize_county("  Howard  ") == "howard"

    def test_normalize_county_lowercases(self):
        assert _normalize_county("Howard") == "howard"

    def test_normalize_county_strips_county_suffix(self):
        assert _normalize_county("Howard County") == "howard"

    def test_normalize_county_none(self):
        assert _normalize_county(None) is None

    def test_local_tax_zero_income(self):
        tax, rate, note = _md_local_tax(
            Decimal("0"), "Baltimore City", FilingStatus.SINGLE
        )
        assert tax == Decimal("0")
        assert "zero" in note.lower()

    def test_local_tax_unknown_county_fallback(self):
        tax, rate, note = _md_local_tax(
            Decimal("58450"), "Atlantis", FilingStatus.SINGLE
        )
        assert rate == MD_TY2025_NONRESIDENT_DEFAULT_LOCAL_RATE
        assert "unknown" in note.lower()


# ---------------------------------------------------------------------------
# Anne Arundel and Frederick progressive-bracket sanity
# ---------------------------------------------------------------------------


class TestMarylandProgressiveCounties:
    def test_anne_arundel_schedule_i_bracket_count(self):
        assert len(MD_TY2025_ANNE_ARUNDEL_BRACKETS_SCHEDULE_I) == 3

    def test_anne_arundel_schedule_ii_bracket_count(self):
        assert len(MD_TY2025_ANNE_ARUNDEL_BRACKETS_SCHEDULE_II) == 3

    def test_frederick_schedule_i_has_five_brackets(self):
        assert len(MD_TY2025_FREDERICK_BRACKETS_SCHEDULE_I) == 5

    def test_frederick_schedule_ii_has_four_brackets(self):
        """MFJ/HOH/QSS uses the four-tier Frederick table (the 3.03% bracket
        is Single-only per Withholding Tax Facts 2025)."""
        assert len(MD_TY2025_FREDERICK_BRACKETS_SCHEDULE_II) == 4

    def test_anne_arundel_low_income_flat_2_70(self):
        """Anne Arundel $40k Single → all in lowest bracket: $40k * 2.70% = $1,080."""
        tax, _, _ = _md_local_tax(
            Decimal("40000"), "Anne Arundel", FilingStatus.SINGLE
        )
        assert tax == Decimal("1080.00")

    def test_anne_arundel_mid_income_bracket_breakdown(self):
        """Anne Arundel $100k Single →
        $50k * 2.70% + $50k * 2.94% = $1,350 + $1,470 = $2,820."""
        tax, _, _ = _md_local_tax(
            Decimal("100000"), "Anne Arundel", FilingStatus.SINGLE
        )
        assert tax == Decimal("2820.00")

    def test_anne_arundel_high_income_top_bracket(self):
        """Anne Arundel $500k Single →
        $50k*.0270 + $350k*.0294 + $100k*.0320
        = $1,350 + $10,290 + $3,200 = $14,840."""
        tax, _, _ = _md_local_tax(
            Decimal("500000"), "Anne Arundel", FilingStatus.SINGLE
        )
        assert tax == Decimal("14840.00")

    def test_anne_arundel_mfj_thresholds_differ(self):
        """MFJ brackets are wider: $60k MFJ is still in the 2.70% bracket
        (Schedule II threshold = $75k, not $50k)."""
        tax, _, _ = _md_local_tax(
            Decimal("60000"), "Anne Arundel", FilingStatus.MFJ
        )
        assert tax == Decimal("1620.00")

    def test_frederick_low_income_2_25(self):
        """Frederick $20k Single → $20k * 2.25% = $450."""
        tax, _, _ = _md_local_tax(
            Decimal("20000"), "Frederick", FilingStatus.SINGLE
        )
        assert tax == Decimal("450.00")

    def test_frederick_mfj_three_brackets(self):
        """Frederick MFJ $200k → $25k*.0225 + $75k*.0275 + $100k*.0296 =
        $562.50 + $2,062.50 + $2,960 = $5,585.00."""
        tax, _, _ = _md_local_tax(
            Decimal("200000"), "Frederick", FilingStatus.MFJ
        )
        assert tax == Decimal("5585.00")


# ---------------------------------------------------------------------------
# THE big finding: tenforty does NOT support MD at all
# ---------------------------------------------------------------------------


class TestMarylandTenfortyLimitation:
    """Wave 4 finding: tenforty lists MD in its OTSState enum but has NO
    working OTS implementation. Calling evaluate_return with state='MD'
    raises ``OTS does not support 2025/MD_502`` for every year. THIS IS
    THE HEADLINE FINDING OF THIS WAVE-4 DELIVERABLE."""

    def test_tenforty_raises_for_md_2025(self):
        import tenforty

        with pytest.raises(ValueError, match="does not support.*MD_502"):
            tenforty.evaluate_return(
                year=2025,
                state="MD",
                filing_status="Single",
                w2_income=65000.0,
                standard_or_itemized="Standard",
            )

    def test_tenforty_otsstate_md_exists_but_is_empty(self):
        """OTSState.MD exists in the enum — this is misleading because the
        OpenTaxSolver form for MD is not actually implemented."""
        from tenforty.models import OTSState

        assert OTSState.MD.value == "MD"

    def test_md_not_in_tenforty_supported_states_list(self):
        """Probe each shipped year; MD_502 must fail for all of them."""
        import tenforty

        for year in (2022, 2023, 2024, 2025):
            with pytest.raises(ValueError, match="does not support.*MD_502"):
                tenforty.evaluate_return(
                    year=year,
                    state="MD",
                    filing_status="Single",
                    w2_income=65000.0,
                    standard_or_itemized="Standard",
                )


# ---------------------------------------------------------------------------
# apportion_income()
# ---------------------------------------------------------------------------


class TestMarylandApportionIncome:
    def test_resident_gets_full_wages(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.RESIDENT, 365
        )
        assert isinstance(app, IncomeApportionment)
        assert app.state_source_wages == Decimal("65000.00")

    def test_nonresident_half_wages(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.NONRESIDENT, 182
        )
        expected = (Decimal("65000") * Decimal(182) / Decimal(365)).quantize(
            Decimal("0.01")
        )
        assert app.state_source_wages == expected

    def test_resident_no_investment_income(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.RESIDENT, 365
        )
        assert app.state_source_interest == Decimal("0")
        assert app.state_source_dividends == Decimal("0")
        assert app.state_source_capital_gains == Decimal("0")
        assert app.state_source_self_employment == Decimal("0")
        assert app.state_source_rental == Decimal("0")

    def test_resident_total_equals_wages(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.RESIDENT, 365
        )
        assert app.state_source_total == Decimal("65000.00")


# ---------------------------------------------------------------------------
# V1 limitations — make sure the critical flags are documented
# ---------------------------------------------------------------------------


class TestMarylandV1Limitations:
    def test_v1_limitations_not_empty(self):
        assert len(MD_V1_LIMITATIONS) > 0

    def test_v1_limitations_mention_additions_subtractions(self):
        joined = " ".join(MD_V1_LIMITATIONS).lower()
        assert "additions" in joined and "subtractions" in joined

    def test_v1_limitations_mention_personal_exemption_phaseout(self):
        joined = " ".join(MD_V1_LIMITATIONS).lower()
        assert "phase-out" in joined or "phaseout" in joined

    def test_v1_limitations_mention_local_tax(self):
        joined = " ".join(MD_V1_LIMITATIONS).lower()
        assert "local tax" in joined or "local" in joined

    def test_v1_limitations_mention_nonresident(self):
        joined = " ".join(MD_V1_LIMITATIONS).lower()
        assert "nonresident" in joined or "form 505" in joined

    def test_v1_limitations_mention_capital_gains_surtax(self):
        """2% cap gains surtax for FAGI > $350k is a real TY2025 rule."""
        joined = " ".join(MD_V1_LIMITATIONS).lower()
        assert "capital gains" in joined

    def test_v1_limitations_surfaced_on_state_specific(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
        )
        assert "v1_limitations" in result.state_specific
        assert len(result.state_specific["v1_limitations"]) == len(
            MD_V1_LIMITATIONS
        )


# ---------------------------------------------------------------------------
# Module constants — smoke + coverage
# ---------------------------------------------------------------------------


class TestMarylandConstants:
    def test_standard_deduction_covers_all_filing_statuses(self):
        for fs in FilingStatus:
            assert fs in MD_TY2025_STANDARD_DEDUCTION

    def test_single_std_deduction_3350(self):
        assert MD_TY2025_STANDARD_DEDUCTION[FilingStatus.SINGLE] == Decimal(
            "3350"
        )

    def test_mfj_std_deduction_6700(self):
        assert MD_TY2025_STANDARD_DEDUCTION[FilingStatus.MFJ] == Decimal("6700")

    def test_mfs_std_deduction_3350(self):
        assert MD_TY2025_STANDARD_DEDUCTION[FilingStatus.MFS] == Decimal("3350")

    def test_hoh_std_deduction_6700(self):
        assert MD_TY2025_STANDARD_DEDUCTION[FilingStatus.HOH] == Decimal("6700")

    def test_qss_std_deduction_6700(self):
        assert MD_TY2025_STANDARD_DEDUCTION[FilingStatus.QSS] == Decimal("6700")

    def test_personal_exemption_withholding_default_3200(self):
        assert MD_TY2025_PERSONAL_EXEMPTION_WITHHOLDING_DEFAULT == Decimal(
            "3200"
        )


# ---------------------------------------------------------------------------
# render_pdfs() / form_ids() — stub behavior for wave 4
# ---------------------------------------------------------------------------


class TestMarylandFormIds:
    def test_form_ids_returns_form_502(self):
        assert PLUGIN.form_ids() == ["MD Form 502"]

    def test_render_pdfs_produces_pdf(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """MD Form 502 AcroForm fill produces a non-empty PDF."""
        try:
            from pypdf import PdfReader
        except BaseException:
            pytest.skip("pypdf unavailable")

        state_return = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
        )
        paths = PLUGIN.render_pdfs(state_return, tmp_path)
        assert len(paths) == 1
        assert paths[0].exists()
        assert paths[0].stat().st_size > 0
        assert paths[0].name == "md_502.pdf"

    def test_render_pdfs_field_values(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Verify that rendered MD 502 PDF contains correct field values."""
        try:
            from pypdf import PdfReader
        except BaseException:
            pytest.skip("pypdf unavailable")

        state_return = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            365,
        )
        paths = PLUGIN.render_pdfs(state_return, tmp_path)
        reader = PdfReader(str(paths[0]))
        fields = reader.get_fields()
        assert fields is not None

        # state_total_tax = 4039.01 for $65k Single (with 2.25% default local)
        tax_field = fields.get("Text Box 36")
        assert tax_field is not None
        assert tax_field.get("/V") == "4039.01"
