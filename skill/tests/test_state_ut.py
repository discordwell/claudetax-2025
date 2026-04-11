"""Utah state plugin tests — TY2025.

Hand-rolled UT Form TC-40 calc — see ``skill/scripts/states/ut.py``
docstring for the full DOR-primary-source trace.

UT-SPECIFIC FINDING (Taxpayer Tax Credit phase-out): the wave 5 task
spec said "verify $1,980 graph value carefully — graph is applying the
UT Taxpayer Tax Credit OR our flat rate assumption is outdated."
Verified 2026-04-11 against 2025 TC-40 Instructions:

1. The TY2025 flat rate dropped from 4.55% to **4.5%** per HB 106
   (2025 Utah Legislature). Spec's "4.55%" was stale.
2. The graph backend applies the **full** $945 initial Taxpayer Tax
   Credit but **omits the income-based phase-out**, which is the
   defining feature of the UT credit. At $65k Single the phase-out
   wipes out roughly two-thirds of the initial credit.

Hand-traced reference scenario:
    Single $65k W-2, OBBBA std ded $15,750
    -> federal AGI $65,000
    -> UT TI = $65,000 (no add/sub)
    -> UT tax calc = $65,000 * 4.5% = $2,925.00
    -> Initial credit = $15,750 * 6% = $945.00
    -> Phase-out = ($65,000 - $18,213) * 1.3% = $46,787 * 0.013 = $608.231
    -> Taxpayer tax credit = max(0, $945 - $608.231) = $336.77 (cents)
    -> UT income tax = $2,925 - $336.769 = **$2,588.23**

The graph backend returns $1,980 = $2,925 - $945 (full credit, no
phase-out applied). Delta is ~$608, way outside the ±$5 wrap window
→ hand-roll.
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
from skill.scripts.states.ut import (
    UT_TY2025_BASE_PHASE_OUT_HOH,
    UT_TY2025_BASE_PHASE_OUT_MFJ,
    UT_TY2025_BASE_PHASE_OUT_SINGLE,
    UT_TY2025_FLAT_RATE,
    UT_TY2025_PERSONAL_EXEMPTION_PER_DEPENDENT,
    UT_TY2025_TAXPAYER_TAX_CREDIT_PHASE_OUT_RATE,
    UT_TY2025_TAXPAYER_TAX_CREDIT_RATE,
    UT_V1_LIMITATIONS,
    PLUGIN,
    UtahPlugin,
    ut_base_phase_out,
    ut_personal_exemption,
    ut_taxpayer_tax_credit,
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
            first_name="Brigham",
            last_name="Younger",
            ssn="111-22-3333",
            date_of_birth=dt.date(1990, 1, 1),
        ),
        address=Address(
            street1="350 N State St",
            city="Salt Lake City",
            state="UT",
            zip="84103",
        ),
        w2s=[
            W2(
                employer_name="Beehive State Co",
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
    )


@pytest.fixture
def federal_low_15k() -> FederalTotals:
    """Below the federal std deduction → Qualified Exempt taxpayer."""
    return FederalTotals(
        filing_status=FilingStatus.SINGLE,
        num_dependents=0,
        adjusted_gross_income=Decimal("15000"),
        taxable_income=Decimal("0"),
        total_federal_tax=Decimal("0"),
        federal_income_tax=Decimal("0"),
        federal_standard_deduction=Decimal("15750"),
        federal_itemized_deductions_total=Decimal("0"),
        deduction_taken=Decimal("15750"),
    )


# ---------------------------------------------------------------------------
# Meta + Protocol
# ---------------------------------------------------------------------------


class TestUtahPluginMeta:
    def test_meta_fields(self):
        assert PLUGIN.meta.code == "UT"
        assert PLUGIN.meta.name == "Utah"
        assert PLUGIN.meta.has_income_tax is True
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI

    def test_meta_no_reciprocity(self):
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_meta_submission_channel_is_dor_free_portal(self):
        """UT operates Utah TAP as a free DOR-direct portal."""
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )

    def test_meta_dor_url(self):
        assert "tax.utah.gov" in PLUGIN.meta.dor_url

    def test_meta_free_efile_url_is_tap(self):
        assert PLUGIN.meta.free_efile_url is not None
        assert "tap.utah.gov" in PLUGIN.meta.free_efile_url

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_4_5_rate(self):
        """The TY2025 rate is 4.5% (HB 106 dropped from 4.55%)."""
        assert "4.5" in PLUGIN.meta.notes

    def test_meta_notes_mention_phase_out(self):
        assert "phase-out" in PLUGIN.meta.notes.lower()

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "NV"  # type: ignore[misc]

    def test_plugin_is_state_plugin_protocol(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_utah_plugin_instance(self):
        assert isinstance(PLUGIN, UtahPlugin)


# ---------------------------------------------------------------------------
# Reciprocity
# ---------------------------------------------------------------------------


class TestUtahNoReciprocity:
    def test_partners_empty(self):
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_no_reciprocity_via_table(self):
        table = ReciprocityTable.load()
        assert table.partners_of("UT") == frozenset()
        assert table.has_income_tax("UT") is True


# ---------------------------------------------------------------------------
# TY2025 constants
# ---------------------------------------------------------------------------


class TestUtahConstants:
    def test_flat_rate_is_4_5(self):
        """HB 106 (2025) lowered from 4.55% to 4.5%."""
        assert UT_TY2025_FLAT_RATE == Decimal("0.045")

    def test_personal_exemption(self):
        assert UT_TY2025_PERSONAL_EXEMPTION_PER_DEPENDENT == Decimal("2111")

    def test_credit_rate(self):
        assert UT_TY2025_TAXPAYER_TAX_CREDIT_RATE == Decimal("0.06")

    def test_phase_out_rate(self):
        assert UT_TY2025_TAXPAYER_TAX_CREDIT_PHASE_OUT_RATE == Decimal(
            "0.013"
        )

    def test_base_phase_out_amounts(self):
        assert UT_TY2025_BASE_PHASE_OUT_SINGLE == Decimal("18213")
        assert UT_TY2025_BASE_PHASE_OUT_MFJ == Decimal("36426")
        assert UT_TY2025_BASE_PHASE_OUT_HOH == Decimal("27320")


class TestUtahHelpers:
    def test_personal_exemption_zero_deps(self):
        assert ut_personal_exemption(0) == Decimal("0")

    def test_personal_exemption_two_deps(self):
        assert ut_personal_exemption(2) == Decimal("4222")

    def test_negative_dep_count_clamped_to_zero(self):
        assert ut_personal_exemption(-1) == Decimal("0")

    def test_base_phase_out_dispatch(self):
        assert ut_base_phase_out(FilingStatus.SINGLE) == Decimal("18213")
        assert ut_base_phase_out(FilingStatus.MFJ) == Decimal("36426")
        assert ut_base_phase_out(FilingStatus.MFS) == Decimal("18213")
        assert ut_base_phase_out(FilingStatus.HOH) == Decimal("27320")
        assert ut_base_phase_out(FilingStatus.QSS) == Decimal("36426")


class TestUtahTaxpayerTaxCredit:
    def test_full_credit_below_phase_out_threshold(self):
        """At UT TI $15,000 Single (below $18,213 base), no phase-out."""
        initial, phase_out, credit = ut_taxpayer_tax_credit(
            ut_taxable_income=Decimal("15000"),
            federal_deduction=Decimal("15750"),
            salt_addback=Decimal("0"),
            num_dependents=0,
            filing_status=FilingStatus.SINGLE,
        )
        assert initial == Decimal("945.00")
        assert phase_out == Decimal("0.00")
        assert credit == Decimal("945.00")

    def test_partial_phase_out_at_65k_single(self):
        """At UT TI $65,000 Single:
        initial = 15,750 * 0.06 = 945
        phase_out = (65,000 - 18,213) * 0.013 = 46,787 * 0.013 = 608.231
        credit = max(0, 945 - 608.231) = 336.769 → $336.77
        """
        initial, phase_out, credit = ut_taxpayer_tax_credit(
            ut_taxable_income=Decimal("65000"),
            federal_deduction=Decimal("15750"),
            salt_addback=Decimal("0"),
            num_dependents=0,
            filing_status=FilingStatus.SINGLE,
        )
        assert initial == Decimal("945.00")
        assert phase_out == Decimal("608.23")
        assert credit == Decimal("336.77")

    def test_credit_zeroed_at_high_income(self):
        """At UT TI $200,000 Single, phase-out exceeds initial credit → 0."""
        _initial, _phase_out, credit = ut_taxpayer_tax_credit(
            ut_taxable_income=Decimal("200000"),
            federal_deduction=Decimal("15750"),
            salt_addback=Decimal("0"),
            num_dependents=0,
            filing_status=FilingStatus.SINGLE,
        )
        assert credit == Decimal("0.00")

    def test_personal_exemption_increases_initial_credit(self):
        """3 dependents add 3 * $2,111 = $6,333 to line 13 base.
        New initial credit = ($15,750 + $6,333) * 6% = $22,083 * 0.06
        = $1,324.98
        """
        initial, _, _ = ut_taxpayer_tax_credit(
            ut_taxable_income=Decimal("15000"),
            federal_deduction=Decimal("15750"),
            salt_addback=Decimal("0"),
            num_dependents=3,
            filing_status=FilingStatus.SINGLE,
        )
        assert initial == Decimal("1324.98")


# ---------------------------------------------------------------------------
# compute() — resident
# ---------------------------------------------------------------------------


class TestUtahPluginComputeResident:
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
        assert result.state == "UT"

    def test_resident_single_65k_lock(
        self, single_65k_return, federal_single_65k
    ):
        """**SPEC-MANDATED $65k Single LOCK**: $2,588.23.

        Hand-traced from 2025 TC-40 Instructions — see module docstring
        for the full line-by-line trace. The graph backend's $1,980 is
        wrong because it omits the Taxpayer Tax Credit phase-out."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_total_tax"] == Decimal("2588.23")
        assert result.state_specific[
            "state_total_tax_resident_basis"
        ] == Decimal("2588.23")

    def test_resident_single_65k_line_breakdown(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = result.state_specific
        assert ss["state_federal_agi"] == Decimal("65000.00")
        assert ss["state_taxable_income"] == Decimal("65000.00")
        assert ss["state_tax_before_credit"] == Decimal("2925.00")
        assert ss["state_personal_exemption"] == Decimal("0.00")
        assert ss["state_federal_deduction"] == Decimal("15750.00")
        assert ss["state_initial_taxpayer_tax_credit"] == Decimal("945.00")
        assert ss["state_taxpayer_tax_credit_phase_out"] == Decimal(
            "608.23"
        )
        assert ss["state_taxpayer_tax_credit"] == Decimal("336.77")
        assert ss["state_qualified_exempt"] is False

    def test_qualified_exempt_low_income(self, federal_low_15k):
        """Federal AGI $15,000 < $15,750 std ded → Qualified Exempt."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="P",
                last_name="Q",
                ssn="999-88-7777",
                date_of_birth=dt.date(1990, 1, 1),
            ),
            address=Address(
                street1="1 Main", city="Provo", state="UT", zip="84601"
            ),
        )
        result = PLUGIN.compute(
            ret, federal_low_15k, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert result.state_specific["state_qualified_exempt"] is True
        assert result.state_specific["state_total_tax"] == Decimal("0.00")

    def test_state_specific_decimal_fields(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        for k in (
            "state_federal_agi",
            "state_adjusted_gross_income",
            "state_taxable_income",
            "state_tax_before_credit",
            "state_personal_exemption",
            "state_federal_deduction",
            "state_initial_taxpayer_tax_credit",
            "state_taxpayer_tax_credit_phase_out",
            "state_taxpayer_tax_credit",
            "state_total_tax",
            "state_total_tax_resident_basis",
            "state_flat_rate",
            "state_phase_out_rate",
            "state_base_phase_out",
            "apportionment_fraction",
        ):
            assert k in result.state_specific
            assert isinstance(result.state_specific[k], Decimal)

    def test_state_return_validates(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        rehydrated = StateReturn.model_validate(
            result.model_dump(mode="json")
        )
        assert rehydrated.state == "UT"


# ---------------------------------------------------------------------------
# compute() — nonresident
# ---------------------------------------------------------------------------


class TestUtahPluginComputeNonresident:
    def test_nonresident_half_year_prorates(
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
        assert full == Decimal("2588.23")
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


# ---------------------------------------------------------------------------
# apportion_income / forms / render
# ---------------------------------------------------------------------------


class TestUtahApportionIncome:
    def test_resident_full_amounts(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert isinstance(app, IncomeApportionment)
        assert app.state_source_wages == Decimal("65000.00")


class TestUtahFormIds:
    def test_form_ids(self):
        assert PLUGIN.form_ids() == ["UT Form TC-40"]

    def test_render_pdfs_empty(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        sr = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert PLUGIN.render_pdfs(sr, tmp_path) == []


# ---------------------------------------------------------------------------
# v1 limitations
# ---------------------------------------------------------------------------


class TestUtahV1Limitations:
    def test_limitations_is_tuple(self):
        assert isinstance(UT_V1_LIMITATIONS, tuple)
        assert len(UT_V1_LIMITATIONS) >= 5

    def test_limitations_mention_credit_for_other_state(self):
        joined = " ".join(UT_V1_LIMITATIONS).lower()
        assert "another state" in joined or "other state" in joined

    def test_limitations_mention_tc_40b(self):
        joined = " ".join(UT_V1_LIMITATIONS)
        assert "TC-40B" in joined


# ---------------------------------------------------------------------------
# Gatekeeper — pin tenforty's UT gap (default backend) AND graph drift
# ---------------------------------------------------------------------------


class TestUtahTenfortyGatekeeper:
    """Pin both:
    1. The default OTS backend's "OTS does not support 2025/UT_TC40"
       failure (so we know if tenforty adds UT support).
    2. The graph backend's $1,980 result, which is wrong because it
       skips the Taxpayer Tax Credit phase-out (so we know if tenforty
       fixes the omission).
    """

    def test_tenforty_default_backend_still_raises_for_ut(self):
        with pytest.raises(ValueError, match="OTS does not support"):
            tenforty.evaluate_return(
                year=2025,
                state="UT",
                filing_status="Single",
                w2_income=65000,
                num_dependents=0,
                standard_or_itemized="Standard",
                itemized_deductions=0,
            )

    def test_tenforty_graph_backend_still_diverges_from_dor(self):
        """Pin the WRONG graph result so we detect when tenforty fixes
        the Taxpayer Tax Credit phase-out. When the graph result equals
        our DOR-traced $2,588.23 (or rounds within $5 of it), this test
        fails and the next agent should consider promoting UT to a
        graph wrapper.
        """
        r = tenforty.evaluate_return(
            year=2025,
            state="UT",
            filing_status="Single",
            w2_income=65000,
            num_dependents=0,
            standard_or_itemized="Standard",
            itemized_deductions=0,
            backend="graph",
        )
        graph_value = Decimal(str(r.state_total_tax)).quantize(
            Decimal("0.01")
        )
        # Graph value at the time of writing: $1,980.00
        assert graph_value == Decimal("1980.00")
        # Document the magnitude of the divergence (~$608)
        plugin_value = Decimal("2588.23")
        delta = abs(graph_value - plugin_value)
        assert delta > Decimal("5"), (
            f"Graph backend now agrees with DOR (delta={delta} <= $5). "
            f"Consider converting ut.py to a graph-wrapper plugin."
        )
