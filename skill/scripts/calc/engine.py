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
        3. **OBBBA pre-tax-bracket patch layer** — senior deduction and
           Schedule 1-A (tips/overtime) DO change AGI, so they MUST be folded
           into adjustments BEFORE brackets are applied. We use a two-pass
           tenforty strategy (Approach A): first pass gives us a preliminary
           AGI for the MAGI-driven phase-outs; we compute the OBBBA
           deductions from that AGI, fold them into a copy of
           AdjustmentsToIncome, and re-call tenforty so the second pass sees
           the reduced AGI and applies brackets correctly. Both pre-tax-
           bracket patches are cheaply gated to skip the second pass when no
           senior/tips/overtime trigger is present — which is the common
           case for most returns.
        4. Apply the post-tax-bracket patch layer for gaps that do NOT
           change AGI: CTC, ACTC, ODC, NIIT, EITC.
        5. Populate ComputedTotals and return.

    **Why Approach A (two-pass tenforty) over Approach B (marginal-rate
    approximation)?** Schedule 1-A tips/overtime and the OBBBA senior
    deduction can easily push a return across a bracket boundary (see
    integration test `test_single_with_tips_65k` — a $5k tips deduction on
    a $65k single filer moves the filer from the 22% bracket to the 12%
    bracket, and `marginal_rate × deduction` would OVERSTATE the tax
    savings). Approach A is bit-for-bit correct because tenforty re-runs
    its full bracket calculation on the reduced AGI.

