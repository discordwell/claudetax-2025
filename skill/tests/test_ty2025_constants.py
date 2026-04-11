"""Lock every TY2025 constant to its expected value.

These tests are the audit trail: if OBBBA changes mid-year or we discover a
transcription error, a test will fail loudly. Do not mass-update these without
also updating skill/reference/ty2025-landscape.md and citing the new source.

Sources for the expected values all live in skill/reference/ty2025-constants.json
under meta.primary_sources. When a test fails, trace back to the source URL
before changing the expected value.
"""
from __future__ import annotations

import json

import pytest

from skill.scripts.calc import constants as C


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------


def test_tax_year_is_2025():
    assert C.tax_year() == 2025


def test_obbba_adjusted_flag_set():
    assert C.is_obbba_adjusted() is True


def test_primary_sources_are_listed(skill_dir):
    raw = json.loads((skill_dir / "reference" / "ty2025-constants.json").read_text())
    sources = raw["meta"]["primary_sources"]
    required = [
        "obbba_provisions",
        "rev_proc_2024_40",
        "irs_cola",
        "ssa_wage_base",
        "i1040gi_2025",
        "form_8995_instructions",
        "irs_ctc",
        "irs_eitc_tables",
    ]
    for key in required:
        assert key in sources, f"missing primary source: {key}"
        assert sources[key].startswith("http") or sources[key].startswith("skill/")


# ---------------------------------------------------------------------------
# Standard deduction — OBBBA-adjusted
# ---------------------------------------------------------------------------


class TestStandardDeduction:
    """OBBBA raised TY2025 standard deductions above the Rev. Proc. 2024-40 figures."""

    def test_single(self):
        assert C.standard_deduction("single") == 15750

    def test_mfs(self):
        assert C.standard_deduction("mfs") == 15750

    def test_mfj(self):
        assert C.standard_deduction("mfj") == 31500

    def test_qss(self):
        assert C.standard_deduction("qss") == 31500

    def test_hoh(self):
        assert C.standard_deduction("hoh") == 23625

    def test_additional_65_or_blind_mfj(self):
        assert C.additional_standard_deduction_65_or_blind("mfj") == 1600

    def test_additional_65_or_blind_mfs(self):
        assert C.additional_standard_deduction_65_or_blind("mfs") == 1600

    def test_additional_65_or_blind_qss(self):
        assert C.additional_standard_deduction_65_or_blind("qss") == 1600

    def test_additional_65_or_blind_single(self):
        assert C.additional_standard_deduction_65_or_blind("single") == 2000

    def test_additional_65_or_blind_hoh(self):
        assert C.additional_standard_deduction_65_or_blind("hoh") == 2000

    def test_obbba_senior_deduction_amount(self):
        assert C.obbba_senior_deduction()["amount"] == 6000

    def test_obbba_senior_deduction_phase_out_single(self):
        assert C.obbba_senior_deduction()["phase_out_start_single_hoh_mfs"] == 75000

    def test_obbba_senior_deduction_phase_out_mfj(self):
        assert C.obbba_senior_deduction()["phase_out_start_mfj_qss"] == 150000

    def test_obbba_senior_deduction_years(self):
        assert C.obbba_senior_deduction()["years_applicable"] == [2025, 2026, 2027, 2028]


# ---------------------------------------------------------------------------
# Ordinary brackets
# ---------------------------------------------------------------------------


class TestOrdinaryBrackets:
    """Source: IRS Rev. Proc. 2024-40, IRS inflation-adjustments newsroom.
    OBBBA did NOT change these bracket thresholds for TY2025 — TCJA rates are permanent
    but the 2025 thresholds come from Rev. Proc. 2024-40."""

    def test_single_has_seven_brackets(self):
        assert len(C.ordinary_brackets("single")) == 7

    def test_single_10pct_upper(self):
        assert C.ordinary_brackets("single")[0].upper == 11925

    def test_single_12pct_upper(self):
        assert C.ordinary_brackets("single")[1].upper == 48475

    def test_single_22pct_upper(self):
        assert C.ordinary_brackets("single")[2].upper == 103350

    def test_single_24pct_upper(self):
        assert C.ordinary_brackets("single")[3].upper == 197300

    def test_single_32pct_upper(self):
        assert C.ordinary_brackets("single")[4].upper == 250525

    def test_single_35pct_upper(self):
        assert C.ordinary_brackets("single")[5].upper == 626350

    def test_single_37pct_is_open_ended(self):
        assert C.ordinary_brackets("single")[6].upper is None

    def test_mfj_10pct_upper(self):
        assert C.ordinary_brackets("mfj")[0].upper == 23850

    def test_mfj_12pct_upper(self):
        assert C.ordinary_brackets("mfj")[1].upper == 96950

    def test_mfj_22pct_upper(self):
        assert C.ordinary_brackets("mfj")[2].upper == 206700

    def test_mfj_24pct_upper(self):
        assert C.ordinary_brackets("mfj")[3].upper == 394600

    def test_mfj_32pct_upper(self):
        assert C.ordinary_brackets("mfj")[4].upper == 501050

    def test_mfj_35pct_upper(self):
        assert C.ordinary_brackets("mfj")[5].upper == 751600

    def test_mfs_35pct_upper(self):
        """MFS deviates from Single only at the top — 35% bracket tops out at $375,800 instead of $626,350."""
        assert C.ordinary_brackets("mfs")[5].upper == 375800

    def test_hoh_10pct_upper(self):
        assert C.ordinary_brackets("hoh")[0].upper == 17000

    def test_hoh_12pct_upper(self):
        assert C.ordinary_brackets("hoh")[1].upper == 64850

    def test_hoh_32pct_upper(self):
        assert C.ordinary_brackets("hoh")[4].upper == 250500

    def test_all_statuses_rates_match(self):
        """Every filing status uses the same rate ladder; only bracket thresholds differ."""
        rates = [0.10, 0.12, 0.22, 0.24, 0.32, 0.35, 0.37]
        for status in ("single", "mfj", "mfs", "hoh", "qss"):
            brackets = C.ordinary_brackets(status)  # type: ignore[arg-type]
            assert [b.rate for b in brackets] == rates, f"rates wrong for {status}"


