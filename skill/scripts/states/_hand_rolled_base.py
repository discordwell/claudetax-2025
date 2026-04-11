"""Shared helpers for hand-rolled state plugins.

Wave 4 established a pattern of writing state plugins from DOR primary
sources when tenforty's default OTS backend lacked TY2025 form configs.
Each such plugin (CT, KS, KY, MN, MD, and several wave-3 hand-rolled
plugins) reimplemented the same small set of primitives: Decimal
coercion, day-proration for nonresident/part-year returns, sum-of-tiers
graduated bracket math, and a few structural conventions.

This module exposes those primitives so wave-5 hand-rolled plugins can
import them instead of copy-pasting. **Existing plugins are NOT being
refactored**; they already work and are regression-locked. New plugins
should prefer these helpers.

Usage
-----

    from skill.scripts.states._hand_rolled_base import (
        d, cents, day_prorate, graduated_tax, GraduatedBracket,
    )

    brackets: tuple[GraduatedBracket, ...] = (
        GraduatedBracket(low=Decimal("0"),      high=Decimal("10000"), rate=Decimal("0.02")),
        GraduatedBracket(low=Decimal("10000"),  high=Decimal("50000"), rate=Decimal("0.04")),
        GraduatedBracket(low=Decimal("50000"),  high=None,             rate=Decimal("0.06")),
    )
    tax = graduated_tax(taxable_income=Decimal("65000"), brackets=brackets)
    # tax = 2%*10k + 4%*40k + 6%*15k = 200 + 1600 + 900 = 2700

Design notes
------------

* **Pure functions only.** No side effects, no I/O, no state. Easy to
  unit test in isolation.
* **Decimal everywhere.** Never ``float``. Never implicit conversion.
* **ROUND_HALF_UP.** Matches the IRS rounding convention for tax
  tables and Schedule instructions.
* **Frozen dataclasses** for bracket rows so plugins can't accidentally
  mutate a shared bracket table at runtime.
* **No Protocol conformance** here — this module provides pieces, not
  a full StatePlugin implementation. Plugins still own their
  ``compute``, ``apportion_income``, ``render_pdfs``, and ``form_ids``
  methods.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

__all__ = [
    "CENT",
    "d",
    "cents",
    "day_prorate",
    "GraduatedBracket",
    "graduated_tax",
]


# ---------------------------------------------------------------------------
# Decimal coercion primitives
# ---------------------------------------------------------------------------

CENT = Decimal("0.01")
"""Standard money rounding unit. Round half-up to 2 decimals."""


def d(v: Any) -> Decimal:
    """Coerce ``v`` to Decimal deterministically.

    - ``None`` → ``Decimal("0")``
    - ``Decimal`` → returned as-is
    - anything else → ``Decimal(str(v))`` (never ``Decimal(float)``, which
      would leak binary-float precision noise)
    """
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def cents(v: Any) -> Decimal:
    """Coerce to Decimal and round to the cent, half-up.

    Mirrors ``d`` but applies the standard money quantization. Use this
    for any value that will land on a filed form line.
    """
    return d(v).quantize(CENT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Day-proration for nonresident / part-year sourcing
# ---------------------------------------------------------------------------


def day_prorate(
    amount: Decimal | int | float,
    days_in_state: int,
    total_days: int = 365,
) -> Decimal:
    """Prorate an amount by days-in-state / total-days.

    This is the fan-out-stopgap approximation for nonresident / part-year
    income sourcing. Real per-state rules use income-source ratios (e.g.
    NY IT-203, CA 540NR, MN M1NR, KY 740-NP Schedule A), not calendar
    days. Wave-4 hand-rolled plugins universally use day-proration
    pending real sourcing logic; new plugins should do the same and
    document the limitation in their ``_V1_LIMITATIONS``.

    Parameters
    ----------
    amount
        The dollar amount to prorate. Coerced via ``d``.
    days_in_state
        Number of days the filer was resident in the state (0-366).
    total_days
        Denominator — defaults to 365. Leap years pass 366.

    Returns
    -------
    Decimal
        ``amount * days_in_state / total_days``, quantized to the cent.
        When ``days_in_state >= total_days`` (full-year resident), the
        function short-circuits and returns ``cents(amount)`` without
        the division, avoiding Decimal rounding noise on the common
        case.
    """
    if days_in_state >= total_days:
        return cents(amount)
    if days_in_state <= 0:
        return Decimal("0.00")
    ratio = Decimal(days_in_state) / Decimal(total_days)
    return cents(d(amount) * ratio)


# ---------------------------------------------------------------------------
# Graduated bracket math (sum-of-tiers)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GraduatedBracket:
    """One row of a graduated-tax bracket table.

    A bracket applies its ``rate`` to the portion of taxable income that
    falls in the half-open interval ``[low, high)``. The top bracket
    uses ``high=None`` to mean "infinite" (no upper bound).

    Example — TY2025 MN Single brackets (truncated):

        GraduatedBracket(Decimal("0"),     Decimal("32570"),  Decimal("0.0535"))
        GraduatedBracket(Decimal("32570"), Decimal("106990"), Decimal("0.0680"))
        GraduatedBracket(Decimal("106990"),Decimal("198630"), Decimal("0.0785"))
        GraduatedBracket(Decimal("198630"),None,              Decimal("0.0985"))

    Plugins should define their brackets as a frozen ``tuple`` at module
    scope so they can't be mutated at runtime.
    """

    low: Decimal
    high: Decimal | None
    rate: Decimal

    def applies_to(self, taxable_income: Decimal) -> bool:
        """True if any portion of ``taxable_income`` falls in this tier."""
        if taxable_income <= self.low:
            return False
        return True

    def tier_amount(self, taxable_income: Decimal) -> Decimal:
        """Portion of ``taxable_income`` inside this bracket's interval.

        Returns ``0`` if ``taxable_income <= low``. Returns
        ``high - low`` if ``taxable_income >= high``. Otherwise returns
        ``taxable_income - low``.
        """
        if taxable_income <= self.low:
            return Decimal("0")
        if self.high is None:
            return taxable_income - self.low
        if taxable_income >= self.high:
            return self.high - self.low
        return taxable_income - self.low


def graduated_tax(
    taxable_income: Decimal,
    brackets: tuple[GraduatedBracket, ...] | list[GraduatedBracket],
    *,
    round_each_tier: bool = False,
) -> Decimal:
    """Compute graduated tax as the sum of (tier_amount * rate) per bracket.

    Parameters
    ----------
    taxable_income
        State taxable income. Must be non-negative; negative values
        return ``Decimal("0")`` (no state tax on a loss).
    brackets
        Ordered sequence of ``GraduatedBracket``. MUST be sorted by
        ``low`` ascending. The top bracket MUST have ``high=None``.
    round_each_tier
        If True, round each tier's tax to the cent before summing.
        Some state DORs (notably CT) compute per-tier rounded dollars
        in their printed tables, causing small (±$1) differences from
        unrounded sum-then-round. Default ``False`` matches the
        unrounded convention used by most DORs and by tenforty.

    Returns
    -------
    Decimal
        Total tax, quantized to the cent (regardless of
        ``round_each_tier``).
    """
    ti = d(taxable_income)
    if ti <= 0:
        return Decimal("0.00")

    total = Decimal("0")
    for bracket in brackets:
        if not bracket.applies_to(ti):
            break
        tier_tax = bracket.tier_amount(ti) * bracket.rate
        if round_each_tier:
            tier_tax = cents(tier_tax)
        total += tier_tax
    return cents(total)
