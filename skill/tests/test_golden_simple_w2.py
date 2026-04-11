"""CP7 — first golden fixture: simple_w2_standard.

End-to-end pipeline test:
  1. Load fixtures/simple_w2_standard/input.json as a CanonicalReturn
  2. Run through the calc engine (which wraps tenforty)
  3. Diff the resulting ComputedTotals against fixtures/simple_w2_standard/expected.json

This is the proof that CP1-CP6 plumb together correctly. It will be the template
every future golden fixture copies from. If this test fails, the critical path
has a regression — nothing else matters until it's green again.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.calc.engine import compute
from skill.scripts.models import CanonicalReturn


GOLDEN_NAME = "simple_w2_standard"


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
class TestSimpleW2StandardGolden:
    def test_input_loads(self, input_return):
        assert input_return.tax_year == 2025
        assert input_return.filing_status.value == "single"
        assert len(input_return.w2s) == 1
        assert input_return.w2s[0].box1_wages == Decimal("65000.00")

    def test_compute_runs(self, input_return):
        result = compute(input_return)
        assert result.computed.adjusted_gross_income is not None
        assert result.computed.total_tax is not None

    def test_adjusted_gross_income(self, input_return, expected):
        result = compute(input_return)
        assert result.computed.adjusted_gross_income == _d(
            expected["expected_computed_totals"]["adjusted_gross_income"]
        )

    def test_standard_deduction_obbba(self, input_return, expected):
        result = compute(input_return)
        assert result.computed.deduction_taken == _d(
            expected["expected_computed_totals"]["deduction_taken"]
        )

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

    def test_total_payments_from_w2_withholding(self, input_return, expected):
        result = compute(input_return)
        assert result.computed.total_payments == _d(
            expected["expected_computed_totals"]["total_payments"]
        )

    def test_refund(self, input_return, expected):
        result = compute(input_return)
        assert result.computed.refund == _d(
            expected["expected_computed_totals"]["refund"]
        )
        assert result.computed.amount_owed is None

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
        _check("adjusted_gross_income", c.adjusted_gross_income, exp["adjusted_gross_income"])
        _check("deduction_taken", c.deduction_taken, exp["deduction_taken"])
        _check("taxable_income", c.taxable_income, exp["taxable_income"])
        _check("tentative_tax", c.tentative_tax, exp["tentative_tax"])
        _check("total_tax", c.total_tax, exp["total_tax"])
        _check("total_payments", c.total_payments, exp["total_payments"])
        _check("refund", c.refund, exp["refund"])
        _check("amount_owed", c.amount_owed, exp["amount_owed"])
        _check("marginal_rate", c.marginal_rate, exp["marginal_rate"])

        if mismatches:
            pytest.fail("Golden diff failures:\n" + "\n".join(mismatches))
