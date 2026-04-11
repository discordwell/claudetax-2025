"""North Carolina state plugin.

NC is one of the states tenforty supports directly via OpenTaxSolver. A
reference tenforty call confirms the state pass-through works:

    tenforty.evaluate_return(
        year=2025, state='NC', filing_status='Single',
        w2_income=65000, standard_or_itemized='Standard',
    )
    -> state_total_tax=2220.62, state_tax_bracket=0.0,
       state_taxable_income=52250.0, state_adjusted_gross_income=65000.0,
       state_effective_tax_rate=0.0

NC adopted a flat individual income tax rate schedule that continues to step
down each year. For TY2025 the rate is 4.25% (0.0425) per the NC Department of
Revenue tax-rate schedules:

    https://www.ncdor.gov/taxes-forms/tax-rate-schedules

The NC standard deduction for a Single filer in TY2025 is $12,750, per:

    https://www.ncdor.gov/taxes-forms/individual-income-tax/north-carolina-standard-deduction-or-north-carolina-itemized-deductions

(MFJ/QW/SS: $25,500, HOH: $19,125, MFS: $12,750 conditional.)

Math check for the reference scenario:

    NC taxable income = federal AGI - NC standard deduction
                      = 65,000 - 12,750 = 52,250
    NC tax            = 52,250 * 0.0425 = 2,220.625

which rounds to $2,220.62 — matching tenforty bit-for-bit. (tenforty reports
0.0 for bracket/effective rate on the flat-rate path; that is a known
OpenTaxSolver quirk for flat-rate states and we surface whatever tenforty
returns.)

NC Form D-400 Line 6 starts from federal adjusted gross income; the NC
standard deduction (or NC itemized deductions) is subtracted on Line 11 to
arrive at NC taxable income. Per NCDOR:

    "You may deduct from federal adjusted gross income either the NC standard
     deduction or NC itemized deductions."
    https://www.ncdor.gov/taxes-forms/individual-income-tax/north-carolina-standard-deduction-or-north-carolina-itemized-deductions

So `StateStartingPoint.FEDERAL_AGI` is correct.

NC has no bilateral reciprocity agreements (verified against
skill/reference/state-reciprocity.json — NC is not present in `agreements`).

NC participates in the IRS Fed/State MeF program; individual returns submit
via commercial software's piggyback flow, so we use
`SubmissionChannel.FED_STATE_PIGGYBACK`. NCDOR also publishes NC Free File
online eFile providers:

    https://www.ncdor.gov/file-pay/efile-individuals

This plugin wraps the tenforty NC pass-through: it reuses the calc engine's
`_to_tenforty_input` marshaling (so NC sees the same income/deduction numbers
the federal calc does), calls tenforty with `state='NC'`, and converts the
state_* floats back into Decimal for downstream consumers.

Nonresident / part-year handling is a day-based proration TODO — see the
comment inside `compute`. A real nonresident NC calc requires NC Form D-400
Schedule PN with income-source apportionment (wages sourced to work location,
investment income to domicile, rental to property state, etc.), which is
fan-out work.
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

    TODO: a real nonresident NC calculation uses Form D-400 Schedule PN with
    income-specific sourcing (wages sourced to work state, investment income
    sourced to domicile, rental to the property state, etc.) rather than a
    flat day ratio. Day-based proration is a first-order approximation;
    fan-out will tighten this with the real Schedule PN logic.
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
class NorthCarolinaPlugin:
    """State plugin for North Carolina.

    Wraps tenforty/OpenTaxSolver for the resident case and day-proration for
    nonresident / part-year. Starting point is federal AGI (NC Form D-400
    Line 6); NC subtracts its own standard or itemized deduction on Line 11 to
    arrive at NC taxable income, then applies the flat 4.25% TY2025 rate.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Reuse the federal marshaling so NC sees exactly the same numbers
        # the federal calc did — do NOT duplicate that logic here.
        tf_input = _to_tenforty_input(return_)

        tf_result = tenforty.evaluate_return(
            year=tf_input.year,
            state="NC",
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
        # cents) so precision is preserved. NC is flat 4.25%, so tenforty
        # reports 0.0 for bracket/effective rate; we surface that faithfully.
        state_bracket = _d(tf_result.state_tax_bracket)
        state_eff_rate = _d(tf_result.state_effective_tax_rate)

        # Apportion tax for nonresident / part-year. TODO: replace with real
        # NC Form D-400 Schedule PN income-source apportionment in fan-out.
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
        """Split canonical income into NC-source vs non-NC-source.

        Residents: everything is NC-source. Nonresident / part-year: prorate
        each category by days_in_state / 365.

        TODO: NC actually sources each income type differently on Form D-400
        Schedule PN (wages to the work location, interest/dividends to the
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
        # TODO: fan-out follow-up — fill NC Form D-400 (and Schedule PN where
        # applicable) using pypdf against the NCDOR's fillable PDFs. The
        # output renderer suite is the right home for this; this plugin
        # returns structured state_specific data that the renderer will
        # consume.
        return []

    def form_ids(self) -> list[str]:
        return ["NC Form D-400"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = NorthCarolinaPlugin(
    meta=StatePluginMeta(
        code="NC",
        name="North Carolina",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        # NCDOR: https://www.ncdor.gov/taxes-forms/individual-income-tax
        dor_url="https://www.ncdor.gov/taxes-forms/individual-income-tax",
        # NC Free File: https://www.ncdor.gov/file-pay/efile-individuals
        free_efile_url="https://www.ncdor.gov/file-pay/efile-individuals",
        submission_channel=SubmissionChannel.FED_STATE_PIGGYBACK,
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Uses tenforty/OpenTaxSolver for NC state calc. "
            "TY2025 flat rate 4.25% per NCDOR tax-rate schedules "
            "(https://www.ncdor.gov/taxes-forms/tax-rate-schedules); "
            "Single NC standard deduction $12,750."
        ),
    )
)
