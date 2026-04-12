"""QBI (Qualified Business Income) deduction patch — Section 199A, Form 8995.

IRC §199A allows a deduction of up to 20% of qualified business income
from pass-through entities (sole proprietorships, partnerships, S-corps,
estates/trusts). Made permanent by OBBBA (was scheduled to sunset
12/31/2025).

For TY2025, Form 8995 (simplified) is used when taxable income before
QBI <= $197,300 (Single/HoH/MFS/QSS) or $394,600 (MFJ). This module
implements the simplified computation only; Form 8995-A (for filers
above the threshold) is deferred.

Deduction = min(20% of total QBI, 20% of taxable income before QBI)

QBI sources (v1):
  - Schedule C net profit (sole proprietorship ordinary business income)
  - Schedule E Part I net rental income (properties with qbi_qualified=True)
  - Schedule K-1 ordinary business income (with qbi_qualified=True)

QBI excludes:
  - W-2 wages (employee compensation is NOT QBI)
  - Interest, dividends, capital gains (investment income)
  - Guaranteed payments from partnerships (IRC §707(c))
  - Reasonable S-corp officer compensation

References:
  - IRS Form 8995 instructions: https://www.irs.gov/instructions/i8995
  - Constants: skill/reference/ty2025-constants.json `qbi_deduction`
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from skill.scripts.calc import constants as C
from skill.scripts.calc.engine import schedule_c_net_profit, schedule_e_property_net
from skill.scripts.models import CanonicalReturn, FilingStatus

_ZERO = Decimal("0")
_CENTS = Decimal("0.01")
_QBI_RATE = Decimal("0.20")


@dataclass(frozen=True)
class QBIResult:
    """Result of a Form 8995 simplified QBI computation.

    Attributes
    ----------
    total_qbi : Decimal
        Sum of all qualified business income sources. Can be negative
        (net QBI loss), in which case the deduction is zero and the
        loss carries forward (carryforward logic is deferred).
    qbi_deduction : Decimal
        The Section 199A deduction: min(20% of total QBI,
        20% of taxable income before QBI). Zero when total_qbi <= 0
        or when taxable_income_before_qbi <= 0.
    taxable_income_before_qbi : Decimal
        Taxable income used as the cap base: AGI - standard/itemized
        deduction (Form 1040 line 11 - line 12). Supplied by the caller.
    twenty_pct_of_qbi : Decimal
        20% of total_qbi (zero when total_qbi <= 0).
    twenty_pct_of_ti : Decimal
        20% of taxable_income_before_qbi (zero when TI <= 0).
    simplified_eligible : bool
        True when TI before QBI is at or below the simplified threshold
        for the filing status. When False, Form 8995-A is required
        (not implemented in v1 — deduction falls back to zero).
    schedule_c_qbi : Decimal
        QBI component from Schedule C net profits.
    schedule_e_qbi : Decimal
        QBI component from Schedule E properties marked qbi_qualified.
    k1_qbi : Decimal
        QBI component from K-1 ordinary business income marked qbi_qualified.
    """

    total_qbi: Decimal = _ZERO
    qbi_deduction: Decimal = _ZERO
    taxable_income_before_qbi: Decimal = _ZERO
    twenty_pct_of_qbi: Decimal = _ZERO
    twenty_pct_of_ti: Decimal = _ZERO
    simplified_eligible: bool = True
    schedule_c_qbi: Decimal = _ZERO
    schedule_e_qbi: Decimal = _ZERO
    k1_qbi: Decimal = _ZERO


def _cents(v: Decimal) -> Decimal:
    return v.quantize(_CENTS, rounding=ROUND_HALF_UP)


def compute_qbi(
    return_: CanonicalReturn,
    taxable_income_before_qbi: Decimal,
) -> QBIResult:
    """Compute the Section 199A QBI deduction (Form 8995 simplified).

    Parameters
    ----------
    return_
        A CanonicalReturn — need not be computed yet. Only the income-
        document lists (schedules_c, schedules_e, schedules_k1) and
        the filing_status are read.
    taxable_income_before_qbi
        Form 1040 line 11 (AGI) minus line 12 (standard/itemized
        deduction). This is the cap for the 20%-of-TI limit. The caller
        computes this from tenforty's output.

    Returns
    -------
    QBIResult
        Frozen dataclass with every intermediate and the final deduction.
    """
    status = return_.filing_status.value
    params = C.qbi_params(status)
    threshold = Decimal(str(params.phase_in_threshold))

    # -- Determine simplified eligibility --------------------------------
    simplified_eligible = taxable_income_before_qbi <= threshold

    # -- Sum QBI from each source ----------------------------------------

    # Schedule C: all sole-proprietorship net profit is QBI.
    # (IRC §199A(c)(1) — QBI includes net amounts of qualified items of
    # income, gain, deduction, and loss from a qualified trade or
    # business.)
    schedule_c_qbi = sum(
        (schedule_c_net_profit(sc) for sc in return_.schedules_c),
        start=_ZERO,
    )

    # Schedule E: only properties explicitly marked as qbi_qualified.
    # Rental income is generally QBI only if the taxpayer elects the
    # safe harbor under Rev. Proc. 2019-38 or otherwise meets the
    # trade-or-business standard.
    schedule_e_qbi = _ZERO
    for sched in return_.schedules_e:
        for prop in sched.properties:
            if prop.qbi_qualified:
                schedule_e_qbi += schedule_e_property_net(prop)

    # K-1: ordinary business income from pass-through entities where
    # the K-1 is marked qbi_qualified. Guaranteed payments are excluded
    # per IRC §199A(c)(4).
    k1_qbi = sum(
        (k1.ordinary_business_income for k1 in return_.schedules_k1 if k1.qbi_qualified),
        start=_ZERO,
    )

    total_qbi = schedule_c_qbi + schedule_e_qbi + k1_qbi

    # -- Compute the deduction -------------------------------------------
    if not simplified_eligible:
        # Form 8995-A (above-threshold) is not implemented in v1.
        # Return zero deduction with simplified_eligible=False so
        # callers know why.
        return QBIResult(
            total_qbi=_cents(total_qbi),
            qbi_deduction=_ZERO,
            taxable_income_before_qbi=_cents(taxable_income_before_qbi),
            twenty_pct_of_qbi=_ZERO,
            twenty_pct_of_ti=_ZERO,
            simplified_eligible=False,
            schedule_c_qbi=_cents(schedule_c_qbi),
            schedule_e_qbi=_cents(schedule_e_qbi),
            k1_qbi=_cents(k1_qbi),
        )

    if total_qbi <= _ZERO:
        # Net QBI loss — deduction is zero; loss carries forward (deferred).
        return QBIResult(
            total_qbi=_cents(total_qbi),
            qbi_deduction=_ZERO,
            taxable_income_before_qbi=_cents(taxable_income_before_qbi),
            twenty_pct_of_qbi=_ZERO,
            twenty_pct_of_ti=_ZERO,
            simplified_eligible=True,
            schedule_c_qbi=_cents(schedule_c_qbi),
            schedule_e_qbi=_cents(schedule_e_qbi),
            k1_qbi=_cents(k1_qbi),
        )

    twenty_pct_of_qbi = _cents(total_qbi * _QBI_RATE)
    twenty_pct_of_ti = _cents(
        max(_ZERO, taxable_income_before_qbi) * _QBI_RATE
    )
    qbi_deduction = min(twenty_pct_of_qbi, twenty_pct_of_ti)

    return QBIResult(
        total_qbi=_cents(total_qbi),
        qbi_deduction=_cents(qbi_deduction),
        taxable_income_before_qbi=_cents(taxable_income_before_qbi),
        twenty_pct_of_qbi=twenty_pct_of_qbi,
        twenty_pct_of_ti=twenty_pct_of_ti,
        simplified_eligible=True,
        schedule_c_qbi=_cents(schedule_c_qbi),
        schedule_e_qbi=_cents(schedule_e_qbi),
        k1_qbi=_cents(k1_qbi),
    )
