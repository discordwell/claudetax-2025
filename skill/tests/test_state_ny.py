"""Tests for the New York state plugin (skill/scripts/states/ny.py).

NY is one of the 10 states tenforty supports. The plugin wraps tenforty's
state calc, mirroring the CA pattern: marshal the canonical return to
tenforty inputs via the shared `_to_tenforty_input`, call
`tenforty.evaluate_return(..., state='NY')`, and unpack state_* floats into
Decimal on StateReturn.state_specific.

Coverage:
- Protocol satisfied at runtime
- Meta shape: code, has_income_tax, starting_point, no reciprocity partners
- compute() on a Single $80k W-2 resident returns a positive state_tax
- compute() NONRESIDENT returns a smaller amount than RESIDENT (days-proration)
- form_ids() returns the resident IT-201 form id
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
    W2,
    W2StateRow,
)
from skill.scripts.states._plugin_api import (
    FederalTotals,
    StatePlugin,
    StateStartingPoint,
    SubmissionChannel,
)
from skill.scripts.states.ny import PLUGIN, NewYorkPlugin


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def canonical_return_single_80k() -> CanonicalReturn:
    """Single taxpayer with $80k W-2 wages, NY resident address."""
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=Person(
            first_name="Alex",
            last_name="Doe",
            ssn="111-22-3333",
            date_of_birth=dt.date(1990, 1, 1),
        ),
        address=Address(street1="1 Main", city="Brooklyn", state="NY", zip="11201"),
        w2s=[
            W2(employer_name="Acme NY", box1_wages=Decimal("80000")),
        ],
    )


@pytest.fixture
def federal_single_80k() -> FederalTotals:
    """Federal totals consistent with a Single $80k W-2 TY2025 return."""
    return FederalTotals(
        filing_status=FilingStatus.SINGLE,
        num_dependents=0,
        adjusted_gross_income=Decimal("80000"),
        taxable_income=Decimal("64250"),
        total_federal_tax=Decimal("9055"),
        federal_income_tax=Decimal("9055"),
        federal_standard_deduction=Decimal("15750"),
        federal_itemized_deductions_total=Decimal("0"),
        deduction_taken=Decimal("15750"),
        federal_withholding_from_w2s=Decimal("0"),
    )


# ---------------------------------------------------------------------------
# Protocol / meta
# ---------------------------------------------------------------------------


class TestMeta:
    def test_protocol_satisfied_at_runtime(self):
        """runtime_checkable Protocol must recognize NY plugin."""
        assert isinstance(PLUGIN, StatePlugin)

    def test_module_plugin_is_new_york_plugin(self):
        assert isinstance(PLUGIN, NewYorkPlugin)

    def test_meta_code(self):
        assert PLUGIN.meta.code == "NY"

    def test_meta_name(self):
        assert PLUGIN.meta.name == "New York"

    def test_meta_has_income_tax(self):
        assert PLUGIN.meta.has_income_tax is True

    def test_meta_starting_point_federal_agi(self):
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI

    def test_meta_no_reciprocity_partners(self):
        """NY has no bilateral reciprocity agreements."""
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_meta_submission_channel_free_portal(self):
        assert PLUGIN.meta.submission_channel == SubmissionChannel.STATE_DOR_FREE_PORTAL

    def test_meta_dor_url(self):
        assert PLUGIN.meta.dor_url == "https://www.tax.ny.gov/"

    def test_meta_free_efile_url(self):
        assert PLUGIN.meta.free_efile_url == "https://www.tax.ny.gov/pit/efile/"

    def test_meta_supported_tax_years(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "ZZ"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# compute()
# ---------------------------------------------------------------------------


class TestCompute:
    def test_resident_single_80k_positive_state_tax(
        self, canonical_return_single_80k, federal_single_80k
    ):
        """Single $80k W-2 NY resident should owe positive NY tax."""
        state_return = PLUGIN.compute(
            canonical_return_single_80k,
            federal_single_80k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert state_return.state == "NY"
        assert state_return.residency == ResidencyStatus.RESIDENT
        assert state_return.days_in_state == 365

        state_tax = state_return.state_specific["state_tax"]
        assert isinstance(state_tax, Decimal)
        assert state_tax > Decimal("0")

    def test_state_specific_decimal_types(
        self, canonical_return_single_80k, federal_single_80k
    ):
        """All state_* floats from tenforty must be wrapped as Decimal."""
        state_return = PLUGIN.compute(
            canonical_return_single_80k,
            federal_single_80k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = state_return.state_specific
        for key in (
            "state_tax",
            "state_adjusted_gross_income",
            "state_taxable_income",
        ):
            assert key in ss, f"state_specific missing {key}"
            assert isinstance(ss[key], Decimal), f"{key} is not Decimal: {type(ss[key])}"

    def test_nonresident_smaller_than_resident(
        self, canonical_return_single_80k, federal_single_80k
    ):
        """Nonresident with 180 days should owe less than resident with 365 days."""
        resident = PLUGIN.compute(
            canonical_return_single_80k,
            federal_single_80k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        nonresident = PLUGIN.compute(
            canonical_return_single_80k,
            federal_single_80k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=180,
        )
        assert (
            nonresident.state_specific["state_tax"]
            < resident.state_specific["state_tax"]
        )

    def test_part_year_smaller_than_resident(
        self, canonical_return_single_80k, federal_single_80k
    ):
        """Part-year with 180 days should owe less than full-year resident."""
        resident = PLUGIN.compute(
            canonical_return_single_80k,
            federal_single_80k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        part_year = PLUGIN.compute(
            canonical_return_single_80k,
            federal_single_80k,
            ResidencyStatus.PART_YEAR,
            days_in_state=180,
        )
        assert (
            part_year.state_specific["state_tax"]
            < resident.state_specific["state_tax"]
        )


# ---------------------------------------------------------------------------
# apportion_income / render_pdfs / form_ids
# ---------------------------------------------------------------------------


class TestOtherProtocolMethods:
    def test_apportion_income_resident_full_year(
        self, canonical_return_single_80k
    ):
        """Full-year resident: full wages are state-source."""
        app = PLUGIN.apportion_income(
            canonical_return_single_80k, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert app.state_source_wages == Decimal("80000")

    def test_apportion_income_nonresident_days_based(
        self, canonical_return_single_80k
    ):
        """Nonresident 180/365 days should prorate wages."""
        app = PLUGIN.apportion_income(
            canonical_return_single_80k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=180,
        )
        assert app.state_source_wages < Decimal("80000")
        assert app.state_source_wages > Decimal("0")

    def test_render_pdfs_returns_empty_list(
        self, canonical_return_single_80k, federal_single_80k
    ):
        """PDF rendering is a TODO — returns []."""
        state_return = PLUGIN.compute(
            canonical_return_single_80k,
            federal_single_80k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert PLUGIN.render_pdfs(state_return, Path("/tmp")) == []

    def test_form_ids_resident(self):
        """Resident form is IT-201."""
        assert PLUGIN.form_ids() == ["NY Form IT-201"]


# ---------------------------------------------------------------------------
# Wave 6 — IT-203 / IT-203-B sourcing scaffolding
# ---------------------------------------------------------------------------


class TestNewYorkPluginNonresidentSourcing:
    """When the filer is a non-NY resident AND either (a) an
    ``ny_workdays_in_ny`` count is present OR (b) a W-2 carries an NY
    state row, the plugin must compute NY tax on a sourced wage amount.
    """

    @pytest.fixture
    def nj_resident_with_ny_state_row(self) -> CanonicalReturn:
        """NJ resident who commutes into NY (W-2 has NY state row)."""
        return CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="Nash",
                last_name="Commuter",
                ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
            ),
            address=Address(
                street1="1 Hudson St",
                city="Jersey City",
                state="NJ",
                zip="07302",
            ),
            w2s=[
                W2(
                    employer_name="Wall St Corp",
                    box1_wages=Decimal("80000"),
                    state_rows=[
                        W2StateRow(
                            state="NY",
                            state_wages=Decimal("80000"),
                        ),
                    ],
                ),
            ],
        )

    @pytest.fixture
    def nj_resident_with_workday_count(self) -> CanonicalReturn:
        """NJ resident with an IT-203-B workday apportionment count."""
        return CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="Nash",
                last_name="Commuter",
                ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
                ny_workdays_in_ny=130,  # half of 260
            ),
            address=Address(
                street1="1 Hudson St",
                city="Jersey City",
                state="NJ",
                zip="07302",
            ),
            w2s=[
                W2(employer_name="Wall St Corp", box1_wages=Decimal("80000")),
            ],
        )

    def test_state_rows_path_telemetry(
        self, nj_resident_with_ny_state_row, federal_single_80k
    ):
        result = PLUGIN.compute(
            nj_resident_with_ny_state_row,
            federal_single_80k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=0,
        )
        ss = result.state_specific
        assert ss["ny_state_rows_present"] is True
        assert ss["used_w2_state_rows"] is True
        assert ss["used_it203_workdays"] is False
        assert ss[
            "ny_sourced_wages_from_w2_state_rows"
        ] == Decimal("80000.00")

    def test_workday_path_telemetry(
        self, nj_resident_with_workday_count, federal_single_80k
    ):
        result = PLUGIN.compute(
            nj_resident_with_workday_count,
            federal_single_80k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=0,
        )
        ss = result.state_specific
        assert ss["used_it203_workdays"] is True
        assert ss["used_w2_state_rows"] is False
        assert ss["ny_workdays_in_ny"] == 130

    def test_workday_path_takes_precedence_over_state_rows(
        self, federal_single_80k
    ):
        """When both a workday count and state rows are present, the
        workday path wins (it's the more-specific signal)."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="N",
                last_name="C",
                ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
                ny_workdays_in_ny=100,
            ),
            address=Address(
                street1="1 Hudson St",
                city="Jersey City",
                state="NJ",
                zip="07302",
            ),
            w2s=[
                W2(
                    employer_name="X",
                    box1_wages=Decimal("80000"),
                    state_rows=[
                        W2StateRow(
                            state="NY", state_wages=Decimal("60000")
                        ),
                    ],
                ),
            ],
        )
        result = PLUGIN.compute(
            ret,
            federal_single_80k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=0,
        )
        assert result.state_specific["used_it203_workdays"] is True
        assert result.state_specific["used_w2_state_rows"] is False

    def test_fallback_day_prorate_when_no_signals(
        self, canonical_return_single_80k, federal_single_80k
    ):
        """Without workday count OR W-2 state rows, legacy day-proration
        fallback still applies."""
        result = PLUGIN.compute(
            canonical_return_single_80k,
            federal_single_80k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=182,
        )
        ss = result.state_specific
        assert ss["used_it203_workdays"] is False
        assert ss["used_w2_state_rows"] is False
        assert ss["ny_state_rows_present"] is False
        # Legacy day-prorate: state_tax < full_year_state_tax strictly.
        assert ss["state_tax"] < ss["full_year_state_tax"]
