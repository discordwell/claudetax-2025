"""Louisiana state plugin tests — TY2025.

Mirrors the wave-4 ``test_state_wi.py`` graph-wrap pattern. LA is wired
into tenforty only via the graph backend; the OTS backend raises
``ValueError: OTS does not support 2025/LA_IT540``. The plugin passes
``backend='graph'`` to ``tenforty.evaluate_return``. This test file
pins that backend choice and locks the $65k Single tax number.

Reference scenario (verified 2026-04-11 via direct tenforty probe,
graph backend, 2025):
    Single / $65,000 W-2 / Standard
        -> state_total_tax              = 1575.00
           state_taxable_income         = 0.00 (graph leaves blank)
           state_adjusted_gross_income  = 0.00 (graph leaves blank)
           state_tax_bracket            = 0.0  (graph omits)
           state_effective_tax_rate     = 0.0  (graph omits)

Hand verification ($65k Single, TY2025) using LA HB 10:
    LA AGI                = $65,000
    LA standard deduction = $12,500   (HB 10 raised from $4,500)
    LA taxable income     = $52,500
    LA tax = 52,500 * 0.03 = $1,575.00  (HB 10 flat 3.0%)

The graph backend matches the DOR primary source bit-for-bit for LA at
TY2025, so we wrap (WI pattern), not hand-roll.

LOUDLY FLAGGED RECENT LAW CHANGE: LA HB 10 (2024 Special Session)
repealed graduated brackets and the federal-tax deduction, eliminated
personal exemptions, and raised the standard deduction. This took
effect 1/1/2025. The plugin docstring documents this in detail.

Reciprocity: LA has NO bilateral reciprocity agreements (verified
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
from skill.scripts.states.la import (
    LA_HB10_PHASEOUT_NOTES,
    LA_TY2025_FLAT_RATE,
    LA_TY2025_STD_DED_MFJ,
    LA_TY2025_STD_DED_SINGLE,
    LA_V1_LIMITATIONS,
    LouisianaPlugin,
    PLUGIN,
)


# ---------------------------------------------------------------------------
# Shared fixtures — Single $65k W-2 LA resident
# ---------------------------------------------------------------------------


@pytest.fixture
def single_65k_return() -> CanonicalReturn:
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=Person(
            first_name="Marie",
            last_name="Boudreaux",
            ssn="111-22-3333",
            date_of_birth=dt.date(1990, 1, 1),
        ),
        address=Address(
            street1="617 N 3rd St",
            city="Baton Rouge",
            state="LA",
            zip="70802",
        ),
        w2s=[
            W2(
                employer_name="Bayou Industries",
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


class TestLouisianaPluginMeta:
    def test_meta_fields(self):
        """Core metadata: code, name, starting point, channel."""
        assert PLUGIN.meta.code == "LA"
        assert PLUGIN.meta.name == "Louisiana"
        assert PLUGIN.meta.has_income_tax is True
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )

    def test_plugin_is_state_plugin_protocol(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_louisiana_plugin_instance(self):
        assert isinstance(PLUGIN, LouisianaPlugin)

    def test_meta_dor_url_is_revenue_louisiana_gov(self):
        assert "revenue.louisiana.gov" in PLUGIN.meta.dor_url

    def test_meta_free_efile_url_present_and_latap(self):
        assert PLUGIN.meta.free_efile_url is not None
        assert "latap.revenue.louisiana.gov" in PLUGIN.meta.free_efile_url

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_hb10(self):
        """LA notes must call out HB 10 (the load-bearing law change)."""
        assert "HB 10" in PLUGIN.meta.notes

    def test_meta_notes_mention_flat_rate(self):
        """LA notes must call out the flat 3.00% rate."""
        notes = PLUGIN.meta.notes
        assert "3.00" in notes or "flat" in notes.lower()

    def test_meta_notes_mention_standard_deduction(self):
        """LA notes must call out the new $12,500 / $25,000 std ded."""
        notes = PLUGIN.meta.notes
        assert "12,500" in notes or "12500" in notes
        assert "25,000" in notes or "25000" in notes

    def test_meta_notes_mention_no_reciprocity(self):
        """LA has no reciprocity — call it out."""
        assert "reciprocity" in PLUGIN.meta.notes.lower()

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "MS"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Reciprocity invariants
# ---------------------------------------------------------------------------


class TestLouisianaNoReciprocity:
    """LA has zero bilateral reciprocity agreements."""

    def test_no_reciprocity_partners(self):
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_no_reciprocity_via_reciprocity_table(self):
        """ReciprocityTable.partners_of('LA') must be empty."""
        table = ReciprocityTable.load()
        assert table.partners_of("LA") == frozenset()

    def test_not_reciprocal_with_neighbors(self):
        """Sanity check that LA's neighbors (TX/AR/MS) are NOT
        reciprocal — TX has no income tax (so the question is moot
        but worth pinning), and AR/MS have no agreement with LA."""
        table = ReciprocityTable.load()
        for neighbor in ("TX", "AR", "MS", "AL"):
            assert table.are_reciprocal("LA", neighbor) is False


# ---------------------------------------------------------------------------
# TY2025 constants — pin the law in tests so drift fails CI
# ---------------------------------------------------------------------------


class TestLouisianaTY2025Constants:
    def test_flat_rate_is_3_percent(self):
        """LA HB 10 flat rate = 3.00% effective TY2025."""
        assert LA_TY2025_FLAT_RATE == Decimal("0.03")

    def test_std_ded_single_is_12500(self):
        assert LA_TY2025_STD_DED_SINGLE == Decimal("12500")

    def test_std_ded_mfj_is_25000(self):
        assert LA_TY2025_STD_DED_MFJ == Decimal("25000")

    def test_hb10_notes_mention_personal_exemption_repealed(self):
        joined = " ".join(LA_HB10_PHASEOUT_NOTES)
        assert "personal exemption" in joined.lower()

    def test_hb10_notes_mention_federal_tax_deduction_repealed(self):
        joined = " ".join(LA_HB10_PHASEOUT_NOTES).lower()
        assert "federal" in joined and ("eliminat" in joined or "repeal" in joined)

    def test_hb10_notes_is_tuple_not_list(self):
        """Frozen-as-tuple so plugins can't accidentally mutate at runtime."""
        assert isinstance(LA_HB10_PHASEOUT_NOTES, tuple)


