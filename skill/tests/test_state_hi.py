"""Hawaii state plugin tests.

Mirrors the WI graph-backend wrapper test pattern. HI is wired up in
tenforty only via the graph backend (the OTS backend raises
``ValueError: OTS does not support 2025/HI_N11``), so the HI plugin
passes ``backend='graph'`` to tenforty.evaluate_return. This file pins
that backend choice and locks the $65k Single tax number against an
independent direct probe.

Reference scenario (verified 2026-04-11 via direct tenforty probe,
graph backend, 2025):
    Single / $65,000 W-2 / Standard
      -> state_total_tax            = 3496.80
         state_adjusted_gross_income = 65000.00
         state_taxable_income        = 60600.00
         state_tax_bracket           = 0.0
         state_effective_tax_rate    = 0.0

Hawaii Tax Rate Schedule I (Single / MFS, TY2025):
    - 1.40% up to $9,600
    - 3.20% to $14,400
    - 5.50% to $19,200
    - 6.40% to $24,000
    - 6.80% to $36,000
    - 7.20% to $48,000
    - 7.60% to $125,000
    - 7.90%, 8.25%, 9.00%, 10.00%, 11.00% above

Hand calc cross-check at $60,600 TI (Single):
    Tax = $2,539 + 7.60% × ($60,600 - $48,000)
        = $2,539 + $957.60
        = $3,496.60   (rate schedule)
    Graph backend: $3,496.80 (within $0.20)
    Tax table row 60,600-60,650 prints $3,496 (whole dollars)

Standard deduction Single (TY2025) = $4,400 (Act 46, 2024 — stepped
increase from prior $2,200 toward $12,000 by TY2031).

Reciprocity: HI has **no** bilateral reciprocity agreements.
Geographic isolation makes the agreements irrelevant. Verified
absent from skill/reference/state-reciprocity.json.
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
from skill.scripts.states.hi import PLUGIN, HawaiiPlugin


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
            last_name="Aloha",
            ssn="111-22-3333",
            date_of_birth=dt.date(1990, 1, 1),
        ),
        address=Address(
            street1="830 Punchbowl St",
            city="Honolulu",
            state="HI",
            zip="96813",
        ),
        w2s=[
            W2(
                employer_name="Pacific Corp",
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


class TestHawaiiPluginMeta:
    def test_meta_code(self):
        assert PLUGIN.meta.code == "HI"

    def test_meta_name(self):
        assert PLUGIN.meta.name == "Hawaii"

    def test_has_income_tax(self):
        assert PLUGIN.meta.has_income_tax is True

    def test_starting_point_is_federal_agi(self):
        # HI Form N-11 line 7 imports federal AGI; HI std ded is
        # subtracted on a separate line.
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI

    def test_submission_channel_is_state_dor_free_portal(self):
        # Hawaii Tax Online (HTO) is the free direct portal.
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )

    def test_dor_url(self):
        assert "tax.hawaii.gov" in PLUGIN.meta.dor_url

    def test_free_efile_url_is_hawaii_tax_online(self):
        assert PLUGIN.meta.free_efile_url is not None
        assert "hitax.hawaii.gov" in PLUGIN.meta.free_efile_url

    def test_supports_ty2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_no_reciprocity_partners(self):
        # Hawaii has no bilateral reciprocity agreements (geographic
        # isolation).
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_meta_notes_mention_graph_backend(self):
        # Graph-backend caveat is critical — must be documented in
        # notes for humans reading the registry.
        assert "graph" in PLUGIN.meta.notes.lower()

    def test_meta_notes_mention_n11(self):
        assert "N-11" in PLUGIN.meta.notes

    def test_meta_notes_mention_top_rate(self):
        # 11.00% top marginal rate is load-bearing for the human
        # reader.
        assert "11.00%" in PLUGIN.meta.notes or "11%" in PLUGIN.meta.notes

    def test_plugin_is_state_plugin_protocol(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_hawaii_plugin_instance(self):
        assert isinstance(PLUGIN, HawaiiPlugin)

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "CA"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# compute() — resident case matches tenforty reference numbers
# ---------------------------------------------------------------------------


class TestHawaiiPluginComputeResident:
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

    def test_state_code_is_hi(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state == "HI"

    def test_dollar_lock_single_65k(
        self, single_65k_return, federal_single_65k
    ):
        """DOLLAR LOCK: Single / $65k W-2 / Standard
        -> HI state_total_tax = $3,496.80 (tenforty graph backend, 2025).

        Hand calc against HI Rate Schedule I:
            Tax = $2,539 + 7.60% × ($60,600 - $48,000) = $3,496.60
        Graph backend: $3,496.80 (delta $0.20, within ±$5 wrap
        tolerance from skill/reference/tenforty-ty2025-gap.md rubric).

        This is the load-bearing regression guard for the HI plugin.
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        assert state_tax == Decimal("3496.80")

    def test_state_taxable_income_matches_tenforty(
        self, single_65k_return, federal_single_65k
    ):
        """Graph backend reports HI taxable income = $60,600 for
        $65k Single (= $65k AGI - $4,400 HI std ded). Pin so any
        upstream change to HI std ded handling trips CI."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal(
            "60600.00"
        )

    def test_state_agi_matches_federal_agi(
        self, single_65k_return, federal_single_65k
    ):
        """HI starting point is federal AGI (Form N-11 line 7)."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific[
            "state_adjusted_gross_income"
        ] == Decimal("65000.00")

    def test_state_tax_bracket_graph_backend_zero(
        self, single_65k_return, federal_single_65k
    ):
        """Graph backend doesn't expose marginal rate for HI; pinned
        at 0.0 so any upstream fix is caught."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        bracket = result.state_specific["state_tax_bracket"]
        assert isinstance(bracket, Decimal)
        assert bracket == Decimal("0.0")

    def test_state_effective_tax_rate_graph_backend_zero(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        eff = result.state_specific["state_effective_tax_rate"]
        assert isinstance(eff, Decimal)
        assert eff == Decimal("0.0")

    def test_state_specific_all_decimal_fields(
        self, single_65k_return, federal_single_65k
    ):
        """Every numeric value in state_specific must be Decimal."""
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
        assert result.state_specific["apportionment_fraction"] == Decimal(
            "1"
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

    def test_starting_point_marker(
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
        """Round-trip through Pydantic JSON to confirm contract."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        dumped = result.model_dump(mode="json")
        rehydrated = StateReturn.model_validate(dumped)
        assert rehydrated.state == "HI"
        assert rehydrated.residency == ResidencyStatus.RESIDENT


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment
# ---------------------------------------------------------------------------


