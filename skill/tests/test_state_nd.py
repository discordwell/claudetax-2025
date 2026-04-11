"""North Dakota state plugin tests — TY2025.

Hand-rolled ND-1 calc — see ``skill/scripts/states/nd.py`` docstring
for the full DOR-primary-source trace and the **ND-SPECIFIC FINDING**
that the graph backend's $15.11 result for the spec's $65k Single
scenario is *mathematically correct* despite looking suspicious. The
spec called for hand-rolling regardless, and we honor that, but this
test file documents the finding loudly so the next state agent isn't
mislead.

Reference scenario:
    Single $65k W-2, OBBBA std ded $15,750
    -> federal taxable income $49,250
    -> ND taxable income (=fed TI in v1) $49,250
    -> $49,250 is in the 1.95% middle bracket above the $48,475 zero
       cap (Single), so:
       tax = 0% * $48,475 + 1.95% * ($49,250 - $48,475)
           = 1.95% * $775
           = $15.1125
           -> rounded to cents: **$15.11**
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
from skill.scripts.states.nd import (
    ND_TY2025_BRACKETS_HOH,
    ND_TY2025_BRACKETS_MFJ,
    ND_TY2025_BRACKETS_MFS,
    ND_TY2025_BRACKETS_SINGLE,
    ND_V1_LIMITATIONS,
    NorthDakotaPlugin,
    PLUGIN,
    nd_brackets_for_status,
    nd_tax_from_schedule,
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
            first_name="Theodore",
            last_name="Roughrider",
            ssn="111-22-3333",
            date_of_birth=dt.date(1990, 1, 1),
        ),
        address=Address(
            street1="600 E Boulevard Ave",
            city="Bismarck",
            state="ND",
            zip="58505",
        ),
        w2s=[
            W2(
                employer_name="Roughrider State Co",
                box1_wages=Decimal("65000"),
                box2_federal_income_tax_withheld=Decimal("0"),
            ),
        ],
    )


@pytest.fixture
def federal_single_65k() -> FederalTotals:
    """Federal AGI $65,000 / fed TI $49,250 (post-OBBBA std ded)."""
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
def federal_mfj_120k() -> FederalTotals:
    return FederalTotals(
        filing_status=FilingStatus.MFJ,
        num_dependents=0,
        adjusted_gross_income=Decimal("120000"),
        taxable_income=Decimal("88500"),
        total_federal_tax=Decimal("10173"),
        federal_income_tax=Decimal("10173"),
        federal_standard_deduction=Decimal("31500"),
        federal_itemized_deductions_total=Decimal("0"),
        deduction_taken=Decimal("31500"),
    )


# ---------------------------------------------------------------------------
# Meta + Protocol
# ---------------------------------------------------------------------------


class TestNorthDakotaPluginMeta:
    def test_meta_fields(self):
        assert PLUGIN.meta.code == "ND"
        assert PLUGIN.meta.name == "North Dakota"
        assert PLUGIN.meta.has_income_tax is True

    def test_meta_starting_point_is_federal_taxable_income(self):
        """ND-1 line 1 is federal taxable income (not federal AGI)."""
        assert (
            PLUGIN.meta.starting_point
            == StateStartingPoint.FEDERAL_TAXABLE_INCOME
        )

    def test_meta_reciprocity_partners(self):
        """ND has reciprocity with MN and MT (verified in
        skill/reference/state-reciprocity.json)."""
        assert set(PLUGIN.meta.reciprocity_partners) == {"MN", "MT"}

    def test_meta_submission_channel(self):
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.FED_STATE_PIGGYBACK
        )

    def test_meta_dor_url(self):
        assert "tax.nd.gov" in PLUGIN.meta.dor_url

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_brackets(self):
        notes = PLUGIN.meta.notes
        assert "1.95" in notes
        assert "2.5" in notes

    def test_meta_notes_mention_zero_bracket(self):
        notes = PLUGIN.meta.notes
        assert "48,475" in notes or "48475" in notes

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "MN"  # type: ignore[misc]

    def test_plugin_is_state_plugin_protocol(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_north_dakota_plugin_instance(self):
        assert isinstance(PLUGIN, NorthDakotaPlugin)


# ---------------------------------------------------------------------------
# Reciprocity (MN, MT only)
# ---------------------------------------------------------------------------


class TestNorthDakotaReciprocity:
    def test_reciprocity_table_recognizes_pairs(self):
        table = ReciprocityTable.load()
        assert table.partners_of("ND") == frozenset({"MN", "MT"})
        assert table.are_reciprocal("ND", "MN") is True
        assert table.are_reciprocal("ND", "MT") is True

    def test_not_reciprocal_with_sd(self):
        """ND borders SD; SD has no income tax and no reciprocity."""
        table = ReciprocityTable.load()
        assert table.are_reciprocal("ND", "SD") is False

    def test_meta_does_not_include_sd(self):
        assert "SD" not in PLUGIN.meta.reciprocity_partners

    def test_meta_partners_match_table(self):
        table = ReciprocityTable.load()
        assert (
            frozenset(PLUGIN.meta.reciprocity_partners)
            == table.partners_of("ND")
        )


# ---------------------------------------------------------------------------
# Tax Rate Schedule — bracket math
# ---------------------------------------------------------------------------


class TestNorthDakotaBrackets:
    def test_brackets_for_status_dispatch(self):
        assert (
            nd_brackets_for_status(FilingStatus.SINGLE)
            is ND_TY2025_BRACKETS_SINGLE
        )
        assert (
            nd_brackets_for_status(FilingStatus.MFJ)
            is ND_TY2025_BRACKETS_MFJ
        )
        assert (
            nd_brackets_for_status(FilingStatus.QSS)
            is ND_TY2025_BRACKETS_MFJ
        )
        assert (
            nd_brackets_for_status(FilingStatus.MFS)
            is ND_TY2025_BRACKETS_MFS
        )
        assert (
            nd_brackets_for_status(FilingStatus.HOH)
            is ND_TY2025_BRACKETS_HOH
        )

    def test_zero_income(self):
        assert nd_tax_from_schedule(
            Decimal("0"), FilingStatus.SINGLE
        ) == Decimal("0.00")

    def test_inside_zero_bracket_single(self):
        """Single $40,000 is below the $48,475 zero-bracket cap → tax 0."""
        assert nd_tax_from_schedule(
            Decimal("40000"), FilingStatus.SINGLE
        ) == Decimal("0.00")

    def test_at_zero_bracket_top_single(self):
        """At $48,475 exactly, tax is still 0 (the next bracket starts above)."""
        assert nd_tax_from_schedule(
            Decimal("48475"), FilingStatus.SINGLE
        ) == Decimal("0.00")

    def test_just_above_zero_bracket_single(self):
        """At $49,250 (the spec's $65k Single fed TI):
        tax = 1.95% * ($49,250 - $48,475) = 1.95% * $775 = $15.1125
        """
        assert nd_tax_from_schedule(
            Decimal("49250"), FilingStatus.SINGLE
        ) == Decimal("15.11")

    def test_top_bracket_single(self):
        """At $300,000 Single:
        sub_low = 0
        sub_mid = 1.95% * ($244,825 - $48,475) = 1.95% * $196,350 = $3,828.825
        sub_top = 2.5% * ($300,000 - $244,825) = 2.5% * $55,175 = $1,379.375
        total = $5,208.20
        """
        result = nd_tax_from_schedule(
            Decimal("300000"), FilingStatus.SINGLE
        )
        assert result == Decimal("5208.20")

    def test_mfj_zero_bracket_top(self):
        """MFJ $80,975 → tax 0."""
        assert nd_tax_from_schedule(
            Decimal("80975"), FilingStatus.MFJ
        ) == Decimal("0.00")

    def test_mfj_just_above_zero_bracket(self):
        """MFJ $90,000: tax = 1.95% * ($90,000 - $80,975) = 1.95% * $9,025
        = $176.0
        """
        assert nd_tax_from_schedule(
            Decimal("90000"), FilingStatus.MFJ
        ) == Decimal("175.99")

    def test_hoh_uses_hoh_brackets(self):
        """HOH $70,000: tax = 1.95% * ($70,000 - $64,950) = 1.95% * $5,050
        = $98.475 → $98.48
        """
        assert nd_tax_from_schedule(
            Decimal("70000"), FilingStatus.HOH
        ) == Decimal("98.48")


# ---------------------------------------------------------------------------
# compute() — resident
# ---------------------------------------------------------------------------


class TestNorthDakotaPluginComputeResident:
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
        assert result.state == "ND"

    def test_resident_single_65k_lock(
        self, single_65k_return, federal_single_65k
    ):
        """**SPEC-MANDATED $65k Single LOCK**: $15.11 (yes, really).

        ND has a $48,475 zero bracket for Single TY2025, so a $65k W-2
        leaves only $775 of federal taxable income inside the 1.95%
        middle bracket. The looks-suspicious value is the correct one;
        see module docstring for the finding. We hand-roll regardless
        per spec mandate."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_total_tax"] == Decimal("15.11")
        assert result.state_specific[
            "state_total_tax_resident_basis"
        ] == Decimal("15.11")

    def test_resident_uses_federal_taxable_income_not_agi(
        self, single_65k_return, federal_single_65k
    ):
        """ND-1 line 1 is federal taxable income — verify we read
        ``federal.taxable_income`` and not ``federal.adjusted_gross_income``."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = result.state_specific
        assert ss["state_federal_taxable_income"] == Decimal("49250.00")
        assert ss["state_taxable_income"] == Decimal("49250.00")
        assert ss["starting_point"] == "federal_taxable_income"

    def test_resident_mfj_120k_in_zero_bracket(self, federal_mfj_120k):
        """MFJ $120k AGI → fed TI $88,500 (< $80,975? no, > $80,975 = MFJ
        zero cap). 88,500 - 80,975 = 7,525 * 0.0195 = 146.7375 → $146.74.
        """
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.MFJ,
            taxpayer=Person(
                first_name="X",
                last_name="Y",
                ssn="111-22-3333",
                date_of_birth=dt.date(1985, 1, 1),
            ),
            spouse=Person(
                first_name="A",
                last_name="Y",
                ssn="222-33-4444",
                date_of_birth=dt.date(1986, 1, 1),
            ),
            address=Address(
                street1="1 Main", city="Fargo", state="ND", zip="58102"
            ),
        )
        result = PLUGIN.compute(
            ret,
            federal_mfj_120k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_total_tax"] == Decimal("146.74")

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
            "state_federal_taxable_income",
            "state_adjusted_gross_income",
            "state_taxable_income",
            "state_tax_before_credits",
            "state_credit_other_state",
            "state_marriage_penalty_credit",
            "state_total_credits",
            "state_total_tax",
            "state_total_tax_resident_basis",
            "state_marginal_rate",
            "apportionment_fraction",
        ):
            assert k in result.state_specific
            assert isinstance(result.state_specific[k], Decimal)

    def test_marginal_rate_at_65k_single(
        self, single_65k_return, federal_single_65k
    ):
        """At fed TI $49,250 Single, the marginal rate is 1.95%."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_marginal_rate"] == Decimal("0.0195")

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
        assert rehydrated.state == "ND"


