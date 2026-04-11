"""Golden fixture: w2_investments_itemized.

Exercises the calc engine on an MFJ return with:
  - Two W-2s (one taxpayer, one spouse)
  - 1099-INT interest income
  - 1099-DIV ordinary + qualified dividends + capital gain distribution
  - 1099-B long-term capital gain
  - Itemized deductions with SALT cap ($23k raw -> $10k cap)
  - Itemized election over the MFJ standard deduction ($35k > $31.5k)

Regression locks (see expected.json hand_check):
  - deduction_taken == 35000 EXPLICITLY (SALT cap regression lock)
  - total_income includes LT cap gain $10k + cap gain distribution $500
  - amount_owed reflects 23000 withholding < total_tax
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.calc.engine import compute
from skill.scripts.models import CanonicalReturn


GOLDEN_NAME = "w2_investments_itemized"


@pytest.fixture
def golden_dir(fixtures_dir: Path) -> Path:
    return fixtures_dir / GOLDEN_NAME


@pytest.fixture
def input_return(golden_dir: Path) -> CanonicalReturn:
    data = json.loads((golden_dir / "input.json").read_text())
    return CanonicalReturn.model_validate(data)


@pytest.fixture
def expected(golden_dir: Path) -> dict:
    return json.loads((golden_dir / "expected.json").read_text())


def _d(s: str | None) -> Decimal | None:
    return Decimal(s) if s is not None else None


@pytest.mark.golden
class TestW2InvestmentsItemizedGolden:
    def test_input_loads(self, input_return):
        assert input_return.tax_year == 2025
        assert input_return.filing_status.value == "mfj"
        assert input_return.spouse is not None
        assert len(input_return.w2s) == 2
        assert input_return.w2s[0].box1_wages == Decimal("150000.00")
        assert input_return.w2s[0].employee_is_taxpayer is True
        assert input_return.w2s[1].box1_wages == Decimal("50000.00")
        assert input_return.w2s[1].employee_is_taxpayer is False
        assert len(input_return.forms_1099_int) == 1
        assert len(input_return.forms_1099_div) == 1
        assert len(input_return.forms_1099_b) == 1
        assert len(input_return.forms_1099_b[0].transactions) == 1
        assert input_return.itemize_deductions is True
        assert input_return.itemized is not None

    def test_compute_runs(self, input_return):
        result = compute(input_return)
        assert result.computed.adjusted_gross_income is not None
        assert result.computed.total_tax is not None

    def test_total_income(self, input_return, expected):
        result = compute(input_return)
        assert result.computed.total_income == _d(
            expected["expected_computed_totals"]["total_income"]
        )

    def test_total_income_includes_ltcg_and_cap_gain_distributions(self, input_return):
        """Regression lock: total_income must include the $10k LT 1099-B gain
        and the $500 cap gain distribution. Wages alone are $200k, investment
        stream adds at least $18.5k, so total_income must be >= $218,500."""
        result = compute(input_return)
        assert result.computed.total_income is not None
        assert result.computed.total_income >= Decimal("218500.00")
        # And the exact figure, since this is a golden:
        assert result.computed.total_income == Decimal("218500.00")

    def test_adjusted_gross_income(self, input_return, expected):
        result = compute(input_return)
        assert result.computed.adjusted_gross_income == _d(
            expected["expected_computed_totals"]["adjusted_gross_income"]
        )

    def test_salt_cap_regression_lock(self, input_return):
        """Explicit SALT cap regression lock.

        Raw SALT = $15,000 state income tax + $8,000 real estate tax = $23,000.
        MFJ cap = $10,000. Capped SALT = $10,000.
        Itemized total = $10,000 SALT + $20,000 mortgage + $5,000 charity = $35,000.
        This test pins deduction_taken == 35000 EXACTLY so any regression in the
        SALT cap logic trips immediately.
        """
        result = compute(input_return)
        assert result.computed.deduction_taken == Decimal("35000.00")

    def test_deduction_taken_matches_expected(self, input_return, expected):
        result = compute(input_return)
        assert result.computed.deduction_taken == _d(
            expected["expected_computed_totals"]["deduction_taken"]
        )

    def test_itemized_elected_over_standard(self, input_return):
        """$35,000 itemized > $31,500 MFJ standard, so itemized must be taken."""
        result = compute(input_return)
        assert result.computed.deduction_taken == Decimal("35000.00")
        # Sanity: must be strictly greater than the MFJ standard deduction.
        assert result.computed.deduction_taken > Decimal("31500.00")

    def test_taxable_income(self, input_return, expected):
        result = compute(input_return)
        assert result.computed.taxable_income == _d(
            expected["expected_computed_totals"]["taxable_income"]
        )

    def test_federal_income_tax(self, input_return, expected):
        result = compute(input_return)
        assert result.computed.tentative_tax == _d(
            expected["expected_computed_totals"]["tentative_tax"]
        )

    def test_total_tax(self, input_return, expected):
        result = compute(input_return)
        assert result.computed.total_tax == _d(
            expected["expected_computed_totals"]["total_tax"]
        )

    def test_total_payments_from_both_w2s(self, input_return, expected):
        """$18,000 (Jamie) + $5,000 (Pat) = $23,000 total federal withholding."""
        result = compute(input_return)
        assert result.computed.total_payments == _d(
            expected["expected_computed_totals"]["total_payments"]
        )
        assert result.computed.total_payments == Decimal("23000.00")

    def test_refund_or_owed_sign_consistent_with_withholding(self, input_return, expected):
        """Withholding $23,000 < total tax $29,253, so must owe (not refund)."""
        result = compute(input_return)
        c = result.computed
        assert c.total_payments == Decimal("23000.00")
        assert c.total_tax is not None
        # Since withholding < total_tax, refund must be None and amount_owed set.
        assert c.refund is None
        assert c.amount_owed is not None
        assert c.amount_owed == _d(expected["expected_computed_totals"]["amount_owed"])
        # Sign consistency: amount_owed == total_tax - total_payments
        assert c.amount_owed == c.total_tax - c.total_payments

    def test_marginal_rate(self, input_return, expected):
        result = compute(input_return)
        assert result.computed.marginal_rate == expected["expected_computed_totals"]["marginal_rate"]

    def test_all_expected_fields_match(self, input_return, expected):
        """Catch-all: diff every expected field against actuals in one go."""
        result = compute(input_return)
        exp = expected["expected_computed_totals"]
        c = result.computed

        mismatches: list[str] = []

        def _check(name: str, actual, expected_val) -> None:
            if isinstance(expected_val, str):
                expected_decimal = _d(expected_val)
                if actual != expected_decimal:
                    mismatches.append(f"{name}: actual={actual!r} expected={expected_decimal!r}")
            elif expected_val is None:
                if actual is not None:
                    mismatches.append(f"{name}: actual={actual!r} expected=None")
            else:
                if actual != expected_val:
                    mismatches.append(f"{name}: actual={actual!r} expected={expected_val!r}")

        _check("total_income", c.total_income, exp["total_income"])
        _check("adjustments_total", c.adjustments_total, exp["adjustments_total"])
        _check("adjusted_gross_income", c.adjusted_gross_income, exp["adjusted_gross_income"])
        _check("deduction_taken", c.deduction_taken, exp["deduction_taken"])
        _check("taxable_income", c.taxable_income, exp["taxable_income"])
        _check("tentative_tax", c.tentative_tax, exp["tentative_tax"])
        _check("other_taxes_total", c.other_taxes_total, exp["other_taxes_total"])
        _check("total_tax", c.total_tax, exp["total_tax"])
        _check("total_payments", c.total_payments, exp["total_payments"])
        _check("refund", c.refund, exp["refund"])
        _check("amount_owed", c.amount_owed, exp["amount_owed"])
        _check("effective_rate", c.effective_rate, exp["effective_rate"])
        _check("marginal_rate", c.marginal_rate, exp["marginal_rate"])

        if mismatches:
            pytest.fail("Golden diff failures:\n" + "\n".join(mismatches))
