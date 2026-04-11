"""Minnesota state plugin tests.

Mirrors the OH / NJ plugin test suites but with one important divergence:
tenforty / OpenTaxSolver does NOT ship support for MN_M1 in its 2025 form
dispatch table, so the MN plugin hand-rolls the bracket math rather than
wrapping a tenforty call. The tests below therefore pin the plugin's
computed numbers against bracket constants it owns itself, not against an
independent tenforty reference.

Reference scenario (hand-computed from MN DOR 2025 Tax Professional Desk
Reference Chart):

    Single / $65,000 W-2 / Standard
      Line 1  Federal AGI                 $65,000.00
      Line 4  MN Standard Deduction       $14,950.00
      Line 9  MN Taxable Income           $50,050.00
      Line 10 Tax:
              0-32,570    @ 5.35%         $1,742.4950
              32,570-50,050 @ 6.80%       $1,188.6400
              Total                       $2,931.1350
              Rounded                     $2,931.14

Sources (verified 2026-04-11):
    - MN DOR 2025 Tax Professional Desk Reference Chart
      https://www.revenue.state.mn.us/sites/default/files/2026-01/tax-year-2025-tax-professional-desk-reference-chart-final.pdf
    - MN DOR Individual Income Tax Rates and Brackets
      https://www.revenue.state.mn.us/minnesota-income-tax-rates-and-brackets
    - MN DOR 2024-12-16 press release (standard deduction / exemption)
      https://www.revenue.state.mn.us/press-release/2024-12-16/minnesota-income-tax-brackets-standard-deduction-and-dependent-exemption

Reciprocity: MN has exactly two reciprocity partners — MI and ND — verified
against skill/reference/state-reciprocity.json. MN-WI reciprocity was
terminated in 2010 and has not been reinstated as of TY2025.
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
from skill.scripts.states.mn import (
    MN_TY2025_BRACKETS,
    MN_TY2025_DEPENDENT_EXEMPTION,
    MN_TY2025_STANDARD_DEDUCTION,
    MN_TY2025_STANDARD_DEDUCTION_PHASEOUT_AGI,
    MN_V1_LIMITATIONS,
    PLUGIN,
    MinnesotaPlugin,
    _mn_bracket_tax,
    _mn_taxable_income,
)


# ---------------------------------------------------------------------------
# Shared fixtures — a Single $65k W-2 return domiciled in Minnesota
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
            street1="600 N Robert St", city="St. Paul", state="MN", zip="55146"
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
    # Matches the shared Single $65k W-2 federal scenario used across OH/MI/AZ.
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


class TestMinnesotaPluginMeta:
    def test_meta_code(self):
        assert PLUGIN.meta.code == "MN"

    def test_meta_name(self):
        assert PLUGIN.meta.name == "Minnesota"

    def test_meta_has_income_tax(self):
        assert PLUGIN.meta.has_income_tax is True

    def test_meta_starting_point(self):
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI

    def test_meta_submission_channel(self):
        """MN uses its own e-Services free portal, not IRS Fed/State MeF."""
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )

    def test_meta_reciprocity_partners_exact(self):
        """Reciprocity: exactly MI and ND — verified against
        skill/reference/state-reciprocity.json. MN-WI reciprocity was
        terminated in 2010 and is NOT active for TY2025."""
        assert set(PLUGIN.meta.reciprocity_partners) == {"MI", "ND"}
        assert len(PLUGIN.meta.reciprocity_partners) == 2

    def test_meta_reciprocity_excludes_wisconsin(self):
        """Regression: MN-WI reciprocity was terminated in 2010."""
        assert "WI" not in PLUGIN.meta.reciprocity_partners

    def test_meta_reciprocity_excludes_other_neighbors(self):
        """Iowa and South Dakota are MN neighbors but NOT reciprocity
        partners."""
        for not_partner in ("IA", "SD", "MN", "IL", "CA", "NY"):
            assert not_partner not in PLUGIN.meta.reciprocity_partners

    def test_plugin_is_state_plugin_protocol(self):
        """runtime_checkable Protocol must recognize the concrete plugin."""
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_minnesota_plugin_instance(self):
        assert isinstance(PLUGIN, MinnesotaPlugin)

    def test_meta_urls(self):
        assert "revenue.state.mn.us" in PLUGIN.meta.dor_url
        assert PLUGIN.meta.free_efile_url is not None
        assert "mndor.state.mn.us" in PLUGIN.meta.free_efile_url

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_bracket_rates(self):
        """Notes should document the four TY2025 rates."""
        notes = PLUGIN.meta.notes
        assert "5.35" in notes
        assert "6.80" in notes
        assert "7.85" in notes
        assert "9.85" in notes

    def test_meta_notes_mention_tenforty_gap(self):
        """Notes MUST loudly flag that tenforty does not support MN."""
        notes = PLUGIN.meta.notes.lower()
        assert "tenforty" in notes
        assert "not support" in notes or "does not support" in notes

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "IA"  # type: ignore[misc]

    @pytest.mark.parametrize("partner", ["MI", "ND"])
    def test_meta_reciprocity_contains_each_partner(self, partner):
        assert partner in PLUGIN.meta.reciprocity_partners


# ---------------------------------------------------------------------------
# Constants sanity — TY2025 bracket, standard deduction, exemption values
# ---------------------------------------------------------------------------


class TestMinnesotaTY2025Constants:
    def test_standard_deduction_single(self):
        """Source: MN DOR 2025 Desk Reference Chart — $14,950 for Single."""
        assert MN_TY2025_STANDARD_DEDUCTION[FilingStatus.SINGLE] == Decimal(
            "14950"
        )

    def test_standard_deduction_mfs_matches_single(self):
        """MN standard deduction is identical for Single and MFS in TY2025."""
        assert (
            MN_TY2025_STANDARD_DEDUCTION[FilingStatus.MFS]
            == MN_TY2025_STANDARD_DEDUCTION[FilingStatus.SINGLE]
        )

    def test_standard_deduction_mfj(self):
        """Source: MN DOR press release 2024-12-16 — $29,900 for MFJ."""
        assert MN_TY2025_STANDARD_DEDUCTION[FilingStatus.MFJ] == Decimal(
            "29900"
        )

    def test_standard_deduction_qss_matches_mfj(self):
        assert (
            MN_TY2025_STANDARD_DEDUCTION[FilingStatus.QSS]
            == MN_TY2025_STANDARD_DEDUCTION[FilingStatus.MFJ]
        )

    def test_standard_deduction_hoh(self):
        """Source: MN DOR 2025 Desk Reference Chart — $22,500 for HOH."""
        assert MN_TY2025_STANDARD_DEDUCTION[FilingStatus.HOH] == Decimal(
            "22500"
        )

    def test_dependent_exemption(self):
        """Source: MN DOR press release 2024-12-16 — $5,200 per dependent."""
        assert MN_TY2025_DEPENDENT_EXEMPTION == Decimal("5200")

    def test_bracket_single_first_row_rate(self):
        """Single row 1: floor 0, rate 5.35%."""
        rows = MN_TY2025_BRACKETS[FilingStatus.SINGLE]
        upper, base, rate, floor = rows[0]
        assert floor == Decimal("0")
        assert base == Decimal("0")
        assert rate == Decimal("0.0535")
        assert upper == Decimal("32570")

    def test_bracket_single_top_row_rate(self):
        """Single top bracket: rate 9.85%, upper = None."""
        rows = MN_TY2025_BRACKETS[FilingStatus.SINGLE]
        upper, base, rate, floor = rows[-1]
        assert upper is None
        assert rate == Decimal("0.0985")
        assert floor == Decimal("198630")

    def test_bracket_mfj_row1_upper(self):
        """MFJ 5.35% bracket tops at $47,620."""
        rows = MN_TY2025_BRACKETS[FilingStatus.MFJ]
        assert rows[0][0] == Decimal("47620")

    def test_bracket_mfj_matches_qss(self):
        """MFJ and QSS share the same bracket schedule."""
        assert MN_TY2025_BRACKETS[FilingStatus.MFJ] == MN_TY2025_BRACKETS[
            FilingStatus.QSS
        ]

    def test_bracket_hoh_row1_upper(self):
        """HOH 5.35% bracket tops at $40,100."""
        rows = MN_TY2025_BRACKETS[FilingStatus.HOH]
        assert rows[0][0] == Decimal("40100")

    def test_bracket_mfs_row1_upper(self):
        """MFS 5.35% bracket tops at $23,810 (half of MFJ's $47,620)."""
        rows = MN_TY2025_BRACKETS[FilingStatus.MFS]
        assert rows[0][0] == Decimal("23810")
        assert (
            rows[0][0] * 2 == MN_TY2025_BRACKETS[FilingStatus.MFJ][0][0]
        )

    def test_all_statuses_have_four_brackets(self):
        """Every filing status has exactly four graduated brackets."""
        for fs in (
            FilingStatus.SINGLE,
            FilingStatus.MFJ,
            FilingStatus.MFS,
            FilingStatus.HOH,
            FilingStatus.QSS,
        ):
            assert len(MN_TY2025_BRACKETS[fs]) == 4

    def test_all_statuses_use_same_rates(self):
        """Every filing status uses the same rate sequence — only breakpoints
        differ."""
        expected_rates = (
            Decimal("0.0535"),
            Decimal("0.068"),
            Decimal("0.0785"),
            Decimal("0.0985"),
        )
        for fs in (
            FilingStatus.SINGLE,
            FilingStatus.MFJ,
            FilingStatus.MFS,
            FilingStatus.HOH,
            FilingStatus.QSS,
        ):
            rows = MN_TY2025_BRACKETS[fs]
            actual_rates = tuple(row[2] for row in rows)
            assert actual_rates == expected_rates, f"{fs} rates drifted"

    def test_standard_deduction_phaseout_threshold_single(self):
        """AGI > $238,950 triggers MN std-ded phaseout for non-MFS filers."""
        assert MN_TY2025_STANDARD_DEDUCTION_PHASEOUT_AGI[
            FilingStatus.SINGLE
        ] == Decimal("238950")

    def test_standard_deduction_phaseout_threshold_mfs_is_half(self):
        """MFS threshold ($119,475) is half of the All-Others threshold."""
        all_others = MN_TY2025_STANDARD_DEDUCTION_PHASEOUT_AGI[
            FilingStatus.SINGLE
        ]
        mfs = MN_TY2025_STANDARD_DEDUCTION_PHASEOUT_AGI[FilingStatus.MFS]
        assert mfs * 2 == all_others
        assert mfs == Decimal("119475")


# ---------------------------------------------------------------------------
# Bracket math unit tests
# ---------------------------------------------------------------------------


class TestMinnesotaBracketMath:
    def test_zero_taxable_income_zero_tax(self):
        assert _mn_bracket_tax(Decimal("0"), FilingStatus.SINGLE) == Decimal(
            "0"
        )

    def test_negative_taxable_income_zero_tax(self):
        assert _mn_bracket_tax(
            Decimal("-1000"), FilingStatus.SINGLE
        ) == Decimal("0")

    def test_single_within_first_bracket(self):
        """$10,000 taxable @ 5.35% = $535.00 flat."""
        assert _mn_bracket_tax(
            Decimal("10000"), FilingStatus.SINGLE
        ) == Decimal("535.00")

    def test_single_at_first_bracket_ceiling(self):
        """$32,570 * 5.35% = $1,742.495 -> $1,742.50 rounded."""
        assert _mn_bracket_tax(
            Decimal("32570"), FilingStatus.SINGLE
        ) == Decimal("1742.50")

    def test_single_65k_wrap_lock(self):
        """$65k Single wrap-correctness lock.

        MN Taxable Income (with $14,950 std ded) = $50,050.
            Base     $1,742.4950  (at $32,570)
            Plus     ($50,050 - $32,570) * 0.068 = $1,188.64
            Total    $2,931.1350
            Rounded  $2,931.14

        This is the LOCKED $65k-single reference the fan-out spec called
        out. Because tenforty does not support MN, this is a lock against
        the plugin's OWN bracket math, not against an independent external
        reference.
        """
        assert _mn_bracket_tax(
            Decimal("50050"), FilingStatus.SINGLE
        ) == Decimal("2931.14")

    def test_single_at_second_bracket_ceiling(self):
        """$106,990 taxable — top of the 6.80% bracket.

            Base at $32,570     $1,742.4950
            + (106,990-32,570) * 0.068  = $5,060.56
            = $6,803.0550 -> $6,803.06
        """
        assert _mn_bracket_tax(
            Decimal("106990"), FilingStatus.SINGLE
        ) == Decimal("6803.06")

    def test_single_at_third_bracket_ceiling(self):
        """$198,630 taxable — top of the 7.85% bracket.

            Base at $106,990    $6,803.0550
            + (198,630-106,990) * 0.0785 = $7,193.74
            = $13,996.7950 -> $13,996.80
        """
        assert _mn_bracket_tax(
            Decimal("198630"), FilingStatus.SINGLE
        ) == Decimal("13996.80")

    def test_single_in_top_bracket(self):
        """$300,000 taxable — in the 9.85% top bracket.

            Base at $198,630    $13,996.7950
            + (300,000-198,630) * 0.0985 = $9,984.9450
            = $23,981.7400 -> $23,981.74
        """
        assert _mn_bracket_tax(
            Decimal("300000"), FilingStatus.SINGLE
        ) == Decimal("23981.74")

    def test_mfj_100k(self):
        """MFJ $100,000 taxable:
            5.35% on first $47,620 = $2,547.67
            + (100,000-47,620) * 0.068 = $3,561.84
            = $6,109.51
        """
        assert _mn_bracket_tax(
            Decimal("100000"), FilingStatus.MFJ
        ) == Decimal("6109.51")

    def test_qss_matches_mfj(self):
        """QSS uses the MFJ bracket schedule."""
        for ti in (Decimal("30000"), Decimal("100000"), Decimal("400000")):
            assert _mn_bracket_tax(ti, FilingStatus.QSS) == _mn_bracket_tax(
                ti, FilingStatus.MFJ
            )

    def test_hoh_80k(self):
        """HOH $80,000 taxable:
            5.35% on first $40,100 = $2,145.35
            + (80,000-40,100) * 0.068 = $2,713.20
            = $4,858.55
        """
        assert _mn_bracket_tax(
            Decimal("80000"), FilingStatus.HOH
        ) == Decimal("4858.55")

    def test_mfs_50k(self):
        """MFS $50,000 taxable:
            5.35% on first $23,810 = $1,273.835
            + (50,000-23,810) * 0.068 = $1,780.92
            = $3,054.7550 -> $3,054.76
        """
        assert _mn_bracket_tax(
            Decimal("50000"), FilingStatus.MFS
        ) == Decimal("3054.76")

    def test_rate_monotonic_single(self):
        """Single tax must be monotonically increasing in taxable income."""
        amounts = [
            Decimal("5000"),
            Decimal("25000"),
            Decimal("50000"),
            Decimal("100000"),
            Decimal("200000"),
            Decimal("500000"),
        ]
        taxes = [_mn_bracket_tax(a, FilingStatus.SINGLE) for a in amounts]
        for prev, curr in zip(taxes, taxes[1:]):
            assert curr > prev


# ---------------------------------------------------------------------------
# _mn_taxable_income flow
# ---------------------------------------------------------------------------


class TestMinnesotaTaxableIncomeFlow:
    def test_line9_single_no_dependents(self, federal_single_65k):
        line_3, line_4, line_5, line_9 = _mn_taxable_income(federal_single_65k)
        assert line_3 == Decimal("65000")
        assert line_4 == Decimal("14950")
        assert line_5 == Decimal("0")
        assert line_9 == Decimal("50050")

    def test_line9_mfj_two_dependents(self):
        ft = FederalTotals(
            filing_status=FilingStatus.MFJ,
            num_dependents=2,
            adjusted_gross_income=Decimal("120000"),
            taxable_income=Decimal("90000"),
            total_federal_tax=Decimal("12000"),
            federal_income_tax=Decimal("12000"),
            federal_standard_deduction=Decimal("30000"),
            federal_itemized_deductions_total=Decimal("0"),
            deduction_taken=Decimal("30000"),
        )
        line_3, line_4, line_5, line_9 = _mn_taxable_income(ft)
        assert line_3 == Decimal("120000")
        assert line_4 == Decimal("29900")  # MFJ std ded
        assert line_5 == Decimal("10400")  # 2 * $5,200
        assert line_9 == Decimal("79700")

    def test_line9_floors_at_zero(self):
        """Low income + large std ded + many dependents -> line 9 = 0."""
        ft = FederalTotals(
            filing_status=FilingStatus.SINGLE,
            num_dependents=5,
            adjusted_gross_income=Decimal("10000"),
            taxable_income=Decimal("0"),
            total_federal_tax=Decimal("0"),
            federal_income_tax=Decimal("0"),
            federal_standard_deduction=Decimal("15750"),
            federal_itemized_deductions_total=Decimal("0"),
            deduction_taken=Decimal("15750"),
        )
        _, _, line_5, line_9 = _mn_taxable_income(ft)
        assert line_5 == Decimal("26000")
        assert line_9 == Decimal("0")


# ---------------------------------------------------------------------------
# compute() — resident case matches the pinned wrap-correctness numbers
# ---------------------------------------------------------------------------


class TestMinnesotaPluginComputeResident:
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

    def test_state_code_is_mn(self, single_65k_return, federal_single_65k):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state == "MN"

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

    def test_resident_65k_single_wrap_lock(
        self, single_65k_return, federal_single_65k
    ):
        """**$65k Single MN resident WRAP-CORRECTNESS LOCK.**

        This is the canonical locked number the fan-out spec asked for.
        Plugin output must equal $2,931.14 bit-for-bit. A drift in the
        bracket schedule, the standard deduction, the rounding mode, or
        the M1 line flow will fail this test immediately.

        Because tenforty does not support 2025/MN_M1, this number is the
        plugin's own computation against its own bracket constants — it is
        NOT an independent third-party reference. An external
        reconciliation against the MN DOR printed tax table is tracked as
        a v1 TODO.
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        assert state_tax == Decimal("2931.14")

    def test_state_taxable_income_65k(
        self, single_65k_return, federal_single_65k
    ):
        """$65k AGI - $14,950 MN std ded = $50,050 MN taxable income."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal(
            "50050.00"
        )

    def test_state_agi_matches_federal_agi(
        self, single_65k_return, federal_single_65k
    ):
        """MN starting point is federal AGI (Form M1 line 1)."""
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
        """All numeric fields in state_specific must be Decimal."""
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
            "apportionment_fraction",
            "m1_line_1_federal_agi",
            "m1_line_2_additions",
            "m1_line_3",
            "m1_line_4_standard_deduction",
            "m1_line_5_exemptions",
            "m1_line_6_state_refund_addback",
            "m1_line_7_subtractions",
            "m1_line_8_total_subtractions",
            "m1_line_9_mn_taxable_income",
            "m1_line_10_tax",
        ]
        for key in numeric_keys:
            assert key in result.state_specific, f"missing {key}"
            assert isinstance(
                result.state_specific[key], Decimal
            ), f"{key} is not Decimal"

    def test_m1_line_numbers_match_manual_flow(
        self, single_65k_return, federal_single_65k
    ):
        """Spot-check each M1 line detail for the $65k Single scenario."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = result.state_specific
        assert ss["m1_line_1_federal_agi"] == Decimal("65000.00")
        assert ss["m1_line_2_additions"] == Decimal("0.00")
        assert ss["m1_line_3"] == Decimal("65000.00")
        assert ss["m1_line_4_standard_deduction"] == Decimal("14950.00")
        assert ss["m1_line_5_exemptions"] == Decimal("0.00")
        assert ss["m1_line_6_state_refund_addback"] == Decimal("0.00")
        assert ss["m1_line_7_subtractions"] == Decimal("0.00")
        assert ss["m1_line_8_total_subtractions"] == Decimal("14950.00")
        assert ss["m1_line_9_mn_taxable_income"] == Decimal("50050.00")
        assert ss["m1_line_10_tax"] == Decimal("2931.14")

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
        """Round-trip the StateReturn through Pydantic JSON to verify it
        satisfies the canonical model contract."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        dumped = result.model_dump(mode="json")
        rehydrated = StateReturn.model_validate(dumped)
        assert rehydrated.state == "MN"
        assert rehydrated.residency == ResidencyStatus.RESIDENT

    def test_tenforty_gap_flag_set(
        self, single_65k_return, federal_single_65k
    ):
        """LOUD flag: tenforty support is absent for MN."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["tenforty_supports_mn"] is False
        note = result.state_specific["tenforty_status_note"]
        assert "OTS does not support 2025/MN_M1" in note

    def test_v1_limitations_present(
        self, single_65k_return, federal_single_65k
    ):
        """state_specific must expose the v1 limitations list."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        v1 = result.state_specific["v1_limitations"]
        assert isinstance(v1, list)
        assert len(v1) >= 5
        # spot-check one key limitation
        joined = " ".join(v1).lower()
        assert "m1nr" in joined or "nonresident" in joined


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment
# ---------------------------------------------------------------------------


