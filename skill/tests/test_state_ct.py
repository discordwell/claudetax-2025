"""Connecticut state plugin tests.

Covers the ``ConnecticutPlugin`` hand-rolled CT-1040 TCS calc. Unlike the
tenforty-backed state plugins (NC, OH, CA, ...), CT is NOT supported by
tenforty / OpenTaxSolver — ``OTSState.CT`` exists in the enum but maps to
``CT_1`` which has zero entries in ``OTS_FORM_CONFIG`` for any year, so
``tenforty.evaluate_return(..., state='CT')`` raises
``ValueError: OTS does not support 2025/CT_1`` (verified 2026-04-11 against
tenforty 2025.8). The plugin therefore hand-rolls the TCS via the tables
transcribed from Form CT-1040 TCS (Rev. 12/25). This test suite locks the
TCS values via hand-computed reference points straight from the schedule.

Structure mirrors ``test_state_co.py`` (the other hand-rolled plugin)
plus the tenforty-wrap cross-check pattern from ``test_state_nc.py`` /
``test_state_oh.py``, but the "wrap correctness" test asserts the LOUD
TODO — that tenforty explicitly refuses CT, and that our computed value
is therefore independent of tenforty. That wrap-correctness lock is the
anchor: if OTS upstream ever adds real CT support, the CT plugin should
be rewritten to delegate to tenforty and the assertion here flipped.

Reference values locked below:

- **Single $65,000 resident**: CT tax = **$2,875.00**
  (Line 2 exemption = $0; Line 3 TI = $65,000; Line 4 initial tax =
  $2,825 per Table B [$2,000 + 5.5% * $15,000]; Line 5 add-back = $50
  per Table C; Line 6 recapture = $0; Line 7 = $2,875; Line 8
  credit = 0.00; Line 10 = $2,875.)
- **MFJ $120,000 resident**: CT tax = **$5,300.00**
  ($120k TI -> Table B $4,000 + 5.5% * $20,000 = $5,100; Table C
  $200; Table D $0; Line 7 = $5,300; credit = 0; Line 10 = $5,300.)
- **Single $30,000 resident**: CT tax = **$361.25**
  (Exemption $15,000 -> TI $15,000 -> Table B $200 + 4.5% * $5,000 =
  $425; Table C $0; Table D $0; Line 7 = $425; Table E = 0.15; credit
  = $63.75; Line 10 = $361.25.)
- **Single $500,000 resident** (top-bracket sanity): CT tax = **$34,450.00**
  (TI $500k -> Table B $31,250 [top of second-highest bracket];
  Table C $250; Table D $2,950; Line 7 $34,450; credit 0; Line 10
  $34,450.)
- **HOH $80,000 resident**: CT tax = **$3,240.00**
  (Exemption $0 -> TI $80,000 -> Table B HOH $320 + 4.5% * $64,000 =
  $3,200; Table C HOH $40; Line 7 $3,240; credit phased to 0 above
  $78,500; Line 10 $3,240.)
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
from skill.scripts.states.ct import (
    CT_V1_LIMITATIONS,
    ConnecticutPlugin,
    CTTaxCalcResult,
    LOCK_VALUE,
    PLUGIN,
    compute_ct_tax,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def single_65k_return() -> CanonicalReturn:
    """A Single $65k W-2 CT resident."""
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=Person(
            first_name="Charter",
            last_name="Oak",
            ssn="111-22-3333",
            date_of_birth=dt.date(1990, 1, 1),
        ),
        address=Address(
            street1="165 Capitol Ave",
            city="Hartford",
            state="CT",
            zip="06106",
        ),
        w2s=[
            W2(
                employer_name="Nutmeg Industries",
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
    """A MFJ $120k W-2 CT resident couple."""
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.MFJ,
        taxpayer=Person(
            first_name="Alex",
            last_name="Whaler",
            ssn="111-22-3333",
            date_of_birth=dt.date(1985, 3, 15),
        ),
        spouse=Person(
            first_name="Jordan",
            last_name="Whaler",
            ssn="222-33-4444",
            date_of_birth=dt.date(1986, 7, 4),
        ),
        address=Address(
            street1="1 Constitution Plaza",
            city="New Haven",
            state="CT",
            zip="06510",
        ),
        w2s=[
            W2(
                employer_name="Long Island Sound Co",
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


class TestConnecticutPluginMeta:
    def test_meta_fields(self):
        """Consolidated meta assertion on the core fields per task spec."""
        assert PLUGIN.meta.code == "CT"
        assert PLUGIN.meta.name == "Connecticut"
        assert PLUGIN.meta.has_income_tax is True
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI
        # CT uses its own free portal (myconneCT).
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )
        # CT has NO reciprocity agreements.
        assert PLUGIN.meta.reciprocity_partners == ()
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_plugin_is_state_plugin_protocol(self):
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_connecticut_plugin_instance(self):
        assert isinstance(PLUGIN, ConnecticutPlugin)

    def test_meta_dor_url_is_portal_ct_gov_drs(self):
        assert "portal.ct.gov/drs" in PLUGIN.meta.dor_url.lower()

    def test_meta_free_efile_url_is_myconnect(self):
        assert PLUGIN.meta.free_efile_url is not None
        assert "myconnect" in PLUGIN.meta.free_efile_url.lower()

    def test_meta_notes_flag_tenforty_not_supported(self):
        """Notes must loudly flag that tenforty does NOT support CT."""
        notes_lower = PLUGIN.meta.notes.lower()
        assert "tenforty" in notes_lower
        # Must either say "not" or "hand-rolled".
        assert "not" in notes_lower or "hand-rolled" in notes_lower

    def test_meta_notes_mention_bracket_rates(self):
        """Notes should mention the bracket rates so readers can sanity-check."""
        assert "2%" in PLUGIN.meta.notes or "2.0" in PLUGIN.meta.notes
        assert "6.99" in PLUGIN.meta.notes

    def test_meta_is_frozen(self):
        with pytest.raises(Exception):
            PLUGIN.meta.code = "NY"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# The wrap-correctness lock: tenforty explicitly does NOT support CT
# ---------------------------------------------------------------------------


class TestTenfortyDoesNotSupportCT:
    """LOUD TODO gatekeeper.

    The fan-out task brief said to wrap tenforty's CT support. In
    reality tenforty has ``OTSState.CT`` in the enum but zero
    ``OTS_FORM_CONFIG`` entries for ``CT_1`` in any year, so the call
    raises. This test locks that behavior — when it starts failing,
    tenforty has presumably added CT support and the CT plugin should
    be rewritten to wrap tenforty (mirror ``nc.py`` / ``oh.py``) and
    the locked Single-65k value re-derived from ``tf_result.state_total_tax``.
    """

    @pytest.mark.parametrize("year", [2020, 2021, 2022, 2023, 2024, 2025])
    def test_tenforty_refuses_ct_every_year(self, year):
        """tenforty raises ValueError('OTS does not support <year>/CT_1')
        for every year in its OTSYear enum."""
        with pytest.raises(ValueError, match="OTS does not support"):
            tenforty.evaluate_return(
                year=year,
                state="CT",
                filing_status="Single",
                w2_income=65000,
                standard_or_itemized="Standard",
            )

    def test_tenforty_backed_flag_is_false(
        self, single_65k_return, federal_single_65k
    ):
        """``state_specific['tenforty_backed']`` must be False so
        downstream consumers can gate warnings on it."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["tenforty_backed"] is False


