"""Integration tests for the wave-1 calc patch wiring in engine.compute().

These tests prove that the engine correctly threads CanonicalReturn inputs
into the CTC, NIIT, and EITC patches and folds their outputs back into the
Credits / Payments / OtherTaxes blocks AND the top-line ComputedTotals.

Coverage:
  1. Zero-dependent, zero-investment baseline: wiring is a no-op so the
     result matches the raw tenforty output (tentative_tax == total_tax, no
     CTC, no NIIT, no EITC, no refundable credits).
  2. CTC wiring: 1 qualifying child applies $2,200 nonrefundable credit that
     reduces total_tax by exactly $2,200 relative to the dependent-less
     baseline.
  3. NIIT wiring: high-income single with investment income sees NIIT
     computed from nii × 3.8%, added to other_taxes.net_investment_income_tax,
     and added to the final total_tax.
  4. EITC wiring: low-income single with one qualifying child sees EITC
     populated in Credits.earned_income_tax_credit AND
     Payments.earned_income_credit_refundable, and the refund grows by the
     credit amount.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from skill.scripts.calc.engine import compute
from skill.scripts.models import (
    Address,
    CanonicalReturn,
    Dependent,
    DependentRelationship,
    FilingStatus,
    Form1099DIV,
    Form1099INT,
    Person,
    W2,
)


def _taxpayer(name: str = "Alex", dob: dt.date = dt.date(1985, 1, 1)) -> Person:
    return Person(
        first_name=name,
        last_name="Taxpayer",
        ssn="111-22-3333",
        date_of_birth=dob,
    )


def _address() -> Address:
    return Address(street1="1 Test Lane", city="Springfield", state="IL", zip="62701")


def _qualifying_child(
    name: str = "Junior", dob: dt.date = dt.date(2018, 6, 15)
) -> Dependent:
    """A dependent under 17 on 12/31/2025 (dob 2018 → age 7). Qualifying child."""
    return Dependent(
        person=Person(
            first_name=name,
            last_name="Taxpayer",
            ssn="444-55-6666",
            date_of_birth=dob,
        ),
        relationship=DependentRelationship.SON,
        months_lived_with_taxpayer=12,
        is_qualifying_child=True,
        is_qualifying_relative=False,
    )


def _base_single_return(**overrides) -> CanonicalReturn:
    defaults = dict(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=_taxpayer(),
        address=_address(),
    )
    defaults.update(overrides)
    return CanonicalReturn(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. Zero-dependent zero-investment baseline — wiring is a no-op.
# ---------------------------------------------------------------------------


class TestPatchLayerNoOpBaseline:
    """Returns with no dependents, no investment income, and EITC-ineligible
    earned income must produce the same result as pre-wiring."""

    def test_simple_w2_no_patches_fire(self):
        ret = _base_single_return(
            w2s=[
                W2(
                    employer_name="Acme",
                    box1_wages=Decimal("65000.00"),
                    box2_federal_income_tax_withheld=Decimal("7500.00"),
                )
            ]
        )
        r = compute(ret)
        # CTC patch: no dependents → zero credit applied.
        assert r.credits.child_tax_credit == Decimal("0")
        assert r.credits.credit_for_other_dependents == Decimal("0")
        assert r.credits.additional_child_tax_credit_refundable == Decimal("0")
        assert r.payments.additional_child_tax_credit_refundable == Decimal("0")
        # NIIT: no investment income → zero.
        assert r.other_taxes.net_investment_income_tax == Decimal("0")
        # EITC: earned income $65k >> 0-child phase-out → disqualified, zero.
        assert r.credits.earned_income_tax_credit == Decimal("0")
        assert r.payments.earned_income_credit_refundable == Decimal("0")
        # Top line matches the raw tenforty output (which is what the
        # pre-wiring engine produced too).
        assert r.computed.total_credits_nonrefundable == Decimal("0.00")
        assert r.computed.tentative_tax == r.computed.total_tax  # no SE, no NIIT
        assert r.computed.total_tax == Decimal("5755.00")
        assert r.computed.refund == Decimal("1745.00")
        assert r.computed.amount_owed is None


# ---------------------------------------------------------------------------
# 2. CTC wiring — 1 qualifying child, wages above ACTC floor.
# ---------------------------------------------------------------------------


class TestCTCWiring:
    """A single filer with $55k W-2 and one qualifying child sees the full
    $2,200 nonrefundable CTC applied, dropping total_tax by $2,200 relative to
    the same return without the dependent.

    Wages are $55k (above the $50,434 1-child EITC AGI limit) precisely so
    that EITC is out of the picture and we isolate the CTC delta.
    """

    def _ret(self, with_dependent: bool) -> CanonicalReturn:
        deps: list[Dependent] = [_qualifying_child()] if with_dependent else []
        return _base_single_return(
            dependents=deps,
            w2s=[
                W2(
                    employer_name="Acme",
                    box1_wages=Decimal("55000.00"),
                    box2_federal_income_tax_withheld=Decimal("3000.00"),
                )
            ],
        )

    def test_ctc_applied_to_nonrefundable_credits(self):
        r = compute(self._ret(with_dependent=True))
        assert r.credits.child_tax_credit == Decimal("2200.00")
        assert r.credits.additional_child_tax_credit_refundable == Decimal("0")
        assert r.payments.additional_child_tax_credit_refundable == Decimal("0")

    def test_ctc_eitc_does_not_fire_at_55k(self):
        """Sanity: the $55k wage level is above the 1-child EITC AGI limit,
        so EITC must be zero and any refund delta is attributable purely to
        CTC. If this ever starts failing, the test scenario's assumption
        broke and the subsequent deltas need to be re-derived."""
        r = compute(self._ret(with_dependent=True))
        assert r.credits.earned_income_tax_credit == Decimal("0")

    def test_ctc_reduces_total_tax(self):
        without = compute(self._ret(with_dependent=False))
        with_ = compute(self._ret(with_dependent=True))
        # $4475 fed income tax - $2200 CTC = $2275 total federal tax
        assert without.computed.total_tax == Decimal("4475.00")
        assert with_.computed.total_tax == Decimal("2275.00")
        assert (
            without.computed.total_tax - with_.computed.total_tax
            == Decimal("2200.00")
        )

    def test_ctc_tentative_tax_unchanged(self):
        """tentative_tax is the pre-credit federal income tax — the patch
        layer must NOT rewrite it."""
        with_ = compute(self._ret(with_dependent=True))
        assert with_.computed.tentative_tax == Decimal("4475.00")

    def test_ctc_reduces_balance_due(self):
        """Without the dependent, $3000 withheld < $4475 tax → $1,475 owed.
        With the dependent, $3000 withheld vs $2,275 tax → $725 refund.
        Net swing to the taxpayer's side is exactly $2,200.
        """
        without = compute(self._ret(with_dependent=False))
        with_ = compute(self._ret(with_dependent=True))
        assert without.computed.amount_owed == Decimal("1475.00")
        assert without.computed.refund is None
        assert with_.computed.refund == Decimal("725.00")
        assert with_.computed.amount_owed is None

    def test_ctc_populates_total_credits_nonrefundable(self):
        r = compute(self._ret(with_dependent=True))
        assert r.computed.total_credits_nonrefundable == Decimal("2200.00")


# ---------------------------------------------------------------------------
# 3. NIIT wiring — high-income single with investment income.
# ---------------------------------------------------------------------------


class TestNIITWiring:
    """Single, $300k W-2 + $20k interest + $30k LT cap gain (from 1099-DIV
    box 2a to keep the fixture compact). Hand check:
        nii = 20000 + 30000 = 50000
        magi = 350000, single threshold = 200000, excess = 150000
        tax_base = min(50000, 150000) = 50000
        niit = 50000 × 0.038 = 1900.00
    """

    def _ret(self) -> CanonicalReturn:
        return _base_single_return(
            w2s=[
                W2(
                    employer_name="Acme",
                    box1_wages=Decimal("300000.00"),
                    box2_federal_income_tax_withheld=Decimal("60000.00"),
                )
            ],
            forms_1099_int=[
                Form1099INT(payer_name="Big Bank", box1_interest_income=Decimal("20000.00"))
            ],
            forms_1099_div=[
                Form1099DIV(
                    payer_name="Index Fund",
                    box2a_total_capital_gain_distributions=Decimal("30000.00"),
                )
            ],
        )

    def test_niit_populated_in_other_taxes(self):
        r = compute(self._ret())
        assert r.other_taxes.net_investment_income_tax == Decimal("1900.00")

    def test_niit_added_to_other_taxes_total(self):
        r = compute(self._ret())
        # other_taxes_total = tenforty's (total_tax - fed_tax) + NIIT.
        # tenforty already computes Add'l Medicare in its total_tax for high
        # wages, so we only assert that NIIT is present as an additive.
        assert r.computed.other_taxes_total is not None
        assert r.computed.other_taxes_total >= Decimal("1900.00")

    def test_niit_added_to_total_tax(self):
        """Compare against the same filer WITHOUT investment income — the
        delta in total_tax attributable to our patch layer is exactly the
        NIIT (plus whatever tenforty already changes from the extra income;
        we subtract that delta to isolate the NIIT)."""
        with_invest = compute(self._ret())
        without_invest = compute(
            _base_single_return(
                w2s=[
                    W2(
                        employer_name="Acme",
                        box1_wages=Decimal("300000.00"),
                        box2_federal_income_tax_withheld=Decimal("60000.00"),
                    )
                ]
            )
        )
        # NIIT patch contribution is exactly 1900
        assert with_invest.other_taxes.net_investment_income_tax == Decimal("1900.00")
        assert without_invest.other_taxes.net_investment_income_tax == Decimal("0")
        # The patch's NIIT 1900 must be present in with_invest's total_tax.
        # Note: tenforty independently raises fed_tax and total_tax for the
        # extra $50k of income, so we can't assert an exact delta — but we
        # can assert that other_taxes_total with invest minus other_taxes_total
        # without invest contains at least the $1,900 NIIT slice.
        delta_other_taxes = (
            with_invest.computed.other_taxes_total
            - without_invest.computed.other_taxes_total
        )
        assert delta_other_taxes >= Decimal("1900.00")

    def test_niit_below_threshold_does_not_fire(self):
        """Same investment mix but wages drop to $100k → MAGI = $150k <
        $200k threshold → no NIIT."""
        ret = _base_single_return(
            w2s=[
                W2(
                    employer_name="Acme",
                    box1_wages=Decimal("100000.00"),
                    box2_federal_income_tax_withheld=Decimal("15000.00"),
                )
            ],
            forms_1099_int=[
                Form1099INT(payer_name="Big Bank", box1_interest_income=Decimal("20000.00"))
            ],
            forms_1099_div=[
                Form1099DIV(
                    payer_name="Index Fund",
                    box2a_total_capital_gain_distributions=Decimal("30000.00"),
                )
            ],
        )
        r = compute(ret)
        assert r.other_taxes.net_investment_income_tax == Decimal("0")


# ---------------------------------------------------------------------------
# 4. EITC wiring — low-income single with one qualifying child.
# ---------------------------------------------------------------------------


class TestEITCWiring:
    """Single, $15k W-2, 1 qualifying child.

    TY2025 1-child EITC parameters:
      - phase_in_rate = 0.34
      - earned_income_for_max_credit = 12,730
      - max_credit = 4,328
      - phase_out_begin (non-MFJ) = 23,350
      - phase_out_rate = 0.1598

    Hand check:
      - Phase-in credit = 15000 × 0.34 = 5100 → capped at 4328
      - Phase determinant = max(15000, 15000) = 15000 < 23350 → no phase-out
      - Credit = 4328

    Investment income = 0, so disqualifier does not fire.
    Taxable income = 15000 − 15750 = 0 (floored) → fed tax = 0.
    """

    def _ret(self) -> CanonicalReturn:
        return _base_single_return(
            dependents=[_qualifying_child()],
            w2s=[W2(employer_name="Diner", box1_wages=Decimal("15000.00"))],
        )

    def test_eitc_populated_in_credits(self):
        r = compute(self._ret())
        assert r.credits.earned_income_tax_credit == Decimal("4328.00")

    def test_eitc_populated_in_refundable_payments(self):
        """EITC is fully refundable — it should ALSO be in Payments.earned_income_credit_refundable."""
        r = compute(self._ret())
        assert r.payments.earned_income_credit_refundable == Decimal("4328.00")

    def test_eitc_increases_refund(self):
        """The refund should include the full $4,328 EITC plus any ACTC (also
        refundable) from the 1 qualifying child. No withholding in this
        fixture, so refund == EITC + ACTC exactly."""
        r = compute(self._ret())
        # ACTC: fed_tax = 0 → 2200 CTC unused → min(2200, 1700, 15%*(15000-2500)=1875) = 1700
        # Refund = EITC 4328 + ACTC 1700 = 6028
        assert r.computed.total_tax == Decimal("0.00")
        assert r.computed.total_payments == Decimal("6028.00")
        assert r.computed.refund == Decimal("6028.00")
        assert r.computed.amount_owed is None

    def test_eitc_disqualified_by_investment_income_ceiling(self):
        """Same earned income but add enough investment income to trip the
        EITC investment-income disqualifier → EITC = 0."""
        from skill.scripts.calc.constants import eitc_investment_income_disqualifier

        limit = Decimal(eitc_investment_income_disqualifier())
        ret = _base_single_return(
            dependents=[_qualifying_child()],
            w2s=[W2(employer_name="Diner", box1_wages=Decimal("15000.00"))],
            forms_1099_int=[
                Form1099INT(
                    payer_name="Bank",
                    box1_interest_income=limit + Decimal("1.00"),
                )
            ],
        )
        r = compute(ret)
        assert r.credits.earned_income_tax_credit == Decimal("0")
        assert r.payments.earned_income_credit_refundable == Decimal("0")
