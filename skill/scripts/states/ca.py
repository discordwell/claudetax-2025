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

Nonresident / part-year handling implements real Schedule CA (540NR)
apportionment by income sourcing:
  - Wages: sourced to CA via W-2 state rows (box 16)
  - Interest/Dividends: sourced to domicile state (NOT CA for nonresidents)
  - Schedule C: sourced to CA if business_location_state == "CA"
  - Schedule E rental: sourced to CA if property address is in CA
  - Capital gains: conservative — not sourced to CA unless explicitly flagged
  - Tax = (CA tax on total income) * (CA-source income / total income)
    This is the 540NR "tax rate on all income applied to CA-source income"
    method.
When no W-2 state rows are present, falls back to legacy day-proration.
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
    state_source_rental,
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
    ca_sourced_rental_net: Decimal = Decimal("0")
    ca_sourced_interest: Decimal = Decimal("0")
    ca_sourced_dividends: Decimal = Decimal("0")
    ca_sourced_capital_gains: Decimal = Decimal("0")
    ca_source_total: Decimal = Decimal("0")
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
        ca_sourced_rental_net=ss.get("ca_sourced_rental_net", Decimal("0")),
        ca_sourced_interest=ss.get("ca_sourced_interest", Decimal("0")),
        ca_sourced_dividends=ss.get("ca_sourced_dividends", Decimal("0")),
        ca_sourced_capital_gains=ss.get("ca_sourced_capital_gains", Decimal("0")),
        ca_source_total=ss.get("ca_source_total", Decimal("0")),
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
    """Days-based apportionment fallback for nonresident / part-year.

    Residents get 1.0 (full state tax). Nonresidents and part-year residents
    are prorated by days_in_state / 365. Clamped to [0, 1].

    This is now a fallback path used only when no W-2 state rows are present
    (i.e., no real sourcing data). The primary nonresident path uses real
    per-category income sourcing via the 540NR tax-rate method.
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

        # Real Schedule CA (540NR) income sourcing for nonresidents.
        # Each income category is sourced to CA per 540NR rules:
        #   - Wages: W-2 state rows with state="CA"
        #   - Interest/Dividends: sourced to domicile (NOT CA for nonresidents)
        #   - Schedule C: business_location_state == "CA"
        #   - Schedule E rental: property address in CA
        #   - Capital gains: conservative — $0 unless explicitly CA-sourced
        ca_state_rows_present = state_has_w2_state_rows(return_, "CA")
        ca_sourced_wages = state_source_wages_from_w2s(return_, "CA")
        ca_sourced_se = state_source_schedule_c(return_, "CA")
        ca_sourced_rental = state_source_rental(return_, "CA")

        if residency == ResidencyStatus.RESIDENT:
            fraction = Decimal("1")
            state_tax_apportioned = state_tax_full
            # For residents, CA-source == total income
            ca_source_total = state_agi
            ca_sourced_interest = _cents(_d(tf_input.taxable_interest))
            ca_sourced_dividends = _cents(_d(tf_input.ordinary_dividends))
            ca_sourced_capital_gains = _cents(
                _d(tf_input.short_term_capital_gains) + _d(tf_input.long_term_capital_gains)
            )
        elif ca_state_rows_present:
            # Real 540NR per-category sourcing with tax-rate method.
            # Interest and dividends are sourced to domicile, not CA.
            ca_sourced_interest = Decimal("0")
            ca_sourced_dividends = Decimal("0")
            ca_sourced_capital_gains = Decimal("0")

            ca_source_total = _cents(
                ca_sourced_wages + ca_sourced_interest + ca_sourced_dividends
                + ca_sourced_capital_gains + ca_sourced_se + ca_sourced_rental
            )

            # 540NR tax-rate method:
            # tax = (tax on total income) * (CA-source income / total income)
            # This uses the tax rate computed on ALL income, applied only
            # to the CA-source portion.
            if state_agi > Decimal("0") and ca_source_total > Decimal("0"):
                ratio = ca_source_total / state_agi
                if ratio > Decimal("1"):
                    ratio = Decimal("1")
                state_tax_apportioned = _cents(state_tax_full * ratio)
            else:
                state_tax_apportioned = Decimal("0.00")

            fraction = Decimal("1")  # no day-proration under sourcing
        else:
            # Legacy day-proration fallback when no W-2 state rows present.
            fraction = _apportionment_fraction(residency, days_in_state)
            state_tax_apportioned = _cents(state_tax_full * fraction)
            ca_sourced_interest = Decimal("0")
            ca_sourced_dividends = Decimal("0")
            ca_sourced_capital_gains = Decimal("0")
            ca_source_total = _cents(state_agi * fraction)

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
            "ca_sourced_rental_net": ca_sourced_rental,
            "ca_sourced_interest": ca_sourced_interest,
            "ca_sourced_dividends": ca_sourced_dividends,
            "ca_sourced_capital_gains": ca_sourced_capital_gains,
            "ca_source_total": ca_source_total,
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

        Residents: everything is CA-source.

        Nonresidents with W-2 state rows: real per-category sourcing per
        Schedule CA (540NR) rules:
          - Wages: sourced via W-2 state rows (box 16)
          - Interest/Dividends: sourced to domicile, NOT CA
          - Schedule C: sourced if business_location_state == "CA"
          - Schedule E rental: sourced if property address is in CA
          - Capital gains: conservative $0 (not sourced to CA by default)

        Nonresidents without W-2 state rows: legacy day-proration fallback.
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

        if residency == ResidencyStatus.RESIDENT:
            # All income is CA-source for residents.
            return IncomeApportionment(
                state_source_wages=_cents(wages),
                state_source_interest=_cents(interest),
                state_source_dividends=_cents(ord_div),
                state_source_capital_gains=_cents(capital_gains),
                state_source_self_employment=_cents(se_net),
                state_source_rental=_cents(rental_net),
            )

        if state_has_w2_state_rows(return_, "CA"):
            # Real per-category sourcing (540NR rules).
            ca_wages = state_source_wages_from_w2s(return_, "CA")
            ca_interest = Decimal("0")       # Interest sourced to domicile, not CA
            ca_dividends = Decimal("0")      # Dividends sourced to domicile, not CA
            ca_capital_gains = Decimal("0")  # Conservative: not sourced unless explicitly flagged
            ca_se = state_source_schedule_c(return_, "CA")
            ca_rental = state_source_rental(return_, "CA")

            return IncomeApportionment(
                state_source_wages=ca_wages,
                state_source_interest=ca_interest,
                state_source_dividends=ca_dividends,
                state_source_capital_gains=ca_capital_gains,
                state_source_self_employment=ca_se,
                state_source_rental=ca_rental,
            )

        # Legacy day-proration fallback when no W-2 state rows are present.
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
        from dataclasses import asdict

        from skill.scripts.output._acroform_overlay import (
            fill_acroform_pdf,
            format_money,
            load_widget_map,
            fetch_and_verify_source_pdf,
        )

        _REF = Path(__file__).resolve().parents[2] / "reference"
        _WIDGET_MAP = _REF / "ca-540-acroform-map.json"
        _SOURCE_PDF = _REF / "state_forms" / "ca_540.pdf"

        wmap = load_widget_map(_WIDGET_MAP)
        fetch_and_verify_source_pdf(
            _SOURCE_PDF, wmap.source_pdf_url, wmap.source_pdf_sha256
        )

        fields = compute_ca540_fields(state_return)
        widget_values: dict[str, str] = {}
        for sem_name, value in asdict(fields).items():
            widget_names = wmap.widget_names_for(sem_name)
            if not widget_names:
                continue
            text = format_money(value) if isinstance(value, Decimal) else str(value) if value else ""
            for wn in widget_names:
                widget_values[wn] = text

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
