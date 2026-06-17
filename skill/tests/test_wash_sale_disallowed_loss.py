"""Regression tests for wash-sale disallowed-loss handling.

A wash-sale disallowed loss (1099-B box 1g) is entered on Form 8949 as a
POSITIVE column-(g) adjustment with code 'W' that *reduces* an otherwise-
reportable loss. It must therefore be folded into the capital gain/loss
EVERYWHERE the gain is aggregated — the calc engine (tenforty marshaling,
``total_income``, ``investment_income``), the federal forms (Form 1040
line 7, Schedule D line 16, Form 8949), and the per-state apportionment
helpers.

Historically the engine, Form 1040 line 7, NIIT, and all 40+ state plugins
inlined ``proceeds - cost_basis + adjustment_amount`` and silently OMITTED
``wash_sale_loss_disallowed``, while Form 8949 / Schedule D included it. The
result was a "silently wrong number": the engine ALLOWED a disallowed loss
(understating tax) and the printed Form 8949 disagreed with Schedule D /
Form 1040 in the same bundle. ``Form1099BTransaction.net_gain_loss()`` is
now the single source of truth. These tests lock that behavior.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from skill.scripts.calc.engine import compute, investment_income, total_income
from skill.scripts.models import CanonicalReturn, Form1099BTransaction
from skill.scripts.output.form_1040 import compute_form_1040_fields
from skill.scripts.output.form_8949 import compute_form_8949_fields
from skill.scripts.output.schedule_d import compute_schedule_d_fields


def _d(s: str) -> Decimal:
    return Decimal(s)


# A single short-term lot: raw loss of -$2,000, of which $1,500 is a
# disallowed wash sale -> net reportable loss -$500 (well within the
# $3,000 capital-loss cap, so it flows dollar-for-dollar to taxable income).
_PROCEEDS = "5000"
_COST = "7000"
_WASH = "1500"
_RAW_LOSS = Decimal("-2000")  # proceeds - cost
_NET_LOSS = Decimal("-500")  # proceeds - cost + wash
_WASH_DEC = Decimal("1500")


def _return_with_1099_b(*, wash: str, wages: str = "90000") -> CanonicalReturn:
    payload = {
        "schema_version": "0.1.0",
        "tax_year": 2025,
        "filing_status": "single",
        "taxpayer": {
            "first_name": "Wash",
            "last_name": "Sale",
            "ssn": "111-22-3333",
            "date_of_birth": "1985-06-01",
        },
        "address": {
            "street1": "1 Broker Way",
            "city": "Boston",
            "state": "MA",
            "zip": "02108",
        },
        "itemize_deductions": False,
        "w2s": [
            {
                "employer_name": "Acme",
                "employer_ein": "12-3456789",
                "box1_wages": wages,
            }
        ],
        "forms_1099_b": [
            {
                "broker_name": "Brokerage Inc",
                "recipient_is_taxpayer": True,
                "transactions": [
                    {
                        "description": "100 sh XYZ",
                        "date_acquired": "2025-01-02",
                        "date_sold": "2025-02-01",
                        "proceeds": _PROCEEDS,
                        "cost_basis": _COST,
                        "wash_sale_loss_disallowed": wash,
                        "is_long_term": False,
                        "basis_reported_to_irs": True,
                    }
                ],
            }
        ],
    }
    return CanonicalReturn.model_validate(payload)


# ---------------------------------------------------------------------------
# Model-level: net_gain_loss() is the single source of truth
# ---------------------------------------------------------------------------


def test_model_net_gain_loss_includes_wash_sale() -> None:
    txn = Form1099BTransaction(
        description="x",
        date_sold=dt.date(2025, 2, 1),
        proceeds=_d(_PROCEEDS),
        cost_basis=_d(_COST),
        wash_sale_loss_disallowed=_d(_WASH),
        is_long_term=False,
    )
    assert txn.net_gain_loss() == _NET_LOSS


def test_model_net_gain_loss_without_wash_sale_is_raw() -> None:
    txn = Form1099BTransaction(
        description="x",
        date_sold=dt.date(2025, 2, 1),
        proceeds=_d(_PROCEEDS),
        cost_basis=_d(_COST),
        is_long_term=True,
    )
    assert txn.net_gain_loss() == _RAW_LOSS


def test_model_net_gain_loss_combines_wash_and_other_adjustment() -> None:
    # Both column-(g) adjustments stack: -2000 + 1500 (W) + 100 (other) = -400.
    txn = Form1099BTransaction(
        description="x",
        date_sold=dt.date(2025, 2, 1),
        proceeds=_d(_PROCEEDS),
        cost_basis=_d(_COST),
        wash_sale_loss_disallowed=_d(_WASH),
        adjustment_amount=_d("100"),
        is_long_term=False,
    )
    assert txn.net_gain_loss() == Decimal("-400")


# ---------------------------------------------------------------------------
# Engine-level: total_income / investment_income add back the wash sale
# ---------------------------------------------------------------------------


def test_total_income_adds_back_wash_sale() -> None:
    with_wash = total_income(_return_with_1099_b(wash=_WASH))
    no_wash = total_income(_return_with_1099_b(wash="0"))
    assert with_wash - no_wash == _WASH_DEC


def test_investment_income_adds_back_wash_sale() -> None:
    ret = _return_with_1099_b(wash=_WASH)
    # The 1099-B lot is the only investment income on this return.
    assert investment_income(ret) == _NET_LOSS
    no_wash = investment_income(_return_with_1099_b(wash="0"))
    assert investment_income(ret) - no_wash == _WASH_DEC


# ---------------------------------------------------------------------------
# Cross-form consistency: 8949 == Schedule D line 16 == Form 1040 line 7
# ---------------------------------------------------------------------------


def test_forms_agree_with_wash_sale() -> None:
    ret = compute(_return_with_1099_b(wash=_WASH))

    f8949 = compute_form_8949_fields(ret)
    sched_d = compute_schedule_d_fields(ret)
    f1040 = compute_form_1040_fields(ret)

    total_8949 = sum((p.total_gain_loss for p in f8949.pages), start=Decimal("0"))
    assert total_8949 == _NET_LOSS
    assert sched_d.line_16_total_gain_loss == _NET_LOSS
    assert f1040.line_7_capital_gain_or_loss == _NET_LOSS


def test_forms_agree_baseline_without_wash_sale() -> None:
    ret = compute(_return_with_1099_b(wash="0"))

    f8949 = compute_form_8949_fields(ret)
    sched_d = compute_schedule_d_fields(ret)
    f1040 = compute_form_1040_fields(ret)

    total_8949 = sum((p.total_gain_loss for p in f8949.pages), start=Decimal("0"))
    assert total_8949 == _RAW_LOSS
    assert sched_d.line_16_total_gain_loss == _RAW_LOSS
    assert f1040.line_7_capital_gain_or_loss == _RAW_LOSS


# ---------------------------------------------------------------------------
# End-to-end: the disallowed loss is no longer subtracted from tax
# ---------------------------------------------------------------------------


def test_disallowed_loss_increases_taxable_income_and_tax() -> None:
    with_wash = compute(_return_with_1099_b(wash=_WASH))
    no_wash = compute(_return_with_1099_b(wash="0"))

    # The $1,500 disallowed loss is no longer netted against income, so
    # taxable income is exactly $1,500 higher (loss stays within the cap).
    assert (
        with_wash.computed.taxable_income - no_wash.computed.taxable_income
        == _WASH_DEC
    )
    # More taxable income => strictly more tax.
    assert with_wash.computed.total_tax > no_wash.computed.total_tax
