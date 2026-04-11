"""New Mexico (NM) state plugin — TY2025.

Wraps tenforty / OpenTaxSolver for the New Mexico Form PIT-1 resident
calc, in the same shape as the WI plugin (graph backend wrapper). NM is
NOT supported by the default OTS backend in tenforty
(``ValueError: OTS does not support 2025/NM_PIT1``) but IS supported by
the newer graph backend, which produces a tax number that matches the
DOR primary source bit-for-bit on the spec's $65k Single scenario.

Source of truth
---------------
The TY2025 graph backend output for Single / $65,000 W-2 / Standard is
$1,905.75. A hand calculation against the DOR's TY2025 PIT-1 schedule
(NM HB 252, Laws 2024 ch. 67 — restructured brackets effective 2025)
matches the graph value to the cent:

    Federal AGI                    $65,000.00
    NM std deduction (= federal)   $15,750.00  (NM conforms to fed std ded)
    NM additions / subtractions    $0.00
    NM taxable income              $49,250.00
    Tax (graduated, Single):
      1.5%  *   5,500             $    82.50
      3.2%  *  11,000             $   352.00
      4.3%  *  17,000             $   731.00
      4.7%  *  15,750             $   740.25
                                  ------------
      Total                        $ 1,905.75   ✓ matches graph backend

Because the calc reconciles to DOR primary source ±$0.00, this plugin
follows the WI graph-wrapper pattern rather than hand-rolling brackets.

Brackets / standard deduction (TY2025)
--------------------------------------
HB 252 (Laws 2024 ch. 67) was the first major restructuring since 2005.
It lowered the bottom rate from 1.7% to 1.5%, added a new 4.3% middle
bracket, and shifted the higher-bracket thresholds. TY2025 Single
brackets per the NM Taxation and Revenue Department PIT-1 instructions:

    Over            Not over        Rate
    $0              $5,500          1.5%
    $5,500          $16,500         3.2%
    $16,500         $33,500         4.3%
    $33,500         $66,500         4.7%
    $66,500         $210,000        4.9%
    $210,000        —               5.9%

NM standard deduction = federal standard deduction (NM conforms).
TY2025 OBBBA federal Single std ded = $15,750.

Sources (verified 2026-04-11):
- New Mexico Taxation and Revenue Department, Personal Income Tax Rates,
  https://www.tax.newmexico.gov/all-nm-taxes/current-historic-tax-rates-overview/personal-income-tax-rates/
- New Mexico HB 252, Laws 2024 ch. 67 (PIT bracket restructuring).
- Tax Foundation 2025 State Income Tax Rates and Brackets summary.

Reciprocity
-----------
New Mexico has **no** bilateral reciprocity agreements with any other
state — verified against ``skill/reference/state-reciprocity.json`` (NM
does not appear in the ``agreements`` array). NM residents who work in
neighboring states (TX, OK, AZ, UT, CO) must file a nonresident return
in any state with income tax (TX has none) and claim the NM credit for
taxes paid to other states on PIT-1.

Submission channel
------------------
New Mexico operates "Taxpayer Access Point" (TAP) at
https://tap.state.nm.us as its DOR-direct portal, but TAP is primarily
for business taxes and credit applications. Individual filers
participate in the IRS Fed/State MeF program — commercial software
piggybacks the PIT-1 onto the federal 1040. The canonical channel here
is ``SubmissionChannel.FED_STATE_PIGGYBACK``; the TAP URL is surfaced
in ``meta.free_efile_url`` for completeness.

Nonresident / part-year
-----------------------
Day-based proration of the resident-basis tax is a v0.1 stopgap. The
correct treatment is PIT-B (Allocation and Apportionment of Income for
Nonresidents and Part-Year Residents), which sources each income type
to a state. TODO(nm-pit-b) tracks this.
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

# NM is wired up only on the graph backend in tenforty TY2025 — the
# default OTS backend raises ``ValueError: OTS does not support
# 2025/NM_PIT1``. Same idiom as WI.
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
    """Days-based proration for nonresident / part-year. See TODO."""
    if residency == ResidencyStatus.RESIDENT:
        return Decimal("1")
    frac = Decimal(days_in_state) / Decimal("365")
    if frac < 0:
        return Decimal("0")
    if frac > 1:
        return Decimal("1")
    return frac


@dataclass(frozen=True)
class NewMexicoPlugin:
    """State plugin for New Mexico — TY2025.

    Wraps tenforty / OpenTaxSolver (graph backend) for the resident case
    and day-proration for nonresident / part-year. Starting point is
    federal AGI; NM applies its own additions / subtractions on PIT-ADJ
    and conforms to the federal standard deduction.

    See module docstring for the DOR-primary-source reconciliation
    that justifies the wrap (rather than hand-rolling).
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
            state="NM",
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
        """Day-prorated income split. TODO(nm-pit-b): replace with PIT-B."""
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
        # TODO(nm-pdf): fan-out follow-up — fill PIT-1 + PIT-ADJ +
        # PIT-B against the NM TRD's fillable PDFs.
        return []

    def form_ids(self) -> list[str]:
        return ["NM Form PIT-1"]


PLUGIN: StatePlugin = NewMexicoPlugin(
    meta=StatePluginMeta(
        code="NM",
        name="New Mexico",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://www.tax.newmexico.gov/individuals/",
        # NM TAP is the DOR's online portal — primarily business taxes
        # but listed for completeness; individuals will use commercial
        # MeF software via the FED_STATE_PIGGYBACK channel.
        free_efile_url="https://tap.state.nm.us/",
        submission_channel=SubmissionChannel.FED_STATE_PIGGYBACK,
        # NM has no bilateral reciprocity with any state — verified
        # against skill/reference/state-reciprocity.json.
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Wraps tenforty/OpenTaxSolver (graph backend — NM is not on "
            "the OTS backend) for NM Form PIT-1. Six-bracket graduated "
            "structure for TY2025 (HB 252, 2024): 1.5% / 3.2% / 4.3% / "
            "4.7% / 4.9% / 5.9%. Standard deduction conforms to "
            "federal ($15,750 Single TY2025). No reciprocity. Source: "
            "NM Taxation and Revenue Department PIT-1 instructions."
        ),
    )
)