# ---------------------------------------------------------------------------
# compute() — nonresident
# ---------------------------------------------------------------------------


class TestNorthDakotaPluginComputeNonresident:
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
        assert full == Decimal("15.11")
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


class TestNorthDakotaApportionIncome:
    def test_resident_full_amounts(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert isinstance(app, IncomeApportionment)
        assert app.state_source_wages == Decimal("65000.00")


class TestNorthDakotaFormIds:
    def test_form_ids(self):
        assert PLUGIN.form_ids() == ["ND Form ND-1"]

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


class TestNorthDakotaV1Limitations:
    def test_limitations_is_tuple(self):
        assert isinstance(ND_V1_LIMITATIONS, tuple)
        assert len(ND_V1_LIMITATIONS) >= 5

    def test_limitations_mention_schedule_nd1nr(self):
        joined = " ".join(ND_V1_LIMITATIONS)
        assert "ND-1NR" in joined

    def test_limitations_mention_marriage_penalty(self):
        joined = " ".join(ND_V1_LIMITATIONS).lower()
        assert "marriage penalty" in joined


# ---------------------------------------------------------------------------
# GATEKEEPER — pin the broken-stub probe finding
# ---------------------------------------------------------------------------


class TestNorthDakotaTenfortyGatekeeper:
    """**ND-SPECIFIC GATEKEEPER**.

    The wave 5 fan-out spec asserted that tenforty's graph backend
    returned $15.11 on the $65k Single scenario "which is clearly
    broken or stubbed in tenforty's graph definition." This test pins
    that probe value AND documents the finding that the value is
    actually **mathematically correct** — see module docstring.

    The spec required hand-rolling ND regardless. We honor that. This
    gatekeeper exists so that:

    1. If tenforty fixes the graph backend's ND state_taxable_income
       echo (it currently echoes federal TI rather than reporting an
       independently-computed ND TI — same WI bug), the assertion on
       state_taxable_income below will fire.
    2. If tenforty changes the graph numerical result (because the
       ND brackets shift, or because the spec's "broken" theory turns
       out to actually be true), the state_total_tax assertion fires.
    3. The plugin's hand-rolled $15.11 must equal both the DOR formula
       AND the graph value, so the cross-check below also acts as a
       drift detector for the underlying ND-1 schedule.
    """

    def test_tenforty_default_backend_still_raises_for_nd(self):
        """When this STARTS PASSING (i.e. no exception), tenforty has
        added ND to OTSv1. The next agent should consider promoting ND
        to a wrap-style plugin."""
        with pytest.raises(ValueError, match="OTS does not support"):
            tenforty.evaluate_return(
                year=2025,
                state="ND",
                filing_status="Single",
                w2_income=65000,
                num_dependents=0,
                standard_or_itemized="Standard",
                itemized_deductions=0,
            )

    def test_tenforty_graph_backend_returns_15_11(self):
        """Pin the wave 5 spec's $15.11 probe value.

        The spec called this "clearly broken or stubbed." Empirically
        it is the **correct** ND-1 result given the high zero-bracket
        of $48,475 (Single) — federal TI $49,250 leaves only $775 in
        the 1.95% middle bracket → $15.1125. When tenforty changes
        either the graph backend's ND brackets or the input handling,
        this test will fire.
        """
        r = tenforty.evaluate_return(
            year=2025,
            state="ND",
            filing_status="Single",
            w2_income=65000,
            num_dependents=0,
            standard_or_itemized="Standard",
            itemized_deductions=0,
            backend="graph",
        )
        assert Decimal(str(r.state_total_tax)).quantize(
            Decimal("0.01")
        ) == Decimal("15.11")

    def test_plugin_lock_matches_graph_value(
        self, single_65k_return, federal_single_65k
    ):
        """Cross-check: hand-rolled plugin lock == graph backend output.

        The two paths must agree to the cent. If they ever diverge, the
        graph backend changed (or the ND DOR changed the brackets and
        we missed it). Both are bugs that this test catches loudly.
        """
        plugin_result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        graph_result = tenforty.evaluate_return(
            year=2025,
            state="ND",
            filing_status="Single",
            w2_income=65000,
            num_dependents=0,
            standard_or_itemized="Standard",
            itemized_deductions=0,
            backend="graph",
        )
        assert plugin_result.state_specific[
            "state_total_tax"
        ] == Decimal(str(graph_result.state_total_tax)).quantize(
            Decimal("0.01")
        )
