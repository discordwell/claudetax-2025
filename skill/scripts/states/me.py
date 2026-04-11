"""Maine (ME) state plugin — TY2025.

*** TENFORTY DEFAULT BACKEND DOES NOT SUPPORT ME / ME GRAPH BACKEND HAS BUGS ***

Default OTS backend: ``tenforty.evaluate_return(year=2025, state='ME', ...)``
raises ``ValueError: OTS does not support 2025/ME_1040ME`` (verified
2026-04-11). The newer graph backend (``backend='graph'``) DOES return a
number, but cross-checking against the published Maine Form 1040ME flow
shows the graph backend OMITS the Maine personal exemption (Form 1040ME
line 13 / Schedule for line 13), which materially under-reports the state
tax for almost every filer:

    $65k Single  graph backend  = $3,069.78
    $65k Single  hand-rolled    = $2,722.15
    Divergence                  = $347.63

The mismatch is exactly the Maine personal exemption ($5,150 for TY2025
Single) times the 5.8% / 6.75% bracket the exemption "comes off" of. The
graph backend treats line 13 as an input that defaults to zero; nothing
in the graph automatically computes it.

Per the wave-5 decision rubric in
``skill/reference/tenforty-ty2025-gap.md``, "Material mismatch (>$5):
hand-roll from DOR primary source. The graph backend is doing something
wrong for this state — do NOT trust it." This plugin therefore hand-rolls
ME Form 1040ME from the official Maine Revenue Services tax-rate
schedules and instructions.

=============================================================================
THIS PLUGIN HAND-ROLLS THE ME FORM 1040ME CALCULATION. The locked $65k
Single tax number is the plugin's OWN computation, NOT a tenforty
graph-backend value.
=============================================================================

Form 1040ME line structure (TY2025):

    Line 14  Federal AGI  (from federal Form 1040 line 11)
    Line 15  Income modifications  (Schedule 1A additions / 1S subtractions)
    Line 16  Maine AGI  =  Line 14 + Schedule 1A - Schedule 1S
    Line 17  Maine deduction  =  itemized (Form 1040ME Sch 2) OR ME standard
    Line 18  Maine taxable income before exemption  =  Line 16 - Line 17
    Line 19  Maine personal exemption deduction  ($5,150 per exemption,
             phased out for high income — see exemption phaseout note)
    Line 20  Maine taxable income  =  max(0, Line 18 - Line 19)
    Line 21  Maine income tax (from rate schedules)

TY2025 Maine standard deduction (per ME RS Tax Alert September 2024 and
verified against tenforty's ``me_1040me_2025.json`` graph file constants):
Maine conforms to the federal standard deduction:

    Single                                       $15,750
    Married Filing Separately                    $15,750
    Head of Household                            $23,625
    Married Filing Jointly                       $31,500
    Qualifying Surviving Spouse                  $31,500

Maine standard-deduction phaseout (line 17 worksheet, per 1040ME Schedule
NRH instructions): the standard deduction phases out for AGI between
$94,250 - $169,250 Single ($188,500 - $338,500 MFJ) on a linear schedule
similar to the pre-TCJA federal Pease limitation. NOT modeled in v1 — see
``ME_V1_LIMITATIONS``.

TY2025 Maine personal exemption (per ME RS Tax Alert September 2024,
indexed from $5,000 in TY2024): **$5,150 per exemption.**

    Single                                        $5,150  (1 exemption)
    Married Filing Separately                     $5,150
    Head of Household                             $5,150
    Married Filing Jointly                       $10,300  (2 exemptions)
    Qualifying Surviving Spouse                  $10,300
    Each dependent                                $5,150

Personal exemption phaseout: phases out between AGI $98,150 - $173,150
Single ($196,300 - $346,300 MFJ) on a linear "1/$2,500" schedule (1/30 of
the exemption per $2,500 over the floor for non-MFJ; per $5,000 for MFJ).
NOT modeled in v1.

TY2025 Maine bracket schedule (verified against tenforty's
``me_1040me_2025.json`` graph file ``me_brackets_2025`` table — the
bracket constants there match the Maine RS published TY2025 schedule):

    Single / Married Filing Separately
        5.80%   on $0 - $26,800
        6.75%   on $26,800 - $63,450
        7.15%   on $63,450+

    Married Filing Jointly / Qualifying Surviving Spouse
        5.80%   on $0 - $53,600
        6.75%   on $53,600 - $126,900
        7.15%   on $126,900+

    Head of Household
        5.80%   on $0 - $40,200
        6.75%   on $40,200 - $95,150
        7.15%   on $95,150+

$65k Single / Standard wrap-correctness lock (this plugin's own math):

    Line 14  Federal AGI                $65,000.00
    Line 15  Income modifications            $0.00
    Line 16  Maine AGI                  $65,000.00
    Line 17  ME Standard Deduction      $15,750.00
    Line 18  Subtotal                   $49,250.00
    Line 19  Personal Exemption          $5,150.00
    Line 20  Maine Taxable Income       $44,100.00
    Line 21  Maine Income Tax
             0-26,800 @ 5.80%            $1,554.40
             26,800-44,100 @ 6.75%       $1,167.75
             Total                       $2,722.15

The test suite pins ``state_total_tax == Decimal('2722.15')`` for this
scenario.

For comparison, the tenforty graph backend (``backend='graph'``) returns
$3,069.78 for the same scenario — the +$347.63 delta is the missing
personal exemption applied at the marginal rate (5,150 * 0.0675 = 347.63).
The plugin's ``state_specific`` payload exposes both numbers under
``state_total_tax`` (canonical, hand-rolled) and
``state_total_tax_graph_backend`` (the tenforty graph value, for drift
detection — pinned in tests).

Reciprocity: Maine has **NO** bilateral income tax reciprocity agreements
with any state — verified against ``skill/reference/state-reciprocity.json``
(ME does not appear in ``agreements``) and against the Tax Foundation's
"State Reciprocity Agreements" research page. Maine residents working in
NH (no income tax) or MA file the appropriate work-state return and claim
the Maine credit for taxes paid to other states.

Submission channel: Maine operates "Maine Tax Portal" (formerly Maine
EZ Pay / I-File) at https://revenue.maine.gov/ as its free direct-file
portal for individual returns. ME also participates in the IRS Fed/State
MeF program for commercial software piggyback filings. The canonical free
path for an individual is the state's own portal, so this plugin reports
``SubmissionChannel.STATE_DOR_FREE_PORTAL``.

Sources (verified 2026-04-11):

    - Maine Revenue Services, Income/Estate Tax forms hub:
      https://www.maine.gov/revenue/tax-return-forms/income-estate-tax

    - Maine Revenue Services, Form 1040ME 2025 (filed copy):
      https://www.maine.gov/revenue/sites/maine.gov.revenue/files/inline-files/25_1040me_dwnld.pdf
      (URL pattern; 2024 file lives at .../24_1040me_book_dwnld.pdf)

    - Maine Revenue Services Tax Alert September 2024, "Individual Income
      Tax 2025 Rate Schedules and Personal Exemption / Standard
      Deduction Amounts":
      https://www.maine.gov/revenue/publications/tax-alerts

    - tenforty graph backend ``me_1040me_2025.json`` (verifies bracket
      constants match Maine RS published TY2025 schedule):
      $VENV/lib/python3.12/site-packages/tenforty/forms/me_1040me_2025.json

Nonresident / part-year handling: ME nonresident filers use Form 1040ME
Schedule NR (Nonresident Credit) which prorates the resident-basis tax
by a Maine-source-income ratio. Day-based proration is the v1
approximation, consistent with the other wave-4/5 hand-rolled plugins.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
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
    day_prorate,
    sourced_or_prorated_schedule_c,
    sourced_or_prorated_wages,
    state_has_w2_state_rows,
    state_source_schedule_c,
    state_source_wages_from_w2s,
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


# Canonical wave-5 $65k Single gatekeeper lock. Hand-traced from ME
# Form 1040ME — see module docstring. Referenced from test_state_me.py.
LOCK_VALUE: Final[Decimal] = Decimal("2722.15")


# ---------------------------------------------------------------------------
# TY2025 constants
# ---------------------------------------------------------------------------


# Maine standard deduction by filing status (TY2025).
# Source: Maine RS Tax Alert September 2024 + tenforty graph file
# me_1040me_2025.json. Maine conforms to the federal standard deduction
# amounts as updated by OBBBA for TY2025.
ME_TY2025_STANDARD_DEDUCTION: dict[FilingStatus, Decimal] = {
    FilingStatus.SINGLE: Decimal("15750"),
    FilingStatus.MFS: Decimal("15750"),
    FilingStatus.HOH: Decimal("23625"),
    FilingStatus.MFJ: Decimal("31500"),
    FilingStatus.QSS: Decimal("31500"),
}


# Maine personal exemption per exemption (TY2025).
# Source: Maine RS Tax Alert September 2024. Indexed from $5,000 (TY2024)
# to $5,150 (TY2025). NOT inflation-conformed to federal — Maine kept the
# personal exemption when the federal one was zeroed out by TCJA, and
# Maine's amount is its own statutory inflation index.
ME_TY2025_PERSONAL_EXEMPTION_PER_PERSON: Decimal = Decimal("5150")


# Number of "filer" exemptions baked into the filing status. MFJ and QSS
# get two filer exemptions; everyone else gets one. Each dependent then
# adds another exemption.
ME_TY2025_FILER_EXEMPTIONS: dict[FilingStatus, int] = {
    FilingStatus.SINGLE: 1,
    FilingStatus.MFS: 1,
    FilingStatus.HOH: 1,
    FilingStatus.MFJ: 2,
    FilingStatus.QSS: 2,
}


# Maine bracket schedule by filing status (TY2025).
# Source: tenforty me_1040me_2025.json (me_brackets_2025) — these
# constants match the Maine RS Tax Alert September 2024 published TY2025
# rate schedules. The thresholds happen to be unchanged from TY2024
# (Maine RS rounded the inflation index to no change for the bottom two
# breakpoints).
ME_TY2025_BRACKETS_SINGLE: tuple[GraduatedBracket, ...] = (
    GraduatedBracket(low=Decimal("0"),     high=Decimal("26800"), rate=Decimal("0.058")),
    GraduatedBracket(low=Decimal("26800"), high=Decimal("63450"), rate=Decimal("0.0675")),
    GraduatedBracket(low=Decimal("63450"), high=None,             rate=Decimal("0.0715")),
)
ME_TY2025_BRACKETS_MFJ: tuple[GraduatedBracket, ...] = (
    GraduatedBracket(low=Decimal("0"),      high=Decimal("53600"),  rate=Decimal("0.058")),
    GraduatedBracket(low=Decimal("53600"),  high=Decimal("126900"), rate=Decimal("0.0675")),
    GraduatedBracket(low=Decimal("126900"), high=None,              rate=Decimal("0.0715")),
)
ME_TY2025_BRACKETS_HOH: tuple[GraduatedBracket, ...] = (
    GraduatedBracket(low=Decimal("0"),     high=Decimal("40200"), rate=Decimal("0.058")),
    GraduatedBracket(low=Decimal("40200"), high=Decimal("95150"), rate=Decimal("0.0675")),
    GraduatedBracket(low=Decimal("95150"), high=None,             rate=Decimal("0.0715")),
)
# MFS uses the Single schedule. QSS uses the MFJ schedule. (Same as
# Maine RS prints them.)
ME_TY2025_BRACKETS: dict[FilingStatus, tuple[GraduatedBracket, ...]] = {
    FilingStatus.SINGLE: ME_TY2025_BRACKETS_SINGLE,
    FilingStatus.MFS: ME_TY2025_BRACKETS_SINGLE,
    FilingStatus.HOH: ME_TY2025_BRACKETS_HOH,
    FilingStatus.MFJ: ME_TY2025_BRACKETS_MFJ,
    FilingStatus.QSS: ME_TY2025_BRACKETS_MFJ,
}


# Reference value: tenforty's graph backend returns this for $65k Single
# Standard. The plugin pins this in tests so any tenforty drift trips CI
# and the agent can reconcile by hand. This is NOT what the plugin's
# canonical state_total_tax reports — see the module docstring for the
# divergence rationale.
ME_TY2025_GRAPH_BACKEND_65K_SINGLE: Decimal = Decimal("3069.78")


ME_V1_LIMITATIONS: tuple[str, ...] = (
    "Maine Schedule 1A additions NOT modeled: state/municipal bond "
    "interest from non-Maine sources, state income tax refund add-back "
    "(if itemized prior year), bonus depreciation add-back, federal "
    "QBI add-back (Maine partially conforms), 529 plan rollovers, "
    "PTE-elected entity tax modifications, and other Schedule 1A items.",
    "Maine Schedule 1S subtractions NOT modeled: US Government bond "
    "interest, Maine pension income deduction (up to $45,864 for 2025 "
    "per ME RS), social security benefits subtraction, military pension "
    "subtraction, premium tax credit subtraction, qualified educational "
    "savings program contributions, contributions to Maine 529 plan, "
    "and other Schedule 1S items.",
    "Maine itemized deductions (Form 1040ME Schedule 2) NOT modeled. "
    "v1 always takes the Maine standard deduction. Maine's itemized "
    "schedule starts from federal Schedule A and adds back state income "
    "tax (no double-deduction) and applies a $30,400 cap (TY2025 "
    "indexed) on non-medical itemized deductions.",
    "Maine standard-deduction phaseout NOT modeled — high-AGI filers "
    "($94,250 - $169,250 Single, $188,500 - $338,500 MFJ for TY2025) "
    "see a linear phaseout of the standard deduction (1/30 reduction per "
    "$2,500 of AGI over the floor). v1 applies the flat std ded at all "
    "AGIs.",
    "Maine personal exemption phaseout NOT modeled — high-AGI filers "
    "($98,150 - $173,150 Single, $196,300 - $346,300 MFJ for TY2025) "
    "see a linear phaseout of the personal exemption. v1 applies the "
    "flat $5,150-per-exemption deduction at all AGIs.",
    "Maine credits NOT modeled: property tax fairness credit (refundable, "
    "Form 1040ME Schedule PTFC), child care credit, earned income tax "
    "credit (Maine EITC = 25% of federal EITC, refundable for residents), "
    "credit for income tax paid to other state (Schedule NRH), retirement "
    "savings contribution credit, sales tax fairness credit (Schedule "
    "STFC), elderly tax credit, and other Form 1040ME Schedule A credits.",
    "Maine Alternative Minimum Tax NOT modeled — Maine no longer "
    "imposes a separate state AMT (repealed effective TY2018), so this "
    "is a non-limitation but noted for completeness.",
    "Nonresident / part-year apportionment uses day-based proration "
    "(days_in_state / 365) instead of the Maine-source-income ratio "
    "from Form 1040ME Schedule NR. A real ME nonresident filer computes "
    "the full-year resident-basis tax then prorates by Maine-source "
    "wages / Maine-source AGI / total AGI.",
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def me_standard_deduction(filing_status: FilingStatus) -> Decimal:
    """Return the TY2025 Maine standard deduction for the filing status."""
    return ME_TY2025_STANDARD_DEDUCTION.get(
        filing_status, ME_TY2025_STANDARD_DEDUCTION[FilingStatus.SINGLE]
    )


def me_personal_exemption(
    filing_status: FilingStatus, num_dependents: int
) -> Decimal:
    """Maine personal exemption deduction (Form 1040ME line 19).

    $5,150 per exemption (filer + dependents). Single/HOH/MFS get one
    filer exemption; MFJ/QSS get two. Plus ``num_dependents`` exemptions.

    Does NOT apply the high-AGI phaseout — see ``ME_V1_LIMITATIONS``.
    """
    filers = ME_TY2025_FILER_EXEMPTIONS.get(filing_status, 1)
    deps = max(0, num_dependents)
    total_count = filers + deps
    return Decimal(total_count) * ME_TY2025_PERSONAL_EXEMPTION_PER_PERSON


def me_bracket_tax(
    taxable_income: Decimal, filing_status: FilingStatus
) -> Decimal:
    """Apply the TY2025 ME bracket schedule to a taxable-income amount.

    Returns a non-negative Decimal rounded to cents. Negative or zero
    taxable income yields zero.
    """
    if taxable_income <= 0:
        return Decimal("0.00")
    schedule = ME_TY2025_BRACKETS.get(filing_status, ME_TY2025_BRACKETS_SINGLE)
    return graduated_tax(taxable_income, schedule)


def me_taxable_income(
    federal: FederalTotals,
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    """Compute Maine taxable income (Form 1040ME line 20).

    Returns ``(line_16_me_agi, line_17_deduction, line_18_subtotal,
    line_19_personal_exemption, line_20_taxable_income)``.

    v1 treats Schedule 1A additions and Schedule 1S subtractions as zero.
    """
    line_16 = federal.adjusted_gross_income  # ME AGI ≈ Federal AGI in v1
    line_17 = me_standard_deduction(federal.filing_status)
    line_18 = line_16 - line_17
    if line_18 < 0:
        line_18 = Decimal("0")
    line_19 = me_personal_exemption(
        federal.filing_status, federal.num_dependents
    )
    line_20 = line_18 - line_19
    if line_20 < 0:
        line_20 = Decimal("0")
    return (line_16, line_17, line_18, line_19, line_20)


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MainePlugin:
    """State plugin for Maine — TY2025.

    Hand-rolled Form 1040ME calc. tenforty's default backend does NOT
    support 2025/ME_1040ME (raises ``ValueError``); the graph backend
    returns a number but omits the Maine personal exemption, producing
    a +$347 over-statement vs the DOR primary source for a $65k Single
    filer. See module docstring.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        line_16, line_17, line_18, line_19, line_20 = me_taxable_income(
            federal
        )
        line_21 = me_bracket_tax(line_20, federal.filing_status)

        # Apportion for nonresident / part-year (day-based v1).
        # TODO(me-schedule-nr): replace with Form 1040ME Schedule NR
        # Maine-source-income ratio in fan-out.
        if residency == ResidencyStatus.RESIDENT:
            state_tax_apportioned = cents(line_21)
            apportionment_fraction = Decimal("1")
        else:
            state_tax_apportioned = day_prorate(line_21, days_in_state)
            if days_in_state >= 365:
                apportionment_fraction = Decimal("1")
            elif days_in_state <= 0:
                apportionment_fraction = Decimal("0")
            else:
                apportionment_fraction = Decimal(days_in_state) / Decimal("365")

        state_specific: dict[str, Any] = {
            "state_adjusted_gross_income": cents(line_16),
            "state_taxable_income": cents(line_20),
            # Canonical state tax — hand-rolled. NOT the graph-backend
            # number; see module docstring for the +$348 divergence.
            "state_total_tax": state_tax_apportioned,
            "state_total_tax_resident_basis": cents(line_21),
            # Pinned tenforty graph-backend value for $65k Single — drift
            # detection only. The plugin's canonical answer is line_21
            # above, NOT this number.
            "state_total_tax_graph_backend_65k_single_reference": (
                ME_TY2025_GRAPH_BACKEND_65K_SINGLE
            ),
            "apportionment_fraction": apportionment_fraction,
            # 1040ME line-level detail for downstream rendering.
            "me_line_14_federal_agi": cents(federal.adjusted_gross_income),
            "me_line_15_modifications": Decimal("0.00"),
            "me_line_16_me_agi": cents(line_16),
            "me_line_17_deduction": cents(line_17),
            "me_line_18_subtotal": cents(line_18),
            "me_line_19_personal_exemption": cents(line_19),
            "me_line_20_taxable_income": cents(line_20),
            "me_line_21_tax": cents(line_21),
            "starting_point": "federal_agi",
            "tenforty_supports_me_default_backend": False,
            "tenforty_supports_me_graph_backend": True,
            "tenforty_status_note": (
                "tenforty default OTS backend does not support "
                "2025/ME_1040ME (raises ValueError). The graph backend "
                "(backend='graph') returns a number but it omits the "
                "Maine personal exemption (Form 1040ME line 19), "
                "producing a +$347.63 over-statement on a $65k Single "
                "return. This plugin hand-rolls the calc against the "
                "Maine RS published bracket schedule and personal "
                "exemption from Tax Alert Sept 2024. See module "
                "docstring."
            ),
            "v1_limitations": list(ME_V1_LIMITATIONS),
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
        """Split canonical income into ME-source vs non-ME-source.

        Residents: everything is ME-source. Nonresident / part-year:
        prorate each category by ``days_in_state / 365``.

        TODO(me-schedule-nr): real Maine sourcing on Form 1040ME Schedule
        NR uses ME-source wages, ME-source business income (sourced to
        the location of activity), ME-source rental (to the property
        state), and ME-source intangibles (to the domicile). Day-based
        proration is the shared first-cut.
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
            state_source_wages=sourced_or_prorated_wages(return_, "ME", wages, days_in_state),
            state_source_interest=day_prorate(interest, days_in_state),
            state_source_dividends=day_prorate(ord_div, days_in_state),
            state_source_capital_gains=day_prorate(capital_gains, days_in_state),
            state_source_self_employment=sourced_or_prorated_schedule_c(return_, "ME", se_net, days_in_state),
            state_source_rental=day_prorate(rental_net, days_in_state),
        )

    def render_pdfs(
        self, state_return: StateReturn, out_dir: Path
    ) -> list[Path]:
        # TODO(me-pdf): fan-out follow-up — fill Form 1040ME (and
        # Schedule 1A, Schedule 1S, Schedule NR for nonresidents,
        # Schedule PTFC for property tax fairness credit) using pypdf
        # against the Maine RS fillable PDFs.
        return []

    def form_ids(self) -> list[str]:
        return ["ME Form 1040ME"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = MainePlugin(
    meta=StatePluginMeta(
        code="ME",
        name="Maine",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://www.maine.gov/revenue/tax-return-forms/income-estate-tax",
        free_efile_url="https://revenue.maine.gov/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # Maine has NO bilateral reciprocity agreements — verified
        # against skill/reference/state-reciprocity.json (ME does not
        # appear in `agreements`) and against Tax Foundation 2024.
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Hand-rolled ME Form 1040ME calc. tenforty default backend "
            "does NOT support 2025/ME_1040ME (raises ValueError); the "
            "graph backend returns a number but omits the Maine "
            "personal exemption (Form 1040ME line 19) — graph reports "
            "$3,069.78 for $65k Single, hand-rolled is $2,722.15. "
            "Three-bracket graduated schedule for TY2025: 5.80% / 6.75% "
            "/ 7.15% (Single $0-$26,800, $26,800-$63,450, $63,450+). "
            "Standard deduction conforms to federal: $15,750 Single, "
            "$31,500 MFJ, $23,625 HOH. Personal exemption $5,150 per "
            "exemption. Starting point: federal AGI. No reciprocity "
            "agreements. Free e-file via Maine Tax Portal. Source: "
            "Maine RS Tax Alert September 2024 + tenforty graph file "
            "me_1040me_2025.json bracket constants."
        ),
    )
)
