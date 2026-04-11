"""Calc engine — wrap + patch architecture.

This module marshals a CanonicalReturn into tenforty's evaluate_return and
unpacks the result into ComputedTotals. It addresses the calc hot spots that
are load-bearing for every downstream golden fixture and state plugin.

Architecture (see skill/reference/cp4-tenforty-verification.md):

    compute(canonical_return) -> canonical_return with computed totals populated
        1. Marshal CanonicalReturn -> tenforty.evaluate_return kwargs, applying:
             - Schedule C net profit (gross - ALL expenses - home office)
             - Schedule E net rental income (rents + royalties - expenses)
             - Itemized total with SALT cap ($10k / $5k MFS)
             - Schedule 1 net (Part I additions - Part II adjustments)
        2. Call tenforty (OBBBA-current for standard deduction, brackets, SE,
           LTCG, Additional Medicare Tax, California state)
        3. Apply patch layer for gaps: CTC, OBBBA senior deduction, Form 4547,
           Schedule 1-A, QBI 8995-A, NIIT verification
        4. Populate ComputedTotals and return

Fan-out rule: when you add a patch, write a golden fixture that exercises it.
No patch without a test. No skill change without a test.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import tenforty

from skill.scripts.models import (
    AdjustmentsToIncome,
    CanonicalReturn,
    ComputedTotals,
    Credits,
    FilingStatus,
    ItemizedDeductions,
    OtherTaxes,
    Payments,
    ScheduleC,
    ScheduleCExpenses,
    ScheduleE,
    ScheduleEProperty,
    W2,
)


# Tenforty's filing-status strings (verified in CP4)
_TENFORTY_STATUS: dict[FilingStatus, str] = {
    FilingStatus.SINGLE: "Single",
    FilingStatus.MFJ: "Married/Joint",
    FilingStatus.MFS: "Married/Sep",
    FilingStatus.HOH: "Head_of_House",
    FilingStatus.QSS: "Widow(er)",
}

# SALT cap (TCJA, made permanent by OBBBA) — $10,000 MFJ/S/HoH/QSS, $5,000 MFS
SALT_CAP_NORMAL = Decimal("10000")
SALT_CAP_MFS = Decimal("5000")

_CENTS = Decimal("0.01")


def _d(v: Any) -> Decimal:
    """Coerce to Decimal deterministically."""
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _cents(v: Any) -> Decimal | None:
    """Round to 2 decimal places, preserving None."""
    if v is None:
        return None
    return _d(v).quantize(_CENTS, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Schedule C — net profit per business
# ---------------------------------------------------------------------------


def _sch_c_total_expenses(expenses: ScheduleCExpenses) -> Decimal:
    """Sum every Schedule C Part II expense line plus other_expense_detail."""
    fixed_sum = (
        expenses.line8_advertising
        + expenses.line9_car_and_truck
        + expenses.line10_commissions_and_fees
        + expenses.line11_contract_labor
        + expenses.line12_depletion
        + expenses.line13_depreciation
        + expenses.line14_employee_benefit_programs
        + expenses.line15_insurance_not_health
        + expenses.line16a_mortgage_interest
        + expenses.line16b_other_interest
        + expenses.line17_legal_and_professional
        + expenses.line18_office_expense
        + expenses.line19_pension_and_profit_sharing
        + expenses.line20a_rent_vehicles_machinery_equipment
        + expenses.line20b_rent_other_business_property
        + expenses.line21_repairs_and_maintenance
        + expenses.line22_supplies
        + expenses.line23_taxes_and_licenses
        + expenses.line24a_travel
        + expenses.line24b_meals_50pct_deductible
        + expenses.line25_utilities
        + expenses.line26_wages
        + expenses.line27a_other_expenses
    )
    other_sum = sum(expenses.other_expense_detail.values(), start=Decimal("0"))
    return fixed_sum + other_sum


def schedule_c_net_profit(sc: ScheduleC) -> Decimal:
    """Compute a single Schedule C's net profit (Line 31).

    Line 7 = gross income = gross_receipts - returns_and_allowances - COGS + other_income
    Line 28 = total expenses
    Line 29 = tentative profit = line 7 - line 28
    Line 30 = home office expense (from Form 8829)
    Line 31 = net profit = line 29 - line 30
    """
    gross_income = (
        sc.line1_gross_receipts
        - sc.line2_returns_and_allowances
        - sc.line4_cost_of_goods_sold
        + sc.line6_other_income
    )
    total_expenses = _sch_c_total_expenses(sc.expenses)
    tentative_profit = gross_income - total_expenses
    return tentative_profit - sc.line30_home_office_expense


# ---------------------------------------------------------------------------
# Schedule E — net rental income per property
# ---------------------------------------------------------------------------


def schedule_e_property_net(p: ScheduleEProperty) -> Decimal:
    """Compute a single Schedule E property's net rental income."""
    gross = p.rents_received + p.royalties_received
    expenses = (
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
    other_exp = sum(p.other_expenses.values(), start=Decimal("0"))
    return gross - expenses - other_exp


def schedule_e_total_net(sched_e: ScheduleE) -> Decimal:
    return sum(
        (schedule_e_property_net(p) for p in sched_e.properties), start=Decimal("0")
    )


# ---------------------------------------------------------------------------
# Itemized total with SALT cap
# ---------------------------------------------------------------------------


def itemized_total_capped(it: ItemizedDeductions, status: FilingStatus) -> Decimal:
    """Sum Schedule A items applying the SALT cap.

    Taxpayer can elect state/local SALES tax instead of income tax on one line
    (Schedule A line 5a). Whichever is used, the combined SALT total (that
    election + real estate + personal property) is capped at $10k ($5k MFS).

    Mortgage insurance premium deduction (line 8d) expired for TY2022 unless
    reinstated; TY2025 status — assume not allowed; reserved in model but
    excluded from total. Fan-out can re-enable if law changes.
    """
    # Medical: full amount (the 7.5% AGI floor is applied by tenforty, not here)
    medical = it.medical_and_dental_total

    # SALT: sales OR income + real estate + personal property, capped
    salt_elected_tax = (
        it.state_and_local_sales_tax
        if it.elect_sales_tax_over_income_tax
        else it.state_and_local_income_tax
    )
    salt_raw = salt_elected_tax + it.real_estate_tax + it.personal_property_tax
    salt_cap = SALT_CAP_MFS if status == FilingStatus.MFS else SALT_CAP_NORMAL
    salt_capped = min(salt_raw, salt_cap)

    # Interest paid: mortgage interest + points + investment interest
    interest = it.home_mortgage_interest + it.mortgage_points + it.investment_interest

    # Charity: cash + non-cash + carryover (subject to AGI-percentage limits
    # which tenforty enforces — we pass the raw total)
    charity = (
        it.gifts_to_charity_cash
        + it.gifts_to_charity_other_than_cash
        + it.gifts_to_charity_carryover
    )

    # Casualty/theft (federal disaster only post-TCJA)
    casualty = it.casualty_and_theft_losses_federal_disaster

    # Other itemized: user-supplied bucket for anything we don't model explicitly
    other = sum(it.other_itemized.values(), start=Decimal("0"))

    return medical + salt_capped + interest + charity + casualty + other


# ---------------------------------------------------------------------------
# Schedule 1 net (Part I additions - Part II adjustments)
# ---------------------------------------------------------------------------


def _sum_adjustments(adj: AdjustmentsToIncome) -> Decimal:
    """Sum every Schedule 1 Part II adjustment. Positive = reduces AGI.

    deductible_se_tax is EXCLUDED — tenforty computes the ½ SE tax adjustment
    automatically from self_employment_income, so passing it again would double
    count.

    OBBBA additions (Schedule 1-A tips/overtime, senior deduction, Trump
    Account) are included — they reduce AGI just like traditional Part II items.
    """
    return (
        adj.educator_expenses
        + adj.hsa_deduction
        + adj.se_health_insurance
        + adj.se_retirement_plans
        + adj.alimony_paid
        + adj.ira_deduction
        + adj.student_loan_interest
        + adj.archer_msa_deduction
        + adj.penalty_on_early_withdrawal_of_savings
        + adj.moving_expenses_military
        + adj.qualified_tips_deduction_schedule_1a
        + adj.qualified_overtime_deduction_schedule_1a
        + adj.senior_deduction_obbba
        + adj.trump_account_deduction_form_4547
        + sum(adj.other_adjustments.values(), start=Decimal("0"))
    )


def _sum_part_i_additional_income(return_: CanonicalReturn) -> Decimal:
    """Sum Schedule 1 Part I additional-income items from the canonical return.

    This is where we route income that doesn't fit tenforty's top-level
    parameters. v0.1 handles unemployment (1099-G box 1). Other items (state
    refund, alimony received, gambling, other) are fan-out work.
    """
    unemployment = sum(
        (f.box1_unemployment_compensation for f in return_.forms_1099_g),
        start=Decimal("0"),
    )
    return unemployment


def schedule_1_net(return_: CanonicalReturn) -> Decimal:
    """Schedule 1 net = Part I additions - Part II adjustments.

    Passed to tenforty's schedule_1_income parameter (positive = add to AGI,
    negative = reduce AGI; signed semantics verified in CP4.1 probe).
    """
    return _sum_part_i_additional_income(return_) - _sum_adjustments(return_.adjustments)


# ---------------------------------------------------------------------------
# Marshaling to tenforty
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TenfortyInput:
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
    schedule_1_income: float
    standard_or_itemized: str
    itemized_deductions: float
    num_dependents: int


def _to_tenforty_input(return_: CanonicalReturn) -> TenfortyInput:
    """Marshal a canonical return into tenforty.evaluate_return kwargs."""
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

    # Schedule C net profit (Line 31), summed across all businesses.
    # Any 1099-NEC linked to a Schedule C should have its nonemployee
    # compensation already reflected in that Schedule C's gross_receipts.
    se_net_profit = sum(
        (schedule_c_net_profit(sc) for sc in return_.schedules_c), start=Decimal("0")
    )

    # Schedule E net rental income, summed across properties + schedules.
    rental_net = sum(
        (schedule_e_total_net(sched) for sched in return_.schedules_e),
        start=Decimal("0"),
    )

    if return_.itemize_deductions and return_.itemized is not None:
        itemized_total = itemized_total_capped(return_.itemized, return_.filing_status)
        standard_or_itemized = "Itemized"
    else:
        itemized_total = Decimal("0")
        standard_or_itemized = "Standard"

    sched_1 = schedule_1_net(return_)

    return TenfortyInput(
        year=return_.tax_year,
        filing_status=_TENFORTY_STATUS[return_.filing_status],
        w2_income=float(w2_sum),
        taxable_interest=float(interest_sum),
        qualified_dividends=float(qual_div_sum),
        ordinary_dividends=float(ord_div_sum),
        short_term_capital_gains=float(st_1099b),
        long_term_capital_gains=float(lt_1099b + cap_gain_distr_sum),
        self_employment_income=float(se_net_profit),
        rental_income=float(rental_net),
        schedule_1_income=float(sched_1),
        standard_or_itemized=standard_or_itemized,
        itemized_deductions=float(itemized_total),
        num_dependents=len(return_.dependents),
    )


# ---------------------------------------------------------------------------
# Total-income and total-payments aggregation (Decimal-correct)
# ---------------------------------------------------------------------------


def total_income(return_: CanonicalReturn) -> Decimal:
    """Sum of all income sources in the canonical return, Decimal-based.

    This is a display-facing "total" that matches roughly line 9 of Form 1040
    (total income before adjustments). It is NOT what tenforty computes for
    AGI — AGI subtracts above-the-line adjustments.
    """
    w2_sum = sum((w2.box1_wages for w2 in return_.w2s), start=Decimal("0"))
    interest = sum(
        (f.box1_interest_income for f in return_.forms_1099_int), start=Decimal("0")
    )
    ord_div = sum(
        (f.box1a_ordinary_dividends for f in return_.forms_1099_div),
        start=Decimal("0"),
    )
    cap_gain_distr = sum(
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

    se_net = sum(
        (schedule_c_net_profit(sc) for sc in return_.schedules_c), start=Decimal("0")
    )
    rental_net = sum(
        (schedule_e_total_net(sched) for sched in return_.schedules_e),
        start=Decimal("0"),
    )

    # Schedule 1 Part I additions (unemployment, etc.)
    sched_1_part_i = _sum_part_i_additional_income(return_)

    # 1099-R taxable amounts (pensions/IRAs/retirement)
    retirement = sum(
        (f.box2a_taxable_amount for f in return_.forms_1099_r), start=Decimal("0")
    )

    # SSA-1099 net benefits (taxable portion computed in patch layer via the SS
    # benefits worksheet; for display total_income we include the full amount
    # and note that tenforty doesn't currently handle this — fan-out will add
    # an 85%-worksheet patch).
    ssa = sum((f.box5_net_benefits for f in return_.forms_ssa_1099), start=Decimal("0"))

    return (
        w2_sum
        + interest
        + ord_div
        + cap_gain_distr
        + st_1099b
        + lt_1099b
        + se_net
        + rental_net
        + sched_1_part_i
        + retirement
        + ssa
    )


def total_payments(return_: CanonicalReturn) -> Decimal:
    """Sum all payments and refundable credits against the tax.

    Convention:
    - W-2 federal withholding is summed from w2s[].box2 (preferred source).
    - Payments.federal_income_tax_withheld_from_w2 is a fallback aggregate;
      use it only if w2s is empty. If both are populated, we use w2s and the
      caller is expected to see a warning (TODO: emit via logging).
    - 1099 federal withholding is summed from each 1099 form's box 4.
    - Estimated payments, prior-year overpayment applied, extension payment,
      excess SS, and refundable credits all add to the total.
    """
    w2_withholding = sum(
        (w2.box2_federal_income_tax_withheld for w2 in return_.w2s), start=Decimal("0")
    )
    if w2_withholding == 0 and return_.payments.federal_income_tax_withheld_from_w2 > 0:
        w2_withholding = return_.payments.federal_income_tax_withheld_from_w2

    withholding_1099 = (
        sum(
            (f.box4_federal_income_tax_withheld for f in return_.forms_1099_int),
            start=Decimal("0"),
        )
        + sum(
            (f.box4_federal_income_tax_withheld for f in return_.forms_1099_div),
            start=Decimal("0"),
        )
        + sum(
            (f.box4_federal_income_tax_withheld for f in return_.forms_1099_b),
            start=Decimal("0"),
        )
        + sum(
            (f.box4_federal_income_tax_withheld for f in return_.forms_1099_nec),
            start=Decimal("0"),
        )
        + sum(
            (f.box4_federal_income_tax_withheld for f in return_.forms_1099_r),
            start=Decimal("0"),
        )
        + sum(
            (f.box4_federal_income_tax_withheld for f in return_.forms_1099_g),
            start=Decimal("0"),
        )
    )

    ssa_withholding = sum(
        (f.box6_federal_income_tax_withheld for f in return_.forms_ssa_1099),
        start=Decimal("0"),
    )

    p = return_.payments
    return (
        w2_withholding
        + withholding_1099
        + ssa_withholding
        + p.federal_income_tax_withheld_from_1099
        + p.federal_income_tax_withheld_other
        + p.estimated_tax_payments_2025
        + p.prior_year_overpayment_applied
        + p.amount_paid_with_4868_extension
        + p.excess_social_security_tax_withheld
        + p.earned_income_credit_refundable
        + p.additional_child_tax_credit_refundable
        + p.american_opportunity_credit_refundable
    )


# ---------------------------------------------------------------------------
# Earned income / investment income / MAGI (patch-layer inputs)
# ---------------------------------------------------------------------------


def earned_income(return_: CanonicalReturn) -> Decimal:
    """Earned income for CTC/ACTC/EITC purposes.

    Includes:
      - W-2 box 1 wages (all W-2s, taxpayer + spouse)
      - Schedule C net profit (Line 31) summed across all businesses

    Excludes (explicitly NOT earned):
      - Interest, dividends, capital gains (investment income)
      - 1099-R pensions/IRAs (retirement distributions)
      - Schedule E rental / royalties (passive)
      - SSA-1099 Social Security benefits
      - Unemployment (1099-G box 1)

    TODO: Include Schedule SE-eligible partnership earnings from K-1. The v1
    ScheduleK1 model carries ordinary_business_income and guaranteed_payments,
    but classification as SE-eligible requires source_type inspection and a
    material-participation flag we do not yet model. Deferred.
    """
    w2_sum = sum((w2.box1_wages for w2 in return_.w2s), start=Decimal("0"))
    sc_net = sum(
        (schedule_c_net_profit(sc) for sc in return_.schedules_c), start=Decimal("0")
    )
    # TODO(k1-se): add SE-eligible partnership earnings from K-1s.
    return w2_sum + sc_net


def investment_income(return_: CanonicalReturn) -> Decimal:
    """Investment income for the EITC disqualifier.

    Includes:
      - 1099-INT box 1 (taxable interest) + box 3 (US Treasury interest)
      - 1099-DIV box 1a (ordinary dividends, which already contains qualified)
      - 1099-DIV box 2a (capital gain distributions)
      - 1099-B realized gains (ST + LT)
      - Schedule E Part I net (rental + royalties)

    Excludes:
      - SSA-1099 SS benefits
      - 1099-R pensions/retirement
      - Tax-exempt interest (box 8)

    Per IRS Pub. 596, EITC investment income limits compare TOTAL investment
    income (not net). Losses (e.g. negative rental) DO reduce the total.
    """
    interest = sum(
        (
            f.box1_interest_income + f.box3_us_savings_bond_and_treasury_interest
            for f in return_.forms_1099_int
        ),
        start=Decimal("0"),
    )
    ord_div = sum(
        (f.box1a_ordinary_dividends for f in return_.forms_1099_div),
        start=Decimal("0"),
    )
    cap_gain_distr = sum(
        (f.box2a_total_capital_gain_distributions for f in return_.forms_1099_div),
        start=Decimal("0"),
    )

    realized_gains = Decimal("0")
    for form in return_.forms_1099_b:
        for txn in form.transactions:
            realized_gains += txn.proceeds - txn.cost_basis + txn.adjustment_amount

    rental_net = sum(
        (schedule_e_total_net(sched) for sched in return_.schedules_e),
        start=Decimal("0"),
    )

    return interest + ord_div + cap_gain_distr + realized_gains + rental_net


def magi(return_: CanonicalReturn, agi: Decimal) -> Decimal:
    """Modified Adjusted Gross Income.

    v1: MAGI == AGI for purely domestic returns.

    TODO: Add back §911 foreign earned income exclusion (Form 2555 excluded
    amount) + §931/933 US-territory exclusions when those modules land. For
    CTC/NIIT/EITC, the add-back rules differ slightly — see IRC §24(b), §1411,
    and §32 respectively — but all collapse to AGI when there are no foreign
    exclusions to add back.
    """
    return agi


# ---------------------------------------------------------------------------
# Input hash — detects stale computed totals after mutation
# ---------------------------------------------------------------------------


def _input_hash(return_: CanonicalReturn) -> str:
    """Hash the compute-relevant inputs of a canonical return.

    Excludes the `computed` block so the hash doesn't change when we set the
    result of compute() itself. If any input field changes, the hash drifts
    and consumers can detect stale values.
    """
    as_json = return_.model_dump(mode="json", exclude={"computed", "notes"})
    canonical = json.dumps(as_json, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def compute(return_: CanonicalReturn) -> CanonicalReturn:
    """Compute a canonical return end-to-end.

    Returns a new CanonicalReturn with ComputedTotals populated.

    Architecture:
      1. Marshal CanonicalReturn -> tenforty.evaluate_return kwargs.
      2. Unpack tenforty result (AGI, taxable income, federal income tax,
         federal total tax = fed tax + SE + Add'l Medicare, effective/marginal
         rates).
      3. Apply the patch layer (CTC, NIIT, EITC) — tenforty does not compute
         these from its high-level API.
      4. Fold patch results into Credits / Payments / OtherTaxes on a copy of
         the canonical return, and recompute the top-line totals so the
         caller's `computed` block reflects the full federal picture.
    """
    # Lazy import to avoid circular import: niit.py imports from this module.
    from skill.scripts.calc.patches.ctc import compute_ctc
    from skill.scripts.calc.patches.eitc import compute_eitc
    from skill.scripts.calc.patches.niit import compute_niit

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
        schedule_1_income=tf_input.schedule_1_income,
        standard_or_itemized=tf_input.standard_or_itemized,
        itemized_deductions=tf_input.itemized_deductions,
        num_dependents=tf_input.num_dependents,
    )

    agi = _cents(tf_result.federal_adjusted_gross_income)
    ti = _cents(tf_result.federal_taxable_income)
    fed_tax = _cents(tf_result.federal_income_tax)
    tf_total_tax = _cents(tf_result.federal_total_tax)
    deduction = (agi - ti) if (agi is not None and ti is not None) else None

    ti_val = _cents(total_income(return_))
    adjustments_val = _cents(_sum_adjustments(return_.adjustments))
    payments_val = _cents(total_payments(return_))

    # -------------------------------------------------------------------
    # Patch layer inputs
    # -------------------------------------------------------------------
    # AGI from tenforty is authoritative. MAGI == AGI for v1 (no FEIE).
    agi_for_patches = agi if agi is not None else Decimal("0")
    magi_val = magi(return_, agi_for_patches)
    earned_income_val = earned_income(return_)
    investment_income_val = investment_income(return_)
    tax_before_credits = fed_tax if fed_tax is not None else Decimal("0")

    # -------------------------------------------------------------------
    # Patch: CTC + ACTC + ODC
    # -------------------------------------------------------------------
    ctc_result = compute_ctc(
        return_=return_,
        magi=magi_val,
        tax_before_credits=tax_before_credits,
        earned_income=earned_income_val,
    )

    # -------------------------------------------------------------------
    # Patch: NIIT (Form 8960)
    # -------------------------------------------------------------------
    niit_result = compute_niit(return_=return_, magi=magi_val)

    # -------------------------------------------------------------------
    # Patch: EITC
    # -------------------------------------------------------------------
    eitc_result = compute_eitc(
        return_=return_,
        agi=agi_for_patches,
        earned_income=earned_income_val,
        investment_income=investment_income_val,
    )

    # -------------------------------------------------------------------
    # Fold patch outputs into the Credits / Payments / OtherTaxes objects
    # -------------------------------------------------------------------
    # CTC nonrefundable credits (child tax credit + ODC) reduce tax dollar
    # for dollar and cannot produce a refund. ACTC is the refundable slice,
    # landed in Payments. EITC is fully refundable.
    nonref_ctc = _cents(ctc_result.nonrefundable_ctc) or Decimal("0")
    nonref_odc = _cents(ctc_result.credit_for_other_dependents) or Decimal("0")
    refundable_actc = _cents(ctc_result.refundable_actc) or Decimal("0")
    niit_val = _cents(niit_result.niit) or Decimal("0")
    eitc_val = (
        Decimal("0") if eitc_result.disqualified else (_cents(eitc_result.eitc) or Decimal("0"))
    )

    updated_credits = return_.credits.model_copy(
        update={
            "child_tax_credit": nonref_ctc,
            "credit_for_other_dependents": nonref_odc,
            "additional_child_tax_credit_refundable": refundable_actc,
            "earned_income_tax_credit": eitc_val,
        }
    )
    updated_other_taxes = return_.other_taxes.model_copy(
        update={"net_investment_income_tax": niit_val}
    )
    # The wave-1 patch layer sets Payments refundable-credit fields directly
    # so total_payments() reflects them.
    updated_payments = return_.payments.model_copy(
        update={
            "additional_child_tax_credit_refundable": refundable_actc,
            "earned_income_credit_refundable": eitc_val,
        }
    )

    # -------------------------------------------------------------------
    # Recompute top-line totals using the patch-augmented return.
    # -------------------------------------------------------------------
    total_credits_nonref = nonref_ctc + nonref_odc

    # Other taxes: SE + Add'l Medicare already in tenforty's federal_total_tax,
    # plus NIIT that we computed ourselves.
    tf_other_taxes = (
        (tf_total_tax - fed_tax) if (tf_total_tax is not None and fed_tax is not None) else Decimal("0")
    )
    other_taxes_val = tf_other_taxes + niit_val

    # Final total tax:
    #   max(0, federal income tax - nonrefundable credits)
    #   + other federal taxes (SE + Add'l Medicare + NIIT)
    fed_tax_after_nonref = max(Decimal("0"), tax_before_credits - total_credits_nonref)
    final_total_tax = _cents(fed_tax_after_nonref + other_taxes_val)

    # total_payments must include the refundable credits we just set.
    # total_payments() already sums Payments.additional_child_tax_credit_refundable
    # and Payments.earned_income_credit_refundable, so rebuild a temporary
    # return with the updated Payments object to get the correct aggregate.
    return_with_patches = return_.model_copy(
        update={
            "credits": updated_credits,
            "other_taxes": updated_other_taxes,
            "payments": updated_payments,
        }
    )
    payments_val = _cents(total_payments(return_with_patches))

    refund: Decimal | None = None
    owed: Decimal | None = None
    if final_total_tax is not None and payments_val is not None:
        diff = payments_val - final_total_tax
        if diff > 0:
            refund = diff
        elif diff < 0:
            owed = -diff
        else:
            refund = Decimal("0")

    effective_rate = (
        float(tf_result.federal_effective_tax_rate)
        if tf_result.federal_effective_tax_rate is not None
        else None
    )
    marginal_rate = (
        float(tf_result.federal_tax_bracket)
        if tf_result.federal_tax_bracket is not None
        else None
    )

    computed = ComputedTotals(
        total_income=ti_val,
        adjustments_total=adjustments_val,
        adjusted_gross_income=agi,
        deduction_taken=deduction,
        taxable_income=ti,
        tentative_tax=fed_tax,
        total_credits_nonrefundable=_cents(total_credits_nonref),
        other_taxes_total=_cents(other_taxes_val),
        total_tax=final_total_tax,
        total_payments=payments_val,
        refund=refund,
        amount_owed=owed,
        effective_rate=effective_rate,
        marginal_rate=marginal_rate,
        computed_input_hash=_input_hash(return_),
    )

    return return_with_patches.model_copy(update={"computed": computed})
