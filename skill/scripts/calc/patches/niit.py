"""NIIT (Net Investment Income Tax, Form 8960) patch.

IRC §1411. A 3.8% tax on the lesser of:
  (a) net investment income (NII), or
  (b) the excess of modified adjusted gross income (MAGI) over a filing-status
      threshold.

Thresholds are NOT indexed for inflation (fixed by statute):
  Single / HoH / QSS  $200,000
  MFJ                 $250,000
  MFS                 $125,000

References:
  - IRS Form 8960 overview: https://www.irs.gov/forms-pubs/about-form-8960
  - Constants: skill/reference/ty2025-constants.json `payroll_taxes.niit`

v1 scope
--------
NII includes:
  - Interest (1099-INT box 1, taxable only)
  - Ordinary dividends (1099-DIV box 1a)
  - Capital gain distributions (1099-DIV box 2a)
  - Realized capital gains on 1099-B transactions (ST + LT)
  - Net passive rental income (Schedule E Part I)
  - Royalties (Schedule E Part I)

NII excludes:
  - Wages and SE income
  - 1099-R retirement distributions (pensions, IRAs)
  - SSA-1099 Social Security benefits
  - Tax-exempt interest (1099-INT box 8)
  - §121 primary residence gain exclusion

TODOs deferred to future patches:
  - §469 material participation analysis. v1 treats ALL Schedule E rental as
    passive NII. A taxpayer who materially participates (real estate
    professional, self-rental to an active business, etc.) can exclude that
    rental from NII. A §469 classification pass will split Sch E properties
    into passive vs. non-passive and only the passive bucket will hit NII.
  - Investment interest expense, state-tax allocable to NII, and misc
    investment expenses are deductions against NII on Form 8960 lines 9a-9c.
    v1 uses GROSS NII (no NII deductions). A "niit_deductions" patch will
    subtract these allocable expenses once SALT apportionment is available.
  - Passive K-1 income (partnership/S-corp pass-through investment income).
    v1 does not read Schedule K-1 for NII purposes.
  - Non-retirement annuity income (would come from a 1099-R with a code
    indicating non-qualified annuity, or a separate model field).
  - CFC/PFIC inclusions (Form 8621).
  - Trader-in-securities election exclusion (§1411(c)(2)).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from skill.scripts.calc import constants as C
from skill.scripts.calc.engine import schedule_e_property_net
from skill.scripts.models import CanonicalReturn


# 3.8% NIIT rate (IRC §1411(a))
_NIIT_RATE = Decimal("0.038")


@dataclass(frozen=True)
class NIITResult:
    """Result of a Form 8960 NIIT computation.

    Attributes
    ----------
    net_investment_income : Decimal
        Gross net investment income (v1: no NII-allocable deductions applied).
    magi : Decimal
        Modified AGI as supplied by the caller. For domestic returns with no
        foreign earned-income exclusion this equals AGI; the caller is
        responsible for foreign-income add-backs before calling.
    threshold : Decimal
        Statutory filing-status threshold.
    excess_magi_over_threshold : Decimal
        max(0, magi - threshold).
    tax_base : Decimal
        min(net_investment_income, excess_magi_over_threshold).
    niit : Decimal
        3.8% × tax_base.
    details : dict
        Per-source breakdown of NII, for audit trail and Form 8960 line fills.
    """

    net_investment_income: Decimal
    magi: Decimal
    threshold: Decimal
    excess_magi_over_threshold: Decimal
    tax_base: Decimal
    niit: Decimal
    details: dict = field(default_factory=dict)


def _sum_interest(return_: CanonicalReturn) -> Decimal:
    """Taxable interest (1099-INT box 1). Box 8 tax-exempt is explicitly excluded."""
    return sum(
        (f.box1_interest_income for f in return_.forms_1099_int),
        start=Decimal("0"),
    )


def _sum_ordinary_dividends(return_: CanonicalReturn) -> Decimal:
    """Ordinary dividends (1099-DIV box 1a). 1b qualified dividends are a
    SUBSET of box 1a and should not be double-counted."""
    return sum(
        (f.box1a_ordinary_dividends for f in return_.forms_1099_div),
        start=Decimal("0"),
    )


def _sum_capital_gain_distributions(return_: CanonicalReturn) -> Decimal:
    """Mutual-fund/REIT capital gain distributions (1099-DIV box 2a)."""
    return sum(
        (f.box2a_total_capital_gain_distributions for f in return_.forms_1099_div),
        start=Decimal("0"),
    )


def _sum_realized_capital_gains(return_: CanonicalReturn) -> Decimal:
    """Sum of realized gains on 1099-B transactions (ST + LT).

    Gain per transaction = proceeds - cost_basis + adjustment_amount.
    Matches the engine's marshaling for tenforty; kept in sync by formula not
    by shared helper to keep this patch decoupled from the tenforty wiring.
    """
    total = Decimal("0")
    for form in return_.forms_1099_b:
        for txn in form.transactions:
            total += txn.proceeds - txn.cost_basis + txn.adjustment_amount
    return total


def _sum_rental_and_royalties(return_: CanonicalReturn) -> Decimal:
    """Net passive rental income + royalties from Schedule E Part I.

    ``schedule_e_property_net`` already combines rents + royalties minus every
    Part I expense, so one call per property is correct. Do NOT additionally
    add royalties_received — that would double-count.

    v1 treats ALL Sch E rental as passive NII. See module TODO on §469.
    """
    total = Decimal("0")
    for sched in return_.schedules_e:
        for prop in sched.properties:
            total += schedule_e_property_net(prop)
    return total


def compute_niit(return_: CanonicalReturn, magi: Decimal) -> NIITResult:
    """Compute Form 8960 NIIT for a canonical return at a given MAGI.

    Parameters
    ----------
    return_ : CanonicalReturn
        The canonical return. NII is read from this.
    magi : Decimal
        Modified adjusted gross income. Caller is responsible for any FEIE /
        §911 add-backs that convert AGI into MAGI. For purely domestic returns,
        MAGI == AGI and the caller can pass AGI directly.

    Returns
    -------
    NIITResult
        Frozen dataclass with the full Form 8960 intermediate values.
    """
    interest = _sum_interest(return_)
    ord_div = _sum_ordinary_dividends(return_)
    cap_gain_distr = _sum_capital_gain_distributions(return_)
    realized_gains = _sum_realized_capital_gains(return_)
    rental_and_royalties = _sum_rental_and_royalties(return_)

    nii = interest + ord_div + cap_gain_distr + realized_gains + rental_and_royalties

    threshold = Decimal(str(C.niit_threshold(return_.filing_status.value)))
    excess = max(Decimal("0"), magi - threshold)
    tax_base = min(nii, excess)
    # NIIT only applies when tax_base > 0 (a net investment LOSS doesn't create
    # a negative tax)
    if tax_base < 0:
        tax_base = Decimal("0")

    niit = tax_base * _NIIT_RATE

    details = {
        "interest": interest,
        "ordinary_dividends": ord_div,
        "capital_gain_distributions": cap_gain_distr,
        "realized_capital_gains": realized_gains,
        "rental_net": rental_and_royalties,
        "rate": _NIIT_RATE,
        "filing_status": return_.filing_status.value,
        # TODOs intentionally surfaced so consumers can see what's missing
        "todo_section_469_material_participation": (
            "v1 treats all Sch E rental as passive; real-estate professional "
            "or self-rental to active business not yet excluded from NII."
        ),
        "todo_nii_deductions": (
            "v1 uses gross NII; investment interest, state tax allocable to "
            "NII, and misc investment expenses not yet subtracted (Form 8960 "
            "lines 9a-9c)."
        ),
        "todo_passive_k1_income": (
            "v1 does not read Schedule K-1 for NII; passive partnership / "
            "S-corp investment income not yet sourced."
        ),
    }

    return NIITResult(
        net_investment_income=nii,
        magi=magi,
        threshold=threshold,
        excess_magi_over_threshold=excess,
        tax_base=tax_base,
        niit=niit,
        details=details,
    )
