"""Vermont state plugin tests.

Mirrors the WI plugin test suite — VT is the second wave-5 graph-backend
wrap (after WI in wave 4). VT is wired up in tenforty only via the newer
graph backend (``vt_in111_2025.json`` ships but the OTS backend raises
``ValueError: OTS does not support 2025/VT_IN111``), so the VT plugin
passes ``backend='graph'`` to ``tenforty.evaluate_return``.

Reference scenario (verified 2026-04-11 via direct tenforty probe,
graph backend, 2025):

    Single / $65,000 W-2 / Standard
      VT AGI                            $65,000.00
      VT Standard Deduction              -$7,400.00
      VT Taxable Income                  $57,600.00
      Tax (3.35% / 6.6% schedule):
          3.35% on first $47,900         $1,604.65
          6.6% on $9,700                  $640.20
          Total                          $2,244.85

Vermont folded its personal exemption into the standard deduction in
Act 65 of 2023, so unlike ME / RI / WV there is no separate "personal
exemption" gap in the graph-backend flow — the $2,244.85 result reconciles
against a hand calc using the published TY2025 brackets directly.

Reciprocity: Vermont has NO bilateral reciprocity agreements — verified
against skill/reference/state-reciprocity.json.
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
from skill.scripts.states.vt import PLUGIN, VermontPlugin


# ---------------------------------------------------------------------------
# Shared fixtures
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
            street1="133 State St", city="Montpelier", state="VT", zip="05633"
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


class TestVermontPluginMeta:
    def test_meta_code(self):
        assert PLUGIN.meta.code == "VT"

    def test_meta_name(self):
        assert PLUGIN.meta.name == "Vermont"

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
        """Vermont has NO bilateral reciprocity agreements."""
        assert PLUGIN.meta.reciprocity_partners == ()
        assert len(PLUGIN.meta.reciprocity_partners) == 0

    def test_plugin_is_state_plugin_protocol(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_vermont_plugin_instance(self):
        assert isinstance(PLUGIN, VermontPlugin)

    def test_meta_dor_url_is_tax_vermont_gov(self):
        assert "tax.vermont.gov" in PLUGIN.meta.dor_url

    def test_meta_free_efile_url_is_myvtax(self):
        assert PLUGIN.meta.free_efile_url is not None
        assert "myvtax" in PLUGIN.meta.free_efile_url.lower()

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_bracket_rates(self):
        notes = PLUGIN.meta.notes
        assert "3.35" in notes
        assert "6.6" in notes
        assert "7.6" in notes
        assert "8.75" in notes

    def test_meta_notes_mention_graph_backend(self):
        notes = PLUGIN.meta.notes.lower()
        assert "tenforty" in notes
        assert "graph" in notes

    def test_meta_notes_mention_act_65(self):
        notes = PLUGIN.meta.notes.lower()
        assert "act 65" in notes

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "NH"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# No-reciprocity verification
# ---------------------------------------------------------------------------


class TestVermontNoReciprocity:
    def test_no_reciprocity_via_reciprocity_table(self):
        table = ReciprocityTable.load()
        assert table.partners_of("VT") == frozenset()

    def test_not_reciprocal_with_neighbors(self):
        """Vermont borders NH (no income tax), MA, and NY."""
        table = ReciprocityTable.load()
        for other in ("NH", "MA", "NY", "ME", "CT", "RI"):
            assert table.are_reciprocal("VT", other) is False

    def test_meta_partners_match_reciprocity_table(self):
        table = ReciprocityTable.load()
        assert (
            frozenset(PLUGIN.meta.reciprocity_partners)
            == table.partners_of("VT")
        )


# ---------------------------------------------------------------------------
# compute() — resident
# ---------------------------------------------------------------------------


class TestVermontPluginComputeResident:
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

    def test_state_code_is_vt(self, single_65k_return, federal_single_65k):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state == "VT"

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

    def test_resident_65k_single_lock(
        self, single_65k_return, federal_single_65k
    ):
        """**$65k Single VT resident WRAP-CORRECTNESS LOCK.**

        VT graph backend output:
            VT AGI                $65,000.00
            VT Std Ded             -$7,400.00
            VT Taxable Income      $57,600.00
            VT Tax (3.35% / 6.6%)   $2,244.85

        Pin the plugin's result bit-for-bit against an independent direct
        tenforty call so OpenTaxSolver schedule drift fails this test."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        assert state_tax == Decimal("2244.85")

        # Cross-check: direct tenforty probe (graph backend) must agree
        # bit-for-bit with the plugin's wrapped result.
        direct = tenforty.evaluate_return(
            year=2025,
            state="VT",
            filing_status="Single",
            w2_income=65000,
            standard_or_itemized="Standard",
            backend="graph",
        )
        assert Decimal(str(direct.state_total_tax)).quantize(
            Decimal("0.01")
        ) == Decimal("2244.85")

    def test_state_taxable_income_57600(
        self, single_65k_return, federal_single_65k
    ):
        """VT TI = AGI - $7,400 std ded = $57,600.

        Note: Unlike WI's graph backend (which echoes AGI), VT's graph
        backend correctly subtracts the std ded on the output side."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal(
            "57600.00"
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
            "state_tax_bracket",
            "state_effective_tax_rate",
            "apportionment_fraction",
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
        assert rehydrated.state == "VT"
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
            "tenforty_supports_vt_default_backend"
        ] is False
        assert result.state_specific["tenforty_supports_vt_graph_backend"] is True
        note = result.state_specific["tenforty_status_note"]
        assert "act 65" in note.lower()


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment
# ---------------------------------------------------------------------------


class TestVermontPluginComputeNonresident:
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
        assert full == Decimal("2244.85")
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
        assert result.state_specific[
            "state_total_tax_resident_basis"
        ] == Decimal("2244.85")

    def test_full_year_nonresident_equals_resident_tax(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_total_tax"] == Decimal("2244.85")


# ---------------------------------------------------------------------------
# apportion_income()
# ---------------------------------------------------------------------------


class TestVermontPluginApportionIncome:
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


class TestVermontPluginFormIds:
    def test_form_ids(self):
        form_ids = PLUGIN.form_ids()
        assert "VT Form IN-111" in form_ids
        assert form_ids == ["VT Form IN-111"]

    def test_render_pdfs_produces_pdf(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """VT Form IN-111 AcroForm fill produces a non-empty PDF."""
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
        assert paths[0].name == "vt_in111.pdf"

    def test_render_pdfs_field_values(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Verify that rendered VT IN-111 PDF contains correct field values."""
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

        # state_total_tax = $2,244.85 -> widget "Line7"
        assert fields["Line7"].get("/V") == "2244.85"
        # state_taxable_income = $57,600 -> widget "Line6"
        assert fields["Line6"].get("/V") == "57600.00"
        # state_adjusted_gross_income = $65,000 -> widget "Line1"
        assert fields["Line1"].get("/V") == "65000.00"


