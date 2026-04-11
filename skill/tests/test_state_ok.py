"""Oklahoma state plugin tests — TY2025.

Mirrors the wave-4 ``test_state_ks.py`` HAND-ROLLED pattern. OK is NOT
supported by tenforty's default OTS backend (raises
``ValueError: OTS does not support 2025/OK_511``), AND the graph
backend has a real correctness gap at TY2025: it overstates OK tax by
exactly $47.50 = $1,000 (personal exemption) * 4.75% (top rate). The
plugin therefore hand-rolls the OK Form 511 graduated-bracket calc
from the 2025 OK Tax Commission Form 511 packet and the OW-2 2025
withholding tables.

Hand verification ($65k Single, TY2025):
    AGI                = $65,000
    - Std ded (Single) = $6,350    (OK Form 511 instructions)
    - Personal ex      = $1,000    ($1,000 per filer)
    OK taxable income  = $57,650
    Tax (Single brackets):
        $0 - $1,000        @ 0.25%  = 2.50
        $1,000 - $2,500    @ 0.75%  = 11.25
        $2,500 - $3,750    @ 1.75%  = 21.875
        $3,750 - $4,900    @ 2.75%  = 31.625
        $4,900 - $7,200    @ 3.75%  = 86.25
        $7,200 - $57,650   @ 4.75%  = 50,450 * 0.0475 = 2,396.375
        Total                       = 2,549.875
        Quantized to cents          = $2,549.88

The graph backend returns $2,597.38 — exactly $47.50 high (the
personal exemption it omits times the top marginal rate). The
``TestTenfortyStillHasGapOnOK`` gatekeeper test pins the graph
backend's wrong number so when OK lands an upstream fix, CI fails and
we re-evaluate the wrap-vs-hand-roll decision.

LOUDLY FLAGGED RECENT LAW: OK HB 2764 (2025) reduces the top marginal
rate to 4.50% and consolidates 6 brackets into 3, effective TY2026.
This plugin is TY2025 only — TY2026 needs a separate update.

Reciprocity: OK has NO bilateral reciprocity agreements (verified
against skill/reference/state-reciprocity.json).
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
from skill.scripts.states.ok import (
    LOCK_VALUE,
    OK_TY2025_BRACKETS_MFJ,
    OK_TY2025_BRACKETS_SINGLE,
    OK_TY2025_PERSONAL_EXEMPTION,
    OK_TY2025_STD_DED_HOH,
    OK_TY2025_STD_DED_MFJ,
    OK_TY2025_STD_DED_MFS,
    OK_TY2025_STD_DED_SINGLE,
    OK_TY2025_TOP_RATE,
    OK_V1_LIMITATIONS,
    OklahomaPlugin,
    PLUGIN,
    ok_exemption_allowance,
    ok_standard_deduction,
    ok_tax_from_brackets,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def single_65k_return() -> CanonicalReturn:
    """Single $65k W-2 OK resident — the spec wrap lock scenario."""
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=Person(
            first_name="Sooner",
            last_name="Williams",
            ssn="111-22-3333",
            date_of_birth=dt.date(1990, 1, 1),
        ),
        address=Address(
            street1="2501 N Lincoln Blvd",
            city="Oklahoma City",
            state="OK",
            zip="73105",
        ),
        w2s=[
            W2(
                employer_name="Red Earth Energy",
                box1_wages=Decimal("65000"),
                box2_federal_income_tax_withheld=Decimal("0"),
            ),
        ],
    )


@pytest.fixture
def federal_single_65k() -> FederalTotals:
    """$65k AGI Single / OBBBA std ded $15,750."""
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
    """MFJ $120k OK resident couple from Tulsa."""
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.MFJ,
        taxpayer=Person(
            first_name="Will",
            last_name="Rogers",
            ssn="111-22-3333",
            date_of_birth=dt.date(1985, 3, 15),
        ),
        spouse=Person(
            first_name="Betty",
            last_name="Rogers",
            ssn="222-33-4444",
            date_of_birth=dt.date(1986, 7, 4),
        ),
        address=Address(
            street1="200 S Lewis Ave",
            city="Tulsa",
            state="OK",
            zip="74104",
        ),
        w2s=[
            W2(
                employer_name="Cherokee Holdings",
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


@pytest.fixture
def hoh_50k_2deps_return() -> CanonicalReturn:
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.HOH,
        taxpayer=Person(
            first_name="Jamie",
            last_name="Sooner",
            ssn="333-44-5555",
            date_of_birth=dt.date(1980, 6, 1),
        ),
        address=Address(
            street1="123 N Main St",
            city="Norman",
            state="OK",
            zip="73069",
        ),
        w2s=[
            W2(
                employer_name="Boomer Co",
                box1_wages=Decimal("50000"),
                box2_federal_income_tax_withheld=Decimal("0"),
            ),
        ],
    )


@pytest.fixture
def federal_hoh_50k_2deps() -> FederalTotals:
    return FederalTotals(
        filing_status=FilingStatus.HOH,
        num_dependents=2,
        adjusted_gross_income=Decimal("50000"),
        taxable_income=Decimal("26375"),
        total_federal_tax=Decimal("0"),
        federal_income_tax=Decimal("0"),
        federal_standard_deduction=Decimal("23625"),
        federal_itemized_deductions_total=Decimal("0"),
        deduction_taken=Decimal("23625"),
    )


# ---------------------------------------------------------------------------
# Meta + Protocol conformance
# ---------------------------------------------------------------------------


class TestOklahomaPluginMeta:
    def test_meta_fields(self):
        assert PLUGIN.meta.code == "OK"
        assert PLUGIN.meta.name == "Oklahoma"
        assert PLUGIN.meta.has_income_tax is True
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )

    def test_plugin_is_state_plugin_protocol(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_oklahoma_plugin_instance(self):
        assert isinstance(PLUGIN, OklahomaPlugin)

    def test_meta_dor_url_is_oklahoma_gov(self):
        assert "oklahoma.gov" in PLUGIN.meta.dor_url

    def test_meta_free_efile_url_is_oktap(self):
        assert PLUGIN.meta.free_efile_url is not None
        assert "oktap.tax.ok.gov" in PLUGIN.meta.free_efile_url

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_hand_rolled(self):
        notes = PLUGIN.meta.notes.lower()
        assert "hand-rolled" in notes or "hand rolled" in notes

    def test_meta_notes_mention_graph_backend_bug(self):
        """OK plugin notes must call out the graph-backend bug — load
        bearing for the hand-roll decision."""
        notes = PLUGIN.meta.notes
        assert "graph" in notes.lower() or "GRAPH" in notes
        assert "$47.50" in notes or "47.50" in notes or "exemption" in notes.lower()

    def test_meta_notes_mention_hb2764_ty2026(self):
        """OK notes must call out HB 2764 — load-bearing forward note."""
        assert "HB 2764" in PLUGIN.meta.notes
        assert "2026" in PLUGIN.meta.notes

    def test_meta_notes_mention_top_rate_4_75(self):
        notes = PLUGIN.meta.notes
        assert "4.75" in notes

    def test_meta_notes_mention_no_reciprocity(self):
        assert "reciprocity" in PLUGIN.meta.notes.lower()

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "AR"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Reciprocity invariants
# ---------------------------------------------------------------------------


class TestOklahomaNoReciprocity:
    def test_no_reciprocity_partners(self):
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_no_reciprocity_via_reciprocity_table(self):
        table = ReciprocityTable.load()
        assert table.partners_of("OK") == frozenset()

    def test_not_reciprocal_with_neighbors(self):
        table = ReciprocityTable.load()
        for neighbor in ("TX", "AR", "KS", "MO", "NM", "CO"):
            assert table.are_reciprocal("OK", neighbor) is False


# ---------------------------------------------------------------------------
# TY2025 constants — pin the law
# ---------------------------------------------------------------------------


class TestOklahomaTY2025Constants:
    def test_top_rate_is_4_75_percent(self):
        assert OK_TY2025_TOP_RATE == Decimal("0.0475")

    def test_personal_exemption_is_1000(self):
        assert OK_TY2025_PERSONAL_EXEMPTION == Decimal("1000")

    def test_std_ded_single_is_6350(self):
        assert OK_TY2025_STD_DED_SINGLE == Decimal("6350")

    def test_std_ded_mfj_is_12700(self):
        assert OK_TY2025_STD_DED_MFJ == Decimal("12700")

    def test_std_ded_hoh_is_9350(self):
        assert OK_TY2025_STD_DED_HOH == Decimal("9350")

    def test_std_ded_mfs_is_6350(self):
        assert OK_TY2025_STD_DED_MFS == Decimal("6350")

    def test_single_brackets_count(self):
        assert len(OK_TY2025_BRACKETS_SINGLE) == 6

    def test_single_brackets_top_is_open(self):
        """Top bracket has high=None (open-ended)."""
        assert OK_TY2025_BRACKETS_SINGLE[-1].high is None
        assert OK_TY2025_BRACKETS_SINGLE[-1].rate == Decimal("0.0475")
        assert OK_TY2025_BRACKETS_SINGLE[-1].low == Decimal("7200")

    def test_mfj_brackets_count(self):
        assert len(OK_TY2025_BRACKETS_MFJ) == 6

    def test_mfj_brackets_top_is_open(self):
        assert OK_TY2025_BRACKETS_MFJ[-1].high is None
        assert OK_TY2025_BRACKETS_MFJ[-1].rate == Decimal("0.0475")
        assert OK_TY2025_BRACKETS_MFJ[-1].low == Decimal("14400")

    def test_mfj_brackets_are_2x_single(self):
        """MFJ brackets must be exactly 2x the Single bracket widths."""
        for s_bracket, m_bracket in zip(
            OK_TY2025_BRACKETS_SINGLE, OK_TY2025_BRACKETS_MFJ
        ):
            assert m_bracket.low == s_bracket.low * 2
            if s_bracket.high is not None:
                assert m_bracket.high == s_bracket.high * 2
            else:
                assert m_bracket.high is None
            assert m_bracket.rate == s_bracket.rate


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestOklahomaStandardDeduction:
    def test_single(self):
        assert ok_standard_deduction(FilingStatus.SINGLE) == Decimal("6350")

    def test_mfj(self):
        assert ok_standard_deduction(FilingStatus.MFJ) == Decimal("12700")

    def test_hoh(self):
        assert ok_standard_deduction(FilingStatus.HOH) == Decimal("9350")

    def test_mfs(self):
        assert ok_standard_deduction(FilingStatus.MFS) == Decimal("6350")

    def test_qss_mirrors_mfj(self):
        assert ok_standard_deduction(FilingStatus.QSS) == Decimal("12700")


class TestOklahomaExemptionAllowance:
    def test_single_no_dependents(self):
        assert ok_exemption_allowance(FilingStatus.SINGLE, 0) == Decimal("1000")

    def test_mfj_no_dependents(self):
        """MFJ gets 2 base exemptions = $2,000."""
        assert ok_exemption_allowance(FilingStatus.MFJ, 0) == Decimal("2000")

    def test_hoh_no_dependents(self):
        assert ok_exemption_allowance(FilingStatus.HOH, 0) == Decimal("1000")

    def test_mfs_no_dependents(self):
        assert ok_exemption_allowance(FilingStatus.MFS, 0) == Decimal("1000")

    def test_qss_no_dependents(self):
        assert ok_exemption_allowance(FilingStatus.QSS, 0) == Decimal("2000")

    def test_single_2_dependents(self):
        """Single + 2 deps = 1 + 2 = 3 exemptions = $3,000."""
        assert ok_exemption_allowance(FilingStatus.SINGLE, 2) == Decimal("3000")

    def test_mfj_3_dependents(self):
        """MFJ + 3 deps = 2 + 3 = 5 exemptions = $5,000."""
        assert ok_exemption_allowance(FilingStatus.MFJ, 3) == Decimal("5000")

    def test_negative_dependents_clamped(self):
        assert ok_exemption_allowance(FilingStatus.SINGLE, -3) == Decimal("1000")


class TestOklahomaBracketTax:
    def test_zero_income(self):
        assert ok_tax_from_brackets(Decimal("0"), FilingStatus.SINGLE) == Decimal("0.00")

    def test_negative_income_returns_zero(self):
        assert ok_tax_from_brackets(Decimal("-100"), FilingStatus.SINGLE) == Decimal(
            "0.00"
        )

    def test_single_500_in_first_bracket(self):
        """500 * 0.0025 = 1.25"""
        assert ok_tax_from_brackets(
            Decimal("500"), FilingStatus.SINGLE
        ) == Decimal("1.25")

    def test_single_1000_at_first_break(self):
        """1000 * 0.0025 = 2.50"""
        assert ok_tax_from_brackets(
            Decimal("1000"), FilingStatus.SINGLE
        ) == Decimal("2.50")

    def test_single_at_top_break(self):
        """At $7,200: top of 5th bracket
        = 1*0.0025 + 1.5*0.0075 + 1.25*0.0175 + 1.15*0.0275 + 2.3*0.0375
        (in thousands)
        = 2.50 + 11.25 + 21.875 + 31.625 + 86.25 = 153.50
        """
        assert ok_tax_from_brackets(
            Decimal("7200"), FilingStatus.SINGLE
        ) == Decimal("153.50")

    def test_single_57650_lock(self):
        """The $65k Single OK taxable income point — bit-for-bit lock.

        TI = $57,650 (= $65,000 - $6,350 std - $1,000 ex)
        Tax = 153.50 + (57,650 - 7,200) * 0.0475
            = 153.50 + 50,450 * 0.0475
            = 153.50 + 2,396.375
            = 2,549.875 -> $2,549.88
        """
        assert ok_tax_from_brackets(
            Decimal("57650"), FilingStatus.SINGLE
        ) == Decimal("2549.88")

    def test_mfj_at_top_break(self):
        """MFJ top of 5th bracket = $307.00 (the OW-2 page 9 constant
        printed for MFJ at the start of the top 4.75% bracket).
        MFJ widths are exactly 2x Single widths so the cumulative tax
        is also exactly 2x = 153.50 * 2 = 307.00 at the top break."""
        assert ok_tax_from_brackets(
            Decimal("14400"), FilingStatus.MFJ
        ) == Decimal("307.00")

    def test_mfj_at_15000_above_top_break(self):
        """At MFJ TI = $15,000 we're $600 into the 4.75% top bracket.
        Tax = 307.00 + 600 * 0.0475 = 307.00 + 28.50 = $335.50"""
        assert ok_tax_from_brackets(
            Decimal("15000"), FilingStatus.MFJ
        ) == Decimal("335.50")

    def test_hoh_uses_single_brackets(self):
        """HOH uses Single bracket widths per Form 511 instructions."""
        # At $7,200 HOH should equal $7,200 Single = $153.50.
        single_tax = ok_tax_from_brackets(Decimal("7200"), FilingStatus.SINGLE)
        hoh_tax = ok_tax_from_brackets(Decimal("7200"), FilingStatus.HOH)
        assert single_tax == hoh_tax == Decimal("153.50")

    def test_qss_uses_mfj_brackets(self):
        qss_at = ok_tax_from_brackets(Decimal("14400"), FilingStatus.QSS)
        mfj_at = ok_tax_from_brackets(Decimal("14400"), FilingStatus.MFJ)
        assert qss_at == mfj_at == Decimal("307.00")


# ---------------------------------------------------------------------------
# compute() — resident scenarios
# ---------------------------------------------------------------------------


class TestOklahomaPluginComputeResident:
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
        assert result.state == "OK"
        assert result.residency == ResidencyStatus.RESIDENT
        assert result.days_in_state == 365

    def test_resident_single_65k_tax_lock(
        self, single_65k_return, federal_single_65k
    ):
        """**$65k SINGLE LOCK** — hand-rolled TY2025.

        OK state_total_tax = $2,549.88 (NOT the graph backend's
        $2,597.38, which is wrong — see plugin docstring).

        Hand trace:
            AGI               = $65,000
            - Std ded Single  = $6,350
            - Personal ex     = $1,000
            OK taxable income = $57,650
            Tax = 153.50 + 50,450 * 0.0475 = $2,549.875 -> $2,549.88
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_total_tax"] == LOCK_VALUE
        assert (
            result.state_specific["state_total_tax_resident_basis"] == LOCK_VALUE
        )

    def test_resident_single_65k_line_breakdown(
        self, single_65k_return, federal_single_65k
    ):
        """Every Form 511 line is surfaced on state_specific."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = result.state_specific
        assert ss["state_federal_agi"] == Decimal("65000.00")
        assert ss["state_adjusted_gross_income"] == Decimal("65000.00")
        assert ss["state_standard_deduction"] == Decimal("6350.00")
        assert ss["state_exemption_allowance"] == Decimal("1000.00")
        assert ss["state_total_deductions"] == Decimal("7350.00")
        assert ss["state_taxable_income"] == Decimal("57650.00")

    def test_resident_mfj_120k(self, mfj_120k_return, federal_mfj_120k):
        """MFJ $120k, 0 deps.

        AGI=120000
        - Std ded MFJ = 12700
        - Exemption (MFJ, 0 deps) = 2000
        OK TI = 120000 - 12700 - 2000 = 105300
        TI > 14400, top bracket: 307.00 + (105300 - 14400) * 0.0475
            = 307.00 + 90900 * 0.0475
            = 307.00 + 4,317.75
            = $4,624.75
        """
        result = PLUGIN.compute(
            mfj_120k_return,
            federal_mfj_120k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal(
            "105300.00"
        )
        assert result.state_specific["state_total_tax"] == Decimal("4624.75")

    def test_resident_hoh_2deps(
        self, hoh_50k_2deps_return, federal_hoh_50k_2deps
    ):
        """HOH $50k, 2 deps.

        AGI=50000
        - Std ded HOH = 9350
        - Exemption (HOH + 2 deps) = 1000 + 2*1000 = 3000
        OK TI = 50000 - 9350 - 3000 = 37650
        TI > 7200 (HOH uses Single brackets), top bracket:
            153.50 + (37650 - 7200) * 0.0475
            = 153.50 + 30450 * 0.0475
            = 153.50 + 1,446.375
            = $1,599.875 -> $1,599.88
        """
        result = PLUGIN.compute(
            hoh_50k_2deps_return,
            federal_hoh_50k_2deps,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = result.state_specific
        assert ss["state_exemption_allowance"] == Decimal("3000.00")
        assert ss["state_taxable_income"] == Decimal("37650.00")
        assert ss["state_total_tax"] == Decimal("1599.88")

    def test_state_specific_decimal_fields(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        decimal_keys = [
            "state_federal_agi",
            "state_adjusted_gross_income",
            "state_standard_deduction",
            "state_exemption_allowance",
            "state_total_deductions",
            "state_taxable_income",
            "state_total_tax",
            "state_total_tax_resident_basis",
            "state_top_rate",
            "apportionment_fraction",
            "ok_modifications_applied",
            "ok_personal_exemption_per_filer",
        ]
        for key in decimal_keys:
            assert key in result.state_specific, f"missing {key}"
            assert isinstance(
                result.state_specific[key], Decimal
            ), f"{key} is not Decimal"

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

    def test_hb2764_note_present(
        self, single_65k_return, federal_single_65k
    ):
        """The TY2026 forward note must be on every result so a human
        reading the output knows the law is changing."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        note = result.state_specific["ok_hb2764_ty2026_note"]
        assert "HB 2764" in note
        assert "2026" in note

    def test_zero_income_yields_zero_tax(self):
        zero_return = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="Zero",
                last_name="Income",
                ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
            ),
            address=Address(
                street1="1 St", city="OKC", state="OK", zip="73105"
            ),
        )
        zero_fed = FederalTotals(
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
            zero_return, zero_fed, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert result.state_specific["state_total_tax"] == Decimal("0.00")
        assert result.state_specific["state_taxable_income"] == Decimal("0.00")

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
        assert rehydrated.state == "OK"


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year
# ---------------------------------------------------------------------------


class TestOklahomaPluginComputeNonresident:
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
        assert full == Decimal("2549.88")
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
        assert result.state_specific["apportionment_fraction"] == (
            Decimal(91) / Decimal(365)
        )

    def test_zero_days_yields_zero_apportioned_tax(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=0,
        )
        assert result.state_specific["state_total_tax"] == Decimal("0.00")
        # Resident-basis tax unchanged.
        assert (
            result.state_specific["state_total_tax_resident_basis"]
            == Decimal("2549.88")
        )


# ---------------------------------------------------------------------------
# apportion_income()
# ---------------------------------------------------------------------------


class TestOklahomaPluginApportionIncome:
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


class TestOklahomaPluginFormIds:
    def test_form_ids(self):
        assert "OK Form 511" in PLUGIN.form_ids()

    def test_render_pdfs_returns_empty_list(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
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


# ---------------------------------------------------------------------------
# v1 limitations visibility
# ---------------------------------------------------------------------------


class TestOklahomaV1Limitations:
    def test_limitations_is_tuple(self):
        assert isinstance(OK_V1_LIMITATIONS, tuple)
        assert len(OK_V1_LIMITATIONS) > 0

    def test_limitations_mention_form_511_nr(self):
        joined = " ".join(OK_V1_LIMITATIONS)
        assert "511-NR" in joined

    def test_limitations_mention_credit_for_other_states(self):
        joined = " ".join(OK_V1_LIMITATIONS).lower()
        assert "other state" in joined or "511cr" in joined

    def test_limitations_mention_eitc(self):
        joined = " ".join(OK_V1_LIMITATIONS)
        assert "EIC" in joined or "Earned Income" in joined


# ---------------------------------------------------------------------------
# Reciprocity table consistency
# ---------------------------------------------------------------------------


def test_reciprocity_matches_json():
    table = ReciprocityTable.load()
    ok_partners = table.partners_of("OK")
    assert ok_partners == frozenset()
    assert frozenset(PLUGIN.meta.reciprocity_partners) == ok_partners


# ---------------------------------------------------------------------------
# GATEKEEPER TEST — tenforty support seam
# ---------------------------------------------------------------------------


class TestTenfortyStillHasGapOnOK:
    """Hand-rolled gatekeeper for OK.

    Pins TWO things:

    1. The default OTS backend still raises ``ValueError: OTS does not
       support 2025/OK_511``. When this starts failing, OTS has gained
       OK support and we can re-evaluate the wrap path.

    2. The graph backend still returns the wrong $2,597.38 number for
       $65k Single (it's $47.50 high — the missing $1,000 personal
       exemption * 4.75% top rate). When this number changes, the
       upstream graph definition has been updated and we should
       re-evaluate the hand-roll decision.

    Either failure is a flag to **manually re-verify** the OK plugin
    decision; do NOT auto-flip from hand-roll to wrap on signal alone.
    """

    def test_default_backend_raises_value_error(self):
        with pytest.raises(ValueError, match="OTS does not support"):
            tenforty.evaluate_return(
                year=2025,
                state="OK",
                filing_status="Single",
                w2_income=65000,
                standard_or_itemized="Standard",
            )

    def test_graph_backend_still_omits_personal_exemption(self):
        """Graph backend overstates OK tax by exactly $47.50 = $1,000
        personal exemption * 4.75% top rate. Pin the wrong number so
        an upstream fix flags this test for human attention."""
        result = tenforty.evaluate_return(
            year=2025,
            state="OK",
            filing_status="Single",
            w2_income=65000,
            standard_or_itemized="Standard",
            backend="graph",
        )
        graph_tax = Decimal(str(result.state_total_tax)).quantize(
            Decimal("0.01")
        )
        assert graph_tax == Decimal("2597.38")

        # Hand-rolled plugin produces the correct $2,549.88.
        # The delta is exactly the missing exemption credit.
        expected_correct = Decimal("2549.88")
        delta = graph_tax - expected_correct
        assert delta == Decimal("47.50")

    def test_graph_backend_omits_exemption_in_taxable_income(self):
        """Graph backend's state_taxable_income = 58650 = 65000 - 6350
        (std ded only, missing the $1,000 personal exemption that
        would bring it to $57,650). Pin the wrong value."""
        result = tenforty.evaluate_return(
            year=2025,
            state="OK",
            filing_status="Single",
            w2_income=65000,
            standard_or_itemized="Standard",
            backend="graph",
        )
        graph_ti = Decimal(str(result.state_taxable_income)).quantize(
            Decimal("0.01")
        )
        assert graph_ti == Decimal("58650.00")

    def test_hand_rolled_disagrees_with_graph_by_47_50(
        self, single_65k_return, federal_single_65k
    ):
        """The plugin's hand-rolled value must be the CORRECT $2,549.88,
        $47.50 less than the buggy graph backend."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        plugin_tax = result.state_specific["state_total_tax"]
        graph_result = tenforty.evaluate_return(
            year=2025,
            state="OK",
            filing_status="Single",
            w2_income=65000,
            standard_or_itemized="Standard",
            backend="graph",
        )
        graph_tax = Decimal(str(graph_result.state_total_tax)).quantize(
            Decimal("0.01")
        )
        delta = graph_tax - plugin_tax
        assert plugin_tax == Decimal("2549.88")
        assert delta == Decimal("47.50")
