"""Maine state plugin tests.

Mirrors the MN / KS hand-rolled plugin test suites. tenforty's default
OTS backend does NOT support 2025/ME_1040ME (raises ValueError); the
graph backend returns a number but omits the Maine personal exemption,
producing a +$347.63 over-statement on a $65k Single return. The Maine
plugin therefore hand-rolls Form 1040ME from the Maine RS published
TY2025 rate schedules and personal exemption amounts.

Reference scenario (hand-computed from ME RS Tax Alert September 2024
and verified against tenforty's me_1040me_2025.json bracket constants):

    Single / $65,000 W-2 / Standard
      Line 14  Federal AGI                $65,000.00
      Line 16  Maine AGI                  $65,000.00
      Line 17  Standard Deduction         $15,750.00
      Line 18  Subtotal                   $49,250.00
      Line 19  Personal Exemption          $5,150.00
      Line 20  Maine Taxable Income       $44,100.00
      Line 21  Maine Income Tax:
               0-26,800 @ 5.80%            $1,554.40
               26,800-44,100 @ 6.75%       $1,167.75
               Total                       $2,722.15

Sources:
    - Maine RS Tax Alert September 2024 (TY2025 rate schedules and
      personal exemption / standard deduction amounts)
    - Maine RS Form 1040ME 2025
      https://www.maine.gov/revenue/tax-return-forms/income-estate-tax
    - tenforty graph file me_1040me_2025.json (bracket constants
      cross-check)

Reciprocity: Maine has NO bilateral reciprocity agreements — verified
against skill/reference/state-reciprocity.json.
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
from skill.scripts.states.me import (
    ME_TY2025_BRACKETS,
    ME_TY2025_BRACKETS_HOH,
    ME_TY2025_BRACKETS_MFJ,
    ME_TY2025_BRACKETS_SINGLE,
    ME_TY2025_GRAPH_BACKEND_65K_SINGLE,
    ME_TY2025_PERSONAL_EXEMPTION_PER_PERSON,
    ME_TY2025_STANDARD_DEDUCTION,
    ME_V1_LIMITATIONS,
    MainePlugin,
    PLUGIN,
    me_bracket_tax,
    me_personal_exemption,
    me_standard_deduction,
    me_taxable_income,
)


# ---------------------------------------------------------------------------
# Shared fixtures — a Single $65k W-2 return domiciled in Maine
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
            street1="51 Commerce Dr", city="Augusta", state="ME", zip="04330"
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


class TestMainePluginMeta:
    def test_meta_code(self):
        assert PLUGIN.meta.code == "ME"

    def test_meta_name(self):
        assert PLUGIN.meta.name == "Maine"

    def test_meta_has_income_tax(self):
        assert PLUGIN.meta.has_income_tax is True

    def test_meta_starting_point(self):
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI

    def test_meta_submission_channel(self):
        """Maine uses its own state DOR free portal (Maine Tax Portal)."""
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )

    def test_meta_no_reciprocity_partners(self):
        """Maine has NO bilateral reciprocity agreements."""
        assert PLUGIN.meta.reciprocity_partners == ()
        assert len(PLUGIN.meta.reciprocity_partners) == 0

    def test_plugin_is_state_plugin_protocol(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_maine_plugin_instance(self):
        assert isinstance(PLUGIN, MainePlugin)

    def test_meta_dor_url_is_maine_gov(self):
        assert "maine.gov/revenue" in PLUGIN.meta.dor_url

    def test_meta_free_efile_url_present(self):
        assert PLUGIN.meta.free_efile_url is not None
        assert "maine.gov" in PLUGIN.meta.free_efile_url

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_bracket_rates(self):
        notes = PLUGIN.meta.notes
        assert "5.80" in notes
        assert "6.75" in notes
        assert "7.15" in notes

    def test_meta_notes_mention_tenforty_gap(self):
        """Notes MUST loudly flag the tenforty default-backend gap and
        the graph-backend personal-exemption omission."""
        notes = PLUGIN.meta.notes.lower()
        assert "tenforty" in notes
        assert "personal exemption" in notes
        assert "graph" in notes

    def test_meta_notes_cite_maine_rs(self):
        notes = PLUGIN.meta.notes.lower()
        assert "maine rs" in notes or "maine revenue" in notes

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "VT"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# No-reciprocity verification
# ---------------------------------------------------------------------------


class TestMaineNoReciprocity:
    def test_no_reciprocity_via_reciprocity_table(self):
        """ReciprocityTable.partners_of('ME') must equal the empty set."""
        table = ReciprocityTable.load()
        assert table.partners_of("ME") == frozenset()

    def test_not_reciprocal_with_neighbors(self):
        """Maine borders NH (no income tax) and shares no reciprocity with
        any other state."""
        table = ReciprocityTable.load()
        for other in ("NH", "MA", "VT", "CT", "RI", "NY", "NJ"):
            assert table.are_reciprocal("ME", other) is False

    def test_meta_partners_match_reciprocity_table(self):
        """Plugin meta must agree with skill/reference/state-reciprocity.json."""
        table = ReciprocityTable.load()
        assert (
            frozenset(PLUGIN.meta.reciprocity_partners)
            == table.partners_of("ME")
        )


# ---------------------------------------------------------------------------
# Constants sanity — TY2025 bracket and standard-deduction values
# ---------------------------------------------------------------------------


class TestMaineTY2025Constants:
    def test_standard_deduction_single(self):
        """Maine conforms to federal std ded for TY2025: $15,750 Single."""
        assert ME_TY2025_STANDARD_DEDUCTION[FilingStatus.SINGLE] == Decimal(
            "15750"
        )

    def test_standard_deduction_mfj(self):
        assert ME_TY2025_STANDARD_DEDUCTION[FilingStatus.MFJ] == Decimal(
            "31500"
        )

    def test_standard_deduction_hoh(self):
        assert ME_TY2025_STANDARD_DEDUCTION[FilingStatus.HOH] == Decimal(
            "23625"
        )

    def test_standard_deduction_mfs_matches_single(self):
        assert (
            ME_TY2025_STANDARD_DEDUCTION[FilingStatus.MFS]
            == ME_TY2025_STANDARD_DEDUCTION[FilingStatus.SINGLE]
        )

    def test_standard_deduction_qss_matches_mfj(self):
        assert (
            ME_TY2025_STANDARD_DEDUCTION[FilingStatus.QSS]
            == ME_TY2025_STANDARD_DEDUCTION[FilingStatus.MFJ]
        )

    def test_personal_exemption_per_person(self):
        """Source: Maine RS Tax Alert Sept 2024 — $5,150 per exemption."""
        assert ME_TY2025_PERSONAL_EXEMPTION_PER_PERSON == Decimal("5150")

    def test_bracket_single_first_row(self):
        b = ME_TY2025_BRACKETS_SINGLE[0]
        assert b.low == Decimal("0")
        assert b.high == Decimal("26800")
        assert b.rate == Decimal("0.058")

    def test_bracket_single_top_row(self):
        b = ME_TY2025_BRACKETS_SINGLE[-1]
        assert b.low == Decimal("63450")
        assert b.high is None
        assert b.rate == Decimal("0.0715")

    def test_bracket_mfj_first_row_double_single(self):
        """MFJ first-bracket top is exactly 2x the Single first-bracket top."""
        single_top = ME_TY2025_BRACKETS_SINGLE[0].high
        mfj_top = ME_TY2025_BRACKETS_MFJ[0].high
        assert mfj_top == single_top * 2

    def test_bracket_hoh_first_row(self):
        b = ME_TY2025_BRACKETS_HOH[0]
        assert b.high == Decimal("40200")
        assert b.rate == Decimal("0.058")

    def test_all_statuses_have_three_brackets(self):
        for fs in (
            FilingStatus.SINGLE,
            FilingStatus.MFJ,
            FilingStatus.MFS,
            FilingStatus.HOH,
            FilingStatus.QSS,
        ):
            assert len(ME_TY2025_BRACKETS[fs]) == 3

    def test_all_statuses_use_same_rates(self):
        expected = (Decimal("0.058"), Decimal("0.0675"), Decimal("0.0715"))
        for fs in (
            FilingStatus.SINGLE,
            FilingStatus.MFJ,
            FilingStatus.MFS,
            FilingStatus.HOH,
            FilingStatus.QSS,
        ):
            actual = tuple(b.rate for b in ME_TY2025_BRACKETS[fs])
            assert actual == expected, f"{fs} rates drifted"


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestMainePersonalExemption:
    def test_single_no_dependents(self):
        assert me_personal_exemption(FilingStatus.SINGLE, 0) == Decimal("5150")

    def test_mfj_no_dependents(self):
        """MFJ gets two filer exemptions = 2 * $5,150 = $10,300."""
        assert me_personal_exemption(FilingStatus.MFJ, 0) == Decimal("10300")

    def test_hoh_one_dependent(self):
        """HOH (1 filer) + 1 dependent = 2 * $5,150 = $10,300."""
        assert me_personal_exemption(FilingStatus.HOH, 1) == Decimal("10300")

    def test_mfj_two_dependents(self):
        """MFJ (2 filers) + 2 deps = 4 * $5,150 = $20,600."""
        assert me_personal_exemption(FilingStatus.MFJ, 2) == Decimal("20600")

    def test_negative_dependents_clamped_to_zero(self):
        assert me_personal_exemption(FilingStatus.SINGLE, -3) == Decimal(
            "5150"
        )

    def test_qss_two_filer_exemptions(self):
        assert me_personal_exemption(FilingStatus.QSS, 0) == Decimal("10300")


class TestMaineBracketMath:
    def test_zero_taxable_income_zero_tax(self):
        assert me_bracket_tax(Decimal("0"), FilingStatus.SINGLE) == Decimal(
            "0.00"
        )

    def test_negative_taxable_income_zero_tax(self):
        assert me_bracket_tax(
            Decimal("-1000"), FilingStatus.SINGLE
        ) == Decimal("0.00")

    def test_single_within_first_bracket(self):
        """$10,000 @ 5.8% = $580.00 flat."""
        assert me_bracket_tax(
            Decimal("10000"), FilingStatus.SINGLE
        ) == Decimal("580.00")

    def test_single_at_first_bracket_ceiling(self):
        """$26,800 @ 5.8% = $1,554.40."""
        assert me_bracket_tax(
            Decimal("26800"), FilingStatus.SINGLE
        ) == Decimal("1554.40")

    def test_single_44100_locked(self):
        """$44,100 (the $65k Single ti after std ded + exemption):
            5.8% * 26,800 = 1,554.40
            6.75% * (44,100-26,800) = 6.75% * 17,300 = 1,167.75
            Total = 2,722.15
        """
        assert me_bracket_tax(
            Decimal("44100"), FilingStatus.SINGLE
        ) == Decimal("2722.15")

    def test_single_at_top_bracket(self):
        """$100,000 taxable:
            5.8% * 26,800 = 1,554.40
            6.75% * 36,650 = 2,473.875
            7.15% * 36,550 = 2,613.325
            Total = 6,641.60
        """
        assert me_bracket_tax(
            Decimal("100000"), FilingStatus.SINGLE
        ) == Decimal("6641.60")

    def test_mfj_uses_doubled_brackets(self):
        """$60,000 MFJ taxable — entirely within the 5.8% MFJ bracket
        (top of MFJ first bracket = $53,600)."""
        # 5.8% * 53,600 = 3,108.80
        # 6.75% * (60,000-53,600) = 6.75% * 6,400 = 432.00
        # Total = 3,540.80
        assert me_bracket_tax(
            Decimal("60000"), FilingStatus.MFJ
        ) == Decimal("3540.80")

    def test_mfs_uses_single_brackets(self):
        for ti in (Decimal("10000"), Decimal("44100"), Decimal("80000")):
            assert me_bracket_tax(ti, FilingStatus.MFS) == me_bracket_tax(
                ti, FilingStatus.SINGLE
            )

    def test_qss_uses_mfj_brackets(self):
        for ti in (Decimal("10000"), Decimal("60000"), Decimal("150000")):
            assert me_bracket_tax(ti, FilingStatus.QSS) == me_bracket_tax(
                ti, FilingStatus.MFJ
            )

    def test_rate_monotonic_single(self):
        amounts = [
            Decimal("5000"),
            Decimal("25000"),
            Decimal("44100"),
            Decimal("100000"),
            Decimal("250000"),
        ]
        taxes = [me_bracket_tax(a, FilingStatus.SINGLE) for a in amounts]
        for prev, curr in zip(taxes, taxes[1:]):
            assert curr > prev


class TestMaineTaxableIncomeFlow:
    def test_single_65k_no_dependents(self, federal_single_65k):
        l16, l17, l18, l19, l20 = me_taxable_income(federal_single_65k)
        assert l16 == Decimal("65000")
        assert l17 == Decimal("15750")
        assert l18 == Decimal("49250")
        assert l19 == Decimal("5150")
        assert l20 == Decimal("44100")

    def test_low_income_floors_at_zero(self):
        ft = FederalTotals(
            filing_status=FilingStatus.SINGLE,
            num_dependents=3,
            adjusted_gross_income=Decimal("10000"),
            taxable_income=Decimal("0"),
            total_federal_tax=Decimal("0"),
            federal_income_tax=Decimal("0"),
            federal_standard_deduction=Decimal("15750"),
            federal_itemized_deductions_total=Decimal("0"),
            deduction_taken=Decimal("15750"),
        )
        _, _, l18, l19, l20 = me_taxable_income(ft)
        assert l18 == Decimal("0")  # 10k - 15.75k -> floored
        assert l19 == Decimal("20600")  # 4 * 5150
        assert l20 == Decimal("0")  # floored


# ---------------------------------------------------------------------------
# compute() — resident
# ---------------------------------------------------------------------------


class TestMainePluginComputeResident:
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

    def test_state_code_is_me(self, single_65k_return, federal_single_65k):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state == "ME"

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
        """**$65k Single ME resident WRAP-CORRECTNESS LOCK.**

        Hand-rolled per Maine RS published TY2025 schedule:
            Federal AGI         $65,000.00
            ME std ded         -$15,750.00
            Personal exempt    - $5,150.00
            ME taxable income   $44,100.00
            Tax (5.8/6.75)      $2,722.15

        This is the canonical locked number — NOT the tenforty graph
        backend value ($3,069.78), which omits the personal exemption.
        Drift in brackets, std ded, exemption, or rounding fails CI.
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        assert state_tax == Decimal("2722.15")

    def test_state_taxable_income_65k(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal(
            "44100.00"
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

    def test_me_line_numbers_match_manual_flow(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = result.state_specific
        assert ss["me_line_14_federal_agi"] == Decimal("65000.00")
        assert ss["me_line_15_modifications"] == Decimal("0.00")
        assert ss["me_line_16_me_agi"] == Decimal("65000.00")
        assert ss["me_line_17_deduction"] == Decimal("15750.00")
        assert ss["me_line_18_subtotal"] == Decimal("49250.00")
        assert ss["me_line_19_personal_exemption"] == Decimal("5150.00")
        assert ss["me_line_20_taxable_income"] == Decimal("44100.00")
        assert ss["me_line_21_tax"] == Decimal("2722.15")

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
            "me_line_14_federal_agi",
            "me_line_15_modifications",
            "me_line_16_me_agi",
            "me_line_17_deduction",
            "me_line_18_subtotal",
            "me_line_19_personal_exemption",
            "me_line_20_taxable_income",
            "me_line_21_tax",
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
        assert rehydrated.state == "ME"
        assert rehydrated.residency == ResidencyStatus.RESIDENT

    def test_tenforty_gap_flag_set(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific[
            "tenforty_supports_me_default_backend"
        ] is False
        assert result.state_specific["tenforty_supports_me_graph_backend"] is True
        note = result.state_specific["tenforty_status_note"]
        assert "personal exemption" in note.lower()

    def test_v1_limitations_present(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        v1 = result.state_specific["v1_limitations"]
        assert isinstance(v1, list)
        assert len(v1) >= 5


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment
# ---------------------------------------------------------------------------


class TestMainePluginComputeNonresident:
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
        assert full == Decimal("2722.15")
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


class TestMainePluginApportionIncome:
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


class TestMainePluginFormIds:
    def test_form_ids(self):
        form_ids = PLUGIN.form_ids()
        assert "ME Form 1040ME" in form_ids
        assert form_ids == ["ME Form 1040ME"]

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
# Gatekeeper test — tenforty default backend gap + graph divergence
# ---------------------------------------------------------------------------


class TestMaineTenfortyGap:
    """When this test starts failing, tenforty has changed Maine support.

    Pins both:
      (a) the default OTS backend STILL raises ValueError, AND
      (b) the graph backend STILL returns the divergent $3,069.78 number.

    If (a) flips, rewrite the plugin as a tenforty default-backend wrap
    (mirror nc.py / oh.py) and delete this test.
    If (b) flips (graph backend changes), the personal exemption might
    have been added — re-verify against ME RS, and either update the
    locked number or convert the plugin to a graph-backend wrap.
    """

    def test_default_backend_still_raises(self):
        import tenforty
        with pytest.raises(ValueError, match="OTS does not support"):
            tenforty.evaluate_return(
                year=2025,
                state="ME",
                filing_status="Single",
                w2_income=65_000,
                num_dependents=0,
                standard_or_itemized="Standard",
                itemized_deductions=0,
            )

    def test_graph_backend_pinned_at_known_divergent_value(self):
        """The graph-backend value is pinned so any tenforty drift trips
        CI. The constant ME_TY2025_GRAPH_BACKEND_65K_SINGLE represents
        what tenforty's graph backend currently returns ($3,069.78), NOT
        what the plugin's canonical state_total_tax reports ($2,722.15)."""
        import tenforty
        result = tenforty.evaluate_return(
            year=2025,
            state="ME",
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
        assert graph_total == ME_TY2025_GRAPH_BACKEND_65K_SINGLE

    def test_plugin_diverges_from_graph_by_personal_exemption_amount(
        self, single_65k_return, federal_single_65k
    ):
        """The hand-rolled value ($2,722.15) and the graph value ($3,069.78)
        differ by exactly $5,150 * 6.75% = $347.625 — i.e., one personal
        exemption applied at the second marginal rate. This is a
        regression guard for the divergence story in the docstring."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        plugin_tax = result.state_specific["state_total_tax"]
        graph_tax = ME_TY2025_GRAPH_BACKEND_65K_SINGLE
        delta = (graph_tax - plugin_tax).quantize(Decimal("0.01"))
        # 5150 * 0.0675 = 347.625 -> 347.63 rounded to cents
        assert delta == Decimal("347.63")


# ---------------------------------------------------------------------------
# V1 limitations list sanity
# ---------------------------------------------------------------------------


def test_v1_limitations_module_constant_non_empty():
    assert len(ME_V1_LIMITATIONS) >= 5


def test_v1_limitations_mentions_schedule_nr():
    """The nonresident Schedule NR limitation must be called out."""
    joined = " ".join(ME_V1_LIMITATIONS).lower()
    assert "schedule nr" in joined or "nonresident" in joined


def test_v1_limitations_mentions_personal_exemption_phaseout():
    joined = " ".join(ME_V1_LIMITATIONS).lower()
    assert "phaseout" in joined and "exemption" in joined
