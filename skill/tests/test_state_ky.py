"""Kentucky state plugin tests — HAND-ROLLED plugin (no tenforty).

IMPORTANT: tenforty/OpenTaxSolver does NOT ship a 2025 KY_740 module — a
live call to ``tenforty.evaluate_return(year=2025, state='KY', ...)`` raises
``ValueError: OTS does not support 2025/KY_740``. These tests also pin down
that behavior with a ``test_tenforty_still_does_not_support_ky_2025`` lock
so if OTS ever adds KY support the plugin can be reevaluated.

Rate / base (TY2025, verified 2026-04-11 via KY DOR):
    - Flat rate: 4% (0.04). Cite: 2025 Form 740 instructions, Line 12
      "Multiply line 11 by four percent (.04)" and KY DOR Individual
      Income Tax landing page
      https://revenue.ky.gov/Individual/Individual-Income-Tax/Pages/default.aspx
    - Standard deduction: $3,270 single-column. Cite: KY DOR announcement
      https://revenue.ky.gov/News/pages/kentucky-dor-announces-2025-standard-deduction.aspx
      and 2025 Form 740 instructions, "What's New".
    - Reciprocity partners: IL, IN, MI, OH, VA, WI, WV (7 states) — the
      LARGEST bilateral network. Cite: 2025 Form 740-NP instructions +
      skill/reference/state-reciprocity.json.

LOCKED wrap-correctness number (Single / $65,000 W-2 / 0 deps / Standard):

    KY AGI  = $65,000 (v1 = federal AGI)
    KY SD   = $3,270
    KY TI   = $61,730
    KY tax  = $61,730 * 0.04 = $2,469.20 (exact, ROUND_HALF_UP)

This is the wrap-correctness lock: a real Form 740 with the KY DOR's
line-by-line instructions produces $2,469.20 for this scenario. Because
tenforty does NOT support KY 2025, the "bit-for-bit against tenforty"
lock the task brief requested is impossible; instead we lock it
bit-for-bit against the KY DOR instructions (Line 12 direct formula).
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
from skill.scripts.states.ky import (
    KY_FAMILY_SIZE_THRESHOLDS_TY2025,
    KY_FLAT_RATE,
    KY_STANDARD_DEDUCTION_TY2025,
    LOCK_VALUE,
    PLUGIN,
    KentuckyPlugin,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def single_65k_return() -> CanonicalReturn:
    """Single filer, $65k W-2 wages, Louisville address, no dependents."""
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
            street1="501 High St",
            city="Frankfort",
            state="KY",
            zip="40601",
        ),
        w2s=[
            W2(
                employer_name="Bluegrass Corp",
                box1_wages=Decimal("65000"),
                box2_federal_income_tax_withheld=Decimal("0"),
            ),
        ],
    )


@pytest.fixture
def federal_single_65k() -> FederalTotals:
    """Matches the shared CP4 Single $65k W-2 federal scenario."""
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
            street1="400 Main St",
            city="Louisville",
            state="KY",
            zip="40202",
        ),
        w2s=[
            W2(
                employer_name="River City LLC",
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


class TestKentuckyPluginMeta:
    def test_plugin_is_state_plugin_protocol(self):
        """runtime_checkable Protocol must recognize our concrete plugin."""
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_kentucky_plugin_instance(self):
        assert isinstance(PLUGIN, KentuckyPlugin)

    def test_meta_code_is_ky(self):
        assert PLUGIN.meta.code == "KY"

    def test_meta_name_is_kentucky(self):
        assert PLUGIN.meta.name == "Kentucky"

    def test_meta_has_income_tax(self):
        assert PLUGIN.meta.has_income_tax is True

    def test_meta_starting_point_is_federal_agi(self):
        """KY Form 740 Line 5 starts from federal AGI."""
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI

    def test_meta_submission_channel_is_state_dor_free_portal(self):
        """KY operates its own free file software tier via KY File."""
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )

    def test_meta_reciprocity_partners_exact_set(self):
        """KY has exactly 7 bilateral reciprocity partners — the largest
        network. Verified against 2025 Form 740-NP instructions and
        skill/reference/state-reciprocity.json."""
        partners = set(PLUGIN.meta.reciprocity_partners)
        assert partners == {"IL", "IN", "MI", "OH", "VA", "WI", "WV"}
        # Exactly seven — lock the count so adds/drops fail CI.
        assert len(PLUGIN.meta.reciprocity_partners) == 7

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_dor_url_is_ky_gov(self):
        assert "revenue.ky.gov" in PLUGIN.meta.dor_url

    def test_meta_free_efile_url_is_ky_gov(self):
        assert PLUGIN.meta.free_efile_url is not None
        assert "revenue.ky.gov" in PLUGIN.meta.free_efile_url

    def test_meta_notes_mention_flat_rate_4_percent(self):
        assert "4%" in PLUGIN.meta.notes

    def test_meta_notes_mention_hand_rolled(self):
        assert "HAND-ROLLED" in PLUGIN.meta.notes

    def test_meta_notes_mention_standard_deduction(self):
        assert "3,270" in PLUGIN.meta.notes

    def test_meta_is_frozen(self):
        """Plugin meta is frozen — no runtime mutation."""
        with pytest.raises(Exception):
            PLUGIN.meta.code = "NY"  # type: ignore[misc]

    def test_flat_rate_constant_is_4_percent(self):
        """TY2025 Form 740 Line 12 locks rate at 0.04."""
        assert KY_FLAT_RATE == Decimal("0.04")

    def test_standard_deduction_constant_is_3270(self):
        """TY2025 Form 740 Step 4 / DOR announcement locks at $3,270."""
        assert KY_STANDARD_DEDUCTION_TY2025 == Decimal("3270")

    def test_family_size_thresholds_have_four_entries(self):
        """Family size capped at 4 per KY Chart A."""
        assert set(KY_FAMILY_SIZE_THRESHOLDS_TY2025.keys()) == {1, 2, 3, 4}

    def test_family_size_thresholds_match_2025_chart_a(self):
        """TY2025 Chart A thresholds from 2025 Form 740 instructions."""
        assert KY_FAMILY_SIZE_THRESHOLDS_TY2025[1] == Decimal("15650")
        assert KY_FAMILY_SIZE_THRESHOLDS_TY2025[2] == Decimal("21150")
        assert KY_FAMILY_SIZE_THRESHOLDS_TY2025[3] == Decimal("26650")
        assert KY_FAMILY_SIZE_THRESHOLDS_TY2025[4] == Decimal("32150")


# ---------------------------------------------------------------------------
# Tenforty gap lock — pin the fact OTS does not support 2025 KY_740
# ---------------------------------------------------------------------------


class TestTenfortyKentuckyGap:
    def test_tenforty_still_does_not_support_ky_2025(self):
        """Lock the fact that OpenTaxSolver does not ship a 2025 KY_740
        module. If this test starts failing, tenforty has gained KY support
        and the plugin should be reevaluated against a bit-for-bit tenforty
        wrap (the NC pattern) rather than the hand-rolled IL pattern.

        As of 2026-04-11 this raises ``ValueError: OTS does not support
        2025/KY_740``.
        """
        import tenforty

        with pytest.raises(ValueError, match=r"OTS does not support 2025/KY_740"):
            tenforty.evaluate_return(
                year=2025,
                state="KY",
                filing_status="Single",
                w2_income=65000,
                standard_or_itemized="Standard",
            )


# ---------------------------------------------------------------------------
# compute() — resident case
# ---------------------------------------------------------------------------


class TestKentuckyPluginComputeResident:
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

    def test_state_code_is_ky(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state == "KY"

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

    def test_resident_single_65k_tax_lock(
        self, single_65k_return, federal_single_65k
    ):
        """WRAP-CORRECTNESS LOCK — the task brief's $65k single-filer KY
        resident reference.

        Since tenforty does NOT support KY 2025, we cannot match tenforty
        bit-for-bit. Instead we lock against the KY DOR Form 740 Line 12
        direct formula:

            Line 9  (KY AGI)         = 65,000 (v1 = federal AGI)
            Line 10 (KY SD)          = 3,270
            Line 11 (KY TI)          = 65,000 - 3,270 = 61,730
            Line 12 (tax)            = 61,730 * 0.04 = 2,469.20

        This locks the plugin's flat-rate + standard-deduction math against
        the DOR's own instructions. If OTS ever ships a 2025 KY_740 module,
        a separate parallel tenforty-comparison test should be added to
        confirm agreement.

        LOCKED KY $65k SINGLE TAX = $2,469.20
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = result.state_specific
        assert ss["state_adjusted_gross_income"] == Decimal("65000.00")
        assert ss["state_standard_deduction"] == Decimal("3270.00")
        assert ss["state_taxable_income"] == Decimal("61730.00")
        # THE LOCK.
        assert ss["state_total_tax"] == LOCK_VALUE
        assert ss["flat_rate"] == Decimal("0.04")

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

    def test_resident_basis_equals_apportioned_for_full_year(
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

    def test_mfj_120k_two_deps_tax(
        self, mfj_120k_two_deps_return, federal_mfj_120k_two_deps
    ):
        """MFJ / $120k AGI / 2 deps.

        v1 uses a single $3,270 SD (Filing Status 3 basis — see v1
        limitations). Taxpayer-unfavorable vs. the two-column Filing Status
        2 return which would give $6,540.

            KY AGI  = 120,000
            KY SD   = 3,270
            KY TI   = 116,730
            KY tax  = 116,730 * 0.04 = 4,669.20
        """
        result = PLUGIN.compute(
            mfj_120k_two_deps_return,
            federal_mfj_120k_two_deps,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = result.state_specific
        assert ss["state_adjusted_gross_income"] == Decimal("120000.00")
        assert ss["state_standard_deduction"] == Decimal("3270.00")
        assert ss["state_taxable_income"] == Decimal("116730.00")
        assert ss["state_total_tax"] == Decimal("4669.20")

    def test_state_total_tax_is_decimal(
        self, single_65k_return, federal_single_65k
    ):
        """Every numeric field must be Decimal, never float — keeps the
        JSON round-trip deterministic."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        numeric_keys = [
            "state_adjusted_gross_income",
            "state_standard_deduction",
            "state_taxable_income",
            "state_total_tax",
            "state_total_tax_resident_basis",
            "flat_rate",
            "apportionment_fraction",
        ]
        for key in numeric_keys:
            assert key in result.state_specific, f"missing {key}"
            assert isinstance(
                result.state_specific[key], Decimal
            ), f"{key} not Decimal"

    def test_pydantic_round_trip(
        self, single_65k_return, federal_single_65k
    ):
        """Round-trip through Pydantic JSON (plus json.dumps) to confirm no
        non-JSON primitives leaked into state_specific."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        dumped = result.model_dump(mode="json")
        rehydrated = StateReturn.model_validate(json.loads(json.dumps(dumped)))
        assert rehydrated.state == "KY"
        assert rehydrated.residency == ResidencyStatus.RESIDENT

    def test_low_income_floors_taxable_at_zero(self, single_65k_return):
        """A taxpayer with AGI below the standard deduction should pay $0,
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

    def test_exactly_at_standard_deduction_yields_zero(
        self, single_65k_return
    ):
        """AGI == standard deduction means taxable = 0."""
        federal = FederalTotals(
            filing_status=FilingStatus.SINGLE,
            num_dependents=0,
            adjusted_gross_income=Decimal("3270"),
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
            federal,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal("0.00")
        assert result.state_specific["state_total_tax"] == Decimal("0.00")


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment
# ---------------------------------------------------------------------------


class TestKentuckyPluginComputeNonresident:
    def test_nonresident_half_year_prorates(
        self, single_65k_return, federal_single_65k
    ):
        """182 / 365 proration of the resident-basis tax. v1 day-based;
        TODO(ky-form-740-np) for real Form 740-NP sourcing."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=182,
        )
        full = result.state_specific["state_total_tax_resident_basis"]
        apportioned = result.state_specific["state_total_tax"]
        assert full == Decimal("2469.20")
        expected = (full * Decimal(182) / Decimal(365)).quantize(Decimal("0.01"))
        assert apportioned == expected
        # Sanity: roughly half of 2,469.20.
        assert Decimal("1200.00") < apportioned < Decimal("1270.00")

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
        """days_in_state=365 with NONRESIDENT still gives the full
        resident-basis tax — the apportionment is day-based, not
        residency-toggle based."""
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


class TestKentuckyPluginApportionIncome:
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
        """Day-fraction applied to every category for nonresident."""
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


class TestKentuckyPluginV1Limitations:
    def test_v1_limitations_present(
        self, single_65k_return, federal_single_65k
    ):
        """The plugin MUST surface a v1_limitations list on state_specific."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        lims = result.state_specific.get("v1_limitations")
        assert isinstance(lims, list)
        assert len(lims) > 0

    def test_v1_limitations_mentions_schedule_m(
        self, single_65k_return, federal_single_65k
    ):
        """Schedule M additions/subtractions is the biggest gap — lock the
        limitations list to mention it explicitly."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        joined = " ".join(result.state_specific["v1_limitations"]).lower()
        assert "schedule m" in joined
        assert "additions" in joined and "subtractions" in joined

    def test_v1_limitations_mentions_obbba_conformity(
        self, single_65k_return, federal_single_65k
    ):
        """OBBBA 2025 federal changes (tips/overtime/car loan interest) are
        NOT deductible on KY per the 2025 Form 740 instructions Federal Tax
        Law Changes section. Lock this note so it can't silently vanish."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        joined = " ".join(result.state_specific["v1_limitations"]).lower()
        assert "obbba" in joined or "12/31/2024" in joined

    def test_v1_limitations_mentions_mfj_column_split(
        self, single_65k_return, federal_single_65k
    ):
        """Filing Status 2 combined-return column split is unique to KY
        and a loud v1 gap — taxpayer-unfavorable."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        joined = " ".join(result.state_specific["v1_limitations"]).lower()
        assert "column" in joined and "mfj" in joined.lower()

    def test_v1_limitations_mentions_family_size_tax_credit(
        self, single_65k_return, federal_single_65k
    ):
        """The Family Size Tax Credit is a KY-specific low-income zero-out
        — lock that the gap is documented."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        joined = " ".join(result.state_specific["v1_limitations"]).lower()
        assert "family size tax credit" in joined

    def test_v1_limitations_mentions_740_np(
        self, single_65k_return, federal_single_65k
    ):
        """Day-based nonresident proration is a stopgap — Form 740-NP is
        the real answer. Lock the note."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        joined = " ".join(result.state_specific["v1_limitations"]).lower()
        assert "740-np" in joined


# ---------------------------------------------------------------------------
# Reciprocity — symmetry + cross-check vs. reference JSON
# ---------------------------------------------------------------------------


_RECIPROCITY_PATH = (
    Path(__file__).resolve().parent.parent
    / "reference"
    / "state-reciprocity.json"
)


class TestKentuckyReciprocity:
    def test_reciprocity_partners_match_json(self):
        """PLUGIN.meta.reciprocity_partners must match the set of KY pairs
        in skill/reference/state-reciprocity.json. If reciprocity.json
        changes, this test fails and the plugin tuple must be updated."""
        raw = json.loads(_RECIPROCITY_PATH.read_text())
        json_partners: set[str] = set()
        for entry in raw["agreements"]:
            states = entry["states"]
            if "KY" in states:
                other = next(s for s in states if s != "KY")
                json_partners.add(other)
        assert set(PLUGIN.meta.reciprocity_partners) == json_partners
        # Sanity: seven partners — the largest bilateral network.
        assert json_partners == {"IL", "IN", "MI", "OH", "VA", "WI", "WV"}

    def test_reciprocity_symmetry_ky_to_il(self):
        """RECIPROCITY SYMMETRY LOCK — if KY->IL is in KY's partners, then
        IL->KY must symmetrize via the shared ReciprocityTable. Agreements
        are bilateral by definition, but a bug that lists KY->IL without
        IL->KY in the JSON (or vice versa) would slip past single-sided
        tests."""
        table = ReciprocityTable.load()
        assert table.are_reciprocal("KY", "IL")
        assert table.are_reciprocal("IL", "KY")
        # KY must appear in IL's partner set per the shared table.
        assert "KY" in table.partners_of("IL")
        # IL must appear in KY's partner set per the shared table.
        assert "IL" in table.partners_of("KY")
        # And the plugin's own tuple must agree with the shared table.
        assert "IL" in PLUGIN.meta.reciprocity_partners

    def test_reciprocity_symmetry_every_partner(self):
        """Full symmetry sweep — for every partner P in KY's tuple, the
        shared ReciprocityTable must also recognize P->KY. This is the
        full reciprocity symmetry test the task brief asked for."""
        table = ReciprocityTable.load()
        for partner in PLUGIN.meta.reciprocity_partners:
            assert table.are_reciprocal(
                "KY", partner
            ), f"ReciprocityTable missing KY-{partner}"
            assert table.are_reciprocal(
                partner, "KY"
            ), f"ReciprocityTable missing {partner}-KY (not symmetric)"
            assert "KY" in table.partners_of(partner), (
                f"partners_of({partner}) should contain KY"
            )

    def test_ky_is_not_self_reciprocal(self):
        """Defensive: a state is not reciprocal with itself even though
        every state 'taxes its own residents only'."""
        table = ReciprocityTable.load()
        assert table.are_reciprocal("KY", "KY") is False

    def test_ky_has_income_tax_in_reciprocity_table(self):
        """KY is not a no-income-tax state."""
        table = ReciprocityTable.load()
        assert table.has_income_tax("KY") is True


# ---------------------------------------------------------------------------
# render_pdfs() / form_ids()
# ---------------------------------------------------------------------------


class TestKentuckyPluginFormIds:
    def test_form_ids_returns_ky_form_740(self):
        assert PLUGIN.form_ids() == ["KY Form 740"]

    def test_render_pdfs_returns_empty_list(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Fan-out follow-up — KY Form 740 PDF fill not yet implemented."""
        state_return = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert PLUGIN.render_pdfs(state_return, tmp_path) == []

    def test_render_pdfs_accepts_arbitrary_path(
        self, single_65k_return, federal_single_65k
    ):
        """Even a nonexistent path should not raise on a no-op render."""
        state_return = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert PLUGIN.render_pdfs(state_return, Path("/tmp/nonexistent")) == []
