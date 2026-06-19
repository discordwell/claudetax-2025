"""OBBBA Schedule 1-A deductions are below the line; age-65/blind additional.

Regression lock for the fix that moved the OBBBA senior / tips / overtime
deductions OFF Schedule 1 (where they wrongly reduced AGI) and onto Form 1040
line 13b (below the AGI line, reducing taxable income only), and that applies
the long-standing §63(f) age-65/blind additional standard deduction that
tenforty omits because it has no age input.

Why this matters: AGI/MAGI drive the NIIT threshold, the 7.5% medical-expense
floor, every MAGI phase-out, and every state return that conforms to federal
AGI. Reducing AGI by a below-the-line deduction silently corrupts all of them.

Authority:
- IRS 2025 Schedule 1-A (Form 1040): senior + tips + overtime + car-loan
  interest total flows to Form 1040 line 13b.
- IRS 2025 Form 1040: line 14 = line 12 + line 13a (QBI) + line 13b;
  line 15 (taxable income) = line 11 (AGI) - line 14.
- IRC §63(f): additional standard deduction for age 65+/blind, TY2025
  $2,000 per box (single/HoH), $1,600 per box (MFJ/MFS/QSS).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from skill.scripts.calc.engine import compute
from skill.scripts.models import (
    Address,
    AdjustmentsToIncome,
    CanonicalReturn,
    FilingStatus,
    Form1099INT,
    ItemizedDeductions,
    Person,
    W2,
)


def _addr() -> Address:
    return Address(street1="1 Test Lane", city="Springfield", state="IL", zip="62701")


def _person(
    first: str = "Alex",
    ssn: str = "111-22-3333",
    dob: dt.date = dt.date(1985, 1, 1),
    blind: bool = False,
) -> Person:
    return Person(
        first_name=first, last_name="Doe", ssn=ssn, date_of_birth=dob, is_blind=blind
    )


_SENIOR_DOB = dt.date(1955, 1, 1)  # age 70 at end of 2025


def _single_senior_std(wages: Decimal) -> CanonicalReturn:
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=_person(dob=_SENIOR_DOB),
        address=_addr(),
        w2s=[W2(employer_name="Acme", box1_wages=wages)],
    )


# ---------------------------------------------------------------------------
# 1. AGI is never reduced by the OBBBA Schedule 1-A deductions.
# ---------------------------------------------------------------------------


class TestAGIUnchangedByOBBBADeductions:
    def test_senior_deduction_does_not_touch_agi(self):
        r = compute(_single_senior_std(Decimal("80000")))
        # Senior deduction ($6,000 base, phase-out 6% * (80k - 75k) = $300 ->
        # $5,700) lands on line 13b, not in AGI.
        assert r.computed.adjusted_gross_income == Decimal("80000.00")
        assert r.computed.additional_deductions_schedule_1a == Decimal("5700.00")
        assert r.computed.adjustments_total == Decimal("0.00")

    def test_tips_overtime_do_not_touch_agi(self):
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(),
            address=_addr(),
            w2s=[W2(employer_name="Diner", box1_wages=Decimal("45000"))],
            adjustments=AdjustmentsToIncome(
                qualified_tips_deduction_schedule_1a=Decimal("3000"),
                qualified_overtime_deduction_schedule_1a=Decimal("1500"),
            ),
        )
        r = compute(ret)
        assert r.computed.adjusted_gross_income == Decimal("45000.00")
        assert r.computed.additional_deductions_schedule_1a == Decimal("4500.00")
        assert r.computed.adjustments_total == Decimal("0.00")

    def test_idempotent_under_recompute(self):
        """compute() twice on the same return yields identical totals — the
        OBBBA fields are overwritten, never accumulated."""
        ret = _single_senior_std(Decimal("80000"))
        first = compute(ret)
        second = compute(first)
        assert (
            second.computed.adjusted_gross_income
            == first.computed.adjusted_gross_income
            == Decimal("80000.00")
        )
        assert (
            second.computed.additional_deductions_schedule_1a
            == first.computed.additional_deductions_schedule_1a
        )
        assert second.computed.total_tax == first.computed.total_tax


# ---------------------------------------------------------------------------
# 2. Cascade: the corrected (higher) AGI now drives NIIT and the medical floor.
#    These are the bugs that the AGI-reducing placement silently produced.
# ---------------------------------------------------------------------------


class TestAGICascades:
    def test_niit_threshold_uses_unreduced_agi(self):
        """MFJ both 65+, $200k wages + $55k interest. True MAGI = $255,000,
        $5,000 over the $250k NIIT threshold -> NIIT = 3.8% * $5,000 = $190.
        The old AGI-reducing senior deduction pushed MAGI to $249,300 and
        zeroed NIIT."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.MFJ,
            taxpayer=_person("Al", "111-11-1111", _SENIOR_DOB),
            spouse=_person("Sam", "222-22-2222", _SENIOR_DOB),
            address=_addr(),
            w2s=[W2(employer_name="Acme", box1_wages=Decimal("200000"))],
            forms_1099_int=[
                Form1099INT(payer_name="Bank", box1_interest_income=Decimal("55000"))
            ],
        )
        r = compute(ret)
        assert r.computed.adjusted_gross_income == Decimal("255000.00")
        # NIIT correctly applies on $5,000 of net investment income over the
        # MFJ threshold (per-field value; the engine's authoritative Form 8960).
        assert r.other_taxes.net_investment_income_tax == Decimal("190.00")

    def test_medical_floor_uses_unreduced_agi(self):
        """Single age 70 itemizing: $100k wages, $20k medical, $5k SALT, $8k
        mortgage. The 7.5% medical floor must use the TRUE AGI ($100k -> floor
        $7,500), not an AGI reduced by the senior deduction."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(dob=_SENIOR_DOB),
            address=_addr(),
            w2s=[W2(employer_name="Acme", box1_wages=Decimal("100000"))],
            itemize_deductions=True,
            itemized=ItemizedDeductions(
                medical_and_dental_total=Decimal("20000"),
                state_and_local_income_tax=Decimal("5000"),
                home_mortgage_interest=Decimal("8000"),
            ),
        )
        r = compute(ret)
        assert r.computed.adjusted_gross_income == Decimal("100000.00")
        # Itemized (line 12) = (20000 - 7500 floor) + 5000 + 8000 = 25500.
        assert r.computed.deduction_taken == Decimal("25500.00")
        # Senior deduction ($4,500 after phase-out) on line 13b. Itemizers get
        # NO age-65 additional standard deduction.
        assert r.computed.additional_deductions_schedule_1a == Decimal("4500.00")
        # Taxable income = 100000 - 25500 - 4500 = 70000.
        assert r.computed.taxable_income == Decimal("70000.00")


# ---------------------------------------------------------------------------
# 3. §63(f) age-65/blind additional standard deduction (standard filers only).
# ---------------------------------------------------------------------------


class TestAge65BlindAdditionalStandardDeduction:
    def test_single_senior_gets_2000(self):
        # $15,750 base + $2,000 (single/HoH, one box) = $17,750.
        r = compute(_single_senior_std(Decimal("50000")))
        assert r.computed.deduction_taken == Decimal("17750.00")

    def test_single_blind_nonsenior_gets_2000(self):
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(blind=True),  # age 40, blind
            address=_addr(),
            w2s=[W2(employer_name="Acme", box1_wages=Decimal("50000"))],
        )
        r = compute(ret)
        assert r.computed.deduction_taken == Decimal("17750.00")

    def test_single_senior_and_blind_gets_two_boxes(self):
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(dob=_SENIOR_DOB, blind=True),
            address=_addr(),
            w2s=[W2(employer_name="Acme", box1_wages=Decimal("50000"))],
        )
        r = compute(ret)
        # $15,750 base + 2 * $2,000 = $19,750.
        assert r.computed.deduction_taken == Decimal("19750.00")

    def test_mfj_one_senior_gets_1600(self):
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.MFJ,
            taxpayer=_person("Al", "111-11-1111", _SENIOR_DOB),
            spouse=_person("Sam", "222-22-2222", dt.date(1985, 1, 1)),
            address=_addr(),
            w2s=[W2(employer_name="Acme", box1_wages=Decimal("60000"))],
        )
        r = compute(ret)
        # $31,500 base MFJ + $1,600 (one box) = $33,100.
        assert r.computed.deduction_taken == Decimal("33100.00")

    def test_itemizing_senior_gets_no_additional(self):
        """The age-65/blind additional is part of the STANDARD deduction;
        itemizers do not get it."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(dob=_SENIOR_DOB),
            address=_addr(),
            w2s=[W2(employer_name="Acme", box1_wages=Decimal("50000"))],
            itemize_deductions=True,
            itemized=ItemizedDeductions(
                state_and_local_income_tax=Decimal("8000"),
                home_mortgage_interest=Decimal("12000"),
            ),
        )
        r = compute(ret)
        assert r.computed.deduction_taken == Decimal("20000.00")  # itemized only

    def test_young_filer_gets_no_additional(self):
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(),  # age 40, not blind
            address=_addr(),
            w2s=[W2(employer_name="Acme", box1_wages=Decimal("50000"))],
        )
        r = compute(ret)
        assert r.computed.deduction_taken == Decimal("15750.00")  # base only