# ---------------------------------------------------------------------------
# Gatekeeper test — tenforty default-backend gap
# ---------------------------------------------------------------------------


class TestVermontTenfortyGap:
    """When this test starts failing, tenforty has added VT to the OTS
    default backend. Rewrite the plugin as a default-backend wrapper
    (mirror nc.py / oh.py) and delete this test."""

    def test_default_backend_still_raises(self):
        with pytest.raises(ValueError, match="OTS does not support"):
            tenforty.evaluate_return(
                year=2025,
                state="VT",
                filing_status="Single",
                w2_income=65_000,
                num_dependents=0,
                standard_or_itemized="Standard",
                itemized_deductions=0,
            )

    def test_graph_backend_pinned_at_known_value(self):
        """Pin the graph backend's $65k Single value so any tenforty
        drift trips CI."""
        result = tenforty.evaluate_return(
            year=2025,
            state="VT",
            filing_status="Single",
            w2_income=65_000,
            num_dependents=0,
            standard_or_itemized="Standard",
            itemized_deductions=0,
            backend="graph",
        )
        assert Decimal(str(result.state_total_tax)).quantize(
            Decimal("0.01")
        ) == Decimal("2244.85")

    def test_graph_backend_correctness_against_hand_calc(self):
        """Cross-check the graph-backend tax against a hand calc using
        the published TY2025 VT bracket schedule.

        Hand calc:
            VT Single Std Ded:           $7,400
            VT Taxable Income:           $57,600
            3.35% on first $47,900:     $1,604.65
            6.6% on remaining $9,700:    $640.20
            Total:                       $2,244.85

        Vermont folded its personal exemption into the std ded in Act 65
        of 2023, so this number reconciles directly with the published
        VT bracket constants — no missing personal-exemption gap."""
        ti = Decimal("65000") - Decimal("7400")  # = 57,600
        tier1 = Decimal("47900") * Decimal("0.0335")
        tier2 = (ti - Decimal("47900")) * Decimal("0.066")
        hand_tax = (tier1 + tier2).quantize(Decimal("0.01"))
        assert hand_tax == Decimal("2244.85")