class TestMinnesotaPluginComputeNonresident:
    def test_nonresident_half_year_prorates_tax(
        self, single_65k_return, federal_single_65k
    ):
        """Nonresident / 182 days -> 182/365 of resident-basis tax."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=182,
        )
        full = result.state_specific["state_total_tax_resident_basis"]
        apportioned = result.state_specific["state_total_tax"]
        assert full == Decimal("2931.14")
        assert apportioned < full
        expected = (full * Decimal(182) / Decimal(365)).quantize(
            Decimal("0.01")
        )
        assert apportioned == expected

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


class TestMinnesotaPluginApportionIncome:
    def test_apportion_income_resident_full_amounts(self, single_65k_return):
        """Residents get full amounts for every canonical income category."""
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
        """Nonresident with 182 days -> wages * 182/365."""
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


class TestMinnesotaPluginFormIds:
    def test_form_ids(self):
        form_ids = PLUGIN.form_ids()
        assert "MN Form M1" in form_ids
        assert form_ids == ["MN Form M1"]

    def test_render_pdfs_returns_empty_list(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Fan-out follow-up: actual M1 fill is not yet implemented."""
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
        assert PLUGIN.render_pdfs(state_return, Path("/tmp")) == []


# ---------------------------------------------------------------------------
# ReciprocityTable consistency
# ---------------------------------------------------------------------------


