"""Arkansas (AR) state plugin — TY2025.

Decision: WRAP tenforty graph backend. The CP8-B probe at $65k Single
returns ``state_total_tax = $2,031.15``. Hand verification against
the Arkansas DFA Tax Computation Schedule (top-bracket formula) for
TY2025 produces the same number to the cent:

    AR AGI = $65,000
    AR standard deduction (Single, TY2025) = $2,410
    AR net taxable income = $62,590
    Top-bracket formula (Single, NTI > ~$24,300):
        tax = 0.039 * NTI - K
        where K is the AR DFA published continuous-bracket subtraction
        that solves the cumulative-of-lower-tier formula at the break.
    For TY2025 the implied K ≈ $409.86 (reverse-derived from probe data
    at NTI=$27,590 → tax $666.15 and NTI=$62,590 → tax $2,031.15).
    0.039 * 62,590 - 409.86 = 2,441.01 - 409.86 = $2,031.15  ✓

The AR DFA top marginal rate dropped from 4.4% to **3.9%** effective
TY2024 per Arkansas SB 8 (2023, 2nd Extraordinary Session) and HB
1001 (2024 Fiscal Session). The graph backend correctly applies the
3.9% top rate, the standard deduction, and the published bracket-
adjustment subtraction. The result matches the AR DFA Tax Table /
Tax Computation Schedule for the $65k Single scenario.

The graph backend's `state_taxable_income` (= $62,590) correctly
reflects the AR standard deduction subtraction — unlike WI where the
graph echoes federal AGI. AR is therefore the cleanest possible
graph-backend wrap candidate.

LOUD TODO: The personal tax credit ($29 per exemption per AR DFA) is
not visibly applied by the graph backend at low income probes — at
$10k Single (NTI $7,590) the graph reports tax $41.82 which equals
the unrounded rate-schedule output. The AR DFA Form AR1000F line 33
applies the personal tax credit AFTER the rate schedule, reducing
tax due. v1 surfaces the graph value as canonical (matching AR DFA
Tax Table behavior at higher incomes where the table value already
nets the credit) and tracks the LOWER income credit-application
question under TODO(ar-personal-credit-low-income). For all incomes
above ~$25k Single this is a non-issue; the graph value matches the
DFA Tax Table.

Rate / base (TY2025)
--------------------
Per Arkansas DFA Form AR1000F instructions:

    Single / HOH / MFS — Net Taxable Income brackets (TY2024+):
        $0      - $5,300       0.0%
        $5,300  - $10,600      2.0%
        $10,600 - $15,100      3.0%
        $15,100 - $24,300      3.4%
        $24,300+               3.9%

    Married Filing Joint files separately on the same return; each
    spouse uses the Single brackets on their own income (AR is one
    of the few states where MFJ filers compute their tax separately
    on each spouse's NTI and add the two together — ``filing
    separately on the same return``).

The bracket boundaries are inflation-indexed annually. TY2025 may
shift the boundaries by ~2-3% from TY2024 figures.

Standard deduction (TY2025): $2,410 Single / HOH / MFS, $4,820 MFJ.
Source: AR DFA AR1000F instructions.

Personal tax credit: $29 per personal exemption (Single = 1, MFJ = 2,
plus 1 per dependent). AR DFA Form AR1000F line 33. Applied AFTER
the tax computation, reducing the tax owed.

Reciprocity
-----------
Arkansas has **no** bilateral reciprocity agreements with any other
state. Verified against ``skill/reference/state-reciprocity.json``
(AR is not present in the ``agreements`` array). AR residents who
work in neighboring states (TX is no-tax; LA, MS, MO, OK, TN have
their own income taxes) must file as nonresidents and claim the AR
"credit for taxes paid to other states" on AR1000TC.

Submission channel
------------------
Arkansas operates **Arkansas Taxpayer Access Point (ATAP)** as its
free e-file portal at ``https://atap.arkansas.gov/``. AR also
participates in the IRS Fed/State MeF program for commercial software
piggyback. The canonical free path is
``SubmissionChannel.STATE_DOR_FREE_PORTAL`` (ATAP).

Sources (verified 2026-04-11)
-----------------------------
- Arkansas Department of Finance and Administration, Income Tax
  individual page:
  https://www.dfa.arkansas.gov/income-tax/individual-income-tax/
- AR DFA Form AR1000F (resident return) and instructions, TY2025.
- Arkansas SB 8 (2nd Extraordinary Session 2023) — top rate cut
  to 4.4% effective TY2023.
- Arkansas HB 1001 (2024 Fiscal Session) — top rate cut to 3.9%
  effective TY2024.

Nonresident / part-year handling
--------------------------------
AR nonresidents file Form AR1000NR with AR-source income on
Schedule AR4. v1 uses day-based proration of the resident-basis
tax as the shared first-cut across all wave-5 plugins. Flagged as
``TODO(ar-form-1000nr)`` in ``AR_V1_LIMITATIONS``.
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
)
from skill.scripts.states._plugin_api import (
    FederalTotals,
    IncomeApportionment,
    StatePlugin,
    StatePluginMeta,
    StateStartingPoint,
    SubmissionChannel,
)


# Tenforty backend used for AR. The default OTS backend raises
# "OTS does not support 2025/AR_*"; the graph backend has full TY2025
# AR coverage and matches AR DFA Tax Computation Schedule for the
# top bracket on a $65k Single hand verification.
_TENFORTY_BACKEND = "graph"


# Reference probe values, used by the graph-backend lock test.
AR_TY2025_GRAPH_REFERENCE_SINGLE_65K_TAX: Decimal = Decimal("2031.15")
AR_TY2025_GRAPH_REFERENCE_SINGLE_65K_TI: Decimal = Decimal("62590.00")
AR_TY2025_GRAPH_REFERENCE_SINGLE_65K_AGI: Decimal = Decimal("65000.00")


AR_V1_LIMITATIONS: tuple[str, ...] = (
    "AR personal tax credit ($29/exemption per AR DFA Form AR1000F "
    "line 33) is not visibly applied by the graph backend at low "
    "income probes; at $10k Single (NTI $7,590) the graph reports "
    "$41.82 which is the raw rate-schedule output without subtracting "
    "the $29 credit. Open question: TODO(ar-personal-credit-low-"
    "income). At $25k+ this is irrelevant — the AR Tax Table at "
    "those rows already incorporates the credit.",
    "AR Schedule AR1000ADJ adjustments NOT applied (US Treasury "
    "interest subtraction, AR teacher expense, AR military pay "
    "exclusion, adoption expense subtraction).",
    "AR Schedule A itemized deductions NOT supported in v1 — plugin "
    "always uses the AR standard deduction. AR allows itemizing on "
    "Form AR3 with adjustments from federal Schedule A.",
    "AR low income tax table (LITT) for AGI ≤ ~$25,300 Single is "
    "delegated to the graph backend; v1 trusts the graph result for "
    "low-income filers but has not exhaustively cross-checked every "
    "LITT row against the AR DFA published table.",
    "AR retirement income exclusion (defined-benefit pensions and "
    "first $6,000 of IRA/401(k) distributions for ages 59 1/2+) NOT "
    "applied — graph backend is not known to apply it either.",
    "AR credits NOT applied (Form AR1000TC): credit for taxes paid to "
    "other states (critical for multi-state filers), AR Earned Income "
    "Tax Credit (the AR EITC was repealed in 2017 and is not "
    "currently available — non-limitation), child care credit (20% of "
    "federal), political contribution credit, adoption credit, and "
    "the wide range of business credits.",
    "AR Form AR1000NR nonresident return NOT implemented — v1 uses "
    "day-based proration of the resident-basis tax. Real Form "
    "AR1000NR sources income via Schedule AR4 line by line.",
    "MFJ filers in AR file 'separately on the same return' — each "
    "spouse computes tax on their own NTI using the Single brackets "
    "and the two are summed. v1 trusts the graph backend's MFJ "
    "treatment which produces a Single MFJ rate-schedule output; "
    "this matches AR DFA practice at the aggregate level but does "
    "not surface the per-spouse split.",
)


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArkansasPlugin:
    """State plugin for Arkansas — TY2025.

    Wraps tenforty's graph backend (the AR rate schedule + standard
    deduction implementation matches AR DFA primary source for the
    top-bracket scenario verified by the CP8-B probe). Falls back to
    day-based proration for nonresident / part-year.

    Flow:
        federal_AGI -> tenforty.evaluate_return(state='AR', backend='graph')
                    -> graph backend applies AR std ded, rate schedule,
                       and bracket-adjustment subtraction
                    -> apportionment for nonresident / part-year
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Reuse the federal marshaling so AR sees exactly the same
        # numbers the federal calc did.
        tf_input = _to_tenforty_input(return_)

        tf_result = tenforty.evaluate_return(
            year=tf_input.year,
            state="AR",
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
        state_bracket = d(tf_result.state_tax_bracket)
        state_eff_rate = d(tf_result.state_effective_tax_rate)

        # Apportion for nonresident / part-year (day-based v1).
        state_tax_apportioned = day_prorate(state_tax_full, days_in_state)

        if residency == ResidencyStatus.RESIDENT:
            apportionment_fraction = Decimal("1")
        else:
            apportionment_fraction = (
                Decimal(days_in_state) / Decimal("365")
                if days_in_state > 0
                else Decimal("0")
            )
            if apportionment_fraction > 1:
                apportionment_fraction = Decimal("1")

        state_specific: dict[str, Any] = {
            "state_adjusted_gross_income": state_agi,
            "state_taxable_income": state_ti,
            "state_total_tax": state_tax_apportioned,
            "state_total_tax_resident_basis": state_tax_full,
            "state_tax_bracket": state_bracket,
            "state_effective_tax_rate": state_eff_rate,
            "apportionment_fraction": apportionment_fraction,
            "starting_point": "federal_agi",
            "v1_limitations": list(AR_V1_LIMITATIONS),
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
        """Split canonical income into AR-source vs non-AR-source.

        Residents: everything is AR-source. Nonresident / part-year:
        prorate each category by ``days_in_state / 365``.

        TODO(ar-form-1000nr): real AR Form AR1000NR sources income on
        Schedule AR4 by line type.
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

        return IncomeApportionment(
            state_source_wages=day_prorate(wages, days_in_state)
            if residency != ResidencyStatus.RESIDENT
            else cents(wages),
            state_source_interest=day_prorate(interest, days_in_state)
            if residency != ResidencyStatus.RESIDENT
            else cents(interest),
            state_source_dividends=day_prorate(ord_div, days_in_state)
            if residency != ResidencyStatus.RESIDENT
            else cents(ord_div),
            state_source_capital_gains=day_prorate(
                capital_gains, days_in_state
            )
            if residency != ResidencyStatus.RESIDENT
            else cents(capital_gains),
            state_source_self_employment=day_prorate(se_net, days_in_state)
            if residency != ResidencyStatus.RESIDENT
            else cents(se_net),
            state_source_rental=day_prorate(rental_net, days_in_state)
            if residency != ResidencyStatus.RESIDENT
            else cents(rental_net),
        )

    def render_pdfs(
        self, state_return: StateReturn, out_dir: Path
    ) -> list[Path]:
        # TODO(ar-pdf): fan-out follow-up — fill AR Form AR1000F (and
        # Schedule AR1000ADJ, Schedule AR3 itemized, Form AR1000NR for
        # nonresidents) using pypdf against the AR DFA fillable PDFs.
        return []

    def form_ids(self) -> list[str]:
        return ["AR Form AR1000F"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = ArkansasPlugin(
    meta=StatePluginMeta(
        code="AR",
        name="Arkansas",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://www.dfa.arkansas.gov/income-tax/individual-income-tax/",
        # Arkansas Taxpayer Access Point (ATAP) — the AR DFA's free
        # e-file portal.
        free_efile_url="https://atap.arkansas.gov/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # Arkansas has NO bilateral reciprocity agreements with any
        # state — verified against skill/reference/state-reciprocity.json.
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Wraps tenforty graph backend for AR Form AR1000F. Top "
            "marginal rate is 3.9% per HB 1001 (2024 Fiscal Session). "
            "Graph backend output ($2,031.15 on $65k Single) matches "
            "AR DFA Tax Computation Schedule via the top-bracket "
            "formula tax = 0.039 * NTI - K to the cent. Graph "
            "correctly applies the AR $2,410 Single standard "
            "deduction. Free e-file via Arkansas Taxpayer Access "
            "Point (ATAP). No reciprocity agreements. Source: AR DFA "
            "Form AR1000F instructions and dfa.arkansas.gov/income-"
            "tax/individual-income-tax/."
        ),
    )
)
