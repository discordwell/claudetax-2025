"""Regression tests for the six engine blockers flagged in the CP1-CP7 review.

Each test pins behavior that was broken in the initial commit. If any of these
fail, the regression is back.

Blockers covered:
1. Schedule C must compute net profit (gross - ALL expenses - home office)
2. total_payments must sum all payment categories across sources
3. Itemized total must sum all categories + apply SALT cap
4. QSS spouse validation requires date_of_death
5. total_income / adjustments_total must be Decimal-correct and semantically right
6. return_.adjustments must reduce AGI via schedule_1_income

Plus regression coverage for:
- 1099 federal withholding from multiple sources
- Schedule E royalties included in rental net
- OBBBA adjustments (tips, overtime, senior, Trump Account) reduce AGI
- Input hash changes when inputs change
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from skill.scripts.calc.engine import (
    _input_hash,
    compute,
    itemized_total_capped,
    schedule_1_net,
    schedule_c_net_profit,
    schedule_e_property_net,
    total_income,
    total_payments,
)
from skill.scripts.models import (
    Address,
    AdjustmentsToIncome,
    CanonicalReturn,
    FilingStatus,
    Form1099B,
    Form1099BTransaction,
    Form1099DIV,
    Form1099G,
    Form1099INT,
    Form1099NEC,
    Form1099R,
    ItemizedDeductions,
    Payments,
    Person,
    ScheduleC,
    ScheduleCExpenses,
    ScheduleE,
    ScheduleEProperty,
    W2,
)


def _person(name: str, ssn: str = "111-22-3333") -> Person:
    return Person(first_name=name, last_name="X", ssn=ssn, date_of_birth=dt.date(1985, 1, 1))


def _addr() -> Address:
    return Address(street1="1 A", city="B", state="CA", zip="90001")


def _base_return(**overrides) -> CanonicalReturn:
    defaults = dict(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=_person("Alex"),
        address=_addr(),
    )
    defaults.update(overrides)
    return CanonicalReturn(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Blocker 1: Schedule C net profit
# ---------------------------------------------------------------------------


class TestScheduleCNetProfit:
    def test_net_profit_subtracts_all_expenses(self):
        """Line 31 = Line 7 (gross income) - Line 28 (total expenses) - Line 30 (home office)."""
        sc = ScheduleC(
            business_name="Freelance Co",
            principal_business_or_profession="consulting",
            line1_gross_receipts=Decimal("100000"),
            line2_returns_and_allowances=Decimal("0"),
            line4_cost_of_goods_sold=Decimal("0"),
            line6_other_income=Decimal("0"),
            expenses=ScheduleCExpenses(
                line8_advertising=Decimal("5000"),
                line22_supplies=Decimal("3000"),
                line23_taxes_and_licenses=Decimal("2000"),
                line24a_travel=Decimal("4000"),
                line25_utilities=Decimal("1500"),
                line27a_other_expenses=Decimal("2500"),
            ),
            line30_home_office_expense=Decimal("2000"),
        )
        # Gross = 100000
        # Expenses = 5000 + 3000 + 2000 + 4000 + 1500 + 2500 = 18000
        # Home office = 2000
        # Net profit = 100000 - 18000 - 2000 = 80000
        assert schedule_c_net_profit(sc) == Decimal("80000")

    def test_net_profit_with_cogs(self):
        sc = ScheduleC(
            business_name="Retail Co",
            principal_business_or_profession="retail",
            line1_gross_receipts=Decimal("200000"),
            line4_cost_of_goods_sold=Decimal("80000"),
            expenses=ScheduleCExpenses(line22_supplies=Decimal("10000")),
        )
        # Gross income = 200000 - 80000 = 120000
        # Total expenses = 10000
        # Net profit = 120000 - 10000 = 110000
        assert schedule_c_net_profit(sc) == Decimal("110000")

    def test_other_expense_detail_included(self):
        sc = ScheduleC(
            business_name="X",
            principal_business_or_profession="y",
            line1_gross_receipts=Decimal("50000"),
            expenses=ScheduleCExpenses(
                other_expense_detail={"software_subscriptions": Decimal("2000"), "cowork": Decimal("3000")}
            ),
        )
        # Net = 50000 - 5000 = 45000
        assert schedule_c_net_profit(sc) == Decimal("45000")

    def test_compute_uses_net_profit_not_gross(self):
        """End-to-end: engine must pass net profit to tenforty, not gross income."""
        ret = _base_return(
            schedules_c=[
                ScheduleC(
                    business_name="Consulting",
                    principal_business_or_profession="consulting",
                    line1_gross_receipts=Decimal("100000"),
                    expenses=ScheduleCExpenses(
                        line22_supplies=Decimal("30000"),
                        line25_utilities=Decimal("10000"),
                    ),
                )
            ]
        )
        result = compute(ret)
        # Net profit is 100000 - 40000 = 60000
        # SE tax on 60000: 60000 * 0.9235 * 0.153 ~ 8478.77
        # Half SE: ~4239.38
        # AGI: 60000 - 4239.38 ~ 55760.62
        assert result.computed.adjusted_gross_income is not None
        # AGI must be below 56000 (proves expenses were subtracted; if gross $100k
        # had been passed instead, AGI would be close to 93000)
        assert result.computed.adjusted_gross_income < Decimal("56000")
        assert result.computed.adjusted_gross_income > Decimal("55000")


# ---------------------------------------------------------------------------
# Blocker 2: total_payments sums all categories
# ---------------------------------------------------------------------------


class TestTotalPayments:
    def test_sums_w2_withholding(self):
        ret = _base_return(
            w2s=[
                W2(employer_name="A", box1_wages=Decimal("50000"), box2_federal_income_tax_withheld=Decimal("5000")),
                W2(employer_name="B", box1_wages=Decimal("30000"), box2_federal_income_tax_withheld=Decimal("3000")),
            ]
        )
        assert total_payments(ret) == Decimal("8000")

    def test_sums_1099_withholding(self):
        ret = _base_return(
            forms_1099_int=[
                Form1099INT(payer_name="Bank", box1_interest_income=Decimal("500"),
                            box4_federal_income_tax_withheld=Decimal("100"))
            ],
            forms_1099_div=[
                Form1099DIV(payer_name="Fund", box1a_ordinary_dividends=Decimal("1000"),
                            box4_federal_income_tax_withheld=Decimal("200"))
            ],
            forms_1099_nec=[
                Form1099NEC(payer_name="Client", box1_nonemployee_compensation=Decimal("5000"),
                            box4_federal_income_tax_withheld=Decimal("500"))
            ],
        )
        assert total_payments(ret) == Decimal("800")

    def test_sums_all_payment_categories(self):
        ret = _base_return(
            w2s=[W2(employer_name="A", box1_wages=Decimal("50000"),
                    box2_federal_income_tax_withheld=Decimal("5000"))],
            payments=Payments(
                federal_income_tax_withheld_from_1099=Decimal("100"),
                federal_income_tax_withheld_other=Decimal("50"),
                estimated_tax_payments_2025=Decimal("2000"),
                prior_year_overpayment_applied=Decimal("500"),
                amount_paid_with_4868_extension=Decimal("1000"),
                excess_social_security_tax_withheld=Decimal("150"),
                earned_income_credit_refundable=Decimal("300"),
                additional_child_tax_credit_refundable=Decimal("400"),
                american_opportunity_credit_refundable=Decimal("200"),
            ),
        )
        # 5000 + 100 + 50 + 2000 + 500 + 1000 + 150 + 300 + 400 + 200 = 9700
        assert total_payments(ret) == Decimal("9700")

    def test_uses_w2s_not_payments_aggregate_when_both_set(self):
        """Prefer w2s[].box2 over Payments.federal_income_tax_withheld_from_w2."""
        ret = _base_return(
            w2s=[W2(employer_name="A", box1_wages=Decimal("50000"),
                    box2_federal_income_tax_withheld=Decimal("5000"))],
            payments=Payments(federal_income_tax_withheld_from_w2=Decimal("9999")),
        )
        # Should use the W-2 amount, not the aggregate
        assert total_payments(ret) == Decimal("5000")

    def test_fallback_to_payments_aggregate_when_no_w2s(self):
        ret = _base_return(
            payments=Payments(federal_income_tax_withheld_from_w2=Decimal("5000"))
        )
        assert total_payments(ret) == Decimal("5000")


# ---------------------------------------------------------------------------
# Blocker 3: Itemized total with SALT cap
# ---------------------------------------------------------------------------


class TestItemizedWithSALTCap:
    def test_includes_all_schedule_a_categories(self):
        it = ItemizedDeductions(
            medical_and_dental_total=Decimal("1000"),
            state_and_local_income_tax=Decimal("5000"),  # under cap
            real_estate_tax=Decimal("3000"),
            personal_property_tax=Decimal("500"),
            home_mortgage_interest=Decimal("10000"),
            mortgage_points=Decimal("500"),
            investment_interest=Decimal("200"),
            gifts_to_charity_cash=Decimal("2000"),
            gifts_to_charity_other_than_cash=Decimal("500"),
            gifts_to_charity_carryover=Decimal("100"),
            casualty_and_theft_losses_federal_disaster=Decimal("0"),
            other_itemized={"gambling_losses": Decimal("50")},
        )
        # SALT raw = 5000 + 3000 + 500 = 8500, under 10000 cap → 8500
        # Medical 1000 + SALT 8500 + interest 10700 + charity 2600 + other 50 = 22850
        assert itemized_total_capped(it, FilingStatus.SINGLE) == Decimal("22850")

    def test_salt_cap_10k_applied_single(self):
        it = ItemizedDeductions(
            state_and_local_income_tax=Decimal("12000"),
            real_estate_tax=Decimal("8000"),
            personal_property_tax=Decimal("1000"),
            home_mortgage_interest=Decimal("5000"),
        )
        # SALT raw = 21000, capped at 10000
        # Interest = 5000
        # Total = 10000 + 5000 = 15000
        assert itemized_total_capped(it, FilingStatus.SINGLE) == Decimal("15000")

    def test_salt_cap_5k_applied_mfs(self):
        it = ItemizedDeductions(
            state_and_local_income_tax=Decimal("8000"),
            real_estate_tax=Decimal("4000"),
        )
        # SALT raw = 12000, MFS cap 5000
        assert itemized_total_capped(it, FilingStatus.MFS) == Decimal("5000")

    def test_salt_cap_10k_applied_mfj(self):
        it = ItemizedDeductions(
            state_and_local_income_tax=Decimal("15000"),
            real_estate_tax=Decimal("7000"),
        )
        # SALT raw = 22000, MFJ cap 10000
        assert itemized_total_capped(it, FilingStatus.MFJ) == Decimal("10000")

    def test_elect_sales_tax_uses_sales_not_income(self):
        it = ItemizedDeductions(
            state_and_local_income_tax=Decimal("0"),  # no income tax state
            state_and_local_sales_tax=Decimal("3000"),
            elect_sales_tax_over_income_tax=True,
            real_estate_tax=Decimal("4000"),
        )
        # SALT raw = 3000 + 4000 = 7000, under cap
        assert itemized_total_capped(it, FilingStatus.SINGLE) == Decimal("7000")

    def test_compute_applies_salt_cap(self):
        """End-to-end: a user with $30k SALT should see the cap applied through tenforty."""
        ret = _base_return(
            w2s=[W2(employer_name="A", box1_wages=Decimal("200000"))],
            itemize_deductions=True,
            itemized=ItemizedDeductions(
                state_and_local_income_tax=Decimal("20000"),
                real_estate_tax=Decimal("10000"),
                home_mortgage_interest=Decimal("8000"),
            ),
        )
        result = compute(ret)
        # Deduction should reflect SALT cap: 10000 SALT + 8000 mortgage = 18000
        assert result.computed.deduction_taken == Decimal("18000")


# ---------------------------------------------------------------------------
# Blocker 4: QSS spouse validation
# ---------------------------------------------------------------------------


class TestQSSSpouseValidation:
    def test_qss_with_deceased_spouse_ok(self):
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.QSS,
            taxpayer=Person(
                first_name="Survivor",
                last_name="X",
                ssn="111-22-3333",
                date_of_birth=dt.date(1970, 1, 1),
            ),
            spouse=Person(
                first_name="Deceased",
                last_name="X",
                ssn="444-55-6666",
                date_of_birth=dt.date(1968, 5, 5),
                date_of_death=dt.date(2024, 8, 10),
            ),
            address=_addr(),
        )
        assert ret.filing_status == FilingStatus.QSS
        assert ret.spouse is not None
        assert ret.spouse.date_of_death == dt.date(2024, 8, 10)

    def test_qss_without_spouse_ok(self):
        """QSS can legally have no spouse populated (all data for deceased spouse may be off record)."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.QSS,
            taxpayer=Person(
                first_name="Survivor", last_name="X", ssn="111-22-3333",
                date_of_birth=dt.date(1970, 1, 1),
            ),
            address=_addr(),
        )
        assert ret.spouse is None

    def test_qss_with_living_spouse_rejected(self):
        with pytest.raises(ValidationError, match="date_of_death"):
            CanonicalReturn(
                tax_year=2025,
                filing_status=FilingStatus.QSS,
                taxpayer=Person(
                    first_name="Survivor", last_name="X", ssn="111-22-3333",
                    date_of_birth=dt.date(1970, 1, 1),
                ),
                spouse=Person(
                    first_name="Alive", last_name="X", ssn="444-55-6666",
                    date_of_birth=dt.date(1971, 2, 2),
                ),
                address=_addr(),
            )