# ---------------------------------------------------------------------------
# compute() — resident scenarios
# ---------------------------------------------------------------------------


class TestLouisianaPluginComputeResident:
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
        assert result.state == "LA"
        assert result.residency == ResidencyStatus.RESIDENT
        assert result.days_in_state == 365

    def test_resident_single_65k_tax_lock(
        self, single_65k_return, federal_single_65k
    ):
        """**$65k SINGLE LOCK** — tenforty graph backend, TY2025.

        LA state_total_tax = $1,575.00.

        Hand trace (LA HB 10 flat 3% schedule):
            AGI               = $65,000
            - Std ded Single  = $12,500   (HB 10)
            LA taxable income = $52,500
            Tax = 52,500 * 0.03 = $1,575.00

        This matches the graph-backend probe value bit-for-bit. Drift
        in either tenforty's LA graph definition OR a future change to
        LA law that breaks this calculation will fail this test.
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        assert state_tax == Decimal("1575.00")

    def test_resident_single_65k_matches_hand_calc(
        self, single_65k_return, federal_single_65k
    ):
        """Cross-check: hand-calc with the published constants must
        match the wrapped value within $5 (the gap-doc rubric tolerance).
        For LA at $65k Single the match is exact."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        # Hand calc
        ti = Decimal("65000") - LA_TY2025_STD_DED_SINGLE
        hand_tax = (ti * LA_TY2025_FLAT_RATE).quantize(Decimal("0.01"))
        wrap_tax = result.state_specific["state_total_tax"]
        delta = abs(hand_tax - wrap_tax)
        assert hand_tax == Decimal("1575.00")
        assert delta <= Decimal("5.00"), (
            f"Hand calc {hand_tax} vs wrap {wrap_tax} differ by "
            f"{delta} — exceeds the $5 wrap tolerance."
        )

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

    def test_personal_exemption_repealed_flag(
        self, single_65k_return, federal_single_65k
    ):
        """LA HB 10 ELIMINATED personal/dependent exemptions. Pin it."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["la_personal_exemption_repealed"] is True

    def test_federal_tax_deduction_repealed_flag(
        self, single_65k_return, federal_single_65k
    ):
        """LA HB 10 ELIMINATED the federal income tax deduction."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert (
            result.state_specific["la_federal_tax_deduction_repealed"]
            is True
        )

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
        assert rehydrated.state == "LA"
        assert rehydrated.residency == ResidencyStatus.RESIDENT


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment
# ---------------------------------------------------------------------------


