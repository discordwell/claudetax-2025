"""Golden fixture: senior filer with qualified tips + validation report.

Exercises the wave-3 cleanup series (S1/S2/S3) end-to-end:

  1. Both OBBBA pre-tax-bracket patches fire in a single compute() call
     (senior deduction + Schedule 1-A tips). The two-pass tenforty
     strategy must recompute AGI on the reduced value and produce the
     correct bracket-calculated tax.
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

    def test_both_obbba_patches_fire_in_single_pass(self, fixtures_dir: Path):
        """Proves both pre-tax-bracket patches fire simultaneously. The
        two-pass tenforty strategy must handle the combined deduction
        correctly, not just one patch at a time."""
        ret, _ = _load_fixture(fixtures_dir)
        r = compute(ret)
        # Senior deduction fired (age 67, $80k MAGI with phase-out)
        assert r.adjustments.senior_deduction_obbba == Decimal("5700.00")
        # Schedule 1-A tips fired ($5k under cap, under phase-out)
        assert r.adjustments.qualified_tips_deduction_schedule_1a == Decimal(
            "5000.00"
        )
        # Combined adjustments: $10,700 reduction off $80k → $69,300 AGI
        assert r.computed.adjustments_total == Decimal("10700.00")
        assert r.computed.adjusted_gross_income == Decimal("69300.00")
