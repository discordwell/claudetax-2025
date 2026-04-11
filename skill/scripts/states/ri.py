"""Rhode Island (RI) state plugin — TY2025.

*** TENFORTY DEFAULT BACKEND DOES NOT SUPPORT RI / GRAPH BACKEND HAS BUGS ***

Default OTS backend: ``tenforty.evaluate_return(year=2025, state='RI', ...)``
raises ``ValueError: OTS does not support 2025/RI_1040`` (verified
2026-04-11). The newer graph backend (``backend='graph'``) DOES return a
number, but cross-checking against the published Rhode Island Form RI-1040
flow shows the graph backend OMITS the Rhode Island personal exemption
(Form RI-1040 line 6), which materially under-reports relief for almost
every filer:

    $65k Single  graph backend  = $2,028.75
    $65k Single  hand-rolled    = $1,833.75
    Divergence                  = $195.00

The mismatch is exactly the RI personal exemption ($5,200 for TY2025)
times the 3.75% bottom-bracket rate ($5,200 * 0.0375 = $195.00). The
graph backend treats line 6 (RI exemption) as an input that defaults to
zero; nothing in the graph automatically computes the exemption from the
filing status and dependent count.

Per the wave-5 decision rubric in
``skill/reference/tenforty-ty2025-gap.md``, "Material mismatch (>$5):
hand-roll from DOR primary source. The graph backend is doing something
wrong for this state — do NOT trust it." This plugin therefore hand-rolls
RI Form RI-1040 from the official Rhode Island Division of Taxation rate
schedule and instructions.

=============================================================================
THIS PLUGIN HAND-ROLLS THE RI FORM RI-1040 CALCULATION. The locked $65k
Single tax number is the plugin's OWN computation, NOT a tenforty
graph-backend value.
=============================================================================

Form RI-1040 line structure (TY2025):

    Line 1   Federal AGI  (from federal Form 1040 line 11)
    Line 2   Modifications  (Schedule M additions / subtractions)
    Line 3   Modified federal AGI  =  Line 1 + Line 2
    Line 4   RI standard deduction OR itemized (RI Schedule A)
    Line 5   Subtotal  =  Line 3 - Line 4
    Line 6   RI personal exemption  ($5,200 per exemption, phased out high)
    Line 7   RI taxable income  =  max(0, Line 5 - Line 6)
    Line 8   RI income tax  (from rate schedule or tax table)

TY2025 RI standard deduction (per RI Division of Taxation publication
"2025 Rhode Island Personal Income Tax Forms" and verified against
tenforty's ``ri_1040_2025.json`` graph file constants):

    Single                                       $10,900
    Married Filing Separately                    $10,900
    Head of Household                            $16,350
    Married Filing Jointly                       $21,800
    Qualifying Surviving Spouse                  $21,800

The RI standard deduction phases out for AGI between $239,200 and
$262,950 (Single, indexed) — fully eliminated above the upper threshold.
NOT modeled in v1 (see ``RI_V1_LIMITATIONS``).

TY2025 RI personal exemption: **$5,200 per exemption** (taxpayer +
spouse + each dependent), indexed from $5,050 in TY2024 per the RI
Division of Taxation 2025 Indexed Amounts release. Phaseout matches the
standard-deduction phaseout (linear between $239,200 - $262,950 Single).
NOT modeled in v1.

TY2025 RI bracket schedule (verified against tenforty's
``ri_1040_2025.json`` ``ri_brackets_2025`` table — these constants match
the RI Division of Taxation published TY2025 schedule):

    All filing statuses share the same brackets — RI does NOT split
    brackets by filing status, unlike most graduated-rate states:

        3.75%   on $0 - $79,900
        4.75%   on $79,900 - $181,650
        5.99%   on $181,650+

Note: RI uses a "Tax Computation Worksheet" for RI taxable income above
$108,300 (with continuous-bracket subtractions), and a printed Tax Table
for RI taxable income below that threshold. Both are mathematically
equivalent to the bracket formula above. v1 uses the bracket formula
directly.

$65k Single / Standard wrap-correctness lock (this plugin's own math):

    Line 1   Federal AGI                $65,000.00
    Line 2   Modifications                   $0.00
    Line 3   Modified federal AGI       $65,000.00
    Line 4   Standard Deduction         $10,900.00
    Line 5   Subtotal                   $54,100.00
    Line 6   Personal Exemption          $5,200.00
    Line 7   RI Taxable Income          $48,900.00
    Line 8   RI Income Tax (3.75% flat) $ 1,833.75

The test suite pins ``state_total_tax == Decimal('1833.75')`` for this
scenario.

For comparison, the tenforty graph backend (``backend='graph'``) returns
$2,028.75 for the same scenario — the +$195.00 delta is exactly one
personal exemption applied at the 3.75% bottom-bracket rate. The
plugin's ``state_specific`` payload exposes both numbers under
``state_total_tax`` (canonical, hand-rolled) and
``state_total_tax_graph_backend_65k_single_reference`` (the tenforty
graph value, for drift detection — pinned in tests).

Reciprocity: Rhode Island has **NO** bilateral income tax reciprocity
agreements with any state — verified against
``skill/reference/state-reciprocity.json`` (RI does not appear in
``agreements``) and against the Tax Foundation's "State Reciprocity
Agreements" research page. RI residents working in MA, CT, or NY file
the work-state nonresident return and claim the RI credit for taxes
paid to other states (Form RI-1040 line 9b).

Submission channel: Rhode Island Division of Taxation operates a free
direct-file option through the "Tax Portal" at https://taxportal.ri.gov/
for individual income tax. RI also participates in the IRS Fed/State
MeF program for commercial software piggyback filings. The canonical
free path for an individual is the state's own portal, so this plugin
reports ``SubmissionChannel.STATE_DOR_FREE_PORTAL``.

Sources (verified 2026-04-11):

    - RI Division of Taxation, Individual Tax Forms hub:
      https://tax.ri.gov/forms/individual-tax-forms

    - RI Division of Taxation, "2025 Rhode Island Personal Income Tax
      Forms" (Form RI-1040 booklet, December 2025 publication date)

    - RI Division of Taxation, 2025 Indexed Amounts release (annual
      inflation-adjusted standard deduction, personal exemption, and
      bracket thresholds)

    - tenforty graph backend ``ri_1040_2025.json`` (verifies bracket
      and standard-deduction constants match the RI Division of
      Taxation TY2025 schedule):
      $VENV/lib/python3.12/site-packages/tenforty/forms/ri_1040_2025.json

Nonresident / part-year handling: RI nonresident filers use Form
RI-1040NR which prorates the resident-basis tax by a Rhode Island AGI
ratio (RI-source AGI / total AGI). v1 approximates this with day-based
proration, consistent with the other wave-4/5 hand-rolled plugins.
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


# Canonical wave-5 $65k Single gatekeeper lock. Hand-traced from RI
# Form RI-1040 — see module docstring. Referenced from test_state_ri.py.
LOCK_VALUE: Final[Decimal] = Decimal("1833.75")


# ---------------------------------------------------------------------------
# TY2025 constants
# ---------------------------------------------------------------------------


# RI standard deduction by filing status (TY2025).
# Source: RI Division of Taxation 2025 Indexed Amounts + tenforty graph
# file ri_1040_2025.json.
RI_TY2025_STANDARD_DEDUCTION: dict[FilingStatus, Decimal] = {
    FilingStatus.SINGLE: Decimal("10900"),
    FilingStatus.MFS: Decimal("10900"),
    FilingStatus.HOH: Decimal("16350"),
    FilingStatus.MFJ: Decimal("21800"),
    FilingStatus.QSS: Decimal("21800"),
}


# RI personal exemption per exemption (TY2025).
# Source: RI Division of Taxation 2025 Indexed Amounts release.
# Indexed from $5,050 (TY2024) to $5,200 (TY2025).
RI_TY2025_PERSONAL_EXEMPTION_PER_PERSON: Decimal = Decimal("5200")


# Number of "filer" exemptions baked into the filing status. MFJ and QSS
# get two filer exemptions; everyone else gets one. Each dependent then
# adds another exemption.
RI_TY2025_FILER_EXEMPTIONS: dict[FilingStatus, int] = {
    FilingStatus.SINGLE: 1,
    FilingStatus.MFS: 1,
    FilingStatus.HOH: 1,
    FilingStatus.MFJ: 2,
    FilingStatus.QSS: 2,
}


# RI bracket schedule. Critically, RI uses the SAME brackets for all
# filing statuses — no married/joint widening, no head-of-household
# adjustment. Verified against tenforty ri_1040_2025.json.
RI_TY2025_BRACKETS: tuple[GraduatedBracket, ...] = (
    GraduatedBracket(low=Decimal("0"),      high=Decimal("79900"),  rate=Decimal("0.0375")),
    GraduatedBracket(low=Decimal("79900"),  high=Decimal("181650"), rate=Decimal("0.0475")),
    GraduatedBracket(low=Decimal("181650"), high=None,              rate=Decimal("0.0599")),
)

# Per-status mapping (all map to the same tuple, but kept for code
# symmetry with the other graduated-bracket states and to make the
# "single bracket schedule across statuses" property explicit at call
# sites).
RI_TY2025_BRACKETS_BY_STATUS: dict[
    FilingStatus, tuple[GraduatedBracket, ...]
] = {
    FilingStatus.SINGLE: RI_TY2025_BRACKETS,
    FilingStatus.MFS: RI_TY2025_BRACKETS,
    FilingStatus.HOH: RI_TY2025_BRACKETS,
    FilingStatus.MFJ: RI_TY2025_BRACKETS,
    FilingStatus.QSS: RI_TY2025_BRACKETS,
}


# Reference value: tenforty's graph backend returns this for $65k Single
# Standard. Pinned in tests as a drift detector.
RI_TY2025_GRAPH_BACKEND_65K_SINGLE: Decimal = Decimal("2028.75")


RI_V1_LIMITATIONS: tuple[str, ...] = (
    "RI Schedule M additions NOT modeled: bonus depreciation addback, "
    "out-of-state municipal bond interest addback, federal NOL addback, "
    "section 179 carryforward, federal passthrough entity tax addback, "
    "Family Education Account contributions over the cap, and other "
    "Schedule M Part 1 items.",
    "RI Schedule M subtractions NOT modeled: US government bond interest "
    "subtraction, RI Family Education 529 plan subtraction, modification "
    "for taxable retirement income (RI partial pension exclusion for "
    "filers reaching full Social Security retirement age, up to "
    "~$20,000 per filer for 2025), Social Security benefits taxable on "
    "federal return (full RI subtraction for AGI under thresholds), "
    "military pension subtraction, RI tuition saving program "
    "contributions, and other Schedule M Part 2 items.",
    "RI itemized deductions (Schedule A) NOT modeled. v1 always takes "
    "the RI standard deduction. RI Schedule A starts from federal "
    "Schedule A then applies RI-specific cap and add-backs (no double "
    "deduction for RI income tax).",
    "RI standard-deduction phaseout NOT modeled — high-AGI filers "
    "($239,200 - $262,950 Single, scaled for other statuses, TY2025 "
    "indexed) see a linear phaseout of the standard deduction. v1 "
    "applies the flat std ded at all AGIs.",
    "RI personal exemption phaseout NOT modeled — same AGI range as "
    "the std-ded phaseout. v1 applies the flat $5,200-per-exemption "
    "deduction at all AGIs.",
    "RI nonrefundable credits NOT modeled (RI-1040 line 9): credit "
    "against family education contribution, historic tax credit, "
    "scholarship trust contribution credit, motion picture production "
    "credit, residential lead abatement credit, taxes paid to other "
    "states (line 9b — critical for multi-state filers), credit for "
    "child and dependent care expenses (line 9c — RI CDCC at 25% of "
    "federal), property tax relief credit, and other Schedule CR items.",
    "RI refundable credits NOT modeled: RI Earned Income Tax Credit "
    "(refundable, 16% of federal EITC for TY2025), property tax relief "
    "credit refundable portion, residential lead abatement, child tax "
    "credit, and other RI Schedule CR refundable credits.",
    "Rhode Island Alternative Minimum Tax NOT modeled — RI does not "
    "currently impose a separate state AMT, so this is a non-limitation "
    "but noted for completeness.",
    "Nonresident / part-year apportionment uses day-based proration "
    "(days_in_state / 365) instead of the RI-source-AGI ratio from "
    "Form RI-1040NR Schedule III. A real RI nonresident filer computes "
    "the resident-basis tax then prorates by RI-source AGI / total AGI.",
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def ri_standard_deduction(filing_status: FilingStatus) -> Decimal:
    """Return the TY2025 RI standard deduction for the filing status."""
    return RI_TY2025_STANDARD_DEDUCTION.get(
        filing_status, RI_TY2025_STANDARD_DEDUCTION[FilingStatus.SINGLE]
    )


def ri_personal_exemption(
    filing_status: FilingStatus, num_dependents: int
) -> Decimal:
    """RI personal exemption deduction (Form RI-1040 line 6).

    $5,200 per exemption (filer + dependents). Single/HOH/MFS get one
    filer exemption; MFJ/QSS get two. Plus ``num_dependents`` exemptions.

    Does NOT apply the high-AGI phaseout — see ``RI_V1_LIMITATIONS``.
    """
    filers = RI_TY2025_FILER_EXEMPTIONS.get(filing_status, 1)
    deps = max(0, num_dependents)
    total_count = filers + deps
    return Decimal(total_count) * RI_TY2025_PERSONAL_EXEMPTION_PER_PERSON


def ri_bracket_tax(taxable_income: Decimal) -> Decimal:
    """Apply the TY2025 RI bracket schedule (filing-status-independent).

    Returns a non-negative Decimal rounded to cents. Negative or zero
    taxable income yields zero.
    """
    if taxable_income <= 0:
        return Decimal("0.00")
    return graduated_tax(taxable_income, RI_TY2025_BRACKETS)


def ri_taxable_income(
    federal: FederalTotals,
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    """Compute Rhode Island taxable income (Form RI-1040 line 7).

    Returns ``(line_3_modified_agi, line_4_deduction, line_5_subtotal,
    line_6_personal_exemption, line_7_taxable_income)``.

    v1 treats Schedule M modifications (Line 2) as zero.
    """
    line_3 = federal.adjusted_gross_income  # +0 modifications in v1
    line_4 = ri_standard_deduction(federal.filing_status)
    line_5 = line_3 - line_4
    if line_5 < 0:
        line_5 = Decimal("0")
    line_6 = ri_personal_exemption(
        federal.filing_status, federal.num_dependents
    )
    line_7 = line_5 - line_6
    if line_7 < 0:
        line_7 = Decimal("0")
    return (line_3, line_4, line_5, line_6, line_7)


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RhodeIslandPlugin:
    """State plugin for Rhode Island — TY2025.

    Hand-rolled Form RI-1040 calc. tenforty's default backend does NOT
    support 2025/RI_1040 (raises ValueError); the graph backend returns
    a number but omits the RI personal exemption, producing a +$195.00
    over-statement vs the DOR primary source for a $65k Single filer.
    See module docstring.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        line_3, line_4, line_5, line_6, line_7 = ri_taxable_income(federal)
        line_8 = ri_bracket_tax(line_7)

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
            "state_adjusted_gross_income": cents(line_3),
            "state_taxable_income": cents(line_7),
            "state_total_tax": state_tax_apportioned,
            "state_total_tax_resident_basis": cents(line_8),
            "state_total_tax_graph_backend_65k_single_reference": (
                RI_TY2025_GRAPH_BACKEND_65K_SINGLE
            ),
            "apportionment_fraction": apportionment_fraction,
            "ri_line_1_federal_agi": cents(federal.adjusted_gross_income),
            "ri_line_2_modifications": Decimal("0.00"),
            "ri_line_3_modified_agi": cents(line_3),
            "ri_line_4_standard_deduction": cents(line_4),
            "ri_line_5_subtotal": cents(line_5),
            "ri_line_6_personal_exemption": cents(line_6),
            "ri_line_7_taxable_income": cents(line_7),
            "ri_line_8_tax": cents(line_8),
            "starting_point": "federal_agi",
            "tenforty_supports_ri_default_backend": False,
            "tenforty_supports_ri_graph_backend": True,
            "tenforty_status_note": (
                "tenforty default OTS backend does not support "
                "2025/RI_1040 (raises ValueError). The graph backend "
                "returns a number but omits the RI personal exemption "
                "(Form RI-1040 line 6), producing a +$195.00 over-"
                "statement on a $65k Single return ($5,200 exemption "
                "* 3.75% bottom rate = $195.00). This plugin hand-"
                "rolls the calc against the RI Division of Taxation "
                "TY2025 rate schedule and 2025 Indexed Amounts release."
            ),
            "v1_limitations": list(RI_V1_LIMITATIONS),
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
        """Split canonical income into RI-source vs non-RI-source.

        Residents: everything is RI-source. Nonresident / part-year:
        prorate each category by ``days_in_state / 365``.

        TODO(ri-1040nr): real RI sourcing on Form RI-1040NR Schedule III
        sources wages to the work location, business income to the
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
            state_source_wages=sourced_or_prorated_wages(return_, "RI", wages, days_in_state),
            state_source_interest=day_prorate(interest, days_in_state),
            state_source_dividends=day_prorate(ord_div, days_in_state),
            state_source_capital_gains=day_prorate(capital_gains, days_in_state),
            state_source_self_employment=sourced_or_prorated_schedule_c(return_, "RI", se_net, days_in_state),
            state_source_rental=day_prorate(rental_net, days_in_state),
        )

    def render_pdfs(
        self, state_return: StateReturn, out_dir: Path
    ) -> list[Path]:
        # TODO(ri-pdf): fan-out follow-up — fill Form RI-1040 (and
        # Schedule M, Schedule A for itemizers, Form RI-1040NR for
        # nonresidents, Schedule W for withholding) using pypdf against
        # the RI Division of Taxation fillable PDFs.
        return []

    def form_ids(self) -> list[str]:
        return ["RI Form RI-1040"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = RhodeIslandPlugin(
    meta=StatePluginMeta(
        code="RI",
        name="Rhode Island",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://tax.ri.gov/forms/individual-tax-forms",
        free_efile_url="https://taxportal.ri.gov/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # Rhode Island has NO bilateral reciprocity agreements — verified
        # against skill/reference/state-reciprocity.json (RI does not
        # appear in `agreements`).
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Hand-rolled RI Form RI-1040 calc. tenforty default backend "
            "does NOT support 2025/RI_1040 (raises ValueError); the "
            "graph backend returns a number but omits the RI personal "
            "exemption (Form RI-1040 line 6) — graph reports $2,028.75 "
            "for $65k Single, hand-rolled is $1,833.75 (+$195.00 "
            "delta = $5,200 exemption * 3.75% bottom rate). Three-"
            "bracket graduated schedule for TY2025 (same brackets for "
            "ALL filing statuses): 3.75% / 4.75% / 5.99% ($0-$79,900, "
            "$79,900-$181,650, $181,650+). Standard deduction: Single "
            "$10,900, MFJ $21,800, HOH $16,350. Personal exemption "
            "$5,200 per exemption. Starting point: federal AGI. No "
            "reciprocity agreements. Free e-file via RI Tax Portal. "
            "Source: RI Division of Taxation 2025 Indexed Amounts + "
            "tenforty graph file ri_1040_2025.json bracket constants."
        ),
    )
)
