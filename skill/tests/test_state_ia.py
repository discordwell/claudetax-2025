"""Iowa state plugin tests.

Mirrors the WI plugin test suite shape because IA, like WI, wraps the
tenforty graph backend (the default OTS backend raises ``ValueError:
OTS does not support 2025/IA_IA1040``). The plugin passes
``backend='graph'`` to ``tenforty.evaluate_return``, and we cross-check
the result against an independent flat-rate hand calculation from Iowa
DOR primary sources.

Reference scenario (verified 2026-04-11 via direct tenforty probe,
graph backend, 2025):

    Single / $65,000 W-2 / Standard
      -> state_total_tax            = 1871.50  (LOCKED)
         state_adjusted_gross_income = 65000.00
         state_taxable_income        = 49250.00 (= AGI - $15,750
                                                  federal std ded)
         state_tax_bracket           = 0.0      (graph backend omits)
         state_effective_tax_rate    = 0.0      (graph backend omits)

Iowa TY2025 rate / base (Senate File 2442 (2024) acceleration):
    Single FLAT RATE of 3.80% on (federal AGI - federal standard
    deduction). Iowa's separate standard-deduction schedule and
    personal-exemption credit were eliminated for TY2025 — Iowa now
    conforms to the federal standard deduction.

Hand check at $65k Single:
    (65000 - 15750) * 0.038 = 49250 * 0.038 = 1871.50  ✓

Reciprocity: Iowa has EXACTLY ONE bilateral reciprocity partner — IL —
per IA DOR's "Iowa - Illinois Reciprocal Agreement" page and
skill/reference/state-reciprocity.json (entry ``["IL", "IA"]``).
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
from skill.scripts.states.ia import (
    IA_TY2025_FLAT_RATE,
    PLUGIN,
    IowaPlugin,
)


# ---------------------------------------------------------------------------
# Shared fixtures — a Single $65k W-2 return domiciled in Iowa
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
            street1="1305 E Walnut St",
            city="Des Moines",
            state="IA",
            zip="50319",
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
    """Matches the CP4 Single $65k W-2 federal scenario (TY2025 OBBBA)."""
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


class TestIowaPluginMeta:
    def test_meta_fields(self):
        """Consolidated metadata check covering code, name, starting point,
        submission channel, reciprocity_partners, and has_income_tax flag."""
        assert PLUGIN.meta.code == "IA"
        assert PLUGIN.meta.name == "Iowa"
        assert PLUGIN.meta.has_income_tax is True
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.FED_STATE_PIGGYBACK
        )
        # Reciprocity: exactly IL — verified against
        # skill/reference/state-reciprocity.json and IA DOR's
        # Iowa-Illinois Reciprocal Agreement publication.
        assert set(PLUGIN.meta.reciprocity_partners) == {"IL"}
        assert len(PLUGIN.meta.reciprocity_partners) == 1

    def test_plugin_is_state_plugin_protocol(self):
        """runtime_checkable Protocol must recognize the concrete plugin."""
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_iowa_plugin_instance(self):
        assert isinstance(PLUGIN, IowaPlugin)

    def test_meta_urls(self):
        assert "tax.iowa.gov" in PLUGIN.meta.dor_url
        assert PLUGIN.meta.free_efile_url is not None
        assert "tax.iowa.gov" in PLUGIN.meta.free_efile_url
        # Explicit GovConnectIowa surface in free_efile_url.
        assert "govconnect" in PLUGIN.meta.free_efile_url.lower()

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_flat_rate_and_legislation(self):
        """Notes must document the TY2025 flat rate and SF 2442."""
        notes = PLUGIN.meta.notes
        assert "3.80" in notes or "3.8%" in notes
        assert "2442" in notes  # Senate File 2442 (2024)
        assert "flat" in notes.lower()

    def test_meta_notes_mentions_tenforty_and_graph_backend(self):
        notes = PLUGIN.meta.notes.lower()
        assert "tenforty" in notes
        assert "graph" in notes

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "NY"  # type: ignore[misc]

    def test_meta_reciprocity_excludes_non_partners(self):
        """A few common neighbors that are NOT IA reciprocity partners.

        Iowa has only IL — NE/SD/MN/WI/MO are explicitly NOT partners,
        which matters because Iowa-NE / Iowa-MN / Iowa-WI commuters are
        common cross-border cases."""
        for not_partner in ("NE", "SD", "MN", "WI", "MO", "CA", "NY"):
            assert not_partner not in PLUGIN.meta.reciprocity_partners


# ---------------------------------------------------------------------------
# compute() — resident case matches DOR primary source AND tenforty
# ---------------------------------------------------------------------------


class TestIowaPluginComputeResident:
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

    def test_state_code_is_ia(self, single_65k_return, federal_single_65k):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state == "IA"

    def test_residency_preserved(self, single_65k_return, federal_single_65k):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.residency == ResidencyStatus.RESIDENT
        assert result.days_in_state == 365

    def test_resident_65k_single_lock(
        self, single_65k_return, federal_single_65k
    ):
        """WRAP-CORRECTNESS LOCK: Single / $65k W-2 / Standard
        -> IA state_total_tax = $1,871.50

        Pinned bit-for-bit against (a) the tenforty graph backend
        result and (b) the hand calculation 49,250 * 0.038 = 1,871.50.
        Both checks must pass for the plugin to be acceptable."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        assert state_tax == Decimal("1871.50")

        # Cross-check (1): direct tenforty probe must agree.
        direct = tenforty.evaluate_return(
            year=2025,
            state="IA",
            filing_status="Single",
            w2_income=65000,
            standard_or_itemized="Standard",
            itemized_deductions=0,
            num_dependents=0,
            backend="graph",
        )
        assert Decimal(str(direct.state_total_tax)).quantize(
            Decimal("0.01")
        ) == Decimal("1871.50")

        # Cross-check (2): hand calculation per IA Code §422.5(1)(a).
        hand = (Decimal("65000") - Decimal("15750")) * IA_TY2025_FLAT_RATE
        assert hand.quantize(Decimal("0.01")) == Decimal("1871.50")

    def test_state_taxable_income_matches_federal_taxable_income(
        self, single_65k_return, federal_single_65k
    ):
        """For TY2025, IA conforms its starting point to federal
        taxable income (federal AGI minus federal standard deduction).
        At $65k Single Standard, IA TI = 65,000 - 15,750 = $49,250."""
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
        """Iowa's starting point is federal AGI."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific[
            "state_adjusted_gross_income"
        ] == Decimal("65000.00")

    def test_handcheck_matches_graph_backend(
        self, single_65k_return, federal_single_65k
    ):
        """The plugin records its own hand calculation as a diagnostic
        and asserts it agrees with the graph backend wrap value."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["ia_handcheck_matches_graph"] is True
        assert result.state_specific["ia_tax_handcheck"] == Decimal(
            "1871.50"
        )
        assert result.state_specific[
            "ia_taxable_income_handcheck"
        ] == Decimal("49250.00")

    def test_state_tax_bracket_graph_backend_zero(
        self, single_65k_return, federal_single_65k
    ):
        """Graph backend reports state_tax_bracket = 0.0 for IA (same
        gap as WI). Pinned to catch upstream changes."""
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
        """Graph backend reports state_effective_tax_rate = 0.0 for IA."""
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
            "ia_flat_rate",
            "ia_taxable_income_handcheck",
            "ia_tax_handcheck",
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
        """Full-year resident: resident-basis tax == apportioned tax."""
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
        """Round-trip through Pydantic JSON to confirm the returned
        StateReturn validates under the canonical model contract."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        dumped = result.model_dump(mode="json")
        rehydrated = StateReturn.model_validate(dumped)
        assert rehydrated.state == "IA"
        assert rehydrated.residency == ResidencyStatus.RESIDENT


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment (day-based v1)
# ---------------------------------------------------------------------------


class TestIowaPluginComputeNonresident:
    def test_nonresident_half_year_prorates_tax(
        self, single_65k_return, federal_single_65k
    ):
        """NONRESIDENT with days_in_state=182 should yield 182/365 of
        the resident-basis tax via day-based proration. v1 stopgap;
        the real Iowa rule is Schedule IA 126."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=182,
        )
        full = result.state_specific["state_total_tax_resident_basis"]
        apportioned = result.state_specific["state_total_tax"]
        assert full == Decimal("1871.50")
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
        assert result.state_specific[
            "state_total_tax_resident_basis"
        ] == Decimal("1871.50")

    def test_part_year_apportionment_fraction(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.PART_YEAR,
            days_in_state=91,
        )
        expected_fraction = Decimal(91) / Decimal("365")
        assert (
            result.state_specific["apportionment_fraction"]
            == expected_fraction
        )