class TestHawaiiPluginComputeNonresident:
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
        assert full == Decimal("3496.80")
        assert apportioned < full
        # day_prorate uses Decimal(days)/Decimal(365) and rounds to
        # cents half-up.
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
        assert result.state_specific[
            "state_total_tax_resident_basis"
        ] == Decimal("3496.80")

    def test_full_year_nonresident_equals_resident_tax(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_total_tax"] == Decimal(
            "3496.80"
        )

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


# ---------------------------------------------------------------------------
# apportion_income()
# ---------------------------------------------------------------------------


class TestHawaiiPluginApportionIncome:
    def test_apportion_income_resident_full_amounts(
        self, single_65k_return
    ):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert isinstance(app, IncomeApportionment)
        assert app.state_source_wages == Decimal("65000.00")
        assert app.state_source_total == Decimal("65000.00")

    def test_apportion_income_nonresident_prorates(
        self, single_65k_return
    ):
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


class TestHawaiiPluginFormIds:
    def test_form_ids(self):
        assert PLUGIN.form_ids() == ["HI Form N-11"]

    def test_render_pdfs_produces_pdf(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """HI Form N-11 AcroForm fill produces a non-empty PDF."""
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
        assert paths[0].name == "HI_N11.pdf"

    def test_render_pdfs_field_values(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Verify that rendered HI N-11 PDF contains correct field values."""
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

        # Widget "7" maps to state_adjusted_gross_income (N-11 line 7)
        assert fields["7"].get("/V") == "65000.00"
        # Widget "27" maps to state_taxable_income (N-11 line 27)
        assert fields["27"].get("/V") == "60600.00"
        # Widget "28" maps to state_total_tax (N-11 line 28)
        assert fields["28"].get("/V") == "3496.80"


# ---------------------------------------------------------------------------
# ReciprocityTable consistency
# ---------------------------------------------------------------------------


class TestHawaiiReciprocityConsistency:
    def test_reciprocity_table_lookup_matches_plugin(self):
        table = ReciprocityTable.load()
        hi_partners_from_table = table.partners_of("HI")
        assert hi_partners_from_table == frozenset()
        assert (
            frozenset(PLUGIN.meta.reciprocity_partners)
            == hi_partners_from_table
        )

    def test_no_reciprocity_with_any_state(self):
        """Spot-check: HI must not be reciprocal with any common
        candidate (CA, neighbors, etc.)."""
        table = ReciprocityTable.load()
        for other in ("CA", "WA", "NY", "FL", "TX", "OR", "NV"):
            assert table.are_reciprocal("HI", other) is False


# ---------------------------------------------------------------------------
# Graph-backend lock — pin tenforty's graph backend bit-for-bit so any
# upstream OpenTaxSolver schedule drift trips CI.
# ---------------------------------------------------------------------------


class TestGraphBackendLockForHI:
    """When ANY of these tests STARTS FAILING, tenforty's graph
    backend has changed its TY2025 Hawaii output. Investigate
    the upstream change against HI DOR Form N-11 instructions
    before updating the lock — the change may be a real DOR
    schedule update, OR may be an upstream regression.

    HI is a graph-backend WRAPPER (not hand-rolled), per the
    decision rubric in skill/reference/tenforty-ty2025-gap.md:
    hand calc $3,496.60 vs graph backend $3,496.80 → delta
    $0.20, within the ±$5 wrap tolerance.
    """

    def test_default_backend_still_raises(self):
        """The OTS default backend MUST still raise — if it stops,
        HI may have been promoted to the OTS path and the plugin
        should be re-evaluated as a default-backend wrap."""
        with pytest.raises(ValueError, match="OTS does not support"):
            tenforty.evaluate_return(
                year=2025,
                state="HI",
                filing_status="Single",
                w2_income=65_000,
                num_dependents=0,
                standard_or_itemized="Standard",
                itemized_deductions=0,
            )

    def test_graph_backend_returns_3496_80(self):
        result = tenforty.evaluate_return(
            year=2025,
            state="HI",
            filing_status="Single",
            w2_income=65_000,
            num_dependents=0,
            standard_or_itemized="Standard",
            itemized_deductions=0,
            backend="graph",
        )
        # Pin to the cent. Use Decimal(str(...)) to avoid float
        # binary noise in the comparison.
        assert Decimal(str(result.state_total_tax)).quantize(
            Decimal("0.01")
        ) == Decimal("3496.80")

    def test_graph_backend_taxable_income_60600(self):
        result = tenforty.evaluate_return(
            year=2025,
            state="HI",
            filing_status="Single",
            w2_income=65_000,
            num_dependents=0,
            standard_or_itemized="Standard",
            itemized_deductions=0,
            backend="graph",
        )
        assert Decimal(str(result.state_taxable_income)).quantize(
            Decimal("0.01")
        ) == Decimal("60600.00")

    def test_graph_backend_agi_65000(self):
        result = tenforty.evaluate_return(
            year=2025,
            state="HI",
            filing_status="Single",
            w2_income=65_000,
            num_dependents=0,
            standard_or_itemized="Standard",
            itemized_deductions=0,
            backend="graph",
        )
        assert Decimal(
            str(result.state_adjusted_gross_income)
        ).quantize(Decimal("0.01")) == Decimal("65000.00")

    def test_hand_calc_within_5_dollars_of_graph(self):
        """Independent rate-schedule hand calc must agree with the
        graph backend within $5 (the wrap tolerance from the rubric).

        Hand calc: $2,539 + 7.60% × ($60,600 - $48,000) = $3,496.60
        Graph    : $3,496.80
        Delta    : $0.20  (well within ±$5)
        """
        hand_calc = Decimal("2539") + (
            Decimal("0.076") * (Decimal("60600") - Decimal("48000"))
        )
        assert hand_calc == Decimal("3496.60")
        graph = Decimal("3496.80")
        delta = abs(graph - hand_calc)
        assert delta <= Decimal("5.00")


# ---------------------------------------------------------------------------
# Hand-calc cross-checks at additional taxable-income points
# ---------------------------------------------------------------------------


class TestHawaiiHandCalcCrossChecks:
    """Standalone arithmetic checks of the HI Schedule I rate
    schedule, independent of tenforty. Documents the bracket math
    that the graph-backend wrap is supposed to be implementing.
    """

    def test_rate_schedule_at_top_of_4th_bracket(self):
        # $24,000 boundary: tax should be exactly $859 (the printed
        # cumulative tax through bracket 4).
        # Tax(24000) = $552 + 6.40% × ($24,000 - $19,200)
        #            = $552 + 6.40% × $4,800
        #            = $552 + $307.20
        #            = $859.20
        # Schedule prints $859 at the boundary (rounded).
        tax = Decimal("552") + (
            Decimal("0.064") * (Decimal("24000") - Decimal("19200"))
        )
        assert tax == Decimal("859.20")

    def test_rate_schedule_at_top_of_5th_bracket(self):
        # $36,000 boundary: tax should be $1,675 (printed cumulative).
        # Tax(36000) = $859 + 6.80% × ($36,000 - $24,000)
        #            = $859 + 6.80% × $12,000
        #            = $859 + $816.00
        #            = $1,675.00
        tax = Decimal("859") + (
            Decimal("0.068") * (Decimal("36000") - Decimal("24000"))
        )
        assert tax == Decimal("1675.00")

    def test_rate_schedule_at_top_of_7th_bracket(self):
        # $125,000 boundary: tax should be $8,391 (printed
        # cumulative). Tax(125000) = $2,539 + 7.60% × ($125,000 -
        # $48,000) = $2,539 + 7.60% × $77,000 = $2,539 + $5,852 =
        # $8,391.
        tax = Decimal("2539") + (
            Decimal("0.076") * (Decimal("125000") - Decimal("48000"))
        )
        assert tax == Decimal("8391.00")
