"""Washington state plugin tests.

WA has no broad income tax but taxes long-term capital gains above a TY2025
standard deduction of $278,000 at 7%. Source:
https://dor.wa.gov/taxes-rates/other-taxes/capital-gains-tax (verified
2026-04-10).
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
    Form1099B,
    Form1099BTransaction,
    Form1099DIV,
    Person,
    ResidencyStatus,
)
from skill.scripts.states._plugin_api import (
    FederalTotals,
    IncomeApportionment,
    StatePlugin,
    StatePluginMeta,
    StateStartingPoint,
    SubmissionChannel,
)
from skill.scripts.states.wa import (
    PLUGIN,
    WA_LTCG_EXEMPT_THRESHOLD_TY2025,
    WA_LTCG_RATE,
    WashingtonPlugin,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _person() -> Person:
    return Person(
        first_name="Rainier",
        last_name="Stevens",
        ssn="111-22-3333",
        date_of_birth=dt.date(1985, 6, 15),
    )


def _address() -> Address:
    # Seattle ZIP; resident in WA.
    return Address(
        street1="1 Pike Place",
        city="Seattle",
        state="WA",
        zip="98101",
    )


def _federal() -> FederalTotals:
    return FederalTotals(
        filing_status=FilingStatus.SINGLE,
        num_dependents=0,
        adjusted_gross_income=Decimal("500000"),
        taxable_income=Decimal("484250"),
        total_federal_tax=Decimal("130000"),
        federal_income_tax=Decimal("130000"),
        federal_standard_deduction=Decimal("15750"),
        federal_itemized_deductions_total=Decimal("0"),
        deduction_taken=Decimal("15750"),
        federal_withholding_from_w2s=Decimal("0"),
    )


def _return_with_no_capital_gains() -> CanonicalReturn:
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=_person(),
        address=_address(),
    )


def _ltcg_1099b(proceeds: Decimal, basis: Decimal) -> Form1099B:
    return Form1099B(
        broker_name="Evergreen Brokerage",
        transactions=[
            Form1099BTransaction(
                description="100 sh ACME",
                date_acquired=dt.date(2020, 1, 15),
                date_sold=dt.date(2025, 6, 1),
                proceeds=proceeds,
                cost_basis=basis,
                is_long_term=True,
            )
        ],
    )


def _stcg_1099b(proceeds: Decimal, basis: Decimal) -> Form1099B:
    return Form1099B(
        broker_name="Evergreen Brokerage",
        transactions=[
            Form1099BTransaction(
                description="100 sh FLIPR",
                date_acquired=dt.date(2025, 2, 15),
                date_sold=dt.date(2025, 6, 1),
                proceeds=proceeds,
                cost_basis=basis,
                is_long_term=False,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Protocol + meta
# ---------------------------------------------------------------------------


class TestWashingtonPluginMeta:
    def test_protocol_satisfied_at_runtime(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_washington_plugin_instance(self):
        assert isinstance(PLUGIN, WashingtonPlugin)

    def test_meta_shape(self):
        meta = PLUGIN.meta
        assert isinstance(meta, StatePluginMeta)
        assert meta.code == "WA"
        assert meta.name == "Washington"
        # WA has no BROAD income tax — cap gains only.
        assert meta.has_income_tax is False
        assert meta.starting_point == StateStartingPoint.NONE
        assert meta.dor_url == "https://dor.wa.gov/"
        assert meta.free_efile_url is not None
        assert "dor.wa.gov" in meta.free_efile_url
        assert meta.submission_channel == SubmissionChannel.STATE_DOR_FREE_PORTAL
        assert meta.reciprocity_partners == ()
        assert 2025 in meta.supported_tax_years
        assert "278" in meta.notes  # mentions the TY2025 threshold

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "OR"  # type: ignore[misc]

    def test_module_constants(self):
        assert WA_LTCG_RATE == Decimal("0.07")
        assert WA_LTCG_EXEMPT_THRESHOLD_TY2025 == Decimal("278000")


# ---------------------------------------------------------------------------
# compute()
# ---------------------------------------------------------------------------


class TestWashingtonCompute:
    def test_resident_no_capital_gains_owes_zero(self):
        return_ = _return_with_no_capital_gains()
        state_return = PLUGIN.compute(
            return_, _federal(), ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert state_return.state == "WA"
        assert state_return.residency == ResidencyStatus.RESIDENT
        spec = state_return.state_specific
        assert spec["state_total_tax"] == Decimal("0")
        assert spec["total_ltcg"] == Decimal("0")
        assert spec["taxable_ltcg"] == Decimal("0")
        assert spec["exempt_threshold"] == WA_LTCG_EXEMPT_THRESHOLD_TY2025
        assert spec["rate"] == WA_LTCG_RATE

    def test_resident_ltcg_below_threshold_owes_zero(self):
        # $200k LTCG — below the $278k TY2025 standard deduction.
        return_ = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(),
            address=_address(),
            forms_1099_b=[_ltcg_1099b(Decimal("250000"), Decimal("50000"))],
        )
        state_return = PLUGIN.compute(
            return_, _federal(), ResidencyStatus.RESIDENT, days_in_state=365
        )
        spec = state_return.state_specific
        assert spec["total_ltcg"] == Decimal("200000")
        assert spec["state_total_tax"] == Decimal("0")
        assert spec["taxable_ltcg"] == Decimal("0")

    def test_resident_ltcg_exactly_at_threshold_owes_zero(self):
        return_ = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(),
            address=_address(),
            forms_1099_b=[_ltcg_1099b(Decimal("400000"), Decimal("122000"))],
        )
        state_return = PLUGIN.compute(
            return_, _federal(), ResidencyStatus.RESIDENT, days_in_state=365
        )
        spec = state_return.state_specific
        assert spec["total_ltcg"] == WA_LTCG_EXEMPT_THRESHOLD_TY2025
        assert spec["state_total_tax"] == Decimal("0")

    def test_resident_ltcg_well_above_threshold_owes_seven_percent(self):
        # $1,000,000 LTCG → taxable = 1,000,000 - 278,000 = 722,000
        # tax = 722,000 * 0.07 = 50,540
        return_ = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(),
            address=_address(),
            forms_1099_b=[_ltcg_1099b(Decimal("1200000"), Decimal("200000"))],
        )
        state_return = PLUGIN.compute(
            return_, _federal(), ResidencyStatus.RESIDENT, days_in_state=365
        )
        spec = state_return.state_specific
        assert spec["total_ltcg"] == Decimal("1000000")
        assert spec["taxable_ltcg"] == Decimal("722000")
        assert spec["state_total_tax"] == Decimal("50540.00")
        assert spec["rate"] == Decimal("0.07")
        assert spec["exempt_threshold"] == Decimal("278000")

    def test_short_term_gains_do_not_trigger_wa_tax(self):
        # Massive short-term gain — not long-term, so WA does NOT tax it.
        return_ = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(),
            address=_address(),
            forms_1099_b=[_stcg_1099b(Decimal("1500000"), Decimal("200000"))],
        )
        state_return = PLUGIN.compute(
            return_, _federal(), ResidencyStatus.RESIDENT, days_in_state=365
        )
        spec = state_return.state_specific
        assert spec["total_ltcg"] == Decimal("0")
        assert spec["state_total_tax"] == Decimal("0")

    def test_mixed_short_and_long_only_long_counts(self):
        return_ = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(),
            address=_address(),
            forms_1099_b=[
                _stcg_1099b(Decimal("500000"), Decimal("100000")),  # $400k STCG ignored
                _ltcg_1099b(Decimal("900000"), Decimal("400000")),  # $500k LTCG
            ],
        )
        state_return = PLUGIN.compute(
            return_, _federal(), ResidencyStatus.RESIDENT, days_in_state=365
        )
        spec = state_return.state_specific
        assert spec["total_ltcg"] == Decimal("500000")
        # 500,000 - 278,000 = 222,000 * 0.07 = 15,540
        assert spec["taxable_ltcg"] == Decimal("222000")
        assert spec["state_total_tax"] == Decimal("15540.00")

    def test_1099_div_cap_gain_distributions_are_long_term(self):
        # Box 2a cap gain distributions from mutual funds are always long-term.
        return_ = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(),
            address=_address(),
            forms_1099_div=[
                Form1099DIV(
                    payer_name="Mount Rainier Fund",
                    box2a_total_capital_gain_distributions=Decimal("400000"),
                )
            ],
        )
        state_return = PLUGIN.compute(
            return_, _federal(), ResidencyStatus.RESIDENT, days_in_state=365
        )
        spec = state_return.state_specific
        assert spec["total_ltcg"] == Decimal("400000")
        # 400,000 - 278,000 = 122,000 * 0.07 = 8,540
        assert spec["taxable_ltcg"] == Decimal("122000")
        assert spec["state_total_tax"] == Decimal("8540.00")

    def test_adjustment_amount_applied_to_ltcg(self):
        # Wash sale / adjustment increases gain.
        return_ = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(),
            address=_address(),
            forms_1099_b=[
                Form1099B(
                    broker_name="Cascade",
                    transactions=[
                        Form1099BTransaction(
                            description="LTCG with adj",
                            date_acquired=dt.date(2018, 1, 1),
                            date_sold=dt.date(2025, 3, 1),
                            proceeds=Decimal("1000000"),
                            cost_basis=Decimal("200000"),
                            adjustment_amount=Decimal("100000"),
                            is_long_term=True,
                        )
                    ],
                )
            ],
        )
        state_return = PLUGIN.compute(
            return_, _federal(), ResidencyStatus.RESIDENT, days_in_state=365
        )
        spec = state_return.state_specific
        # 1,000,000 - 200,000 + 100,000 = 900,000
        assert spec["total_ltcg"] == Decimal("900000")
        # 900,000 - 278,000 = 622,000 * 0.07 = 43,540
        assert spec["taxable_ltcg"] == Decimal("622000")
        assert spec["state_total_tax"] == Decimal("43540.00")

    def test_nonresident_owes_zero_with_todo_flag(self):
        # RCW 82.87.100: intangibles are sourced only to domicile state. A
        # nonresident with stock gains owes WA nothing. This test locks in the
        # MVP simplification: nonresident → 0 with a flag for the follow-up
        # sourcing work.
        return_ = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(),
            address=Address(street1="1 Elsewhere", city="Portland", state="OR", zip="97201"),
            forms_1099_b=[_ltcg_1099b(Decimal("2000000"), Decimal("100000"))],
        )
        state_return = PLUGIN.compute(
            return_, _federal(), ResidencyStatus.NONRESIDENT, days_in_state=0
        )
        spec = state_return.state_specific
        assert state_return.residency == ResidencyStatus.NONRESIDENT
        assert spec["state_total_tax"] == Decimal("0")
        assert spec.get("nonresident_sourcing_todo") is True


# ---------------------------------------------------------------------------
# apportion_income()
# ---------------------------------------------------------------------------


class TestWashingtonApportion:
    def test_apportion_only_fills_capital_gains(self):
        return_ = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(),
            address=_address(),
            forms_1099_b=[_ltcg_1099b(Decimal("500000"), Decimal("100000"))],
            forms_1099_div=[
                Form1099DIV(
                    payer_name="Mt. Baker Fund",
                    box2a_total_capital_gain_distributions=Decimal("50000"),
                )
            ],
        )
        apportionment = PLUGIN.apportion_income(
            return_, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert isinstance(apportionment, IncomeApportionment)
        assert apportionment.state_source_wages == Decimal("0")
        assert apportionment.state_source_interest == Decimal("0")
        assert apportionment.state_source_dividends == Decimal("0")
        assert apportionment.state_source_self_employment == Decimal("0")
        assert apportionment.state_source_rental == Decimal("0")
        # LTCG from broker ($400k) + cap gain distribution ($50k) = $450k
        assert apportionment.state_source_capital_gains == Decimal("450000")

    def test_apportion_nonresident_zero(self):
        return_ = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(),
            address=Address(street1="1 Elsewhere", city="Portland", state="OR", zip="97201"),
            forms_1099_b=[_ltcg_1099b(Decimal("2000000"), Decimal("100000"))],
        )
        apportionment = PLUGIN.apportion_income(
            return_, ResidencyStatus.NONRESIDENT, days_in_state=0
        )
        assert apportionment.state_source_capital_gains == Decimal("0")
        assert apportionment.state_source_total == Decimal("0")


# ---------------------------------------------------------------------------
# render_pdfs / form_ids
# ---------------------------------------------------------------------------


class TestWashingtonOutputs:
    def test_render_pdfs_returns_empty(self, tmp_path: Path):
        # MVP: My DOR portal is the submission channel — no paper PDFs produced.
        return_ = _return_with_no_capital_gains()
        state_return = PLUGIN.compute(
            return_, _federal(), ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert PLUGIN.render_pdfs(state_return, tmp_path) == []

    def test_form_ids_returns_wa_capital_gains_return(self):
        ids = PLUGIN.form_ids()
        assert isinstance(ids, list)
        assert "WA Capital Gains Excise Tax Return" in ids
