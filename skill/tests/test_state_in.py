"""Indiana state plugin tests.

Mirrors the KS / KY plugin test suites because IN, like KS/KY, is
hand-rolled from DOR primary sources. Indiana's tenforty default-OTS-
backend support raises ``ValueError: OTS does not support 2025/IN_IT40``
AND tenforty's graph backend computes ``AGI × 3.00%`` directly without
applying the $1,000 Indiana personal exemption — a $30 over-statement
at the locked $65k Single scenario. We therefore hand-roll IT-40 from
the Indiana DOR Form IT-40 line-by-line instructions.

Filename note: the plugin module is ``skill.scripts.states.in_`` (with
trailing underscore — ``in`` is a Python reserved keyword), but the
test filename uses ``test_state_in.py`` for pytest discovery.

Reference scenario (verified 2026-04-11 against the IN DOR Form IT-40
instructions and against the tenforty graph-backend probe for the
divergence amount):

    Single / $65,000 W-2 / Standard, no dependents
      Hand calc (Form IT-40 line-by-line, this plugin):
        Indiana AGI                = $65,000.00
        Personal exemption         =  $1,000.00
        Indiana taxable income     = $64,000.00
        Indiana state tax (3.00%)  =  $1,920.00   ← LOCKED
      tenforty graph backend probe (for comparison):
        state_total_tax            =  $1,950.00   (= 65000 * 0.03,
                                                   no exemption applied)
      Divergence                   =     $30.00   (material > $5)

Indiana TY2025 rate / base:
    Indiana Code §6-3-2-1(b) as amended by HEA 1001 (2023) sets the
    individual adjusted gross income tax rate at 3.00% for tax years
    beginning after Dec 31, 2024 and before Jan 1, 2026 (down from
    3.05% TY2024 and 3.15% TY2023). Indiana Form IT-40 line 6
    subtracts a $1,000 personal exemption per filer / $1,000 per
    dependent / $2,000 MFJ (per Schedule 3 line 7).

Reciprocity: Indiana has FIVE bilateral reciprocity partners — KY, MI,
OH, PA, WI — verified against skill/reference/state-reciprocity.json
and IN DOR Income Tax Information Bulletin #28. This is the second-
largest reciprocity network in the US after PA's six.
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
from skill.scripts.states.in_ import (
    IN_TY2025_EXEMPTION_BY_FILING_STATUS,
    IN_TY2025_FLAT_RATE,
    IN_TY2025_PERSONAL_EXEMPTION_BASE,
    IN_TY2025_QUALIFYING_CHILD_EXTRA_EXEMPTION,
    IN_V1_LIMITATIONS,
    IndianaPlugin,
    LOCK_VALUE,
    PLUGIN,
    in_personal_exemption,
    in_state_tax,
)


# ---------------------------------------------------------------------------
# Shared fixtures — a Single $65k W-2 return domiciled in Indiana
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
            street1="200 W Washington St",
            city="Indianapolis",
            state="IN",
            zip="46204",
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


class TestIndianaPluginMeta:
    def test_meta_fields(self):
        assert PLUGIN.meta.code == "IN"
        assert PLUGIN.meta.name == "Indiana"
        assert PLUGIN.meta.has_income_tax is True
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.FED_STATE_PIGGYBACK
        )
        # Reciprocity: exactly KY, MI, OH, PA, WI — 5 bilateral
        # partners per IN DOR Information Bulletin #28.
        assert set(PLUGIN.meta.reciprocity_partners) == {
            "KY",
            "MI",
            "OH",
            "PA",
            "WI",
        }
        assert len(PLUGIN.meta.reciprocity_partners) == 5

    def test_plugin_is_state_plugin_protocol(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_indiana_plugin_instance(self):
        assert isinstance(PLUGIN, IndianaPlugin)

    def test_meta_urls(self):
        assert "in.gov/dor" in PLUGIN.meta.dor_url
        assert PLUGIN.meta.free_efile_url is not None
        assert "intime" in PLUGIN.meta.free_efile_url.lower()

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_flat_rate_and_legislation(self):
        notes = PLUGIN.meta.notes
        assert "3.00" in notes or "3.0%" in notes
        assert "1001" in notes  # HEA 1001-2023

    def test_meta_notes_mentions_personal_exemption(self):
        """Notes must document the $1,000 personal exemption — this is
        the difference between hand-rolled correctness and the graph-
        backend wrap that misses it."""
        notes = PLUGIN.meta.notes
        assert "1,000" in notes or "1000" in notes
        assert "exemption" in notes.lower()

    def test_meta_notes_mentions_county_tax_omission(self):
        """Notes must call out that Indiana County Income Tax (Schedule
        CT-40) is NOT computed — this is a load-bearing limitation."""
        notes = PLUGIN.meta.notes.lower()
        assert "county" in notes

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "NY"  # type: ignore[misc]

    @pytest.mark.parametrize("partner", ["KY", "MI", "OH", "PA", "WI"])
    def test_meta_reciprocity_contains_each_partner(self, partner):
        assert partner in PLUGIN.meta.reciprocity_partners

    def test_meta_reciprocity_excludes_non_partners(self):
        """Indiana has NO reciprocity with IL despite sharing a long
        border (IL has reciprocity with IA/KY/MI/WI but NOT IN)."""
        for not_partner in ("IL", "CA", "NY", "FL", "IN"):
            assert not_partner not in PLUGIN.meta.reciprocity_partners


# ---------------------------------------------------------------------------
# Pure-function unit tests
# ---------------------------------------------------------------------------


class TestIndianaConstants:
    def test_flat_rate_is_3_percent(self):
        assert IN_TY2025_FLAT_RATE == Decimal("0.030")

    def test_personal_exemption_base(self):
        assert IN_TY2025_PERSONAL_EXEMPTION_BASE == Decimal("1000")

    def test_qualifying_child_extra_exemption(self):
        """Schedule 3 line 3: additional $1,500 per qualifying child.
        v1 does not apply this; the constant is exposed so the
        TODO(in-qualifying-child-extra) future work knows the value."""
        assert IN_TY2025_QUALIFYING_CHILD_EXTRA_EXEMPTION == Decimal(
            "1500"
        )

    def test_exemption_table_filing_status_keys(self):
        """Single/MFS/HOH = 1 exemption ($1,000); MFJ/QSS = 2 ($2,000)."""
        assert IN_TY2025_EXEMPTION_BY_FILING_STATUS[
            FilingStatus.SINGLE
        ] == Decimal("1000")
        assert IN_TY2025_EXEMPTION_BY_FILING_STATUS[
            FilingStatus.HOH
        ] == Decimal("1000")
        assert IN_TY2025_EXEMPTION_BY_FILING_STATUS[
            FilingStatus.MFS
        ] == Decimal("1000")
        assert IN_TY2025_EXEMPTION_BY_FILING_STATUS[
            FilingStatus.MFJ
        ] == Decimal("2000")
        assert IN_TY2025_EXEMPTION_BY_FILING_STATUS[
            FilingStatus.QSS
        ] == Decimal("2000")

    def test_v1_limitations_documented(self):
        """v1 limitations must enumerate at least add-backs, county
        tax, and credits — these are the load-bearing gaps."""
        joined = " ".join(IN_V1_LIMITATIONS).lower()
        assert "add-back" in joined or "add back" in joined
        assert "county" in joined
        assert "credit" in joined
        assert len(IN_V1_LIMITATIONS) >= 5


class TestInPersonalExemption:
    def test_single_no_dependents(self):
        assert in_personal_exemption(FilingStatus.SINGLE, 0) == Decimal(
            "1000"
        )

    def test_mfj_no_dependents(self):
        """MFJ gets two $1,000 personal exemptions = $2,000 base."""
        assert in_personal_exemption(FilingStatus.MFJ, 0) == Decimal(
            "2000"
        )

    def test_single_one_dependent(self):
        """Single + 1 dep = $1,000 (personal) + $1,000 (dep) = $2,000."""
        assert in_personal_exemption(FilingStatus.SINGLE, 1) == Decimal(
            "2000"
        )

    def test_mfj_two_dependents(self):
        """MFJ + 2 deps = $2,000 + $2,000 = $4,000."""
        assert in_personal_exemption(FilingStatus.MFJ, 2) == Decimal(
            "4000"
        )

    def test_hoh_one_dependent(self):
        """HOH + 1 dep = $1,000 + $1,000 = $2,000."""
        assert in_personal_exemption(FilingStatus.HOH, 1) == Decimal(
            "2000"
        )

    def test_negative_dependents_clamped_to_zero(self):
        assert in_personal_exemption(FilingStatus.SINGLE, -3) == Decimal(
            "1000"
        )


class TestInStateTax:
    def test_zero_taxable_income_zero_tax(self):
        assert in_state_tax(Decimal("0")) == Decimal("0.00")

    def test_negative_taxable_income_zero_tax(self):
        assert in_state_tax(Decimal("-500")) == Decimal("0.00")

    def test_64k_taxable_income_locks_to_1920(self):
        """64,000 * 0.03 = 1,920.00 — the load-bearing $65k Single
        scenario after the $1,000 personal exemption."""
        assert in_state_tax(Decimal("64000")) == Decimal("1920.00")

    def test_one_dollar_taxable_income(self):
        """$1 * 0.03 = $0.03 — sanity check on cent precision."""
        assert in_state_tax(Decimal("1")) == Decimal("0.03")

    @pytest.mark.parametrize(
        "ti, expected",
        [
            (Decimal("10000"), Decimal("300.00")),
            (Decimal("50000"), Decimal("1500.00")),
            (Decimal("100000"), Decimal("3000.00")),
            (Decimal("250000"), Decimal("7500.00")),
        ],
    )
    def test_flat_rate_at_various_incomes(self, ti, expected):
        assert in_state_tax(ti) == expected


# ---------------------------------------------------------------------------
# compute() — resident case matches DOR primary source hand calc
# ---------------------------------------------------------------------------


class TestIndianaPluginComputeResident:
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

    def test_state_code_is_in(self, single_65k_return, federal_single_65k):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state == "IN"

    def test_residency_preserved(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.residency == ResidencyStatus.RESIDENT
        assert result.days_in_state == 365

    def test_resident_single_65k_tax_lock(
        self, single_65k_return, federal_single_65k
    ):
        """HAND-ROLL CORRECTNESS LOCK: Single / $65k W-2 / Standard
        -> IN state_total_tax = $1,920.00.

        This is the load-bearing regression guard. The hand calc:
            Indiana AGI            = $65,000.00
            Personal exemption     =  $1,000.00
            Indiana taxable income = $64,000.00
            State tax @ 3.00%      =  $1,920.00

        The tenforty graph backend computes $1,950.00 for this same
        scenario because it omits the $1,000 personal exemption — that
        is exactly why this plugin hand-rolls instead of wrapping."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        assert state_tax == LOCK_VALUE

    def test_indiana_taxable_income_correct(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal(
            "64000.00"
        )

    def test_indiana_exemption_applied(
        self, single_65k_return, federal_single_65k
    ):
        """The $1,000 Indiana personal exemption must appear in
        state_specific so downstream output rendering can show the
        line-6 amount on Form IT-40."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific[
            "state_exemption_allowance"
        ] == Decimal("1000.00")

    def test_state_agi_matches_federal_agi(
        self, single_65k_return, federal_single_65k
    ):
        """Indiana's starting point is federal AGI (Form IT-40 line 1)."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific[
            "state_adjusted_gross_income"
        ] == Decimal("65000.00")

    def test_county_tax_explicitly_zero_in_v1(
        self, single_65k_return, federal_single_65k
    ):
        """Indiana County Income Tax (Schedule CT-40) is NOT computed
        in v1 — must be reported as $0.00 with the explanatory note."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_county_tax"] == Decimal("0.00")
        assert result.state_specific["in_county_tax_computed"] is False
        # The note must mention the county-tax limitation explicitly.
        note = result.state_specific["in_county_tax_note"]
        assert "county" in note.lower()
        assert "92" in note  # Indiana has 92 counties

    def test_state_specific_decimal_fields(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        numeric_keys = [
            "state_federal_agi",
            "state_adjusted_gross_income",
            "state_exemption_allowance",
            "state_taxable_income",
            "state_total_tax",
            "state_total_tax_resident_basis",
            "state_county_tax",
            "state_flat_rate",
            "apportionment_fraction",
        ]
        for key in numeric_keys:
            assert key in result.state_specific, f"missing {key}"
            assert isinstance(
                result.state_specific[key], Decimal
            ), f"{key} is not Decimal"

    def test_v1_limitations_in_state_specific(
        self, single_65k_return, federal_single_65k
    ):
        """state_specific must surface the v1 limitations so
        downstream output rendering can warn the user."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert "v1_limitations" in result.state_specific
        lim = result.state_specific["v1_limitations"]
        assert isinstance(lim, list)
        assert len(lim) >= 5

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
        assert rehydrated.state == "IN"


