"""Rhode Island state plugin tests.

Mirrors the MN / KS / ME hand-rolled plugin test suites. tenforty's
default OTS backend does NOT support 2025/RI_1040 (raises ValueError);
the graph backend returns a number but omits the RI personal exemption,
producing a +$195.00 over-statement on a $65k Single return. The RI
plugin therefore hand-rolls Form RI-1040 from the RI Division of
Taxation TY2025 rate schedule.

Reference scenario:

    Single / $65,000 W-2 / Standard
      Line 1   Federal AGI                $65,000.00
      Line 3   Modified federal AGI       $65,000.00
      Line 4   Standard Deduction         $10,900.00
      Line 5   Subtotal                   $54,100.00
      Line 6   Personal Exemption          $5,200.00
      Line 7   RI Taxable Income          $48,900.00
      Line 8   RI Tax (3.75% flat)         $1,833.75

Sources:
    - RI Division of Taxation, Individual Tax Forms hub:
      https://tax.ri.gov/forms/individual-tax-forms
    - RI Division of Taxation 2025 Indexed Amounts release
    - tenforty graph file ri_1040_2025.json (bracket constants)

Reciprocity: Rhode Island has NO bilateral reciprocity agreements —
verified against skill/reference/state-reciprocity.json.
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
from skill.scripts.states.ri import (
    LOCK_VALUE,
    PLUGIN,
    RI_TY2025_BRACKETS,
    RI_TY2025_BRACKETS_BY_STATUS,
    RI_TY2025_GRAPH_BACKEND_65K_SINGLE,
    RI_TY2025_PERSONAL_EXEMPTION_PER_PERSON,
    RI_TY2025_STANDARD_DEDUCTION,
    RI_V1_LIMITATIONS,
    RhodeIslandPlugin,
    ri_bracket_tax,
    ri_personal_exemption,
    ri_standard_deduction,
    ri_taxable_income,
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
            street1="1 Capitol Hill", city="Providence", state="RI", zip="02908"
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


class TestRhodeIslandPluginMeta:
    def test_meta_code(self):
        assert PLUGIN.meta.code == "RI"

    def test_meta_name(self):
        assert PLUGIN.meta.name == "Rhode Island"

    def test_meta_has_income_tax(self):
        assert PLUGIN.meta.has_income_tax is True

    def test_meta_starting_point(self):
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI

    def test_meta_submission_channel(self):
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )

    def test_meta_no_reciprocity_partners(self):
        assert PLUGIN.meta.reciprocity_partners == ()
        assert len(PLUGIN.meta.reciprocity_partners) == 0

    def test_plugin_is_state_plugin_protocol(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_rhode_island_plugin_instance(self):
        assert isinstance(PLUGIN, RhodeIslandPlugin)

    def test_meta_dor_url_is_tax_ri_gov(self):
        assert "tax.ri.gov" in PLUGIN.meta.dor_url

    def test_meta_free_efile_url_present(self):
        assert PLUGIN.meta.free_efile_url is not None
        assert "ri.gov" in PLUGIN.meta.free_efile_url

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_bracket_rates(self):
        notes = PLUGIN.meta.notes
        assert "3.75" in notes
        assert "4.75" in notes
        assert "5.99" in notes

    def test_meta_notes_mention_tenforty_gap(self):
        notes = PLUGIN.meta.notes.lower()
        assert "tenforty" in notes
        assert "personal exemption" in notes
        assert "graph" in notes

    def test_meta_notes_cite_ri_division(self):
        notes = PLUGIN.meta.notes.lower()
        assert "ri division" in notes or "rhode island" in notes

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "CT"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# No-reciprocity verification
# ---------------------------------------------------------------------------


class TestRhodeIslandNoReciprocity:
    def test_no_reciprocity_via_reciprocity_table(self):
        table = ReciprocityTable.load()
        assert table.partners_of("RI") == frozenset()

    def test_not_reciprocal_with_neighbors(self):
        """Rhode Island borders MA and CT and shares no reciprocity with
        any state."""
        table = ReciprocityTable.load()
        for other in ("MA", "CT", "NH", "VT", "ME", "NY"):
            assert table.are_reciprocal("RI", other) is False

    def test_meta_partners_match_reciprocity_table(self):
        table = ReciprocityTable.load()
        assert (
            frozenset(PLUGIN.meta.reciprocity_partners)
            == table.partners_of("RI")
        )


# ---------------------------------------------------------------------------
# TY2025 constants sanity
# ---------------------------------------------------------------------------


class TestRhodeIslandTY2025Constants:
    def test_standard_deduction_single(self):
        """Source: RI Division of Taxation 2025 Indexed Amounts + tenforty
        graph file."""
        assert RI_TY2025_STANDARD_DEDUCTION[FilingStatus.SINGLE] == Decimal(
            "10900"
        )

    def test_standard_deduction_mfj(self):
        assert RI_TY2025_STANDARD_DEDUCTION[FilingStatus.MFJ] == Decimal(
            "21800"
        )

    def test_standard_deduction_hoh(self):
        assert RI_TY2025_STANDARD_DEDUCTION[FilingStatus.HOH] == Decimal(
            "16350"
        )

    def test_standard_deduction_mfs_matches_single(self):
        assert (
            RI_TY2025_STANDARD_DEDUCTION[FilingStatus.MFS]
            == RI_TY2025_STANDARD_DEDUCTION[FilingStatus.SINGLE]
        )

    def test_standard_deduction_qss_matches_mfj(self):
        assert (
            RI_TY2025_STANDARD_DEDUCTION[FilingStatus.QSS]
            == RI_TY2025_STANDARD_DEDUCTION[FilingStatus.MFJ]
        )

    def test_personal_exemption_per_person(self):
        """Source: RI Division of Taxation 2025 Indexed Amounts release."""
        assert RI_TY2025_PERSONAL_EXEMPTION_PER_PERSON == Decimal("5200")

    def test_brackets_have_three_rows(self):
        assert len(RI_TY2025_BRACKETS) == 3

    def test_bracket_first_row(self):
        b = RI_TY2025_BRACKETS[0]
        assert b.low == Decimal("0")
        assert b.high == Decimal("79900")
        assert b.rate == Decimal("0.0375")

    def test_bracket_middle_row(self):
        b = RI_TY2025_BRACKETS[1]
        assert b.low == Decimal("79900")
        assert b.high == Decimal("181650")
        assert b.rate == Decimal("0.0475")

    def test_bracket_top_row(self):
        b = RI_TY2025_BRACKETS[2]
        assert b.low == Decimal("181650")
        assert b.high is None
        assert b.rate == Decimal("0.0599")

    def test_brackets_filing_status_independent(self):
        """RI uses the SAME bracket schedule for ALL filing statuses —
        single, joint, HOH all hit the 4.75% bracket at $79,900 of TI."""
        for fs in (
            FilingStatus.SINGLE,
            FilingStatus.MFJ,
            FilingStatus.MFS,
            FilingStatus.HOH,
            FilingStatus.QSS,
        ):
            assert RI_TY2025_BRACKETS_BY_STATUS[fs] is RI_TY2025_BRACKETS


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestRhodeIslandPersonalExemption:
    def test_single_no_dependents(self):
        assert ri_personal_exemption(FilingStatus.SINGLE, 0) == Decimal("5200")

    def test_mfj_no_dependents(self):
        assert ri_personal_exemption(FilingStatus.MFJ, 0) == Decimal("10400")

    def test_hoh_one_dependent(self):
        assert ri_personal_exemption(FilingStatus.HOH, 1) == Decimal("10400")

    def test_mfj_two_dependents(self):
        assert ri_personal_exemption(FilingStatus.MFJ, 2) == Decimal("20800")

    def test_negative_dependents_clamped_to_zero(self):
        assert ri_personal_exemption(FilingStatus.SINGLE, -3) == Decimal(
            "5200"
        )

    def test_qss_two_filer_exemptions(self):
        assert ri_personal_exemption(FilingStatus.QSS, 0) == Decimal("10400")


class TestRhodeIslandBracketMath:
    def test_zero_taxable_income_zero_tax(self):
        assert ri_bracket_tax(Decimal("0")) == Decimal("0.00")

    def test_negative_taxable_income_zero_tax(self):
        assert ri_bracket_tax(Decimal("-1000")) == Decimal("0.00")

    def test_within_first_bracket(self):
        """$10,000 @ 3.75% = $375.00."""
        assert ri_bracket_tax(Decimal("10000")) == Decimal("375.00")

    def test_at_first_bracket_ceiling(self):
        """$79,900 @ 3.75% = $2,996.25."""
        assert ri_bracket_tax(Decimal("79900")) == Decimal("2996.25")

    def test_48900_locked(self):
        """$48,900 (the $65k Single ti after std + exemption):
            3.75% * 48,900 = 1,833.75
        """
        assert ri_bracket_tax(Decimal("48900")) == Decimal("1833.75")

    def test_in_middle_bracket(self):
        """$100,000 taxable:
            3.75% * 79,900 = 2,996.25
            4.75% * 20,100 = 954.75
            Total = 3,951.00
        """
        assert ri_bracket_tax(Decimal("100000")) == Decimal("3951.00")

    def test_in_top_bracket(self):
        """$200,000 taxable:
            3.75% * 79,900 = 2,996.25
            4.75% * (181,650-79,900) = 4,833.125
            5.99% * (200,000-181,650) = 1,099.165
            Total = 8,928.54
        """
        assert ri_bracket_tax(Decimal("200000")) == Decimal("8928.54")

    def test_rate_monotonic(self):
        amounts = [
            Decimal("5000"),
            Decimal("48900"),
            Decimal("100000"),
            Decimal("200000"),
            Decimal("500000"),
        ]
        taxes = [ri_bracket_tax(a) for a in amounts]
        for prev, curr in zip(taxes, taxes[1:]):
            assert curr > prev


class TestRhodeIslandTaxableIncomeFlow:
    def test_single_65k_no_dependents(self, federal_single_65k):
        l3, l4, l5, l6, l7 = ri_taxable_income(federal_single_65k)
        assert l3 == Decimal("65000")
        assert l4 == Decimal("10900")
        assert l5 == Decimal("54100")
        assert l6 == Decimal("5200")
        assert l7 == Decimal("48900")

    def test_low_income_floors_at_zero(self):
        ft = FederalTotals(
            filing_status=FilingStatus.SINGLE,
            num_dependents=2,
            adjusted_gross_income=Decimal("8000"),
            taxable_income=Decimal("0"),
            total_federal_tax=Decimal("0"),
            federal_income_tax=Decimal("0"),
            federal_standard_deduction=Decimal("15750"),
            federal_itemized_deductions_total=Decimal("0"),
            deduction_taken=Decimal("15750"),
        )
        _, _, l5, l6, l7 = ri_taxable_income(ft)
        assert l5 == Decimal("0")  # 8k - 10.9k -> floored
        assert l6 == Decimal("15600")  # 3 * 5200
        assert l7 == Decimal("0")  # floored


# ---------------------------------------------------------------------------
# compute() — resident
# ---------------------------------------------------------------------------


class TestRhodeIslandPluginComputeResident:
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

    def test_state_code_is_ri(self, single_65k_return, federal_single_65k):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state == "RI"

    def test_residency_preserved(self, single_65k_return, federal_single_65k):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.residency == ResidencyStatus.RESIDENT

    def test_resident_single_65k_tax_lock(
        self, single_65k_return, federal_single_65k
    ):
        """**$65k Single RI resident WRAP-CORRECTNESS LOCK.**

        Hand-rolled per RI Division of Taxation TY2025:
            Federal AGI         $65,000.00
            RI std ded         -$10,900.00
            Personal exempt    - $5,200.00
            RI taxable income   $48,900.00
            Tax (3.75% flat)    $1,833.75

        This is the canonical locked number — NOT the tenforty graph
        backend value ($2,028.75), which omits the personal exemption.
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        assert state_tax == LOCK_VALUE

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
            "48900.00"
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

    def test_ri_line_numbers_match_manual_flow(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = result.state_specific
        assert ss["ri_line_1_federal_agi"] == Decimal("65000.00")
        assert ss["ri_line_2_modifications"] == Decimal("0.00")
        assert ss["ri_line_3_modified_agi"] == Decimal("65000.00")
        assert ss["ri_line_4_standard_deduction"] == Decimal("10900.00")
        assert ss["ri_line_5_subtotal"] == Decimal("54100.00")
        assert ss["ri_line_6_personal_exemption"] == Decimal("5200.00")
        assert ss["ri_line_7_taxable_income"] == Decimal("48900.00")
        assert ss["ri_line_8_tax"] == Decimal("1833.75")

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
            "ri_line_1_federal_agi",
            "ri_line_2_modifications",
            "ri_line_3_modified_agi",
            "ri_line_4_standard_deduction",
            "ri_line_5_subtotal",
            "ri_line_6_personal_exemption",
            "ri_line_7_taxable_income",
            "ri_line_8_tax",
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
        assert rehydrated.state == "RI"
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
            "tenforty_supports_ri_default_backend"
        ] is False
        assert result.state_specific["tenforty_supports_ri_graph_backend"] is True
        note = result.state_specific["tenforty_status_note"]
        assert "personal exemption" in note.lower()


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment
# ---------------------------------------------------------------------------