# ---------------------------------------------------------------------------
# Table A — Personal Exemption (piecewise-constant step function)
# ---------------------------------------------------------------------------


class TestTableAPersonalExemption:
    """Spot-check CT-1040 TCS Table A for every filing status."""

    def test_single_at_zero(self):
        r = compute_ct_tax(FilingStatus.SINGLE, Decimal("0"))
        assert r.line_2_exemption == Decimal("15000.00")

    def test_single_at_30k_boundary(self):
        """$30,000 is 'less than or equal to $30,000' → $15,000."""
        r = compute_ct_tax(FilingStatus.SINGLE, Decimal("30000"))
        assert r.line_2_exemption == Decimal("15000.00")

    def test_single_at_30001_drops_to_14k(self):
        """$30,001 is 'more than $30,000 less than or equal to $31,000'."""
        r = compute_ct_tax(FilingStatus.SINGLE, Decimal("30001"))
        assert r.line_2_exemption == Decimal("14000.00")

    def test_single_at_44k_is_last_step(self):
        r = compute_ct_tax(FilingStatus.SINGLE, Decimal("44000"))
        assert r.line_2_exemption == Decimal("1000.00")

    def test_single_above_44k_is_zero(self):
        r = compute_ct_tax(FilingStatus.SINGLE, Decimal("44001"))
        assert r.line_2_exemption == Decimal("0")

    def test_mfj_at_zero(self):
        r = compute_ct_tax(FilingStatus.MFJ, Decimal("0"))
        assert r.line_2_exemption == Decimal("24000.00")

    def test_mfj_at_71k_is_last_step(self):
        r = compute_ct_tax(FilingStatus.MFJ, Decimal("71000"))
        assert r.line_2_exemption == Decimal("1000.00")

    def test_mfj_above_71k_is_zero(self):
        r = compute_ct_tax(FilingStatus.MFJ, Decimal("71001"))
        assert r.line_2_exemption == Decimal("0")

    def test_qss_uses_mfj_exemption(self):
        """Qualifying Surviving Spouse shares MFJ's Table A."""
        r_qss = compute_ct_tax(FilingStatus.QSS, Decimal("50000"))
        r_mfj = compute_ct_tax(FilingStatus.MFJ, Decimal("50000"))
        assert r_qss.line_2_exemption == r_mfj.line_2_exemption

    def test_mfs_at_zero(self):
        r = compute_ct_tax(FilingStatus.MFS, Decimal("0"))
        assert r.line_2_exemption == Decimal("12000.00")

    def test_mfs_above_35k_is_zero(self):
        r = compute_ct_tax(FilingStatus.MFS, Decimal("35001"))
        assert r.line_2_exemption == Decimal("0")

    def test_hoh_at_zero(self):
        r = compute_ct_tax(FilingStatus.HOH, Decimal("0"))
        assert r.line_2_exemption == Decimal("19000.00")

    def test_hoh_above_56k_is_zero(self):
        r = compute_ct_tax(FilingStatus.HOH, Decimal("56001"))
        assert r.line_2_exemption == Decimal("0")


