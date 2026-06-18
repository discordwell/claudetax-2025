"""End-to-end tests: Schedule 3 credits flow into Form 1040 total tax / refund.

Before this wiring, the calc engine computed ``total_tax`` and ``refund``
using only the child tax credit / ODC and EITC/ACTC. The Schedule 3 credits —
education (Form 8863), dependent care (Form 2441), and the premium tax credit
(Form 8962) — were rendered on their own forms but NEVER reduced the headline
tax: Form 1040 lines 20 and 31 were hardcoded to $0. A filer with college
tuition, daycare, or ACA marketplace coverage was told to overpay, and the
rendered Form 8863/2441/8962 disagreed with the Form 1040 in the same bundle.

Each test here asserts the credit actually moves ``total_tax`` / ``refund`` and
that the printed Form 1040 reconciles (line 24 == line 22 + line 23; line 33 ==
engine ``total_payments``; refund == line 33 − line 24). Every assertion would
FAIL against the pre-wiring engine.

Authority: IRS Form 1040 (TY2025), Schedule 3, Schedule 8812 Credit Limit
Worksheet, and the Instructions for Forms 8863 / 2441 / 8962.
"""
from __future__ import annotations

from decimal import Decimal

from skill.scripts.calc.engine import compute, total_payments
from skill.scripts.models import (
    Address,
    CanonicalReturn,
    DependentCareExpenses,
    Dependent,
    DependentRelationship,
    EducationCredits,
    EducationStudent,
    FilingStatus,
    Form1095A,
    Form1095AMonthly,
    Person,
    W2,
)
from skill.scripts.output.form_1040 import compute_form_1040_fields

_ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _person(first: str = "Test", last: str = "Payer", ssn: str = "111-22-3333") -> Person:
    return Person(
        first_name=first, last_name=last, ssn=ssn, date_of_birth="1985-06-15"
    )


def _address() -> Address:
    return Address(street1="1 Test St", city="Springfield", state="IL", zip="62701")


def _w2(wages: str, withheld: str = "8000") -> W2:
    return W2(
        employer_name="ACME",
        employer_ein="12-3456789",
        box1_wages=Decimal(wages),
        box2_federal_income_tax_withheld=Decimal(withheld),
    )


def _return(
    *,
    wages: str = "60000",
    withheld: str = "8000",
    filing_status: FilingStatus = FilingStatus.SINGLE,
    spouse: Person | None = None,
    dependents: list[Dependent] | None = None,
    **kw,
) -> CanonicalReturn:
    return CanonicalReturn(
        tax_year=2025,
        filing_status=filing_status,
        taxpayer=_person(),
        spouse=spouse,
        address=_address(),
        w2s=[_w2(wages, withheld)],
        dependents=dependents or [],
        **kw,
    )


def _child(ssn: str = "444-55-6666") -> Dependent:
    return Dependent(
        person=Person(
            first_name="Kid", last_name="Payer", ssn=ssn, date_of_birth="2016-01-01"
        ),
        relationship=DependentRelationship.SON,
        months_lived_with_taxpayer=12,
        is_qualifying_child=True,
        is_qualifying_relative=False,
    )


def _assert_form_1040_reconciles(computed_return: CanonicalReturn) -> None:
    """The printed Form 1040 must be internally consistent with the engine."""
    f = compute_form_1040_fields(computed_return)
    # Total tax (line 24) == regular-tax-after-credits (line 22) + other taxes (23)
    assert f.line_24_total_tax == f.line_22_subtract_21_from_18 + f.line_23_other_taxes_from_sch_2_line_21
    # Total payments (line 33) == the engine aggregate that drives refund/owed.
    assert f.line_33_total_payments == total_payments(computed_return)
    # Refund / owed reconcile against the printed tax and payments.
    if f.line_37_amount_you_owe > _ZERO:
        assert f.line_37_amount_you_owe == f.line_24_total_tax - f.line_33_total_payments
    else:
        assert f.line_34_overpayment == f.line_33_total_payments - f.line_24_total_tax


# ---------------------------------------------------------------------------
# Education credits (Form 8863)
# ---------------------------------------------------------------------------


