"""Golden fixture: se_home_office.

Regression-lock test for the blocker-1 fix: Schedule C with full Part II
expenses ($27k) plus home office ($3k), no W-2s, $10k in 2025 estimated
payments.

The critical assertion is on AGI: if the engine ever drops Schedule C
expenses again, AGI will jump from ~$83,641 toward $120,000 and this test
will fail loudly. It also locks SE tax, the 1/2 SE tax above-the-line
adjustment, the standard deduction, and the amount_owed path.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.calc.engine import compute
from skill.scripts.models import CanonicalReturn


GOLDEN_NAME = "se_home_office"


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
class TestSeHomeOfficeGolden:
    def test_input_loads(self, input_return):
        assert input_return.tax_year == 2025
        assert input_return.filing_status.value == "single"
        assert len(input_return.w2s) == 0
        assert len(input_return.schedules_c) == 1
        sc = input_return.schedules_c[0]
        assert sc.business_name == "Freeman Consulting LLC"
        assert sc.line1_gross_receipts == Decimal("120000.00")
        assert sc.line30_home_office_expense == Decimal("3000.00")

    def test_compute_runs(self, input_return):
        result = compute(input_return)
        assert result.computed.adjusted_gross_income is not None
        assert result.computed.total_tax is not None

    def test_adjusted_gross_income(self, input_return, expected):
        result = compute(input_return)
        assert result.computed.adjusted_gross_income == _d(
            expected["expected_computed_totals"]["adjusted_gross_income"]
        )

    def test_agi_regression_lock_below_90k(self, input_return):
        """Blocker-1 regression lock: if AGI is near $120k, the engine has
        dropped Schedule C expenses. AGI must be < 90000 because the 1/2 SE
        tax above-the-line adjustment further reduces SE net profit of $90k.
        """
        result = compute(input_return)
        assert result.computed.adjusted_gross_income is not None
        assert result.computed.adjusted_gross_income < Decimal("90000"), (
            "AGI exceeded $90,000 — engine likely lost the 1/2 SE tax "
            "adjustment or (worse) isn't subtracting Schedule C expenses."
        )

    def test_standard_deduction_obbba(self, input_return, expected):
        result = compute(input_return)
        assert result.computed.deduction_taken == _d(
            expected["expected_computed_totals"]["deduction_taken"]
        )
        assert result.computed.deduction_taken == Decimal("15750")

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

    def test_tentative_tax_strictly_less_than_total_tax(self, input_return):
        """Federal income tax alone must be less than total tax (which includes
        SE tax). If these are equal, SE tax never flowed through.
        """
        result = compute(input_return)
        assert result.computed.tentative_tax is not None
        assert result.computed.total_tax is not None
        assert result.computed.tentative_tax < result.computed.total_tax

    def test_other_taxes_total_matches_se_tax(self, input_return):
        """other_taxes_total (= total_tax - federal_income_tax) should be the
        SE tax. Hand-calc: 90000 * 0.9235 * 0.153 = 12717.03. Tenforty
        produces 12716.60; allow a $1 tolerance to absorb worksheet rounding.
        """
        result = compute(input_return)
        assert result.computed.other_taxes_total is not None
        hand_se_tax = Decimal("12717.03")
        diff = abs(result.computed.other_taxes_total - hand_se_tax)
        assert diff <= Decimal("1.00"), (
            f"other_taxes_total={result.computed.other_taxes_total} "
            f"differs from hand SE tax {hand_se_tax} by {diff}"
        )

    def test_half_se_tax_adjustment_applied(self, input_return):
        """AGI < 90000 (SE net profit) proves the 1/2 SE tax above-the-line
        adjustment was applied. Specifically expect AGI ~ 83641.70.
        """
        result = compute(input_return)
        assert result.computed.adjusted_gross_income is not None
        assert result.computed.adjusted_gross_income < Decimal("90000")
        # AGI should be within ~$5 of hand calculation 90000 - 12716.60/2
        hand_agi = Decimal("90000") - Decimal("12716.60") / Decimal("2")
        diff = abs(result.computed.adjusted_gross_income - hand_agi)
        assert diff <= Decimal("5.00"), (
            f"AGI {result.computed.adjusted_gross_income} differs from "
            f"hand {hand_agi} by {diff}"
        )

    def test_total_payments_from_estimated_taxes(self, input_return, expected):
        result = compute(input_return)
        assert result.computed.total_payments == _d(
            expected["expected_computed_totals"]["total_payments"]
        )
        assert result.computed.total_payments == Decimal("10000.00")

    def test_amount_owed_not_refund(self, input_return, expected):
        """With only $10k in estimated payments against ~$22.5k total tax,
        the taxpayer owes. refund must be None.
        """
        result = compute(input_return)
        assert result.computed.refund is None
        assert result.computed.amount_owed is not None
        assert result.computed.amount_owed == _d(
            expected["expected_computed_totals"]["amount_owed"]
        )

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
        _check("marginal_rate", c.marginal_rate, exp["marginal_rate"])

        if mismatches:
            pytest.fail("Golden diff failures:\n" + "\n".join(mismatches))
