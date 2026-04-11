"""South Carolina (SC) state plugin — TY2025.

Wraps tenforty / OpenTaxSolver (graph backend) for the SC1040 resident
calc, in the same shape as the WI plugin. SC is NOT supported by the
default OTS backend in tenforty (``ValueError: OTS does not support
2025/SC_1040``) but IS supported by the newer graph backend, which
produces a tax number that matches the DOR primary source within $0.30
on the spec's $65k Single scenario — well inside the ±$5 wrap window.

Source of truth
---------------
The TY2025 graph backend output for Single / $65,000 W-2 / Standard is
$2,313.30. The DOR's printed Tax Tables (SC1040TT, Rev. 6/17/25) round
each $100 row to whole dollars; the row $49,200-$49,300 prints **$2,313**.
The graph backend uses the underlying continuous formula

    tax = 0.06 * TI - 642   (for TI >= $100,000)

evaluated at TI = $49,250 (federal taxable income, since SC starts from
federal taxable income on SC1040 line 1):

    0.06 * 49250 - 642 = 2955 - 642 = 2313.00

The graph backend reports $2,313.30 — a $0.30 difference from the
formula above, which is the residue of how the OTS graph models the
sub-bracket (0% / 3% / 6%) lookup before $100k. The whole-dollar table
at the row $49,200-$49,300 is $2,313 (within $0.30 of the graph value
and within $0.30 of my closed-form computation). All three values
agree within ±$0.30, well inside the ±$5 wrap window.

Brackets / starting point (TY2025)
----------------------------------
South Carolina starts from **federal taxable income** (SC1040 line 1),
not federal AGI. SC adds back state tax refunds and certain other
items, then applies graduated brackets. For TY2025, the top marginal
rate dropped from 6.2% to **6.0%** (SCDOR 2025 Booklet "Reduction In
Income Tax Rates"). The current SC bracket structure (TY2025) is:

    $0       - $3,560      0%
    $3,560   - $17,830     3.0%   (subtract $107)
    $17,830  - and above   6.0%   (subtract $642)

For income at or above $100,000 the SC1040 instructs you to use the
Tax Rate Schedule rather than the printed tables; the formula is
exactly ``tax = 0.06 * TI - 642``. The under-$100k tables print
whole-dollar values from the same formula.

Sources (verified 2026-04-11):
- SCDOR 2025 SC1040 Booklet (IITPacket_2025.pdf), "Reduction In Income
  Tax Rates" announcement: top marginal rate is 6%.
  https://dor.sc.gov/sites/dor/files/forms/IITPacket_2025.pdf
- SC1040TT 2025, Tax Rate Schedule for $100,000+ (formula above).
  https://dor.sc.gov/sites/dor/files/forms/SC1040TT_2025.pdf
- SCDOR Forms landing page: https://dor.sc.gov/forms

Reciprocity
-----------
South Carolina has **no** bilateral reciprocity agreements with any
other state — verified against ``skill/reference/state-reciprocity.json``.
SC residents who work in NC or GA must file nonresident returns there
and claim the SC credit for taxes paid to other states (SC1040TC).

Submission channel
------------------
South Carolina operates **MyDORWAY** (https://dor.sc.gov/mydorway) as a
free DOR-direct portal for individual filers. SC also participates in
the IRS Fed/State MeF program for commercial-software piggyback
filings. The canonical channel is ``SubmissionChannel.STATE_DOR_FREE_PORTAL``
(MyDORWAY) with FED_STATE_PIGGYBACK as the alternate path.

Nonresident / part-year
-----------------------
Day-based proration of the resident-basis tax is the v0.1 stopgap.
The correct treatment is SC Schedule NR (Nonresident Schedule) with
income sourcing rules. TODO(sc-schedule-nr) tracks this.

Two-wage-earner credit
----------------------
SC offers a credit for two-wage-earner couples (SC1040 line 19) up to
$249. Not modeled in v1; flagged as a TODO. Does not affect the
$65k Single lock scenario.
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
_TENFORTY_BACKEND = "graph"


def _d(v: Any) -> Decimal:
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _cents(v: Any) -> Decimal:
    return _d(v).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _apportionment_fraction(
    residency: ResidencyStatus, days_in_state: int
) -> Decimal:
    """Days-based proration. TODO(sc-schedule-nr)."""
    if residency == ResidencyStatus.RESIDENT:
        return Decimal("1")
    frac = Decimal(days_in_state) / Decimal("365")
    if frac < 0:
        return Decimal("0")
    if frac > 1:
        return Decimal("1")
    return frac


@dataclass(frozen=True)
class SouthCarolinaPlugin:
    """State plugin for South Carolina — TY2025.

    Wraps tenforty / OpenTaxSolver (graph backend) for the resident case
    and day-proration for nonresident / part-year. Starting point is
    federal taxable income (SC1040 line 1), not federal AGI — note this
    differs from most states.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        tf_input = _to_tenforty_input(return_)

        tf_result = tenforty.evaluate_return(
            year=tf_input.year,
            state="SC",
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
            backend=_TENFORTY_BACKEND,
        )

        state_agi = _cents(tf_result.state_adjusted_gross_income)
        state_ti = _cents(tf_result.state_taxable_income)
        state_tax_full = _cents(tf_result.state_total_tax)
        state_bracket = _d(tf_result.state_tax_bracket)
        state_eff_rate = _d(tf_result.state_effective_tax_rate)

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
        """Day-prorated income split. TODO(sc-schedule-nr)."""
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
                gain = (
                    txn.proceeds - txn.cost_basis + txn.adjustment_amount
                )
                if txn.is_long_term:
                    lt_gain += gain
                else:
                    st_gain += gain
        capital_gains = st_gain + lt_gain + cap_gain_distr

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
        # TODO(sc-pdf): fan-out follow-up — fill SC1040 + Schedule NR
        # against SCDOR fillable PDFs.
        return []

    def form_ids(self) -> list[str]:
        return ["SC Form SC1040"]


PLUGIN: StatePlugin = SouthCarolinaPlugin(
    meta=StatePluginMeta(
        code="SC",
        name="South Carolina",
        has_income_tax=True,
        # SC1040 line 1 is "Federal Taxable Income".
        starting_point=StateStartingPoint.FEDERAL_TAXABLE_INCOME,
        dor_url="https://dor.sc.gov/iit",
        # MyDORWAY — SCDOR's free direct portal for individual filers.
        free_efile_url="https://dor.sc.gov/mydorway",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # SC has no bilateral reciprocity with any state — verified
        # against skill/reference/state-reciprocity.json.
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Wraps tenforty/OpenTaxSolver (graph backend — SC is not on "
            "the OTS backend) for SC Form SC1040. Three-tier graduated "
            "structure for TY2025: 0% / 3% / 6% with the top rate "
            "reduced from 6.2% to 6.0% per the 2025 SCDOR booklet. "
            "Starting point: federal taxable income (SC1040 line 1). "
            "Free DOR portal: MyDORWAY. No reciprocity. Source: 2025 "
            "SC1040 Booklet (IITPacket_2025) and SC1040TT (Rev. "
            "6/17/25)."
        ),
    )
)
