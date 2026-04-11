"""Integration tests for the wave-3 OBBBA patch wiring in engine.compute().

These tests prove that engine.compute() correctly:

  1. Gates the two-pass tenforty strategy behind cheap "any senior?" and
     "any tips/overtime?" checks so baseline returns are unchanged
     (bit-for-bit regression lock against the three existing golden
     fixtures: simple_w2_standard, w2_investments_itemized, se_home_office).
  2. Computes the OBBBA senior deduction on MFJ two-senior returns using
     a clean first-pass MAGI (AGI with the OBBBA adjustments zeroed out),
     folds the result into AdjustmentsToIncome, and re-runs tenforty so
     the bracket calculation sees the reduced AGI.
  3. Computes the Schedule 1-A tips deduction for a single filer with
     declared qualified tips, demonstrating that bracket re-application
     (Approach A) correctly moves the filer across the 22% → 12% bracket
     transition for a $5k tips deduction on a $65k W-2.
  4. Year-gates: for tax_year=2030 the senior deduction patch returns
     zero regardless of age 65+ filers.

Architectural choice
--------------------
Approach A (two-pass tenforty, exact) is used because the OBBBA tips
deduction on a $65k single filer spans a bracket boundary — a marginal-
rate approximation would overstate the tax savings. See the module
docstring in skill/scripts/calc/engine.py for details.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.calc.engine import compute
from skill.scripts.models import (
    Address,
    AdjustmentsToIncome,
    CanonicalReturn,
    FilingStatus,
    Person,
    W2,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _addr() -> Address:
    return Address(street1="1 Test Lane", city="Springfield", state="IL", zip="62701")


def _person(
    first: str = "Alex",
    last: str = "Doe",
    ssn: str = "111-22-3333",
    dob: dt.date = dt.date(1985, 5, 5),
) -> Person:
    return Person(first_name=first, last_name=last, ssn=ssn, date_of_birth=dob)


def _simple_single_return(wages: Decimal, withheld: Decimal = Decimal("0")) -> CanonicalReturn:
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=_person(),
        address=_addr(),
        w2s=[
            W2(
                employer_name="Acme",
                box1_wages=wages,
                box2_federal_income_tax_withheld=withheld,
            )
        ],
    )


def _d(s: str | None) -> Decimal | None:
    return Decimal(s) if s is not None else None


# ---------------------------------------------------------------------------
# 1. Zero-senior, zero-tips baseline — the wiring must be a no-op.
# ---------------------------------------------------------------------------


class TestNoSeniorNoTipsIsUnchanged:
    """A simple_w2_standard-style canonical return should produce EXACTLY
    the pre-wiring values: $5,755 total tax, $1,745 refund. Locks the "cheap
    gate skips the second tenforty call" invariant for the common case.
    """

    def test_simple_return_no_seniors_no_tips_is_unchanged(self):
        ret = _simple_single_return(
            wages=Decimal("65000.00"), withheld=Decimal("7500.00")
        )
        r = compute(ret)
        assert r.computed.adjusted_gross_income == Decimal("65000.00")
        assert r.computed.deduction_taken == Decimal("15750.00")
        assert r.computed.taxable_income == Decimal("49250.00")
        assert r.computed.tentative_tax == Decimal("5755.00")
        assert r.computed.total_tax == Decimal("5755.00")
        assert r.computed.refund == Decimal("1745.00")
        assert r.computed.amount_owed is None

        # OBBBA fields stay at zero on the returned canonical model.
        assert r.adjustments.senior_deduction_obbba == Decimal("0")
        assert r.adjustments.qualified_tips_deduction_schedule_1a == Decimal("0")
        assert r.adjustments.qualified_overtime_deduction_schedule_1a == Decimal("0")
        assert r.computed.adjustments_total == Decimal("0.00")


# ---------------------------------------------------------------------------
# 2. MFJ two seniors, $180k, partial phase-out — full Approach A proof.
# ---------------------------------------------------------------------------


class TestMFJTwoSeniors180kFullDeduction:
    """MFJ, both spouses age 67 at end of 2025 (born 1958), $180k W-2.

    Hand check (from obbba_senior_deduction params, verified in
    test_calc_obbba_senior.py):
      - num_filers_age_65_plus = 2
      - base_deduction = 2 * $6,000 = $12,000
      - MAGI = $180,000 (first-pass AGI with OBBBA fields zeroed out)
      - phase-out threshold (MFJ) = $150,000
      - excess = $30,000
      - phase-out reduction = 0.06 * $30,000 = $1,800
      - final senior_deduction = $12,000 - $1,800 = $10,200

    After folding into adjustments and re-running tenforty:
      - AGI = $180,000 - $10,200 = $169,800
      - standard deduction MFJ = $31,500
      - taxable income = $138,300
      - fed tax (MFJ TY2025 brackets, tenforty-computed) = $20,254
      - total tax = $20,254 (no SE, no NIIT)

    Baseline without OBBBA (same $180k, no seniors): fed tax = $22,498.
    Delta from OBBBA = $2,244 = 22% * $10,200 (the filer stays in the 22%
    bracket after the deduction, so the marginal approximation happens to
    match — we lock the exact number for regression).
    """

    def _ret(self, tax_year: int = 2025) -> CanonicalReturn:
        taxpayer = _person("Alex", "Doe", "111-11-1111", dt.date(1958, 3, 3))
        spouse = _person("Sam", "Doe", "222-22-2222", dt.date(1958, 7, 7))
        return CanonicalReturn(
            tax_year=tax_year,
            filing_status=FilingStatus.MFJ,
            taxpayer=taxpayer,
            spouse=spouse,
            address=_addr(),
            w2s=[
                W2(
                    employer_name="Acme",
                    box1_wages=Decimal("180000.00"),
                    box2_federal_income_tax_withheld=Decimal("0"),
                )
            ],
        )

    def test_senior_deduction_applied_to_adjustments(self):
        r = compute(self._ret())
        assert r.adjustments.senior_deduction_obbba == Decimal("10200")

    def test_agi_reduced_by_senior_deduction(self):
        r = compute(self._ret())
        # $180,000 - $10,200 = $169,800
        assert r.computed.adjusted_gross_income == Decimal("169800.00")

    def test_deduction_taken_is_mfj_standard(self):
        r = compute(self._ret())
        # Note: this is the Form 1040 line 12 standard deduction ($31,500),
        # NOT the senior deduction (which is a Schedule 1 above-the-line
        # adjustment, flowing through AGI).
        assert r.computed.deduction_taken == Decimal("31500.00")

    def test_taxable_income_reflects_senior_deduction(self):
        r = compute(self._ret())
        # $169,800 AGI - $31,500 standard = $138,300
        assert r.computed.taxable_income == Decimal("138300.00")

    def test_fed_tax_reflects_bracket_reapplication(self):
        r = compute(self._ret())
        assert r.computed.tentative_tax == Decimal("20254.00")
        assert r.computed.total_tax == Decimal("20254.00")

    def test_senior_deduction_is_in_adjustments_total(self):
        r = compute(self._ret())
        # adjustments_total = sum of Schedule 1 Part II items; here only
        # the senior deduction is non-zero.
        assert r.computed.adjustments_total == Decimal("10200.00")

    def test_delta_vs_no_senior_return(self):
        """Same return without age 65+ filers should NOT apply the senior
        deduction (cheap gate skips the second pass entirely)."""
        younger_tp = _person("Alex", "Doe", "111-11-1111", dt.date(1985, 3, 3))
        younger_sp = _person("Sam", "Doe", "222-22-2222", dt.date(1985, 7, 7))
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.MFJ,
            taxpayer=younger_tp,
            spouse=younger_sp,
            address=_addr(),
            w2s=[
                W2(
                    employer_name="Acme",
                    box1_wages=Decimal("180000.00"),
                )
            ],
        )
        without = compute(ret)
        with_ = compute(self._ret())
        assert without.computed.adjusted_gross_income == Decimal("180000.00")
        assert without.adjustments.senior_deduction_obbba == Decimal("0")
        # MFJ $180k baseline fed tax (verified via direct tenforty call)
        assert without.computed.tentative_tax == Decimal("22498.00")
        # Senior patch reduces tax by exactly $2,244 (= 22% * $10,200)
        assert (
            without.computed.total_tax - with_.computed.total_tax
            == Decimal("2244.00")
        )


# ---------------------------------------------------------------------------
# 3. Single filer with $5k declared qualified tips — Schedule 1-A wiring.
# ---------------------------------------------------------------------------


class TestSingleWithTips65k:
    """Single filer, $65k W-2, $5,000 declared qualified tips.

    Hand check (from obbba_schedule_1a.py constants):
      - TIPS_CAP = $25,000; $5k < cap → no cap applied
      - Single phase-out start = $150,000; MAGI $65k < $150k → no phase-out
      - Final tips_deduction = $5,000

    After the engine folds $5k into AdjustmentsToIncome and re-runs tenforty:
      - AGI = $65,000 - $5,000 = $60,000
      - Standard deduction (single) = $15,750
      - Taxable income = $44,250
      - Fed tax (single TY2025 brackets): the filer has moved from the 22%
        bracket ($48,475 threshold) into the 12% bracket ($11,925–$48,475).
        tenforty produces $5,075 (vs $5,755 baseline). Delta = $680.

    This is EXACTLY why we chose Approach A: the marginal-rate
    approximation (22% * $5,000 = $1,100) would overstate the savings. The
    real delta is $680 because the deduction crosses a bracket boundary.
    """

    def _ret(self) -> CanonicalReturn:
        return CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(),
            address=_addr(),
            w2s=[
                W2(
                    employer_name="Diner",
                    box1_wages=Decimal("65000.00"),
                    box2_federal_income_tax_withheld=Decimal("7500.00"),
                    box7_social_security_tips=Decimal("5000.00"),
                )
            ],
            adjustments=AdjustmentsToIncome(
                qualified_tips_deduction_schedule_1a=Decimal("5000.00"),
            ),
        )

    def test_tips_deduction_remains_5000_after_patch(self):
        r = compute(self._ret())
        assert r.adjustments.qualified_tips_deduction_schedule_1a == Decimal("5000.00")
        assert r.adjustments.qualified_overtime_deduction_schedule_1a == Decimal("0")

    def test_agi_reduced_by_tips(self):
        r = compute(self._ret())
        assert r.computed.adjusted_gross_income == Decimal("60000.00")

    def test_taxable_income_after_standard_deduction(self):
        r = compute(self._ret())
        # $60,000 - $15,750 = $44,250
        assert r.computed.taxable_income == Decimal("44250.00")

    def test_fed_tax_uses_correct_bracket(self):
        r = compute(self._ret())
        # Single, $44,250 taxable → 12% bracket (not 22%). tenforty-locked.
        assert r.computed.tentative_tax == Decimal("5075.00")

    def test_delta_from_tips_crosses_bracket(self):
        """Approach A proof: the $5k tips deduction saves $680 of federal
        tax, not the $1,100 a marginal-rate approximation would give."""
        baseline = compute(_simple_single_return(Decimal("65000.00"), Decimal("7500.00")))
        with_tips = compute(self._ret())
        delta = baseline.computed.total_tax - with_tips.computed.total_tax
        assert delta == Decimal("680.00")
        # Sanity: a $1,100 savings would be 22% * $5,000. We are NOT that.
        assert delta < Decimal("1100.00")

    def test_adjustments_total_includes_tips(self):
        r = compute(self._ret())
        assert r.computed.adjustments_total == Decimal("5000.00")


# ---------------------------------------------------------------------------
# 4. Year gating: 2030 is outside the OBBBA window → no senior deduction.
# ---------------------------------------------------------------------------


class TestYearGatingNoOBBBA:
    """The OBBBA senior deduction patch has years_applicable = [2025..2028].
    For any year outside that window, even two age-65+ filers must receive
    zero senior deduction — the patch's internal year gate fires and the
    engine honors its zero output.

    We cannot use 2030 as the task spec originally suggested because
    tenforty only supports years 2018-2025 (enum-validated by its Pydantic
    model). We instead use 2024 — which IS inside tenforty's support window
    but BEFORE the OBBBA window — to exercise the same gate.
    """

    def test_year_gating_2024_no_obbba(self):
        taxpayer = _person("Alex", "Doe", "111-11-1111", dt.date(1957, 3, 3))
        spouse = _person("Sam", "Doe", "222-22-2222", dt.date(1957, 7, 7))
        ret = CanonicalReturn(
            tax_year=2024,
            filing_status=FilingStatus.MFJ,
            taxpayer=taxpayer,
            spouse=spouse,
            address=_addr(),
            w2s=[
                W2(
                    employer_name="Acme",
                    box1_wages=Decimal("180000.00"),
                )
            ],
        )
        r = compute(ret)
        # Even though the filers are 65+, the patch's year gate zeros out
        # the deduction.
        assert r.adjustments.senior_deduction_obbba == Decimal("0")
        assert r.computed.adjustments_total == Decimal("0.00")
        # AGI matches tenforty's raw computation — no OBBBA deduction
        # slipped in.
        assert r.computed.adjusted_gross_income == Decimal("180000.00")

    def test_year_gating_2030_skipped_tenforty_unsupported(self):
        """The task spec asked for year=2030 but tenforty's Pydantic
        validator only allows 2018-2025. Document the limitation and skip
        the 2030 test; 2024 covers the same year-gate logic above.
        """
        pytest.skip(
            "tenforty only supports years 2018-2025 per its Pydantic enum. "
            "The year-gate invariant is covered by test_year_gating_2024_no_obbba."
        )


# ---------------------------------------------------------------------------
# 5. Regression lock: simple_w2_standard bit-for-bit against expected.json.
# ---------------------------------------------------------------------------


def _load_golden(fixtures_dir: Path, name: str) -> tuple[CanonicalReturn, dict]:
    inp = json.loads((fixtures_dir / name / "input.json").read_text())
    exp = json.loads((fixtures_dir / name / "expected.json").read_text())
    return CanonicalReturn.model_validate(inp), exp


def _assert_golden_matches(computed, expected: dict) -> None:
    exp = expected["expected_computed_totals"]
    for field_name, expected_value in exp.items():
        actual = getattr(computed, field_name, None)
        if isinstance(expected_value, str):
            assert actual == Decimal(expected_value), (
                f"{field_name}: actual={actual!r} expected={expected_value!r}"
            )
        elif expected_value is None:
            assert actual is None, f"{field_name}: actual={actual!r} expected=None"
        else:
            assert actual == expected_value, (
                f"{field_name}: actual={actual!r} expected={expected_value!r}"
            )


class TestGoldenFixtureRegressionBitForBit:
    """Lock the wave-3 wiring against the pre-existing golden fixtures.

    Each fixture has no age 65+ filer AND no tips/overtime, so both gates
    skip and the second tenforty call is never issued. These tests fail
    LOUDLY if the wiring introduces any drift in the hot path.
    """

    def test_regression_simple_w2_standard_bit_for_bit(self, fixtures_dir: Path):
        ret, exp = _load_golden(fixtures_dir, "simple_w2_standard")
        result = compute(ret)
        _assert_golden_matches(result.computed, exp)

    def test_regression_w2_investments_itemized_bit_for_bit(self, fixtures_dir: Path):
        ret, exp = _load_golden(fixtures_dir, "w2_investments_itemized")
        result = compute(ret)
        _assert_golden_matches(result.computed, exp)

    def test_regression_se_home_office_bit_for_bit(self, fixtures_dir: Path):
        ret, exp = _load_golden(fixtures_dir, "se_home_office")
        result = compute(ret)
        _assert_golden_matches(result.computed, exp)


# ---------------------------------------------------------------------------
# 6. Form 4547 Trump Account — AGI leak regression.
# ---------------------------------------------------------------------------


class TestForm4547TrumpAccountAGILeak:
    """Lock the wave-3 Form 4547 fix: IRC §219 disallows any individual
    deduction for Trump Account contributions. The canonical model still
    carries `AdjustmentsToIncome.trump_account_deduction_form_4547` for
    schema stability, but `compute()` must never let a caller-supplied
    nonzero value reduce AGI, and must force the field to $0 on the
    returned canonical model.

    Without the fix:
      - `_sum_adjustments` folded the field into the Schedule 1 Part II
        total, so tenforty saw the reduced AGI and bracket-calculated the
        wrong tax.
      - The returned CanonicalReturn exposed the caller's leaked value.

    With the fix:
      - `_sum_adjustments` excludes the field entirely.
      - `compute()` runs `compute_trump_account_deduction` (audit-only,
        always returns $0) and force-zeros the field on the returned
        adjustments object so downstream consumers see the invariant.
    """

    def _leaked_return(self) -> CanonicalReturn:
        return CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(),
            address=_addr(),
            w2s=[
                W2(
                    employer_name="Acme",
                    box1_wages=Decimal("65000.00"),
                    box2_federal_income_tax_withheld=Decimal("7500.00"),
                )
            ],
            # A misbehaving caller populates the field. Pre-wave-3 this
            # would silently reduce AGI by $1,000 and bracket-shift the
            # tax owed.
            adjustments=AdjustmentsToIncome(
                trump_account_deduction_form_4547=Decimal("1000.00")
            ),
        )

    def test_leaked_value_does_not_reduce_agi(self):
        """AGI must match the simple_w2_standard baseline exactly,
        ignoring the leaked $1,000."""
        r = compute(self._leaked_return())
        assert r.computed.adjusted_gross_income == Decimal("65000.00")
        assert r.computed.taxable_income == Decimal("49250.00")
        assert r.computed.tentative_tax == Decimal("5755.00")
        assert r.computed.total_tax == Decimal("5755.00")
        assert r.computed.refund == Decimal("1745.00")

    def test_returned_adjustments_field_is_forced_to_zero(self):
        """The canonical return produced by compute() must expose the
        §219 invariant: trump_account_deduction_form_4547 is $0."""
        r = compute(self._leaked_return())
        assert r.adjustments.trump_account_deduction_form_4547 == Decimal("0")

    def test_adjustments_total_excludes_trump_account(self):
        """`_sum_adjustments` must not include the field. With no other
        Schedule 1 Part II items populated, the total is $0."""
        r = compute(self._leaked_return())
        assert r.computed.adjustments_total == Decimal("0.00")

    def test_clean_return_also_zero(self):
        """A return that never touched the field should also come back
        with trump_account_deduction_form_4547 = $0 (idempotent)."""
        clean = _simple_single_return(
            wages=Decimal("65000.00"), withheld=Decimal("7500.00")
        )
        r = compute(clean)
        assert r.adjustments.trump_account_deduction_form_4547 == Decimal("0")
        assert r.computed.adjusted_gross_income == Decimal("65000.00")