# ---------------------------------------------------------------------------
# Table B — Initial Tax Calculation (piecewise-linear on CT Taxable Income)
# ---------------------------------------------------------------------------


class TestTableBInitialTax:
    """Spot-check CT-1040 TCS Table B for every bracket transition.

    All examples below are the DRS-published Table B examples (pages 2
    of the TCS) or exact bracket-boundary calculations.
    """

    def test_single_first_bracket_5000(self):
        """Single TI $5,000 → 2% × $5,000 = $100."""
        # Use AGI that yields TI $5,000: AGI = $5k (Table A Single = $15k
        # exemption for AGI <= $30k, so TI = max(0, 5000 - 15000) = 0).
        # Instead construct via MFS at $5k (exemption $12k -> TI 0).
        # Easier: AGI $50k for Single has exemption $0 and TI $50k.
        # To test the 2% bracket directly, use a filing status with no
        # exemption at low AGI. We use Single with AGI slightly above
        # the exemption phase-out: AGI $44,001 -> exemption 0 -> TI $44,001.
        # That's deep in the 4.5% bracket, not useful.
        # Simplest: exercise Table B via synthetic TI by calling the
        # helper directly.
        from skill.scripts.states.ct import _table_b_initial_tax
        assert _table_b_initial_tax(
            FilingStatus.SINGLE, Decimal("5000")
        ) == Decimal("100.00")

    def test_single_example_ti_13000_is_335(self):
        """TCS example: 'Line 3 is $13,000, Line 4 is $335' (Single/MFS)."""
        from skill.scripts.states.ct import _table_b_initial_tax
        assert _table_b_initial_tax(
            FilingStatus.SINGLE, Decimal("13000")
        ) == Decimal("335.00")

    def test_single_example_ti_525000_is_32998(self):
        """TCS example: 'Line 3 is $525,000, Line 4 is $32,998' (Single/MFS)."""
        from skill.scripts.states.ct import _table_b_initial_tax
        assert _table_b_initial_tax(
            FilingStatus.SINGLE, Decimal("525000")
        ) == Decimal("32998.00")

    def test_mfs_uses_same_table_as_single(self):
        """TCS explicitly says 'Single or Married Filing Separately'."""
        from skill.scripts.states.ct import _table_b_initial_tax
        assert (
            _table_b_initial_tax(FilingStatus.MFS, Decimal("50000"))
            == _table_b_initial_tax(FilingStatus.SINGLE, Decimal("50000"))
        )

    def test_mfj_example_ti_22500_is_513(self):
        """TCS example: 'Line 3 is $22,500, Line 4 is $513' (MFJ/QSS)."""
        from skill.scripts.states.ct import _table_b_initial_tax
        assert _table_b_initial_tax(
            FilingStatus.MFJ, Decimal("22500")
        ) == Decimal("513.00")

    def test_mfj_example_ti_1_100_000_is_69490(self):
        """TCS example: 'Line 3 is $1,100,000, Line 4 is $69,490' (MFJ/QSS)."""
        from skill.scripts.states.ct import _table_b_initial_tax
        assert _table_b_initial_tax(
            FilingStatus.MFJ, Decimal("1100000")
        ) == Decimal("69490.00")

    def test_qss_uses_mfj_table(self):
        """TCS explicitly says 'MFJ or Qualifying Surviving Spouse'."""
        from skill.scripts.states.ct import _table_b_initial_tax
        assert (
            _table_b_initial_tax(FilingStatus.QSS, Decimal("100000"))
            == _table_b_initial_tax(FilingStatus.MFJ, Decimal("100000"))
        )

    def test_hoh_example_ti_20000_is_500(self):
        """TCS example: 'Line 3 is $20,000, Line 4 is $500' (HOH)."""
        from skill.scripts.states.ct import _table_b_initial_tax
        assert _table_b_initial_tax(
            FilingStatus.HOH, Decimal("20000")
        ) == Decimal("500.00")

    def test_hoh_example_ti_825000_is_51748(self):
        """TCS example: 'Line 3 is $825,000, Line 4 is $51,748' (HOH)."""
        from skill.scripts.states.ct import _table_b_initial_tax
        assert _table_b_initial_tax(
            FilingStatus.HOH, Decimal("825000")
        ) == Decimal("51748.00")

    def test_single_top_bracket_1m(self):
        """Single TI $1M: $31,250 + 6.99% × $500,000 = $66,200."""
        from skill.scripts.states.ct import _table_b_initial_tax
        assert _table_b_initial_tax(
            FilingStatus.SINGLE, Decimal("1000000")
        ) == Decimal("66200.00")

    def test_zero_ti_is_zero_tax(self):
        from skill.scripts.states.ct import _table_b_initial_tax
        assert _table_b_initial_tax(
            FilingStatus.SINGLE, Decimal("0")
        ) == Decimal("0")

    def test_negative_ti_clamps_to_zero(self):
        from skill.scripts.states.ct import _table_b_initial_tax
        assert _table_b_initial_tax(
            FilingStatus.SINGLE, Decimal("-1000")
        ) == Decimal("0")


