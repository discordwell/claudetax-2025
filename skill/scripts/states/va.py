"""Virginia state plugin.

VA is one of the states tenforty supports directly via OpenTaxSolver.
Fan-out verification (Single / $65k W-2 / Standard) confirms the
state pass-through works:

    tenforty.evaluate_return(
        year=2025, state='VA', filing_status='Single',
        w2_income=65000, standard_or_itemized='Standard',
    )
    -> state_total_tax=2366.8, state_tax_bracket=5.8,
       state_taxable_income=45640.0, state_adjusted_gross_income=65000.0,
       state_effective_tax_rate=5.2

This plugin wraps that: it reuses the calc engine's `_to_tenforty_input`
marshaling (so VA sees the same income/deduction numbers the federal calc
does), calls tenforty with `state='VA'`, and converts the state_* floats
back into Decimal for downstream consumers.

Reciprocity: Virginia has bilateral reciprocity agreements with DC, KY,
MD, PA, and WV. A Virginia resident who earns wages only in one of those
states is not subject to that state's income tax on those wages (and
vice versa). The registry and multi-state driver consume
`meta.reciprocity_partners` to route those wages back to the home state.
Both sides of each pair must list the other, so this set is load-bearing
across the plugin suite — tests pin it explicitly.

Nonresident / part-year handling is a days-based proration TODO — see
the comment inside `compute`. A full nonresident calc requires VA
Schedule 763 ADJ / Form 763 apportionment by income sourcing, which is
fan-out follow-up work.
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


# ---------------------------------------------------------------------------
# Layer 1: VA Form 760 field dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VA760Fields:
    """Frozen snapshot of VA Form 760 line values, ready for rendering.

    Field names correspond to state_specific keys produced by
    VirginiaPlugin.compute().
    """

    state_adjusted_gross_income: Decimal = Decimal("0")
    state_taxable_income: Decimal = Decimal("0")
    state_total_tax: Decimal = Decimal("0")
    state_total_tax_resident_basis: Decimal = Decimal("0")
    state_tax_bracket: Decimal = Decimal("0")
    state_effective_tax_rate: Decimal = Decimal("0")
    apportionment_fraction: Decimal = Decimal("1")


def compute_va760_fields(state_return: StateReturn) -> VA760Fields:
    """Map StateReturn.state_specific to VA760Fields."""
    ss = state_return.state_specific
    return VA760Fields(
        state_adjusted_gross_income=ss.get("state_adjusted_gross_income", Decimal("0")),
        state_taxable_income=ss.get("state_taxable_income", Decimal("0")),
        state_total_tax=ss.get("state_total_tax", Decimal("0")),
        state_total_tax_resident_basis=ss.get("state_total_tax_resident_basis", Decimal("0")),
        state_tax_bracket=ss.get("state_tax_bracket", Decimal("0")),
        state_effective_tax_rate=ss.get("state_effective_tax_rate", Decimal("0")),
        apportionment_fraction=ss.get("apportionment_fraction", Decimal("1")),
    )


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

    TODO: a real nonresident VA calculation uses Form 763 with Virginia
    source income / total income ratio (wages sourced to the work state,
    investment income sourced to domicile, etc.) rather than a flat day
    ratio. Day-based proration is a first-order approximation shared with
    the other fan-out state plugins; follow-up work will tighten this
    with the actual 763 sourcing logic.
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
class VirginiaPlugin:
    """State plugin for Virginia.

    Wraps tenforty/OpenTaxSolver for the resident case and day-proration
    for nonresident / part-year. Starting point is federal AGI (with VA
    additions/subtractions — tenforty handles these internally for the
    resident 760 path).
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Reuse the federal marshaling so VA sees exactly the same numbers
        # the federal calc did — do NOT duplicate that logic here.
        tf_input = _to_tenforty_input(return_)

        tf_result = tenforty.evaluate_return(
            year=tf_input.year,
            state="VA",
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
        # Bracket and effective rate are percentages — keep as Decimal
        # (not cents) so 5.8 and 5.2 stay precise.
        state_bracket = _d(tf_result.state_tax_bracket)
        state_eff_rate = _d(tf_result.state_effective_tax_rate)

        # Apportion tax for nonresident / part-year. TODO: replace with
        # real VA Form 763 income-source apportionment in fan-out
        # follow-up.
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
        """Split canonical income into VA-source vs non-VA-source.

        Residents: everything is VA-source. Nonresident / part-year:
        prorate each category by days_in_state / 365.

        TODO: VA actually sources each income type differently (wages to
        the work location, interest/dividends to the taxpayer's domicile,
        rental to the property state, etc.). Day-based proration is the
        shared first-cut across all fan-out state plugins; refine in
        follow-up work with real 763 sourcing rules.
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
            (
                f.box2a_total_capital_gain_distributions
                for f in return_.forms_1099_div
            ),
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

        # Schedule C / E net — reuse engine helpers rather than re-running
        # _to_tenforty_input (which would double-call tenforty).
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
        from skill.scripts.output._acroform_overlay import (
            fill_acroform_pdf,
            format_money,
            load_widget_map,
            fetch_and_verify_source_pdf,
        )

        _REF = Path(__file__).resolve().parents[2] / "reference"
        _WIDGET_MAP = _REF / "va-760-acroform-map.json"
        _SOURCE_PDF = _REF / "state_forms" / "va_760.pdf"

        widget_map = load_widget_map(_WIDGET_MAP)
        fetch_and_verify_source_pdf(
            _SOURCE_PDF, widget_map.source_pdf_url, widget_map.source_pdf_sha256
        )

        ss = state_return.state_specific
        widget_values: dict[str, str] = {}
        for sem_name, widget_name in widget_map.semantic_to_widget.items():
            value = ss.get(sem_name)
            if value is not None:
                if isinstance(value, (Decimal, int, float)):
                    widget_values[widget_name] = format_money(value)
                else:
                    widget_values[widget_name] = str(value)

        out_path = out_dir / "va_760.pdf"
        fill_acroform_pdf(_SOURCE_PDF, widget_values, out_path)
        return [out_path]

    def form_ids(self) -> list[str]:
        return ["VA Form 760"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = VirginiaPlugin(
    meta=StatePluginMeta(
        code="VA",
        name="Virginia",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://www.tax.virginia.gov/",
        free_efile_url="https://www.tax.virginia.gov/free-file",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # Bilateral reciprocity partners — verified against
        # skill/reference/state-reciprocity.json. Both sides of each
        # pair must list the other; regression tests pin this exact set.
        reciprocity_partners=("DC", "KY", "MD", "PA", "WV"),
        supported_tax_years=(2025,),
        notes="Uses tenforty/OpenTaxSolver for VA state calc.",
    )
)