# ---------------------------------------------------------------------------
# Capital gains brackets
# ---------------------------------------------------------------------------


class TestCapitalGainsBrackets:
    def test_single_zero_rate(self):
        assert C.capital_gains_brackets("single").zero_rate_upper == 48350

    def test_single_fifteen_rate(self):
        assert C.capital_gains_brackets("single").fifteen_rate_upper == 533400

    def test_mfj_zero_rate(self):
        assert C.capital_gains_brackets("mfj").zero_rate_upper == 96700

    def test_mfj_fifteen_rate(self):
        assert C.capital_gains_brackets("mfj").fifteen_rate_upper == 600050

    def test_hoh_zero_rate(self):
        assert C.capital_gains_brackets("hoh").zero_rate_upper == 64750

    def test_hoh_fifteen_rate(self):
        assert C.capital_gains_brackets("hoh").fifteen_rate_upper == 566700

    def test_mfs_fifteen_rate_differs_from_single(self):
        """MFS 15% threshold is $300,000 (not Single's $533,400)."""
        assert C.capital_gains_brackets("mfs").fifteen_rate_upper == 300000


# ---------------------------------------------------------------------------
# Payroll / SE
# ---------------------------------------------------------------------------


class TestPayrollTaxes:
    def test_social_security_wage_base(self):
        assert C.social_security_wage_base() == 176100

    def test_schedule_se_filing_floor(self):
        assert C.schedule_se_filing_floor() == 400

    def test_schedule_se_combined_rate(self):
        assert C.schedule_se_combined_rate() == pytest.approx(0.153)

    def test_additional_medicare_single(self):
        assert C.additional_medicare_tax_threshold("single") == 200000

    def test_additional_medicare_mfj(self):
        assert C.additional_medicare_tax_threshold("mfj") == 250000

    def test_additional_medicare_mfs(self):
        assert C.additional_medicare_tax_threshold("mfs") == 125000

    def test_niit_single(self):
        assert C.niit_threshold("single") == 200000

    def test_niit_mfj(self):
        assert C.niit_threshold("mfj") == 250000

    def test_niit_mfs(self):
        assert C.niit_threshold("mfs") == 125000


# ---------------------------------------------------------------------------
# QBI
# ---------------------------------------------------------------------------


class TestQBI:
    def test_rate(self):
        assert C.qbi_params("single").rate == 0.20

    def test_phase_in_threshold_single(self):
        assert C.qbi_params("single").phase_in_threshold == 197300

    def test_phase_in_threshold_mfj(self):
        assert C.qbi_params("mfj").phase_in_threshold == 394600

    def test_phase_in_width_single(self):
        assert C.qbi_params("single").phase_in_width == 50000

    def test_phase_in_width_mfj(self):
        assert C.qbi_params("mfj").phase_in_width == 100000

    def test_full_phase_in_single(self):
        assert C.qbi_params("single").full_phase_in_complete == 247300

    def test_full_phase_in_mfj(self):
        assert C.qbi_params("mfj").full_phase_in_complete == 494600


# ---------------------------------------------------------------------------
# Child Tax Credit (OBBBA-adjusted to $2,200)
# ---------------------------------------------------------------------------


class TestChildTaxCredit:
    """OBBBA raised the per-child amount from $2,000 to $2,200 and indexed it going forward."""

    def test_amount_per_child(self):
        assert C.ctc_params("single").amount_per_child == 2200

    def test_refundable_max_actc(self):
        assert C.ctc_params("single").refundable_max_actc == 1700

    def test_phase_out_start_single(self):
        assert C.ctc_params("single").phase_out_start == 200000

    def test_phase_out_start_mfj(self):
        assert C.ctc_params("mfj").phase_out_start == 400000

    def test_phase_out_reduction_per_1000(self):
        assert C.ctc_params("single").phase_out_reduction_per_1000_over == 50


