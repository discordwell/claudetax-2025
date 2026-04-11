"""Colorado state plugin tests.

Covers the ColoradoPlugin hand-rolled CO Form DR 0104 calc. Colorado is
NOT tenforty-backed (``OTS does not support 2025/CO_Form104``) so every
number here is computed in-plugin from the permanent flat rate of 4.40%.

TY2025 flat rate = 4.40% (the permanent statutory rate under
C.R.S. §39-22-104(1.7)). The TABOR temporary rate reduction mechanism
(C.R.S. §39-22-627) did NOT trigger for TY2025 because the remaining
excess state revenues after the property tax exemption reimbursement
(~$111.2M) fell below the $300M threshold required to activate the
income tax rate reduction. Source: Colorado OSA "Schedule of TABOR
Revenue — Fiscal Year 2025 Performance Audit" (October 2025, report
2557P), pages 18-19.
https://content.leg.colorado.gov/sites/default/files/documents/audits/2557p_schedule_of_tabor_revenue_fy_25.pdf

Test structure mirrors ``test_state_pa.py``. Lock in:

- ``starting_point == FEDERAL_TAXABLE_INCOME`` (DR 0104 line 1).
- ``reciprocity_partners == ()`` — CO has no bilateral reciprocity
  agreements; verified against ``state-reciprocity.json``.
- Single $65k AGI / std ded $15,750 / taxable $49,250 → CO tax =
  49250 * 0.044 = $2,167.00.
- MFJ $120k AGI / std ded $31,500 / taxable $88,500 → CO tax =
  88500 * 0.044 = $3,894.00.
- Nonresident half-year day-proration is exactly
  ``resident_tax * days_in_state / 365`` (stub — the real DR 0104PN
  ratio is fan-out follow-up).
- ``v1_limitations`` is a non-empty list (enumerates CO adjustments
  we do not model).
- ``tabor_refund_deferred is True`` on ``state_specific`` (DR 0104
  lines 34-38 six-tier refund is deferred).
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
from skill.scripts.states.co import (
    CO_TY2025_FLAT_RATE,
    CO_V1_LIMITATIONS,
    ColoradoPlugin,
    PLUGIN,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def single_65k_return() -> CanonicalReturn:
    """A Single $65k W-2 CO resident."""
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=Person(
            first_name="Rocky",
            last_name="Mountain",
            ssn="111-22-3333",
            date_of_birth=dt.date(1990, 1, 1),
        ),
        address=Address(
            street1="100 16th St",
            city="Denver",
            state="CO",
            zip="80202",
        ),
        w2s=[
            W2(
                employer_name="Summit Corp",
                box1_wages=Decimal("65000"),
                box2_federal_income_tax_withheld=Decimal("0"),
            ),
        ],
    )


@pytest.fixture
def federal_single_65k() -> FederalTotals:
    """$65k AGI Single / OBBBA standard deduction / $49,250 taxable."""
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


@pytest.fixture
def mfj_120k_return() -> CanonicalReturn:
    """A MFJ $120k W-2 CO resident couple."""
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.MFJ,
        taxpayer=Person(
            first_name="Alex",
            last_name="Bluebird",
            ssn="111-22-3333",
            date_of_birth=dt.date(1985, 3, 15),
        ),
        spouse=Person(
            first_name="Jordan",
            last_name="Bluebird",
            ssn="222-33-4444",
            date_of_birth=dt.date(1986, 7, 4),
        ),
        address=Address(
            street1="500 Pearl St",
            city="Boulder",
            state="CO",
            zip="80302",
        ),
        w2s=[
            W2(
                employer_name="Flatirons Inc",
                box1_wages=Decimal("120000"),
                box2_federal_income_tax_withheld=Decimal("0"),
            ),
        ],
    )


@pytest.fixture
def federal_mfj_120k() -> FederalTotals:
    """$120k AGI MFJ / OBBBA standard deduction / $88,500 taxable."""
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
        federal_withholding_from_w2s=Decimal("0"),
    )


# ---------------------------------------------------------------------------
# Meta + Protocol conformance
# ---------------------------------------------------------------------------


class TestColoradoPluginMeta:
    """Meta-field assertions the task spec calls out explicitly."""

    def test_meta_fields(self):
        """Single consolidated meta assertion per task spec."""
        assert PLUGIN.meta.code == "CO"
        assert PLUGIN.meta.has_income_tax is True
        assert (
            PLUGIN.meta.starting_point
            == StateStartingPoint.FEDERAL_TAXABLE_INCOME
        )
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_meta_name(self):
        assert PLUGIN.meta.name == "Colorado"

    def test_meta_submission_channel_is_state_dor_free_portal(self):
        """CO has Revenue Online as its free state DOR portal."""
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )

    def test_meta_dor_url_is_tax_colorado_gov(self):
        assert "tax.colorado.gov" in PLUGIN.meta.dor_url

    def test_meta_free_efile_url_is_revenue_online(self):
        """CO's free e-file portal is Revenue Online."""
        assert PLUGIN.meta.free_efile_url is not None
        assert "colorado.gov" in PLUGIN.meta.free_efile_url.lower()

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_flat_rate(self):
        """Notes should mention 4.40% so downstream readers can sanity-check."""
        assert "4.40" in PLUGIN.meta.notes

    def test_meta_notes_mention_tabor(self):
        """Notes should flag the TABOR temporary rate reduction mechanism."""
        assert "TABOR" in PLUGIN.meta.notes

    def test_meta_is_frozen(self):
        """StatePluginMeta is frozen — mutation raises."""
        with pytest.raises(Exception):
            PLUGIN.meta.code = "CA"  # type: ignore[misc]

    def test_plugin_is_state_plugin_protocol(self):
        """runtime_checkable Protocol must recognize our concrete plugin."""
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_colorado_plugin_instance(self):
        assert isinstance(PLUGIN, ColoradoPlugin)


