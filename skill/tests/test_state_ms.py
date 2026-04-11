"""Mississippi state plugin tests — TY2025.

Mirrors the wave-4 ``test_state_wi.py`` graph-wrap pattern. MS is wired
into tenforty only via the graph backend; the OTS backend raises
``ValueError: OTS does not support 2025/MS_80105``. The plugin passes
``backend='graph'`` to ``tenforty.evaluate_return``. This test file
pins that backend choice and locks the $65k Single tax number.

Reference scenario (verified 2026-04-11 via direct tenforty probe,
graph backend, 2025):
    Single / $65,000 W-2 / Standard
        -> state_total_tax              = 2054.80
           state_taxable_income         = 56700.00 (= 65k - 6k ex - 2.3k std)
           state_adjusted_gross_income  = 65000.00
           state_tax_bracket            = 0.0  (graph omits)
           state_effective_tax_rate     = 0.0  (graph omits)

Hand verification ($65k Single, TY2025) using HB 531 phase-down:
    MS AGI                = $65,000
    - Personal exemption  = $6,000  (Single, MS DOR Form 80-100)
    - Std ded             = $2,300  (Single, MS DOR Form 80-100)
    MS taxable income     = $56,700
    Tax = (56,700 - 10,000) * 0.044 = 46,700 * 0.044 = $2,054.80
    (zero-bracket on first $10k, flat 4.4% above)

The graph backend matches the DOR primary source bit-for-bit at
$56,700 of taxable income AND the $2,054.80 tax — both the deductions
and the brackets are correctly applied. We wrap (WI pattern), not
hand-roll.

LOUDLY FLAGGED RECENT LAW: MS HB 531 (2022) and HB 1 (2025) phase down
the rate annually: 5.0% (2023), 4.7% (2024), 4.4% (2025), 4.0% (2026),
to 3.0% by 2030. The plugin docstring documents this in detail.

Reciprocity: MS has NO bilateral reciprocity agreements (verified
against skill/reference/state-reciprocity.json).
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
from skill.scripts.states.ms import (
    MS_HB531_PHASEDOWN_NOTES,
    MS_TY2025_EXEMPTION_HOH,
    MS_TY2025_EXEMPTION_MFJ,
    MS_TY2025_EXEMPTION_PER_DEPENDENT,
    MS_TY2025_EXEMPTION_SINGLE,
    MS_TY2025_FLAT_RATE,
    MS_TY2025_STD_DED_HOH,
    MS_TY2025_STD_DED_MFJ,
    MS_TY2025_STD_DED_SINGLE,
    MS_TY2025_ZERO_BRACKET,
    MS_V1_LIMITATIONS,
    MississippiPlugin,
    PLUGIN,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def single_65k_return() -> CanonicalReturn:
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=Person(
            first_name="Beauregard",
            last_name="Magnolia",
            ssn="111-22-3333",
            date_of_birth=dt.date(1990, 1, 1),
        ),
        address=Address(
            street1="500 Clinton Blvd",
            city="Jackson",
            state="MS",
            zip="39201",
        ),
        w2s=[
            W2(
                employer_name="Delta Cotton Co",
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


# ---------------------------------------------------------------------------
# Meta + Protocol conformance
# ---------------------------------------------------------------------------


class TestMississippiPluginMeta:
    def test_meta_fields(self):
        assert PLUGIN.meta.code == "MS"
        assert PLUGIN.meta.name == "Mississippi"
        assert PLUGIN.meta.has_income_tax is True
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )

    def test_plugin_is_state_plugin_protocol(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_mississippi_plugin_instance(self):
        assert isinstance(PLUGIN, MississippiPlugin)

    def test_meta_dor_url_is_dor_ms_gov(self):
        assert "dor.ms.gov" in PLUGIN.meta.dor_url

    def test_meta_free_efile_url_is_ms_tap(self):
        assert PLUGIN.meta.free_efile_url is not None
        assert "tap.dor.ms.gov" in PLUGIN.meta.free_efile_url

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_hb531(self):
        """MS notes must call out HB 531 (load-bearing law change)."""
        assert "HB 531" in PLUGIN.meta.notes

    def test_meta_notes_mention_4_4_percent_rate(self):
        notes = PLUGIN.meta.notes
        assert "4.4" in notes

    def test_meta_notes_mention_zero_bracket_10k(self):
        """The $10k zero-bracket floor is the load-bearing structural fact."""
        notes = PLUGIN.meta.notes
        assert "10,000" in notes or "10000" in notes or "$10" in notes

    def test_meta_notes_mention_no_reciprocity(self):
        assert "reciprocity" in PLUGIN.meta.notes.lower()

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "LA"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Reciprocity invariants
# ---------------------------------------------------------------------------


class TestMississippiNoReciprocity:
    def test_no_reciprocity_partners(self):
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_no_reciprocity_via_reciprocity_table(self):
        table = ReciprocityTable.load()
        assert table.partners_of("MS") == frozenset()

    def test_not_reciprocal_with_neighbors(self):
        table = ReciprocityTable.load()
        for neighbor in ("LA", "AR", "TN", "AL"):
            assert table.are_reciprocal("MS", neighbor) is False


# ---------------------------------------------------------------------------
# TY2025 constants — pin the law
# ---------------------------------------------------------------------------


class TestMississippiTY2025Constants:
    def test_flat_rate_is_4_4_percent(self):
        """MS HB 531 / HB 1 phase-down: TY2025 = 4.40%."""
        assert MS_TY2025_FLAT_RATE == Decimal("0.044")

    def test_zero_bracket_is_10000(self):
        assert MS_TY2025_ZERO_BRACKET == Decimal("10000")

    def test_std_ded_single_is_2300(self):
        assert MS_TY2025_STD_DED_SINGLE == Decimal("2300")

    def test_std_ded_mfj_is_4600(self):
        assert MS_TY2025_STD_DED_MFJ == Decimal("4600")

    def test_std_ded_hoh_is_3400(self):
        assert MS_TY2025_STD_DED_HOH == Decimal("3400")

    def test_exemption_single_is_6000(self):
        assert MS_TY2025_EXEMPTION_SINGLE == Decimal("6000")

    def test_exemption_mfj_is_12000(self):
        assert MS_TY2025_EXEMPTION_MFJ == Decimal("12000")

    def test_exemption_hoh_is_8000(self):
        assert MS_TY2025_EXEMPTION_HOH == Decimal("8000")

    def test_exemption_per_dependent_is_1500(self):
        assert MS_TY2025_EXEMPTION_PER_DEPENDENT == Decimal("1500")

    def test_phasedown_notes_is_tuple(self):
        assert isinstance(MS_HB531_PHASEDOWN_NOTES, tuple)
        assert len(MS_HB531_PHASEDOWN_NOTES) > 0

    def test_phasedown_notes_mention_3_percent_target(self):
        joined = " ".join(MS_HB531_PHASEDOWN_NOTES)
        assert "3.0" in joined or "3%" in joined

    def test_phasedown_notes_mention_2030(self):
        joined = " ".join(MS_HB531_PHASEDOWN_NOTES)
        assert "2030" in joined or "TY2030" in joined


# ---------------------------------------------------------------------------
# compute() — resident scenarios
# ---------------------------------------------------------------------------


class TestMississippiPluginComputeResident:
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
        assert result.state == "MS"
        assert result.residency == ResidencyStatus.RESIDENT
        assert result.days_in_state == 365

    def test_resident_single_65k_tax_lock(
        self, single_65k_return, federal_single_65k
    ):
        """**$65k SINGLE LOCK** — tenforty graph backend, TY2025.

        MS state_total_tax = $2,054.80.

        Hand trace (HB 531 schedule):
            AGI               = $65,000
            - Personal ex     = $6,000
            - Std ded         = $2,300
            MS taxable income = $56,700
            Tax = (56,700 - 10,000) * 0.044
                = 46,700 * 0.044
                = $2,054.80
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        assert state_tax == Decimal("2054.80")

    def test_resident_single_65k_matches_hand_calc(
        self, single_65k_return, federal_single_65k
    ):
        """Cross-check: hand-calc with the published constants must
        match the wrapped value within $5 (for MS the match is exact)."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        # Hand calc
        ti = (
            Decimal("65000")
            - MS_TY2025_EXEMPTION_SINGLE
            - MS_TY2025_STD_DED_SINGLE
        )
        above_zero = max(Decimal("0"), ti - MS_TY2025_ZERO_BRACKET)
        hand_tax = (above_zero * MS_TY2025_FLAT_RATE).quantize(
            Decimal("0.01")
        )
        wrap_tax = result.state_specific["state_total_tax"]
        delta = abs(hand_tax - wrap_tax)
        assert hand_tax == Decimal("2054.80")
        assert delta <= Decimal("5.00"), (
            f"Hand calc {hand_tax} vs wrap {wrap_tax} differ by "
            f"{delta} — exceeds the $5 wrap tolerance."
        )

    def test_state_taxable_income_matches_hand(
        self, single_65k_return, federal_single_65k
    ):
        """Graph backend correctly applies BOTH std ded and personal
        exemption for MS — taxable_income = 65000 - 6000 - 2300 = 56700."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal(
            "56700.00"
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

    def test_state_specific_decimal_fields(
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
            "state_taxable_income",
            "state_total_tax",
            "state_total_tax_resident_basis",
            "state_tax_bracket",
            "state_effective_tax_rate",
            "state_flat_rate",
            "state_zero_bracket",
            "apportionment_fraction",
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

    def test_zero_bracket_flag_set(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["ms_zero_bracket_first_10k"] is True

    def test_phasing_down_flag_set(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["ms_flat_rate_phasing_down"] is True

    def test_resident_basis_equals_apportioned_for_resident(
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
        assert rehydrated.state == "MS"


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year
# ---------------------------------------------------------------------------


class TestMississippiPluginComputeNonresident:
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
        assert full == Decimal("2054.80")
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

    def test_zero_days_yields_zero_apportioned_tax(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=0,
        )
        assert result.state_specific["state_total_tax"] == Decimal("0.00")
        assert (
            result.state_specific["state_total_tax_resident_basis"]
            == Decimal("2054.80")
        )


# ---------------------------------------------------------------------------
# apportion_income()
# ---------------------------------------------------------------------------


class TestMississippiPluginApportionIncome:
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


class TestMississippiPluginFormIds:
    def test_form_ids(self):
        assert "MS Form 80-105" in PLUGIN.form_ids()

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
# v1 limitations visibility
# ---------------------------------------------------------------------------


class TestMississippiV1Limitations:
    def test_limitations_is_tuple(self):
        assert isinstance(MS_V1_LIMITATIONS, tuple)
        assert len(MS_V1_LIMITATIONS) > 0

    def test_limitations_mention_form_80_205(self):
        joined = " ".join(MS_V1_LIMITATIONS)
        assert "80-205" in joined

    def test_limitations_mention_credit_for_other_states(self):
        joined = " ".join(MS_V1_LIMITATIONS).lower()
        assert "other states" in joined or "schedule n" in joined


# ---------------------------------------------------------------------------
# Reciprocity table consistency
# ---------------------------------------------------------------------------


def test_reciprocity_matches_json():
    table = ReciprocityTable.load()
    ms_partners = table.partners_of("MS")
    assert ms_partners == frozenset()
    assert frozenset(PLUGIN.meta.reciprocity_partners) == ms_partners


# ---------------------------------------------------------------------------
# GRAPH BACKEND LOCK — gatekeeper
# ---------------------------------------------------------------------------


class TestGraphBackendLockForMS:
    """Wave-5 graph-backend wrap-correctness lock for Mississippi.

    Mirrors the WI lock pattern. Re-runs the graph-backend probe at
    test time and asserts the canonical $2,054.80 number.
    """

    def test_graph_backend_returns_2054_80(self):
        result = tenforty.evaluate_return(
            year=2025,
            state="MS",
            filing_status="Single",
            w2_income=65000,
            standard_or_itemized="Standard",
            backend="graph",
        )
        assert Decimal(str(result.state_total_tax)).quantize(
            Decimal("0.01")
        ) == Decimal("2054.80")

    def test_graph_backend_taxable_income_is_56700(self):
        """Confirms graph backend applies both std ded and personal
        exemption for MS (unlike OK)."""
        result = tenforty.evaluate_return(
            year=2025,
            state="MS",
            filing_status="Single",
            w2_income=65000,
            standard_or_itemized="Standard",
            backend="graph",
        )
        assert Decimal(str(result.state_taxable_income)).quantize(
            Decimal("0.01")
        ) == Decimal("56700.00")

    def test_default_backend_still_raises_value_error(self):
        """Confirm OTS backend still does NOT support MS_80105."""
        with pytest.raises(ValueError, match="OTS does not support"):
            tenforty.evaluate_return(
                year=2025,
                state="MS",
                filing_status="Single",
                w2_income=65000,
                standard_or_itemized="Standard",
            )

    def test_plugin_wrap_matches_direct_graph_probe(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        direct = tenforty.evaluate_return(
            year=2025,
            state="MS",
            filing_status="Single",
            w2_income=65000,
            standard_or_itemized="Standard",
            backend="graph",
        )
        assert result.state_specific["state_total_tax"] == Decimal(
            str(direct.state_total_tax)
        ).quantize(Decimal("0.01"))
