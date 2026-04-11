"""Indiana (IN) state plugin — TY2025.

Filename note
-------------
This module is named ``in_.py`` (with a trailing underscore) because
``in`` is a Python reserved keyword. The package thus exposes it as
``skill.scripts.states.in_``. This mirrors the existing ``or_.py``
convention used for Oregon. Always import as
``from skill.scripts.states import in_``.

Decision: HAND-ROLL
-------------------
Per the wave-5 probe-then-verify-then-decide rubric (CP8-B), we re-
probed tenforty for Indiana on the **graph backend** and cross-checked
against an independent hand calculation from Indiana Form IT-40 (TY2025)
primary sources. The results disagree by **$30** at the locked $65k
Single scenario:

    Single / $65,000 W-2 / Standard
      tenforty graph backend (probed 2026-04-11):
        state_total_tax            = 1950.00   (= 65000 × 3.00%)
        state_taxable_income       = 0.00      (graph echoes nothing)

      DOR primary-source hand calc (Form IT-40 line-by-line):
        Indiana AGI                = $65,000.00
        Personal exemption (1)     =  $1,000.00
        Indiana taxable income     = $64,000.00
        Indiana tax (3.00% flat)   =  $1,920.00

The graph backend computes ``AGI × 3.00%`` directly without applying
the **$1,000 Indiana personal exemption** that every IT-40 filer is
entitled to (Form IT-40 Schedule 3 line 1). This is the same class of
gap as IL ($2,850 personal exemption missing) and KS ($9,160 exemption
missing) flagged in the wave-4 / CP8-B graph-backend cross-check (see
``skill/reference/tenforty-ty2025-gap.md``). $30 is materially > $5, so
this plugin **hand-rolls** Indiana from DOR primary source rather than
wrapping the graph backend.

The default OTS backend also fails:
    ``ValueError: OTS does not support 2025/IN_IT40``

Indiana TY2025 rate / base — DOR primary source verification
------------------------------------------------------------
Indiana's adjusted gross income tax rate has been on a multi-year
phase-down under **House Enrolled Act 1001 (2023)**, signed by Governor
Holcomb on May 4, 2023. The full schedule:

    TY2023   3.15%
    TY2024   3.05%
    TY2025   3.00%   ← current
    TY2026   2.95%
    TY2027   2.90%   (final scheduled rate, subject to revenue triggers)

For TY2025 the rate is a single FLAT **3.00%** on Indiana taxable
income (Indiana Code §6-3-2-1(b), as amended by HEA 1001-2023).

Indiana Form IT-40 line structure (TY2025):

    Line 1   Federal AGI (from federal Form 1040 line 11)
    Line 2   Indiana add-backs (Schedule 1 line 8)
    Line 3   Subtotal = Line 1 + Line 2
    Line 4   Indiana deductions / subtractions (Schedule 2 line 13)
    Line 5   Indiana adjusted gross income = Line 3 - Line 4
    Line 6   Personal / dependent exemptions (Schedule 3 line 7)
              Single / HOH / MFS:    $1,000 (1 personal exemption)
              MFJ / QSS:             $2,000 (2 personal exemptions)
              Per dependent:         $1,000 (Schedule 3 line 2)
              Per qualifying child:  +$1,500 (additional exemption,
                                              Schedule 3 line 3, for
                                              each child under age 19
                                              or under age 24 if a
                                              full-time student)
              Senior / blind:        +$1,000 each (Schedule 3 lines
                                                   4-5)
              IT-40 Schedule 3 reference:
              https://www.in.gov/dor/individual-income-taxes/

    Line 7   Indiana taxable income = max(0, Line 5 - Line 6)
    Line 8   State adjusted gross income tax = 0.0300 × Line 7

    (County income tax is computed separately on Schedule CT-40 and
    added on Form IT-40 line 9. v1 does NOT compute county tax — it is
    a totally separate per-county schedule keyed to county of residence
    and county of principal employment on January 1 of the tax year.
    Indiana has 92 counties with rates ranging from 0.50% to 3.38% as
    of TY2025. See LIMITATIONS below.)

Hand calculation, Single $65,000 W-2 / Standard, no dependents (TY2025):

    Federal AGI                                = $65,000.00
    Indiana add-backs (v1)                     =      $0.00
    Indiana subtractions (v1)                  =      $0.00
    Indiana AGI                                = $65,000.00
    Personal exemption (Single, 1 exemption)   =  $1,000.00
    Indiana taxable income                     = $64,000.00
    Indiana state tax (3.00% flat)             =  $1,920.00

    LOCKED: state_total_tax = $1,920.00 for the Single $65k scenario.

Indiana TY2025 personal-exemption matrix (v1 supports the base
allowances; senior/blind/qualifying-child/additional exemptions are
flagged as TODOs in ``IN_V1_LIMITATIONS``):

    SINGLE          $1,000  (1 personal exemption)
    MFS             $1,000  (1 personal exemption — taxpayer only)
    HOH             $1,000  (1 personal exemption)
    MFJ             $2,000  (2 personal exemptions — taxpayer + spouse)
    QSS             $2,000  (treated like MFJ for the Indiana exemption
                             allowance; the IT-40 instructions check
                             the QSS box but the exemption matches MFJ)
    + dependents    $1,000 per dependent claimed on the federal return
    + qual. child   +$1,500 per qualifying child under 19 (or under 24
                    if a full-time student); v1 does NOT model this —
                    we apply only the base $1,000 dependent exemption.

Reciprocity
-----------
Indiana has **five** bilateral reciprocity agreements — KY, MI, OH, PA,
WI — verified against ``skill/reference/state-reciprocity.json`` (five
entries that pair IN with each partner). Per Indiana DOR Information
Bulletin #28 ("Income Tax Information Bulletin No. 28: Reciprocity"),
employees who are residents of one of these states and work in Indiana
do NOT pay Indiana adjusted gross income tax on their wages — they file
Indiana Form WH-47 (Certificate of Residence) with their employer to
stop Indiana withholding. Reciprocity covers WAGES ONLY; nonresidents
with Indiana-source nonwage income (rental, business, gambling) still
file Indiana Form IT-40PNR.

  - IN DOR Income Tax Information Bulletin #28 ("Reciprocity"),
    available via https://www.in.gov/dor/legal-resources/.
  - skill/reference/state-reciprocity.json — entries
    ``["IN", "KY"]``, ``["IN", "MI"]``, ``["IN", "OH"]``,
    ``["IN", "PA"]``, ``["IN", "WI"]``.

Submission channel
------------------
Indiana participates in the IRS Fed/State MeF program — the IT-40
piggybacks on the federal 1040 transmission via commercial software /
IRS Authorized e-file Provider. The Indiana DOR also offers
"INTIME" (Indiana Taxpayer Information Management Engine) at
https://intime.dor.in.gov/ as a free direct-entry portal for individual
income-tax returns. Our canonical channel for IN is therefore
``SubmissionChannel.FED_STATE_PIGGYBACK`` (matching OH/NJ/MI/WI), with
the INTIME portal surfaced in ``meta.free_efile_url``.

Nonresident / part-year handling
--------------------------------
v1 uses day-based proration (``days_in_state / 365``) of the resident-
basis tax. The real Indiana rule for nonresidents and part-year
residents is **Form IT-40PNR** ("Part-Year or Full-Year Nonresident
Individual Income Tax Return"), which sources each income type to its
state of origin (wages to work location, rental to property state,
etc.) on Schedule A and computes the Indiana-source ratio for tax
allocation. TODO(in-it40pnr) tracks this.

Loud TODOs
----------
- TODO(in-it40pnr): replace day-based proration with the real Form
  IT-40PNR Indiana-source-income ratio for nonresident / part-year
  filers. Indiana has FIVE reciprocity partners (KY/MI/OH/PA/WI), so
  this is a high-volume case for the Indiana plugin.
- TODO(in-add-backs): model Indiana add-backs (Schedule 1):
  - Tax add-back (state income tax deducted on federal Schedule A)
  - Bonus depreciation add-back
  - Section 179 add-back (Indiana cap = $25,000)
  - Domestic production activities deduction add-back
  - Federal NOL add-back
  - Other Indiana-specific add-backs per Schedule 1 line numbers
- TODO(in-subtractions): model Indiana subtractions (Schedule 2):
  - Renter's deduction (up to $3,000)
  - Homeowner's residential property tax deduction (up to $2,500)
  - Indiana state tax refund subtraction (federal Schedule 1 line 1)
  - US Government interest subtraction
  - Civil Service Annuity subtraction (up to $16,000 over 62)
  - Active Duty Military Pay subtraction
  - National Guard / Reserve Pay subtraction
  - Indiana 529 contribution subtraction (up to $5,000)
  - Other Indiana-specific subtractions per Schedule 2 line numbers
- TODO(in-county-tax): Indiana County Income Tax (Schedule CT-40) is
  NOT computed. This is a SEPARATE per-county schedule (92 counties,
  rates 0.50%-3.38% TY2025) keyed to county of residence and county
  of principal employment on January 1 of the tax year. The county
  tax is added to Form IT-40 line 9 and is part of the total Indiana
  tax liability. v1 reports only the state tax (line 8). A future
  CT-40 plugin should consume Indiana taxable income from this
  plugin and add the county tax to ``state_specific["state_total_
  tax"]`` (or expose it as a separate field).
- TODO(in-credits): model Indiana credits (Schedule 6 / Schedule IN-EIC
  / Schedule IN-CR):
  - Indiana Earned Income Tax Credit (10% of federal EITC)
  - Indiana Unified Tax Credit for the Elderly
  - Credit for taxes paid to other states (critical for multi-state
    filers — Schedule 6 line 8)
  - 529 Plan credit (20% of contributions, up to $1,500 / $750 MFS)
  - Lake County residential property tax credit
  - Adoption credit (10% of federal adoption credit, up to $1,000)
  - Public school educator expense credit
  - Other Indiana credits per Schedule 6 / IN-CR line items
- TODO(in-qualifying-child-extra): model the additional $1,500
  qualifying child exemption (Schedule 3 line 3) — v1 only applies
  the base $1,000 dependent exemption.
- TODO(in-pdf): fan-out follow-up — fill IT-40 (and Schedules 1-7,
  CT-40, IN-EIC, IT-40PNR) using pypdf against the IN DOR fillable
  PDFs.

Sources (verified 2026-04-11)
-----------------------------
- Indiana Department of Revenue, "Individual Income Tax" landing page:
  https://www.in.gov/dor/individual-income-taxes/
- Indiana Form IT-40 (TY2025), individual income tax return.
- Indiana Code §6-3-2-1(b), individual adjusted gross income tax
  rate (3.00% for tax years beginning after Dec 31, 2024 and before
  Jan 1, 2026), as amended by House Enrolled Act 1001 (2023).
- Indiana Code §6-3-1-3.5, definition of "Indiana adjusted gross
  income" and personal-exemption rules.
- Indiana DOR Income Tax Information Bulletin #28 (Reciprocity).
- Tax Foundation, "State Individual Income Tax Rates and Brackets,
  2025" — confirms IN flat 3.00% TY2025.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from skill.scripts.models import (
    CanonicalReturn,
    FilingStatus,
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


# ---------------------------------------------------------------------------
# TY2025 constants — verified from Indiana DOR primary sources
# ---------------------------------------------------------------------------


IN_TY2025_FLAT_RATE: Decimal = Decimal("0.030")
"""Indiana TY2025 individual adjusted gross income tax rate.