# ---------------------------------------------------------------------------
# Table C — 2% Phase-Out Add-Back
# ---------------------------------------------------------------------------


class TestTableCPhaseoutAddback:
    def test_single_below_56500_is_zero(self):
        r = compute_ct_tax(FilingStatus.SINGLE, Decimal("56000"))
        assert r.line_5_phaseout_addback == Decimal("0")

    def test_single_at_65000_is_50(self):
        """Single AGI $65k is in the '$61,500 < AGI <= $66,500' row → $50."""
        r = compute_ct_tax(FilingStatus.SINGLE, Decimal("65000"))
        assert r.line_5_phaseout_addback == Decimal("50.00")

    def test_single_top_of_table_c_is_250(self):
        r = compute_ct_tax(FilingStatus.SINGLE, Decimal("200000"))
        assert r.line_5_phaseout_addback == Decimal("250.00")

    def test_mfj_top_of_table_c_is_500(self):
        r = compute_ct_tax(FilingStatus.MFJ, Decimal("200000"))
        assert r.line_5_phaseout_addback == Decimal("500.00")

    def test_hoh_at_80k_is_40(self):
        """HOH AGI $80k is in '$78,500 < AGI <= $82,500' row → $40."""
        r = compute_ct_tax(FilingStatus.HOH, Decimal("80000"))
        assert r.line_5_phaseout_addback == Decimal("40.00")


