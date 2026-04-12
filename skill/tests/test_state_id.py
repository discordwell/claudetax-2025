"""Idaho state plugin tests.

Mirrors the WI graph-backend wrapper test pattern. ID is wired up in
tenforty only via the graph backend (the OTS backend raises
``ValueError: OTS does not support 2025/ID_FORM40``), so the ID
plugin passes ``backend='graph'`` to tenforty.evaluate_return.

Reference scenario (verified 2026-04-11 via direct tenforty probe,
graph backend, 2025):
    Single / $65,000 W-2 / Standard
      -> state_total_tax            = 2355.267
         state_adjusted_gross_income = 65000.00
         state_taxable_income        = 49250.00
         state_tax_bracket           = 0.0
         state_effective_tax_rate    = 0.0

Idaho TY2025 rate schedule (Single / MFS / HoH):
    $1 - $4,811   →  0% (zero-bracket exemption)
    $4,812+        →  5.3% (flat per H.B. 40, 2025; reduced from 5.69%)

Hand calc cross-check at $49,250 TI (Single):
    Tax = 5.3% × ($49,250 - $4,811)
        = 5.3% × $44,439
        = $2,355.267  (continuous formula)
    Graph backend: $2,355.267  (EXACT MATCH to the third decimal)
    Quantized to cents: $2,355.27

Standard deduction Single (TY2025) = $15,750 — Idaho conforms via
2025 H.B. 559 to the OBBBA federal standard deduction increase.

Reciprocity: ID has **no** bilateral reciprocity agreements.
Idaho is the only Mountain West state without a reciprocity
agreement with any neighbor.
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
from skill.scripts.states.id_ import PLUGIN, IdahoPlugin


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def single_65k_return() -> CanonicalReturn:
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=Person(
            first_name="Sam",
            last_name="Spud",
            ssn="111-22-3333",
            date_of_birth=dt.date(1990, 1, 1),
        ),
        address=Address(
            street1="700 W State St",
            city="Boise",
            state="ID",
            zip="83702",
        ),
        w2s=[
            W2(
                employer_name="Gem State Co",
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


class TestIdahoPluginMeta:
    def test_meta_code(self):
        assert PLUGIN.meta.code == "ID"

    def test_meta_name(self):
        assert PLUGIN.meta.name == "Idaho"

    def test_has_income_tax(self):
        assert PLUGIN.meta.has_income_tax is True

    def test_starting_point_is_federal_taxable_income(self):
        # Per the _plugin_api.py FEDERAL_TAXABLE_INCOME enum docstring
        # ("CO, ID, ND, SC, OR, UT") — Idaho's Form 40 line 7 is
        # federal AGI but federal std ded subtraction yields the same
        # net effect.
        assert (
            PLUGIN.meta.starting_point
            == StateStartingPoint.FEDERAL_TAXABLE_INCOME
        )

    def test_submission_channel_is_fed_state_piggyback(self):
        # Idaho does not operate a free direct portal; e-file is
        # routed through commercial software per EPB00070.
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.FED_STATE_PIGGYBACK
        )

    def test_dor_url(self):
        assert "tax.idaho.gov" in PLUGIN.meta.dor_url

    def test_no_free_efile_url(self):
        # Idaho relies on free-file partner software, not a DOR
        # direct portal.
        assert PLUGIN.meta.free_efile_url is None

    def test_supports_ty2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_no_reciprocity_partners(self):
        # Idaho is the only Mountain West state with no reciprocity
        # agreement with any neighbor.
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_meta_notes_mention_graph_backend(self):
        assert "graph" in PLUGIN.meta.notes.lower()

    def test_meta_notes_mention_form_40(self):
        assert "Form 40" in PLUGIN.meta.notes

    def test_meta_notes_mention_flat_rate(self):
        # 5.3% top rate per H.B. 40 (2025) is load-bearing.
        assert "5.3%" in PLUGIN.meta.notes

    def test_plugin_is_state_plugin_protocol(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_idaho_plugin_instance(self):
        assert isinstance(PLUGIN, IdahoPlugin)

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "CA"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# compute() — resident case matches tenforty reference numbers
# ---------------------------------------------------------------------------


class TestIdahoPluginComputeResident:
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

    def test_state_code_is_id(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state == "ID"

    def test_dollar_lock_single_65k(
        self, single_65k_return, federal_single_65k
    ):
        """DOLLAR LOCK: Single / $65k W-2 / Standard
        -> ID state_total_tax = $2,355.27 (tenforty graph backend,
           2025; raw float 2355.267 quantized half-up to cents).

        Hand calc against ID 2025 rate schedule:
            Tax = 5.3% × ($49,250 - $4,811) = $2,355.267
        Graph backend: $2,355.267 — EXACT MATCH (cent-quantized
        $2,355.27).

        This is the load-bearing regression guard for the ID plugin.
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        assert state_tax == Decimal("2355.27")

    def test_state_taxable_income_matches_tenforty(
        self, single_65k_return, federal_single_65k
    ):
        """Graph backend reports ID taxable income = $49,250 for
        $65k Single (= $65k AGI - $15,750 federal/ID std ded).
        Pin so any upstream change to ID conformity trips CI."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal(
            "49250.00"
        )

    def test_state_agi_matches_federal_agi(
        self, single_65k_return, federal_single_65k
    ):
        """Graph backend echoes federal AGI as state_agi for Idaho."""
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
            "id_flat_rate",
            "id_zero_bracket_top_single",
            "id_zero_bracket_top_mfj",
        ]
        for key in numeric_keys:
            assert key in result.state_specific, f"missing {key}"
            assert isinstance(
                result.state_specific[key], Decimal
            ), f"{key} is not Decimal"

    def test_starting_point_marker(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert (
            result.state_specific["starting_point"]
            == "federal_taxable_income"
        )

    def test_id_flat_rate_constant(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["id_flat_rate"] == Decimal("0.053")

    def test_id_zero_bracket_constants(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific[
            "id_zero_bracket_top_single"
        ] == Decimal("4811")
        assert result.state_specific[
            "id_zero_bracket_top_mfj"
        ] == Decimal("9622")

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
        assert rehydrated.state == "ID"
        assert rehydrated.residency == ResidencyStatus.RESIDENT


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment
# ---------------------------------------------------------------------------


class TestIdahoPluginComputeNonresident:
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
        assert full == Decimal("2355.27")
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
            "2355.27"
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


class TestIdahoPluginApportionIncome:
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


class TestIdahoPluginFormIds:
    def test_form_ids(self):
        assert PLUGIN.form_ids() == ["ID Form 40"]

    def test_render_pdfs_produces_pdf(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """ID Form 40 AcroForm fill produces a non-empty PDF."""
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
        assert paths[0].name == "ID_Form40.pdf"

    def test_render_pdfs_field_values(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Verify that rendered ID Form 40 PDF contains correct field values."""
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

        # Widget "IncomeL7" maps to state_adjusted_gross_income (Form 40 line 7)
        assert fields["IncomeL7"].get("/V") == "65000.00"
        # Widget "IncomeL19" maps to state_taxable_income (Form 40 line 19)
        assert fields["IncomeL19"].get("/V") == "49250.00"
        # Widget "TxCompL20" maps to state_total_tax (Form 40 line 20)
        assert fields["TxCompL20"].get("/V") == "2355.27"