# ---------------------------------------------------------------------------
# compute() — non-Single filing statuses (dependent / MFJ / HOH)
# ---------------------------------------------------------------------------


class TestIndianaPluginFilingStatuses:
    def test_mfj_65k_doubles_personal_exemption(
        self, single_65k_return, federal_single_65k
    ):
        """A MFJ filer gets $2,000 personal exemption (vs $1,000 for
        Single). At AGI=$65k MFJ, IN TI = 65,000 - 2,000 = $63,000,
        tax = $63,000 * 0.03 = $1,890.00."""
        federal_mfj = FederalTotals(
            filing_status=FilingStatus.MFJ,
            num_dependents=0,
            adjusted_gross_income=Decimal("65000"),
            taxable_income=Decimal("33500"),  # 65000 - 31500 fed std
            total_federal_tax=Decimal("3640"),
            federal_income_tax=Decimal("3640"),
            federal_standard_deduction=Decimal("31500"),
            federal_itemized_deductions_total=Decimal("0"),
            deduction_taken=Decimal("31500"),
        )
        result = PLUGIN.compute(
            single_65k_return,
            federal_mfj,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific[
            "state_exemption_allowance"
        ] == Decimal("2000.00")
        assert result.state_specific["state_taxable_income"] == Decimal(
            "63000.00"
        )
        assert result.state_specific["state_total_tax"] == Decimal(
            "1890.00"
        )

    def test_single_with_two_dependents(
        self, single_65k_return, federal_single_65k
    ):
        """Single + 2 dependents = $1,000 (personal) + $2,000 (deps) =
        $3,000 exemption. TI = $65,000 - $3,000 = $62,000.
        Tax = $62,000 * 0.03 = $1,860.00."""
        federal_with_deps = FederalTotals(
            filing_status=FilingStatus.SINGLE,
            num_dependents=2,
            adjusted_gross_income=Decimal("65000"),
            taxable_income=Decimal("49250"),
            total_federal_tax=Decimal("5755"),
            federal_income_tax=Decimal("5755"),
            federal_standard_deduction=Decimal("15750"),
            federal_itemized_deductions_total=Decimal("0"),
            deduction_taken=Decimal("15750"),
        )
        result = PLUGIN.compute(
            single_65k_return,
            federal_with_deps,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific[
            "state_exemption_allowance"
        ] == Decimal("3000.00")
        assert result.state_specific["state_total_tax"] == Decimal(
            "1860.00"
        )

    def test_hoh_one_dependent(
        self, single_65k_return, federal_single_65k
    ):
        """HOH + 1 dep = $1,000 (personal) + $1,000 (dep) = $2,000
        exemption. TI = $65,000 - $2,000 = $63,000. Tax = $1,890.00."""
        federal_hoh = FederalTotals(
            filing_status=FilingStatus.HOH,
            num_dependents=1,
            adjusted_gross_income=Decimal("65000"),
            taxable_income=Decimal("41375"),
            total_federal_tax=Decimal("4660"),
            federal_income_tax=Decimal("4660"),
            federal_standard_deduction=Decimal("23625"),
            federal_itemized_deductions_total=Decimal("0"),
            deduction_taken=Decimal("23625"),
        )
        result = PLUGIN.compute(
            single_65k_return,
            federal_hoh,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific[
            "state_exemption_allowance"
        ] == Decimal("2000.00")
        assert result.state_specific["state_total_tax"] == Decimal(
            "1890.00"
        )

    def test_low_income_floors_at_zero(
        self, single_65k_return, federal_single_65k
    ):
        """A very low-AGI Single filer (e.g. $500) has TI = max(0,
        500 - 1000) = 0, so tax = $0.00."""
        federal_low = FederalTotals(
            filing_status=FilingStatus.SINGLE,
            num_dependents=0,
            adjusted_gross_income=Decimal("500"),
            taxable_income=Decimal("0"),
            total_federal_tax=Decimal("0"),
            federal_income_tax=Decimal("0"),
            federal_standard_deduction=Decimal("15750"),
            federal_itemized_deductions_total=Decimal("0"),
            deduction_taken=Decimal("15750"),
        )
        result = PLUGIN.compute(
            single_65k_return,
            federal_low,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal(
            "0.00"
        )
        assert result.state_specific["state_total_tax"] == Decimal("0.00")


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment (day-based v1)
# ---------------------------------------------------------------------------


class TestIndianaPluginComputeNonresident:
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
        assert full == Decimal("1920.00")
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


class TestIndianaPluginApportionIncome:
    def test_apportion_income_resident_full_amounts(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert isinstance(app, IncomeApportionment)
        assert app.state_source_wages == Decimal("65000.00")
        assert app.state_source_total == Decimal("65000.00")

    def test_apportion_income_nonresident_prorates(self, single_65k_return):
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


class TestIndianaPluginFormIds:
    def test_form_ids(self):
        form_ids = PLUGIN.form_ids()
        assert "IN Form IT-40" in form_ids
        assert form_ids == ["IN Form IT-40"]

    def test_render_pdfs_produces_pdf(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """IN Form IT-40 AcroForm fill produces a non-empty PDF."""
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
        assert paths[0].name == "IN_IT40.pdf"

    def test_render_pdfs_field_values(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Verify that rendered IN IT-40 PDF contains correct field values."""
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

        # Widget "Line 1" maps to state_federal_agi (IT-40 Line 1)
        assert fields["Line 1"].get("/V") == "65000.00"
        # Widget "Line 5" maps to state_adjusted_gross_income (IT-40 Line 5)
        assert fields["Line 5"].get("/V") == "65000.00"
        # Widget "Line 6" maps to state_exemptions (IT-40 Line 6)
        assert fields["Line 6"].get("/V") == "1000.00"
        # Widget "Line 7" maps to state_taxable_income (IT-40 Line 7)
        assert fields["Line 7"].get("/V") == "64000.00"
        # Widget "Line 8" maps to state_total_tax (IT-40 Line 8)
        assert fields["Line 8"].get("/V") == "1920.00"


# ---------------------------------------------------------------------------
# ReciprocityTable consistency
# ---------------------------------------------------------------------------


def test_reciprocity_matches_json():
    """ReciprocityTable.partners_of('IN') must equal the plugin's
    meta.reciprocity_partners. Catches drift between
    skill/reference/state-reciprocity.json and the IN plugin."""
    table = ReciprocityTable.load()
    in_partners = table.partners_of("IN")
    assert in_partners == frozenset({"KY", "MI", "OH", "PA", "WI"})
    assert frozenset(PLUGIN.meta.reciprocity_partners) == in_partners


def test_reciprocity_table_recognizes_in_pairs():
    """Each of the five IN partners must satisfy are_reciprocal."""
    table = ReciprocityTable.load()
    for partner in ("KY", "MI", "OH", "PA", "WI"):
        assert table.are_reciprocal("IN", partner) is True
        assert table.are_reciprocal(partner, "IN") is True
    # IL is NOT a partner despite the long border:
    assert table.are_reciprocal("IN", "IL") is False


# ---------------------------------------------------------------------------
# Gatekeeper test — auto-detect tenforty TY2025 IN behavior changes.
# ---------------------------------------------------------------------------


class TestTenfortyDoesNotFullySupportInTy2025:
    """When this STARTS FAILING, tenforty has either added IN to the
    default backend or fixed the graph backend's missing personal
    exemption. At that point reconsider this plugin against the
    skill/reference/tenforty-ty2025-gap.md decision rubric.
    """

    def test_default_backend_still_raises(self):
        with pytest.raises(ValueError, match="OTS does not support"):
            tenforty.evaluate_return(
                year=2025,
                state="IN",
                filing_status="Single",
                w2_income=65000,
                num_dependents=0,
                standard_or_itemized="Standard",
                itemized_deductions=0,
            )

    def test_graph_backend_still_misses_personal_exemption(self):
        """Graph backend computes 65000 * 0.03 = 1950 (no $1,000
        personal exemption applied). When this STARTS FAILING — i.e.
        graph returns ~$1,920 instead — the plugin can be re-rolled
        as a graph wrapper."""
        result = tenforty.evaluate_return(
            year=2025,
            state="IN",
            filing_status="Single",
            w2_income=65000,
            num_dependents=0,
            standard_or_itemized="Standard",
            itemized_deductions=0,
            backend="graph",
        )
        graph_tax = Decimal(str(result.state_total_tax)).quantize(
            Decimal("0.01")
        )
        # 65000 * 0.03 = 1950 (no exemption)
        assert graph_tax == Decimal("1950.00")
        # The hand-rolled plugin's $1,920 number is exactly $30 less.
        assert graph_tax - Decimal("1920.00") == Decimal("30.00")
