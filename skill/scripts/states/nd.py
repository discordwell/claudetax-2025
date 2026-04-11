"""North Dakota (ND) state plugin — TY2025.

See skill/reference/tenforty-ty2025-gap.md for the TY2025 probe rubric
and why ND is hand-rolled rather than graph-wrapped (the $15.11 graph
value is mathematically correct but the graph's other output fields
are stubbed — hand-rolling gives cleaner state_specific output).

Hand-rolled ND Form ND-1 calculation. Tenforty does NOT support ND via
the default OTS backend (``ValueError: OTS does not support 2025/ND_1``).
The newer **graph** backend does return a number for ND ($15.11 on the
spec's $65k Single scenario) but the wave 5 fan-out spec required us
to hand-roll ND on the assumption that the $15.11 graph value was
broken or stubbed.

**ND-SPECIFIC FINDING (verified 2026-04-11)**: the $15.11 graph result
is **mathematically correct**, not broken. ND has a *very* high zero
bracket for TY2025 (Single threshold $48,475) — see the 2025 ND-1
booklet "2025 Tax Rate Schedules" page. Federal taxable income on a
$65k Single OBBBA return is $49,250 ($65,000 - $15,750 std deduction);
ND taxable income equals federal taxable income for this profile, so
only $49,250 - $48,475 = $775 is subject to the 1.95% middle rate:

    ND tax = 0% * $48,475 + 1.95% * ($49,250 - $48,475)
           = 1.95% * $775
           = **$15.1125**

This matches the graph backend output to the cent. The graph is NOT
broken; the spec's premise was wrong. The result merely *looks* tiny
because federal AGI of $65k barely clears the ND zero bracket after
the federal standard deduction is applied.

We hand-roll anyway, per the spec's mandate, because:

1. Hand-rolling gives us full Decimal precision and DOR-traceable
   line-by-line state_specific output instead of opaque graph
   variables.
2. The graph backend's other ND fields (state_taxable_income echoes
   federal TI, state_tax_bracket = 0.0) are stubbed in the same way
   as the WI graph backend. Hand-rolling lets us emit cleaner
   state-specific data.
3. Hand-rolling gives us the marriage penalty credit, the credit for
   tax paid to other states, and other ND-specific lines as v1
   stubs (TODOs) that a wrap could not surface.

A gatekeeper test pins the $15.11 graph result with a comment that
explains the finding. When tenforty fixes the graph backend's
state_taxable_income echo (it should report ND taxable income, not
federal taxable income), the test will not break — but the docstring
TODO will direct the next agent to revisit.

Source of truth
---------------
2025 ND-1 Booklet (Form ND-EZ / Form ND-1 Individual Income Tax
Instructions), retrieved 2026-04-11 from:
https://www.tax.nd.gov/sites/www/files/documents/forms/individual/2025-iit/2025-individual-income-tax-booklet.pdf

The booklet's "2025 Tax Rate Schedules" (page 27) prints the brackets
verbatim:

    Single
        Over          But Not Over
        $0            $48,475         0.00%   of ND taxable income
        $48,475       $244,825        $0.00 + 1.95% of amount over $48,475
        $244,825      —               $3,828.83 + 2.50% of amount over $244,825

    Married Filing Jointly / QSS
        $0            $80,975         0.00%
        $80,975       $298,075        $0.00 + 1.95% of amount over $80,975
        $298,075      —               $4,233.45 + 2.50% of amount over $298,075

    Married Filing Separately
        $0            $40,475         0.00%
        $40,475       $149,025        $0.00 + 1.95% of amount over $40,475
        $149,025      —               $2,116.73 + 2.50% of amount over $149,025

    Head of Household
        $0            $64,950         0.00%
        $64,950       $271,450        $0.00 + 1.95% of amount over $64,950
        $271,450      —               $4,026.75 + 2.50% of amount over $271,450

(For ND taxable income < $100,000 the booklet directs you to the Tax
Table on pages 20-26, which is generated from the same formula and is
mathematically equivalent to the rate schedule. v1 always uses the
schedule for cents-precision arithmetic; the table was verified
spot-row by spot-row against booklet page 27.)

Form ND-1 starting point and flow
---------------------------------
ND Form ND-1 is one of the few US state returns that starts from
**federal taxable income** rather than federal AGI:

    Line 1   Federal taxable income (federal 1040 line 15)
    Line 2-15 ND additions / subtractions (Schedule ND-1A / ND-1S /
             ND-1SA), e.g. interest on non-ND state/muni bonds (add),
             interest on US obligations (subtract), workforce-recruit
             exclusion, stillbirth deduction, etc.
    Line 18  ND taxable income (after additions / subtractions)
    Line 20  Tax (Tax Table or Rate Schedule above; Schedule ND-1NR
             for nonresidents/part-year)
    Line 21  Credit for income tax paid to another state
    Line 22  Marriage penalty credit (MFJ when both spouses had qualified
             income > $47,550 and joint TI > $81,036)
    Line 23+ Other credits

v1 stubs Schedule ND-1A/ND-1S/ND-1SA additions/subtractions to zero
and treats ND taxable income = federal taxable income. The list of
unmodeled adjustments lives in ``ND_V1_LIMITATIONS``.

**TY2025 Single $65k resident reference scenario** (locked in tests):

    Line 1   Federal taxable income           = $49,250
                                                ($65,000 - $15,750 OBBBA std ded)
    Lines 2-15 ND adjustments                  = $0
    Line 18  ND taxable income                = $49,250
    Line 20  Tax (Single rate schedule):
             $49,250 is over $48,475 but not
             over $244,825, so:
             $0 + 1.95% * ($49,250 - $48,475)
             = 1.95% * $775
             = **$15.1125**

Locked at $15.11 to the cent in tests. This matches both:
- The DOR rate schedule (page 27 of the 2025 booklet).
- Tenforty's graph backend output for the same scenario (probed
  2026-04-11).

Reciprocity
-----------
North Dakota has **two** bilateral reciprocity agreements — with
**Minnesota** and **Montana** — verified against
``skill/reference/state-reciprocity.json``. ND DOR Schedule ND-1CR
and the corresponding MN / MT employee certificate forms allow
cross-border commuters to claim exemption from withholding in the
work state.

Submission channel
------------------
North Dakota participates in the IRS Fed/State MeF program for
piggyback filings. The Office of State Tax Commissioner also operates
**ND Taxpayer Access Point (ND TAP)** at https://apps.nd.gov/tax/tap/
as a free DOR-direct portal for many tax types; for individual income
tax e-filing the canonical free-path channel is the federal MeF
piggyback rather than TAP. Channel = ``FED_STATE_PIGGYBACK``.

Why hand-roll instead of wrap
------------------------------
The spec mandated hand-rolling ND on the assumption that the $15.11
graph backend value was broken. Empirically the graph value is
**correct** (this docstring traces the formula). Per spec we
hand-roll anyway; the gatekeeper test in ``test_state_nd.py`` pins
the $15.11 value to both the DOR formula AND the graph backend so
any divergence between the two fails CI loudly.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Final

from skill.scripts.models import (
    CanonicalReturn,
    FilingStatus,
    ResidencyStatus,
    StateReturn,
)
from skill.scripts.states._hand_rolled_base import (
    GraduatedBracket,
    cents,
    d,
    day_prorate,
    graduated_tax,
)
from skill.scripts.states._plugin_api import (
    FederalTotals,
    IncomeApportionment,
    StatePlugin,
    StatePluginMeta,
    StateStartingPoint,
    SubmissionChannel,
)


# Canonical wave-5 $65k Single gatekeeper lock. Hand-traced from ND
# Form ND-1 — the value is tiny ($15.11) because ND has a very high
# Single zero bracket ($48,475). See module docstring. Referenced
# from test_state_nd.py.
LOCK_VALUE: Final[Decimal] = Decimal("15.11")


# ---------------------------------------------------------------------------
# TY2025 ND Tax Rate Schedules — booklet page 27
# ---------------------------------------------------------------------------


ND_TY2025_BRACKETS_SINGLE: tuple[GraduatedBracket, ...] = (
    GraduatedBracket(low=Decimal("0"),       high=Decimal("48475"),  rate=Decimal("0")),
    GraduatedBracket(low=Decimal("48475"),   high=Decimal("244825"), rate=Decimal("0.0195")),
    GraduatedBracket(low=Decimal("244825"),  high=None,              rate=Decimal("0.025")),
)
"""TY2025 Single (and Single-equivalent statuses) brackets per ND-1
booklet page 27. ND-1 line 20 tax computation."""

ND_TY2025_BRACKETS_MFJ: tuple[GraduatedBracket, ...] = (
    GraduatedBracket(low=Decimal("0"),       high=Decimal("80975"),  rate=Decimal("0")),
    GraduatedBracket(low=Decimal("80975"),   high=Decimal("298075"), rate=Decimal("0.0195")),
    GraduatedBracket(low=Decimal("298075"),  high=None,              rate=Decimal("0.025")),
)
"""TY2025 MFJ / QSS brackets per ND-1 booklet page 27."""

ND_TY2025_BRACKETS_MFS: tuple[GraduatedBracket, ...] = (
    GraduatedBracket(low=Decimal("0"),       high=Decimal("40475"),  rate=Decimal("0")),
    GraduatedBracket(low=Decimal("40475"),   high=Decimal("149025"), rate=Decimal("0.0195")),
    GraduatedBracket(low=Decimal("149025"),  high=None,              rate=Decimal("0.025")),
)

ND_TY2025_BRACKETS_HOH: tuple[GraduatedBracket, ...] = (
    GraduatedBracket(low=Decimal("0"),       high=Decimal("64950"),  rate=Decimal("0")),
    GraduatedBracket(low=Decimal("64950"),   high=Decimal("271450"), rate=Decimal("0.0195")),
    GraduatedBracket(low=Decimal("271450"),  high=None,              rate=Decimal("0.025")),
)


def nd_brackets_for_status(
    filing_status: FilingStatus,
) -> tuple[GraduatedBracket, ...]:
    """Return the ND TY2025 graduated bracket table for the filing status."""
    if filing_status in (FilingStatus.MFJ, FilingStatus.QSS):
        return ND_TY2025_BRACKETS_MFJ
    if filing_status == FilingStatus.MFS:
        return ND_TY2025_BRACKETS_MFS
    if filing_status == FilingStatus.HOH:
        return ND_TY2025_BRACKETS_HOH
    return ND_TY2025_BRACKETS_SINGLE


def nd_tax_from_schedule(
    taxable_income: Decimal, filing_status: FilingStatus
) -> Decimal:
    """Compute ND tax via the 2025 Tax Rate Schedule (booklet page 27)."""
    return graduated_tax(taxable_income, nd_brackets_for_status(filing_status))


# Marriage penalty credit thresholds (booklet page 14)
ND_TY2025_MARRIAGE_PENALTY_TI_THRESHOLD: Decimal = Decimal("81036")
"""MFJ joint ND taxable income must exceed this for the marriage
penalty credit (ND-1 line 22) to apply."""

ND_TY2025_MARRIAGE_PENALTY_LOWER_INCOME_THRESHOLD: Decimal = Decimal("47550")
"""The lower-earning spouse's qualified income must exceed this for
the marriage penalty credit to apply."""


ND_V1_LIMITATIONS: tuple[str, ...] = (
    "Schedule ND-1SA additions NOT applied: lump-sum distribution from "
    "federal Form 4972, loss from S corporation taxed as C corp, "
    "Renaissance zone income exemption, new/expanding business income "
    "exemption, college expense reimbursement deduction, employee "
    "workforce recruitment exclusion, stillborn child deduction, "
    "income from S corp taxed as C corp, human organ donor deduction.",
    "Schedule ND-1SA subtractions NOT applied: same set, on the "
    "subtraction side.",
    "ND-1 line 21 Credit for income tax paid to another state "
    "(Schedule ND-1CR) defaults to 0 — critical for ND residents who "
    "work in MN or MT outside the reciprocity scope.",
    "ND-1 line 22 Marriage penalty credit defaults to 0. The credit "
    "applies for MFJ when both spouses have qualified income, the "
    "lower spouse's qualified income exceeds $47,550, and joint ND "
    "taxable income exceeds $81,036. The fact-specific worksheet on "
    "booklet page 14 is not yet modeled.",
    "Farm income averaging (Schedule ND-1FA) defaults to standard tax. "
    "ND farmers may achieve a lower tax via 4-year averaging.",
    "Sale of ND research expense tax credit proceeds (Schedule ND-1CS) "
    "is not modeled — adjusts the line 20 tax calculation when the "
    "taxpayer sold a research credit to another taxpayer.",
    "Schedule ND-1NR (Nonresident / Part-Year Resident calculation) "
    "is replaced with day-based proration of the resident-basis tax. "
    "A real ND nonresident return tracks ND-source income on Schedule "
    "ND-1NR and computes tax on the income-source ratio basis.",
    "Renewable energy credit (Schedule ND-1RZ), historic preservation "
    "credit, and the long list of ND nonrefundable credits on the "
    "back of Schedule ND-1TC are not modeled in v1.",
    "ND has no separate AMT (non-limitation, noted for completeness).",
    "ND has reciprocity with MN and MT — a real plugin uses Schedule "
    "ND-1CR and the partner-state employee withholding certificates "
    "rather than computing tax on commuter wages.",
)


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NorthDakotaPlugin:
    """State plugin for North Dakota — TY2025.

    Hand-rolled ND-1 calc. ND-1 line 1 is **federal taxable income**
    (not federal AGI), so this plugin reads ``federal.taxable_income``
    rather than ``federal.adjusted_gross_income``. The TY2025 rate
    schedule has a 0% bracket for the first $48,475 of single ND TI,
    which makes the apparent ND tax small for typical W-2 incomes.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # ND-1 Line 1: federal taxable income (NOT federal AGI).
        federal_ti = d(federal.taxable_income)
        if federal_ti < 0:
            federal_ti = Decimal("0")

        # Lines 2-15: ND additions / subtractions (Schedule ND-1SA).
        # v1 = 0 — see ND_V1_LIMITATIONS.
        nd_additions = Decimal("0")
        nd_subtractions = Decimal("0")

        # ND-1 Line 18: ND taxable income.
        nd_taxable_income = federal_ti + nd_additions - nd_subtractions
        if nd_taxable_income < 0:
            nd_taxable_income = Decimal("0")

        # ND-1 Line 20: Tax via the 2025 Tax Rate Schedule.
        nd_tax_full = nd_tax_from_schedule(
            nd_taxable_income, federal.filing_status
        )

        # ND-1 Line 21: credit for tax paid to other states. v1 = 0.
        credit_other_state = Decimal("0")
        # ND-1 Line 22: marriage penalty credit. v1 = 0.
        marriage_penalty_credit = Decimal("0")
        # Other credits — v1 = 0.
        other_credits = Decimal("0")
        total_credits = (
            credit_other_state + marriage_penalty_credit + other_credits
        )
        nd_tax_after_credits = nd_tax_full - total_credits
        if nd_tax_after_credits < 0:
            nd_tax_after_credits = Decimal("0")
        nd_tax_after_credits = cents(nd_tax_after_credits)

        # Apportion for nonresident / part-year (day-based v1).
        if residency == ResidencyStatus.RESIDENT or days_in_state >= 365:
            nd_tax_apportioned = nd_tax_after_credits
            apportionment_fraction = Decimal("1")
        else:
            nd_tax_apportioned = day_prorate(
                nd_tax_after_credits, days_in_state=max(0, days_in_state)
            )
            apportionment_fraction = (
                Decimal(max(0, days_in_state)) / Decimal("365")
            )
            if apportionment_fraction > 1:
                apportionment_fraction = Decimal("1")

        # Bracket / effective rate diagnostics.
        brackets = nd_brackets_for_status(federal.filing_status)
        marginal_rate = Decimal("0")
        for b in brackets:
            if nd_taxable_income > b.low and (
                b.high is None or nd_taxable_income <= b.high
            ):
                marginal_rate = b.rate
                break

        state_specific: dict[str, Any] = {
            "state_federal_taxable_income": cents(federal_ti),
            "state_adjusted_gross_income": cents(
                federal.adjusted_gross_income
            ),
            "state_taxable_income": cents(nd_taxable_income),
            "state_tax_before_credits": cents(nd_tax_full),
            "state_credit_other_state": cents(credit_other_state),
            "state_marriage_penalty_credit": cents(marriage_penalty_credit),
            "state_total_credits": cents(total_credits),
            "state_total_tax": nd_tax_apportioned,
            "state_total_tax_resident_basis": nd_tax_after_credits,
            "state_marginal_rate": marginal_rate,
            "apportionment_fraction": apportionment_fraction,
            "starting_point": "federal_taxable_income",
            "v1_limitations": list(ND_V1_LIMITATIONS),
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
        """Day-prorated income split. TODO(nd-schedule-nd-1nr)."""
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

        if residency == ResidencyStatus.RESIDENT or days_in_state >= 365:
            return IncomeApportionment(
                state_source_wages=cents(wages),
                state_source_interest=cents(interest),
                state_source_dividends=cents(ord_div),
                state_source_capital_gains=cents(capital_gains),
                state_source_self_employment=cents(se_net),
                state_source_rental=cents(rental_net),
            )
        return IncomeApportionment(
            state_source_wages=day_prorate(wages, days_in_state),
            state_source_interest=day_prorate(interest, days_in_state),
            state_source_dividends=day_prorate(ord_div, days_in_state),
            state_source_capital_gains=day_prorate(
                capital_gains, days_in_state
            ),
            state_source_self_employment=day_prorate(
                se_net, days_in_state
            ),
            state_source_rental=day_prorate(rental_net, days_in_state),
        )

    def render_pdfs(
        self, state_return: StateReturn, out_dir: Path
    ) -> list[Path]:
        # TODO(nd-pdf): fan-out follow-up — fill ND-1 + Schedule ND-1SA
        # + Schedule ND-1NR against ND DOR fillable PDFs.
        return []

    def form_ids(self) -> list[str]:
        return ["ND Form ND-1"]


PLUGIN: StatePlugin = NorthDakotaPlugin(
    meta=StatePluginMeta(
        code="ND",
        name="North Dakota",
        has_income_tax=True,
        # ND-1 Line 1 is federal taxable income (not federal AGI).
        starting_point=StateStartingPoint.FEDERAL_TAXABLE_INCOME,
        dor_url="https://www.tax.nd.gov/individual",
        # ND TAP exists but is not the canonical individual e-file
        # path; commercial MeF software piggyback is.
        free_efile_url="https://apps.nd.gov/tax/tap/",
        submission_channel=SubmissionChannel.FED_STATE_PIGGYBACK,
        # ND has reciprocity with MN and MT — verified against
        # skill/reference/state-reciprocity.json.
        reciprocity_partners=("MN", "MT"),
        supported_tax_years=(2025,),
        notes=(
            "Hand-rolled ND Form ND-1 calc (tenforty does not support "
            "2025/ND_1 on the OTS backend; the graph backend's $15.11 "
            "value on the $65k Single scenario is mathematically "
            "correct due to the high zero-bracket of $48,475, but per "
            "wave 5 fan-out spec we hand-roll for DOR-traceability and "
            "richer state_specific output). Three-tier graduated rate "
            "schedule for TY2025 with bracket structure 0% / 1.95% / "
            "2.5%. Single zero-bracket cap $48,475. Starting point: "
            "federal taxable income (ND-1 line 1). Reciprocity: MN, "
            "MT. Source: 2025 ND Individual Income Tax booklet page "
            "27 (Tax Rate Schedules)."
        ),
    )
)