class TestLouisianaPluginComputeNonresident:
    def test_nonresident_half_year_prorates_tax(
        self, single_65k_return, federal_single_65k
    ):
        """NONRESIDENT with days_in_state=182 -> 182/365 of resident tax."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=182,
        )
        full = result.state_specific["state_total_tax_resident_basis"]
        apportioned = result.state_specific["state_total_tax"]
        assert full == Decimal("1575.00")
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
        # Resident-basis tax is unchanged.
        assert (
            result.state_specific["state_total_tax_resident_basis"]
            == Decimal("1575.00")
        )


# ---------------------------------------------------------------------------
# apportion_income()
# ---------------------------------------------------------------------------


class TestLouisianaPluginApportionIncome:
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


class TestLouisianaPluginFormIds:
    def test_form_ids(self):
        form_ids = PLUGIN.form_ids()
        assert "LA Form IT-540" in form_ids

    def test_render_pdfs_produces_pdf(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """LA Form IT-540 AcroForm fill produces a non-empty PDF."""
        try:
            from pypdf import PdfReader
        except BaseException:
            pytest.skip("pypdf unavailable")

        state_return = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        paths = PLUGIN.render_pdfs(state_return, tmp_path)
        assert len(paths) == 1
        assert paths[0].exists()
        assert paths[0].stat().st_size > 0
        assert paths[0].name == "la_it540.pdf"

    def test_render_pdfs_field_values(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Verify that rendered LA IT-540 PDF contains correct field values."""
        try:
            from pypdf import PdfReader
        except BaseException:
            pytest.skip("pypdf unavailable")

        state_return = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        paths = PLUGIN.render_pdfs(state_return, tmp_path)
        reader = PdfReader(str(paths[0]))
        fields = reader.get_fields()
        assert fields is not None

        # state_total_tax = 1575.00 for $65k Single
        tax_field = fields.get("LATAX")
        assert tax_field is not None
        assert tax_field.get("/V") == "1575.00"


# ---------------------------------------------------------------------------
# v1 limitations visibility
# ---------------------------------------------------------------------------


class TestLouisianaV1Limitations:
    def test_limitations_is_tuple(self):
        assert isinstance(LA_V1_LIMITATIONS, tuple)
        assert len(LA_V1_LIMITATIONS) > 0

    def test_limitations_mention_form_it540b(self):
        joined = " ".join(LA_V1_LIMITATIONS)
        assert "IT-540B" in joined or "540B" in joined

    def test_limitations_mention_credit_for_other_states(self):
        joined = " ".join(LA_V1_LIMITATIONS).lower()
        assert "schedule g" in joined or "other states" in joined


# ---------------------------------------------------------------------------
# Reciprocity table consistency
# ---------------------------------------------------------------------------


def test_reciprocity_matches_json():
    """ReciprocityTable.load().partners_of('LA') == empty set, and the
    plugin must agree."""
    table = ReciprocityTable.load()
    la_partners = table.partners_of("LA")
    assert la_partners == frozenset()
    assert frozenset(PLUGIN.meta.reciprocity_partners) == la_partners


# ---------------------------------------------------------------------------
# GRAPH BACKEND LOCK — gatekeeper test for the wrap decision
# ---------------------------------------------------------------------------


class TestGraphBackendLockForLA:
    """Wave-5 graph-backend wrap-correctness lock for Louisiana.

    Mirrors the WI lock pattern. Re-runs the graph-backend probe at
    test time and asserts the canonical $1,575.00 number. If tenforty
    ever changes the LA graph definition, this test fails and forces
    a deliberate plugin update.
    """

    def test_graph_backend_returns_1575(self):
        result = tenforty.evaluate_return(
            year=2025,
            state="LA",
            filing_status="Single",
            w2_income=65000,
            standard_or_itemized="Standard",
            backend="graph",
        )
        assert Decimal(str(result.state_total_tax)).quantize(
            Decimal("0.01")
        ) == Decimal("1575.00")

    def test_default_backend_still_raises_value_error(self):
        """Confirm the default OTS backend still does NOT support
        2025/LA_IT540. If this starts failing, the OTS backend has
        gained LA support and we can simplify the plugin to drop the
        backend='graph' kwarg (verify the result first)."""
        with pytest.raises(ValueError, match="OTS does not support"):
            tenforty.evaluate_return(
                year=2025,
                state="LA",
                filing_status="Single",
                w2_income=65000,
                standard_or_itemized="Standard",
            )

    def test_plugin_wrap_matches_direct_graph_probe(
        self, single_65k_return, federal_single_65k
    ):
        """Plugin wrap must equal a direct graph-backend probe — the
        whole point of the wrap pattern."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        direct = tenforty.evaluate_return(
            year=2025,
            state="LA",
            filing_status="Single",
            w2_income=65000,
            standard_or_itemized="Standard",
            backend="graph",
        )
        assert result.state_specific["state_total_tax"] == Decimal(
            str(direct.state_total_tax)
        ).quantize(Decimal("0.01"))