# ---------------------------------------------------------------------------
# Blocker 5: total_income and adjustments_total semantics
# ---------------------------------------------------------------------------


class TestTotalIncomeAndAdjustmentsSemantics:
    def test_total_income_sums_decimal_sources(self):
        ret = _base_return(
            w2s=[W2(employer_name="A", box1_wages=Decimal("50000.10"))],
            forms_1099_int=[Form1099INT(payer_name="Bank", box1_interest_income=Decimal("100.01"))],
            forms_1099_div=[
                Form1099DIV(
                    payer_name="Fund",
                    box1a_ordinary_dividends=Decimal("200.02"),
                    box2a_total_capital_gain_distributions=Decimal("50.03"),
                )
            ],
        )
        # 50000.10 + 100.01 + 200.02 + 50.03 = 50350.16
        assert total_income(ret) == Decimal("50350.16")

    def test_adjustments_total_matches_adjustments_block(self):
        ret = _base_return(
            w2s=[W2(employer_name="A", box1_wages=Decimal("60000"))],
            adjustments=AdjustmentsToIncome(
                hsa_deduction=Decimal("4300"),
                ira_deduction=Decimal("7000"),
                student_loan_interest=Decimal("2500"),
            ),
        )
        result = compute(ret)
        # adjustments_total should be the SUM of Part II items, not AGI - total_income
        assert result.computed.adjustments_total == Decimal("13800")

    def test_total_income_is_independent_of_adjustments(self):
        """Mutating adjustments does not change total_income."""
        ret_a = _base_return(w2s=[W2(employer_name="A", box1_wages=Decimal("60000"))])
        ret_b = _base_return(
            w2s=[W2(employer_name="A", box1_wages=Decimal("60000"))],
            adjustments=AdjustmentsToIncome(hsa_deduction=Decimal("4300")),
        )
        assert total_income(ret_a) == total_income(ret_b) == Decimal("60000")


