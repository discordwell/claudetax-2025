"""1099-R taxable distributions must reach AGI / taxable income / tax.

Regression tests for a silent income-drop bug: `_to_tenforty_input` summed
1099-R `box2a_taxable_amount` into the *display* `total_income` (Form 1040
line 9) and the renderer printed it on line 4b/5b, but it was never routed
into tenforty's input, so it never reached AGI, taxable income, or tax. Every
retiree with a pension or IRA distribution had their tax silently understated,
and the rendered Form 1040 was internally inconsistent (line 9 did not
reconcile with line 11 + the deductions).

The fix routes the taxable amount through tenforty's `schedule_1_income`
parameter, which tenforty adds to AGI as ordinary income — exactly how a
pension / IRA distribution is taxed. The canonical `schedule_1_net()` is left
untouched so the Schedule 1 renderer still shows only true Schedule 1 items.

These tests assert AGI and tax with retirement income present; they all fail
against the pre-fix engine (which produced AGI $0 / tax $0 for a retiree with
only 1099-R income).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from skill.scripts.calc.engine import compute, total_income
from skill.scripts.models import (
    Address,
    CanonicalReturn,
    FilingStatus,
    Form1099B,
    Form1099BTransaction,
    Form1099R,
    Person,
    W2,
)


def _taxpayer(dob: dt.date = dt.date(1985, 1, 1)) -> Person:
    # Default age 40 (no senior / age-65 deductions) so the standard deduction
    # is the clean base amount and test arithmetic is transparent.
    return Person(
        first_name="Alex",
        last_name="Taxpayer",
        ssn="111-22-3333",
        date_of_birth=dob,
    )


def _address() -> Address:
    return Address(street1="1 Test Lane", city="Austin", state="TX", zip="78701")


def _ret(**overrides) -> CanonicalReturn:
    defaults: dict = dict(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=_taxpayer(),
        address=_address(),
    )
    defaults.update(overrides)
    return CanonicalReturn(**defaults)  # type: ignore[arg-type]


def _1099r(taxable: str, gross: str | None = None) -> Form1099R:
    g = Decimal(gross) if gross is not None else Decimal(taxable)
    return Form1099R(
        payer_name="Fidelity",
        box1_gross_distribution=g,
        box2a_taxable_amount=Decimal(taxable),
    )


class TestRetirementReachesAGI:
    def test_pension_only_reaches_agi_and_tax(self):
        """$40k 1099-R, single age 40: AGI $40,000, taxable income
        $40,000 − $15,750 standard = $24,250, tax = $2,675 (2025 brackets)."""
        r = compute(_ret(forms_1099_r=[_1099r("40000")])).computed
        assert r.adjusted_gross_income == Decimal("40000.00")
        assert r.taxable_income == Decimal("24250.00")
        assert r.total_tax == Decimal("2675.00")

    def test_pension_combines_with_wages(self):
        """$30k W-2 + $20k 1099-R → AGI $50,000 (both are ordinary income)."""
        r = compute(
            _ret(
                w2s=[
                    W2(
                        employer_name="Acme",
                        box1_wages=Decimal("30000"),
                        box2_federal_income_tax_withheld=Decimal("2000"),
                    )
                ],
                forms_1099_r=[_1099r("20000")],
            )
        ).computed
        assert r.adjusted_gross_income == Decimal("50000.00")
        assert r.taxable_income == Decimal("34250.00")  # 50000 − 15750

    def test_only_taxable_box2a_reaches_agi_not_gross(self):
        """A partially-taxable distribution (box 1 $50k, box 2a $30k — e.g. a
        partial return of after-tax basis) adds ONLY the box 2a amount to AGI."""
        r = compute(_ret(forms_1099_r=[_1099r("30000", gross="50000")])).computed
        assert r.adjusted_gross_income == Decimal("30000.00")

    def test_zero_taxable_amount_adds_nothing(self):
        """A fully non-taxable rollover (box 2a $0) leaves AGI at $0."""
        r = compute(_ret(forms_1099_r=[_1099r("0", gross="40000")])).computed
        assert r.adjusted_gross_income == Decimal("0.00")

    def test_line9_total_income_reconciles_with_agi(self):
        """Display total_income (line 9) and AGI (line 11) both include the
        retirement income — the rendered return is internally consistent. With
        no above-the-line adjustments, line 9 == AGI."""
        ret = _ret(forms_1099_r=[_1099r("40000")])
        r = compute(ret).computed
        assert total_income(ret) == Decimal("40000")
        assert r.total_income == Decimal("40000.00")
        assert r.adjusted_gross_income == Decimal("40000.00")


class TestRetirementCascadesToMAGI:
    def test_pension_lifts_magi_over_niit_threshold(self):
        """Retirement income is part of MAGI (it raises AGI), so it can push a
        filer over the NIIT threshold even though it is not itself net
        investment income.

        Single, $180k wages + $10k LT capital gain: MAGI $190k < $200k → NIIT
        $0. Add a $40k pension: MAGI $230k, NII still $10k → NIIT = 3.8% ×
        min($10k, $30k excess) = $380."""
        ltcg = Form1099B(
            broker_name="Broker",
            transactions=[
                Form1099BTransaction(
                    description="VTI",
                    date_sold=dt.date(2025, 6, 1),
                    date_acquired=dt.date(2020, 1, 1),
                    proceeds=Decimal("10000"),
                    cost_basis=Decimal("0"),
                    is_long_term=True,
                )
            ],
        )
        without = compute(
            _ret(
                w2s=[W2(employer_name="Acme", box1_wages=Decimal("180000"))],
                forms_1099_b=[ltcg],
            )
        )
        assert without.other_taxes.net_investment_income_tax == Decimal("0.00")

        with_pension = compute(
            _ret(
                w2s=[W2(employer_name="Acme", box1_wages=Decimal("180000"))],
                forms_1099_b=[ltcg],
                forms_1099_r=[_1099r("40000")],
            )
        )
        assert with_pension.computed.adjusted_gross_income == Decimal("230000.00")
        assert with_pension.other_taxes.net_investment_income_tax == Decimal("380.00")
