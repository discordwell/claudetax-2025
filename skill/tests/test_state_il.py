"""Illinois state plugin tests — HAND-ROLLED plugin (no tenforty).

Illinois is not supported by OpenTaxSolver (no 2025 IL_1040 module), so
`skill/scripts/states/il.py` computes the IL-1040 flat-rate calc in
Python directly. These tests pin down the calc so regressions are caught
at the cent level, and lock in the v1 limitations list so downstream
consumers don't guess what's modeled.

Rate / base (TY2025, verified 2026-04-11 via WebFetch):
    - Flat rate: 4.95% of IL net income. Cite:
      https://tax.illinois.gov/research/taxrates/income.html
    - Personal exemption allowance: $2,850 per exemption. Cite:
      https://tax.illinois.gov/forms/incometax/currentyear/individual/il-1040-instr/what-is-new.html
    - Reciprocity partners: IA, KY, MI, WI (4 states). Cite:
      skill/reference/state-reciprocity.json

Reference computations pinned in the tests below:

- Single / $65,000 W-2 / 0 dependents
    exemption = 1 * $2,850 = $2,850
    taxable   = 65,000 - 2,850 = $62,150
    tax       = 62,150 * 0.0495 = $3,076.425 -> $3,076.43 (ROUND_HALF_UP)

- MFJ / $120,000 AGI / 2 dependents
    exemption = 4 * $2,850 = $11,400   (taxpayer + spouse + 2 deps)
    taxable   = 120,000 - 11,400 = $108,600
    tax       = 108,600 * 0.0495 = $5,375.70 (exact)
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.models import (
    Address,
    CanonicalReturn,
    FilingStatus,
    Form1099DIV,
    Form1099INT,
    Form1099R,
    FormSSA1099,
    Person,
    ResidencyStatus,
    ScheduleC,
    ScheduleE,
    ScheduleEProperty,
    StateReturn,
    W2,
    W2StateRow,
)
from skill.scripts.states._plugin_api import (
    FederalTotals,
    IncomeApportionment,
    StatePlugin,
    StateStartingPoint,
    SubmissionChannel,
)
from skill.scripts.states.il import (
    IL_FLAT_RATE,
    IL_PERSONAL_EXEMPTION_TY2025,
    PLUGIN,
    IllinoisPlugin,
)

from decimal import ROUND_HALF_UP

_CENTS = Decimal("0.01")


def _rhu(v: Decimal) -> Decimal:
    """Round to cents using ROUND_HALF_UP (matching the plugin)."""
    return v.quantize(_CENTS, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def single_65k_return() -> CanonicalReturn:
    """Single filer, $65k W-2 wages, Chicago address, no dependents."""
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
            street1="100 N State St", city="Chicago", state="IL", zip="60602"
        ),
        w2s=[
            W2(
                employer_name="Prairie Corp",
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
def mfj_120k_two_deps_return() -> CanonicalReturn:
    """MFJ, two dependent children, $120k combined wages."""
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.MFJ,
        taxpayer=Person(
            first_name="Pat",
            last_name="Roe",
            ssn="222-33-4444",
            date_of_birth=dt.date(1985, 3, 15),
        ),
        spouse=Person(
            first_name="Sam",
            last_name="Roe",
            ssn="333-44-5555",
            date_of_birth=dt.date(1986, 7, 20),
        ),
        address=Address(
            street1="200 E Wacker Dr",
            city="Chicago",
            state="IL",
            zip="60601",
        ),
        w2s=[
            W2(
                employer_name="Lakeside LLC",
                box1_wages=Decimal("120000"),
                box2_federal_income_tax_withheld=Decimal("0"),
            ),
        ],
    )


@pytest.fixture
def federal_mfj_120k_two_deps() -> FederalTotals:
    return FederalTotals(
        filing_status=FilingStatus.MFJ,
        num_dependents=2,
        adjusted_gross_income=Decimal("120000"),
        taxable_income=Decimal("88500"),
        total_federal_tax=Decimal("10000"),
        federal_income_tax=Decimal("10000"),
        federal_standard_deduction=Decimal("31500"),
        federal_itemized_deductions_total=Decimal("0"),
        deduction_taken=Decimal("31500"),
        federal_withholding_from_w2s=Decimal("0"),
    )


# ---------------------------------------------------------------------------
# Metadata / protocol conformance
# ---------------------------------------------------------------------------


class TestIllinoisPluginMeta:
    def test_plugin_is_state_plugin_protocol(self):
        """runtime_checkable Protocol must recognize our concrete plugin."""
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_illinois_plugin_instance(self):
        assert isinstance(PLUGIN, IllinoisPlugin)

    def test_meta_fields(self):
        """Core metadata: code, starting point, reciprocity partners."""
        assert PLUGIN.meta.code == "IL"
        assert PLUGIN.meta.name == "Illinois"
        assert PLUGIN.meta.has_income_tax is True
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )
        # Reciprocity: exactly four Midwestern commuter-belt partners.
        partners = set(PLUGIN.meta.reciprocity_partners)
        assert partners == {"IA", "KY", "MI", "WI"}

    def test_meta_dor_url(self):
        assert "tax.illinois.gov" in PLUGIN.meta.dor_url

    def test_meta_free_efile_url_is_mytax_illinois(self):
        assert PLUGIN.meta.free_efile_url is not None
        assert "mytax.illinois.gov" in PLUGIN.meta.free_efile_url.lower()

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "WI"  # type: ignore[misc]

    def test_meta_notes_mention_flat_rate_and_hand_rolled(self):
        assert "4.95" in PLUGIN.meta.notes
        assert "HAND-ROLLED" in PLUGIN.meta.notes

    def test_flat_rate_constant_is_4_95_percent(self):
        assert IL_FLAT_RATE == Decimal("0.0495")

    def test_personal_exemption_constant_is_2850(self):
        """TY2025 IL-1040 Step 4 — verified via WebFetch of the IL DOR TY2025
        IL-1040 instructions (what's-new and step-4-exemptions pages)."""
        assert IL_PERSONAL_EXEMPTION_TY2025 == Decimal("2850")


# ---------------------------------------------------------------------------
# compute() — resident case
# ---------------------------------------------------------------------------


class TestIllinoisPluginComputeResident:
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

    def test_state_code_is_il(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state == "IL"

    def test_residency_and_days_preserved(
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

    def test_resident_single_65k_no_deps(
        self, single_65k_return, federal_single_65k
    ):
        """Single / $65k AGI / 0 deps.

            exemption = 1 * $2,850 = $2,850
            taxable   = $65,000 - $2,850 = $62,150
            tax       = $62,150 * 0.0495 = $3,076.425 -> $3,076.43
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = result.state_specific
        assert ss["state_base_income_approx"] == Decimal("65000.00")
        assert ss["state_exemption_count"] == 1
        assert ss["state_exemption_total"] == Decimal("2850.00")
        assert ss["state_taxable_income"] == Decimal("62150.00")
        assert ss["state_total_tax"] == Decimal("3076.43")
        assert ss["flat_rate"] == Decimal("0.0495")

    def test_resident_mfj_120k_two_deps(
        self, mfj_120k_two_deps_return, federal_mfj_120k_two_deps
    ):
        """MFJ / $120k AGI / 2 deps.

            exemption_count = 4 (taxpayer + spouse + 2 deps)
            exemption       = 4 * $2,850 = $11,400
            taxable         = $120,000 - $11,400 = $108,600
            tax             = $108,600 * 0.0495 = $5,375.70 (exact)
        """
        result = PLUGIN.compute(
            mfj_120k_two_deps_return,
            federal_mfj_120k_two_deps,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = result.state_specific
        assert ss["state_base_income_approx"] == Decimal("120000.00")
        assert ss["state_exemption_count"] == 4
        assert ss["state_exemption_total"] == Decimal("11400.00")
        assert ss["state_taxable_income"] == Decimal("108600.00")
        assert ss["state_total_tax"] == Decimal("5375.70")

    def test_state_total_tax_is_decimal(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert isinstance(result.state_specific["state_total_tax"], Decimal)

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

    def test_resident_apportioned_equals_resident_basis(
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

    def test_pydantic_round_trip(
        self, single_65k_return, federal_single_65k
    ):
        """Verify the StateReturn validates under the canonical JSON
        round-trip, including the v1_limitations list and all Decimal
        fields."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        dumped = result.model_dump(mode="json")
        # json.dumps / loads is an extra sanity check — ensures no non-JSON
        # primitives leaked into state_specific.
        rehydrated = StateReturn.model_validate(json.loads(json.dumps(dumped)))
        assert rehydrated.state == "IL"
        assert rehydrated.residency == ResidencyStatus.RESIDENT

    def test_low_income_floors_taxable_at_zero(self, single_65k_return):
        """A taxpayer with base income below the exemption should pay $0,
        not negative tax."""
        low_federal = FederalTotals(
            filing_status=FilingStatus.SINGLE,
            num_dependents=0,
            adjusted_gross_income=Decimal("2000"),
            taxable_income=Decimal("0"),
            total_federal_tax=Decimal("0"),
            federal_income_tax=Decimal("0"),
            federal_standard_deduction=Decimal("15750"),
            federal_itemized_deductions_total=Decimal("0"),
            deduction_taken=Decimal("15750"),
            federal_withholding_from_w2s=Decimal("0"),
        )
        result = PLUGIN.compute(
            single_65k_return,
            low_federal,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal("0.00")
        assert result.state_specific["state_total_tax"] == Decimal("0.00")


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment
# ---------------------------------------------------------------------------


class TestIllinoisPluginComputeNonresident:
    def test_nonresident_half_year_prorates(
        self, single_65k_return, federal_single_65k
    ):
        """182 / 365 proration of the resident-basis tax. This is v1
        day-based; TODO(il-sched-nr) for real income sourcing."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=182,
        )
        full = result.state_specific["state_total_tax_resident_basis"]
        apportioned = result.state_specific["state_total_tax"]
        assert full == Decimal("3076.43")
        expected = (full * Decimal(182) / Decimal(365)).quantize(_CENTS, rounding=ROUND_HALF_UP)
        assert apportioned == expected
        # Sanity: roughly half.
        assert Decimal("1500.00") < apportioned < Decimal("1600.00")

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
            result.state_specific["apportionment_fraction"] == expected_fraction
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

    def test_full_year_nonresident_equals_resident_tax(
        self, single_65k_return, federal_single_65k
    ):
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


class TestIllinoisPluginApportionIncome:
    def test_apportion_income_resident(self, single_65k_return):
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

    def test_apportion_income_nonresident(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return,
            ResidencyStatus.NONRESIDENT,
            days_in_state=182,
        )
        expected = (Decimal("65000") * Decimal(182) / Decimal(365)).quantize(
            Decimal("0.01")
        )
        assert app.state_source_wages == expected
        assert app.state_source_total == expected


# ---------------------------------------------------------------------------
# v1 limitations — documented loudly
# ---------------------------------------------------------------------------


class TestIllinoisPluginV1Limitations:
    def test_v1_limitations_documented(
        self, single_65k_return, federal_single_65k
    ):
        """The plugin MUST surface a v1_limitations list on state_specific,
        and that list MUST mention Schedule M / additions-and-subtractions
        so downstream consumers know the gap exists."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        lims = result.state_specific.get("v1_limitations")
        assert isinstance(lims, list)
        assert len(lims) > 0
        joined = " ".join(lims).lower()
        assert "sch m" in joined or "schedule m" in joined
        assert (
            "additions" in joined and "subtractions" in joined
        ), "v1_limitations should explicitly mention IL additions/subtractions"

    def test_v1_limitations_mentions_phase_out(
        self, single_65k_return, federal_single_65k
    ):
        """Exemption phase-out cliff is a known gap — make sure it's surfaced."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        joined = " ".join(result.state_specific["v1_limitations"]).lower()
        assert "phase" in joined or "phase-out" in joined

    def test_v1_limitations_mentions_schedule_nr(
        self, single_65k_return, federal_single_65k
    ):
        """Day-based nonresident proration is a stopgap — Schedule NR is
        the real answer. Lock this note in so it doesn't silently vanish."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        joined = " ".join(result.state_specific["v1_limitations"]).lower()
        assert "schedule nr" in joined or "sched nr" in joined or (
            "day-based" in joined and "nonresident" in joined
        )


# ---------------------------------------------------------------------------
# Reciprocity cross-check vs. reference JSON
# ---------------------------------------------------------------------------


_RECIPROCITY_PATH = (
    Path(__file__).resolve().parent.parent
    / "reference"
    / "state-reciprocity.json"
)


class TestIllinoisReciprocityMatchesJson:
    def test_reciprocity_matches_json(self):
        """PLUGIN.meta.reciprocity_partners must match the set of IL pairs
        in skill/reference/state-reciprocity.json. If reciprocity.json
        changes, this test fails and the plugin tuple must be updated."""
        raw = json.loads(_RECIPROCITY_PATH.read_text())
        json_partners: set[str] = set()
        for entry in raw["agreements"]:
            states = entry["states"]
            if "IL" in states:
                other = next(s for s in states if s != "IL")
                json_partners.add(other)
        assert set(PLUGIN.meta.reciprocity_partners) == json_partners
        # Sanity: four Midwestern partners.
        assert json_partners == {"IA", "KY", "MI", "WI"}


# ---------------------------------------------------------------------------
# render_pdfs() / form_ids()
# ---------------------------------------------------------------------------


class TestIllinoisPluginFormIds:
    def test_form_ids_returns_il_1040(self):
        assert PLUGIN.form_ids() == ["IL Form IL-1040"]

    def test_render_pdfs_produces_pdf(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """IL-1040 AcroForm fill produces a non-empty PDF."""
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
        assert paths[0].name == "IL_1040.pdf"

    def test_render_pdfs_field_values(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Verify that rendered IL-1040 PDF contains correct field values."""
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

        # "Federally adjusted income" = state_federal_agi (IL-1040 Line 1)
        assert fields["Federally adjusted income"].get("/V") == "65000.00"
        # "Exemption allowance" = state_exemption_allowance (IL-1040 Line 10)
        assert fields["Exemption allowance"].get("/V") == "2850.00"
        # "Income tax" = state_income_tax (IL-1040 Line 12)
        # $65,000 - $2,850 exemption = $62,150 * 0.0495 = $3,076.43
        assert fields["Income tax"].get("/V") == "3076.43"


# ---------------------------------------------------------------------------
# Wave 4: IL-1040 line 2 + line 5 + Schedule M line 22 adds/subs
# ---------------------------------------------------------------------------
#
# Each test in this class takes the baseline $65k single IL filer and
# attaches exactly one canonical return field (a 1099-INT with US Treasury
# interest, a 1099-R, a SSA-1099, a 1099-INT/1099-DIV with muni interest).
# Every test asserts the DELTA between the baseline IL tax and the IL tax
# with the adjustment applied is exactly ``rate * amount`` (or the negative
# for an addback), at the cent. This structure catches (a) wiring bugs,
# (b) rounding drift, and (c) accidental double-counting on sum lines.
#
# Baseline for the $65k single filer is locked in the wave-3 class
# ``TestIllinoisPluginComputeResident`` at $3,076.43.


def _add_1099_int(
    return_: CanonicalReturn,
    box1: str = "0",
    box3: str = "0",
    box8: str = "0",
) -> CanonicalReturn:
    """Append a 1099-INT to a canonical return — helper for deltas."""
    return_.forms_1099_int.append(
        Form1099INT(
            payer_name="Test Broker",
            box1_interest_income=Decimal(box1),
            box3_us_savings_bond_and_treasury_interest=Decimal(box3),
            box8_tax_exempt_interest=Decimal(box8),
        )
    )
    return return_


def _add_1099_div(
    return_: CanonicalReturn, box11: str = "0"
) -> CanonicalReturn:
    return_.forms_1099_div.append(
        Form1099DIV(
            payer_name="Test Fund",
            box11_exempt_interest_dividends=Decimal(box11),
        )
    )
    return return_


def _add_1099_r(
    return_: CanonicalReturn,
    box2a: str,
    recipient_is_taxpayer: bool = True,
) -> CanonicalReturn:
    return_.forms_1099_r.append(
        Form1099R(
            payer_name="Test Pension",
            box1_gross_distribution=Decimal(box2a),
            box2a_taxable_amount=Decimal(box2a),
            recipient_is_taxpayer=recipient_is_taxpayer,
        )
    )
    return return_


def _add_ssa_1099(
    return_: CanonicalReturn,
    box5: str,
    recipient_is_taxpayer: bool = True,
) -> CanonicalReturn:
    return_.forms_ssa_1099.append(
        FormSSA1099(
            recipient_is_taxpayer=recipient_is_taxpayer,
            box5_net_benefits=Decimal(box5),
        )
    )
    return return_


class TestIllinoisPluginAddsAndSubsWave4:
    # -----------------------------------------------------------------
    # Schedule M Step 2 Line 22 — US Treasury interest subtraction
    # -----------------------------------------------------------------

    def test_us_treasury_interest_reduces_tax_by_rate_times_amount(
        self, single_65k_return, federal_single_65k
    ):
        """Attaching a 1099-INT with box 3 = $1,000 should reduce IL tax
        by exactly 0.0495 * $1,000 = $49.50.

        Source: IL-1040 Schedule M Step 2 Line 22 — "U.S. Treasury bonds,
        bills, notes, savings bonds, and U.S. agency interest from
        federal Form 1040 or 1040-SR".
        """
        baseline = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        with_treasury = PLUGIN.compute(
            _add_1099_int(single_65k_return, box3="1000"),
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        delta = (
            baseline.state_specific["state_total_tax"]
            - with_treasury.state_specific["state_total_tax"]
        )
        assert delta == Decimal("49.50")
        assert with_treasury.state_specific["il_subtractions"][
            "il_schedule_m_line22_us_treasury_subtraction"
        ] == Decimal("1000.00")

    def test_us_treasury_subtraction_aggregates_multiple_1099_ints(
        self, single_65k_return, federal_single_65k
    ):
        """Multiple 1099-INTs with box 3 amounts should sum to a single
        subtraction; delta is 0.0495 * total box3."""
        _add_1099_int(single_65k_return, box3="750")
        _add_1099_int(single_65k_return, box3="250")
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["il_subtractions"][
            "il_schedule_m_line22_us_treasury_subtraction"
        ] == Decimal("1000.00")
        # Same tax as the combined-$1000 single form case.
        assert result.state_specific["state_total_tax"] == Decimal("3026.93")

    # -----------------------------------------------------------------
    # IL-1040 Line 5 — Social Security subtraction
    # -----------------------------------------------------------------

    def test_social_security_benefits_subtracted_100_percent(
        self, single_65k_return, federal_single_65k
    ):
        """IL does not tax Social Security; 100% of SSA-1099 box 5 is
        subtracted on IL-1040 Line 5 per the IL-1040 instructions
        page 2 'Tips To Speed Up' bullet."""
        _add_ssa_1099(single_65k_return, box5="20000")
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["il_subtractions"][
            "il_1040_line5_social_security_subtraction"
        ] == Decimal("20000.00")
        # Baseline $3,076.43 - (0.0495 * 20,000) = $3,076.43 - $990 = $2,086.43
        assert result.state_specific["state_total_tax"] == Decimal("2086.43")

    # -----------------------------------------------------------------
    # IL-1040 Line 5 — Qualified retirement income subtraction
    # -----------------------------------------------------------------

    def test_qualified_retirement_income_subtracted(
        self, single_65k_return, federal_single_65k
    ):
        """IL is retirement-friendly: 100% of 1099-R box 2a taxable
        amount comes off IL base income on Line 5."""
        _add_1099_r(single_65k_return, box2a="15000")
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["il_subtractions"][
            "il_1040_line5_retirement_income_subtraction"
        ] == Decimal("15000.00")
        # Baseline - 0.0495 * 15000 = 3076.43 - 742.50 = 2333.93
        assert result.state_specific["state_total_tax"] == Decimal("2333.93")

    def test_pension_plus_us_treasury_combine_linearly(
        self, single_65k_return, federal_single_65k
    ):
        """Multiple subtractions combine linearly at the cent."""
        _add_1099_r(single_65k_return, box2a="10000")
        _add_1099_int(single_65k_return, box3="2000")
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        # Combined subtraction = $12,000 -> tax reduced by 0.0495*12000 = 594.
        # Baseline 3076.43 - 594.00 = 2482.43
        assert result.state_specific["il_subtractions_total"] == Decimal(
            "12000.00"
        )
        assert result.state_specific["state_total_tax"] == Decimal("2482.43")

    # -----------------------------------------------------------------
    # IL-1040 Line 2 — Non-IL muni interest addback
    # -----------------------------------------------------------------

    def test_non_il_muni_interest_added_back_increases_tax(
        self, single_65k_return, federal_single_65k
    ):
        """Non-IL muni interest (1099-INT box 8) is added back on
        IL-1040 Line 2, which INCREASES IL tax by 0.0495 * box8."""
        _add_1099_int(single_65k_return, box8="5000")
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        # Baseline + 0.0495 * 5000 = 3076.43 + 247.50 = 3323.93
        assert result.state_specific["il_additions"][
            "il_1040_line2_tax_exempt_interest_addback"
        ] == Decimal("5000.00")
        assert result.state_specific["state_total_tax"] == Decimal("3323.93")

    def test_muni_addback_handles_both_1099_int_and_1099_div(
        self, single_65k_return, federal_single_65k
    ):
        """Both 1099-INT box 8 and 1099-DIV box 11 feed Line 2."""
        _add_1099_int(single_65k_return, box8="2000")
        _add_1099_div(single_65k_return, box11="3000")
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["il_additions_total"] == Decimal(
            "5000.00"
        )
        assert result.state_specific["state_total_tax"] == Decimal("3323.93")

    # -----------------------------------------------------------------
    # Adds + subs combined
    # -----------------------------------------------------------------

    def test_adds_and_subs_net_against_each_other(
        self, single_65k_return, federal_single_65k
    ):
        """$1,000 non-IL muni addback + $1,000 US Treasury subtraction
        should net to $0 delta — same tax as the baseline."""
        _add_1099_int(single_65k_return, box3="1000", box8="1000")
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_total_tax"] == Decimal("3076.43")
        assert result.state_specific["il_additions_total"] == Decimal(
            "1000.00"
        )
        assert result.state_specific["il_subtractions_total"] == Decimal(
            "1000.00"
        )

    # -----------------------------------------------------------------
    # Floor behavior — subtractions can't push taxable < 0
    # -----------------------------------------------------------------

    def test_huge_retirement_subtraction_floors_at_zero(
        self, single_65k_return, federal_single_65k
    ):
        """A pension distribution larger than federal AGI should not
        produce negative IL tax."""
        _add_1099_r(single_65k_return, box2a="500000")
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal("0.00")
        assert result.state_specific["state_total_tax"] == Decimal("0.00")

    # -----------------------------------------------------------------
    # v1_limitations — new slugs for wave 4
    # -----------------------------------------------------------------

    def test_v1_limitations_document_wave4_partial_closure(
        self, single_65k_return, federal_single_65k
    ):
        """Wave 4 limitations list should still mention what's NOT
        modeled (phase-out cliff, in-state muni carve-out, 1099-R
        distribution-code gating)."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        joined = " ".join(result.state_specific["v1_limitations"]).lower()
        assert "in-state muni" in joined or "in-state" in joined
        assert "distribution-code" in joined or "distribution code" in joined
        assert "phase-out" in joined or "phase out" in joined


# ---------------------------------------------------------------------------
# Schedule NR nonresident income apportionment (replaces day-proration)
# ---------------------------------------------------------------------------
#
# IL nonresident taxation follows Schedule NR:
#   Column A = total income from all sources (federal amounts)
#   Column B = Illinois-source income
#
# IL-source rules:
#   - Wages: IL-source if work performed in IL (W-2 state rows with state="IL")
#   - Interest/Dividends: NOT IL-source for nonresidents
#   - Business income: IL-source if business operated in IL
#   - Rental income: IL-source if property located in IL
#
# Exemption is prorated: (IL income / total income) * full exemption
# Tax = (IL base income - prorated exemption) * 4.95%


def _nonresident_return_with_il_w2(
    wages: str = "50000",
    il_state_wages: str = "50000",
) -> CanonicalReturn:
    """Nonresident with a W-2 that has IL state rows."""
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=Person(
            first_name="Nora",
            last_name="Nonres",
            ssn="555-66-7777",
            date_of_birth=dt.date(1988, 5, 15),
        ),
        address=Address(
            street1="100 Main St", city="Indianapolis", state="IN", zip="46204"
        ),
        w2s=[
            W2(
                employer_name="IL Employer Inc",
                box1_wages=Decimal(wages),
                box2_federal_income_tax_withheld=Decimal("0"),
                state_rows=[
                    W2StateRow(
                        state="IL",
                        state_wages=Decimal(il_state_wages),
                    ),
                ],
            ),
        ],
    )


def _federal_for_agi(agi: str, filing_status: FilingStatus = FilingStatus.SINGLE,
                     num_deps: int = 0) -> FederalTotals:
    """Quick FederalTotals helper."""
    return FederalTotals(
        filing_status=filing_status,
        num_dependents=num_deps,
        adjusted_gross_income=Decimal(agi),
        taxable_income=Decimal(agi),
        total_federal_tax=Decimal("0"),
        federal_income_tax=Decimal("0"),
        federal_standard_deduction=Decimal("15750"),
        federal_itemized_deductions_total=Decimal("0"),
        deduction_taken=Decimal("15750"),
        federal_withholding_from_w2s=Decimal("0"),
    )


class TestIllinoisScheduleNRNonresidentCompute:
    """Real Schedule NR nonresident tax computation tests."""

    def test_nonresident_il_w2_only_taxes_il_wages(self):
        """Nonresident with $50k total wages, all IL-source.

        IL base = $50,000 (all IL wages)
        Ratio = 50000/50000 = 1.0
        Prorated exemption = 1.0 * $2,850 = $2,850
        Taxable = $50,000 - $2,850 = $47,150
        Tax = $47,150 * 0.0495 = $2,333.925 -> $2,333.93
        """
        ret = _nonresident_return_with_il_w2("50000", "50000")
        fed = _federal_for_agi("50000")
        result = PLUGIN.compute(ret, fed, ResidencyStatus.NONRESIDENT, days_in_state=0)
        ss = result.state_specific
        assert ss["il_sourced_wages_from_w2_state_rows"] == Decimal("50000.00")
        assert ss["state_total_tax"] == Decimal("2333.93")

    def test_nonresident_partial_il_wages(self):
        """Nonresident with $80k total wages but only $30k IL-source.

        IL base = $30,000 (IL wages only)
        Ratio = 30000/80000 = 0.375
        Prorated exemption = 0.375 * $2,850 = $1,068.75
        Taxable = $30,000 - $1,068.75 = $28,931.25
        Tax = $28,931.25 * 0.0495 = $1,432.096875 -> $1,432.10
        """
        ret = _nonresident_return_with_il_w2("80000", "30000")
        fed = _federal_for_agi("80000")
        result = PLUGIN.compute(ret, fed, ResidencyStatus.NONRESIDENT, days_in_state=0)
        ss = result.state_specific
        assert ss["il_sourced_wages_from_w2_state_rows"] == Decimal("30000.00")
        # Verify exemption proration
        il_ratio = Decimal("30000") / Decimal("80000")
        expected_exemption = (Decimal("2850") * il_ratio).quantize(_CENTS, rounding=ROUND_HALF_UP)
        assert ss["state_exemption_prorated"] == expected_exemption
        assert ss["state_total_tax"] == Decimal("1432.10")

    def test_nonresident_interest_not_il_source(self):
        """Interest income should NOT be IL-source for nonresidents.

        A nonresident with $50k IL wages + $10k interest should only
        pay IL tax on the $50k wages, not the interest. The interest
        does appear in Column A (total) for exemption proration though.
        """
        ret = _nonresident_return_with_il_w2("50000", "50000")
        ret.forms_1099_int.append(
            Form1099INT(
                payer_name="Big Bank",
                box1_interest_income=Decimal("10000"),
            )
        )
        fed = _federal_for_agi("60000")  # AGI includes $10k interest
        result = PLUGIN.compute(ret, fed, ResidencyStatus.NONRESIDENT, days_in_state=0)
        ss = result.state_specific
        # IL-source base is only $50k (wages), not $60k
        assert ss["il_sourced_wages_from_w2_state_rows"] == Decimal("50000.00")
        # Ratio = 50000/60000 for exemption proration
        il_ratio = Decimal("50000") / Decimal("60000")
        expected_exemption = (Decimal("2850") * il_ratio).quantize(_CENTS, rounding=ROUND_HALF_UP)
        assert ss["state_exemption_prorated"] == expected_exemption
        # Tax on wages minus prorated exemption
        taxable = Decimal("50000") - expected_exemption
        expected_tax = (taxable * Decimal("0.0495")).quantize(_CENTS, rounding=ROUND_HALF_UP)
        assert ss["state_total_tax"] == expected_tax

    def test_nonresident_dividends_not_il_source(self):
        """Dividends should NOT be IL-source for nonresidents."""
        ret = _nonresident_return_with_il_w2("50000", "50000")
        ret.forms_1099_div.append(
            Form1099DIV(
                payer_name="Mutual Fund Co",
                box1a_ordinary_dividends=Decimal("5000"),
            )
        )
        fed = _federal_for_agi("55000")
        result = PLUGIN.compute(ret, fed, ResidencyStatus.NONRESIDENT, days_in_state=0)
        ss = result.state_specific
        # IL-source base is only $50k wages
        il_ratio = Decimal("50000") / Decimal("55000")
        expected_exemption = (Decimal("2850") * il_ratio).quantize(_CENTS, rounding=ROUND_HALF_UP)
        taxable = Decimal("50000") - expected_exemption
        expected_tax = (taxable * Decimal("0.0495")).quantize(_CENTS, rounding=ROUND_HALF_UP)
        assert ss["state_total_tax"] == expected_tax

    def test_nonresident_with_il_rental_property(self):
        """Rental income from IL property IS IL-source.

        Nonresident with $50k IL wages + $12k IL rental income.
        IL base = $50,000 + $12,000 = $62,000
        Total AGI = $62,000
        Ratio = 62000/62000 = 1.0
        Prorated exemption = $2,850
        Taxable = $62,000 - $2,850 = $59,150
        Tax = $59,150 * 0.0495 = $2,927.925 -> $2,927.93
        """
        ret = _nonresident_return_with_il_w2("50000", "50000")
        ret.schedules_e.append(
            ScheduleE(
                properties=[
                    ScheduleEProperty(
                        address=Address(
                            street1="456 Lake Shore Dr",
                            city="Chicago",
                            state="IL",
                            zip="60611",
                        ),
                        rents_received=Decimal("18000"),
                        insurance=Decimal("2000"),
                        taxes=Decimal("3000"),
                        repairs=Decimal("1000"),
                    ),
                ]
            )
        )
        # Rental net = 18000 - 2000 - 3000 - 1000 = 12000
        fed = _federal_for_agi("62000")
        result = PLUGIN.compute(ret, fed, ResidencyStatus.NONRESIDENT, days_in_state=0)
        ss = result.state_specific
        assert ss["il_sourced_rental_net"] == Decimal("12000.00")
        assert ss["il_sourced_wages_from_w2_state_rows"] == Decimal("50000.00")
        assert ss["state_total_tax"] == Decimal("2927.93")

    def test_nonresident_non_il_rental_excluded(self):
        """Rental income from non-IL property is NOT IL-source.

        Nonresident with $50k IL wages + $12k IN rental (not IL).
        IL base = $50k (wages only), rental excluded.
        """
        ret = _nonresident_return_with_il_w2("50000", "50000")
        ret.schedules_e.append(
            ScheduleE(
                properties=[
                    ScheduleEProperty(
                        address=Address(
                            street1="789 Meridian St",
                            city="Indianapolis",
                            state="IN",
                            zip="46204",
                        ),
                        rents_received=Decimal("18000"),
                        insurance=Decimal("2000"),
                        taxes=Decimal("3000"),
                        repairs=Decimal("1000"),
                    ),
                ]
            )
        )
        fed = _federal_for_agi("62000")  # AGI includes rental
        result = PLUGIN.compute(ret, fed, ResidencyStatus.NONRESIDENT, days_in_state=0)
        ss = result.state_specific
        assert ss["il_sourced_rental_net"] == Decimal("0.00")
        # Only IL wages count
        il_ratio = Decimal("50000") / Decimal("62000")
        expected_exemption = (Decimal("2850") * il_ratio).quantize(_CENTS, rounding=ROUND_HALF_UP)
        taxable = Decimal("50000") - expected_exemption
        expected_tax = (taxable * Decimal("0.0495")).quantize(_CENTS, rounding=ROUND_HALF_UP)
        assert ss["state_total_tax"] == expected_tax

    def test_nonresident_mixed_sources_il_w2_non_il_rental_interest(self):
        """Nonresident with IL W-2 + non-IL rental + interest.

        $40k total wages ($40k IL), $10k interest, $15k IN rental.
        Total AGI = $65k.
        IL-source = $40k (wages only; interest and IN rental excluded).
        Ratio = 40000/65000
        Prorated exemption = (40000/65000) * $2,850
        Taxable = $40,000 - prorated exemption
        Tax = taxable * 0.0495
        """
        ret = _nonresident_return_with_il_w2("40000", "40000")
        ret.forms_1099_int.append(
            Form1099INT(
                payer_name="Savings Bank",
                box1_interest_income=Decimal("10000"),
            )
        )
        ret.schedules_e.append(
            ScheduleE(
                properties=[
                    ScheduleEProperty(
                        address=Address(
                            street1="100 Market St",
                            city="Indianapolis",
                            state="IN",
                            zip="46204",
                        ),
                        rents_received=Decimal("15000"),
                    ),
                ]
            )
        )
        fed = _federal_for_agi("65000")
        result = PLUGIN.compute(ret, fed, ResidencyStatus.NONRESIDENT, days_in_state=0)
        ss = result.state_specific
        # IL-source: only wages
        il_base = Decimal("40000")
        il_ratio = il_base / Decimal("65000")
        expected_exemption = (Decimal("2850") * il_ratio).quantize(_CENTS, rounding=ROUND_HALF_UP)
        taxable = il_base - expected_exemption
        expected_tax = (taxable * Decimal("0.0495")).quantize(_CENTS, rounding=ROUND_HALF_UP)
        assert ss["state_total_tax"] == expected_tax
        assert ss["il_sourced_rental_net"] == Decimal("0.00")

    def test_nonresident_il_business_income_sourced(self):
        """Schedule C business in IL is IL-source for nonresidents."""
        ret = _nonresident_return_with_il_w2("50000", "50000")
        ret.schedules_c.append(
            ScheduleC(
                business_name="IL Consulting",
                principal_business_or_profession="Consulting",
                business_location_state="IL",
                line1_gross_receipts=Decimal("25000"),
            )
        )
        fed = _federal_for_agi("75000")
        result = PLUGIN.compute(ret, fed, ResidencyStatus.NONRESIDENT, days_in_state=0)
        ss = result.state_specific
        assert ss["il_sourced_schedule_c_net"] == Decimal("25000.00")
        # IL base = 50k wages + 25k business = 75k
        il_base = Decimal("75000")
        il_ratio = il_base / Decimal("75000")  # = 1.0
        expected_exemption = (Decimal("2850") * il_ratio).quantize(_CENTS, rounding=ROUND_HALF_UP)
        taxable = il_base - expected_exemption
        expected_tax = (taxable * Decimal("0.0495")).quantize(_CENTS, rounding=ROUND_HALF_UP)
        assert ss["state_total_tax"] == expected_tax

    def test_nonresident_exemption_prorated_less_than_full(self):
        """Verify exemption proration when IL ratio < 1.

        MFJ with 2 deps: full exemption = 4 * $2,850 = $11,400
        $100k total income, $40k IL wages.
        Ratio = 0.4
        Prorated exemption = 0.4 * $11,400 = $4,560
        Taxable = $40,000 - $4,560 = $35,440
        Tax = $35,440 * 0.0495 = $1,754.28
        """
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.MFJ,
            taxpayer=Person(
                first_name="Pat",
                last_name="Nonres",
                ssn="666-77-8888",
                date_of_birth=dt.date(1985, 3, 15),
            ),
            spouse=Person(
                first_name="Sam",
                last_name="Nonres",
                ssn="777-88-9999",
                date_of_birth=dt.date(1986, 7, 20),
            ),
            address=Address(
                street1="200 Monument Circle",
                city="Indianapolis",
                state="IN",
                zip="46204",
            ),
            w2s=[
                W2(
                    employer_name="IL Corp",
                    box1_wages=Decimal("100000"),
                    box2_federal_income_tax_withheld=Decimal("0"),
                    state_rows=[
                        W2StateRow(state="IL", state_wages=Decimal("40000")),
                    ],
                ),
            ],
        )
        fed = _federal_for_agi("100000", FilingStatus.MFJ, num_deps=2)
        result = PLUGIN.compute(ret, fed, ResidencyStatus.NONRESIDENT, days_in_state=0)
        ss = result.state_specific
        assert ss["state_exemption_count"] == 4
        assert ss["state_exemption_total"] == Decimal("11400.00")
        # Prorated exemption = 0.4 * 11400 = 4560
        assert ss["state_exemption_prorated"] == Decimal("4560.00")
        # Tax = (40000 - 4560) * 0.0495 = 35440 * 0.0495 = 1754.28
        assert ss["state_total_tax"] == Decimal("1754.28")

    def test_resident_unchanged_by_schedule_nr_logic(self):
        """Resident computation should be unchanged -- exemption is NOT prorated."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="Res",
                last_name="Ident",
                ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
            ),
            address=Address(
                street1="100 N State St", city="Chicago", state="IL", zip="60602"
            ),
            w2s=[
                W2(
                    employer_name="IL Corp",
                    box1_wages=Decimal("65000"),
                    box2_federal_income_tax_withheld=Decimal("0"),
                ),
            ],
        )
        fed = _federal_for_agi("65000")
        result = PLUGIN.compute(ret, fed, ResidencyStatus.RESIDENT, days_in_state=365)
        ss = result.state_specific
        assert ss["state_exemption_total"] == Decimal("2850.00")
        assert ss["state_exemption_prorated"] == Decimal("2850.00")
        assert ss["state_total_tax"] == Decimal("3076.43")
        assert ss["state_total_tax"] == ss["state_total_tax_resident_basis"]

    def test_nonresident_zero_il_income_yields_zero_tax(self):
        """Nonresident with no IL-source income should pay $0."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="Nora",
                last_name="NoIL",
                ssn="888-99-0000",
                date_of_birth=dt.date(1990, 1, 1),
            ),
            address=Address(
                street1="100 Main St", city="Indianapolis", state="IN", zip="46204"
            ),
            w2s=[
                W2(
                    employer_name="Indiana Corp",
                    box1_wages=Decimal("60000"),
                    box2_federal_income_tax_withheld=Decimal("0"),
                    state_rows=[
                        W2StateRow(state="IN", state_wages=Decimal("60000")),
                    ],
                ),
            ],
        )
        fed = _federal_for_agi("60000")
        result = PLUGIN.compute(ret, fed, ResidencyStatus.NONRESIDENT, days_in_state=0)
        ss = result.state_specific
        assert ss["il_sourced_wages_from_w2_state_rows"] == Decimal("0.00")
        assert ss["state_total_tax"] == Decimal("0.00")


class TestIllinoisScheduleNRApportionIncome:
    """Test apportion_income() with real Schedule NR sourcing."""

    def test_nonresident_apportion_wages_from_w2_state_rows(self):
        """Wages sourced from W-2 state rows, not day-prorated."""
        ret = _nonresident_return_with_il_w2("80000", "30000")
        app = PLUGIN.apportion_income(ret, ResidencyStatus.NONRESIDENT, days_in_state=0)
        assert app.state_source_wages == Decimal("30000.00")

    def test_nonresident_apportion_interest_zero(self):
        """Interest is NOT IL-source for nonresidents."""
        ret = _nonresident_return_with_il_w2("50000", "50000")
        ret.forms_1099_int.append(
            Form1099INT(
                payer_name="Big Bank",
                box1_interest_income=Decimal("10000"),
            )
        )
        app = PLUGIN.apportion_income(ret, ResidencyStatus.NONRESIDENT, days_in_state=0)
        assert app.state_source_interest == Decimal("0")

    def test_nonresident_apportion_dividends_zero(self):
        """Dividends are NOT IL-source for nonresidents."""
        ret = _nonresident_return_with_il_w2("50000", "50000")
        ret.forms_1099_div.append(
            Form1099DIV(
                payer_name="Fund Co",
                box1a_ordinary_dividends=Decimal("5000"),
            )
        )
        app = PLUGIN.apportion_income(ret, ResidencyStatus.NONRESIDENT, days_in_state=0)
        assert app.state_source_dividends == Decimal("0")

    def test_nonresident_apportion_capital_gains_zero(self):
        """Capital gains are NOT IL-source for nonresidents (simplified)."""
        ret = _nonresident_return_with_il_w2("50000", "50000")
        app = PLUGIN.apportion_income(ret, ResidencyStatus.NONRESIDENT, days_in_state=0)
        assert app.state_source_capital_gains == Decimal("0")

    def test_nonresident_apportion_rental_by_property_state(self):
        """Rental sourced by property address state."""
        ret = _nonresident_return_with_il_w2("50000", "50000")
        ret.schedules_e.append(
            ScheduleE(
                properties=[
                    ScheduleEProperty(
                        address=Address(
                            street1="123 IL St",
                            city="Chicago",
                            state="IL",
                            zip="60601",
                        ),
                        rents_received=Decimal("20000"),
                        taxes=Decimal("5000"),
                    ),
                    ScheduleEProperty(
                        address=Address(
                            street1="456 IN St",
                            city="Indianapolis",
                            state="IN",
                            zip="46204",
                        ),
                        rents_received=Decimal("15000"),
                        taxes=Decimal("3000"),
                    ),
                ]
            )
        )
        app = PLUGIN.apportion_income(ret, ResidencyStatus.NONRESIDENT, days_in_state=0)
        # Only IL property: 20000 - 5000 = 15000
        assert app.state_source_rental == Decimal("15000.00")

    def test_nonresident_apportion_business_by_location(self):
        """Schedule C business sourced by business_location_state."""
        ret = _nonresident_return_with_il_w2("50000", "50000")
        ret.schedules_c.append(
            ScheduleC(
                business_name="IL Biz",
                principal_business_or_profession="Consulting",
                business_location_state="IL",
                line1_gross_receipts=Decimal("20000"),
            )
        )
        ret.schedules_c.append(
            ScheduleC(
                business_name="IN Biz",
                principal_business_or_profession="Freelance",
                business_location_state="IN",
                line1_gross_receipts=Decimal("10000"),
            )
        )
        app = PLUGIN.apportion_income(ret, ResidencyStatus.NONRESIDENT, days_in_state=0)
        assert app.state_source_self_employment == Decimal("20000.00")

    def test_resident_apportion_all_income_il_source(self):
        """Resident: all income categories are IL-source."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="Res",
                last_name="Ident",
                ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
            ),
            address=Address(
                street1="100 N State St", city="Chicago", state="IL", zip="60602"
            ),
            w2s=[
                W2(
                    employer_name="Corp",
                    box1_wages=Decimal("50000"),
                    box2_federal_income_tax_withheld=Decimal("0"),
                ),
            ],
        )
        ret.forms_1099_int.append(
            Form1099INT(
                payer_name="Bank",
                box1_interest_income=Decimal("2000"),
            )
        )
        app = PLUGIN.apportion_income(ret, ResidencyStatus.RESIDENT, days_in_state=365)
        assert app.state_source_wages == Decimal("50000.00")
        assert app.state_source_interest == Decimal("2000.00")
        assert app.state_source_total == Decimal("52000.00")

    def test_nonresident_apportion_total_is_il_only(self):
        """Nonresident total should reflect only IL-source income."""
        ret = _nonresident_return_with_il_w2("80000", "30000")
        ret.forms_1099_int.append(
            Form1099INT(
                payer_name="Bank",
                box1_interest_income=Decimal("10000"),
            )
        )
        ret.schedules_e.append(
            ScheduleE(
                properties=[
                    ScheduleEProperty(
                        address=Address(
                            street1="100 IL Rd",
                            city="Springfield",
                            state="IL",
                            zip="62701",
                        ),
                        rents_received=Decimal("10000"),
                        taxes=Decimal("2000"),
                    ),
                ]
            )
        )
        app = PLUGIN.apportion_income(ret, ResidencyStatus.NONRESIDENT, days_in_state=0)
        # Wages: 30000, Interest: 0, Rental: 8000
        assert app.state_source_wages == Decimal("30000.00")
        assert app.state_source_interest == Decimal("0")
        assert app.state_source_rental == Decimal("8000.00")
        assert app.state_source_total == Decimal("38000.00")


class TestIllinoisScheduleNRV1Limitations:
    """Verify the v1_limitations text reflects Schedule NR implementation."""

    def test_v1_limitations_mention_schedule_nr_sourcing(self):
        """v1_limitations should describe the Schedule NR sourcing that IS
        implemented, not the old day-proration stub."""
        ret = _nonresident_return_with_il_w2("50000", "50000")
        fed = _federal_for_agi("50000")
        result = PLUGIN.compute(ret, fed, ResidencyStatus.NONRESIDENT, days_in_state=0)
        joined = " ".join(result.state_specific["v1_limitations"]).lower()
        # Should mention the sourcing rules that are now implemented
        assert "w-2 state rows" in joined or "wages sourced" in joined
        assert "rental" in joined or "property situs" in joined
        assert "exemption prorated" in joined or "prorated" in joined
