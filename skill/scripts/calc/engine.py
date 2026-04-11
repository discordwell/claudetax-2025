"""Calc engine — wrap + patch architecture.

Current implementation handles the CP7 golden-fixture scenarios: W-2 income
with standard deduction. Calc hot spots (QBI 8995-A, AMT, Schedule E
depreciation, CTC override, OBBBA senior deduction, Form 4547, Schedule 1-A,
multi-state apportionment) fan out to parallel sub-agents against this file.

Architecture (see skill/reference/cp4-tenforty-verification.md):

    compute(canonical_return) -> canonical_return with computed totals populated
        1. Marshal CanonicalReturn -> tenforty.evaluate_return kwargs
        2. Call tenforty (OBBBA-current for standard deduction, brackets, SE,
           LTCG, Additional Medicare Tax, California state)
        3. Apply patch layer for gaps: CTC, OBBBA senior deduction, Form 4547,
           Schedule 1-A, QBI 8995-A, NIIT verification
        4. Populate ComputedTotals and return

Fan-out rule: when you add a patch, write a golden fixture that exercises it.
No patch without a test.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import tenforty

from skill.scripts.models import (
    CanonicalReturn,
    ComputedTotals,
    FilingStatus,
)

# Mapping from our FilingStatus enum to tenforty's string values
_TENFORTY_STATUS: dict[FilingStatus, str] = {
    FilingStatus.SINGLE: "Single",
    FilingStatus.MFJ: "Married/Joint",
    FilingStatus.MFS: "Married/Sep",
    FilingStatus.HOH: "Head_of_House",
    FilingStatus.QSS: "Widow(er)",
}


@dataclass(frozen=True)
class TenfortyInput:
    """The subset of tenforty.evaluate_return kwargs we populate from a canonical return."""

    year: int
    filing_status: str
    w2_income: float
    taxable_interest: float
    qualified_dividends: float
    ordinary_dividends: float
    short_term_capital_gains: float
    long_term_capital_gains: float
    self_employment_income: float
    rental_income: float
    standard_or_itemized: str
    itemized_deductions: float
    num_dependents: int


def _to_tenforty_input(return_: CanonicalReturn) -> TenfortyInput:
    """Marshal a canonical return into tenforty's high-level input fields."""
    w2_sum = sum((w2.box1_wages for w2 in return_.w2s), start=Decimal("0"))
    interest_sum = sum(
        (f.box1_interest_income for f in return_.forms_1099_int), start=Decimal("0")
    )
    ord_div_sum = sum(
        (f.box1a_ordinary_dividends for f in return_.forms_1099_div), start=Decimal("0")
    )
    qual_div_sum = sum(
        (f.box1b_qualified_dividends for f in return_.forms_1099_div), start=Decimal("0")
    )
    # Capital gain distributions from 1099-DIV box 2a are long-term by definition
    cap_gain_distr_sum = sum(
        (f.box2a_total_capital_gain_distributions for f in return_.forms_1099_div),
        start=Decimal("0"),
    )
    st_1099b = Decimal("0")
    lt_1099b = Decimal("0")
    for form in return_.forms_1099_b:
        for txn in form.transactions:
            gain = txn.proceeds - txn.cost_basis + txn.adjustment_amount
            if txn.is_long_term:
                lt_1099b += gain
            else:
                st_1099b += gain

    se_income = sum(
        (
            (sc.line1_gross_receipts - sc.line2_returns_and_allowances - sc.line4_cost_of_goods_sold)
            for sc in return_.schedules_c
        ),
        start=Decimal("0"),
    )
    rental_income = sum(
        (
            sum((p.rents_received - _sch_e_expenses(p) for p in sched.properties), start=Decimal("0"))
            for sched in return_.schedules_e
        ),
        start=Decimal("0"),
    )

    itemized_total = Decimal("0")
    if return_.itemize_deductions and return_.itemized is not None:
        it = return_.itemized
        itemized_total = (
            it.medical_and_dental_total
            + it.state_and_local_income_tax
            + it.real_estate_tax
            + it.home_mortgage_interest
            + it.gifts_to_charity_cash
            + it.gifts_to_charity_other_than_cash
        )

    return TenfortyInput(
        year=return_.tax_year,
        filing_status=_TENFORTY_STATUS[return_.filing_status],
        w2_income=float(w2_sum),
        taxable_interest=float(interest_sum),
        qualified_dividends=float(qual_div_sum),
        ordinary_dividends=float(ord_div_sum),
        short_term_capital_gains=float(st_1099b),
        long_term_capital_gains=float(lt_1099b + cap_gain_distr_sum),
        self_employment_income=float(se_income),
        rental_income=float(rental_income),
        standard_or_itemized="Itemized" if return_.itemize_deductions else "Standard",
        itemized_deductions=float(itemized_total),
        num_dependents=len(return_.dependents),
    )


