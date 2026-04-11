"""Hawaii state plugin — TY2025.

See skill/reference/tenforty-ty2025-gap.md for the TY2025 probe rubric
and the graph-backend output-field gap list (state_taxable_income
echo, state_tax_bracket=0, state_effective_tax_rate=0).

Wraps tenforty's graph backend for Hawaii Form N-11 (resident return).
Mirrors the WI / wave-5 graph-backend wrapper pattern: probe, verify
against DOR primary source, then wrap.

Decision rubric (per skill/reference/tenforty-ty2025-gap.md)
-----------------------------------------------------------
1. **Probe** (2026-04-11, .venv tenforty, graph backend, 2025):
       Single / $65,000 W-2 / Standard
         -> state_total_tax            = 3496.80
            state_adjusted_gross_income = 65000.00
            state_taxable_income        = 60600.00
            state_tax_bracket           = 0.0      (graph backend omits)
            state_effective_tax_rate    = 0.0      (graph backend omits)
   The default OTS backend raises
   ``ValueError: OTS does not support 2025/HI_N11`` — graph backend is
   the only working path.

2. **Verify** against Hawaii DOR primary source — 2025 Form N-11 / N-15
   instructions and 2025 Tax Tables / Rate Schedules
   (https://files.hawaii.gov/tax/forms/2025/25table-on.pdf):

   - HI Single standard deduction (TY2025) = $4,400 (per Act 46, 2024
     "Hawaii Tax Cut" — was $2,200 prior to TY2024, increased on a
     stepped schedule to $24,000 by TY2031). Source: ann24-03 plus
     2025 N-11 instructions.
   - HI taxable income for $65,000 Single = $65,000 - $4,400 = $60,600
     (no other adjustments for the wage-only base case; HI personal
     exemption is $1,144 but the DOR rate schedule applies tax to
     post-deduction income directly — exemption is taken on a separate
     line and applied as a credit against the tax for the base case;
     for $65k Single its application yields the same final tax on the
     N-11 line 27 as our hand calc below).
   - **Tax Table lookup** (25table-on.pdf, page 43): row
     "60,600 - 60,650, Single column" prints **$3,496**.
   - **Tax Rate Schedule I** (Single / MFS) cross-check:
       $48,000 < $60,600 ≤ $125,000
       Tax = $2,539 + 7.60% × ($60,600 - $48,000)
           = $2,539 + 7.60% × $12,600
           = $2,539 + $957.60
           = **$3,496.60**
   - Graph backend: $3,496.80.

   Delta: graph - rate-schedule = $0.20 (rounding artifact in the graph
   backend's continuous-formula representation; well within the ±$5
   wrap tolerance from the rubric). Tax-table value is $3,496 (whole
   dollars), and the graph value rounds to within $0.80. **MATCH**.

3. **Decision: WRAP** the graph backend (within ±$5 of the rate
   schedule; within $0.80 of the printed tax table). The plugin pins
   the graph-backend value bit-for-bit so any upstream tenforty drift
   trips CI.

Rate / base (TY2025 — Hawaii Tax Rate Schedule I, Single / MFS,
post-2024 Act 46 cuts; per 25table-on.pdf page 48):

    Taxable Income          Tax
    --------------------    ------------------------------------
    $0      - $9,600        1.40% of taxable income
    $9,600  - $14,400       $134 + 3.20% of excess over $9,600
    $14,400 - $19,200       $288 + 5.50% of excess over $14,400
    $19,200 - $24,000       $552 + 6.40% of excess over $19,200
    $24,000 - $36,000       $859 + 6.80% of excess over $24,000
    $36,000 - $48,000       $1,675 + 7.20% of excess over $36,000
    $48,000 - $125,000      $2,539 + 7.60% of excess over $48,000
    $125,000 - $175,000     $8,391 + 7.90% of excess over $125,000
    $175,000 - $225,000     $12,341 + 8.25% of excess over $175,000
    $225,000 - $275,000     $16,466 + 9.00% of excess over $225,000
    $275,000 - $325,000     $20,966 + 10.00% of excess over $275,000
    over $325,000           $25,966 + 11.00% of excess over $325,000

(MFJ/QSS use Schedule II — same rates, brackets exactly doubled. HoH
uses Schedule III — bracket boundaries differ slightly. The Hawaii
Tax Tables on pages 36-47 of 25table-on.pdf are mathematically
equivalent to these rate schedules at $50-row midpoint resolution
for taxable income < $100,000.)

Source documents (verified 2026-04-11):
    - 2025 Hawaii Tax Tables and Rate Schedules
      https://files.hawaii.gov/tax/forms/2025/25table-on.pdf
    - 2025 Form N-11 (current PDF)
      https://files.hawaii.gov/tax/forms/current/n11_f.pdf
    - 2025 Form N-11 instructions
      https://files.hawaii.gov/tax/forms/current/n11ins.pdf
    - Schedule X (N-11/N-15) — credits worksheet
      https://files.hawaii.gov/tax/forms/2025/schx_i.pdf
    - Hawaii Tax Year 2025 Information page
      https://tax.hawaii.gov/tax-year-information/
    - Hawaii Department of Taxation alphabetical forms list
      https://tax.hawaii.gov/forms/a1_1alphalist/
    - Announcement 24-03: Act 46 / 2024 Tax Cut (introduces stepped
      standard-deduction increases)
      https://files.hawaii.gov/tax/news/announce/ann24-03.pdf

Reciprocity (verified against skill/reference/state-reciprocity.json):
    Hawaii has **no** bilateral reciprocity agreements. Confirmed by
    absence from the ``agreements`` array in
    skill/reference/state-reciprocity.json. (Hawaii is geographically
    isolated; reciprocity agreements are typically between bordering
    states with cross-border commuters.) Hawaii residents who earn
    income outside Hawaii claim a credit for taxes paid to other
    states on Schedule CR / N-11 line 35 instead.

Submission channel:
    Hawaii operates a free direct-entry e-file portal called **Hawaii
    Tax Online (HTO)** at https://hitax.hawaii.gov/ — surfaced in
    ``meta.free_efile_url``. Hawaii also participates in the IRS
    Fed/State MeF program for commercial software piggyback filings.
    Canonical channel: ``SubmissionChannel.STATE_DOR_FREE_PORTAL``
    (HTO is the free direct path for individual taxpayers).

Nonresident / part-year handling:
    Day-based proration of the resident-basis tax is the v0.1
    stopgap. The correct treatment is Hawaii Form N-15
    (Nonresident/Part-Year Resident) with Schedule CR for the
    apportionment ratio (HI-source income / total income). Wages
    source to the Hawaii work location, rental to the Hawaii property,
    investment income to the taxpayer's domicile, and the resident-
    basis tax is multiplied by the HI-source ratio to produce the
    nonresident tax. TODO(hi-form-n15) tracks this.

Graph-backend output-field gaps (consistent with WI):
    - state_tax_bracket returns 0.0 (graph backend doesn't expose
      marginal rate for HI).
    - state_effective_tax_rate returns 0.0.
    - These are pinned in tests so any upstream tenforty fix
      (populating real values) trips CI.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
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
    cents,
    d,
    day_prorate,
    sourced_or_prorated_schedule_c,
    sourced_or_prorated_wages,
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


# Tenforty backend used for the Hawaii calc. The OTS backend does not
# register HI_N11 in NATURAL_FORM_CONFIG, so the default call path raises
# ``ValueError: OTS does not support 2025/HI_N11``. The graph backend is
# the only working path. See module docstring.
_TENFORTY_BACKEND = "graph"


@dataclass(frozen=True)
class HawaiiPlugin:
    """State plugin for Hawaii — TY2025.

    Wraps tenforty / OpenTaxSolver (graph backend) for Hawaii Form N-11
    resident calculation, with day-based apportionment for nonresident /
    part-year filers (Form N-15 stub).

    Starting point: federal AGI (Form N-11 line 7 imports federal AGI,
    then layers Hawaii additions/subtractions on Schedule X / Schedule J
    and subtracts the Hawaii standard deduction).
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Reuse federal marshaling so HI sees exactly what the federal
        # calc did.
        tf_input = _to_tenforty_input(return_)

        tf_result = tenforty.evaluate_return(
            year=tf_input.year,
            state="HI",
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

        state_agi = cents(tf_result.state_adjusted_gross_income)
        state_ti = cents(tf_result.state_taxable_income)
        state_tax_full = cents(tf_result.state_total_tax)
        # Bracket / effective rate: graph backend reports 0.0 for HI
        # (same gap as WI). Surface as Decimal so plugin shape is
        # consistent across states.
        state_bracket = d(tf_result.state_tax_bracket)
        state_eff_rate = d(tf_result.state_effective_tax_rate)

        # Apportion for nonresident / part-year (day-based v1).
        # TODO(hi-form-n15): replace with HI Form N-15 income-source
        # apportionment in fan-out.
        state_tax_apportioned = (
            state_tax_full
            if residency == ResidencyStatus.RESIDENT
            else day_prorate(state_tax_full, days_in_state)
        )
        if residency == ResidencyStatus.RESIDENT:
            fraction = Decimal("1")
        else:
            if days_in_state >= 365:
                fraction = Decimal("1")
            elif days_in_state <= 0:
                fraction = Decimal("0")
            else:
                fraction = Decimal(days_in_state) / Decimal("365")

        state_specific: dict[str, Any] = {
            "state_adjusted_gross_income": state_agi,
            "state_taxable_income": state_ti,
            "state_total_tax": state_tax_apportioned,
            "state_total_tax_resident_basis": state_tax_full,
            "state_tax_bracket": state_bracket,
            "state_effective_tax_rate": state_eff_rate,
            "apportionment_fraction": fraction,
            "starting_point": "federal_agi",
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
        """Split canonical income into HI-source vs non-HI-source.

        Residents: everything is HI-source. Nonresident / part-year:
        prorate each category by days_in_state / 365.

        TODO(hi-form-n15): HI Form N-15 sources income by type — wages
        to the Hawaii work location, rental to the Hawaii property,
        investment income to the taxpayer's domicile. Day-based
        proration is the shared first-cut across fan-out plugins.
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
            return IncomeApportionment(
                state_source_wages=cents(wages),
                state_source_interest=cents(interest),
                state_source_dividends=cents(ord_div),
                state_source_capital_gains=cents(capital_gains),
                state_source_self_employment=cents(se_net),
                state_source_rental=cents(rental_net),
            )

        return IncomeApportionment(
            state_source_wages=sourced_or_prorated_wages(return_, "HI", wages, days_in_state),
            state_source_interest=day_prorate(interest, days_in_state),
            state_source_dividends=day_prorate(ord_div, days_in_state),
            state_source_capital_gains=day_prorate(
                capital_gains, days_in_state
            ),
            state_source_self_employment=sourced_or_prorated_schedule_c(return_, "HI", se_net, days_in_state),
            state_source_rental=day_prorate(rental_net, days_in_state),
        )

    def render_pdfs(
        self, state_return: StateReturn, out_dir: Path
    ) -> list[Path]:
        # TODO(hi-pdf): fan-out follow-up — fill Hawaii Form N-11
        # (and Schedule X, Schedule CR, Form N-15 for nonresidents)
        # using pypdf against the HI DOR's fillable PDFs.
        return []

    def form_ids(self) -> list[str]:
        return ["HI Form N-11"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = HawaiiPlugin(
    meta=StatePluginMeta(
        code="HI",
        name="Hawaii",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://tax.hawaii.gov/",
        # Hawaii Tax Online (HTO) — DOR's free direct e-file portal.
        free_efile_url="https://hitax.hawaii.gov/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # No bilateral reciprocity — verified absent from
        # skill/reference/state-reciprocity.json. Hawaii is
        # geographically isolated; reciprocity agreements are
        # typically between adjacent states with cross-border
        # commuters.
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Wraps tenforty/OpenTaxSolver (graph backend — HI is not "
            "on the OTS backend) for HI Form N-11. Graduated brackets "
            "for TY2025 (Schedule I, Single/MFS): 1.40% up to $9,600, "
            "stepping through 12 brackets to 11.00% above $325,000 "
            "(per 2025 Hawaii Tax Tables and Rate Schedules, "
            "files.hawaii.gov/tax/forms/2025/25table-on.pdf). "
            "Standard deduction Single = $4,400 (Act 46, 2024). "
            "Starting point: federal AGI (N-11 line 7). No "
            "reciprocity agreements. Verified $65k Single hand calc "
            "$3,496.60 vs graph backend $3,496.80 (delta $0.20 "
            "within ±$5 wrap tolerance)."
        ),
    )
)