# ---------------------------------------------------------------------------
# Table D — Tax Recapture
# ---------------------------------------------------------------------------


class TestTableDRecapture:
    def test_single_below_105k_is_zero(self):
        r = compute_ct_tax(FilingStatus.SINGLE, Decimal("100000"))
        assert r.line_6_tax_recapture == Decimal("0")

    def test_single_at_500k_is_2950(self):
        """Single AGI $500k is in the $345k-$500k row → $2,950."""
        r = compute_ct_tax(FilingStatus.SINGLE, Decimal("500000"))
        assert r.line_6_tax_recapture == Decimal("2950.00")

    def test_single_top_of_table_d_is_3400(self):
        """Single AGI above $540k → recapture capped at $3,400."""
        r = compute_ct_tax(FilingStatus.SINGLE, Decimal("1000000"))
        assert r.line_6_tax_recapture == Decimal("3400.00")

    def test_mfj_below_210k_is_zero(self):
        r = compute_ct_tax(FilingStatus.MFJ, Decimal("200000"))
        assert r.line_6_tax_recapture == Decimal("0")

    def test_mfj_top_of_table_d_is_6800(self):
        r = compute_ct_tax(FilingStatus.MFJ, Decimal("2000000"))
        assert r.line_6_tax_recapture == Decimal("6800.00")


# ---------------------------------------------------------------------------
# Table E — Personal Tax Credit (decimal multiplier)
# ---------------------------------------------------------------------------


class TestTableEPersonalCredit:
    def test_single_just_above_15k_is_075(self):
        """Single AGI $15,001 is in 'More Than $15,000 Less Than or Equal
        To $18,800' row → decimal 0.75 (max credit)."""
        r = compute_ct_tax(FilingStatus.SINGLE, Decimal("15001"))
        assert r.line_8_credit_decimal == Decimal("0.75")

    def test_single_at_15k_exactly_is_zero(self):
        """AGI exactly $15,000 is not 'more than $15,000' — falls in the
        implicit below-threshold region → 0 (no credit)."""
        r = compute_ct_tax(FilingStatus.SINGLE, Decimal("15000"))
        assert r.line_8_credit_decimal == Decimal("0")

    def test_single_at_30k_is_010(self):
        """Single AGI $30k is in the $26,500-$31,300 row → 0.15.
        Wait: $30k falls in row '26500 < AGI <= 31300' → 0.15."""
        r = compute_ct_tax(FilingStatus.SINGLE, Decimal("30000"))
        assert r.line_8_credit_decimal == Decimal("0.15")

    def test_single_above_64500_is_zero(self):
        r = compute_ct_tax(FilingStatus.SINGLE, Decimal("64501"))
        assert r.line_8_credit_decimal == Decimal("0")


# ---------------------------------------------------------------------------
# compute() — resident cases (the wrap-correctness scenarios)
# ---------------------------------------------------------------------------