# ---------------------------------------------------------------------------
# EITC
# ---------------------------------------------------------------------------


class TestEITC:
    @pytest.mark.parametrize(
        "kids,expected",
        [(0, 649), (1, 4328), (2, 7152), (3, 8046), (4, 8046), (10, 8046)],
    )
    def test_max_credit(self, kids, expected):
        assert C.eitc_max_credit(kids) == expected

    @pytest.mark.parametrize(
        "kids,status,expected",
        [
            (0, "single", 19104),
            (1, "single", 50434),
            (2, "single", 57310),
            (3, "single", 61555),
            (0, "mfj", 26214),
            (1, "mfj", 57554),
            (2, "mfj", 64430),
            (3, "mfj", 68675),
        ],
    )
    def test_agi_limits(self, kids, status, expected):
        assert C.eitc_agi_limit(kids, status) == expected

    def test_investment_income_disqualifier(self):
        assert C.eitc_investment_income_disqualifier() == 11950


# ---------------------------------------------------------------------------
# Retirement limits
# ---------------------------------------------------------------------------


class TestRetirementLimits:
    def test_401k_elective_deferral(self):
        assert C.elective_deferral_401k() == 23500

    def test_ira_limit(self):
        assert C.ira_contribution_limit() == 7000

    def test_401k_catch_up_50(self):
        limits = C.retirement_limits()
        assert limits["catch_up_age_50_plus_401k_403b_457_tsp"] == 7500

    def test_super_catch_up_60_63(self):
        """SECURE 2.0 §109, effective 2025."""
        limits = C.retirement_limits()
        assert limits["super_catch_up_ages_60_63_401k_403b_457_tsp"]["amount"] == 11250

    def test_ira_catch_up_50(self):
        limits = C.retirement_limits()
        assert limits["ira_catch_up_age_50_plus"] == 1000

    def test_sep_ira_max_dollar(self):
        limits = C.retirement_limits()
        assert limits["sep_ira"]["max_dollar"] == 70000

    def test_simple_ira_elective_deferral(self):
        limits = C.retirement_limits()
        assert limits["simple_ira_elective_deferral"] == 16500

    def test_simple_ira_super_catch_up_60_63(self):
        limits = C.retirement_limits()
        assert limits["simple_ira_super_catch_up_ages_60_63"] == 5250

    def test_db_415_limit(self):
        limits = C.retirement_limits()
        assert limits["defined_benefit_415_limit"] == 280000


# ---------------------------------------------------------------------------
# HSA / HDHP
# ---------------------------------------------------------------------------


class TestHSA:
    def test_self_only(self):
        assert C.hsa_limit("self") == 4300

    def test_family(self):
        assert C.hsa_limit("family") == 8550

    def test_catch_up_55(self, skill_dir):
        raw = json.loads((skill_dir / "reference" / "ty2025-constants.json").read_text())
        assert raw["hsa"]["catch_up_age_55_plus"] == 1000

    def test_hdhp_min_deductible_self(self, skill_dir):
        raw = json.loads((skill_dir / "reference" / "ty2025-constants.json").read_text())
        assert raw["hsa"]["hdhp_min_annual_deductible_self_only"] == 1650

    def test_hdhp_min_deductible_family(self, skill_dir):
        raw = json.loads((skill_dir / "reference" / "ty2025-constants.json").read_text())
        assert raw["hsa"]["hdhp_min_annual_deductible_family"] == 3300

    def test_hdhp_max_oop_self(self, skill_dir):
        raw = json.loads((skill_dir / "reference" / "ty2025-constants.json").read_text())
        assert raw["hsa"]["hdhp_max_oop_self_only"] == 8300

    def test_hdhp_max_oop_family(self, skill_dir):
        raw = json.loads((skill_dir / "reference" / "ty2025-constants.json").read_text())
        assert raw["hsa"]["hdhp_max_oop_family"] == 16600


# ---------------------------------------------------------------------------
# Information returns (1099-K threshold, reverted by OBBBA)
# ---------------------------------------------------------------------------


class TestInformationReturns:
    def test_1099k_thresholds(self):
        """OBBBA retroactively reverted 1099-K to $20,000 AND 200 transactions for TY2025."""
        dollar, count = C.form_1099k_thresholds()
        assert dollar == 20000
        assert count == 200


# ---------------------------------------------------------------------------
# Pending research list
# ---------------------------------------------------------------------------


def test_pending_research_is_listed():
    """Calc modules that need unresearched numbers must be blocked on research, not guesses."""
    pending = C.pending_research()
    assert len(pending) > 0
    assert any("AMT" in item for item in pending)