# ---------------------------------------------------------------------------
# apportion_income()
# ---------------------------------------------------------------------------


class TestIowaPluginApportionIncome:
    def test_apportion_income_resident_full_amounts(self, single_65k_return):
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


class TestIowaPluginFormIds:
    def test_form_ids(self):
        form_ids = PLUGIN.form_ids()
        assert "IA 1040" in form_ids
        assert form_ids == ["IA 1040"]

    def test_render_pdfs_returns_empty_list(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Iowa DOR does not publish a standalone fillable IA 1040 PDF
        with AcroForm widgets for TY2025. render_pdfs returns [] until
        a fillable PDF becomes available."""
        state_return = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert PLUGIN.render_pdfs(state_return, tmp_path) == []


# ---------------------------------------------------------------------------
# ReciprocityTable consistency
# ---------------------------------------------------------------------------


def test_reciprocity_matches_json():
    """ReciprocityTable.partners_of('IA') must equal the plugin's
    meta.reciprocity_partners. Catches drift between
    skill/reference/state-reciprocity.json and the IA plugin."""
    table = ReciprocityTable.load()
    ia_partners = table.partners_of("IA")
    assert ia_partners == frozenset({"IL"})
    assert frozenset(PLUGIN.meta.reciprocity_partners) == ia_partners


def test_reciprocity_table_recognizes_il_pair():
    """IL <-> IA must satisfy are_reciprocal('IA', 'IL')."""
    table = ReciprocityTable.load()
    assert table.are_reciprocal("IA", "IL") is True
    assert table.are_reciprocal("IL", "IA") is True
    # Confirm a NON-partner returns False (NE-IA is a common cross-
    # border commute pair but is NOT reciprocal).
    assert table.are_reciprocal("IA", "NE") is False
    assert table.are_reciprocal("IA", "MN") is False
    assert table.are_reciprocal("IA", "WI") is False


# ---------------------------------------------------------------------------
# Gatekeeper test — locks the wrap-vs-hand-roll decision to current
# tenforty behavior. When this STARTS FAILING, re-evaluate the plugin
# decision against the rubric in skill/reference/tenforty-ty2025-gap.md.
# ---------------------------------------------------------------------------


class TestTenfortyIaTy2025GraphBackendStable:
    """Locks tenforty's TY2025 IA behavior so drift is auto-detected.

    Two invariants:
      1. The default OTS backend MUST still raise
         ``ValueError: OTS does not support 2025/IA_IA1040``. When that
         starts working, the plugin can drop the explicit
         ``backend='graph'`` and follow the OH/NJ/MI shape directly.
      2. The graph backend's $65k Single result MUST still equal
         $1,871.50. If it changes, our wrap is silently broken.
    """

    def test_default_backend_still_raises(self):
        with pytest.raises(ValueError, match="OTS does not support"):
            tenforty.evaluate_return(
                year=2025,
                state="IA",
                filing_status="Single",
                w2_income=65000,
                num_dependents=0,
                standard_or_itemized="Standard",
                itemized_deductions=0,
            )

    def test_graph_backend_65k_single_locked(self):
        result = tenforty.evaluate_return(
            year=2025,
            state="IA",
            filing_status="Single",
            w2_income=65000,
            num_dependents=0,
            standard_or_itemized="Standard",
            itemized_deductions=0,
            backend="graph",
        )
        assert Decimal(str(result.state_total_tax)).quantize(
            Decimal("0.01")
        ) == Decimal("1871.50")
        # Graph also exposes the (federal-conformed) Iowa TI:
        assert Decimal(str(result.state_taxable_income)).quantize(
            Decimal("0.01")
        ) == Decimal("49250.00")

    def test_iowa_flat_rate_constant(self):
        """The TY2025 IA flat rate is 3.80% per IA Code §422.5(1)(a)."""
        assert IA_TY2025_FLAT_RATE == Decimal("0.038")

    def test_handcheck_at_multiple_incomes(self):
        """Verify the (AGI - federal_std_ded) * 3.80% rule against the
        graph backend at several income levels (Single, Standard).

        These are non-trivial because they hit different bands of the
        federal tax tables but Iowa's rate is uniform across them."""
        cases: list[tuple[int, Decimal]] = [
            (20000, Decimal("161.50")),    # (20000-15750)*0.038
            (30000, Decimal("541.50")),    # (30000-15750)*0.038
            (50000, Decimal("1301.50")),
            (75000, Decimal("2251.50")),
            (100000, Decimal("3201.50")),
            (150000, Decimal("5101.50")),
            (250000, Decimal("8901.50")),
            (500000, Decimal("18401.50")),
        ]
        for w2, expected in cases:
            r = tenforty.evaluate_return(
                year=2025,
                state="IA",
                filing_status="Single",
                w2_income=w2,
                num_dependents=0,
                standard_or_itemized="Standard",
                itemized_deductions=0,
                backend="graph",
            )
            got = Decimal(str(r.state_total_tax)).quantize(Decimal("0.01"))
            assert got == expected, (
                f"IA $65k Single hand check FAILED at w2={w2}: "
                f"got {got}, expected {expected}"
            )

    def test_iowa_no_personal_exemption_credit_for_ty2025(self):
        """Iowa SF 2442 (2024) eliminated the personal exemption credit
        for TY2025. There is no $40 single / $80 MFJ credit anymore.

        This test isn't a direct check of the credit constant (we don't
        define one), but rather verifies the locked $1,871.50 number is
        consistent with NO PEC subtraction. If the plugin ever started
        applying a PEC, the $65k Single result would drop below
        $1,871.50 and the lock test above would fail. We assert the
        equivalent invariant here for clarity."""
        result = tenforty.evaluate_return(
            year=2025,
            state="IA",
            filing_status="Single",
            w2_income=65000,
            num_dependents=0,
            standard_or_itemized="Standard",
            itemized_deductions=0,
            backend="graph",
        )
        # 49,250 * 0.038 = 1,871.50 — no credit subtraction.
        assert Decimal(str(result.state_total_tax)).quantize(
            Decimal("0.01")
        ) == Decimal("1871.50")
