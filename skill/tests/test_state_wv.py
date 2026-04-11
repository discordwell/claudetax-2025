"""West Virginia state plugin tests.

Mirrors the MN / KS / ME / RI hand-rolled plugin test suites. tenforty's
default OTS backend does NOT support 2025/WV_IT140 (raises ValueError);
the graph backend returns a number but omits the WV personal exemption,
producing a +$96.40 over-statement on a $65k Single return. The WV
plugin therefore hand-rolls Form IT-140 from the WV State Tax Department
TY2025 rate schedule.

Reference scenario:

    Single / $65,000 W-2 / Standard
      Line 1   Federal AGI                $65,000.00
      Line 4   WV AGI                     $65,000.00
      Line 5   Low Income Exclusion            $0.00
      Line 6   Personal Exemption          $2,000.00
      Line 7   WV Taxable Income          $63,000.00
      Line 8   WV Income Tax:
               $0-10k  @ 2.22%             $222.00
               $10k-25k @ 2.96%             $444.00
               $25k-40k @ 3.33%             $499.50
               $40k-60k @ 4.44%             $888.00
               $60k-63k @ 4.82%             $144.60
               Total                       $2,198.10

Sources:
    - WV Tax Division, Individuals page:
      https://tax.wv.gov/Individuals/Pages/default.aspx
    - WV HB 2526 (2023) + HB 4007 (2024) — trigger-driven rate cuts
    - WV Code §11-21-16 — $2,000 statutory personal exemption
    - tenforty graph file wv_it140_2025.json (bracket constants)

Reciprocity (CRITICAL): WV has FIVE bilateral reciprocity partners —
KY, MD, OH, PA, VA. This is the most of any state in the wave-5 batch
and one of the largest reciprocity networks of any state nationally.
Verified against skill/reference/state-reciprocity.json.
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
from skill.scripts.states.wv import (
    PLUGIN,
    WV_RECIPROCITY_PARTNERS,
    WV_TY2025_BRACKETS,
    WV_TY2025_BRACKETS_MFS,
    WV_TY2025_BRACKETS_NON_MFS,
    WV_TY2025_GRAPH_BACKEND_65K_SINGLE,
    WV_TY2025_PERSONAL_EXEMPTION_PER_PERSON,
    WV_V1_LIMITATIONS,
    WestVirginiaPlugin,
    wv_bracket_tax,
    wv_personal_exemption,
    wv_taxable_income,
)


# ---------------------------------------------------------------------------
# Fixtures
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
            street1="1124 Smith St", city="Charleston", state="WV", zip="25301"
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


class TestWestVirginiaPluginMeta:
    def test_meta_code(self):
        assert PLUGIN.meta.code == "WV"

    def test_meta_name(self):
        assert PLUGIN.meta.name == "West Virginia"

    def test_meta_has_income_tax(self):
        assert PLUGIN.meta.has_income_tax is True

    def test_meta_starting_point(self):
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI

    def test_meta_submission_channel(self):
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )

    def test_plugin_is_state_plugin_protocol(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_west_virginia_plugin_instance(self):
        assert isinstance(PLUGIN, WestVirginiaPlugin)

    def test_meta_dor_url_is_tax_wv_gov(self):
        assert "tax.wv.gov" in PLUGIN.meta.dor_url

    def test_meta_free_efile_url_is_mytaxes(self):
        assert PLUGIN.meta.free_efile_url is not None
        assert "mytaxes" in PLUGIN.meta.free_efile_url.lower()

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_bracket_rates(self):
        notes = PLUGIN.meta.notes
        assert "2.22" in notes
        assert "2.96" in notes
        assert "3.33" in notes
        assert "4.44" in notes
        assert "4.82" in notes

    def test_meta_notes_mention_tenforty_gap(self):
        notes = PLUGIN.meta.notes.lower()
        assert "tenforty" in notes
        assert "personal exemption" in notes
        assert "graph" in notes

    def test_meta_notes_mention_reciprocity_partners(self):
        notes = PLUGIN.meta.notes
        # All five reciprocity partners must be cited
        for partner in ("KY", "MD", "OH", "PA", "VA"):
            assert partner in notes

    def test_meta_notes_cite_statute(self):
        notes = PLUGIN.meta.notes
        assert "§11-21-16" in notes or "WV Code" in notes

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "VA"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# RECIPROCITY — WV has FIVE bilateral partners (most in wave 5)
# ---------------------------------------------------------------------------


class TestWestVirginiaReciprocityNetwork:
    """West Virginia has FIVE bilateral reciprocity agreements:
    KY, MD, OH, PA, VA. This class is the load-bearing test for the
    reciprocity network — drift here would silently mis-handle multi-
    state filers in the entire WV-bordering region."""

    def test_meta_reciprocity_partners_exact_set(self):
        """The exact five-partner set, not a subset."""
        assert set(PLUGIN.meta.reciprocity_partners) == {
            "KY",
            "MD",
            "OH",
            "PA",
            "VA",
        }
        assert len(PLUGIN.meta.reciprocity_partners) == 5

    @pytest.mark.parametrize("partner", ["KY", "MD", "OH", "PA", "VA"])
    def test_each_partner_present(self, partner):
        assert partner in PLUGIN.meta.reciprocity_partners

    def test_reciprocity_matches_json(self):
        """ReciprocityTable.partners_of('WV') must equal the plugin's
        meta.reciprocity_partners as a frozenset. Catches drift between
        skill/reference/state-reciprocity.json and the WV plugin."""
        table = ReciprocityTable.load()
        wv_partners_from_table = table.partners_of("WV")
        assert wv_partners_from_table == frozenset(
            {"KY", "MD", "OH", "PA", "VA"}
        )
        assert (
            frozenset(PLUGIN.meta.reciprocity_partners)
            == wv_partners_from_table
        )

    @pytest.mark.parametrize("partner", ["KY", "MD", "OH", "PA", "VA"])
    def test_each_partner_bilateral_via_table(self, partner):
        """ReciprocityTable must agree both directions."""
        table = ReciprocityTable.load()
        assert table.are_reciprocal("WV", partner) is True
        assert table.are_reciprocal(partner, "WV") is True

    def test_non_partners_not_reciprocal(self):
        """Other WV-adjacent states (TN borders WV — close enough) and
        notable non-partners must NOT show as reciprocal."""
        table = ReciprocityTable.load()
        for not_partner in ("TN", "NC", "NY", "NJ", "DE", "WV"):
            assert table.are_reciprocal("WV", not_partner) is False

    def test_module_constant_matches_meta(self):
        assert tuple(PLUGIN.meta.reciprocity_partners) == WV_RECIPROCITY_PARTNERS


# ---------------------------------------------------------------------------
# TY2025 constants sanity
# ---------------------------------------------------------------------------


class TestWestVirginiaTY2025Constants:
    def test_personal_exemption_per_person(self):
        """WV Code §11-21-16: $2,000 statutory, NOT inflation-indexed."""
        assert WV_TY2025_PERSONAL_EXEMPTION_PER_PERSON == Decimal("2000")

    def test_brackets_have_five_rows(self):
        for fs in (
            FilingStatus.SINGLE,
            FilingStatus.MFJ,
            FilingStatus.HOH,
            FilingStatus.QSS,
            FilingStatus.MFS,
        ):
            assert len(WV_TY2025_BRACKETS[fs]) == 5

    def test_non_mfs_first_row(self):
        b = WV_TY2025_BRACKETS_NON_MFS[0]
        assert b.low == Decimal("0")
        assert b.high == Decimal("10000")
        assert b.rate == Decimal("0.0222")

    def test_non_mfs_top_row(self):
        b = WV_TY2025_BRACKETS_NON_MFS[-1]
        assert b.low == Decimal("60000")
        assert b.high is None
        assert b.rate == Decimal("0.0482")

    def test_mfs_first_row(self):
        b = WV_TY2025_BRACKETS_MFS[0]
        assert b.high == Decimal("5000")  # half of $10,000
        assert b.rate == Decimal("0.0222")

    def test_mfs_breakpoints_are_half_of_non_mfs(self):
        """MFS uses HALVED breakpoints to preserve MFJ tax burden symmetry.
        Each non-top MFS bracket boundary should be exactly half the
        corresponding non-MFS boundary."""
        for non_mfs, mfs in zip(
            WV_TY2025_BRACKETS_NON_MFS[:-1], WV_TY2025_BRACKETS_MFS[:-1]
        ):
            assert mfs.high == non_mfs.high / 2
            assert mfs.rate == non_mfs.rate

    def test_single_hoh_mfj_qss_share_brackets(self):
        """WV is unusual: NO joint widening of brackets."""
        for fs in (FilingStatus.SINGLE, FilingStatus.HOH, FilingStatus.MFJ, FilingStatus.QSS):
            assert WV_TY2025_BRACKETS[fs] is WV_TY2025_BRACKETS_NON_MFS

    def test_all_brackets_use_post_cut_rates(self):
        """Sanity: rates must match the post-HB 2526/HB 4007 schedule.
        If WV passes another rate cut, this test will fail and force
        deliberate update."""
        expected = (
            Decimal("0.0222"),
            Decimal("0.0296"),
            Decimal("0.0333"),
            Decimal("0.0444"),
            Decimal("0.0482"),
        )
        for b, r in zip(WV_TY2025_BRACKETS_NON_MFS, expected):
            assert b.rate == r


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestWestVirginiaPersonalExemption:
    def test_single_no_dependents(self):
        assert wv_personal_exemption(FilingStatus.SINGLE, 0) == Decimal("2000")

    def test_mfj_no_dependents(self):
        assert wv_personal_exemption(FilingStatus.MFJ, 0) == Decimal("4000")

    def test_hoh_one_dependent(self):
        assert wv_personal_exemption(FilingStatus.HOH, 1) == Decimal("4000")

    def test_mfj_two_dependents(self):
        assert wv_personal_exemption(FilingStatus.MFJ, 2) == Decimal("8000")

    def test_negative_dependents_clamped_to_zero(self):
        assert wv_personal_exemption(FilingStatus.SINGLE, -3) == Decimal(
            "2000"
        )

    def test_qss_two_filer_exemptions(self):
        assert wv_personal_exemption(FilingStatus.QSS, 0) == Decimal("4000")


class TestWestVirginiaBracketMath:
    def test_zero_taxable_income_zero_tax(self):
        assert wv_bracket_tax(Decimal("0"), FilingStatus.SINGLE) == Decimal(
            "0.00"
        )

    def test_negative_taxable_income_zero_tax(self):
        assert wv_bracket_tax(
            Decimal("-1000"), FilingStatus.SINGLE
        ) == Decimal("0.00")

    def test_within_first_bracket(self):
        """$5,000 @ 2.22% = $111.00."""
        assert wv_bracket_tax(
            Decimal("5000"), FilingStatus.SINGLE
        ) == Decimal("111.00")

    def test_at_first_bracket_ceiling(self):
        """$10,000 @ 2.22% = $222.00."""
        assert wv_bracket_tax(
            Decimal("10000"), FilingStatus.SINGLE
        ) == Decimal("222.00")

    def test_63000_locked(self):
        """$63,000 (the $65k Single ti after $2,000 exemption):
            2.22% * 10,000 = 222.00
            2.96% * 15,000 = 444.00
            3.33% * 15,000 = 499.50
            4.44% * 20,000 = 888.00
            4.82% * 3,000 = 144.60
            Total = 2,198.10
        """
        assert wv_bracket_tax(
            Decimal("63000"), FilingStatus.SINGLE
        ) == Decimal("2198.10")

    def test_at_top_bracket_floor(self):
        """$60,000 — sum of all four lower brackets:
            2.22%*10k + 2.96%*15k + 3.33%*15k + 4.44%*20k =
            222 + 444 + 499.50 + 888 = 2053.50
        """
        assert wv_bracket_tax(
            Decimal("60000"), FilingStatus.SINGLE
        ) == Decimal("2053.50")

    def test_in_top_bracket(self):
        """$100,000 = 2053.50 + 4.82% * 40,000 = 2053.50 + 1928 = 3981.50."""
        assert wv_bracket_tax(
            Decimal("100000"), FilingStatus.SINGLE
        ) == Decimal("3981.50")

    def test_mfj_uses_same_brackets_as_single(self):
        """WV is unusual: MFJ uses the SAME brackets as Single."""
        for ti in (Decimal("10000"), Decimal("63000"), Decimal("100000")):
            assert wv_bracket_tax(ti, FilingStatus.MFJ) == wv_bracket_tax(
                ti, FilingStatus.SINGLE
            )

    def test_hoh_uses_same_brackets_as_single(self):
        for ti in (Decimal("10000"), Decimal("63000"), Decimal("100000")):
            assert wv_bracket_tax(ti, FilingStatus.HOH) == wv_bracket_tax(
                ti, FilingStatus.SINGLE
            )

    def test_mfs_uses_half_brackets(self):
        """MFS at $30,000 should equal Single at $60,000 (since MFS half-
        brackets are exactly the Single bracket boundaries divided by 2):
        actually no — equal MFS taxable income at each MFS row boundary
        equals one HALF of the Single tax at the corresponding doubled
        income. Verify MFS at $5,000 hits the same rate boundary as
        Single at $10,000."""
        # MFS at 5000 is at the end of the first MFS bracket (2.22%)
        # = 5000 * 0.0222 = 111.00
        assert wv_bracket_tax(Decimal("5000"), FilingStatus.MFS) == Decimal(
            "111.00"
        )
        # MFS at 30000 is at the end of the fourth bracket
        # = 0.0222*5000 + 0.0296*7500 + 0.0333*7500 + 0.0444*10000
        # = 111 + 222 + 249.75 + 444 = 1026.75
        assert wv_bracket_tax(
            Decimal("30000"), FilingStatus.MFS
        ) == Decimal("1026.75")

    def test_rate_monotonic(self):
        amounts = [
            Decimal("5000"),
            Decimal("25000"),
            Decimal("63000"),
            Decimal("100000"),
            Decimal("250000"),
        ]
        taxes = [wv_bracket_tax(a, FilingStatus.SINGLE) for a in amounts]
        for prev, curr in zip(taxes, taxes[1:]):
            assert curr > prev


class TestWestVirginiaTaxableIncomeFlow:
    def test_single_65k_no_dependents(self, federal_single_65k):
        l4, l5, l6, l7 = wv_taxable_income(federal_single_65k)
        assert l4 == Decimal("65000")
        assert l5 == Decimal("0")
        assert l6 == Decimal("2000")
        assert l7 == Decimal("63000")

    def test_low_income_floors_at_zero(self):
        ft = FederalTotals(
            filing_status=FilingStatus.SINGLE,
            num_dependents=2,
            adjusted_gross_income=Decimal("4000"),
            taxable_income=Decimal("0"),
            total_federal_tax=Decimal("0"),
            federal_income_tax=Decimal("0"),
            federal_standard_deduction=Decimal("15750"),
            federal_itemized_deductions_total=Decimal("0"),
            deduction_taken=Decimal("15750"),
        )
        _, _, l6, l7 = wv_taxable_income(ft)
        assert l6 == Decimal("6000")  # 3 * 2000
        assert l7 == Decimal("0")  # 4000 - 6000 floored


# ---------------------------------------------------------------------------
# compute() — resident
# ---------------------------------------------------------------------------


class TestWestVirginiaPluginComputeResident:
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

    def test_state_code_is_wv(self, single_65k_return, federal_single_65k):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state == "WV"

    def test_residency_preserved(self, single_65k_return, federal_single_65k):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.residency == ResidencyStatus.RESIDENT

    def test_resident_65k_single_lock(
        self, single_65k_return, federal_single_65k
    ):
        """**$65k Single WV resident WRAP-CORRECTNESS LOCK.**

        Hand-rolled per WV State Tax Department TY2025:
            Federal AGI         $65,000.00
            Personal exempt    - $2,000.00
            WV taxable income   $63,000.00
            Tax (5 brackets)    $2,198.10

        This is the canonical locked number — NOT the tenforty graph
        backend value ($2,294.50), which omits the personal exemption.
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        assert state_tax == Decimal("2198.10")

    def test_state_taxable_income_63k(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal(
            "63000.00"
        )

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

    def test_wv_line_numbers_match_manual_flow(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = result.state_specific
        assert ss["wv_line_1_federal_agi"] == Decimal("65000.00")
        assert ss["wv_line_2_increasing_modifications"] == Decimal("0.00")
        assert ss["wv_line_3_decreasing_modifications"] == Decimal("0.00")
        assert ss["wv_line_4_wv_agi"] == Decimal("65000.00")
        assert ss["wv_line_5_low_income_exclusion"] == Decimal("0.00")
        assert ss["wv_line_6_personal_exemption"] == Decimal("2000.00")
        assert ss["wv_line_7_taxable_income"] == Decimal("63000.00")
        assert ss["wv_line_8_tax"] == Decimal("2198.10")
        assert ss["wv_line_9_family_tax_credit"] == Decimal("0.00")

    def test_state_specific_all_decimal_fields(
        self, single_65k_return, federal_single_65k
    ):
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
            "state_total_tax_graph_backend_65k_single_reference",
            "apportionment_fraction",
            "wv_line_1_federal_agi",
            "wv_line_4_wv_agi",
            "wv_line_5_low_income_exclusion",
            "wv_line_6_personal_exemption",
            "wv_line_7_taxable_income",
            "wv_line_8_tax",
            "wv_line_9_family_tax_credit",
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
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        dumped = result.model_dump(mode="json")
        rehydrated = StateReturn.model_validate(dumped)
        assert rehydrated.state == "WV"
        assert rehydrated.residency == ResidencyStatus.RESIDENT

    def test_tenforty_status_flags(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific[
            "tenforty_supports_wv_default_backend"
        ] is False
        assert result.state_specific["tenforty_supports_wv_graph_backend"] is True
        note = result.state_specific["tenforty_status_note"]
        assert "personal exemption" in note.lower()

    def test_reciprocity_partners_in_state_specific(
        self, single_65k_return, federal_single_65k
    ):
        """state_specific must expose the WV reciprocity network for
        downstream multi-state planners."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        partners = result.state_specific["wv_reciprocity_partners"]
        assert set(partners) == {"KY", "MD", "OH", "PA", "VA"}
        assert len(partners) == 5
        note = result.state_specific["wv_reciprocity_note"]
        assert "IT-104R" in note


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment
# ---------------------------------------------------------------------------


class TestWestVirginiaPluginComputeNonresident:
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
        assert full == Decimal("2198.10")
        assert apportioned < full
        expected = (full * Decimal(182) / Decimal(365)).quantize(
            Decimal("0.01")
        )
        assert apportioned == expected

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


class TestWestVirginiaPluginApportionIncome:
    def test_apportion_income_resident_full_amounts(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert isinstance(app, IncomeApportionment)
        assert app.state_source_wages == Decimal("65000.00")
        assert app.state_source_total == Decimal("65000.00")

    def test_apportion_income_nonresident_prorates(self, single_65k_return):
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


class TestWestVirginiaPluginFormIds:
    def test_form_ids(self):
        form_ids = PLUGIN.form_ids()
        assert "WV Form IT-140" in form_ids
        assert form_ids == ["WV Form IT-140"]

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
# Gatekeeper tests — tenforty default-backend gap and graph divergence
# ---------------------------------------------------------------------------


class TestWestVirginiaTenfortyGap:
    def test_default_backend_still_raises(self):
        import tenforty
        with pytest.raises(ValueError, match="OTS does not support"):
            tenforty.evaluate_return(
                year=2025,
                state="WV",
                filing_status="Single",
                w2_income=65_000,
                num_dependents=0,
                standard_or_itemized="Standard",
                itemized_deductions=0,
            )

    def test_graph_backend_pinned_at_known_divergent_value(self):
        import tenforty
        result = tenforty.evaluate_return(
            year=2025,
            state="WV",
            filing_status="Single",
            w2_income=65_000,
            num_dependents=0,
            standard_or_itemized="Standard",
            itemized_deductions=0,
            backend="graph",
        )
        graph_total = Decimal(str(result.state_total_tax)).quantize(
            Decimal("0.01")
        )
        assert graph_total == WV_TY2025_GRAPH_BACKEND_65K_SINGLE

    def test_plugin_diverges_from_graph_by_personal_exemption_amount(
        self, single_65k_return, federal_single_65k
    ):
        """Hand-rolled $2,198.10 vs graph $2,294.50. Delta = $96.40 =
        $2,000 personal exemption * 4.82% top-bracket rate."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        plugin_tax = result.state_specific["state_total_tax"]
        graph_tax = WV_TY2025_GRAPH_BACKEND_65K_SINGLE
        delta = (graph_tax - plugin_tax).quantize(Decimal("0.01"))
        assert delta == Decimal("96.40")


# ---------------------------------------------------------------------------
# V1 limitations list sanity
# ---------------------------------------------------------------------------


def test_v1_limitations_module_constant_non_empty():
    assert len(WV_V1_LIMITATIONS) >= 5


def test_v1_limitations_mentions_nonresident_form():
    joined = " ".join(WV_V1_LIMITATIONS).lower()
    assert "it-140nrs" in joined or "nonresident" in joined


def test_v1_limitations_mentions_reciprocity_mechanics():
    joined = " ".join(WV_V1_LIMITATIONS).lower()
    assert "reciprocity" in joined and "it-104r" in joined.lower()


def test_v1_limitations_mentions_family_tax_credit():
    joined = " ".join(WV_V1_LIMITATIONS).lower()
    assert "family tax credit" in joined