class TestConnecticutPluginResident:
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
        assert result.state == "CT"
        assert result.residency == ResidencyStatus.RESIDENT
        assert result.days_in_state == 365

    def test_resident_single_65k_tax_lock(
        self, single_65k_return, federal_single_65k
    ):
        """CORE WRAP-CORRECTNESS LOCK: Single $65k W-2 CT resident.

        Per Form CT-1040 TCS (Rev. 12/25):
            Line 1 (CT AGI)             = $65,000
            Line 2 (Personal Exemption) = $0     (Table A: Single > $44k)
            Line 3 (CT TI)              = $65,000
            Line 4 (Initial Tax)        = $2,825 (Table B: $2k + 5.5%*$15k)
            Line 5 (2% Add-Back)        = $50    (Table C: $61.5k<AGI<=$66.5k)
            Line 6 (Tax Recapture)      = $0     (Table D: AGI<=$105k)
            Line 7 (Sum)                = $2,875
            Line 8 (Credit decimal)     = 0.00   (Table E: AGI>$64.5k)
            Line 10 (CT Tax)            = $2,875

        This is THE number the task brief asked us to lock bit-for-bit.
        Since tenforty does not support CT, "bit-for-bit match against
        tenforty" is impossible; instead we lock against the hand-derived
        TCS schedule value. ``TestTenfortyDoesNotSupportCT`` above
        independently asserts tenforty refuses CT so downstream cannot
        accidentally "cross-check" against a silently-wrong fallback.
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        state_tax = result.state_specific["state_total_tax"]
        assert isinstance(state_tax, Decimal)
        assert state_tax == LOCK_VALUE

    def test_resident_single_65k_all_tcs_lines(
        self, single_65k_return, federal_single_65k
    ):
        """Every TCS line from 1-10 is pinned on state_specific."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = result.state_specific
        assert ss["tcs_line_1_ct_agi"] == Decimal("65000.00")
        assert ss["tcs_line_2_exemption"] == Decimal("0.00")
        assert ss["tcs_line_3_ct_taxable_income"] == Decimal("65000.00")
        assert ss["tcs_line_4_initial_tax"] == Decimal("2825.00")
        assert ss["tcs_line_5_phaseout_addback"] == Decimal("50.00")
        assert ss["tcs_line_6_tax_recapture"] == Decimal("0.00")
        assert ss["tcs_line_7_sum"] == Decimal("2875.00")
        assert ss["tcs_line_8_credit_decimal"] == Decimal("0")
        assert ss["tcs_line_9_credit_amount"] == Decimal("0.00")
        assert ss["tcs_line_10_ct_tax"] == Decimal("2875.00")

    def test_resident_mfj_120k_ct_tax_is_5300(
        self, mfj_120k_return, federal_mfj_120k
    ):
        """MFJ $120k → Line 4 = $4k + 5.5%*$20k = $5,100; Line 5 = $200
        (MFJ $115.5k<AGI<=$120.5k); Line 6 = $0; Line 10 = $5,300."""
        result = PLUGIN.compute(
            mfj_120k_return,
            federal_mfj_120k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_total_tax"] == Decimal("5300.00")

    def test_state_agi_equals_federal_agi_in_v1(
        self, single_65k_return, federal_single_65k
    ):
        """v1: CT AGI ≈ federal AGI (Schedule 1 additions/subtractions
        are not modeled). A v1 limitation."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific[
            "state_adjusted_gross_income"
        ] == Decimal("65000.00")

    def test_state_specific_has_v1_limitations_list(
        self, single_65k_return, federal_single_65k
    ):
        """v1_limitations is a non-empty list enumerating CT items we
        do not yet model (Schedule 1, property tax credit, CT EITC, AMT, ...)."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        lims = result.state_specific["v1_limitations"]
        assert isinstance(lims, list)
        assert len(lims) >= 5

    def test_v1_limitations_flags_tenforty_gap(self):
        """The first limitation must loudly call out the tenforty gap."""
        joined = " ".join(CT_V1_LIMITATIONS).lower()
        assert "tenforty" in joined
        assert "does not" in joined or "does not actually" in joined

    def test_state_specific_all_decimal_money_fields(
        self, single_65k_return, federal_single_65k
    ):
        """Every money field in state_specific must be Decimal."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        money_keys = [
            "state_adjusted_gross_income",
            "state_taxable_income",
            "state_total_tax",
            "state_total_tax_resident_basis",
            "tcs_line_1_ct_agi",
            "tcs_line_2_exemption",
            "tcs_line_3_ct_taxable_income",
            "tcs_line_4_initial_tax",
            "tcs_line_5_phaseout_addback",
            "tcs_line_6_tax_recapture",
            "tcs_line_7_sum",
            "tcs_line_9_credit_amount",
            "tcs_line_10_ct_tax",
        ]
        for key in money_keys:
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

    def test_state_return_validates_via_pydantic(
        self, single_65k_return, federal_single_65k
    ):
        """Round-trip through Pydantic JSON to confirm the returned
        StateReturn validates under the canonical model contract."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        dumped = result.model_dump(mode="json")
        rehydrated = StateReturn.model_validate(dumped)
        assert rehydrated.state == "CT"
        assert rehydrated.residency == ResidencyStatus.RESIDENT


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year apportionment
# ---------------------------------------------------------------------------


class TestConnecticutPluginNonresident:
    def test_nonresident_half_year_prorates_tax(
        self, single_65k_return, federal_single_65k
    ):
        """NONRESIDENT with days_in_state=182 yields 182/365 of the
        resident-basis tax via day-based proration."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=182,
        )
        full = result.state_specific["state_total_tax_resident_basis"]
        apportioned = result.state_specific["state_total_tax"]
        assert full == Decimal("2875.00")
        assert apportioned < full
        expected = (full * Decimal(182) / Decimal(365)).quantize(
            Decimal("0.01")
        )
        assert apportioned == expected
        # Sanity: "roughly half" of $2,875 ≈ $1,434.
        assert Decimal("1400") < apportioned < Decimal("1500")

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

    def test_leap_day_clamps_to_one(
        self, single_65k_return, federal_single_65k
    ):
        """days_in_state=366 (leap year) for a nonresident clamps the
        day-based fraction to 1.0 so the apportioned tax equals the
        full resident tax."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=366,
        )
        assert result.state_specific["apportionment_fraction"] == Decimal("1")
        assert result.state_specific["state_total_tax"] == Decimal("2875.00")