3.00% flat per Indiana Code §6-3-2-1(b) as amended by HEA 1001 (2023).
This is the third step in the multi-year phase-down: 3.15% (TY2023) ->
3.05% (TY2024) -> 3.00% (TY2025) -> 2.95% (TY2026) -> 2.90% (TY2027).
"""

IN_TY2025_PERSONAL_EXEMPTION_BASE: Decimal = Decimal("1000")
"""Indiana TY2025 base personal exemption per filer / per dependent.

Form IT-40 Schedule 3 line 1 / line 2: $1,000 per personal exemption
(taxpayer, spouse if MFJ) and $1,000 per dependent claimed on the
federal return. Sourced from IN DOR Form IT-40 instructions and IC
§6-3-1-3.5(a)(7).
"""

IN_TY2025_QUALIFYING_CHILD_EXTRA_EXEMPTION: Decimal = Decimal("1500")
"""Additional $1,500 per qualifying child (Schedule 3 line 3).

NOT applied in v1 — see ``IN_V1_LIMITATIONS``. v1 only applies the base
$1,000 dependent exemption from line 2; the additional $1,500 for each
qualifying child under age 19 (or under age 24 if a full-time student)
requires age data we don't currently track on the canonical return.
"""

IN_TY2025_EXEMPTION_BY_FILING_STATUS: dict[FilingStatus, Decimal] = {
    FilingStatus.SINGLE: IN_TY2025_PERSONAL_EXEMPTION_BASE,
    FilingStatus.MFS: IN_TY2025_PERSONAL_EXEMPTION_BASE,
    FilingStatus.HOH: IN_TY2025_PERSONAL_EXEMPTION_BASE,
    FilingStatus.MFJ: IN_TY2025_PERSONAL_EXEMPTION_BASE * Decimal("2"),
    FilingStatus.QSS: IN_TY2025_PERSONAL_EXEMPTION_BASE * Decimal("2"),
}
"""TY2025 Indiana personal-exemption allowance by filing status.

