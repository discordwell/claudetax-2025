"""New Jersey state plugin.

NJ is one of the 10 states tenforty supports directly via OpenTaxSolver.
Verified against tenforty for TY2025 Single / $65k W-2 / Standard:

    tenforty.evaluate_return(
        year=2025, state='NJ', filing_status='Single',
        w2_income=65000, standard_or_itemized='Standard',
    )
    -> state_total_tax=2042.0, state_tax_bracket=5.5,
       state_taxable_income=64000.0, state_adjusted_gross_income=65000.0,
       state_effective_tax_rate=3.2

This plugin wraps that: it reuses the calc engine's `_to_tenforty_input`
marshaling (so NJ sees the same income/deduction numbers the federal calc
does), calls tenforty with `state='NJ'`, and converts the state_* floats
back into Decimal for downstream consumers.

Reciprocity note: NJ has exactly one bilateral reciprocity partner —
Pennsylvania. A PA resident working in NJ (or vice versa) can file an
employer exemption certificate so that withholding only runs to the home
state. The PA-side exemption form for NJ residents is REV-419. This is
load-bearing for the skill's multi-state routing logic.

Nonresident / part-year handling is day-based proration — NJ actually
requires Form NJ-1040NR with specific sourcing rules (wages to work state,
investment income to domicile, etc.); day-proration is the first-cut
approximation shared with the other fan-out state plugins.
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

    TODO: a real nonresident NJ calculation uses Form NJ-1040NR with
    income-specific sourcing (wages sourced to work state, investment income
    sourced to domicile, etc.) rather than a flat day ratio. Day-based
    proration is a first-order approximation; fan-out will tighten this with
    the real NJ-1040NR logic.
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
class NewJerseyPlugin:
    """State plugin for New Jersey.

    Wraps tenforty/OpenTaxSolver for the resident case and day-proration for
    nonresident / part-year. Starting point is federal AGI (with NJ
    additions/subtractions, which tenforty handles internally for the
    resident NJ-1040 path).
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Reuse the federal marshaling so NJ sees exactly the same numbers
        # the federal calc did — do NOT duplicate that logic here.
        tf_input = _to_tenforty_input(return_)

        tf_result = tenforty.evaluate_return(
            year=tf_input.year,
            state="NJ",
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
        # cents) so 5.5 and 3.2 stay precise.
        state_bracket = _d(tf_result.state_tax_bracket)
        state_eff_rate = _d(tf_result.state_effective_tax_rate)

        # Apportion tax for nonresident / part-year. TODO: replace with real
        # NJ-1040NR income-source apportionment in fan-out.
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
        """Split canonical income into NJ-source vs non-NJ-source.

        Residents: everything is NJ-source. Nonresident / part-year: prorate
        each category by days_in_state / 365.

        TODO: NJ actually sources each income type differently on the
        NJ-1040NR (wages to the work location, interest/dividends to the
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

        # Schedule C net profit — reuse engine helpers via _to_tenforty_input
        # would double-call tenforty, so recompute here lightly.
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
        # NJ-1040 fillable PDF (https://www.nj.gov/treasury/taxation/pdf/
        # current/1040.pdf) uses digit-by-digit entry for all monetary
        # amounts: each dollar line has 8+ individual single-character text
        # widgets (one per digit). The form has 844 widget annotations,
        # almost all of which are these single-digit cells with garbled
        # names (e.g. "Text100", "undefined_37", "4036y54ethdf%%^87").
        #
        # Implementing a reliable renderer requires mapping each digit
        # position across ~70 line items, handling right-to-left fill, and
        # dealing with the non-semantic widget names. This is deferred to a
        # dedicated NJ rendering sprint. The compute() method correctly
        # produces state_specific data for downstream consumers.
        return []

    def form_ids(self) -> list[str]:
        return ["NJ Form NJ-1040"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = NewJerseyPlugin(
    meta=StatePluginMeta(
        code="NJ",
        name="New Jersey",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://www.nj.gov/treasury/taxation/",
        free_efile_url="https://www.njportal.com/Taxation/NJ1040/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        reciprocity_partners=("PA",),  # NJ's only bilateral reciprocity
        supported_tax_years=(2025,),
        notes=(
            "Uses tenforty/OpenTaxSolver for NJ state calc. "
            "Note: NJ-PA border commuters can file exemption via PA DOE form REV-419."
        ),
    )
)
