"""CP4 — tenforty OBBBA verification.

These tests run tenforty on known scenarios and assert that the output matches
OBBBA-adjusted TY2025 expectations. They're the regression guard for our
"wrap + patch" calc strategy: if a future tenforty update changes one of these
numbers, a test will fail loudly and we'll need to re-verify.

Full findings document: skill/reference/cp4-tenforty-verification.md
"""
from __future__ import annotations

import pytest

import tenforty

from skill.scripts.calc import constants as C


def _run(**kwargs):
    kwargs.setdefault("year", 2025)
    kwargs.setdefault("filing_status", "Single")
    kwargs.setdefault("standard_or_itemized", "Standard")
    return tenforty.evaluate_return(**kwargs)


# ---------------------------------------------------------------------------
# Standard deductions match OBBBA-adjusted constants
# ---------------------------------------------------------------------------


class TestStandardDeductionsMatchOBBBA:
    """tenforty must be using the OBBBA numbers, not Rev. Proc. 2024-40 alone."""

    def test_single(self):
        r = _run(filing_status="Single", w2_income=65000)
        implied = r.federal_adjusted_gross_income - r.federal_taxable_income
        assert implied == C.standard_deduction("single") == 15750

    def test_mfs(self):
        r = _run(filing_status="Married/Sep", w2_income=65000)
        implied = r.federal_adjusted_gross_income - r.federal_taxable_income
        assert implied == C.standard_deduction("mfs") == 15750

    def test_mfj(self):
        r = _run(filing_status="Married/Joint", w2_income=150000)
        implied = r.federal_adjusted_gross_income - r.federal_taxable_income
        assert implied == C.standard_deduction("mfj") == 31500

    def test_hoh(self):
        r = _run(filing_status="Head_of_House", w2_income=80000)
        implied = r.federal_adjusted_gross_income - r.federal_taxable_income
        assert implied == C.standard_deduction("hoh") == 23625


# ---------------------------------------------------------------------------
# Federal bracket math matches hand calculations
# ---------------------------------------------------------------------------


class TestFederalBracketMath:
    """Hand-compute expected tax using bracket formulas, accept the $50 tax-table rounding."""

    def test_mfj_468500_taxable(self):
        """MFJ $500k W-2 → $468,500 taxable → $104,046 federal income tax (exact)."""
        r = _run(filing_status="Married/Joint", w2_income=500000)
        assert r.federal_taxable_income == 468500
        # Bracket formula:
        # 10% * 23,850 = 2,385
        # 12% * (96,950-23,850) = 8,772
        # 22% * (206,700-96,950) = 24,145
        # 24% * (394,600-206,700) = 45,096
        # 32% * (468,500-394,600) = 23,648
        # Total = 104,046
        assert r.federal_income_tax == 104046

    def test_hoh_56375_taxable(self):
        """HoH $80k W-2 → $56,375 taxable → $6,425 federal income tax (exact)."""
        r = _run(filing_status="Head_of_House", w2_income=80000)
        assert r.federal_taxable_income == 56375
        # 10% * 17,000 = 1,700
        # 12% * 39,375 = 4,725
        # = 6,425
        assert r.federal_income_tax == 6425


# ---------------------------------------------------------------------------
# Schedule SE computation matches hand calc
# ---------------------------------------------------------------------------


class TestScheduleSE:
    def test_50k_se_income(self):
        """$50k SE: net SE earnings = 50000 * 0.9235, SE tax = net * 0.153."""
        r = _run(w2_income=0, self_employment_income=50000)
        # Expected AGI = 50000 - (SE_tax / 2)
        # SE tax = 50000 * 0.9235 * 0.153 = 7064.775
        # half = 3532.39 (rounded)
        # AGI = 50000 - 3532.39 = 46467.61
        assert abs(r.federal_adjusted_gross_income - 46467.61) < 0.01

        # total_tax must include SE tax — should be roughly fed income tax + 7064.77
        se_tax_included = r.federal_total_tax - r.federal_income_tax
        assert abs(se_tax_included - 7064.77) < 1.0  # allow $1 rounding


# ---------------------------------------------------------------------------
# LTCG worksheet: 0% rate when under threshold
# ---------------------------------------------------------------------------


class TestLTCGZeroRate:
    def test_under_threshold_pays_zero_on_ltcg(self):
        """$40k W-2 + $5k LTCG → total taxable $29,250, all LTCG under $48,350 0% cap."""
        r_no_ltcg = _run(w2_income=40000)
        r_with_ltcg = _run(w2_income=40000, long_term_capital_gains=5000)
        # Adding $5k LTCG should raise AGI by $5k but not change tax (0% rate)
        assert r_with_ltcg.federal_adjusted_gross_income == r_no_ltcg.federal_adjusted_gross_income + 5000
        # Ordinary income portion tax is the same (same as $40k W-2 case would be without LTCG)
        # Under 0% LTCG rate, the $5k LTCG contributes $0 to tax
        # So tax_with_ltcg should == tax computed only on the ordinary portion
        # Check: tax_no_ltcg is $2,605 (ordinary on $24,250)
        # tax_with_ltcg is $2,675 — but it's on a bigger income with 0% LTCG,
        # so the ordinary-portion tax is the same as if we'd had only $40k W-2
        assert r_with_ltcg.federal_income_tax == r_no_ltcg.federal_income_tax


# ---------------------------------------------------------------------------
# Additional Medicare Tax appears on high MFJ wages
# ---------------------------------------------------------------------------


class TestAdditionalMedicareTax:
    def test_mfj_500k_additional_medicare(self):
        """MFJ $500k → 0.9% * (500k - 250k) = $2,250 Additional Medicare Tax."""
        r = _run(filing_status="Married/Joint", w2_income=500000)
        extra_tax = r.federal_total_tax - r.federal_income_tax
        # Could also include NIIT if there's any investment income; none here
        assert abs(extra_tax - 2250) < 1.0


# ---------------------------------------------------------------------------
# CTC gap — documents the known limitation
# ---------------------------------------------------------------------------


class TestCTCGap:
    """tenforty's high-level API does not apply Child Tax Credit from num_dependents.
    This test documents the gap. Our calc engine must compute CTC in the patch layer."""

    def test_num_dependents_does_not_apply_ctc(self):
        r0 = _run(w2_income=60000, num_dependents=0)
        r1 = _run(w2_income=60000, num_dependents=1)
        assert r0.federal_income_tax == r1.federal_income_tax, (
            "If this test starts failing because the values differ, tenforty has added "
            "CTC support to num_dependents. Update the patch layer and this test."
        )


# ---------------------------------------------------------------------------
# State pass-through (CA)
# ---------------------------------------------------------------------------


class TestStatePassthrough:
    def test_california_state_populated(self):
        r = _run(state="CA", w2_income=65000)
        assert r.state_total_tax is not None
        assert r.state_taxable_income is not None
        assert r.state_adjusted_gross_income == 65000
        # CA 8% bracket for middle income is correct for the 2025 CA single brackets
        assert r.state_tax_bracket == 8.0