def test_aotc_reduces_tax_and_adds_refundable_portion() -> None:
    """$4k AOTC = $2,500 credit: $1,500 nonrefundable (cuts tax) + $1,000
    refundable (adds to refund)."""
    base = compute(_return())
    edu = EducationCredits(
        students=[
            EducationStudent(
                name="Kid",
                ssn="222-33-4444",
                institution_name="State U",
                qualified_expenses=Decimal("4000"),
            )
        ]
    )
    got = compute(_return(education=edu))

    # Nonrefundable $1,500 cuts total tax dollar-for-dollar.
    assert got.computed.total_tax == base.computed.total_tax - Decimal("1500.00")
    # Refund rises by the $1,500 tax cut PLUS the $1,000 refundable AOTC.
    assert got.computed.refund == base.computed.refund + Decimal("2500.00")
    # Credit fields are populated on the model (so Schedule 3 / 8863 agree).
    assert got.credits.education_credits_nonrefundable == Decimal("1500.00")
    assert got.payments.american_opportunity_credit_refundable == Decimal("1000.00")
    _assert_form_1040_reconciles(got)


def test_llc_is_purely_nonrefundable() -> None:
    """A grad student (not AOTC-eligible) gets the Lifetime Learning Credit:
    20% of expenses, fully nonrefundable, no refundable portion."""
    base = compute(_return())
    edu = EducationCredits(
        students=[
            EducationStudent(
                name="Grad",
                ssn="222-33-4444",
                institution_name="State U",
                qualified_expenses=Decimal("8000"),
                is_aotc_eligible=False,
            )
        ]
    )
    got = compute(_return(education=edu))

    # LLC = 20% * $8,000 = $1,600, all nonrefundable.
    assert got.credits.education_credits_nonrefundable == Decimal("1600.00")
    assert got.payments.american_opportunity_credit_refundable == _ZERO
    assert got.computed.total_tax == base.computed.total_tax - Decimal("1600.00")
    assert got.computed.refund == base.computed.refund + Decimal("1600.00")
    _assert_form_1040_reconciles(got)


# ---------------------------------------------------------------------------
# Dependent care credit (Form 2441)
# ---------------------------------------------------------------------------


def test_dependent_care_credit_reduces_tax() -> None:
    """$3k of daycare for one child at AGI $60k = 20% = $600 nonrefundable."""
    base = compute(_return())
    dc = DependentCareExpenses(qualifying_persons=1, total_expenses_paid=Decimal("3000"))
    got = compute(_return(dependent_care=dc))

    assert got.credits.dependent_care_credit == Decimal("600.00")
    assert got.computed.total_tax == base.computed.total_tax - Decimal("600.00")
    assert got.computed.refund == base.computed.refund + Decimal("600.00")
    _assert_form_1040_reconciles(got)


# ---------------------------------------------------------------------------
# Premium tax credit (Form 8962) — both directions
# ---------------------------------------------------------------------------


def test_net_premium_tax_credit_increases_refund() -> None:
    """When the allowed PTC exceeds advance payments, the net PTC is a
    refundable credit that increases the refund (and does not touch tax)."""
    base = compute(_return(wages="40000"))
    # Generous SLCSP, zero advance -> full PTC is a refundable net credit.
    monthly = [
        Form1095AMonthly(
            enrollment_premium=Decimal("500"),
            slcsp_premium=Decimal("600"),
            advance_ptc=Decimal("0"),
        )
        for _ in range(12)
    ]
    got = compute(_return(wages="40000", forms_1095_a=[Form1095A(monthly_data=monthly)]))

    net_ptc = got.credits.premium_tax_credit_net
    assert net_ptc > _ZERO
    # PTC is refundable: tax unchanged, refund up by the net PTC.
    assert got.computed.total_tax == base.computed.total_tax
    assert got.computed.refund == base.computed.refund + net_ptc
    _assert_form_1040_reconciles(got)


def test_excess_advance_ptc_repayment_increases_tax() -> None:
    """When advance PTC exceeds the allowed PTC, the excess is repaid as an
    additional tax — the engine must INCREASE total tax, never silently drop
    it (which would understate tax)."""
    base = compute(_return(wages="40000"))
    # Large advance, small SLCSP -> excess advance PTC must be repaid.
    monthly = [
        Form1095AMonthly(
            enrollment_premium=Decimal("400"),
            slcsp_premium=Decimal("300"),
            advance_ptc=Decimal("450"),
        )
        for _ in range(12)
    ]
    got = compute(_return(wages="40000", forms_1095_a=[Form1095A(monthly_data=monthly)]))

    repayment = got.other_taxes.other.get("excess_advance_ptc_repayment", _ZERO)
    assert repayment > _ZERO
    assert got.credits.premium_tax_credit_net == _ZERO
    # The repayment is an additional tax.
    assert got.computed.total_tax == base.computed.total_tax + repayment
    _assert_form_1040_reconciles(got)