# ---------------------------------------------------------------------------
# Blocker 6: Adjustments reduce AGI via schedule_1_income
# ---------------------------------------------------------------------------


class TestAdjustmentsReduceAGI:
    def test_hsa_deduction_reduces_agi(self):
        """A $4,300 HSA contribution must reduce AGI by $4,300 relative to baseline."""
        baseline = _base_return(w2s=[W2(employer_name="A", box1_wages=Decimal("60000"))])
        with_hsa = _base_return(
            w2s=[W2(employer_name="A", box1_wages=Decimal("60000"))],
            adjustments=AdjustmentsToIncome(hsa_deduction=Decimal("4300")),
        )
        r_base = compute(baseline)
        r_hsa = compute(with_hsa)
        assert r_base.computed.adjusted_gross_income is not None
        assert r_hsa.computed.adjusted_gross_income is not None
        assert r_base.computed.adjusted_gross_income - r_hsa.computed.adjusted_gross_income == Decimal("4300")

    def test_ira_deduction_reduces_agi(self):
        baseline = _base_return(w2s=[W2(employer_name="A", box1_wages=Decimal("80000"))])
        with_ira = _base_return(
            w2s=[W2(employer_name="A", box1_wages=Decimal("80000"))],
            adjustments=AdjustmentsToIncome(ira_deduction=Decimal("7000")),
        )
        r_base = compute(baseline)
        r_ira = compute(with_ira)
        assert r_base.computed.adjusted_gross_income - r_ira.computed.adjusted_gross_income == Decimal("7000")

    def test_multiple_adjustments_additive(self):
        ret = _base_return(
            w2s=[W2(employer_name="A", box1_wages=Decimal("100000"))],
            adjustments=AdjustmentsToIncome(
                hsa_deduction=Decimal("4300"),
                ira_deduction=Decimal("7000"),
                student_loan_interest=Decimal("2500"),
            ),
        )
        baseline = _base_return(w2s=[W2(employer_name="A", box1_wages=Decimal("100000"))])
        r = compute(ret)
        b = compute(baseline)
        # AGI should drop by 4300 + 7000 + 2500 = 13800
        assert b.computed.adjusted_gross_income - r.computed.adjusted_gross_income == Decimal("13800")

    def test_obbba_senior_deduction_reduces_agi(self):
        """OBBBA senior deduction +$6k is a Schedule 1-like adjustment that reduces AGI."""
        ret = _base_return(
            w2s=[W2(employer_name="A", box1_wages=Decimal("60000"))],
            adjustments=AdjustmentsToIncome(senior_deduction_obbba=Decimal("6000")),
        )
        baseline = _base_return(w2s=[W2(employer_name="A", box1_wages=Decimal("60000"))])
        r = compute(ret)
        b = compute(baseline)
        assert b.computed.adjusted_gross_income - r.computed.adjusted_gross_income == Decimal("6000")

    def test_obbba_schedule_1a_tips_and_overtime(self):
        ret = _base_return(
            w2s=[W2(employer_name="Restaurant", box1_wages=Decimal("45000"))],
            adjustments=AdjustmentsToIncome(
                qualified_tips_deduction_schedule_1a=Decimal("3000"),
                qualified_overtime_deduction_schedule_1a=Decimal("1500"),
            ),
        )
        baseline = _base_return(
            w2s=[W2(employer_name="Restaurant", box1_wages=Decimal("45000"))]
        )
        r = compute(ret)
        b = compute(baseline)
        assert b.computed.adjusted_gross_income - r.computed.adjusted_gross_income == Decimal("4500")


