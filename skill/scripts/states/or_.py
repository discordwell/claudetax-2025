# Filename has a trailing underscore because `or` is a Python reserved keyword
# and `from skill.scripts.states import or` would be a syntax error. All other
# state modules use the bare 2-letter code (ca.py, ny.py, etc.); OR is the sole
# exception in the package.
"""Oregon state plugin.

OR is one of the ~10 states tenforty supports directly via OpenTaxSolver. A
reference tenforty call confirms the state pass-through works:

    tenforty.evaluate_return(
        year=2025, state='OR', filing_status='Single',
        w2_income=65000, standard_or_itemized='Standard',
    )
    -> state_total_tax=4370.00, state_tax_bracket=8.8,
       state_taxable_income=56410.00, state_adjusted_gross_income=65000.00,
       state_effective_tax_rate=8.2

OR uses graduated brackets. TY2025 Single-filer brackets (per SmartAsset's
Oregon tax calculator summary of the current OR-40 tables at
https://smartasset.com/taxes/oregon-tax-calculator, cross-referenced against
the Oregon Department of Revenue's PIT page at
https://www.oregon.gov/dor/programs/individuals/Pages/PIT.aspx):

    4.75%  on taxable income from      $0  to   $4,400
    6.75%  on taxable income from   $4,400 to  $11,050
    8.75%  on taxable income from  $11,050 to $125,000
    9.90%  on taxable income over $125,000

(MFJ/HoH double the threshold amounts. Oregon's "kicker" credit and
local taxes are not modeled here — tenforty computes the base OR-40 tax,
which is what we surface.)

OR starts from federal TAXABLE INCOME (not federal AGI), so
StateStartingPoint.FEDERAL_TAXABLE_INCOME. Per the tenforty reference call
above, tenforty reports state_taxable_income = 56,410 = 65,000 (AGI)
- 8,590 (OR standard deduction + personal exemption chain), which is what
OR-40 line 19 should land on for a Single filer with no additions or
subtractions.

OR has no bilateral reciprocity agreements (verified: no "OR" entry in
skill/reference/state-reciprocity.json). A taxpayer who lives in OR but
works in WA (or vice versa) does not benefit from any reciprocity; OR
residents are taxed on all worldwide income and claim credit on OR-40
Schedule OR-ASC for tax paid to other states.

OR participates in the IRS Fed/State MeF program; individual e-filing is
routed through approved commercial software — hence
SubmissionChannel.FED_STATE_PIGGYBACK.

Nonresident / part-year handling is day-based proration for now. A real
nonresident OR calc uses Form OR-40-N with income-source apportionment
(wages sourced to work location, investment income to domicile, etc.).
That refinement is fan-out follow-up; this plugin is the first cut.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import tenforty

from skill.scripts.calc.engine import _to_tenforty_input
from skill.scripts.models import (
    CanonicalReturn,
    ResidencyStatus,
    StateReturn,
)
from skill.scripts.states._plugin_api import (
    FederalTotals,
    IncomeApportionment,
    StatePlugin,
    StatePluginMeta,
    StateStartingPoint,
    SubmissionChannel,
)


_CENTS = Decimal("0.01")


def _d(v: Any) -> Decimal:
    """Coerce a tenforty-returned float (or None) to Decimal."""
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _cents(v: Any) -> Decimal:
    """Decimal with 2 decimal places, half-up."""
    return _d(v).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _apportionment_fraction(
    residency: ResidencyStatus, days_in_state: int
) -> Decimal:
    """Days-based apportionment for nonresident / part-year.

    Residents get 1.0 (full state tax). Nonresidents and part-year residents
    are prorated by days_in_state / 365. Clamped to [0, 1].

    TODO: a real nonresident OR calculation uses Form OR-40-N with
    income-specific sourcing (wages sourced to work state, investment income
    sourced to domicile, rental to property state, etc.) rather than a flat
    day ratio. Day-based proration is a first-order approximation; fan-out
    will tighten this with the real OR-40-N logic.
    """
    if residency == ResidencyStatus.RESIDENT:
        return Decimal("1")
    frac = Decimal(days_in_state) / Decimal("365")
    if frac < 0:
        return Decimal("0")
    if frac > 1:
        return Decimal("1")
    return frac


@dataclass(frozen=True)
class OregonPlugin:
    """State plugin for Oregon.

    Wraps tenforty/OpenTaxSolver for the resident case and day-proration for
    nonresident / part-year. Starting point is federal TAXABLE INCOME (OR
    does not derive from AGI like most states; it applies its own additions
    and subtractions to federal taxable income on Form OR-40). Graduated
    brackets: 4.75% / 6.75% / 8.75% / 9.9% for TY2025 (see module docstring
    for thresholds).
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Reuse the federal marshaling so OR sees exactly the same numbers
        # the federal calc did — do NOT duplicate that logic here.
        tf_input = _to_tenforty_input(return_)

        tf_result = tenforty.evaluate_return(
            year=tf_input.year,
            state="OR",
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

        state_agi = _cents(tf_result.state_adjusted_gross_income)
        state_ti = _cents(tf_result.state_taxable_income)
        state_tax_full = _cents(tf_result.state_total_tax)
        # Bracket and effective rate are percentages — keep as Decimal (not
        # cents) so precision is preserved. OR is graduated, so tenforty
        # reports the actual top-bracket rate (8.8 at this income level) and
        # effective rate (8.2 at this income level); we surface faithfully.
        state_bracket = _d(tf_result.state_tax_bracket)
        state_eff_rate = _d(tf_result.state_effective_tax_rate)

        # Apportion tax for nonresident / part-year. TODO: replace with real
        # OR Form OR-40-N income-source apportionment in fan-out.
        fraction = _apportionment_fraction(residency, days_in_state)
        state_tax_apportioned = _cents(state_tax_full * fraction)

        state_specific: dict[str, Any] = {
            "state_adjusted_gross_income": state_agi,
            "state_taxable_income": state_ti,
            "state_total_tax": state_tax_apportioned,
            "state_total_tax_resident_basis": state_tax_full,
            "state_tax_bracket": state_bracket,
            "state_effective_tax_rate": state_eff_rate,
            "apportionment_fraction": fraction,
        }

        return StateReturn(
            state=self.meta.code,
            residency=residency,
            days_in_state=days_in_state,
            state_specific=state_specific,
        )

    def apportion_income(
        self,
        return_: CanonicalReturn,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> IncomeApportionment:
        """Split canonical income into OR-source vs non-OR-source.

        Residents: everything is OR-source. Nonresident / part-year: prorate
        each category by days_in_state / 365.

        TODO: OR actually sources each income type differently on Form
        OR-40-N (wages to the work location, interest/dividends to the
        taxpayer's domicile, rental to the property state, etc.). Day-based
        proration is the shared first-cut across all fan-out state plugins;
        refine in follow-up.
        """
        wages = sum(
            (w2.box1_wages for w2 in return_.w2s), start=Decimal("0")
        )
        interest = sum(
            (f.box1_interest_income for f in return_.forms_1099_int),
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
        st_gain = Decimal("0")
        lt_gain = Decimal("0")
        for form in return_.forms_1099_b:
            for txn in form.transactions:
                gain = txn.proceeds - txn.cost_basis + txn.adjustment_amount
                if txn.is_long_term:
                    lt_gain += gain
                else:
                    st_gain += gain
        capital_gains = st_gain + lt_gain + cap_gain_distr

        # Schedule C net profit / Schedule E rental net — reuse engine helpers.
        # Using _to_tenforty_input here would double-call tenforty, so we pull
        # the helpers directly.
        from skill.scripts.calc.engine import (
            schedule_c_net_profit,
            schedule_e_total_net,
        )
        se_net = sum(
            (schedule_c_net_profit(sc) for sc in return_.schedules_c),
            start=Decimal("0"),
        )
        rental_net = sum(
            (schedule_e_total_net(sched) for sched in return_.schedules_e),
            start=Decimal("0"),
        )

        fraction = _apportionment_fraction(residency, days_in_state)

        return IncomeApportionment(
            state_source_wages=_cents(wages * fraction),
            state_source_interest=_cents(interest * fraction),
            state_source_dividends=_cents(ord_div * fraction),
            state_source_capital_gains=_cents(capital_gains * fraction),
            state_source_self_employment=_cents(se_net * fraction),
            state_source_rental=_cents(rental_net * fraction),
        )

    def render_pdfs(
        self, state_return: StateReturn, out_dir: Path
    ) -> list[Path]:
        # TODO: fan-out follow-up — fill OR Form OR-40 (and OR-40-N / OR-40-P
        # where applicable) using pypdf against the OR DOR's fillable PDFs.
        # The output renderer suite is the right home for this; this plugin
        # returns structured state_specific data that the renderer will
        # consume.
        return []

    def form_ids(self) -> list[str]:
        return ["OR Form OR-40"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = OregonPlugin(
    meta=StatePluginMeta(
        code="OR",
        name="Oregon",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_TAXABLE_INCOME,
        dor_url="https://www.oregon.gov/dor/",
        free_efile_url="https://www.oregon.gov/dor/programs/individuals/Pages/PIT.aspx",
        submission_channel=SubmissionChannel.FED_STATE_PIGGYBACK,
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes="Uses tenforty/OpenTaxSolver for OR state calc. Graduated brackets 4.75/6.75/8.75/9.9% for TY2025.",
    )
)