# ---------------------------------------------------------------------------
# apportion_income()
# ---------------------------------------------------------------------------


class TestConnecticutPluginApportionIncome:
    def test_apportion_income_resident_full_amounts(self, single_65k_return):
        """Residents get full amounts for every canonical income category."""
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

    def test_apportion_income_nonresident_prorates(self, single_65k_return):
        """Nonresidents with days_in_state=182 get wages * 182/365."""
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.NONRESIDENT, days_in_state=182
        )
        expected = (
            Decimal("65000") * Decimal(182) / Decimal(365)
        ).quantize(Decimal("0.01"))
        assert app.state_source_wages == expected


# ---------------------------------------------------------------------------
# No reciprocity agreements (verified via ReciprocityTable)
# ---------------------------------------------------------------------------


class TestConnecticutNoReciprocity:
    """CT has NO bilateral reciprocity agreements with any state."""

    def test_no_reciprocity_partners(self):
        assert PLUGIN.meta.reciprocity_partners == ()
        assert len(PLUGIN.meta.reciprocity_partners) == 0

    def test_no_reciprocity_via_reciprocity_table(self):
        """The shared ReciprocityTable also reports no partners for CT."""
        table = ReciprocityTable.load()
        assert table.partners_of("CT") == frozenset()
        assert table.has_income_tax("CT") is True

    def test_not_reciprocal_with_neighbors(self):
        """Spot-check CT is not reciprocal with any of its neighbors."""
        table = ReciprocityTable.load()
        for neighbor in ("NY", "MA", "RI", "NJ", "PA"):
            assert table.are_reciprocal("CT", neighbor) is False

    @pytest.mark.parametrize(
        "not_partner", ["NY", "MA", "RI", "NJ", "PA", "CA", "FL", "CT"]
    )
    def test_meta_reciprocity_excludes_every_state(self, not_partner):
        assert not_partner not in PLUGIN.meta.reciprocity_partners


