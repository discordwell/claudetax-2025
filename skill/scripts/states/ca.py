"""California state plugin.

CA is one of 10 states tenforty supports directly via OpenTaxSolver. CP4
verification confirmed the state pass-through works:

    tenforty.evaluate_return(
        year=2025, state='CA', filing_status='Single',
        w2_income=65000, standard_or_itemized='Standard',
    )
    -> state_total_tax=1975.0, state_tax_bracket=8.0,
       state_taxable_income=59294.0, state_adjusted_gross_income=65000.0,
       state_effective_tax_rate=3.6

This plugin wraps that: it reuses the calc engine's `_to_tenforty_input`
marshaling (so CA sees the same income/deduction numbers the federal calc
does), calls tenforty with `state='CA'`, and converts the state_* floats back
into Decimal for downstream consumers.

Nonresident / part-year handling is a day-based proration TODO — see the
comment inside `compute`. A full nonresident calc requires CA Schedule CA
(540NR) apportionment by income sourcing, which is fan-out work.
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
from skill.scripts.states._hand_rolled_base import (
    state_has_w2_state_rows,
    state_source_schedule_c,
    state_source_wages_from_w2s,
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
# Layer 1: CA Form 540 field dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CA540Fields:
    """Frozen snapshot of CA Form 540 line values, ready for rendering.

    Field names correspond to state_specific keys produced by
    CaliforniaPlugin.compute().
    """

    state_adjusted_gross_income: Decimal = Decimal("0")
    state_taxable_income: Decimal = Decimal("0")
    state_total_tax: Decimal = Decimal("0")
    state_total_tax_resident_basis: Decimal = Decimal("0")
    state_tax_bracket: Decimal = Decimal("0")
    state_effective_tax_rate: Decimal = Decimal("0")
    apportionment_fraction: Decimal = Decimal("1")
    ca_sourced_wages_from_w2_state_rows: Decimal = Decimal("0")
    ca_sourced_schedule_c_net: Decimal = Decimal("0")
    ca_state_rows_present: bool = False


def compute_ca540_fields(state_return: StateReturn) -> CA540Fields:
    """Map StateReturn.state_specific to CA540Fields."""
    ss = state_return.state_specific
    return CA540Fields(
        state_adjusted_gross_income=ss.get("state_adjusted_gross_income", Decimal("0")),
        state_taxable_income=ss.get("state_taxable_income", Decimal("0")),
        state_total_tax=ss.get("state_total_tax", Decimal("0")),
        state_total_tax_resident_basis=ss.get("state_total_tax_resident_basis", Decimal("0")),
        state_tax_bracket=ss.get("state_tax_bracket", Decimal("0")),
        state_effective_tax_rate=ss.get("state_effective_tax_rate", Decimal("0")),
        apportionment_fraction=ss.get("apportionment_fraction", Decimal("1")),
        ca_sourced_wages_from_w2_state_rows=ss.get("ca_sourced_wages_from_w2_state_rows", Decimal("0")),
        ca_sourced_schedule_c_net=ss.get("ca_sourced_schedule_c_net", Decimal("0")),
        ca_state_rows_present=ss.get("ca_state_rows_present", False),
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

    TODO: a real nonresident CA calculation uses Schedule CA (540NR) with
    income-specific sourcing (wages sourced to work state, investment income
    sourced to domicile, etc.) rather than a flat day ratio. Day-based
    proration is a first-order approximation; fan-out will tighten this with
    the real 540NR logic.
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
class CaliforniaPlugin:
    """State plugin for California.

    Wraps tenforty/OpenTaxSolver for the resident case and day-proration for
    nonresident / part-year. Starting point is federal AGI (with CA
    additions/subtractions via Schedule CA, which tenforty handles internally
    for the resident 540 path).
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Reuse the federal marshaling so CA sees exactly the same numbers
        # the federal calc did — do NOT duplicate that logic here.
        tf_input = _to_tenforty_input(return_)

        tf_result = tenforty.evaluate_return(
            year=tf_input.year,
            state="CA",
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
        # cents) so 8.0 and 3.6 stay precise.
        state_bracket = _d(tf_result.state_tax_bracket)
        state_eff_rate = _d(tf_result.state_effective_tax_rate)

        # Wave 6: real Schedule CA (540NR) scaffolding. When the filer is
        # not a CA resident AND at least one W-2 carries a state_rows
        # entry for CA, re-compute the state tax on the CA-sourced wages
        # (W-2 box 16 sum) as if they were the full w2_income. This
        # matches the 540NR Schedule CA-NR pattern: income items are
        # sourced to CA via their state-specific columns, then the tax
        # is computed on that sourced base. For the all-wage case this
        # simplifies to "tenforty on the sourced wage sum." When no state
        # rows are present, fall back to the legacy day-proration path.
        ca_state_rows_present = state_has_w2_state_rows(return_, "CA")
        ca_sourced_wages = state_source_wages_from_w2s(return_, "CA")
        ca_sourced_se = state_source_schedule_c(return_, "CA")

        if residency == ResidencyStatus.RESIDENT:
            fraction = Decimal("1")
            state_tax_apportioned = state_tax_full
        elif ca_state_rows_present:
            tf_sourced = tenforty.evaluate_return(
                year=tf_input.year,
                state="CA",
                filing_status=tf_input.filing_status,
                w2_income=float(ca_sourced_wages),
                taxable_interest=0.0,
                qualified_dividends=0.0,
                ordinary_dividends=0.0,
                short_term_capital_gains=0.0,
                long_term_capital_gains=0.0,
                self_employment_income=float(ca_sourced_se),
                rental_income=0.0,
                schedule_1_income=0.0,
                standard_or_itemized=tf_input.standard_or_itemized,
                itemized_deductions=tf_input.itemized_deductions,
                num_dependents=tf_input.num_dependents,
            )
            state_tax_apportioned = _cents(tf_sourced.state_total_tax)
            fraction = Decimal("1")  # no day-proration under sourcing
        else:
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
            "ca_sourced_wages_from_w2_state_rows": ca_sourced_wages,
            "ca_sourced_schedule_c_net": ca_sourced_se,
            "ca_state_rows_present": ca_state_rows_present,
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
        """Split canonical income into CA-source vs non-CA-source.

        Residents: everything is CA-source. Nonresident / part-year: prorate
        each category by days_in_state / 365.

        TODO: CA actually sources each income type differently (wages to the
        work location, interest/dividends to the taxpayer's domicile, rental
        to the property state, etc.). Day-based proration is the shared
        first-cut across all fan-out state plugins; refine in follow-up.
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

        # Wave 6: prefer real W-2 state-row sourcing when the filer is
        # not a CA resident AND at least one W-2 carries a CA state row.
        # Fall back to day-proration when no state rows are present.
        if residency == ResidencyStatus.RESIDENT:
            ca_wages = _cents(wages)
            ca_se = _cents(se_net)
        elif state_has_w2_state_rows(return_, "CA"):
            ca_wages = state_source_wages_from_w2s(return_, "CA")
            ca_se = state_source_schedule_c(return_, "CA")
        else:
            ca_wages = _cents(wages * fraction)
            ca_se = _cents(se_net * fraction)

        return IncomeApportionment(
            state_source_wages=ca_wages,
            state_source_interest=_cents(interest * fraction),
            state_source_dividends=_cents(ord_div * fraction),
            state_source_capital_gains=_cents(capital_gains * fraction),
            state_source_self_employment=ca_se,
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
        _WIDGET_MAP = _REF / "ca-540-acroform-map.json"
        _SOURCE_PDF = _REF / "state_forms" / "ca_540.pdf"

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

        out_path = out_dir / "ca_540.pdf"
        fill_acroform_pdf(_SOURCE_PDF, widget_values, out_path)
        return [out_path]

    def form_ids(self) -> list[str]:
        return ["CA Form 540"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = CaliforniaPlugin(
    meta=StatePluginMeta(
        code="CA",
        name="California",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://www.ftb.ca.gov/",
        free_efile_url="https://www.ftb.ca.gov/file/ways-to-file/online/calfile/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes="Uses tenforty/OpenTaxSolver. Verified in CP4.",
    )
)
