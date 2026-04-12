"""Montana state plugin tests.

Mirrors the WI graph-backend wrapper test pattern. MT is wired up in
tenforty only via the graph backend (the OTS backend raises
``ValueError: OTS does not support 2025/MT_Form2``), so the MT
plugin passes ``backend='graph'`` to tenforty.evaluate_return.

Reference scenario (verified 2026-04-11 via direct tenforty probe,
graph backend, 2025):
    Single / $65,000 W-2 / Standard
      -> state_total_tax            = 2652.55
         state_adjusted_gross_income = 49250.00
         state_taxable_income        = 49250.00
         state_tax_bracket           = 0.0
         state_effective_tax_rate    = 0.0

Note: graph backend reports state_agi = $49,250 (NOT $65,000 federal
AGI) because Montana Form 2 lines 1-3 collapse federal AGI - federal
std/itemized into a "federal taxable income" base, and the graph
backend surfaces line 3 as both state_agi and state_taxable_income
for the wage-only base case.

Montana TY2025 ordinary income brackets (per 2025 Montana Tax Tables
and Deductions, post Tax Simplification 2-bracket structure):

    Single / MFS / Estates / Trusts:
        $0     - $21,100   →  4.7%
        $21,100+           →  5.9%
    HoH:
        $0     - $31,700   →  4.7%
        $31,700+           →  5.9%
    MFJ / QSS:
        $0     - $42,200   →  4.7%
        $42,200+           →  5.9%

Hand calc cross-check at $49,250 TI (Single):
    Tax = 4.7% × $21,100 + 5.9% × ($49,250 - $21,100)
        = $991.70 + 5.9% × $28,150
        = $991.70 + $1,660.85
        = $2,652.55  ← EXACT MATCH to graph backend

Reciprocity: MT has ONE bilateral reciprocity agreement: with
North Dakota (ND). Verified against
skill/reference/state-reciprocity.json.
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
from skill.scripts.states.mt import PLUGIN, MontanaPlugin


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def single_65k_return() -> CanonicalReturn:
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=Person(
            first_name="Morgan",
            last_name="Big-Sky",
            ssn="111-22-3333",
            date_of_birth=dt.date(1990, 1, 1),
        ),
        address=Address(
            street1="125 N Roberts St",
            city="Helena",
            state="MT",
            zip="59601",
        ),
        w2s=[
            W2(
                employer_name="Big Sky Co",
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


class TestMontanaPluginMeta:
    def test_meta_code(self):
        assert PLUGIN.meta.code == "MT"

    def test_meta_name(self):
        assert PLUGIN.meta.name == "Montana"

    def test_has_income_tax(self):
        assert PLUGIN.meta.has_income_tax is True

    def test_starting_point_is_federal_taxable_income(self):
        # Montana Form 2 line 3 = federal AGI - federal std/itemized
        # = federal taxable income.
        assert (
            PLUGIN.meta.starting_point
            == StateStartingPoint.FEDERAL_TAXABLE_INCOME
        )

    def test_submission_channel_is_state_dor_free_portal(self):
        # Montana TransAction Portal (TAP) is the free direct path.
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )

    def test_dor_url(self):
        assert "revenue.mt.gov" in PLUGIN.meta.dor_url

    def test_free_efile_url_is_tap(self):
        assert PLUGIN.meta.free_efile_url is not None
        assert "tap.dor.mt.gov" in PLUGIN.meta.free_efile_url

    def test_supports_ty2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_reciprocity_partners_only_nd(self):
        # MT has exactly ONE reciprocity partner: ND.
        assert PLUGIN.meta.reciprocity_partners == ("ND",)

    def test_meta_notes_mention_graph_backend(self):
        assert "graph" in PLUGIN.meta.notes.lower()

    def test_meta_notes_mention_form_2(self):
        assert "Form 2" in PLUGIN.meta.notes

    def test_meta_notes_mention_two_bracket(self):
        # 4.7% / 5.9% structure is load-bearing for the human reader.
        assert "4.7%" in PLUGIN.meta.notes
        assert "5.9%" in PLUGIN.meta.notes

    def test_meta_notes_mention_reciprocity_with_nd(self):
        assert "ND" in PLUGIN.meta.notes

    def test_plugin_is_state_plugin_protocol(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_montana_plugin_instance(self):
        assert isinstance(PLUGIN, MontanaPlugin)

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "CA"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# compute() — resident case matches tenforty reference numbers
# ---------------------------------------------------------------------------


class TestMontanaPluginComputeResident:
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

    def test_state_code_is_mt(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state == "MT"

    def test_dollar_lock_single_65k(
        self, single_65k_return, federal_single_65k
    ):
        """DOLLAR LOCK: Single / $65k W-2 / Standard
        -> MT state_total_tax = $2,652.55 (tenforty graph backend,
           2025).

        Hand calc against MT 2-bracket schedule:
            Tax = 4.7% × $21,100 + 5.9% × ($49,250 - $21,100)
                = $991.70 + $1,660.85
                = $2,652.55
        Graph backend: $2,652.55 — EXACT MATCH (Montana is the
        cleanest of the wave-5 graph-backend wraps).

        This is the load-bearing regression guard for the MT plugin.
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        assert state_tax == Decimal("2652.55")

    def test_state_taxable_income_matches_tenforty(
        self, single_65k_return, federal_single_65k
    ):
        """Graph backend reports MT taxable income = $49,250 for
        $65k Single (= $65k AGI - $15,750 federal std ded). Pin so
        any upstream conformity change trips CI."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal(
            "49250.00"
        )

    def test_state_agi_echoes_federal_taxable_income(
        self, single_65k_return, federal_single_65k
    ):
        """Graph backend echoes federal TAXABLE INCOME (NOT federal
        AGI) into state_adjusted_gross_income for MT. This is the
        Form 2 line 3 collapse — pin so any upstream change is
        caught and the docstring caveat can be revisited."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific[
            "state_adjusted_gross_income"
        ] == Decimal("49250.00")

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
            "mt_lower_rate",
            "mt_upper_rate",
            "mt_bracket_break_single",
            "mt_bracket_break_hoh",
            "mt_bracket_break_mfj",
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

    def test_mt_rate_constants(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["mt_lower_rate"] == Decimal(
            "0.047"
        )
        assert result.state_specific["mt_upper_rate"] == Decimal(
            "0.059"
        )

    def test_mt_bracket_break_constants(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific[
            "mt_bracket_break_single"
        ] == Decimal("21100")
        assert result.state_specific[
            "mt_bracket_break_hoh"
        ] == Decimal("31700")
        assert result.state_specific[
            "mt_bracket_break_mfj"
        ] == Decimal("42200")

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
        assert rehydrated.state == "MT"
        assert rehydrated.residency == ResidencyStatus.RESIDENT


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment
# ---------------------------------------------------------------------------


class TestMontanaPluginComputeNonresident:
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
        assert full == Decimal("2652.55")
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
            "2652.55"
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


class TestMontanaPluginApportionIncome:
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


class TestMontanaPluginFormIds:
    def test_form_ids(self):
        assert PLUGIN.form_ids() == ["MT Form 2"]

    def test_render_pdfs_produces_pdf(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """MT Form 2 AcroForm fill produces a non-empty PDF."""
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
        assert paths[0].name == "mt_form2.pdf"

    def test_render_pdfs_field_values(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Verify rendered MT Form 2 PDF contains correct field values."""
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

        # Widget "Page 1 Line 17" maps to state_total_tax
        assert fields["Page 1 Line 17"].get("/V") == "2652.55"
        # Widget "Page 1 Line 3" maps to state_taxable_income
        assert fields["Page 1 Line 3"].get("/V") == "49250.00"


# ---------------------------------------------------------------------------
# ReciprocityTable consistency
# ---------------------------------------------------------------------------


class TestMontanaReciprocityConsistency:
    def test_reciprocity_table_lookup_matches_plugin(self):
        table = ReciprocityTable.load()
        mt_partners_from_table = table.partners_of("MT")
        # MT - ND is the only bilateral pair involving Montana.
        assert mt_partners_from_table == frozenset({"ND"})
        assert (
            frozenset(PLUGIN.meta.reciprocity_partners)
            == mt_partners_from_table
        )

    def test_mt_nd_pair_recognized(self):
        table = ReciprocityTable.load()
        assert table.are_reciprocal("MT", "ND") is True
        # And the symmetric direction:
        assert table.are_reciprocal("ND", "MT") is True

    def test_mt_not_reciprocal_with_other_neighbors(self):
        """Spot-check that MT does NOT have agreements with other
        common neighbors (ID, WY, SD)."""
        table = ReciprocityTable.load()
        for neighbor in ("ID", "WY", "SD"):
            assert table.are_reciprocal("MT", neighbor) is False


# ---------------------------------------------------------------------------
# Graph-backend lock — pin tenforty's graph backend bit-for-bit so any
# upstream OpenTaxSolver schedule drift trips CI.
# ---------------------------------------------------------------------------


class TestGraphBackendLockForMT:
    """When ANY of these tests STARTS FAILING, tenforty's graph
    backend has changed its TY2025 Montana output. Investigate
    against MT DOR Form 2 instructions before updating the lock.

    MT is a graph-backend WRAPPER (not hand-rolled), per the
    decision rubric: hand calc and graph backend EXACT MATCH at
    $2,652.55 — Montana is the cleanest match in the wave-5 batch.
    """

    def test_default_backend_still_raises(self):
        """The OTS default backend MUST still raise — if it stops,
        MT may have been promoted to the OTS path."""
        with pytest.raises(ValueError, match="OTS does not support"):
            tenforty.evaluate_return(
                year=2025,
                state="MT",
                filing_status="Single",
                w2_income=65_000,
                num_dependents=0,
                standard_or_itemized="Standard",
                itemized_deductions=0,
            )

    def test_graph_backend_returns_2652_55(self):
        result = tenforty.evaluate_return(
            year=2025,
            state="MT",
            filing_status="Single",
            w2_income=65_000,
            num_dependents=0,
            standard_or_itemized="Standard",
            itemized_deductions=0,
            backend="graph",
        )
        assert Decimal(str(result.state_total_tax)).quantize(
            Decimal("0.01")
        ) == Decimal("2652.55")

    def test_graph_backend_taxable_income_49250(self):
        result = tenforty.evaluate_return(
            year=2025,
            state="MT",
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

    def test_graph_backend_agi_echoes_taxable_income(self):
        """MT graph backend echoes federal taxable income into both
        state_adjusted_gross_income AND state_taxable_income for the
        wage-only base case. Pin so any upstream change is caught."""
        result = tenforty.evaluate_return(
            year=2025,
            state="MT",
            filing_status="Single",
            w2_income=65_000,
            num_dependents=0,
            standard_or_itemized="Standard",
            itemized_deductions=0,
            backend="graph",
        )
        assert Decimal(
            str(result.state_adjusted_gross_income)
        ).quantize(Decimal("0.01")) == Decimal("49250.00")

    def test_hand_calc_exact_match_to_graph(self):
        """Hand calc and graph backend agree to the cent."""
        # 4.7% × $21,100 = $991.70
        # 5.9% × ($49,250 - $21,100) = 5.9% × $28,150 = $1,660.85
        # Total = $2,652.55
        lower_tier = Decimal("0.047") * Decimal("21100")
        assert lower_tier == Decimal("991.700")
        upper_tier = Decimal("0.059") * (
            Decimal("49250") - Decimal("21100")
        )
        assert upper_tier == Decimal("1660.850")
        hand_calc = (lower_tier + upper_tier).quantize(Decimal("0.01"))
        assert hand_calc == Decimal("2652.55")


# ---------------------------------------------------------------------------
# Hand-calc cross-checks at additional taxable-income points
# ---------------------------------------------------------------------------


class TestMontanaHandCalcCrossChecks:
    """Standalone arithmetic checks of the MT 2-bracket schedule,
    independent of tenforty.
    """

    def test_at_lower_bracket_top_single(self):
        # TI = $21,100 single → tax = 4.7% × $21,100 = $991.70
        tax = Decimal("0.047") * Decimal("21100")
        assert tax.quantize(Decimal("0.01")) == Decimal("991.70")

    def test_at_lower_bracket_top_mfj(self):
        # TI = $42,200 MFJ → tax = 4.7% × $42,200 = $1,983.40
        tax = Decimal("0.047") * Decimal("42200")
        assert tax.quantize(Decimal("0.01")) == Decimal("1983.40")

    def test_at_lower_bracket_top_hoh(self):
        # TI = $31,700 HoH → tax = 4.7% × $31,700 = $1,489.90
        tax = Decimal("0.047") * Decimal("31700")
        assert tax.quantize(Decimal("0.01")) == Decimal("1489.90")

    def test_high_income_single(self):
        # TI = $200,000 single → 4.7% × $21,100 + 5.9% × $178,900
        # = $991.70 + $10,555.10 = $11,546.80
        lower = Decimal("0.047") * Decimal("21100")
        upper = Decimal("0.059") * (Decimal("200000") - Decimal("21100"))
        total = (lower + upper).quantize(Decimal("0.01"))
        assert total == Decimal("11546.80")