# ---------------------------------------------------------------------------
# 4. Form 1040 reconciliation: line 15 = AGI - (line 12 + 13a + 13b).
# ---------------------------------------------------------------------------


class TestForm1040Reconciliation:
    @pytest.mark.parametrize(
        "ret_factory",
        [
            lambda: _single_senior_std(Decimal("80000")),
            lambda: CanonicalReturn(
                tax_year=2025,
                filing_status=FilingStatus.SINGLE,
                taxpayer=_person(),
                address=_addr(),
                w2s=[W2(employer_name="Diner", box1_wages=Decimal("45000"))],
                adjustments=AdjustmentsToIncome(
                    qualified_tips_deduction_schedule_1a=Decimal("3000"),
                ),
            ),
        ],
    )
    def test_taxable_income_reconciles(self, ret_factory):
        c = compute(ret_factory()).computed
        line_12 = c.deduction_taken or Decimal("0")
        line_13a = c.qbi_deduction or Decimal("0")
        line_13b = c.additional_deductions_schedule_1a or Decimal("0")
        assert c.adjusted_gross_income - (line_12 + line_13a + line_13b) == (
            c.taxable_income
        )


# ---------------------------------------------------------------------------
# 5. Bit-for-bit: a plain return with no senior/OBBBA/QBI is unchanged.
# ---------------------------------------------------------------------------


def test_plain_return_takes_hot_path_unchanged():
    """A young single filer, standard deduction, no tips/senior/QBI: the
    deduction flow must be identical to the pre-fix single-pass behavior."""
    ret = CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=_person(),
        address=_addr(),
        w2s=[
            W2(
                employer_name="Acme",
                box1_wages=Decimal("65000"),
                box2_federal_income_tax_withheld=Decimal("7500"),
            )
        ],
    )
    c = compute(ret).computed
    assert c.adjusted_gross_income == Decimal("65000.00")
    assert c.deduction_taken == Decimal("15750.00")
    assert c.additional_deductions_schedule_1a is None
    assert c.taxable_income == Decimal("49250.00")
    assert c.total_tax == Decimal("5755.00")
    assert c.refund == Decimal("1745.00")
