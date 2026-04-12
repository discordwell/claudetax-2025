"""Missouri state plugin tests — TY2025.

Hand-rolled MO Form MO-1040 calc — see ``skill/scripts/states/mo.py``
docstring for the full DOR-primary-source trace. The graph backend
diverges from DOR by ~$102 on the spec's $65k Single scenario, so
this plugin is hand-rolled rather than wrapped.

Reference scenario:
    Single $65k W-2, OBBBA std ded $15,750
    -> federal AGI $65,000
    -> federal tax deduction = $5,755 * 15% = $863.25 (under $5k cap)
    -> MO TI = $65,000 - $863.25 - $15,750 = $48,386.75 → rounds to $48,387
    -> Tax (over $9,191 bracket): $256 + 4.7% * ($48,387 - $9,191)
                                  = $256 + $1,842.21
                                  = $2,098.21
                                  -> rounded to whole dollars: **$2,098**
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
from skill.scripts.states.mo import (
    LOCK_VALUE,
    MO_TY2025_BRACKETS,
    MO_TY2025_FED_TAX_DEDUCTION_CAP_MFJ,
    MO_TY2025_FED_TAX_DEDUCTION_CAP_OTHER,
    MO_TY2025_STD_DED_HOH,
    MO_TY2025_STD_DED_MFJ,
    MO_TY2025_STD_DED_SINGLE,
    MO_TY2025_TOP_RATE,
    MO_V1_LIMITATIONS,
    MissouriPlugin,
    PLUGIN,
    mo_fed_tax_deduction,
    mo_fed_tax_percentage,
    mo_standard_deduction,
    mo_tax_from_table,
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
            first_name="Show",
            last_name="Mestate",
            ssn="111-22-3333",
            date_of_birth=dt.date(1990, 1, 1),
        ),
        address=Address(
            street1="301 W High St",
            city="Jefferson City",
            state="MO",
            zip="65101",
        ),
        w2s=[
            W2(
                employer_name="Show Me State Co",
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


@pytest.fixture
def mfj_120k_return() -> CanonicalReturn:
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.MFJ,
        taxpayer=Person(
            first_name="Mark",
            last_name="Twain",
            ssn="111-22-3333",
            date_of_birth=dt.date(1985, 1, 1),
        ),
        spouse=Person(
            first_name="Olivia",
            last_name="Twain",
            ssn="222-33-4444",
            date_of_birth=dt.date(1986, 2, 2),
        ),
        address=Address(
            street1="206 Hill St",
            city="Hannibal",
            state="MO",
            zip="63401",
        ),
        w2s=[
            W2(
                employer_name="River Pilot Co",
                box1_wages=Decimal("120000"),
                box2_federal_income_tax_withheld=Decimal("0"),
            ),
        ],
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


class TestMissouriPluginMeta:
    def test_meta_fields(self):
        assert PLUGIN.meta.code == "MO"
        assert PLUGIN.meta.name == "Missouri"
        assert PLUGIN.meta.has_income_tax is True
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI

    def test_meta_no_reciprocity(self):
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_meta_submission_channel(self):
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.FED_STATE_PIGGYBACK
        )

    def test_meta_dor_url(self):
        assert "dor.mo.gov" in PLUGIN.meta.dor_url

    def test_meta_no_free_efile_portal(self):
        """MO does not run a free DOR-direct portal — only commercial MeF."""
        assert PLUGIN.meta.free_efile_url is None

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_top_rate(self):
        assert "4.7" in PLUGIN.meta.notes

    def test_meta_notes_mention_federal_tax_deduction(self):
        assert "federal income tax deduction" in PLUGIN.meta.notes.lower()

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "AR"  # type: ignore[misc]

    def test_plugin_is_state_plugin_protocol(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_missouri_plugin_instance(self):
        assert isinstance(PLUGIN, MissouriPlugin)


# ---------------------------------------------------------------------------
# Reciprocity
# ---------------------------------------------------------------------------


class TestMissouriNoReciprocity:
    def test_partners_empty(self):
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_no_reciprocity_via_table(self):
        table = ReciprocityTable.load()
        assert table.partners_of("MO") == frozenset()
        assert table.has_income_tax("MO") is True

    def test_not_reciprocal_with_neighbors(self):
        """MO borders KS, NE, IA, IL, KY, TN, AR, OK. None reciprocal."""
        table = ReciprocityTable.load()
        for neighbor in ("KS", "NE", "IA", "IL", "KY", "TN", "AR", "OK"):
            assert table.are_reciprocal("MO", neighbor) is False


# ---------------------------------------------------------------------------
# Helper functions: standard deduction, fed tax %, fed tax deduction
# ---------------------------------------------------------------------------


class TestMissouriStandardDeduction:
    def test_single(self):
        assert mo_standard_deduction(FilingStatus.SINGLE) == Decimal("15750")

    def test_mfj(self):
        assert mo_standard_deduction(FilingStatus.MFJ) == Decimal("31500")

    def test_hoh(self):
        assert mo_standard_deduction(FilingStatus.HOH) == Decimal("23625")

    def test_mfs(self):
        assert mo_standard_deduction(FilingStatus.MFS) == Decimal("15750")

    def test_qss_mirrors_mfj(self):
        assert mo_standard_deduction(FilingStatus.QSS) == Decimal("31500")

    def test_constants_match(self):
        assert MO_TY2025_STD_DED_SINGLE == Decimal("15750")
        assert MO_TY2025_STD_DED_MFJ == Decimal("31500")
        assert MO_TY2025_STD_DED_HOH == Decimal("23625")


class TestMissouriFederalTaxPercentage:
    """MO-1040 line 12 sliding scale by AGI band."""

    def test_band_at_or_under_25k(self):
        assert mo_fed_tax_percentage(Decimal("22450")) == Decimal("0.35")
        assert mo_fed_tax_percentage(Decimal("25000")) == Decimal("0.35")

    def test_band_25k_to_50k(self):
        assert mo_fed_tax_percentage(Decimal("25001")) == Decimal("0.25")
        assert mo_fed_tax_percentage(Decimal("50000")) == Decimal("0.25")

    def test_band_50k_to_100k(self):
        assert mo_fed_tax_percentage(Decimal("58750")) == Decimal("0.15")
        assert mo_fed_tax_percentage(Decimal("65000")) == Decimal("0.15")
        assert mo_fed_tax_percentage(Decimal("100000")) == Decimal("0.15")

    def test_band_100k_to_125k(self):
        assert mo_fed_tax_percentage(Decimal("100001")) == Decimal("0.05")
        assert mo_fed_tax_percentage(Decimal("125000")) == Decimal("0.05")

    def test_band_above_125k(self):
        assert mo_fed_tax_percentage(Decimal("125001")) == Decimal("0")
        assert mo_fed_tax_percentage(Decimal("500000")) == Decimal("0")


class TestMissouriFederalTaxDeduction:
    def test_negative_federal_tax_yields_zero(self):
        assert mo_fed_tax_deduction(
            Decimal("-100"), Decimal("65000"), FilingStatus.SINGLE
        ) == Decimal("0")

    def test_below_cap_single(self):
        # $5,755 * 15% = $863.25, well below $5,000 cap
        assert mo_fed_tax_deduction(
            Decimal("5755"), Decimal("65000"), FilingStatus.SINGLE
        ) == Decimal("863.25")

    def test_capped_single(self):
        # Federal tax of $40,000 * 35% (low AGI band) = $14,000 → cap to $5k
        assert mo_fed_tax_deduction(
            Decimal("40000"), Decimal("20000"), FilingStatus.SINGLE
        ) == MO_TY2025_FED_TAX_DEDUCTION_CAP_OTHER

    def test_capped_mfj(self):
        assert mo_fed_tax_deduction(
            Decimal("80000"), Decimal("20000"), FilingStatus.MFJ
        ) == MO_TY2025_FED_TAX_DEDUCTION_CAP_MFJ

    def test_zero_when_pct_band_zero(self):
        assert mo_fed_tax_deduction(
            Decimal("10000"), Decimal("200000"), FilingStatus.SINGLE
        ) == Decimal("0")


# ---------------------------------------------------------------------------
# Tax Rate Chart — bracket math
# ---------------------------------------------------------------------------


class TestMissouriBrackets:
    def test_zero_income(self):
        assert mo_tax_from_table(Decimal("0")) == Decimal("0.00")

    def test_under_first_bracket(self):
        assert mo_tax_from_table(Decimal("1313")) == Decimal("0.00")

    def test_first_bracket_top(self):
        # $2,626 - $1,313 = $1,313 * 2% = $26.26
        assert mo_tax_from_table(Decimal("2626")) == Decimal("26.26")

    def test_top_bracket_starts_at_9191(self):
        """Just over $9,191 the rate is 4.7% on the excess."""
        # At $9,191 exactly, sum of all sub-brackets:
        # 1313*0 + 1313*.02 + 1313*.025 + 1313*.03 + 1313*.035 + 1313*.04 + 1313*.045
        # = 0 + 26.26 + 32.825 + 39.39 + 45.955 + 52.52 + 59.085
        # = 256.035 → cents = 256.04 (instructions print $256)
        assert mo_tax_from_table(Decimal("9191")) == Decimal("256.04")

    def test_top_marginal_rate_constant(self):
        assert MO_TY2025_TOP_RATE == Decimal("0.047")

    def test_brackets_count(self):
        assert len(MO_TY2025_BRACKETS) == 8

    def test_top_bracket_unbounded(self):
        assert MO_TY2025_BRACKETS[-1].high is None
        assert MO_TY2025_BRACKETS[-1].rate == Decimal("0.047")

    def test_high_income_top_bracket(self):
        """At $48,387 (the spec's $65k Single TI):
        Sum of low brackets ≈ $256 + 4.7% * (48387 - 9191)
        = 256.035 + 4.7% * 39196
        = 256.035 + 1842.212
        = $2098.247 → cents = $2098.25
        """
        assert mo_tax_from_table(Decimal("48387")) == Decimal("2098.25")


# ---------------------------------------------------------------------------
# compute() — resident
# ---------------------------------------------------------------------------


class TestMissouriPluginComputeResident:
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
        assert result.state == "MO"
        assert result.residency == ResidencyStatus.RESIDENT

    def test_resident_single_65k_tax_lock(
        self, single_65k_return, federal_single_65k
    ):
        """**SPEC-MANDATED $65k Single LOCK**: $2,098.00 (whole-dollar
        rounded per MO-1040 instructions). Hand-traced from the 2025
        MO-1040 worksheet — see module docstring for the full line-by-
        line trace. The graph backend's $2,200.52 is wrong because it
        omits MO's federal income tax deduction (line 13)."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_total_tax"] == LOCK_VALUE
        assert (
            result.state_specific["state_total_tax_resident_basis"]
            == LOCK_VALUE
        )

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
        assert ss["state_adjusted_gross_income"] == Decimal("65000.00")
        assert ss["state_federal_tax_input"] == Decimal("5755.00")
        assert ss["state_federal_tax_percentage"] == Decimal("0.15")
        assert ss["state_federal_tax_deduction"] == Decimal("863.25")
        assert ss["state_standard_deduction"] == Decimal("15750.00")
        assert ss["state_total_deductions"] == Decimal("16613.25")
        # MO TI rounds to whole dollars: 48386.75 → 48387
        assert ss["state_taxable_income"] == Decimal("48387.00")

    def test_resident_mfj_120k(self, mfj_120k_return, federal_mfj_120k):
        """MFJ $120k.

        federal AGI $120,000 → MO AGI $120,000
        federal tax = $10,173, AGI $100,001-$125,000 band → 5%
        fed tax deduction = $10,173 * 0.05 = $508.65 (under $10k cap)
        std ded MFJ = $31,500
        total ded = $32,008.65
        MO TI = $120,000 - $32,008.65 = $87,991.35 → $87,991
        Tax = $256 + 4.7% * ($87,991 - $9,191)
            = $256 + 4.7% * $78,800
            = $256 + $3,703.60
            = $3,959.60 → $3,960 (whole-dollar rounding)
        """
        result = PLUGIN.compute(
            mfj_120k_return,
            federal_mfj_120k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = result.state_specific
        assert ss["state_federal_tax_percentage"] == Decimal("0.05")
        assert ss["state_federal_tax_deduction"] == Decimal("508.65")
        assert ss["state_standard_deduction"] == Decimal("31500.00")
        assert ss["state_taxable_income"] == Decimal("87991.00")
        # 256.035 + 0.047 * (87991 - 9191) = 256.035 + 3,703.60 = 3,959.635
        # → whole dollars: 3,960
        assert ss["state_total_tax"] == Decimal("3960.00")

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
            "state_federal_tax_input",
            "state_federal_tax_percentage",
            "state_federal_tax_deduction",
            "state_standard_deduction",
            "state_total_deductions",
            "state_taxable_income",
            "state_total_tax",
            "state_total_tax_resident_basis",
            "state_top_marginal_rate",
            "apportionment_fraction",
        ):
            assert k in result.state_specific
            assert isinstance(result.state_specific[k], Decimal)

    def test_zero_income_yields_zero_tax(self):
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="Z",
                last_name="Z",
                ssn="999-88-7777",
                date_of_birth=dt.date(1990, 1, 1),
            ),
            address=Address(
                street1="1 Main", city="St Louis", state="MO", zip="63101"
            ),
        )
        fed = FederalTotals(
            filing_status=FilingStatus.SINGLE,
            num_dependents=0,
            adjusted_gross_income=Decimal("0"),
            taxable_income=Decimal("0"),
            total_federal_tax=Decimal("0"),
            federal_income_tax=Decimal("0"),
            federal_standard_deduction=Decimal("15750"),
            federal_itemized_deductions_total=Decimal("0"),
            deduction_taken=Decimal("15750"),
        )
        result = PLUGIN.compute(
            ret, fed, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert result.state_specific["state_taxable_income"] == Decimal("0.00")
        assert result.state_specific["state_total_tax"] == Decimal("0.00")

    def test_starting_point_is_federal_agi(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["starting_point"] == "federal_agi"

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
        assert rehydrated.state == "MO"


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year
# ---------------------------------------------------------------------------


class TestMissouriPluginComputeNonresident:
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
        assert full == Decimal("2098.00")
        expected = (full * Decimal(182) / Decimal(365)).quantize(
            Decimal("0.01")
        )
        assert apportioned == expected

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


class TestMissouriApportionIncome:
    def test_resident_full_amounts(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert isinstance(app, IncomeApportionment)
        assert app.state_source_wages == Decimal("65000.00")


class TestMissouriFormIds:
    def test_form_ids(self):
        assert PLUGIN.form_ids() == ["MO Form MO-1040"]

    def test_render_pdfs_produces_pdf(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """MO Form MO-1040 AcroForm fill produces a non-empty PDF."""
        try:
            from pypdf import PdfReader
        except BaseException:
            pytest.skip("pypdf unavailable")

        sr = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        paths = PLUGIN.render_pdfs(sr, tmp_path)
        assert len(paths) == 1
        assert paths[0].exists()
        assert paths[0].stat().st_size > 0
        assert paths[0].name == "mo_1040.pdf"

    def test_render_pdfs_field_values(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Verify rendered MO-1040 PDF contains correct field values."""
        try:
            from pypdf import PdfReader
        except BaseException:
            pytest.skip("pypdf unavailable")

        sr = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        paths = PLUGIN.render_pdfs(sr, tmp_path)
        reader = PdfReader(str(paths[0]))
        fields = reader.get_fields()
        assert fields is not None

        # Widget "36" maps to state_total_tax (line 36)
        assert fields["36"].get("/V") == "2098.00"
        # Widget "29Y" maps to state_taxable_income (line 29 Yourself)
        assert fields["29Y"].get("/V") == "48387.00"
        # Widget "6" maps to state_adjusted_gross_income (line 6)
        assert fields["6"].get("/V") == "65000.00"


# ---------------------------------------------------------------------------
# v1 limitations
# ---------------------------------------------------------------------------


class TestMissouriV1Limitations:
    def test_limitations_is_tuple(self):
        assert isinstance(MO_V1_LIMITATIONS, tuple)
        assert len(MO_V1_LIMITATIONS) >= 5

    def test_limitations_mention_form_mo_a(self):
        joined = " ".join(MO_V1_LIMITATIONS)
        assert "Form MO-A" in joined or "MO-A" in joined

    def test_limitations_mention_resident_credit(self):
        joined = " ".join(MO_V1_LIMITATIONS)
        assert "Resident credit" in joined or "MO-CR" in joined

    def test_limitations_mention_nonresident(self):
        joined = " ".join(MO_V1_LIMITATIONS).lower()
        assert "nonresident" in joined


# ---------------------------------------------------------------------------
# Gatekeeper — pin tenforty's MO gap (default backend) AND drift on graph
# ---------------------------------------------------------------------------


class TestMissouriTenfortyGapGatekeeper:
    """When tenforty fixes the default backend OR fixes the graph
    backend's MO federal-tax-deduction omission, these tests will start
    failing and the next state agent should consider promoting MO to a
    wrap-style plugin.
    """

    def test_tenforty_default_backend_still_raises_for_mo(self):
        with pytest.raises(ValueError, match="OTS does not support"):
            tenforty.evaluate_return(
                year=2025,
                state="MO",
                filing_status="Single",
                w2_income=65000,
                num_dependents=0,
                standard_or_itemized="Standard",
                itemized_deductions=0,
            )

    def test_tenforty_graph_backend_still_diverges_from_dor(self):
        """Pin the graph backend's WRONG result so we can detect when
        tenforty fixes the federal income tax deduction handling. When
        the graph result equals our DOR-traced $2,098.00 (or rounds
        within $5 of it), this test fails and we can convert MO to a
        graph-wrapper plugin.
        """
        r = tenforty.evaluate_return(
            year=2025,
            state="MO",
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
        # Graph value at the time of writing: $2,200.52
        assert graph_value == Decimal("2200.52")
        # Document the magnitude of the divergence (~$102.52)
        plugin_value = Decimal("2098.00")
        delta = abs(graph_value - plugin_value)
        assert delta > Decimal("5"), (
            f"Graph backend now agrees with DOR (delta={delta} <= $5). "
            f"Consider converting mo.py to a graph-wrapper plugin."
        )
