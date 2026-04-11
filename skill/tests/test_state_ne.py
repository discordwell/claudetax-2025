"""Nebraska state plugin tests.

Mirrors the MN / KS plugin test suites because NE, like MN/KS, is
hand-rolled from DOR primary sources. Nebraska's tenforty default-OTS-
backend support raises ``ValueError: OTS does not support 2025/NE_1040N``.
The graph backend computes the bracket tax correctly but (a) does not
apply the Nebraska personal exemption credit and (b) RAISES
``NotImplementedError`` for any return with ``num_dependents > 0``.

Reference scenario (verified 2026-04-11 against the NE DOR Form 1040N
booklet brackets and against tenforty's graph-backend NE form
definition at .venv/lib/.../tenforty/forms/ne_1040n_2025.json):

    Single / $65,000 W-2 / Standard, no dependents
      Hand calc (Form 1040N line-by-line, this plugin):
        Federal AGI                       = $65,000.00
        NE standard deduction (Single)    =  $8,600.00
        NE income before adjustments      = $56,400.00
        NE taxable income                 = $56,400.00
        NE bracket tax (sum of tiers):
          $0     - $4,030    @ 2.46%      =     $99.138
          $4,030 - $24,120   @ 3.51%      =    $705.159
          $24,120 - $38,870  @ 5.01%      =    $738.975
          $38,870 - $56,400  @ 5.20%      =    $911.560
                                          ------------
        Subtotal                          =  $2,454.832
        Personal exemption credit (v1)    =      $0.00
        TOTAL NE TAX                      =  $2,454.83  ← LOCKED

      tenforty graph backend probe:
        state_total_tax                   =  $2,454.83  (matches)
        state_taxable_income              = $56,400.00

The bracket math is bit-for-bit consistent with tenforty's graph
backend (and tenforty's NE form definition is itself derived from the
NE DOR 2025 1040N booklet, so this is a primary-source check).

The TWO reasons we hand-roll instead of wrap:
  1. Personal exemption credit (Form 1040N line 19, Neb. Rev. Stat.
     §77-2716(7), inflation-indexed) is missing from tenforty's graph
     form definition. v1 documents this as a TODO and does NOT yet
     subtract it (the indexed TY2025 value needs DOR-booklet
     confirmation), but the constant is exposed and the per-exemption
     count works.
  2. Graph backend RAISES NotImplementedError on any
     ``num_dependents > 0`` input. The hand-rolled plugin handles
     dependents (NE doesn't have a per-dependent exemption from
     income, but the dependent count is needed for the per-exemption
     credit count even though v1 zeroes the per-exemption value).

Reciprocity: Nebraska has NO bilateral reciprocity agreements with any
state — verified against skill/reference/state-reciprocity.json (NE
does not appear in any pair) and the Tax Foundation reciprocity survey.
NE residents who commute into IA/KS/MO/SD/WY/CO must file a nonresident
return in the work state and claim the credit for tax paid to another
state on Schedule II of Form 1040N (NOT modeled in v1).
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
from skill.scripts.states.ne import (
    NE_TY2025_BRACKETS,
    NE_TY2025_PEC_PER_EXEMPTION_APPLIED,
    NE_TY2025_STANDARD_DEDUCTION,
    NE_V1_LIMITATIONS,
    NebraskaPlugin,
    PLUGIN,
    ne_bracket_tax,
    ne_personal_exemption_credit,
    ne_standard_deduction,
)


# ---------------------------------------------------------------------------
# Shared fixtures — a Single $65k W-2 return domiciled in Nebraska
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
            street1="1445 K St",
            city="Lincoln",
            state="NE",
            zip="68508",
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
    """Matches the CP4 Single $65k W-2 federal scenario (TY2025 OBBBA)."""
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


class TestNebraskaPluginMeta:
    def test_meta_fields(self):
        assert PLUGIN.meta.code == "NE"
        assert PLUGIN.meta.name == "Nebraska"
        assert PLUGIN.meta.has_income_tax is True
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.FED_STATE_PIGGYBACK
        )
        # Reciprocity: NONE — Nebraska has no bilateral agreements.
        assert PLUGIN.meta.reciprocity_partners == ()
        assert len(PLUGIN.meta.reciprocity_partners) == 0

    def test_plugin_is_state_plugin_protocol(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_nebraska_plugin_instance(self):
        assert isinstance(PLUGIN, NebraskaPlugin)

    def test_meta_urls(self):
        assert "revenue.nebraska.gov" in PLUGIN.meta.dor_url
        assert PLUGIN.meta.free_efile_url is not None
        assert "nebfile" in PLUGIN.meta.free_efile_url.lower()

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_brackets_and_lb754(self):
        notes = PLUGIN.meta.notes
        assert "2.46" in notes
        assert "5.20" in notes
        assert "754" in notes  # LB 754 (2023)

    def test_meta_notes_mentions_no_reciprocity(self):
        """The note must explicitly call out that Nebraska has NO
        reciprocity agreements — this is the load-bearing fact for
        IA/KS/MO/SD/WY/CO commuters."""
        notes = PLUGIN.meta.notes.lower()
        assert "no" in notes and "reciprocity" in notes or "none" in notes

    def test_meta_notes_mentions_pec_omission(self):
        """v1 omits the personal exemption credit — this must be
        clearly documented in notes so future agents pick it up."""
        notes = PLUGIN.meta.notes.lower()
        assert "personal exemption credit" in notes or "pec" in notes

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "NY"  # type: ignore[misc]

    def test_meta_reciprocity_excludes_all_neighbors(self):
        """Nebraska borders Iowa, Missouri, Kansas, Colorado, Wyoming,
        and South Dakota — none of which are reciprocity partners."""
        for neighbor in ("IA", "MO", "KS", "CO", "WY", "SD", "MN"):
            assert neighbor not in PLUGIN.meta.reciprocity_partners


# ---------------------------------------------------------------------------
# Pure-function unit tests — bracket schedule and helpers
# ---------------------------------------------------------------------------


class TestNebraskaConstants:
    def test_standard_deduction_table_keys(self):
        """Verify the TY2025 NE std deds match the published amounts
        and the tenforty form definition literals."""
        assert NE_TY2025_STANDARD_DEDUCTION[
            FilingStatus.SINGLE
        ] == Decimal("8600")
        assert NE_TY2025_STANDARD_DEDUCTION[
            FilingStatus.MFJ
        ] == Decimal("17200")
        assert NE_TY2025_STANDARD_DEDUCTION[
            FilingStatus.QSS
        ] == Decimal("17200")
        assert NE_TY2025_STANDARD_DEDUCTION[
            FilingStatus.MFS
        ] == Decimal("8600")
        assert NE_TY2025_STANDARD_DEDUCTION[
            FilingStatus.HOH
        ] == Decimal("12600")

    def test_pec_per_exemption_is_zero_in_v1(self):
        """v1 does NOT apply the personal exemption credit — the
        constant is exposed at zero so the test number is exactly the
        bracket tax."""
        assert NE_TY2025_PEC_PER_EXEMPTION_APPLIED == Decimal("0")

    def test_brackets_present_for_all_filing_statuses(self):
        for fs in (
            FilingStatus.SINGLE,
            FilingStatus.MFJ,
            FilingStatus.HOH,
            FilingStatus.MFS,
            FilingStatus.QSS,
        ):
            assert fs in NE_TY2025_BRACKETS
            schedule = NE_TY2025_BRACKETS[fs]
            assert len(schedule) == 4  # four-bracket structure
            # Last bracket must have high=None (open-ended).
            assert schedule[-1].high is None
            # Top rate must be 5.20% per LB 754.
            assert schedule[-1].rate == Decimal("0.052")
            # First bracket must start at 0 with 2.46% rate.
            assert schedule[0].low == Decimal("0")
            assert schedule[0].rate == Decimal("0.0246")

    def test_single_bracket_thresholds(self):
        """Single TY2025 thresholds: 4030 / 24120 / 38870 (per the NE
        DOR booklet and tenforty form definition cross-check)."""
        s = NE_TY2025_BRACKETS[FilingStatus.SINGLE]
        assert s[0].high == Decimal("4030")
        assert s[1].low == Decimal("4030")
        assert s[1].high == Decimal("24120")
        assert s[2].low == Decimal("24120")
        assert s[2].high == Decimal("38870")
        assert s[3].low == Decimal("38870")
        # Rates:
        assert s[0].rate == Decimal("0.0246")
        assert s[1].rate == Decimal("0.0351")
        assert s[2].rate == Decimal("0.0501")
        assert s[3].rate == Decimal("0.052")

    def test_mfj_bracket_thresholds(self):
        """MFJ thresholds (approximately doubled but indexed
        separately): 8040 / 48250 / 77730."""
        m = NE_TY2025_BRACKETS[FilingStatus.MFJ]
        assert m[0].high == Decimal("8040")
        assert m[1].high == Decimal("48250")
        assert m[2].high == Decimal("77730")

    def test_hoh_bracket_thresholds(self):
        """HOH thresholds: 7510 / 38590 / 57630."""
        h = NE_TY2025_BRACKETS[FilingStatus.HOH]
        assert h[0].high == Decimal("7510")
        assert h[1].high == Decimal("38590")
        assert h[2].high == Decimal("57630")

    def test_v1_limitations_documented(self):
        joined = " ".join(NE_V1_LIMITATIONS).lower()
        assert "personal exemption" in joined
        assert "reciprocity" in joined or "another state" in joined
        assert "nonresident" in joined or "schedule iii" in joined
        assert len(NE_V1_LIMITATIONS) >= 5


class TestNeStandardDeduction:
    @pytest.mark.parametrize(
        "fs, expected",
        [
            (FilingStatus.SINGLE, Decimal("8600")),
            (FilingStatus.MFJ, Decimal("17200")),
            (FilingStatus.HOH, Decimal("12600")),
            (FilingStatus.MFS, Decimal("8600")),
            (FilingStatus.QSS, Decimal("17200")),
        ],
    )
    def test_std_ded_by_status(self, fs, expected):
        assert ne_standard_deduction(fs) == expected


class TestNeBracketTax:
    def test_zero_taxable_income(self):
        assert ne_bracket_tax(
            Decimal("0"), FilingStatus.SINGLE
        ) == Decimal("0.00")

    def test_negative_taxable_income(self):
        assert ne_bracket_tax(
            Decimal("-500"), FilingStatus.SINGLE
        ) == Decimal("0.00")

    def test_56400_single_locks_to_2454_83(self):
        """LOAD-BEARING: TI=56,400 Single -> $2,454.83 (the $65k Single
        scenario after the $8,600 standard deduction)."""
        assert ne_bracket_tax(
            Decimal("56400"), FilingStatus.SINGLE
        ) == Decimal("2454.83")

    def test_first_bracket_only(self):
        """TI=4,030 Single hits exactly the top of bracket 1.
        Tax = 4,030 * 0.0246 = $99.138 -> $99.14."""
        assert ne_bracket_tax(
            Decimal("4030"), FilingStatus.SINGLE
        ) == Decimal("99.14")

    def test_top_of_second_bracket(self):
        """TI=24,120 Single hits exactly top of bracket 2.
        Tax = 99.138 + (24120-4030)*0.0351 = 99.138 + 705.159 = 804.30."""
        assert ne_bracket_tax(
            Decimal("24120"), FilingStatus.SINGLE
        ) == Decimal("804.30")

    def test_top_of_third_bracket(self):
        """TI=38,870 Single hits exactly top of bracket 3.
        Tax = 804.297 + (38870-24120)*0.0501 = 804.297 + 739.075 = 1543.27."""
        # Actually: 804.297 + 738.975 = 1543.272 → 1543.27
        assert ne_bracket_tax(
            Decimal("38870"), FilingStatus.SINGLE
        ) == Decimal("1543.27")

    def test_high_income_top_bracket(self):
        """TI=200,000 Single — heavy top-bracket weight."""
        # 99.138 + 705.159 + 738.975 + (200000-38870)*0.052
        #  = 1543.272 + 161130*0.052
        #  = 1543.272 + 8378.76 = 9922.032
        result = ne_bracket_tax(
            Decimal("200000"), FilingStatus.SINGLE
        )
        assert result == Decimal("9922.03")

    def test_mfj_at_65k_scenario(self):
        """MFJ TI = 65000 - 17200 (std) = 47800. All of it falls in
        brackets 1 and 2 (bracket 2 ends at 48,250).
        Tax = 8040*0.0246 + (47800-8040)*0.0351
            = 197.784 + 1395.576 = 1593.36."""
        result = ne_bracket_tax(
            Decimal("47800"), FilingStatus.MFJ
        )
        assert result == Decimal("1593.36")


class TestNePersonalExemptionCredit:
    def test_single_no_dependents(self):
        """1 exemption × $0 (v1) = $0."""
        assert ne_personal_exemption_credit(
            FilingStatus.SINGLE, 0
        ) == Decimal("0.00")

    def test_mfj_no_dependents(self):
        """2 exemptions × $0 (v1) = $0."""
        assert ne_personal_exemption_credit(
            FilingStatus.MFJ, 0
        ) == Decimal("0.00")

    def test_single_two_dependents(self):
        """1 + 2 = 3 exemptions × $0 (v1) = $0.

        When v1 is upgraded to actually apply the credit, this test
        will lock the per-exemption multiplication. The exemption
        COUNT logic is correct even at v1 (Single=1, MFJ=2, +deps).
        """
        assert ne_personal_exemption_credit(
            FilingStatus.SINGLE, 2
        ) == Decimal("0.00")

    def test_negative_dependents_clamped(self):
        assert ne_personal_exemption_credit(
            FilingStatus.SINGLE, -3
        ) == Decimal("0.00")


# ---------------------------------------------------------------------------
# compute() — resident case matches the bracket-only hand calc
# ---------------------------------------------------------------------------


class TestNebraskaPluginComputeResident:
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

    def test_state_code_is_ne(self, single_65k_return, federal_single_65k):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state == "NE"

    def test_residency_preserved(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.residency == ResidencyStatus.RESIDENT
        assert result.days_in_state == 365

    def test_resident_65k_single_lock(
        self, single_65k_return, federal_single_65k
    ):
        """HAND-ROLL CORRECTNESS LOCK: Single / $65k W-2 / Standard
        -> NE state_total_tax = $2,454.83.

        Bracket-only calc (v1 omits the personal exemption credit):
            Federal AGI                  = $65,000.00
            NE std ded (Single)          =  $8,600.00
            NE taxable income            = $56,400.00
            Bracket tax (sum of tiers)   =  $2,454.83
            PEC credit (v1: $0)          =      $0.00
            TOTAL NE TAX                 =  $2,454.83
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        assert state_tax == Decimal("2454.83")

    def test_state_taxable_income_correct(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        # 65000 - 8600 = 56400
        assert result.state_specific["state_taxable_income"] == Decimal(
            "56400.00"
        )

    def test_state_standard_deduction_applied(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific[
            "state_standard_deduction"
        ] == Decimal("8600.00")

    def test_state_agi_matches_federal_agi(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific[
            "state_adjusted_gross_income"
        ] == Decimal("65000.00")

    def test_pec_explicitly_zero_in_v1(
        self, single_65k_return, federal_single_65k
    ):
        """The personal exemption credit is exposed in state_specific
        as 0 (v1 omission) — the field exists so output rendering can
        warn the user and so a future v2 enabling the credit only
        needs to flip one constant."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific[
            "state_personal_exemption_credit"
        ] == Decimal("0.00")
        assert result.state_specific[
            "ne_pec_per_exemption_applied"
        ] == Decimal("0")

    def test_state_specific_decimal_fields(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        numeric_keys = [
            "state_federal_agi",
            "state_adjusted_gross_income",
            "state_standard_deduction",
            "state_income_before_adjustments",
            "state_taxable_income",
            "state_bracket_tax",
            "state_credits_total",
            "state_personal_exemption_credit",
            "state_tax_after_credits",
            "state_other_taxes",
            "state_total_tax",
            "state_total_tax_resident_basis",
            "apportionment_fraction",
        ]
        for key in numeric_keys:
            assert key in result.state_specific, f"missing {key}"
            assert isinstance(
                result.state_specific[key], Decimal
            ), f"{key} is not Decimal"

    def test_v1_limitations_in_state_specific(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert "v1_limitations" in result.state_specific
        lim = result.state_specific["v1_limitations"]
        assert isinstance(lim, list)
        assert len(lim) >= 5

    def test_resident_apportionment_is_one(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["apportionment_fraction"] == Decimal(
            "1"
        )

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
        assert rehydrated.state == "NE"


# ---------------------------------------------------------------------------
# compute() — non-Single filing statuses
# ---------------------------------------------------------------------------


class TestNebraskaPluginFilingStatuses:
    def test_mfj_65k(self, single_65k_return):
        """MFJ at $65k AGI:
        TI = 65000 - 17200 (MFJ std) = 47800
        Tax = 8040*0.0246 + (47800-8040)*0.0351
            = 197.784 + 1395.576 = 1593.36."""
        federal_mfj = FederalTotals(
            filing_status=FilingStatus.MFJ,
            num_dependents=0,
            adjusted_gross_income=Decimal("65000"),
            taxable_income=Decimal("33500"),
            total_federal_tax=Decimal("3640"),
            federal_income_tax=Decimal("3640"),
            federal_standard_deduction=Decimal("31500"),
            federal_itemized_deductions_total=Decimal("0"),
            deduction_taken=Decimal("31500"),
        )
        result = PLUGIN.compute(
            single_65k_return,
            federal_mfj,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific[
            "state_standard_deduction"
        ] == Decimal("17200.00")
        assert result.state_specific["state_taxable_income"] == Decimal(
            "47800.00"
        )
        assert result.state_specific["state_total_tax"] == Decimal(
            "1593.36"
        )

    def test_hoh_65k(self, single_65k_return):
        """HOH at $65k AGI:
        TI = 65000 - 12600 (HOH std) = 52400
        Tax = 7510*0.0246 + (38590-7510)*0.0351 + (52400-38590)*0.0501
            = 184.746 + 1090.908 + 691.881 = 1967.535 -> 1967.54.

        (Hand check: bracket 1 cap 7510, bracket 2 cap 38590, into
        bracket 3.)"""
        federal_hoh = FederalTotals(
            filing_status=FilingStatus.HOH,
            num_dependents=0,
            adjusted_gross_income=Decimal("65000"),
            taxable_income=Decimal("41375"),
            total_federal_tax=Decimal("4660"),
            federal_income_tax=Decimal("4660"),
            federal_standard_deduction=Decimal("23625"),
            federal_itemized_deductions_total=Decimal("0"),
            deduction_taken=Decimal("23625"),
        )
        result = PLUGIN.compute(
            single_65k_return,
            federal_hoh,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific[
            "state_standard_deduction"
        ] == Decimal("12600.00")
        assert result.state_specific["state_taxable_income"] == Decimal(
            "52400.00"
        )
        # 184.746 + 1090.908 + 691.881 = 1967.535
        assert result.state_specific["state_total_tax"] == Decimal(
            "1967.54"
        )

    def test_low_income_below_std_ded(self, single_65k_return):
        """Single AGI = $5,000 < $8,600 std ded -> TI = 0 -> tax = 0."""
        federal_low = FederalTotals(
            filing_status=FilingStatus.SINGLE,
            num_dependents=0,
            adjusted_gross_income=Decimal("5000"),
            taxable_income=Decimal("0"),
            total_federal_tax=Decimal("0"),
            federal_income_tax=Decimal("0"),
            federal_standard_deduction=Decimal("15750"),
            federal_itemized_deductions_total=Decimal("0"),
            deduction_taken=Decimal("15750"),
        )
        result = PLUGIN.compute(
            single_65k_return,
            federal_low,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal(
            "0.00"
        )
        assert result.state_specific["state_total_tax"] == Decimal("0.00")


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment (day-based v1)
# ---------------------------------------------------------------------------


class TestNebraskaPluginComputeNonresident:
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
        assert full == Decimal("2454.83")
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
        expected_fraction = Decimal(91) / Decimal("365")
        assert (
            result.state_specific["apportionment_fraction"]
            == expected_fraction
        )


# ---------------------------------------------------------------------------
# Cross-check: graph backend agrees on bracket tax
# ---------------------------------------------------------------------------


class TestNebraskaCrossCheckGraphBackend:
    def test_bracket_tax_matches_graph_backend_at_65k_single(
        self, single_65k_return, federal_single_65k
    ):
        """At Single $65k Standard the hand-rolled NE bracket tax must
        match the tenforty graph backend exactly. The graph backend
        also doesn't apply the personal exemption credit (it has no
        node for it), so this is a fair like-for-like check."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        plugin_tax = result.state_specific["state_total_tax"]

        direct = tenforty.evaluate_return(
            year=2025,
            state="NE",
            filing_status="Single",
            w2_income=65000,
            num_dependents=0,
            standard_or_itemized="Standard",
            itemized_deductions=0,
            backend="graph",
        )
        graph_tax = Decimal(str(direct.state_total_tax)).quantize(
            Decimal("0.01")
        )
        assert plugin_tax == graph_tax == Decimal("2454.83")


# ---------------------------------------------------------------------------
# apportion_income()
# ---------------------------------------------------------------------------


class TestNebraskaPluginApportionIncome:
    def test_apportion_income_resident_full_amounts(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert isinstance(app, IncomeApportionment)
        assert app.state_source_wages == Decimal("65000.00")
        assert app.state_source_total == Decimal("65000.00")

    def test_apportion_income_nonresident_prorates(self, single_65k_return):
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
# render_pdfs() and form_ids()
# ---------------------------------------------------------------------------


class TestNebraskaPluginFormIds:
    def test_form_ids(self):
        form_ids = PLUGIN.form_ids()
        assert "NE Form 1040N" in form_ids
        assert form_ids == ["NE Form 1040N"]

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
# ReciprocityTable consistency
# ---------------------------------------------------------------------------


def test_reciprocity_matches_json():
    """ReciprocityTable.partners_of('NE') must be empty (Nebraska has
    no bilateral agreements). Catches drift between
    skill/reference/state-reciprocity.json and the NE plugin."""
    table = ReciprocityTable.load()
    ne_partners = table.partners_of("NE")
    assert ne_partners == frozenset()
    assert frozenset(PLUGIN.meta.reciprocity_partners) == ne_partners


def test_reciprocity_table_recognizes_ne_has_no_partners():
    """No state should be reciprocal with NE."""
    table = ReciprocityTable.load()
    for state in ("IA", "MO", "KS", "CO", "WY", "SD", "MN", "IL", "OH"):
        assert table.are_reciprocal("NE", state) is False


# ---------------------------------------------------------------------------
# Gatekeeper test — auto-detect tenforty TY2025 NE behavior changes.
# ---------------------------------------------------------------------------


class TestTenfortyDoesNotFullySupportNeTy2025:
    """When this STARTS FAILING, tenforty has either added NE to the
    default OTS backend, fixed the graph backend's missing personal
    exemption credit, or fixed the graph backend's NotImplementedError
    on num_dependents. At that point reconsider this plugin against
    the skill/reference/tenforty-ty2025-gap.md decision rubric.
    """

    def test_default_backend_still_raises(self):
        with pytest.raises(ValueError, match="OTS does not support"):
            tenforty.evaluate_return(
                year=2025,
                state="NE",
                filing_status="Single",
                w2_income=65000,
                num_dependents=0,
                standard_or_itemized="Standard",
                itemized_deductions=0,
            )

    def test_graph_backend_65k_single_locked(self):
        """Graph backend's bracket math is correct at $65k Single
        ($2,454.83). When this changes, the plugin's lock test will
        also fail and we'll know to update."""
        result = tenforty.evaluate_return(
            year=2025,
            state="NE",
            filing_status="Single",
            w2_income=65000,
            num_dependents=0,
            standard_or_itemized="Standard",
            itemized_deductions=0,
            backend="graph",
        )
        graph_tax = Decimal(str(result.state_total_tax)).quantize(
            Decimal("0.01")
        )
        assert graph_tax == Decimal("2454.83")

    def test_graph_backend_still_crashes_on_dependents(self):
        """The graph backend RAISES NotImplementedError for any
        ``num_dependents > 0``. This is one of the load-bearing
        reasons NE is hand-rolled rather than wrapped — a wrap-and-
        pass-through plugin would crash on every filer with kids.
        When this STARTS PASSING (graph backend supports dependents),
        revisit the wrap-vs-hand-roll decision."""
        with pytest.raises(NotImplementedError):
            tenforty.evaluate_return(
                year=2025,
                state="NE",
                filing_status="Single",
                w2_income=65000,
                num_dependents=1,
                standard_or_itemized="Standard",
                itemized_deductions=0,
                backend="graph",
            )
