"""TY2025 tax constants loader (OBBBA-adjusted).

Reads skill/reference/ty2025-constants.json and exposes typed accessors.
The JSON file is the canonical data; this module is a thin typed wrapper.

Every number flows from the JSON file; never hardcode values here. If a new
number is needed, add it to the JSON file first (with a source), then add a
typed accessor here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

FilingStatus = Literal["single", "mfj", "mfs", "hoh", "qss"]

_CONSTANTS_PATH = (
    Path(__file__).resolve().parent.parent.parent / "reference" / "ty2025-constants.json"
)


@lru_cache(maxsize=1)
def _raw() -> dict:
    with _CONSTANTS_PATH.open() as fh:
        return json.load(fh)


def tax_year() -> int:
    return _raw()["meta"]["tax_year"]


def is_obbba_adjusted() -> bool:
    return _raw()["meta"]["obbba_adjusted"]


# ---------------------------------------------------------------------------
# Standard deduction
# ---------------------------------------------------------------------------


def standard_deduction(status: FilingStatus) -> int:
    return _raw()["standard_deduction"][status]


def additional_standard_deduction_65_or_blind(status: FilingStatus) -> int:
    if status in ("mfj", "mfs", "qss"):
        return _raw()["standard_deduction"]["additional_65_or_blind_mfj_mfs_qss"]
    return _raw()["standard_deduction"]["additional_65_or_blind_single_hoh"]


def obbba_senior_deduction() -> dict:
    """Returns the OBBBA new senior deduction params."""
    return _raw()["standard_deduction"]["senior_deduction_obbba"]


# ---------------------------------------------------------------------------
# Ordinary tax brackets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bracket:
    rate: float
    upper: float | None  # None means open-ended (top bracket)


def ordinary_brackets(status: FilingStatus) -> list[Bracket]:
    raw = _raw()["ordinary_brackets"][status]
    return [Bracket(rate=b["rate"], upper=b["upper"]) for b in raw]


# ---------------------------------------------------------------------------
# Capital gains brackets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapitalGainsBrackets:
    zero_rate_upper: int
    fifteen_rate_upper: int


def capital_gains_brackets(status: FilingStatus) -> CapitalGainsBrackets:
    raw = _raw()["capital_gains_brackets"][status]
    return CapitalGainsBrackets(
        zero_rate_upper=raw["zero_rate_upper"],
        fifteen_rate_upper=raw["fifteen_rate_upper"],
    )


# ---------------------------------------------------------------------------
# Payroll and self-employment
# ---------------------------------------------------------------------------


def social_security_wage_base() -> int:
    return _raw()["payroll_taxes"]["social_security_wage_base"]


def schedule_se_filing_floor() -> int:
    return _raw()["schedule_se"]["filing_floor_net_se_earnings"]


def schedule_se_combined_rate() -> float:
    return _raw()["schedule_se"]["combined_rate"]


def additional_medicare_tax_threshold(status: FilingStatus) -> int:
    amt = _raw()["payroll_taxes"]["additional_medicare_tax"]
    return amt[f"threshold_{status}"]


def niit_threshold(status: FilingStatus) -> int:
    niit = _raw()["payroll_taxes"]["niit"]
    return niit[f"threshold_{status}"]


# ---------------------------------------------------------------------------
# QBI
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QBIParams:
    rate: float
    phase_in_threshold: int
    phase_in_width: int
    full_phase_in_complete: int


def qbi_params(status: FilingStatus) -> QBIParams:
    raw = _raw()["qbi_deduction"]
    if status == "mfj":
        return QBIParams(
            rate=raw["rate"],
            phase_in_threshold=raw["phase_in_threshold_mfj"],
            phase_in_width=raw["phase_in_width_mfj"],
            full_phase_in_complete=raw["full_phase_in_complete_mfj"],
        )
    return QBIParams(
        rate=raw["rate"],
        phase_in_threshold=raw["phase_in_threshold_single_hoh_mfs_qss"],
        phase_in_width=raw["phase_in_width_single_hoh_mfs_qss"],
        full_phase_in_complete=raw["full_phase_in_complete_single_hoh_mfs_qss"],
    )


# ---------------------------------------------------------------------------
# Child Tax Credit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CTCParams:
    amount_per_child: int
    refundable_max_actc: int
    phase_out_start: int
    phase_out_reduction_per_1000_over: int


def ctc_params(status: FilingStatus) -> CTCParams:
    raw = _raw()["child_tax_credit"]
    phase_out_start = (
        raw["phase_out_start_mfj_qss"]
        if status in ("mfj", "qss")
        else raw["phase_out_start_single_hoh_mfs"]
    )
    return CTCParams(
        amount_per_child=raw["amount_per_qualifying_child"],
        refundable_max_actc=raw["refundable_portion_max_actc"],
        phase_out_start=phase_out_start,
        phase_out_reduction_per_1000_over=raw["phase_out_reduction_per_1000_over_threshold"],
    )


# ---------------------------------------------------------------------------
# EITC
# ---------------------------------------------------------------------------


def eitc_max_credit(qualifying_children: int) -> int:
    key = "3_or_more" if qualifying_children >= 3 else str(qualifying_children)
    return _raw()["eitc"]["max_credit_by_qualifying_children"][key]


def eitc_agi_limit(qualifying_children: int, status: FilingStatus) -> int:
    key = "3_or_more" if qualifying_children >= 3 else str(qualifying_children)
    if status == "mfj":
        return _raw()["eitc"]["agi_limit_mfj_by_qualifying_children"][key]
    return _raw()["eitc"]["agi_limit_single_hoh_qss_by_qualifying_children"][key]


def eitc_investment_income_disqualifier() -> int:
    return _raw()["eitc"]["investment_income_disqualifier"]


def eitc_phase_in_rate(qualifying_children: int) -> float:
    key = "3_or_more" if qualifying_children >= 3 else str(qualifying_children)
    return _raw()["eitc"]["phase_in_rate_by_qualifying_children"][key]


def eitc_earned_income_for_max_credit(qualifying_children: int) -> int:
    key = "3_or_more" if qualifying_children >= 3 else str(qualifying_children)
    return _raw()["eitc"]["earned_income_for_max_credit_by_qualifying_children"][key]


def eitc_phase_out_rate(qualifying_children: int) -> float:
    key = "3_or_more" if qualifying_children >= 3 else str(qualifying_children)
    return _raw()["eitc"]["phase_out_rate_by_qualifying_children"][key]


def eitc_phase_out_begin(qualifying_children: int, status: FilingStatus) -> int:
    key = "3_or_more" if qualifying_children >= 3 else str(qualifying_children)
    if status == "mfj":
        return _raw()["eitc"]["phase_out_begin_mfj_by_qualifying_children"][key]
    return _raw()["eitc"]["phase_out_begin_non_mfj_by_qualifying_children"][key]


# ---------------------------------------------------------------------------
# Retirement
# ---------------------------------------------------------------------------


def retirement_limits() -> dict:
    """Returns the full retirement-limits dict. Typed accessors below for common cases."""
    return _raw()["retirement_contribution_limits"]


def elective_deferral_401k() -> int:
    return _raw()["retirement_contribution_limits"]["elective_deferral_401k_403b_457_tsp"]


def ira_contribution_limit() -> int:
    return _raw()["retirement_contribution_limits"]["ira_traditional_or_roth_combined"]


# ---------------------------------------------------------------------------
# HSA
# ---------------------------------------------------------------------------


def hsa_limit(coverage: Literal["self", "family"]) -> int:
    raw = _raw()["hsa"]
    return (
        raw["contribution_limit_self_only"]
        if coverage == "self"
        else raw["contribution_limit_family"]
    )


# ---------------------------------------------------------------------------
# Information returns (1099-K threshold etc.)
# ---------------------------------------------------------------------------


def form_1099k_thresholds() -> tuple[int, int]:
    """Returns (dollar_threshold, transaction_count_threshold). Both must be exceeded."""
    raw = _raw()["information_returns"]["form_1099k_threshold_obbba_reverted"]
    return raw["dollar_threshold"], raw["transaction_count_threshold"]


# ---------------------------------------------------------------------------
# TODO list — what still needs to be researched
# ---------------------------------------------------------------------------


def pending_research() -> list[str]:
    """Numbers that still need a targeted research sub-agent before calc modules can use them."""
    return _raw()["_todo"]["pending"]