class TestRhodeIslandPluginComputeNonresident:
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
        assert full == Decimal("1833.75")
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


class TestRhodeIslandPluginApportionIncome:
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


class TestRhodeIslandPluginFormIds:
    def test_form_ids(self):
        form_ids = PLUGIN.form_ids()
        assert "RI Form RI-1040" in form_ids
        assert form_ids == ["RI Form RI-1040"]

    def test_render_pdfs_returns_empty_list(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """RI Form RI-1040 PDF is flattened (no AcroForm widgets).
        render_pdfs() correctly returns [] until a scaffold renderer
        is implemented."""
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


class TestRhodeIslandTenfortyGap:
    """Pin both the default-backend ValueError and the graph-backend
    divergent value. When either fails, RI tenforty support has changed."""

    def test_default_backend_still_raises(self):
        import tenforty
        with pytest.raises(ValueError, match="OTS does not support"):
            tenforty.evaluate_return(
                year=2025,
                state="RI",
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
            state="RI",
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
        assert graph_total == RI_TY2025_GRAPH_BACKEND_65K_SINGLE

    def test_plugin_diverges_from_graph_by_personal_exemption_amount(
        self, single_65k_return, federal_single_65k
    ):
        """Hand-rolled $1,833.75 vs graph $2,028.75. Delta = $195.00 =
        $5,200 personal exemption * 3.75% bottom-bracket rate."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        plugin_tax = result.state_specific["state_total_tax"]
        graph_tax = RI_TY2025_GRAPH_BACKEND_65K_SINGLE
        delta = (graph_tax - plugin_tax).quantize(Decimal("0.01"))
        assert delta == Decimal("195.00")


# ---------------------------------------------------------------------------
# V1 limitations list sanity
# ---------------------------------------------------------------------------


def test_v1_limitations_module_constant_non_empty():
    assert len(RI_V1_LIMITATIONS) >= 5


def test_v1_limitations_mentions_nonresident_form():
    joined = " ".join(RI_V1_LIMITATIONS).lower()
    assert "ri-1040nr" in joined or "nonresident" in joined


def test_v1_limitations_mentions_personal_exemption_phaseout():
    joined = " ".join(RI_V1_LIMITATIONS).lower()
    assert "phaseout" in joined and "exemption" in joined