# ---------------------------------------------------------------------------
# Schedule E royalties included
# ---------------------------------------------------------------------------


class TestScheduleERoyalties:
    def test_royalties_added_to_property_net(self):
        p = ScheduleEProperty(
            address=_addr(),
            rents_received=Decimal("20000"),
            royalties_received=Decimal("5000"),
            repairs=Decimal("2000"),
            taxes=Decimal("3000"),
        )
        # Gross: 20000 + 5000 = 25000; expenses: 5000; net = 20000
        assert schedule_e_property_net(p) == Decimal("20000")


# ---------------------------------------------------------------------------
# Schedule 1 Part I: unemployment from 1099-G
# ---------------------------------------------------------------------------


class TestUnemploymentFromForm1099G:
    def test_unemployment_adds_to_agi(self):
        ret = _base_return(
            w2s=[W2(employer_name="A", box1_wages=Decimal("30000"))],
            forms_1099_g=[
                Form1099G(
                    payer_name="State Unemployment Office",
                    box1_unemployment_compensation=Decimal("5000"),
                )
            ],
        )
        result = compute(ret)
        # AGI should be 30000 + 5000 = 35000 (no adjustments)
        assert result.computed.adjusted_gross_income == Decimal("35000")


# ---------------------------------------------------------------------------
# Input hash stability
# ---------------------------------------------------------------------------


class TestInputHash:
    def test_same_input_same_hash(self):
        r1 = _base_return(w2s=[W2(employer_name="A", box1_wages=Decimal("60000"))])
        r2 = _base_return(w2s=[W2(employer_name="A", box1_wages=Decimal("60000"))])
        assert _input_hash(r1) == _input_hash(r2)

    def test_changing_input_changes_hash(self):
        r1 = _base_return(w2s=[W2(employer_name="A", box1_wages=Decimal("60000"))])
        r2 = _base_return(w2s=[W2(employer_name="A", box1_wages=Decimal("65000"))])
        assert _input_hash(r1) != _input_hash(r2)

    def test_hash_stamped_on_compute(self):
        r = _base_return(w2s=[W2(employer_name="A", box1_wages=Decimal("60000"))])
        result = compute(r)
        assert result.computed.computed_input_hash is not None
        assert result.computed.computed_input_hash == _input_hash(r)

    def test_hash_excludes_computed_block(self):
        """The hash must not include the `computed` block or it would change every time compute() runs."""
        r = _base_return(w2s=[W2(employer_name="A", box1_wages=Decimal("60000"))])
        h_before = _input_hash(r)
        result = compute(r)
        h_after = _input_hash(result)
        assert h_before == h_after