def _sch_e_expenses(p) -> Decimal:
    """Sum all Schedule E expense lines for a property."""
    return (
        p.advertising
        + p.auto_and_travel
        + p.cleaning_and_maintenance
        + p.commissions
        + p.insurance
        + p.legal_and_professional
        + p.management_fees
        + p.mortgage_interest_to_banks
        + p.other_interest
        + p.repairs
        + p.supplies
        + p.taxes
        + p.utilities
        + p.depreciation
    )


def compute(return_: CanonicalReturn) -> CanonicalReturn:
    """Compute a canonical return end-to-end.

    This is the entry point for the calc engine. Returns a new CanonicalReturn
    with computed totals populated.
    """
    tf_input = _to_tenforty_input(return_)
    tf_result = tenforty.evaluate_return(
        year=tf_input.year,
        filing_status=tf_input.filing_status,
        w2_income=tf_input.w2_income,
        taxable_interest=tf_input.taxable_interest,
        qualified_dividends=tf_input.qualified_dividends,
        ordinary_dividends=tf_input.ordinary_dividends,
        short_term_capital_gains=tf_input.short_term_capital_gains,
        long_term_capital_gains=tf_input.long_term_capital_gains,
        self_employment_income=tf_input.self_employment_income,
        rental_income=tf_input.rental_income,
        standard_or_itemized=tf_input.standard_or_itemized,
        itemized_deductions=tf_input.itemized_deductions,
        num_dependents=tf_input.num_dependents,
    )

    # Copy output into ComputedTotals. tenforty returns floats; round to cents.
    def _cents(v) -> Decimal | None:
        if v is None:
            return None
        return Decimal(str(round(float(v), 2)))

    total_payments = sum(
        (w2.box2_federal_income_tax_withheld for w2 in return_.w2s), start=Decimal("0")
    ) + return_.payments.estimated_tax_payments_2025 + return_.payments.prior_year_overpayment_applied

    agi = _cents(tf_result.federal_adjusted_gross_income)
    ti = _cents(tf_result.federal_taxable_income)
    fed_tax = _cents(tf_result.federal_income_tax)
    total_tax = _cents(tf_result.federal_total_tax)
    deduction = (agi - ti) if (agi is not None and ti is not None) else None

    refund = None
    owed = None
    if total_tax is not None:
        diff = total_payments - total_tax
        if diff > 0:
            refund = diff
        elif diff < 0:
            owed = -diff
        else:
            refund = Decimal("0")

    updated = return_.model_copy(
        update={
            "computed": ComputedTotals(
                total_income=_cents(tf_input.w2_income + tf_input.taxable_interest
                                    + tf_input.ordinary_dividends + tf_input.short_term_capital_gains
                                    + tf_input.long_term_capital_gains + tf_input.self_employment_income
                                    + tf_input.rental_income),
                adjustments_total=(agi - _cents(tf_input.w2_income + tf_input.taxable_interest
                                    + tf_input.ordinary_dividends + tf_input.short_term_capital_gains
                                    + tf_input.long_term_capital_gains + tf_input.self_employment_income
                                    + tf_input.rental_income)) if agi is not None else None,
                adjusted_gross_income=agi,
                deduction_taken=deduction,
                taxable_income=ti,
                tentative_tax=fed_tax,
                total_credits_nonrefundable=None,
                other_taxes_total=_cents((float(total_tax or 0) - float(fed_tax or 0))),
                total_tax=total_tax,
                total_payments=total_payments,
                refund=refund,
                amount_owed=owed,
                effective_rate=float(tf_result.federal_effective_tax_rate) if tf_result.federal_effective_tax_rate is not None else None,
                marginal_rate=float(tf_result.federal_tax_bracket) if tf_result.federal_tax_bracket is not None else None,
            )
        }
    )
    return updated
