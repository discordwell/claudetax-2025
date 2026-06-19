"""Golden fixture: senior filer with qualified tips + validation report.

Exercises the wave-3 cleanup series (S1/S2/S3) end-to-end:

  1. Both OBBBA Schedule 1-A deductions fire in a single compute() call
     (senior deduction + qualified tips) AS BELOW-THE-LINE deductions: they
     land on Form 1040 line 13b and reduce taxable income only, leaving AGI
     unchanged at $80,000. The §63(f) age-65 additional standard deduction
     ($2,000 single) is also applied to line 12. tenforty is re-run with the
     combined below-the-line deduction so the bracket calculation is correct.
  2. The Form 4547 AGI-leak fix (S1) is preserved — the returned
     trump_account_deduction_form_4547 is $0 even though the canonical
     model still carries the field.
  3. The validation pipeline (S2) runs and produces a compatible FFFF
     report that is stored on ComputedTotals.validation_report.

Hand check documented in `skill/fixtures/senior_with_tips/expected.json`.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from skill.scripts.calc.engine import compute
from skill.scripts.models import CanonicalReturn


def _load_fixture(fixtures_dir: Path):
    inp = json.loads((fixtures_dir / "senior_with_tips" / "input.json").read_text())
    exp = json.loads((fixtures_dir / "senior_with_tips" / "expected.json").read_text())
    return CanonicalReturn.model_validate(inp), exp


class TestSeniorWithTipsGolden:
    def test_computed_totals_match(self, fixtures_dir: Path):
        ret, exp = _load_fixture(fixtures_dir)
        r = compute(ret)
        for field_name, expected_value in exp["expected_computed_totals"].items():
            actual = getattr(r.computed, field_name, None)
            if isinstance(expected_value, str):
                assert actual == Decimal(expected_value), (
                    f"{field_name}: actual={actual!r} expected={expected_value!r}"
                )
            elif expected_value is None:
                assert actual is None, (
                    f"{field_name}: actual={actual!r} expected=None"
                )
            else:
                assert actual == expected_value, (
                    f"{field_name}: actual={actual!r} expected={expected_value!r}"
                )

    def test_adjustments_match(self, fixtures_dir: Path):
        ret, exp = _load_fixture(fixtures_dir)
        r = compute(ret)
        for field_name, expected_value in exp["expected_adjustments"].items():
            actual = getattr(r.adjustments, field_name, None)
            assert actual == Decimal(expected_value), (
                f"adjustments.{field_name}: actual={actual!r} "
                f"expected={expected_value!r}"
            )

    def test_trump_account_field_is_forced_zero(self, fixtures_dir: Path):
        """S1 invariant: the Form 4547 patch forces the field to $0 on
        every compute() call, regardless of caller input."""
        ret, _ = _load_fixture(fixtures_dir)
        r = compute(ret)
        assert r.adjustments.trump_account_deduction_form_4547 == Decimal("0")

    def test_validation_report_is_populated(self, fixtures_dir: Path):
        """S2 invariant: compute() stores a validation report on
        ComputedTotals."""
        ret, _ = _load_fixture(fixtures_dir)
        r = compute(ret)
        assert r.computed.validation_report is not None
        assert "ffff" in r.computed.validation_report

    def test_validation_report_ffff_compatible(self, fixtures_dir: Path):
        """This fixture is FFFF-compatible — no blockers."""
        ret, exp = _load_fixture(fixtures_dir)
        r = compute(ret)
        ffff = r.computed.validation_report["ffff"]
        assert ffff["compatible"] == exp["expected_validation_report"]["ffff_compatible"]
        assert (
            len(ffff["blockers"])
            == exp["expected_validation_report"]["ffff_blockers_count"]
        )

    def test_both_obbba_patches_fire_below_the_line(self, fixtures_dir: Path):
        """Proves both Schedule 1-A deductions fire simultaneously and land
        BELOW the line (line 13b), not on Schedule 1. AGI stays at $80,000;
        only taxable income drops."""
        ret, _ = _load_fixture(fixtures_dir)
        r = compute(ret)
        # Senior deduction fired (age 67, $80k MAGI with phase-out)
        assert r.adjustments.senior_deduction_obbba == Decimal("5700.00")
        # Schedule 1-A tips fired ($5k under cap, under phase-out)
        assert r.adjustments.qualified_tips_deduction_schedule_1a == Decimal(
            "5000.00"
        )
        # The $10,700 of OBBBA deductions is on Form 1040 line 13b, NOT in
        # Schedule 1 adjustments, so AGI is unchanged at $80,000.
        assert r.computed.additional_deductions_schedule_1a == Decimal("10700.00")
        assert r.computed.adjustments_total == Decimal("0.00")
        assert r.computed.adjusted_gross_income == Decimal("80000.00")

    def test_age_65_additional_standard_deduction_applied(self, fixtures_dir: Path):
        """The §63(f) age-65 additional standard deduction ($2,000 single)
        stacks on the base standard deduction at line 12 — tenforty omits it
        because it has no age input, so the engine must add it."""
        ret, _ = _load_fixture(fixtures_dir)
        r = compute(ret)
        # line 12 = $15,750 base + $2,000 age-65 additional = $17,750
        assert r.computed.deduction_taken == Decimal("17750.00")

    def test_form_1040_taxable_income_reconciles(self, fixtures_dir: Path):
        """Form 1040 line 15 = line 11 (AGI) - line 14, where line 14 =
        line 12 + line 13a (QBI) + line 13b (Schedule 1-A)."""
        ret, _ = _load_fixture(fixtures_dir)
        r = compute(ret)
        c = r.computed
        line_12 = c.deduction_taken or Decimal("0")
        line_13a = c.qbi_deduction or Decimal("0")
        line_13b = c.additional_deductions_schedule_1a or Decimal("0")
        line_14 = line_12 + line_13a + line_13b
        assert c.adjusted_gross_income - line_14 == c.taxable_income
