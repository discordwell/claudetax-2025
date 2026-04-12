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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from skill.scripts.models import CanonicalReturn

__all__ = [
    "CENT",
    "d",
    "cents",
    "day_prorate",
    "GraduatedBracket",
    "graduated_tax",
    "state_source_wages_from_w2s",
    "state_has_w2_state_rows",
    "state_source_schedule_c",
    "state_source_rental",
    "sourced_or_prorated_wages",
    "sourced_or_prorated_schedule_c",
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


# ---------------------------------------------------------------------------
# Real per-state income sourcing from W-2 state rows and Schedule C metadata
# ---------------------------------------------------------------------------


def state_has_w2_state_rows(
    canonical: "CanonicalReturn", state_code: str
) -> bool:
    """True iff any W-2 has a state_rows[] entry matching ``state_code``.

    Used by state plugins to pick between the real W-2 state-row sourcing
    path (when True) and the day-proration fallback (when False).
    """
    for w2 in canonical.w2s:
        for row in w2.state_rows:
            if row.state == state_code:
                return True
    return False


def state_source_wages_from_w2s(
    canonical: "CanonicalReturn", state_code: str
) -> Decimal:
    """Sum every W-2 state row whose ``state`` matches ``state_code``.

    This is the real per-state wage sourcing path: when a W-2 carries
    Copy 2 state rows (box 15 / 16 / 17), the ``state_wages`` field is
    the employer-reported amount sourced to that state — the exact number
    a state nonresident / part-year return is supposed to tax. This
    helper is the intended replacement for ``day_prorate(box1_wages,
    days_in_state)`` across every hand-rolled plugin.

    Callers should fall back to ``day_prorate`` (the "unknown sourcing"
    path) when this helper returns Decimal("0.00") AND the W-2 list has
    no state_rows for ``state_code`` — that's the legacy behavior for
    W-2s that were ingested before state-row tracking landed, or W-2s
    whose Copy 2 page was not scanned. Use ``state_has_w2_state_rows``
    to disambiguate.

    Parameters
    ----------
    canonical
        The full CanonicalReturn.
    state_code
        Two-letter USPS state code (e.g. "CA", "NY", "PA").

    Returns
    -------
    Decimal
        Sum of matching ``state_wages`` values, quantized to the cent.
        Returns Decimal("0.00") if no matching rows are present.
    """
    total = Decimal("0")
    for w2 in canonical.w2s:
        for row in w2.state_rows:
            if row.state == state_code:
                total += d(row.state_wages)
    return cents(total)


def sourced_or_prorated_wages(
    canonical: "CanonicalReturn",
    state_code: str,
    full_year_wages: Decimal | int | float,
    days_in_state: int,
    total_days: int = 365,
) -> Decimal:
    """Wave 6 unified wage-sourcing primitive for hand-rolled plugins.

    Returns the W-2 state-row sourced wage amount for ``state_code``
    when at least one W-2 carries a matching row; otherwise falls back
    to ``day_prorate(full_year_wages, days_in_state)``.

    Used from each hand-rolled plugin's ``apportion_income`` to keep
    the state-row / day-prorate branching logic in one place.
    """
    if state_has_w2_state_rows(canonical, state_code):
        return state_source_wages_from_w2s(canonical, state_code)
    return day_prorate(full_year_wages, days_in_state, total_days)


def sourced_or_prorated_schedule_c(
    canonical: "CanonicalReturn",
    state_code: str,
    full_year_se_net: Decimal | int | float,
    days_in_state: int,
    total_days: int = 365,
) -> Decimal:
    """Wave 6 unified Schedule C sourcing primitive.

    Returns the sum of Schedule C net profit for businesses whose
    ``business_location_state`` matches ``state_code``. When NO Schedule
    C business is sourced to the state (either because the field is
    unset or no match), falls back to day-proration of
    ``full_year_se_net``.
    """
    sourced = state_source_schedule_c(canonical, state_code)
    if sourced != Decimal("0.00"):
        return sourced
    # Even an explicit zero-profit C sourced to the state returns 0.00,
    # which equals the day-proration fallback at zero days. Otherwise
    # day-prorate the full-year SE amount as the ambiguous-sourcing
    # fallback.
    any_sc_sourced = any(
        sc.business_location_state == state_code
        for sc in canonical.schedules_c
    )
    if any_sc_sourced:
        return sourced
    return day_prorate(full_year_se_net, days_in_state, total_days)


def state_source_rental(
    canonical: "CanonicalReturn", state_code: str
) -> Decimal:
    """Sum Schedule E property net income for properties located in ``state_code``.

    A Schedule E rental property is sourced to the state where the
    property is physically located, using the ``address.state`` field on
    each ``ScheduleEProperty``. This is the standard rule across all
    states: rental income from real property is sourced to the state
    where the property sits, regardless of the taxpayer's domicile.

    Parameters
    ----------
    canonical
        The full CanonicalReturn.
    state_code
        Two-letter USPS state code (e.g. "CA", "NY", "PA").

    Returns
    -------
    Decimal
        Sum of net rental income for properties in ``state_code``,
        quantized to the cent. Returns Decimal("0.00") if no properties
        match.
    """
    from skill.scripts.calc.engine import schedule_e_property_net

    total = Decimal("0")
    for sched in canonical.schedules_e:
        for prop in sched.properties:
            if prop.address.state == state_code:
                total += schedule_e_property_net(prop)
    return cents(total)


def state_source_schedule_c(
    canonical: "CanonicalReturn", state_code: str
) -> Decimal:
    """Sum Schedule C net profit for businesses sourced to ``state_code``.

    A Schedule C business is sourced to a state via its
    ``business_location_state`` field. When that field is unset, the
    business is considered ambiguously sourced and this helper does NOT
    include it — callers should fall back to day_prorate for the
    ambiguous-sourcing path.

    Net profit is the calc-engine primitive (line 1 gross + line 6 other
    income - line 2 returns - line 4 COGS - line 28 total expenses -
    line 30 home office), reused from ``calc.engine.schedule_c_net_profit``.
    """
    # Imported locally to avoid a module-scope circular import — calc.engine
    # depends on plenty of skill.scripts modules transitively.
    from skill.scripts.calc.engine import schedule_c_net_profit

    total = Decimal("0")
    for sc in canonical.schedules_c:
        if sc.business_location_state == state_code:
            total += schedule_c_net_profit(sc)
    return cents(total)