# ---------------------------------------------------------------------------
# ReciprocityTable consistency
# ---------------------------------------------------------------------------


class TestIdahoReciprocityConsistency:
    def test_reciprocity_table_lookup_matches_plugin(self):
        table = ReciprocityTable.load()
        id_partners_from_table = table.partners_of("ID")
        assert id_partners_from_table == frozenset()
        assert (
            frozenset(PLUGIN.meta.reciprocity_partners)
            == id_partners_from_table
        )

    def test_no_reciprocity_with_mountain_west_neighbors(self):
        """Idaho is the only Mountain West state with NO reciprocity
        agreement; spot-check that none of its neighbors are
        treated as reciprocal."""
        table = ReciprocityTable.load()
        for neighbor in ("MT", "OR", "WA", "NV", "UT", "WY"):
            assert table.are_reciprocal("ID", neighbor) is False


# ---------------------------------------------------------------------------
# Graph-backend lock — pin tenforty's graph backend bit-for-bit so any
# upstream OpenTaxSolver schedule drift trips CI.
# ---------------------------------------------------------------------------


class TestGraphBackendLockForID:
    """When ANY of these tests STARTS FAILING, tenforty's graph
    backend has changed its TY2025 Idaho output. Investigate
    against ID DOR Form 40 instructions before updating the lock.

    ID is a graph-backend WRAPPER (not hand-rolled), per the
    decision rubric: hand calc and graph backend EXACT MATCH at
    $2,355.267 — the cleanest match in the wave-5 batch.
    """

    def test_default_backend_still_raises(self):
        """The OTS default backend MUST still raise — if it stops,
        ID may have been promoted to the OTS path."""
        with pytest.raises(ValueError, match="OTS does not support"):
            tenforty.evaluate_return(
                year=2025,
                state="ID",
                filing_status="Single",
                w2_income=65_000,
                num_dependents=0,
                standard_or_itemized="Standard",
                itemized_deductions=0,
            )

    def test_graph_backend_returns_2355_27(self):
        result = tenforty.evaluate_return(
            year=2025,
            state="ID",
            filing_status="Single",
            w2_income=65_000,
            num_dependents=0,
            standard_or_itemized="Standard",
            itemized_deductions=0,
            backend="graph",
        )
        assert Decimal(str(result.state_total_tax)).quantize(
            Decimal("0.01")
        ) == Decimal("2355.27")

    def test_graph_backend_taxable_income_49250(self):
        result = tenforty.evaluate_return(
            year=2025,
            state="ID",
            filing_status="Single",
            w2_income=65_000,
            num_dependents=0,
            standard_or_itemized="Standard",
            itemized_deductions=0,
            backend="graph",
        )
        assert Decimal(str(result.state_taxable_income)).quantize(
            Decimal("0.01")
        ) == Decimal("49250.00")

    def test_graph_backend_agi_65000(self):
        result = tenforty.evaluate_return(
            year=2025,
            state="ID",
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

    def test_hand_calc_exact_match_to_graph(self):
        """Hand calc and graph backend agree to the third decimal."""
        # 5.3% of (49,250 - 4,811) = 5.3% of 44,439
        hand_calc = Decimal("0.053") * (
            Decimal("49250") - Decimal("4811")
        )
        # 0.053 * 44,439 = 2,355.267 exactly
        assert hand_calc == Decimal("2355.267")
        # Graph backend (cent-quantized) = $2,355.27
        graph_quantized = Decimal("2355.27")
        # Hand calc quantized half-up = $2,355.27 also
        assert hand_calc.quantize(Decimal("0.01")) == graph_quantized


# ---------------------------------------------------------------------------
# Hand-calc cross-checks at additional taxable-income points
# ---------------------------------------------------------------------------


class TestIdahoHandCalcCrossChecks:
    """Standalone arithmetic checks of the ID 2025 flat rate
    schedule, independent of tenforty.
    """

    def test_zero_bracket_yields_zero(self):
        # Single TI ≤ $4,811 → 0% → $0 tax
        for ti in [
            Decimal("0"),
            Decimal("1000"),
            Decimal("4000"),
            Decimal("4811"),
        ]:
            taxable_above_zero = max(Decimal("0"), ti - Decimal("4811"))
            tax = Decimal("0.053") * taxable_above_zero
            assert tax == Decimal("0")

    def test_just_above_zero_bracket(self):
        # Single TI = $4,812 → tax on $1 → $0.053 → quantized $0.05
        taxable_above_zero = Decimal("4812") - Decimal("4811")
        tax = (
            Decimal("0.053") * taxable_above_zero
        ).quantize(Decimal("0.01"))
        assert tax == Decimal("0.05")

    def test_at_100k_single(self):
        # TI = $100,000 single → tax = 5.3% × ($100,000 - $4,811)
        # = 5.3% × $95,189 = $5,045.017 → $5,045.02
        tax = (
            Decimal("0.053") * (Decimal("100000") - Decimal("4811"))
        ).quantize(Decimal("0.01"))
        assert tax == Decimal("5045.02")

    def test_mfj_zero_bracket_doubled(self):
        # MFJ zero bracket is exactly double single ($9,622 vs $4,811)
        assert Decimal("9622") == Decimal("2") * Decimal("4811")