Fan-out rule: when you add a patch, write a golden fixture that exercises it.
No patch without a test. No skill change without a test.
"""
from __future__ import annotations

import datetime as dt
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
    Person,
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
    """Sum every Schedule C Part II expense line plus other_expense_detail.

    Note: when a ScheduleC has ``depreciable_assets`` populated, the
    line-13 depreciation total is recomputed from Form 4562 and OVERRIDES
    the caller-supplied ``expenses.line13_depreciation``. This helper
    still takes only ``ScheduleCExpenses`` so existing callers work; the
    override lives inside ``schedule_c_net_profit`` which has the full
    ``ScheduleC`` in hand.
    """
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


def _effective_line_13_depreciation(sc: ScheduleC) -> Decimal:
    """Return the depreciation that should appear on Schedule C line 13.

    If the business has ``depreciable_assets`` populated, the Form 4562
    total (line 22) is the authoritative figure and OVERRIDES
    ``expenses.line13_depreciation`` even when the caller set both. When
    no assets are present, the caller-supplied ``line13_depreciation``
    passes through unchanged — this preserves the pre-wave-6 behavior
    for returns that don't use the Form 4562 compute pipeline.

    Lazy-imports the Form 4562 module to avoid a circular dependency
    (form_4562.py imports the engine's ``schedule_c_net_profit``).
    """
    if not sc.depreciable_assets:
        return sc.expenses.line13_depreciation
    from skill.scripts.output.form_4562 import (  # local to break cycle
        compute_form_4562_fields_for_schedule_c,
    )
    fields = compute_form_4562_fields_for_schedule_c(sc)
    return fields.line_22_total_depreciation


def schedule_c_net_profit(sc: ScheduleC) -> Decimal:
    """Compute a single Schedule C's net profit (Line 31).

    Line 7 = gross income = gross_receipts - returns_and_allowances - COGS + other_income
    Line 28 = total expenses
    Line 29 = tentative profit = line 7 - line 28
    Line 30 = home office expense (from Form 8829)
    Line 31 = net profit = line 29 - line 30

    When ``sc.depreciable_assets`` is non-empty, line 13 depreciation
    is recomputed from the Form 4562 compute layer and replaces the
    caller-supplied ``expenses.line13_depreciation`` in the total.
    """
    gross_income = (
        sc.line1_gross_receipts
        - sc.line2_returns_and_allowances
        - sc.line4_cost_of_goods_sold
        + sc.line6_other_income
    )
    total_expenses = _sch_c_total_expenses(sc.expenses)
    if sc.depreciable_assets:
        effective_13 = _effective_line_13_depreciation(sc)
        total_expenses = (
            total_expenses
            - sc.expenses.line13_depreciation
            + effective_13
        )
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


def itemized_total_capped(
    it: ItemizedDeductions,
    status: FilingStatus,
    agi: Decimal,
) -> Decimal:
    """Sum Schedule A items applying the SALT cap AND the 7.5% medical floor.

    CRITICAL SEMANTICS — tenforty interprets its ``itemized_deductions``
    parameter as the **final Schedule A line 17 amount**, not as raw
    pre-floor medical + everything else. This was empirically verified
    in wave 4 (see ``skill/reference/tenforty-ty2025-gap.md`` and the
    CP8 commit message). Passing raw medical here caused tenforty to
    over-deduct medical by ``min(raw_medical, 0.075 * agi)`` for every
    itemizer with nonzero medical — a real-money calc correctness bug.

    The fix: apply the 7.5%-of-AGI floor to medical BEFORE summing. AGI
    is Form 1040 line 11 (after adjustments, including OBBBA). When
    compute() runs a multi-pass tenforty strategy, the AGI passed here
    must reflect the FINAL post-adjustments AGI that will land on the
    filed form (see the three-pass path in ``compute()`` for the
    OBBBA+medical combo).

    Parameters
    ----------
    it
        The ItemizedDeductions block from a canonical return.
    status
        FilingStatus — drives SALT cap selection (MFS = $5k, else $10k).
    agi
        AGI against which to compute the 7.5% medical floor. Pass
        ``Decimal("0")`` in unit tests that deliberately want the
        no-floor edge case (e.g., isolated SALT-cap tests). Callers in
        ``compute()`` thread a preliminary tenforty-computed AGI.

    Notes
    -----
    Taxpayer can elect state/local SALES tax instead of income tax on
    Schedule A line 5a. Whichever is used, the combined SALT total
    (that election + real estate + personal property) is capped at
    $10k ($5k MFS).

    Mortgage insurance premium deduction (line 8d) expired for TY2022
    unless reinstated; TY2025 status — assume not allowed; reserved in
    model but excluded from total. Fan-out can re-enable if law changes.

    Charity AGI-percentage limits (50%/30%/20%) are NOT applied here —
    tenforty still enforces them on the post-sum total. Casualty/theft
    restricted to federal-disaster-declared events post-TCJA.
    """
    # Medical: apply the 7.5%-of-AGI floor (Schedule A lines 2-4).
    # medical_deductible = max(0, raw - 0.075 * AGI)
    raw_medical = it.medical_and_dental_total
    if raw_medical > 0 and agi > 0:
        medical_floor = (agi * Decimal("0.075")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        medical = max(Decimal("0"), raw_medical - medical_floor)
    else:
        medical = raw_medical

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
    # which tenforty enforces on the post-sum total — we pass the raw value)
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

    OBBBA additions (Schedule 1-A tips/overtime, senior deduction) are included —
    they reduce AGI just like traditional Part II items.

    Form 4547 (Trump Account) is EXCLUDED: IRC §219 disallows any individual
    deduction for Trump Account contributions per the 12/2025 Form 4547
    instructions. The canonical model still carries
    `trump_account_deduction_form_4547` for schema stability, but wave-3 patch
    research confirmed it must always be $0. `compute()` forces it to 0 on the
    returned adjustments object; folding it into the sum here would leak a
    nonzero value if a caller populated it by mistake.
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
        + sum(adj.other_adjustments.values(), start=Decimal("0"))
    )


def _form_4797_schedule_1_amount(return_: CanonicalReturn) -> Decimal:
    """Compute the Form 4797 amount that flows to Schedule 1 line 4.

    This is the net of Part II ordinary gains/losses plus any Part I
    §1231 net loss (§1231 losses are ordinary). When Part I §1231 net
    is positive, it flows to Schedule D line 11 as a long-term capital
    gain instead.

    Lazy-imports the Form 4797 module to avoid a circular dependency.
    Returns $0 when no Form 4797 sales are present.
    """
    if not return_.forms_4797:
        return Decimal("0")
    from skill.scripts.output.form_4797 import compute_form_4797_fields
    fields = compute_form_4797_fields(return_)
    return fields.schedule_1_line_4


def _form_4797_schedule_d_amount(return_: CanonicalReturn) -> Decimal:
    """Compute the Form 4797 §1231 gain that flows to Schedule D line 11.

    Only nonzero when the Part I net is a gain (positive). This amount
    is added to long-term capital gains in the tenforty input.
    """
    if not return_.forms_4797:
        return Decimal("0")
    from skill.scripts.output.form_4797 import compute_form_4797_fields
    fields = compute_form_4797_fields(return_)
    return fields.schedule_d_line_11


def _sum_part_i_additional_income(return_: CanonicalReturn) -> Decimal:
    """Sum Schedule 1 Part I additional-income items from the canonical return.

    This is where we route income that doesn't fit tenforty's top-level
    parameters. v0.1 handles unemployment (1099-G box 1) and Form 4797
    gains/losses (Schedule 1 line 4). Other items (state refund, alimony
    received, gambling, other) are fan-out work.
    """
    unemployment = sum(
        (f.box1_unemployment_compensation for f in return_.forms_1099_g),
        start=Decimal("0"),
    )
    # Form 4797: ordinary gains/losses + §1231 losses flow to Schedule 1
    # line 4. §1231 net gains (positive) flow to Schedule D instead and
    # are handled in _to_tenforty_input via long_term_capital_gains.
    form_4797_sched_1 = _form_4797_schedule_1_amount(return_)
    return unemployment + form_4797_sched_1


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


def _to_tenforty_input(
    return_: CanonicalReturn,
    agi_for_medical_floor: Decimal = Decimal("0"),
) -> TenfortyInput:
    """Marshal a canonical return into tenforty.evaluate_return kwargs.

    ``agi_for_medical_floor`` is the AGI used to compute the 7.5% medical
    floor in ``itemized_total_capped``. Pass ``Decimal("0")`` in the
    first-pass tenforty call (before AGI is known); pass the computed
    AGI in subsequent passes where the medical floor matters. See the
    three-pass strategy in ``compute()``.
    """
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
    sc_se_net = sum(
        (schedule_c_net_profit(sc) for sc in return_.schedules_c), start=Decimal("0")
    )

    # K-1 Box 14: self-employment earnings from partnerships flow to
    # Schedule SE alongside Schedule C net profit.
    k1_se = sum(
        (k1.box14_self_employment_earnings for k1 in return_.schedules_k1),
        start=Decimal("0"),
    )
    se_net_profit = sc_se_net + k1_se

    # Schedule E net rental income, summed across properties + schedules.
    rental_net = sum(
        (schedule_e_total_net(sched) for sched in return_.schedules_e),
        start=Decimal("0"),
    )

    if return_.itemize_deductions and return_.itemized is not None:
        itemized_total = itemized_total_capped(
            return_.itemized, return_.filing_status, agi_for_medical_floor
        )
        standard_or_itemized = "Itemized"
    else:
        itemized_total = Decimal("0")
        standard_or_itemized = "Standard"

    sched_1 = schedule_1_net(return_)

    # Form 4797 §1231 net gain flows as long-term capital gain
    form_4797_lt_gain = _form_4797_schedule_d_amount(return_)

    return TenfortyInput(
        year=return_.tax_year,
        filing_status=_TENFORTY_STATUS[return_.filing_status],
        w2_income=float(w2_sum),
        taxable_interest=float(interest_sum),
        qualified_dividends=float(qual_div_sum),
        ordinary_dividends=float(ord_div_sum),
        short_term_capital_gains=float(st_1099b),
        long_term_capital_gains=float(lt_1099b + cap_gain_distr_sum + form_4797_lt_gain),
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

    sc_se_net = sum(
        (schedule_c_net_profit(sc) for sc in return_.schedules_c), start=Decimal("0")
    )
    k1_se = sum(
        (k1.box14_self_employment_earnings for k1 in return_.schedules_k1),
        start=Decimal("0"),
    )
    se_net = sc_se_net + k1_se

    rental_net = sum(
        (schedule_e_total_net(sched) for sched in return_.schedules_e),
        start=Decimal("0"),
    )

    # Schedule 1 Part I additions (unemployment, Form 4797 ordinary gains, etc.)
    sched_1_part_i = _sum_part_i_additional_income(return_)

    # Form 4797 §1231 net gains that flow as long-term capital gains
    # to Schedule D. These are NOT included in sched_1_part_i (which
    # only carries the ordinary portion) and NOT included in 1099-B
    # loops, so they must be added separately.
    form_4797_lt = _form_4797_schedule_d_amount(return_)

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
        + form_4797_lt
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

    K-1 Box 14 self-employment earnings are included when present on
    partnership K-1s (source_type == "partnership"). S-corp K-1s do not
    generate SE earnings (S-corp shareholders are not subject to SE tax
    on distributions).
    """
    w2_sum = sum((w2.box1_wages for w2 in return_.w2s), start=Decimal("0"))
    sc_net = sum(
        (schedule_c_net_profit(sc) for sc in return_.schedules_c), start=Decimal("0")
    )
    k1_se = sum(
        (k1.box14_self_employment_earnings for k1 in return_.schedules_k1),
        start=Decimal("0"),
    )
    return w2_sum + sc_net + k1_se


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
# OBBBA pre-tax-bracket patch gating
# ---------------------------------------------------------------------------
#
# The OBBBA senior deduction and Schedule 1-A tips/overtime deductions both
# reduce AGI (they are Schedule 1 Part II adjustments). Folding them in
# requires a SECOND tenforty call so the bracket calculation sees the
# reduced AGI. These helpers let us skip the second call when no trigger
# is present — which is the majority of returns.


def _person_age_at_end_of_year(person: Person, tax_year: int) -> int:
    """Age of `person` on 12/31/`tax_year` (matches senior-deduction convention)."""
    dob = person.date_of_birth
    end_of_year = dt.date(tax_year, 12, 31)
    years = end_of_year.year - dob.year
    if (end_of_year.month, end_of_year.day) < (dob.month, dob.day):
        years -= 1
    return years


def _any_filer_age_65_plus(return_: CanonicalReturn) -> bool:
    """Cheap gate for the senior-deduction second-pass tenforty call.

    Returns True if the taxpayer (or, on MFJ/QSS, the spouse) is age 65+
    at end of the tax year. Mirrors the logic in
    `obbba_senior_deduction._count_qualifying_filers` without importing
    the patch module — keeps this gate O(1) and independent of the
    patch's year-gating so we can also skip the senior-deduction call
    when tax_year is outside 2025-2028.
    """
    if _person_age_at_end_of_year(return_.taxpayer, return_.tax_year) >= 65:
        return True
    if (
        return_.filing_status in (FilingStatus.MFJ, FilingStatus.QSS)
        and return_.spouse is not None
        and _person_age_at_end_of_year(return_.spouse, return_.tax_year) >= 65
    ):
        return True
    return False


def _any_tips_or_overtime_declared(return_: CanonicalReturn) -> bool:
    """Cheap gate for the Schedule 1-A second-pass tenforty call.

    The canonical model does not yet have a dedicated "user-declared
    qualified tips/overtime" field on W-2 (box 7 is `social_security_tips`
    which includes non-qualifying tips; the OBBBA definition requires
    employer attestation that we don't yet model — see
    `obbba_schedule_1a` module docstring). For now we treat the existing
    `AdjustmentsToIncome.qualified_tips_deduction_schedule_1a` and
    `qualified_overtime_deduction_schedule_1a` fields as the caller-
    supplied raw amounts. The engine feeds those raw values to the
    Schedule 1-A patch (which caps them and applies phase-out) and
    OVERWRITES the adjustment fields with the patch's computed result.

    TODO(w2-tips): once the ingestion layer gains a structured "qualified
    tips" / "qualified overtime" extraction from W-2 box 14 + employer
    attestation metadata, wire those fields through here instead of
    relying on the caller to pre-populate the adjustment fields.
    """
    adj = return_.adjustments
    return (
        adj.qualified_tips_deduction_schedule_1a > 0
        or adj.qualified_overtime_deduction_schedule_1a > 0
    )


def _any_amt_trigger(return_: CanonicalReturn) -> bool:
    """Cheap gate for the Form 6251 AMT compute pass.

    Fires when the taxpayer has any AMT preference that could push
    tentative minimum tax above regular tax. The triggers are:

    * **SALT add-back**: itemizing with any nonzero state/local income
      tax, sales tax, real-estate tax, or personal-property tax. The
      Schedule A line 7 SALT subtotal flows to Form 6251 line 2a.
    * **Manual AMTAdjustments block**: presence of any
      ``amt_adjustments_manual`` field with a nonzero value — ISO
      bargain element, manual PAB interest, depreciation timing, or
      anything in ``other_prefs``.
    * **1099-INT box 9**: specified private activity bond interest
      reported on any 1099-INT flows to line 2g even without a manual
      AMTAdjustments block.

    Returns False for the common case (standard deduction, no manual
    AMT block, no PAB interest) so the engine skips Form 6251 entirely.
    """
    # SALT trigger — any itemizer with nonzero state/local taxes on
    # Schedule A. Even if the post-cap amount is below the AMT
    # exemption, we still run the compute — it's cheap and the
    # resulting AMT is zero so nothing ships.
    if return_.itemize_deductions and return_.itemized is not None:
        it = return_.itemized
        if (
            it.state_and_local_income_tax > Decimal("0")
            or it.state_and_local_sales_tax > Decimal("0")
            or it.real_estate_tax > Decimal("0")
            or it.personal_property_tax > Decimal("0")
        ):
            return True

    # Manual AMTAdjustments block — any entry fires the path.
    if return_.amt_adjustments_manual is not None:
        amt_adj = return_.amt_adjustments_manual
        if (
            amt_adj.iso_bargain_element > Decimal("0")
            or amt_adj.private_activity_bond_interest > Decimal("0")
            or amt_adj.depreciation_adjustment != Decimal("0")
            or any(v != Decimal("0") for v in amt_adj.other_prefs.values())
        ):
            return True

    # 1099-INT box 9: specified private activity bond interest. This
    # alone can push a filer into AMT territory if they hold the right
    # muni bonds.
    for f in return_.forms_1099_int:
        if f.box9_specified_private_activity_bond_interest > Decimal("0"):
            return True

    return False


def _call_tenforty(tf_input: "TenfortyInput") -> Any:
    """Thin wrapper around tenforty.evaluate_return for deterministic calling.

    Exists so the two-pass strategy in compute() can invoke tenforty from a
    single code path; also centralizes the kwargs list.
    """
    return tenforty.evaluate_return(
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


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def compute(return_: CanonicalReturn) -> CanonicalReturn:
    """Compute a canonical return end-to-end.

    Returns a new CanonicalReturn with ComputedTotals populated.

    Architecture:
      1. Marshal CanonicalReturn -> tenforty.evaluate_return kwargs.
      2. First tenforty call: produces a preliminary AGI that the MAGI-
         driven OBBBA phase-outs need as input. Also used as the base
         for the Schedule A 7.5% medical floor (when itemizing with
         nonzero medical). The first pass uses ``agi_for_medical_floor=0``
         because the floor is applied in the second pass — the first
         pass just needs AGI. For returns with no OBBBA triggers AND no
         medical-requiring-floor, this is the final call and the second
         pass is skipped (bit-for-bit invariant on goldens without medical).
      3. **OBBBA pre-tax-bracket patch layer** — senior deduction and
         Schedule 1-A tips/overtime. Both REDUCE AGI, so if either one
         fires we fold the results into AdjustmentsToIncome and re-call
         tenforty (Approach A: exact bracket re-application).
      4. **Medical 7.5% floor (CP8-A)** — tenforty interprets the
         ``itemized_deductions`` param as the *final* Sch A line 17
         amount, NOT raw pre-floor medical. Passing raw medical over-
         deducts by min(raw, 0.075*AGI). So whenever medical > 0 on an
         itemized return, we compute AGI on the first pass and pass the
         post-floor itemized total on the second pass. The post-OBBBA
         "real" AGI used for the floor is computed algebraically:
         ``real_agi = prelim_agi - obbba_total`` (AGI = total_income -
         adjustments, so OBBBA reduction flows directly).
      5. Unpack tenforty result (AGI, taxable income, federal income tax,
         federal total tax = fed tax + SE + Add'l Medicare, effective/marginal
         rates).
      6. Apply the post-tax-bracket patch layer (CTC, NIIT, EITC) — these
         do NOT change AGI so they run on the final tenforty result.
      7. Fold patch results into Credits / Payments / OtherTaxes on a copy
         of the canonical return, and recompute the top-line totals so the
         caller's `computed` block reflects the full federal picture.
    """
    # Lazy import to avoid circular import: niit.py imports from this module.
    from skill.scripts.calc.patches.ctc import compute_ctc
    from skill.scripts.calc.patches.eitc import compute_eitc
    from skill.scripts.calc.patches.form_4547_trump_account import (
        compute_trump_account_deduction,
    )
    from skill.scripts.calc.patches.niit import compute_niit
    from skill.scripts.calc.patches.obbba_schedule_1a import compute_schedule_1a
    from skill.scripts.calc.patches.obbba_senior_deduction import (
        compute_senior_deduction,
    )

    # -------------------------------------------------------------------
    # Wave 6 — Form 8829 home-office dispatcher (pre-compute)
    # -------------------------------------------------------------------
    # If any ScheduleC on the return carries a populated `home_office`
    # block, derive the Schedule C line 30 amount from it BEFORE any
    # tenforty pass. This must run first because SE tax, AGI, and CTC
    # are all downstream of Sch C net profit — recomputing line 30
    # after compute() would leave those numbers stale. Simplified-
    # method filers get $5/sq ft (capped at $1,500, never more than
    # tentative profit); regular-method filers get Form 8829 line 36.
    #
    # Idempotent: the dispatcher OVERWRITES line30_home_office_expense
    # from the HomeOffice inputs, so a caller who runs compute() twice
    # on the same return gets the same result both times. Returns with
    # NO `home_office` block are untouched (matches wave-5 behavior of
    # callers who populate line 30 by hand).
    from skill.scripts.output.form_8829 import apply_home_office_deductions

    apply_home_office_deductions(return_)

    # -------------------------------------------------------------------
    # OBBBA pre-tax-bracket patches (senior deduction + Schedule 1-A)
    # -------------------------------------------------------------------
    # These are gated behind cheap detection so returns with no senior
    # filers AND no caller-declared tips/overtime do NOT pay the second
    # tenforty call. This preserves the bit-for-bit "no-op" invariant on
    # the existing golden fixtures (simple_w2_standard, w2_investments_
    # itemized, se_home_office — none have age 65+ filers or tips/overtime).
    #
    # MAGI-for-phase-out semantics: The OBBBA senior-deduction and
    # Schedule 1-A phase-outs compare MAGI to a threshold. That MAGI is
    # explicitly the AGI WITHOUT the OBBBA deduction in question (you
    # cannot let a deduction reduce its own phase-out MAGI — that would
    # be circular). So our first tenforty pass strips the OBBBA
    # adjustment fields to zero before computing AGI, uses that AGI as
    # MAGI for the phase-out math, then folds the patch outputs into a
    # fresh AdjustmentsToIncome and calls tenforty a second time for the
    # final bracket computation.
    run_senior_patch = _any_filer_age_65_plus(return_)
    run_schedule_1a_patch = _any_tips_or_overtime_declared(return_)

    # CP8-A: medical-floor trigger. Whenever itemizing with nonzero
    # medical, the SECOND pass must use a post-floor itemized total
    # (tenforty does not apply the 7.5% floor itself).
    need_medical_floor = (
        return_.itemize_deductions
        and return_.itemized is not None
        and return_.itemized.medical_and_dental_total > Decimal("0")
    )

    senior_deduction_amount = Decimal("0")
    sched_1a_tips_amount = Decimal("0")
    sched_1a_overtime_amount = Decimal("0")

    updated_adjustments = return_.adjustments

    if run_senior_patch or run_schedule_1a_patch or need_medical_floor:
        # First pass MUST exclude the OBBBA adjustments so MAGI is clean.
        # First pass uses agi_for_medical_floor=0 because AGI isn't known
        # yet — we're running this pass precisely to discover AGI. The
        # first-pass TI/tax values are thrown away; only AGI matters.
        adjustments_without_obbba = return_.adjustments.model_copy(
            update={
                "senior_deduction_obbba": Decimal("0"),
                "qualified_tips_deduction_schedule_1a": Decimal("0"),
                "qualified_overtime_deduction_schedule_1a": Decimal("0"),
            }
        )
        return_for_first_pass = return_.model_copy(
            update={"adjustments": adjustments_without_obbba}
        )
        tf_input = _to_tenforty_input(
            return_for_first_pass, agi_for_medical_floor=Decimal("0")
        )
        tf_result = _call_tenforty(tf_input)

        # First-pass AGI is MAGI (== AGI before any OBBBA deduction).
        prelim_agi = _d(tf_result.federal_adjusted_gross_income)
        prelim_magi = magi(return_, prelim_agi)

        if run_senior_patch:
            senior_result = compute_senior_deduction(
                return_=return_,
                magi=prelim_magi,
            )
            senior_deduction_amount = senior_result.deduction

        if run_schedule_1a_patch:
            # The caller-populated adjustment fields act as the "raw
            # qualified tips/overtime input" — see
            # `_any_tips_or_overtime_declared` docstring for the rationale
            # and TODO. The patch caps each amount, applies the MAGI
            # phase-out, and returns the final deductions.
            raw_tips = return_.adjustments.qualified_tips_deduction_schedule_1a
            raw_overtime = return_.adjustments.qualified_overtime_deduction_schedule_1a
            sched_1a_result = compute_schedule_1a(
                return_=return_,
                magi=prelim_magi,
                qualified_tips_input=raw_tips,
                qualified_overtime_input=raw_overtime,
            )
            sched_1a_tips_amount = sched_1a_result.tips_deduction
            sched_1a_overtime_amount = sched_1a_result.overtime_deduction

        # Build a new AdjustmentsToIncome with the OBBBA values folded in.
        # We OVERWRITE rather than ADD: the patches are idempotent on the
        # canonical state, so if a caller calls compute() twice on the same
        # return, the second pass must see the same inputs as the first.
        updated_adjustments = return_.adjustments.model_copy(
            update={
                "senior_deduction_obbba": senior_deduction_amount,
                "qualified_tips_deduction_schedule_1a": sched_1a_tips_amount,
                "qualified_overtime_deduction_schedule_1a": sched_1a_overtime_amount,
            }
        )

        obbba_total = (
            senior_deduction_amount
            + sched_1a_tips_amount
            + sched_1a_overtime_amount
        )

        # Real filed-form AGI for the medical floor. AGI = total_income -
        # adjustments, so the OBBBA adjustments (which reduce adjustments-
        # total in favor of the filer) reduce AGI by exactly their sum.
        # No second tenforty call is needed to discover this.
        real_agi = prelim_agi - obbba_total

        # Second tenforty pass with the updated adjustments AND the post-
        # floor itemized total. Skipped only when NOTHING changed — i.e.
        # no OBBBA patches fired AND no medical-floor pass needed.
        # (If OBBBA patches net to zero — full phase-out, year gate, etc.
        # — and no medical, we also skip.)
        second_pass_needed = (
            obbba_total != Decimal("0") or need_medical_floor
        )
        if second_pass_needed:
            return_for_second_pass = return_.model_copy(
                update={"adjustments": updated_adjustments}
            )
            tf_input = _to_tenforty_input(
                return_for_second_pass,
                agi_for_medical_floor=real_agi,
            )
            tf_result = _call_tenforty(tf_input)
    else:
        # No OBBBA patches fire AND no medical floor — single tenforty
        # call, identical to the pre-CP8 behavior. Hot path for most
        # returns and preserves bit-for-bit invariance on all goldens
        # that do not have medical expenses.
        tf_input = _to_tenforty_input(return_)
        tf_result = _call_tenforty(tf_input)

    agi = _cents(tf_result.federal_adjusted_gross_income)
    ti = _cents(tf_result.federal_taxable_income)
    fed_tax = _cents(tf_result.federal_income_tax)
    tf_total_tax = _cents(tf_result.federal_total_tax)
    deduction = (agi - ti) if (agi is not None and ti is not None) else None

    ti_val = _cents(total_income(return_))
    # adjustments_total must reflect the OBBBA patch outputs if they fired.
    # `updated_adjustments` is either the original adjustments (gates skipped)
    # or a copy with the patched OBBBA fields.
    adjustments_val = _cents(_sum_adjustments(updated_adjustments))
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
    # Patch: Form 4547 Trump Account (OBBBA)
    # -------------------------------------------------------------------
    # Audit-only: IRC §219 disallows any individual deduction for Trump
    # Account contributions (confirmed against the 12/2025 Form 4547
    # instructions in wave-3 research). `compute_trump_account_deduction`
    # always returns $0 and is kept as a single canonical check that will
    # fire a loud warning if final Treasury regulations (NPRM 2026-04533)
    # ever add a deductible path. We run it unconditionally because it is
    # cheap (no tenforty call) and year-gated internally, and we force the
    # canonical model's `trump_account_deduction_form_4547` to $0 on the
    # returned adjustments object so a mis-populated caller-supplied value
    # cannot leak into downstream consumers.
    form_4547_result = compute_trump_account_deduction(
        return_=return_, magi=magi_val
    )
    assert form_4547_result.deduction == Decimal("0"), (
        "Form 4547 patch returned a nonzero deduction — §219 invariant "
        "violated. Re-read the patch module docstring."
    )
    updated_adjustments = updated_adjustments.model_copy(
        update={"trump_account_deduction_form_4547": Decimal("0")}
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
    # The OBBBA `updated_adjustments` (senior deduction + Schedule 1-A caps/
    # phase-outs applied) are also threaded through so downstream consumers
    # see the final adjustment values.
    return_with_patches = return_.model_copy(
        update={
            "adjustments": updated_adjustments,
            "credits": updated_credits,
            "other_taxes": updated_other_taxes,
            "payments": updated_payments,
        }
    )
    payments_val = _cents(total_payments(return_with_patches))

    # -------------------------------------------------------------------
    # Form 6251 — Alternative Minimum Tax (wave 6)
    # -------------------------------------------------------------------
    # The AMT path fires when the taxpayer has any AMT trigger item:
    # SALT deduction, manual AMTAdjustments block, or specified private
    # activity bond interest on a 1099-INT (box 9). When fired, AMT is
    # computed from a preliminary ComputedTotals (taxable income +
    # deduction_taken + regular tentative_tax) and added to total_tax.
    amt_value: Decimal | None = None
    amt_delta = Decimal("0")
    if _any_amt_trigger(return_with_patches):
        from skill.scripts.output.form_6251 import compute_form_6251_fields

        # Build a temporary canonical return with a preliminary
        # ComputedTotals so the Form 6251 compute can read taxable
        # income, deduction_taken, and tentative_tax. This mirrors the
        # shape the final `computed` block will take, minus the AMT
        # contribution itself.
        prelim_computed = ComputedTotals(
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
        )
        return_for_amt = return_with_patches.model_copy(
            update={"computed": prelim_computed}
        )
        amt_fields = compute_form_6251_fields(return_for_amt)
        amt_value = _cents(amt_fields.line_11_amt_owed) or Decimal("0")
        amt_delta = amt_value

        # Fold AMT into other_taxes and total_tax, and also stamp it on
        # the OtherTaxes model so downstream consumers see it there.
        other_taxes_val = other_taxes_val + amt_delta
        final_total_tax = _cents(
            (final_total_tax or Decimal("0")) + amt_delta
        )
        updated_other_taxes = updated_other_taxes.model_copy(
            update={"alternative_minimum_tax": amt_value}
        )
        return_with_patches = return_with_patches.model_copy(
            update={"other_taxes": updated_other_taxes}
        )
        # Refund / owed rely on total_tax vs payments — recompute the
        # aggregate so the final ComputedTotals reflects the AMT delta.
        payments_val = _cents(total_payments(return_with_patches))

    # -------------------------------------------------------------------
    # Validation pass (FFFF compatibility + future cross-checks)
    # -------------------------------------------------------------------
    # Runs against `return_with_patches` so the report reflects any
    # patch-driven state changes (e.g. a forced-zero Form 4547 field). The
    # result is an opaque JSON-serializable dict stored on ComputedTotals
    # for downstream consumers (SKILL.md interview, output bundlers,
    # FFFF/paper channel selector).
    from skill.scripts.validate import run_return_validation

    validation_report = run_return_validation(return_with_patches)

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
        alternative_minimum_tax=amt_value,
        other_taxes_total=_cents(other_taxes_val),
        total_tax=final_total_tax,
        total_payments=payments_val,
        refund=refund,
        amount_owed=owed,
        effective_rate=effective_rate,
        marginal_rate=marginal_rate,
        computed_input_hash=_input_hash(return_),
        validation_report=validation_report,
    )

    return return_with_patches.model_copy(update={"computed": computed})
