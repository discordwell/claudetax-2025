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
- IT-203 nonresident apportionment:
  - Resident: unchanged behavior (all income is NY-source)
  - Nonresident with workday allocation: 130/260 days = 50% of wages
  - Nonresident with W-2 state rows only: existing behavior
  - Nonresident with NY rental property: rental sourced to NY
  - Mixed: wages (workday) + rental (property in NY) + interest (not sourced)
  - Interest/dividends/cap gains: $0 for nonresidents
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
    ScheduleC,
    ScheduleE,
    ScheduleEProperty,
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

        state_tax = state_return.state_specific["state_total_tax"]
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
            "state_total_tax",
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
            nonresident.state_specific["state_total_tax"]
            < resident.state_specific["state_total_tax"]
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
            part_year.state_specific["state_total_tax"]
            < resident.state_specific["state_total_tax"]
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

    def test_render_pdfs_produces_filled_pdf(
        self, canonical_return_single_80k, federal_single_80k, tmp_path
    ):
        """render_pdfs should produce a filled NY IT-201 PDF."""
        state_return = PLUGIN.compute(
            canonical_return_single_80k,
            federal_single_80k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        try:
            from pypdf import PdfReader
        except BaseException:
            pytest.skip("pypdf unavailable")
        result = PLUGIN.render_pdfs(state_return, tmp_path)
        assert len(result) == 1
        pdf_path = result[0]
        assert pdf_path.exists()
        assert pdf_path.stat().st_size > 0
        assert pdf_path.name == "NY-IT-201.pdf"

    def test_render_pdfs_output_has_form_fields(
        self, canonical_return_single_80k, federal_single_80k, tmp_path
    ):
        """Rendered PDF should still have AcroForm fields (not flattened)."""
        state_return = PLUGIN.compute(
            canonical_return_single_80k,
            federal_single_80k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        try:
            from pypdf import PdfReader
        except BaseException:
            pytest.skip("pypdf unavailable")
        result = PLUGIN.render_pdfs(state_return, tmp_path)
        reader = PdfReader(str(result[0]))
        fields = reader.get_fields()
        assert fields is not None
        assert len(fields) > 0

    def test_render_pdfs_field_values(
        self, canonical_return_single_80k, federal_single_80k, tmp_path
    ):
        """Verify that rendered NY IT-201 PDF contains correct field values."""
        try:
            from pypdf import PdfReader
        except BaseException:
            pytest.skip("pypdf unavailable")

        state_return = PLUGIN.compute(
            canonical_return_single_80k,
            federal_single_80k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        paths = PLUGIN.render_pdfs(state_return, tmp_path)
        reader = PdfReader(str(paths[0]))
        fields = reader.get_fields()
        assert fields is not None

        # Derive expected values from the computed state_return
        ss = state_return.state_specific
        expected_tax = f"{ss['state_total_tax']:.2f}"
        expected_ti = f"{ss['state_taxable_income']:.2f}"
        expected_agi = f"{ss['state_adjusted_gross_income']:.2f}"

        # Widget "Line59" maps to total NYS tax
        assert fields["Line59"].get("/V") == expected_tax
        # Widget "Line35" maps to NYS taxable income
        assert fields["Line35"].get("/V") == expected_ti
        # Widget "Line25" maps to NYS AGI
        assert fields["Line25"].get("/V") == expected_agi

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
        assert ss["state_total_tax"] < ss["full_year_state_tax"]


# ---------------------------------------------------------------------------
# Wave 8C-2 — IT-203 real nonresident income apportionment
# ---------------------------------------------------------------------------


class TestIT203Apportionment:
    """Comprehensive tests for the IT-203 nonresident apportionment logic.

    Validates that apportion_income() and compute() correctly implement
    NY-source income rules:
      - Wages: workday allocation > W-2 state rows > day-proration
      - Interest / Dividends / Capital gains: $0 for nonresidents
      - Business income: sourced by business location
      - Rental: sourced by property location
      - compute() uses IT-203 ratio method for tax calculation
    """

    @pytest.fixture
    def federal_80k(self) -> FederalTotals:
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

    # ---- Resident: unchanged behavior ----

    def test_resident_apportion_all_income_full(self):
        """Resident: all income categories at 100%."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="A", last_name="B", ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
            ),
            address=Address(street1="1 Main", city="NY", state="NY", zip="10001"),
            w2s=[W2(employer_name="X", box1_wages=Decimal("80000"))],
        )
        app = PLUGIN.apportion_income(ret, ResidencyStatus.RESIDENT, 365)
        assert app.state_source_wages == Decimal("80000")
        assert app.state_source_interest == Decimal("0")
        assert app.state_source_dividends == Decimal("0")

    # ---- Nonresident with workday allocation (IT-203-B) ----

    def test_nonresident_workday_130_of_260_wages_50pct(self):
        """Nonresident with 130 of 260 workdays in NY: 50% of wages."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="A", last_name="B", ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
                ny_workdays_in_ny=130,
            ),
            address=Address(street1="1 Hudson", city="JC", state="NJ", zip="07302"),
            w2s=[W2(employer_name="X", box1_wages=Decimal("100000"))],
        )
        app = PLUGIN.apportion_income(ret, ResidencyStatus.NONRESIDENT, 0)
        assert app.state_source_wages == Decimal("50000.00")

    def test_nonresident_workday_260_of_260_wages_100pct(self):
        """Nonresident with 260/260 workdays: capped at 100%."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="A", last_name="B", ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
                ny_workdays_in_ny=260,
            ),
            address=Address(street1="1 Hudson", city="JC", state="NJ", zip="07302"),
            w2s=[W2(employer_name="X", box1_wages=Decimal("100000"))],
        )
        app = PLUGIN.apportion_income(ret, ResidencyStatus.NONRESIDENT, 0)
        assert app.state_source_wages == Decimal("100000.00")

    def test_nonresident_workday_exceeds_260_capped(self):
        """Workdays > 260 should be capped at 100%."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="A", last_name="B", ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
                ny_workdays_in_ny=300,
            ),
            address=Address(street1="1 Hudson", city="JC", state="NJ", zip="07302"),
            w2s=[W2(employer_name="X", box1_wages=Decimal("100000"))],
        )
        app = PLUGIN.apportion_income(ret, ResidencyStatus.NONRESIDENT, 0)
        assert app.state_source_wages == Decimal("100000.00")

    # ---- Nonresident: interest/dividends/cap gains = $0 ----

    def test_nonresident_interest_not_ny_source(self):
        """Interest income is NOT NY-source for nonresidents."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="A", last_name="B", ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
                ny_workdays_in_ny=130,
            ),
            address=Address(street1="1 Hudson", city="JC", state="NJ", zip="07302"),
            w2s=[W2(employer_name="X", box1_wages=Decimal("80000"))],
        )
        app = PLUGIN.apportion_income(ret, ResidencyStatus.NONRESIDENT, 0)
        assert app.state_source_interest == Decimal("0")
        assert app.state_source_dividends == Decimal("0")
        assert app.state_source_capital_gains == Decimal("0")

    # ---- Nonresident with W-2 state rows only (no workday data) ----

    def test_nonresident_w2_state_rows_apportion(self):
        """W-2 state rows: use employer-reported NY wages."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="A", last_name="B", ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
            ),
            address=Address(street1="1 Hudson", city="JC", state="NJ", zip="07302"),
            w2s=[
                W2(
                    employer_name="X",
                    box1_wages=Decimal("100000"),
                    state_rows=[
                        W2StateRow(state="NY", state_wages=Decimal("60000")),
                    ],
                ),
            ],
        )
        app = PLUGIN.apportion_income(ret, ResidencyStatus.NONRESIDENT, 0)
        assert app.state_source_wages == Decimal("60000.00")
        # Investment income still $0 for nonresident
        assert app.state_source_interest == Decimal("0")

    # ---- Nonresident with NY rental property ----

    def test_nonresident_rental_sourced_to_ny(self):
        """Rental income from NY property is NY-source."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="A", last_name="B", ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
                ny_workdays_in_ny=130,
            ),
            address=Address(street1="1 Hudson", city="JC", state="NJ", zip="07302"),
            w2s=[W2(employer_name="X", box1_wages=Decimal("80000"))],
            schedules_e=[
                ScheduleE(properties=[
                    ScheduleEProperty(
                        address=Address(
                            street1="123 Broadway",
                            city="New York",
                            state="NY",
                            zip="10007",
                        ),
                        rents_received=Decimal("24000"),
                        taxes=Decimal("4000"),
                    ),
                ]),
            ],
        )
        app = PLUGIN.apportion_income(ret, ResidencyStatus.NONRESIDENT, 0)
        # Net rental = 24000 - 4000 = 20000
        assert app.state_source_rental == Decimal("20000.00")

    def test_nonresident_rental_not_in_ny(self):
        """Rental income from non-NY property is NOT NY-source."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="A", last_name="B", ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
                ny_workdays_in_ny=130,
            ),
            address=Address(street1="1 Hudson", city="JC", state="NJ", zip="07302"),
            w2s=[W2(employer_name="X", box1_wages=Decimal("80000"))],
            schedules_e=[
                ScheduleE(properties=[
                    ScheduleEProperty(
                        address=Address(
                            street1="456 Broad St",
                            city="Newark",
                            state="NJ",
                            zip="07102",
                        ),
                        rents_received=Decimal("24000"),
                        taxes=Decimal("4000"),
                    ),
                ]),
            ],
        )
        app = PLUGIN.apportion_income(ret, ResidencyStatus.NONRESIDENT, 0)
        assert app.state_source_rental == Decimal("0.00")

    # ---- Mixed: wages (workday) + rental (NY) + interest (not sourced) ----

    def test_nonresident_mixed_income_apportionment(self):
        """Mixed income: wages via workday, rental via property, interest $0."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="A", last_name="B", ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
                ny_workdays_in_ny=130,  # 50% of 260
            ),
            address=Address(street1="1 Hudson", city="JC", state="NJ", zip="07302"),
            w2s=[W2(employer_name="X", box1_wages=Decimal("100000"))],
            schedules_e=[
                ScheduleE(properties=[
                    ScheduleEProperty(
                        address=Address(
                            street1="123 Broadway",
                            city="New York",
                            state="NY",
                            zip="10007",
                        ),
                        rents_received=Decimal("30000"),
                        taxes=Decimal("5000"),
                    ),
                ]),
            ],
        )
        app = PLUGIN.apportion_income(ret, ResidencyStatus.NONRESIDENT, 0)
        assert app.state_source_wages == Decimal("50000.00")
        assert app.state_source_rental == Decimal("25000.00")
        assert app.state_source_interest == Decimal("0")
        assert app.state_source_dividends == Decimal("0")
        assert app.state_source_capital_gains == Decimal("0")
        # Total NY-source = 50000 + 25000 = 75000
        assert app.state_source_total == Decimal("75000.00")

    # ---- compute() with IT-203 ratio method ----

    def test_compute_workday_uses_it203_ratio(self, federal_80k):
        """compute() with workday allocation uses ratio method, not re-eval."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="A", last_name="B", ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
                ny_workdays_in_ny=130,  # 50% of 260
            ),
            address=Address(street1="1 Hudson", city="JC", state="NJ", zip="07302"),
            w2s=[W2(employer_name="X", box1_wages=Decimal("80000"))],
        )
        result = PLUGIN.compute(ret, federal_80k, ResidencyStatus.NONRESIDENT, 0)
        ss = result.state_specific
        assert ss["used_it203_workdays"] is True
        # The tax should be less than full-year tax (50% of wages is NY-source)
        assert ss["state_total_tax"] < ss["full_year_state_tax"]
        assert ss["state_total_tax"] > Decimal("0")

    def test_compute_rental_included_in_ny_source(self, federal_80k):
        """compute() includes NY rental in the IT-203 NY-source calculation."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="A", last_name="B", ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
                ny_workdays_in_ny=130,
            ),
            address=Address(street1="1 Hudson", city="JC", state="NJ", zip="07302"),
            w2s=[W2(employer_name="X", box1_wages=Decimal("80000"))],
            schedules_e=[
                ScheduleE(properties=[
                    ScheduleEProperty(
                        address=Address(
                            street1="123 Broadway",
                            city="New York",
                            state="NY",
                            zip="10007",
                        ),
                        rents_received=Decimal("20000"),
                        taxes=Decimal("2000"),
                    ),
                ]),
            ],
        )
        result = PLUGIN.compute(ret, federal_80k, ResidencyStatus.NONRESIDENT, 0)
        ss = result.state_specific
        assert ss["ny_sourced_rental"] == Decimal("18000.00")
        assert ss["state_total_tax"] > Decimal("0")

    def test_compute_w2_state_rows_uses_it203_ratio(self, federal_80k):
        """compute() with W-2 state rows also uses ratio method."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="A", last_name="B", ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
            ),
            address=Address(street1="1 Hudson", city="JC", state="NJ", zip="07302"),
            w2s=[
                W2(
                    employer_name="X",
                    box1_wages=Decimal("80000"),
                    state_rows=[
                        W2StateRow(state="NY", state_wages=Decimal("80000")),
                    ],
                ),
            ],
        )
        result = PLUGIN.compute(ret, federal_80k, ResidencyStatus.NONRESIDENT, 0)
        ss = result.state_specific
        assert ss["used_w2_state_rows"] is True
        # Full wages are NY-source via state rows, so ratio = 80000/80000 = 1.0
        # Tax should equal or be close to full-year tax
        assert ss["state_total_tax"] > Decimal("0")
        assert ss["state_total_tax"] <= ss["full_year_state_tax"]

    def test_compute_with_wages_plus_rental_higher_than_wages_alone(
        self, federal_80k
    ):
        """Adding NY rental on top of wages should increase the IT-203 ratio
        and thus the tax owed."""
        base_ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="A", last_name="B", ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
                ny_workdays_in_ny=130,
            ),
            address=Address(street1="1 Hudson", city="JC", state="NJ", zip="07302"),
            w2s=[W2(employer_name="X", box1_wages=Decimal("80000"))],
        )
        rental_ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="A", last_name="B", ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
                ny_workdays_in_ny=130,
            ),
            address=Address(street1="1 Hudson", city="JC", state="NJ", zip="07302"),
            w2s=[W2(employer_name="X", box1_wages=Decimal("80000"))],
            schedules_e=[
                ScheduleE(properties=[
                    ScheduleEProperty(
                        address=Address(
                            street1="123 Broadway", city="New York",
                            state="NY", zip="10007",
                        ),
                        rents_received=Decimal("20000"),
                    ),
                ]),
            ],
        )
        # Need separate federal totals for the rental case (higher AGI)
        federal_with_rental = FederalTotals(
            filing_status=FilingStatus.SINGLE,
            num_dependents=0,
            adjusted_gross_income=Decimal("100000"),
            taxable_income=Decimal("84250"),
            total_federal_tax=Decimal("13855"),
            federal_income_tax=Decimal("13855"),
            federal_standard_deduction=Decimal("15750"),
            federal_itemized_deductions_total=Decimal("0"),
            deduction_taken=Decimal("15750"),
            federal_withholding_from_w2s=Decimal("0"),
        )
        base_result = PLUGIN.compute(
            base_ret, federal_80k, ResidencyStatus.NONRESIDENT, 0
        )
        rental_result = PLUGIN.compute(
            rental_ret, federal_with_rental, ResidencyStatus.NONRESIDENT, 0
        )
        # More NY-source income => higher tax
        assert (
            rental_result.state_specific["state_total_tax"]
            > base_result.state_specific["state_total_tax"]
        )

    # ---- Schedule C business income sourcing ----

    def test_nonresident_schedule_c_ny_business(self):
        """Schedule C with NY business location is NY-source."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="A", last_name="B", ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
                ny_workdays_in_ny=130,
            ),
            address=Address(street1="1 Hudson", city="JC", state="NJ", zip="07302"),
            w2s=[W2(employer_name="X", box1_wages=Decimal("80000"))],
            schedules_c=[
                ScheduleC(
                    business_name="NYC Consulting",
                    principal_business_or_profession="Consulting",
                    business_location_state="NY",
                    line1_gross_receipts=Decimal("50000"),
                ),
            ],
        )
        app = PLUGIN.apportion_income(ret, ResidencyStatus.NONRESIDENT, 0)
        assert app.state_source_self_employment == Decimal("50000.00")

    def test_nonresident_schedule_c_non_ny_business(self):
        """Schedule C with NJ business location is NOT NY-source."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="A", last_name="B", ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
                ny_workdays_in_ny=130,
            ),
            address=Address(street1="1 Hudson", city="JC", state="NJ", zip="07302"),
            w2s=[W2(employer_name="X", box1_wages=Decimal("80000"))],
            schedules_c=[
                ScheduleC(
                    business_name="NJ Consulting",
                    principal_business_or_profession="Consulting",
                    business_location_state="NJ",
                    line1_gross_receipts=Decimal("50000"),
                ),
            ],
        )
        app = PLUGIN.apportion_income(ret, ResidencyStatus.NONRESIDENT, 0)
        assert app.state_source_self_employment == Decimal("0.00")

    # ---- Day-proration fallback for wages ----

    def test_nonresident_no_signals_wages_day_prorated(self):
        """Without workday count or W-2 state rows, wages are day-prorated."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="A", last_name="B", ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
            ),
            address=Address(street1="1 Hudson", city="JC", state="NJ", zip="07302"),
            w2s=[W2(employer_name="X", box1_wages=Decimal("100000"))],
        )
        app = PLUGIN.apportion_income(ret, ResidencyStatus.NONRESIDENT, 182)
        expected = (Decimal("100000") * Decimal("182") / Decimal("365")).quantize(
            Decimal("0.01")
        )
        assert app.state_source_wages == expected
        # But interest is still $0 for nonresidents
        assert app.state_source_interest == Decimal("0")

    # ---- Part-year same as nonresident ----

    def test_part_year_uses_nonresident_sourcing(self):
        """Part-year residents use the same IT-203 sourcing as nonresidents."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="A", last_name="B", ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
                ny_workdays_in_ny=130,
            ),
            address=Address(street1="1 Hudson", city="JC", state="NJ", zip="07302"),
            w2s=[W2(employer_name="X", box1_wages=Decimal("100000"))],
        )
        app = PLUGIN.apportion_income(ret, ResidencyStatus.PART_YEAR, 180)
        # Should use workday allocation, not day proration
        assert app.state_source_wages == Decimal("50000.00")
        # Interest still $0
        assert app.state_source_interest == Decimal("0")