def test_reciprocity_matches_json():
    """ReciprocityTable.load().partners_of('MN') must equal the plugin's
    meta.reciprocity_partners as a frozenset. Catches drift between
    skill/reference/state-reciprocity.json and the MN plugin."""
    table = ReciprocityTable.load()
    mn_partners_from_table = table.partners_of("MN")
    assert mn_partners_from_table == frozenset({"MI", "ND"})
    assert frozenset(PLUGIN.meta.reciprocity_partners) == mn_partners_from_table


def test_reciprocity_mn_has_income_tax():
    """Sanity: MN has an individual income tax (not in the no-tax list)."""
    table = ReciprocityTable.load()
    assert table.has_income_tax("MN") is True


def test_reciprocity_mi_mn_bilateral():
    """The MI-MN reciprocity pair must be queryable in both directions."""
    table = ReciprocityTable.load()
    assert table.are_reciprocal("MN", "MI") is True
    assert table.are_reciprocal("MI", "MN") is True


def test_reciprocity_mn_nd_bilateral():
    """The MN-ND reciprocity pair must be queryable in both directions."""
    table = ReciprocityTable.load()
    assert table.are_reciprocal("MN", "ND") is True
    assert table.are_reciprocal("ND", "MN") is True


def test_reciprocity_mn_wi_not_reciprocal():
    """MN-WI reciprocity was terminated in 2010 — must NOT show as
    reciprocal in TY2025."""
    table = ReciprocityTable.load()
    assert table.are_reciprocal("MN", "WI") is False
    assert table.are_reciprocal("WI", "MN") is False


# ---------------------------------------------------------------------------
# V1 limitations list sanity
# ---------------------------------------------------------------------------


def test_v1_limitations_module_constant_non_empty():
    assert len(MN_V1_LIMITATIONS) >= 5


def test_v1_limitations_mentions_m1nr():
    """The nonresident M1NR limitation must be called out."""
    joined = " ".join(MN_V1_LIMITATIONS).lower()
    assert "m1nr" in joined