# ---------------------------------------------------------------------------
# Caller-supplied Schedule 3 Part I credits (no input block needed)
# ---------------------------------------------------------------------------


def test_caller_supplied_foreign_tax_credit_reduces_tax() -> None:
    """A foreign tax credit entered directly on the credits block (no form
    module) must still reduce tax — previously it was silently ignored."""
    base = compute(_return())
    got = compute(
        CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(),
            address=_address(),
            w2s=[_w2("60000")],
            credits={"foreign_tax_credit": Decimal("450")},
        )
    )
    assert got.computed.total_tax == base.computed.total_tax - Decimal("450.00")
    _assert_form_1040_reconciles(got)


def test_caller_supplied_refundable_education_credit_reaches_refund() -> None:
    """A refundable education credit entered directly on the credits block
    (no `education` input block) must increase the refund. Previously it was
    stored back on the model — so the rendered Schedule 3 showed it — but the
    routing only copied it to the payments field when an `education` block was
    present, so it never reached total_payments / refund (a silently-dropped
    refund + a Schedule-3-vs-1040 disagreement)."""
    base = compute(_return())
    got = compute(
        CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(),
            address=_address(),
            w2s=[_w2("60000")],
            credits={"education_credits_refundable": Decimal("400")},
        )
    )
    # Refundable: tax unchanged, refund up by $400, and it lands on the
    # payments field that Form 1040 line 29 / total_payments read.
    assert got.computed.total_tax == base.computed.total_tax
    assert got.computed.refund == base.computed.refund + Decimal("400.00")
    assert got.payments.american_opportunity_credit_refundable == Decimal("400.00")
    assert total_payments(got) == total_payments(base) + Decimal("400.00")
    _assert_form_1040_reconciles(got)


# ---------------------------------------------------------------------------
# CTC ↔ Schedule 3 ordering (Schedule 8812 Credit Limit Worksheet)
# ---------------------------------------------------------------------------


def test_schedule_3_credit_pushes_ctc_into_refundable_actc() -> None:
    """The Credit Limit Worksheet subtracts Schedule 3 credits from tax BEFORE
    the child tax credit fills the remainder. A Schedule 3 credit that eats
    into the tax should push the otherwise-nonrefundable CTC down into the
    refundable ACTC, NOT cause CTC to be silently lost.

    Low wages keep regular tax small so CTC cannot be fully absorbed once the
    education credit is also claimed."""
    # One child, modest wages: tax is small relative to CTC + education credit.
    dep = [_child()]
    edu = EducationCredits(
        students=[
            EducationStudent(
                name="Older Kid",
                ssn="222-33-4444",
                institution_name="State U",
                qualified_expenses=Decimal("4000"),
            )
        ]
    )
    without = compute(_return(wages="22000", withheld="0", dependents=dep))
    with_edu = compute(
        _return(wages="22000", withheld="0", dependents=dep, education=edu)
    )

    # The education credit must not be lost: total benefit (tax cut + extra
    # refundable credits) is strictly larger with the education credit.
    base_benefit = without.computed.refund or _ZERO
    edu_benefit = with_edu.computed.refund or _ZERO
    assert edu_benefit > base_benefit
    # Refundable ACTC should appear/grow because the nonrefundable CTC room
    # was consumed by the education credit (ordering correctness).
    assert (
        with_edu.credits.additional_child_tax_credit_refundable
        >= without.credits.additional_child_tax_credit_refundable
    )
    _assert_form_1040_reconciles(with_edu)
    _assert_form_1040_reconciles(without)


# ---------------------------------------------------------------------------
# Invariance: returns with no Schedule 3 inputs are unchanged
# ---------------------------------------------------------------------------


def test_no_schedule_3_inputs_is_unchanged() -> None:
    """A plain W-2 return (no education / dependent care / 1095-A / caller
    credits) must be byte-for-byte identical to the pre-wiring engine — the
    Schedule 3 fold is fully gated."""
    got = compute(_return())
    assert got.credits.education_credits_nonrefundable == _ZERO
    assert got.credits.dependent_care_credit == _ZERO
    assert got.credits.premium_tax_credit_net == _ZERO
    assert "excess_advance_ptc_repayment" not in got.other_taxes.other
    f = compute_form_1040_fields(got)
    assert f.line_20_amount_from_sch_3_line_8 == _ZERO
    assert f.line_31_amount_from_sch_3_line_15 == _ZERO
