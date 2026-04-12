"""West Virginia (WV) state plugin — TY2025.

*** TENFORTY DEFAULT BACKEND DOES NOT SUPPORT WV / GRAPH BACKEND HAS BUGS ***

Default OTS backend: ``tenforty.evaluate_return(year=2025, state='WV', ...)``
raises ``ValueError: OTS does not support 2025/WV_IT140`` (verified
2026-04-11). The newer graph backend (``backend='graph'``) DOES return a
number, but cross-checking against the published WV Form IT-140 flow
shows the graph backend OMITS the WV personal exemption (Form IT-140
line 6, "Total exemptions × $2,000"), which materially under-reports
relief for almost every filer:

    $65k Single  graph backend  = $2,294.50
    $65k Single  hand-rolled    = $2,198.10
    Divergence                  = $96.40

The mismatch is exactly the WV personal exemption ($2,000 per exemption,
statutory in WV Code §11-21-16) times the 4.82% top-bracket rate
($2,000 × 0.0482 = $96.40). The graph backend treats line 6 (WV total
exemptions) as an input that defaults to zero; nothing in the graph
auto-computes it from filing status and dependent count.

Per the wave-5 decision rubric in
``skill/reference/tenforty-ty2025-gap.md``, "Material mismatch (>$5):
hand-roll from DOR primary source. The graph backend is doing something
wrong for this state — do NOT trust it." This plugin therefore hand-rolls
WV Form IT-140 from the official West Virginia State Tax Department rate
schedule and instructions.

=============================================================================
THIS PLUGIN HAND-ROLLS THE WV FORM IT-140 CALCULATION. The locked $65k
Single tax number is the plugin's OWN computation, NOT a tenforty
graph-backend value.
=============================================================================

Form IT-140 line structure (TY2025):

    Line 1   Federal AGI  (from federal Form 1040 line 11)
    Line 2   Increasing modifications
    Line 3   Decreasing modifications
    Line 4   WV AGI  =  Line 1 + Line 2 - Line 3
    Line 5   Low Income Earned Income Exclusion
    Line 6   Total exemptions ($2,000 × number of exemptions)
    Line 7   WV taxable income  =  max(0, Line 4 - Line 5 - Line 6)
    Line 8   WV income tax  (from rate schedule or tax table)
    Line 9   Family Tax Credit (refundable, low-income only)

Important features of WV's tax base (different from most states):

  * **No state standard deduction.** WV starts from federal AGI and goes
    directly to its own deductions (low-income exclusion + personal
    exemption). There is no equivalent to a federal-style std ded.
  * **Personal exemption is statutory $2,000 per exemption** (NOT
    inflation-indexed) per WV Code §11-21-16. Each "exemption" is a
    filer (one for Single/HOH/MFS, two for MFJ) plus each dependent.
  * **Low-Income Earned Income Exclusion** (LIEEI) on Line 5 is a
    relief mechanism for filers under thresholds — NOT modeled in v1
    (filer must be at or below $10,000 federal AGI to qualify).
  * **Family Tax Credit** (Line 9) is a refundable credit for filers
    below FAGI thresholds — NOT modeled in v1 (typically only fires
    below ~$25,000 AGI for Single).

TY2025 WV personal exemption: **$2,000 per exemption** (statutory).

    Single                                       $2,000  (1 exemption)
    Married Filing Separately                    $2,000
    Head of Household                            $2,000
    Married Filing Jointly                       $4,000  (2 exemptions)
    Qualifying Surviving Spouse                  $4,000
    Each dependent                               $2,000
    Surviving spouse special exemption           $2,000  (one-time, year of
                                                          spouse's death,
                                                          not modeled)

TY2025 WV bracket schedule (verified against tenforty's
``wv_it140_2025.json`` ``wv_brackets_2025`` table — these constants are
the post-HB 2024 reduced rates, the second of West Virginia's two
trigger-driven cuts under HB 2526 of 2023):

    Single / Head of Household / MFJ / QSS:
        2.22%   on $0 - $10,000
        2.96%   on $10,000 - $25,000
        3.33%   on $25,000 - $40,000
        4.44%   on $40,000 - $60,000
        4.82%   on $60,000+

    Married Filing Separately uses HALF the breakpoints:
        2.22%   on $0 - $5,000
        2.96%   on $5,000 - $12,500
        3.33%   on $12,500 - $20,000
        4.44%   on $20,000 - $30,000
        4.82%   on $30,000+

WV is unusual in that the same brackets apply to Single/HOH/MFJ/QSS —
West Virginia does NOT widen brackets for joint filers, unlike most
states. (MFS uses half-brackets to maintain MFJ-equivalent tax burden.)

$65k Single / Standard wrap-correctness lock (this plugin's own math):

    Line 1   Federal AGI                $65,000.00
    Line 2-3 Modifications                   $0.00
    Line 4   WV AGI                     $65,000.00
    Line 5   Low Income Exclusion            $0.00
    Line 6   Personal Exemption          $2,000.00
    Line 7   WV Taxable Income          $63,000.00
    Line 8   WV Income Tax:
             $0-$10,000  @ 2.22%          $222.00
             $10k-$25k   @ 2.96%          $444.00
             $25k-$40k   @ 3.33%          $499.50
             $40k-$60k   @ 4.44%          $888.00
             $60k-$63k   @ 4.82%          $144.60
             Total                       $2,198.10

The test suite pins ``state_total_tax == Decimal('2198.10')`` for this
scenario.

For comparison, the tenforty graph backend (``backend='graph'``) returns
$2,294.50 for the same scenario — the +$96.40 delta is exactly one
personal exemption applied at the 4.82% top-bracket rate.

Reciprocity (CRITICAL — WV has the most reciprocity partners in this
fan-out wave): West Virginia has **FIVE** bilateral income tax
reciprocity agreements:

    - Kentucky (KY)         {"states": ["KY", "WV"]}
    - Maryland (MD)         {"states": ["MD", "WV"]}
    - Ohio (OH)             {"states": ["OH", "WV"]}
    - Pennsylvania (PA)     {"states": ["PA", "WV"]}
    - Virginia (VA)         {"states": ["VA", "WV"]}

Verified against ``skill/reference/state-reciprocity.json`` (all five
pairs are present in the ``agreements`` array) and against the WV State
Tax Department FAQ on reciprocal states. Residents of WV who work in
any of these five neighbors do not file a nonresident return in the
work state — the work-state employer accepts a WV-IT104R reciprocity
form and withholds WV tax instead. Conversely, residents of those five
states working in WV do not file WV nonresident returns.

This plugin's ``meta.reciprocity_partners`` tuple contains exactly
("KY", "MD", "OH", "PA", "VA"). A test asserts the exact set against
the reciprocity JSON so accidental drift fails CI.

Submission channel: West Virginia operates "MyTaxes" at
https://mytaxes.wvtax.gov/ as its free direct-file portal for individual
income tax. WV also participates in the IRS Fed/State MeF program for
commercial software piggyback filings. The canonical free path for an
individual is the state's own portal, so this plugin reports
``SubmissionChannel.STATE_DOR_FREE_PORTAL``.

Sources (verified 2026-04-11):

    - West Virginia Tax Division, Individuals page:
      https://tax.wv.gov/Individuals/Pages/default.aspx

    - West Virginia State Tax Department, "2025 IT-140 West Virginia
      Personal Income Tax Return Booklet" (filed copy)

    - WV HB 2526 of 2023 (initial 21.25% rate cut) and HB 4007 / SB 1009
      of 2024 (second trigger-driven cut): codified at WV Code §11-21-4e

    - WV Code §11-21-16 (personal exemption: $2,000 per exemption,
      statutory, NOT indexed)

    - tenforty graph backend ``wv_it140_2025.json``:
      $VENV/lib/python3.12/site-packages/tenforty/forms/wv_it140_2025.json

Nonresident / part-year handling: WV nonresident filers use Form
IT-140NRS (nonresident special) or Schedule A of Form IT-140 to source
WV income. v1 uses day-based proration as a first-order approximation;
real WV-source-income sourcing is fan-out follow-up (TODO(wv-it140nrs)).
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


# Canonical wave-5 $65k Single gatekeeper lock. Hand-traced from WV
# Form IT-140 — see module docstring. Referenced from test_state_wv.py.
LOCK_VALUE: Final[Decimal] = Decimal("2198.10")


# ---------------------------------------------------------------------------
# TY2025 constants
# ---------------------------------------------------------------------------


# WV personal exemption per exemption (TY2025 — and every prior year).
# WV Code §11-21-16: $2,000 per exemption, statutory, NOT inflation-indexed.
WV_TY2025_PERSONAL_EXEMPTION_PER_PERSON: Decimal = Decimal("2000")


# Number of "filer" exemptions baked into the filing status. MFJ and QSS
# get two filer exemptions; everyone else gets one. Each dependent then
# adds another exemption.
WV_TY2025_FILER_EXEMPTIONS: dict[FilingStatus, int] = {
    FilingStatus.SINGLE: 1,
    FilingStatus.MFS: 1,
    FilingStatus.HOH: 1,
    FilingStatus.MFJ: 2,
    FilingStatus.QSS: 2,
}


# WV bracket schedule for Single / HOH / MFJ / QSS (TY2025).
# Source: tenforty wv_it140_2025.json + WV Code §11-21-4e as amended by
# HB 2526 (2023) and HB 4007 / SB 1009 (2024). These are the post-trigger
# reduced rates.
#
# CRITICAL: West Virginia uses the SAME breakpoints for Single, HOH, MFJ,
# and QSS — there is no "joint widening" of brackets. MFS uses HALVED
# breakpoints to preserve symmetry.
WV_TY2025_BRACKETS_NON_MFS: tuple[GraduatedBracket, ...] = (
    GraduatedBracket(low=Decimal("0"),     high=Decimal("10000"), rate=Decimal("0.0222")),
    GraduatedBracket(low=Decimal("10000"), high=Decimal("25000"), rate=Decimal("0.0296")),
    GraduatedBracket(low=Decimal("25000"), high=Decimal("40000"), rate=Decimal("0.0333")),
    GraduatedBracket(low=Decimal("40000"), high=Decimal("60000"), rate=Decimal("0.0444")),
    GraduatedBracket(low=Decimal("60000"), high=None,             rate=Decimal("0.0482")),
)
WV_TY2025_BRACKETS_MFS: tuple[GraduatedBracket, ...] = (
    GraduatedBracket(low=Decimal("0"),     high=Decimal("5000"),  rate=Decimal("0.0222")),
    GraduatedBracket(low=Decimal("5000"),  high=Decimal("12500"), rate=Decimal("0.0296")),
    GraduatedBracket(low=Decimal("12500"), high=Decimal("20000"), rate=Decimal("0.0333")),
    GraduatedBracket(low=Decimal("20000"), high=Decimal("30000"), rate=Decimal("0.0444")),
    GraduatedBracket(low=Decimal("30000"), high=None,             rate=Decimal("0.0482")),
)

WV_TY2025_BRACKETS: dict[FilingStatus, tuple[GraduatedBracket, ...]] = {
    FilingStatus.SINGLE: WV_TY2025_BRACKETS_NON_MFS,
    FilingStatus.HOH: WV_TY2025_BRACKETS_NON_MFS,
    FilingStatus.MFJ: WV_TY2025_BRACKETS_NON_MFS,
    FilingStatus.QSS: WV_TY2025_BRACKETS_NON_MFS,
    FilingStatus.MFS: WV_TY2025_BRACKETS_MFS,
}


# Reference value: tenforty's graph backend returns this for $65k Single
# Standard. Pinned in tests as a drift detector.
WV_TY2025_GRAPH_BACKEND_65K_SINGLE: Decimal = Decimal("2294.50")


# WV reciprocity partners — verified against
# skill/reference/state-reciprocity.json. WV has FIVE bilateral
# reciprocity agreements: KY, MD, OH, PA, VA.
WV_RECIPROCITY_PARTNERS: tuple[str, ...] = ("KY", "MD", "OH", "PA", "VA")


WV_V1_LIMITATIONS: tuple[str, ...] = (
    "WV increasing modifications NOT modeled (Form IT-140 line 2): "
    "interest on out-of-state municipal bonds, federal NOL addback, "
    "section 179 carryforward addback, withdrawals from a WV Jumpstart "
    "savings account used for non-qualified purposes, REIT capital gain "
    "modifications, and other Schedule M Part 1 items.",
    "WV decreasing modifications NOT modeled (Form IT-140 line 3): "
    "interest on US government obligations (Treasury bonds), WV Teachers "
    "Retirement subtraction, WV Public Employees' Retirement subtraction, "
    "federal Social Security benefits taxed federally (WV fully exempts "
    "Social Security from state tax for AGI under thresholds — "
    "complete-phase-in completed in TY2025 per HB 4001 of 2024), "
    "first-time home buyer savings account contributions, contributions "
    "to WV Jumpstart savings, military retirement subtraction, and other "
    "Schedule M Part 2 items.",
    "WV Low-Income Earned Income Exclusion (LIEEI) on Form IT-140 line 5 "
    "NOT modeled. The exclusion provides relief for very-low-income "
    "filers (typically below $10,000 federal AGI). v1 sets line 5 to "
    "zero.",
    "WV Family Tax Credit on Form IT-140 line 9 NOT modeled. The credit "
    "is refundable for filers below filing-status-specific FAGI "
    "thresholds (typically below $25,000 AGI for Single). v1 sets line 9 "
    "to zero.",
    "WV nonrefundable credits NOT modeled (Schedule TC-1): credit for "
    "income tax paid to other states (critical for multi-state filers), "
    "WV apprenticeship training credit, contractor's manufacturing "
    "investment tax credit, neighborhood investment program credit, "
    "homestead excess property tax credit, and other Schedule TC-1 items.",
    "WV refundable credits NOT modeled (Schedule TC-2): WV Earned Income "
    "Tax Credit (refundable, 5% of federal EITC for TY2025 per HB 5024 of "
    "2024), Senior Citizen tax credit, Low-Income Family Tax Credit "
    "above the line 9 base amount, and other Schedule TC-2 items.",
    "WV Senior Citizen / Low-Income Property Tax Refund (Schedule TR-1) "
    "NOT modeled.",
    "Surviving-spouse special exemption (one-time additional $2,000 in "
    "the year of the spouse's death) NOT modeled — v1 only applies the "
    "base filing-status exemption + dependents.",
    "Nonresident / part-year apportionment uses day-based proration "
    "(days_in_state / 365) instead of the WV-source-income ratio from "
    "Form IT-140NRS / Schedule A. A real WV nonresident filer computes "
    "the resident-basis tax then prorates by WV-source income.",
    "Reciprocity withholding mechanics: a WV resident working in KY, MD, "
    "OH, PA, or VA does NOT file a nonresident return in those states; "
    "they file form IT-104R with their employer to direct WV "
    "withholding instead. v1's day-proration logic does not "
    "auto-suppress nonresident filings for reciprocity-pair scenarios — "
    "downstream multi-state planning must inspect "
    "ReciprocityTable.are_reciprocal('WV', work_state) and skip the "
    "work-state nonresident plugin call when True.",
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def wv_personal_exemption(
    filing_status: FilingStatus, num_dependents: int
) -> Decimal:
    """WV personal exemption deduction (Form IT-140 line 6).

    $2,000 per exemption (filer + dependents). Single/HOH/MFS get one
    filer exemption; MFJ/QSS get two. Plus ``num_dependents`` exemptions.

    Does NOT apply the surviving-spouse special exemption.
    """
    filers = WV_TY2025_FILER_EXEMPTIONS.get(filing_status, 1)
    deps = max(0, num_dependents)
    total_count = filers + deps
    return Decimal(total_count) * WV_TY2025_PERSONAL_EXEMPTION_PER_PERSON


def wv_bracket_tax(
    taxable_income: Decimal, filing_status: FilingStatus
) -> Decimal:
    """Apply the TY2025 WV bracket schedule to a taxable-income amount.

    Returns a non-negative Decimal rounded to cents. Negative or zero
    taxable income yields zero. MFS uses the half-bracket schedule;
    everyone else uses the full schedule.
    """
    if taxable_income <= 0:
        return Decimal("0.00")
    schedule = WV_TY2025_BRACKETS.get(
        filing_status, WV_TY2025_BRACKETS_NON_MFS
    )
    return graduated_tax(taxable_income, schedule)


def wv_taxable_income(
    federal: FederalTotals,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Compute West Virginia taxable income (Form IT-140 line 7).

    Returns ``(line_4_wv_agi, line_5_lieei, line_6_exemption,
    line_7_taxable_income)``.

    v1 treats lines 2-3 (modifications) and line 5 (LIEEI) as zero.
    """
    line_4 = federal.adjusted_gross_income  # +0 modifications in v1
    line_5 = Decimal("0")  # LIEEI not modeled
    line_6 = wv_personal_exemption(
        federal.filing_status, federal.num_dependents
    )
    line_7 = line_4 - line_5 - line_6
    if line_7 < 0:
        line_7 = Decimal("0")
    return (line_4, line_5, line_6, line_7)


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WestVirginiaPlugin:
    """State plugin for West Virginia — TY2025.

    Hand-rolled Form IT-140 calc. tenforty's default backend does NOT
    support 2025/WV_IT140 (raises ValueError); the graph backend returns
    a number but omits the WV personal exemption ($2,000 per filer +
    dependent), producing a +$96.40 over-statement vs the DOR primary
    source for a $65k Single filer. See module docstring.

    West Virginia is the only wave-5 state with a meaningful reciprocity
    network: KY, MD, OH, PA, VA (5 bilateral partners). Test
    ``test_reciprocity_partners_match_json`` enforces the exact set.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        line_4, line_5, line_6, line_7 = wv_taxable_income(federal)
        line_8 = wv_bracket_tax(line_7, federal.filing_status)

        if residency == ResidencyStatus.RESIDENT:
            state_tax_apportioned = cents(line_8)
            apportionment_fraction = Decimal("1")
        else:
            state_tax_apportioned = day_prorate(line_8, days_in_state)
            if days_in_state >= 365:
                apportionment_fraction = Decimal("1")
            elif days_in_state <= 0:
                apportionment_fraction = Decimal("0")
            else:
                apportionment_fraction = Decimal(days_in_state) / Decimal("365")

        state_specific: dict[str, Any] = {
            "state_adjusted_gross_income": cents(line_4),
            "state_taxable_income": cents(line_7),
            "state_total_tax": state_tax_apportioned,
            "state_total_tax_resident_basis": cents(line_8),
            "state_total_tax_graph_backend_65k_single_reference": (
                WV_TY2025_GRAPH_BACKEND_65K_SINGLE
            ),
            "apportionment_fraction": apportionment_fraction,
            "wv_line_1_federal_agi": cents(federal.adjusted_gross_income),
            "wv_line_2_increasing_modifications": Decimal("0.00"),
            "wv_line_3_decreasing_modifications": Decimal("0.00"),
            "wv_line_4_wv_agi": cents(line_4),
            "wv_line_5_low_income_exclusion": cents(line_5),
            "wv_line_6_personal_exemption": cents(line_6),
            "wv_line_7_taxable_income": cents(line_7),
            "wv_line_8_tax": cents(line_8),
            "wv_line_9_family_tax_credit": Decimal("0.00"),
            "starting_point": "federal_agi",
            "tenforty_supports_wv_default_backend": False,
            "tenforty_supports_wv_graph_backend": True,
            "tenforty_status_note": (
                "tenforty default OTS backend does not support "
                "2025/WV_IT140 (raises ValueError). The graph backend "
                "returns a number but omits the WV personal exemption "
                "(Form IT-140 line 6), producing a +$96.40 over-"
                "statement on a $65k Single return ($2,000 exemption "
                "* 4.82% top rate = $96.40). This plugin hand-rolls "
                "the calc against the WV State Tax Department TY2025 "
                "rate schedule (post-HB 2526/HB 4007 trigger cuts) "
                "and WV Code §11-21-16 personal exemption."
            ),
            "wv_reciprocity_partners": list(WV_RECIPROCITY_PARTNERS),
            "wv_reciprocity_note": (
                "WV residents working in KY, MD, OH, PA, or VA do NOT "
                "file a nonresident return in those states. They file "
                "WV Form IT-104R with their employer to direct WV "
                "withholding instead. Multi-state planning code must "
                "inspect ReciprocityTable.are_reciprocal('WV', work_state) "
                "and skip the work-state nonresident plugin call when "
                "True. v1 day-proration does NOT auto-suppress this."
            ),
            "v1_limitations": list(WV_V1_LIMITATIONS),
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
        """Split canonical income into WV-source vs non-WV-source.

        Residents: everything is WV-source. Nonresident / part-year:
        prorate each category by ``days_in_state / 365``.

        TODO(wv-it140nrs): real WV sourcing on Form IT-140NRS / Schedule
        A sources wages to the work location, business income to the
        location of activity, rental to the property state, intangibles
        to the domicile.
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
            state_source_wages=sourced_or_prorated_wages(return_, "WV", wages, days_in_state),
            state_source_interest=day_prorate(interest, days_in_state),
            state_source_dividends=day_prorate(ord_div, days_in_state),
            state_source_capital_gains=day_prorate(capital_gains, days_in_state),
            state_source_self_employment=sourced_or_prorated_schedule_c(return_, "WV", se_net, days_in_state),
            state_source_rental=day_prorate(rental_net, days_in_state),
        )

    def render_pdfs(
        self, state_return: StateReturn, out_dir: Path
    ) -> list[Path]:
        # WV IT-140 2025: The WV State Tax Department published the 2025
        # IT-140 form bundle (it140.PersonalIncomeTaxFormsAndInstructions.
        # 2025.pdf) as a fully flattened PDF — zero AcroForm fields, zero
        # widget annotations across all 56 pages. The 2024 version
        # (it140.2024.pdf) was fillable (564 fields), but WV switched to
        # a non-fillable format for 2025. WV pushes taxpayers to its
        # MyTaxes portal (https://mytaxes.wvtax.gov/) for e-filing.
        # AcroForm filling is not possible for this state form.
        # See skill/reference/wv-it140-acroform-map.json for details.
        # Verified 2026-04-12.
        return []

    def form_ids(self) -> list[str]:
        return ["WV Form IT-140"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = WestVirginiaPlugin(
    meta=StatePluginMeta(
        code="WV",
        name="West Virginia",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://tax.wv.gov/Individuals/Pages/default.aspx",
        free_efile_url="https://mytaxes.wvtax.gov/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # WV has FIVE bilateral reciprocity partners: KY, MD, OH, PA, VA.
        # Verified against skill/reference/state-reciprocity.json.
        reciprocity_partners=WV_RECIPROCITY_PARTNERS,
        supported_tax_years=(2025,),
        notes=(
            "Hand-rolled WV Form IT-140 calc. tenforty default backend "
            "does NOT support 2025/WV_IT140 (raises ValueError); the "
            "graph backend returns a number but omits the WV personal "
            "exemption (Form IT-140 line 6) — graph reports $2,294.50 "
            "for $65k Single, hand-rolled is $2,198.10 (+$96.40 delta "
            "= $2,000 exemption * 4.82% top rate). Five-bracket "
            "graduated schedule for TY2025 (post-HB 2526/HB 4007 "
            "trigger cuts): 2.22% / 2.96% / 3.33% / 4.44% / 4.82% "
            "($0-$10k, $10k-$25k, $25k-$40k, $40k-$60k, $60k+). WV is "
            "unusual: SAME brackets for Single/HOH/MFJ/QSS — no joint "
            "widening. MFS uses half-bracket schedule. WV has NO state "
            "standard deduction; the only deduction off federal AGI is "
            "the $2,000 statutory personal exemption per WV Code "
            "§11-21-16 (NOT inflation-indexed). Reciprocity: FIVE "
            "bilateral partners (KY, MD, OH, PA, VA) — most in this "
            "fan-out wave. Free e-file via MyTaxes."
        ),
    )
)