Single / HOH / MFS get one $1,000 exemption. MFJ and QSS get $2,000
(two $1,000 exemptions for taxpayer + spouse). Add ``num_dependents *
$1,000`` on top of this. Source: Indiana Form IT-40 Schedule 3
instructions, TY2025.
"""


IN_V1_LIMITATIONS: tuple[str, ...] = (
    "Indiana add-backs (Schedule 1) NOT modeled: tax add-back for "
    "state income tax deducted on federal Schedule A, bonus "
    "depreciation add-back, Section 179 add-back ($25,000 IN cap), "
    "domestic production activities deduction add-back, federal NOL "
    "add-back, other Indiana-specific add-backs.",
    "Indiana subtractions (Schedule 2) NOT modeled: renter's deduction "
    "(up to $3,000), homeowner's residential property tax deduction "
    "(up to $2,500), Indiana state tax refund subtraction, US "
    "Government interest subtraction, Civil Service Annuity "
    "subtraction (up to $16,000 over 62), Active Duty Military Pay "
    "subtraction, National Guard / Reserve Pay subtraction, Indiana "
    "529 contribution subtraction, etc.",
    "Indiana County Income Tax (Schedule CT-40) NOT computed. Indiana "
    "has 92 counties with rates ranging from 0.50% to 3.38% (TY2025) "
    "keyed to county of residence and county of principal employment "
    "on January 1 of the tax year. The county tax is added to Form "
    "IT-40 line 9 and is part of the total Indiana tax liability. v1 "
    "reports only the state tax (Form IT-40 line 8). A future CT-40 "
    "plugin should add the county tax separately.",
    "Indiana credits (Schedule 6 / IN-CR) NOT modeled: Indiana EITC "
    "(10% of federal EITC), Unified Tax Credit for the Elderly, credit "
    "for taxes paid to other states (Schedule 6 line 8 — critical for "
    "multi-state filers), 529 plan credit (20% of contributions up to "
    "$1,500), Lake County residential property tax credit, adoption "
    "credit, public school educator expense credit, other Indiana "
    "credits.",
    "Additional $1,500 qualifying-child exemption (Schedule 3 line 3) "
    "NOT modeled. v1 applies only the base $1,000 dependent exemption "
    "(line 2). The additional $1,500 per qualifying child under age 19 "
    "(or under 24 if a full-time student) requires per-dependent age "
    "data not currently tracked on the canonical return.",
    "Senior ($1,000) and blind ($1,000) additional exemptions "
    "(Schedule 3 lines 4-5) NOT modeled. v1 only handles the base "
    "filing-status allowance plus $1,000 per dependent.",
    "Nonresident / part-year apportionment uses day-based proration "
    "(days_in_state / 365). The real treatment is Form IT-40PNR with "
    "Schedule A income sourcing and the Indiana-source ratio.",
    "Indiana itemized deductions are NOT a separate concept — Indiana "
    "starts from federal AGI (post-federal-deduction) so the federal "
    "standard-vs-itemized choice is already baked in upstream. The "
    "v1 plugin therefore does not branch on standard_or_itemized.",
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def in_personal_exemption(
    filing_status: FilingStatus, num_dependents: int
) -> Decimal:
    """Return the Indiana personal-exemption allowance (Schedule 3 line 7).

    Base allowance ($1,000 Single/HOH/MFS, $2,000 MFJ/QSS) plus
    ``num_dependents * $1,000`` for the base dependent exemption (line
    2). Does NOT apply the +$1,500 qualifying-child extra (line 3),
    senior/blind additional exemptions (lines 4-5), or any phase-outs.
    See ``IN_V1_LIMITATIONS``.

    Negative dependent counts are clamped to zero.
    """
    base = IN_TY2025_EXEMPTION_BY_FILING_STATUS.get(
        filing_status, IN_TY2025_PERSONAL_EXEMPTION_BASE
    )
    extra = (
        Decimal(max(0, num_dependents))
        * IN_TY2025_PERSONAL_EXEMPTION_BASE
    )
    return base + extra


def in_state_tax(taxable_income: Decimal) -> Decimal:
    """Compute Indiana state adjusted gross income tax (Form IT-40 line 8).

    Single 3.00% flat rate per IC §6-3-2-1(b) as amended by HEA 1001
    (2023). Negative taxable income returns zero.
    """
    ti = d(taxable_income)
    if ti <= 0:
        return Decimal("0.00")
    return cents(ti * IN_TY2025_FLAT_RATE)


def _apportionment_fraction_decimal(
    residency: ResidencyStatus, days_in_state: int
) -> Decimal:
    """Diagnostic apportionment fraction (Decimal, exact).

    Used for the ``state_specific["apportionment_fraction"]`` field so
    downstream introspection sees the exact rational fraction without
    cent quantization. The actual tax-side apportionment uses
    ``day_prorate`` from ``_hand_rolled_base``.
    """
    if residency == ResidencyStatus.RESIDENT:
        return Decimal("1")
    if days_in_state <= 0:
        return Decimal("0")
    if days_in_state >= 365:
        return Decimal("1")
    return Decimal(days_in_state) / Decimal("365")


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndianaPlugin:
    """State plugin for Indiana — TY2025.

    Hand-rolled IT-40 calculation. tenforty's default OTS backend raises
    ``ValueError: OTS does not support 2025/IN_IT40``, and tenforty's
    graph backend computes ``AGI × 3.00%`` directly without applying
    the $1,000 Indiana personal exemption — at $65k Single this is a
    $30 overstatement. We therefore hand-roll from the IN DOR Form
    IT-40 line-by-line instructions. See module docstring for the
    decision rationale.

    Flow:
        federal_AGI
          -> Indiana AGI            (v1: same as federal AGI; no
                                     Schedule 1 adds or Schedule 2 subs)
          -> exemption allowance    ($1,000 base + $1,000/dep)
          -> Indiana taxable income (= AGI - exemption, floored at 0)
          -> tax = 0.0300 × Indiana taxable income
          -> apportionment for nonresident / part-year
          -> (county tax NOT computed — separate Schedule CT-40)
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Form IT-40 Line 1: federal AGI.
        federal_agi = federal.adjusted_gross_income
        # Line 2: Schedule 1 add-backs. v1 = 0.
        in_addbacks = Decimal("0")
        # Line 4: Schedule 2 subtractions. v1 = 0.
        in_subtractions = Decimal("0")
        # Line 5: Indiana AGI = federal AGI + addbacks - subs.
        in_agi = max(
            Decimal("0"), federal_agi + in_addbacks - in_subtractions
        )

        # Line 6: Schedule 3 personal/dependent exemptions.
        exemption = in_personal_exemption(
            federal.filing_status, federal.num_dependents
        )
        # Line 7: Indiana taxable income (floored at zero).
        in_taxable_income = max(Decimal("0"), in_agi - exemption)

        # Line 8: state adjusted gross income tax (3.00% flat).
        in_state_tax_full = in_state_tax(in_taxable_income)

        # Line 9 (county tax) is NOT computed in v1 — see
        # IN_V1_LIMITATIONS.
        county_tax = Decimal("0.00")

        # Apportion for nonresident / part-year (day-based v1).
        # TODO(in-it40pnr): replace with Form IT-40PNR Indiana-source
        # ratio.
        if residency == ResidencyStatus.RESIDENT:
            in_tax_apportioned = cents(in_state_tax_full)
        else:
            in_tax_apportioned = day_prorate(
                in_state_tax_full, days_in_state
            )

        state_specific: dict[str, Any] = {
            "state_federal_agi": cents(federal_agi),
            "state_adjusted_gross_income": cents(in_agi),
            # Indiana has no separate "standard deduction" concept —
            # the federal AGI starting point already absorbs it.
            "state_exemption_allowance": cents(exemption),
            "state_taxable_income": cents(in_taxable_income),
            "state_total_tax": in_tax_apportioned,
            "state_total_tax_resident_basis": cents(in_state_tax_full),
            "state_county_tax": county_tax,
            "state_flat_rate": IN_TY2025_FLAT_RATE,
            "apportionment_fraction": _apportionment_fraction_decimal(
                residency, days_in_state
            ),
            "starting_point": "federal_agi",
            "in_addbacks_applied": in_addbacks,
            "in_subtractions_applied": in_subtractions,
            "in_county_tax_computed": False,
            "in_county_tax_note": (
                "Indiana County Income Tax (Form IT-40 line 9 / "
                "Schedule CT-40) is NOT computed by this plugin. v1 "
                "reports only the state tax (line 8). The county tax "
                "is keyed to county of residence and county of "
                "principal employment on Jan 1 of the tax year, with "
                "92 counties at rates from 0.50% to 3.38% TY2025."
            ),
            "v1_limitations": list(IN_V1_LIMITATIONS),
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
        """Split canonical income into IN-source vs non-IN-source.

        Residents: everything is IN-source. Nonresident / part-year:
        prorate each category by ``days_in_state / 365``.

        TODO(in-it40pnr): IN actually sources each income type on Form
        IT-40PNR Schedule A — wages to the work location, rental to the
        property state, interest/dividends to the taxpayer's domicile.
        Day-based proration is the shared first-cut across all fan-out
        state plugins; refine with the real Form IT-40PNR logic in
        follow-up.
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

        # Schedule C / E net totals — reuse engine helpers so IN mirrors
        # the federal calc's own rollup logic.
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
        # TODO(in-pdf): fan-out follow-up — fill IT-40 (and Schedules
        # 1-7, CT-40 county tax, IN-EIC, IT-40PNR for nonresidents)
        # using pypdf against the IN DOR's fillable PDFs. The output
        # renderer suite is the right home for this; this plugin
        # returns structured state_specific data that the renderer
        # will consume.
        return []

    def form_ids(self) -> list[str]:
        return ["IN Form IT-40"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = IndianaPlugin(
    meta=StatePluginMeta(
        code="IN",
        name="Indiana",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://www.in.gov/dor/individual-income-taxes/",
        # INTIME — the IN DOR free direct-entry portal at
        # https://intime.dor.in.gov/. Accepts individual income-tax
        # returns without commercial software.
        free_efile_url="https://intime.dor.in.gov/",
        submission_channel=SubmissionChannel.FED_STATE_PIGGYBACK,
        # IN has FIVE bilateral reciprocity partners — KY, MI, OH, PA,
        # WI — verified against skill/reference/state-reciprocity.json
        # and IN DOR Income Tax Information Bulletin #28. A test
        # asserts the exact set so accidental drift fails CI.
        reciprocity_partners=("KY", "MI", "OH", "PA", "WI"),
        supported_tax_years=(2025,),
        notes=(
            "Hand-rolled Indiana Form IT-40 calc (tenforty does not "
            "support 2025/IN_IT40 on either default or graph backend "
            "— graph backend omits the $1,000 personal exemption, "
            "computing AGI*3% directly which over-states tax by $30 "
            "for a $65k Single filer; verified 2026-04-11). Flat "
            "3.00% rate per HEA 1001-2023 (down from 3.05% TY2024 "
            "and 3.15% TY2023). Starting point: federal AGI (Form "
            "IT-40 line 1). Personal exemption: $1,000 Single/HOH/"
            "MFS, $2,000 MFJ/QSS, +$1,000/dependent. Reciprocity: "
            "KY, MI, OH, PA, WI (5 partners — large network). Free "
            "e-file via INTIME. County tax (Schedule CT-40) NOT "
            "computed by v1 — see IN_V1_LIMITATIONS. Source: IN "
            "Code §6-3-2-1(b) as amended by HEA 1001-2023; IN DOR "
            "Form IT-40 instructions; in.gov/dor."
        ),
    )
)