# ---------------------------------------------------------------------------
# No reciprocity agreements — verified via the reciprocity table
# ---------------------------------------------------------------------------


class TestColoradoNoReciprocity:
    """CO has no bilateral reciprocity agreements with any other state."""

    def test_no_reciprocity_partners(self):
        """Meta reciprocity_partners is the empty tuple."""
        assert PLUGIN.meta.reciprocity_partners == ()
        assert len(PLUGIN.meta.reciprocity_partners) == 0

    def test_no_reciprocity_via_reciprocity_table(self):
        """The shared ReciprocityTable also reports no partners for CO."""
        table = ReciprocityTable.load()
        assert table.partners_of("CO") == frozenset()
        assert table.has_income_tax("CO") is True

    def test_not_reciprocal_with_neighbors(self):
        """Spot-check that CO is not reciprocal with any of its neighbors."""
        table = ReciprocityTable.load()
        for neighbor in ("WY", "NE", "KS", "OK", "NM", "UT", "AZ"):
            assert table.are_reciprocal("CO", neighbor) is False


# ---------------------------------------------------------------------------
# Resident compute() — single $65k and MFJ $120k
# ---------------------------------------------------------------------------


class TestColoradoPluginResident:
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
        assert result.state == "CO"
        assert result.residency == ResidencyStatus.RESIDENT
        assert result.days_in_state == 365

    def test_resident_single_65k_standard_deduction(
        self, single_65k_return, federal_single_65k
    ):
        """Single $65k AGI, OBBBA std ded $15,750, taxable $49,250.

        CO tax = $49,250 * 4.40% = $2,167.00 exactly.
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        # 49250 * 0.044 = 2167.00
        assert state_tax == Decimal("2167.00")

    def test_resident_single_65k_base_income_equals_federal_taxable(
        self, single_65k_return, federal_single_65k
    ):
        """v1 approximation: CO base income = federal taxable income."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_base_income_approx"] == Decimal(
            "49250.00"
        )

    def test_resident_mfj_120k_standard_deduction(
        self, mfj_120k_return, federal_mfj_120k
    ):
        """MFJ $120k AGI, OBBBA std ded $31,500, taxable $88,500.

        CO tax = $88,500 * 4.40% = $3,894.00 exactly.
        """
        result = PLUGIN.compute(
            mfj_120k_return,
            federal_mfj_120k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        # 88500 * 0.044 = 3894.00
        assert state_tax == Decimal("3894.00")

    def test_resident_mfj_120k_base_income_is_88500(
        self, mfj_120k_return, federal_mfj_120k
    ):
        result = PLUGIN.compute(
            mfj_120k_return,
            federal_mfj_120k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_base_income_approx"] == Decimal(
            "88500.00"
        )

    def test_flat_rate_in_state_specific(
        self, single_65k_return, federal_single_65k
    ):
        """state_specific surfaces the TY2025 flat rate."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["flat_rate"] == CO_TY2025_FLAT_RATE
        assert result.state_specific["flat_rate"] == Decimal("0.044")

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

    def test_resident_tax_equals_resident_basis(
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
        """Round-trip through Pydantic JSON to confirm StateReturn validates."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        dumped = result.model_dump(mode="json")
        rehydrated = StateReturn.model_validate(dumped)
        assert rehydrated.state == "CO"
        assert rehydrated.residency == ResidencyStatus.RESIDENT


# ---------------------------------------------------------------------------
# Nonresident / part-year compute() — day-proration stub
# ---------------------------------------------------------------------------


class TestColoradoPluginNonresident:
    def test_nonresident_half_year_prorates(
        self, single_65k_return, federal_single_65k
    ):
        """NONRESIDENT with days_in_state=182 should yield exactly
        resident_tax * 182/365 via day-based proration.

        Fan-out TODO: real DR 0104PN income-source ratio.
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=182,
        )
        full = result.state_specific["state_total_tax_resident_basis"]
        apportioned = result.state_specific["state_total_tax"]
        assert apportioned < full
        expected = (full * Decimal(182) / Decimal(365)).quantize(
            Decimal("0.01")
        )
        assert apportioned == expected

    def test_nonresident_half_year_approximately_half(
        self, single_65k_return, federal_single_65k
    ):
        """Sanity: 182/365 * $2,167.00 ~ $1,080.28, should be in (1000, 1100)."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=182,
        )
        tax = result.state_specific["state_total_tax"]
        assert Decimal("1000") < tax < Decimal("1100")

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
        expected = Decimal(91) / Decimal(365)
        assert result.state_specific["apportionment_fraction"] == expected

    def test_nonresident_zero_days_yields_zero_tax(
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
        """365-day nonresident proration equals full resident tax (boundary)."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=365,
        )
        assert (
            result.state_specific["state_total_tax"]
            == result.state_specific["state_total_tax_resident_basis"]
        )


# ---------------------------------------------------------------------------
# apportion_income()
# ---------------------------------------------------------------------------


class TestColoradoApportionIncome:
    def test_apportion_income_resident(self, single_65k_return):
        """Resident: all income is CO-source (fraction = 1)."""
        app = PLUGIN.apportion_income(
            single_65k_return,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert isinstance(app, IncomeApportionment)
        assert app.state_source_wages == Decimal("65000.00")
        assert app.state_source_interest == Decimal("0")
        assert app.state_source_dividends == Decimal("0")
        assert app.state_source_capital_gains == Decimal("0")
        assert app.state_source_self_employment == Decimal("0")
        assert app.state_source_rental == Decimal("0")
        assert app.state_source_total == Decimal("65000.00")

    def test_apportion_income_nonresident(self, single_65k_return):
        """Nonresident: day-proration of each income category."""
        app = PLUGIN.apportion_income(
            single_65k_return,
            ResidencyStatus.NONRESIDENT,
            days_in_state=182,
        )
        expected_wages = (
            Decimal("65000") * Decimal(182) / Decimal(365)
        ).quantize(Decimal("0.01"))
        assert app.state_source_wages == expected_wages
        assert app.state_source_interest == Decimal("0")
        assert app.state_source_dividends == Decimal("0")


# ---------------------------------------------------------------------------
# v1 limitations documentation lock + TABOR deferred flag
# ---------------------------------------------------------------------------


class TestColoradoV1Limitations:
    def test_v1_limitations_documented(
        self, single_65k_return, federal_single_65k
    ):
        """state_specific['v1_limitations'] is a non-empty list.

        Enumerates CO additions/subtractions/credits NOT applied in v1 so
        downstream consumers can surface a warning to the taxpayer.
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        lims = result.state_specific["v1_limitations"]
        assert isinstance(lims, list)
        assert len(lims) > 0
        # At least one entry should mention the DR 0104AD subtraction schedule
        # (social security / pension exclusion / military retirement).
        assert any("DR 0104AD" in s or "subtract" in s.lower() for s in lims)

    def test_v1_limitations_module_constant_matches(self):
        """The module-level CO_V1_LIMITATIONS constant is also non-empty."""
        assert len(CO_V1_LIMITATIONS) > 0


class TestColoradoTaborDeferred:
    def test_tabor_deferred_flag_present(
        self, single_65k_return, federal_single_65k
    ):
        """state_specific['tabor_refund_deferred'] is True."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["tabor_refund_deferred"] is True

    def test_tabor_reason_documented(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        reason = result.state_specific["tabor_refund_reason"]
        assert isinstance(reason, str)
        assert len(reason) > 0
        assert "TABOR" in reason or "six-tier" in reason


# ---------------------------------------------------------------------------
# Flat rate constant and rate lock
# ---------------------------------------------------------------------------


class TestColoradoFlatRate:
    def test_flat_rate_is_four_point_forty_percent(self):
        """TY2025 CO flat rate is locked at 4.40% — the permanent statutory
        rate. TABOR temporary rate reduction did NOT trigger for TY2025 per
        OSA 2557P (October 2025). If a future verification shows a different
        rate (e.g. TABOR triggered 4.25% reduction), update the constant
        AND this test together."""
        assert CO_TY2025_FLAT_RATE == Decimal("0.044")

    def test_flat_rate_applied_to_single_65k(
        self, single_65k_return, federal_single_65k
    ):
        """Exact hand calc lock: 49250 * 0.044 = 2167.00."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        hand = (
            federal_single_65k.taxable_income * CO_TY2025_FLAT_RATE
        ).quantize(Decimal("0.01"))
        assert result.state_specific["state_total_tax"] == hand


# ---------------------------------------------------------------------------
# form_ids() + render_pdfs()
# ---------------------------------------------------------------------------


class TestColoradoFormIds:
    def test_form_ids_returns_dr_0104(self):
        assert PLUGIN.form_ids() == ["CO Form DR 0104"]

    def test_render_pdfs_returns_empty_list(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Fan-out follow-up: actual DR 0104 fill is not yet implemented."""
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