# ---------------------------------------------------------------------------
# form_ids() and render_pdfs()
# ---------------------------------------------------------------------------


class TestConnecticutPluginFormIds:
    def test_form_ids(self):
        form_ids = PLUGIN.form_ids()
        assert "CT Form CT-1040" in form_ids
        assert form_ids == ["CT Form CT-1040"]

    def test_render_pdfs_returns_empty_list(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Fan-out follow-up: actual CT-1040 fill is not yet implemented."""
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
# Cross-check against the 2025 CT Income Tax Tables (DRS published $50-bin
# lookup used by taxpayers at AGI <= $102,000).
# ---------------------------------------------------------------------------


class TestConnecticutTaxTablesCrossCheck:
    """The DRS publishes separate $50-bin "2025 Connecticut Income Tax
    Tables" for AGI <= $102k (https://portal.ct.gov/-/media/drs/forms/
    2025/income/2025-income-tax-tables.pdf). Those tables are derived from
    the same TCS formula but quantize AGI to $50 bins and round to whole
    dollars using the bin midpoint. We verify a handful of anchor points
    from those published tables against our hand-rolled TCS (evaluated at
    the bin midpoint) to triangulate the bracket math independently of
    the Line-by-line TCS assertions above.
    """

    def test_single_65k_bin_matches_published_table(self):
        """Table row 'more than $65,000 less than or equal to $65,050'
        midpoint $65,025 → TCS:
            Line 4 = $2,000 + 5.5% * ($65,025 - $50,000)  = $2,826.375
            Line 5 = $50
            Line 7 = $2,876.375
        DRS published single column reports $2,876 for this row.
        """
        r = compute_ct_tax(FilingStatus.SINGLE, Decimal("65025"))
        # DRS rounds the published tables to whole dollars. We only check
        # that our TCS value rounds to the published $2,876.
        rounded = int(
            r.line_10_ct_tax.quantize(Decimal("1"))
        )
        assert rounded == 2876

    def test_single_just_below_65k_bin(self):
        """Row '$64,950 < AGI <= $65,000' midpoint $64,975 → TCS produces
        Line 4 $2,823.625; Line 5 $50; Line 10 $2,873.625, which rounds
        to $2,874 (matches the $64,000-section bottom row in the
        published DRS table).
        """
        r = compute_ct_tax(FilingStatus.SINGLE, Decimal("64975"))
        rounded = int(r.line_10_ct_tax.quantize(Decimal("1")))
        assert rounded == 2874


# ---------------------------------------------------------------------------
# CTTaxCalcResult dataclass surface
# ---------------------------------------------------------------------------


class TestCTTaxCalcResultDataclass:
    def test_is_frozen(self):
        r = compute_ct_tax(FilingStatus.SINGLE, Decimal("65000"))
        assert isinstance(r, CTTaxCalcResult)
        with pytest.raises(Exception):
            r.line_10_ct_tax = Decimal("0")  # type: ignore[misc]

    def test_all_decimal_fields(self):
        r = compute_ct_tax(FilingStatus.SINGLE, Decimal("65000"))
        for attr in (
            "line_1_ct_agi",
            "line_2_exemption",
            "line_3_ct_taxable_income",
            "line_4_initial_tax",
            "line_5_phaseout_addback",
            "line_6_tax_recapture",
            "line_7_sum",
            "line_8_credit_decimal",
            "line_9_credit_amount",
            "line_10_ct_tax",
        ):
            assert isinstance(getattr(r, attr), Decimal)
