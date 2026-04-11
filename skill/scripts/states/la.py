"""Louisiana (LA) state plugin — TY2025.

Wraps tenforty / OpenTaxSolver (graph backend) for the Louisiana Form
IT-540 resident calc, mirroring the wave-4 ``wi.py`` graph-wrapper
pattern. Day-based proration is the v0.1 nonresident / part-year
fallback (real treatment is Form IT-540B).

Source of truth
---------------
Tenforty exposes Louisiana via ``OTSState.LA`` and ships an LA Form
IT-540 graph definition; the default OTS backend raises
``ValueError: OTS does not support 2025/LA_IT540`` so this plugin calls
``tenforty.evaluate_return(..., backend='graph')`` explicitly. WI is
the wave-4 precedent for the same idiom.

Probe (verified 2026-04-11 against tenforty installed in .venv):
    Single / $65,000 W-2 / Standard
        -> state_total_tax              = 1575.00
           state_taxable_income         = 0.00 (graph backend leaves blank)
           state_adjusted_gross_income  = 0.00 (graph backend leaves blank)
           state_tax_bracket            = 0.0  (graph backend omits)
           state_effective_tax_rate     = 0.0  (graph backend omits)

The graph backend's $1,575.00 number reconciles bit-for-bit against the
TY2025 LA DOR primary source (see "Hand verification" below). It is
trusted as the canonical wrapped value; this plugin pins it so any
upstream OTS / tenforty schedule drift fails CI.

LOUDLY FLAGGED RECENT LAW CHANGE
--------------------------------
Louisiana **HB 10 of the November 2024 Special Session**, signed by
Governor Jeff Landry on December 4, 2024, completely restructured the
Louisiana individual income tax effective for tax periods beginning
**on or after January 1, 2025**:

- The pre-2025 graduated bracket schedule (1.85% / 3.50% / 4.25%) was
  REPEALED. Louisiana now imposes a **flat 3.00%** rate on every
  dollar of Louisiana taxable income.
- The standard deduction was raised from $4,500 (Single) / $9,000
  (MFJ) to **$12,500 (Single / MFS)** and **$25,000 (MFJ / HOH /
  QSS)**.
- Personal exemptions and dependent exemptions were ELIMINATED. The
  larger standard deduction is the substitute.
- The federal income tax deduction (the LA-specific subtraction for
  federal income tax paid) was also ELIMINATED. The state-deductible
  federal tax line is gone from the new IT-540.
- Excess itemized deductions over the federal standard deduction
  remain available as an additional LA-only deduction (LA-specific
  Schedule J / NRPA).

Sources:
    - Louisiana Department of Revenue, "What are the individual
      income tax rates and brackets?"
      https://revenue.louisiana.gov/tax-education-and-faqs/faqs/income-tax-reform/what-are-the-individual-income-tax-rates-and-brackets/
    - LA DOR, "WHAT'S NEW FOR LOUISIANA 2025 INDIVIDUAL INCOME TAX?"
      https://dam.ldr.la.gov/taxforms/IT540i-WEB-2025.pdf  (the 2025
      IT-540 instruction booklet, "What's new" page).
    - EY Tax News 2024-2322, "Louisiana law implements a flat
      personal income tax rate starting in 2025"
      https://taxnews.ey.com/news/2024-2322-louisiana-law-implements-a-flat-personal-income-tax-rate-starting-in-2025
    - Office of Governor Jeff Landry, "Governor Announces Historic
      Tax Relief as Louisiana Families Save More" (Dec 2024 signing
      announcement).
    - Louisiana Revenue Information Bulletin 25-002 ("Bonus
      Depreciation Schedule").

Hand verification ($65k Single, TY2025)
----------------------------------------
    LA AGI               = federal AGI = $65,000  (IT-540 Line 7)
    LA standard deduction = $12,500              (IT-540 Line 8A,
                                                  Single, per HB 10)
    LA exempt income      = $0                    (no personal exemption)
    LA taxable income     = $65,000 - $12,500 = $52,500
    Tax = $52,500 * 0.03  = $1,575.00            (flat rate, HB 10)

Matches the tenforty graph backend value of $1,575.00 exactly. Decision
per the gap-doc rubric: **WRAP** (graph backend is correct for LA at
TY2025; hand-roll is unnecessary and would be redundant work).

Reciprocity
-----------
Louisiana has **NO** bilateral reciprocity agreements with any state
(verified against ``skill/reference/state-reciprocity.json``: LA does
not appear in the ``agreements`` array). Louisiana residents who work
in another state file a nonresident return there and claim the LA
"Credit for taxes paid to other states" (IT-540 Schedule G) on the
home-state return.

Submission channel
------------------
Louisiana operates a free direct-entry portal, **Louisiana File and Pay
Online** (LaTAP), at https://latap.revenue.louisiana.gov/. The state
also participates in the IRS Fed/State MeF program; commercial software
piggybacks the LA return with the federal 1040. The canonical channel
for this plugin is ``SubmissionChannel.STATE_DOR_FREE_PORTAL`` (the
free LaTAP path), with the DOR landing page surfaced in
``meta.dor_url``.

Nonresident / part-year
-----------------------
Louisiana's real nonresident / part-year treatment uses **Form IT-540B**
with LA-source income sourcing (LA wages to the work site, LA-property
rental, LA business income, etc.). v0.1 falls back to day-based
proration of the resident-basis tax — the same first-cut every fan-out
state plugin uses. ``TODO(la-form-it-540b)`` tracks the real treatment.

Form IDs
--------
- LA Form IT-540 (Resident Individual Income Tax Return)
- LA Form IT-540B (Nonresident / Part-Year — fan-out follow-up)
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
from skill.scripts.states._hand_rolled_base import cents, d, day_prorate
from skill.scripts.states._plugin_api import (
    FederalTotals,
    IncomeApportionment,
    StatePlugin,
    StatePluginMeta,
    StateStartingPoint,
    SubmissionChannel,
)


# Tenforty backend used for the Louisiana calc. The OTS backend does
# not register LA_IT540 in NATURAL_FORM_CONFIG; the graph backend
# consumes the LA Form IT-540 graph definition that ships with
# tenforty. This is the same situation as WI in wave 4. See module
# docstring.
_TENFORTY_BACKEND = "graph"


# TY2025 constants — these are NOT used by the wrap path (tenforty owns
# the actual computation). They are exposed so tests can pin the law
# without re-deriving it from the IT-540 instructions on every run, and
# so the gatekeeper test can confirm the hand-calc matches the wrap
# bit-for-bit.

LA_TY2025_FLAT_RATE: Decimal = Decimal("0.03")
"""Louisiana TY2025 flat individual income tax rate = 3.00%.

Per HB 10 (2024 Special Session), effective 1/1/2025. The pre-2025
graduated 1.85% / 3.50% / 4.25% schedule was REPEALED.
Source: LA R.S. 47:32 as amended by HB 10; LA DOR FAQ.
"""

LA_TY2025_STD_DED_SINGLE: Decimal = Decimal("12500")
"""LA TY2025 standard deduction, Single / MFS. HB 10 raised this from
$4,500. Source: LA DOR 2025 IT-540 instructions, Line 8A."""

LA_TY2025_STD_DED_MFJ: Decimal = Decimal("25000")
"""LA TY2025 standard deduction, MFJ / HOH / QSS. HB 10 raised this
from $9,000. Source: LA DOR 2025 IT-540 instructions, Line 8A."""


LA_HB10_PHASEOUT_NOTES: tuple[str, ...] = (
    "HB 10 (2024 Special Session) repealed the LA graduated bracket "
    "schedule (1.85% / 3.50% / 4.25%) and replaced it with a flat 3.00% "
    "rate effective 1/1/2025.",
    "HB 10 raised the LA standard deduction from $4,500/$9,000 to "
    "$12,500 (Single/MFS) / $25,000 (MFJ/HOH/QSS).",
    "HB 10 ELIMINATED the LA personal exemption ($4,500 Single) and "
    "dependent exemption ($1,000 each). The larger standard deduction "
    "is the substitute.",
    "HB 10 ELIMINATED the LA federal-income-tax deduction (the LA-only "
    "subtraction for federal income tax actually paid). It does not "
    "appear on the new IT-540.",
    "Excess itemized deductions over the federal standard deduction "
    "remain available as an additional LA deduction on Schedule J / "
    "NRPA — not yet modeled in this v0.1 plugin (the wrap inherits "
    "tenforty's treatment when the federal return takes itemized).",
)


LA_V1_LIMITATIONS: tuple[str, ...] = (
    "Nonresident / part-year apportionment uses day-based proration "
    "(days / 365) instead of LA Form IT-540B Schedule E income "
    "sourcing. The real treatment sources wages to the LA work site, "
    "LA-property rental to LA, LA-source business income, etc.",
    "Schedule G (Credit for taxes paid to other states) NOT applied — "
    "critical for multi-state filers. The graph backend does not "
    "expose this credit on the standard call path.",
    "School Readiness Tax Credit, LA Earned Income Credit (LA EIC, "
    "5% of federal EIC for TY2025), and the long list of LA "
    "nonrefundable credits (Schedule C / D / F) are NOT yet modeled.",
    "LA itemized deductions (Schedule J / NRPA): the wrap inherits "
    "whatever tenforty does when itemized is selected on the federal "
    "side; v0.1 has not separately verified that LA's excess-itemized "
    "calc is correct under HB 10.",
    "65+ / blind additional deductions: under HB 10 these are no "
    "longer separate exemptions; the senior $6,000 retirement exclusion "
    "is still available via Code 06E on Schedule E. v0.1 does not "
    "explicitly handle the $6,000 retirement exclusion election.",
    "Disability income exclusion (LA: up to $6,000 of permanent "
    "disability income may be excluded) is NOT explicitly modeled.",
    "Consumer Use Tax line (LA-specific) is NOT modeled.",
)


# ---------------------------------------------------------------------------
# Apportionment
# ---------------------------------------------------------------------------


def _apportionment_fraction(
    residency: ResidencyStatus, days_in_state: int
) -> Decimal:
    """Days-based apportionment fraction for nonresident / part-year.

    Residents get 1.0 (full LA tax). Nonresidents and part-year
    residents are prorated by ``days_in_state / 365``. Clamped to [0, 1].

    TODO(la-form-it-540b): replace with LA Form IT-540B Schedule E
    income-source apportionment in fan-out follow-up.
    """
    if residency == ResidencyStatus.RESIDENT:
        return Decimal("1")
    frac = Decimal(max(0, days_in_state)) / Decimal("365")
    if frac > 1:
        return Decimal("1")
    return frac


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LouisianaPlugin:
    """State plugin for Louisiana — TY2025.

    Wraps tenforty / OpenTaxSolver (graph backend) for the resident
    case and day-proration for nonresident / part-year. Starting point
    is federal AGI; LA layers HB-10-era adds/subs (Schedule E) and the
    new larger standard deduction internally.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Reuse the federal marshaling so LA sees exactly the same
        # numbers the federal calc did.
        tf_input = _to_tenforty_input(return_)

        tf_result = tenforty.evaluate_return(
            year=tf_input.year,
            state="LA",
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

        fraction = _apportionment_fraction(residency, days_in_state)
        state_tax_apportioned = cents(state_tax_full * fraction)

        state_specific: dict[str, Any] = {
            "state_federal_agi": cents(federal.adjusted_gross_income),
            "state_adjusted_gross_income": state_agi,
            "state_taxable_income": state_ti,
            "state_total_tax": state_tax_apportioned,
            "state_total_tax_resident_basis": state_tax_full,
            "state_tax_bracket": state_bracket,
            "state_effective_tax_rate": state_eff_rate,
            "state_flat_rate": LA_TY2025_FLAT_RATE,
            "apportionment_fraction": fraction,
            "starting_point": "federal_agi",
            "hb10_notes": list(LA_HB10_PHASEOUT_NOTES),
            "v1_limitations": list(LA_V1_LIMITATIONS),
            "la_personal_exemption_repealed": True,
            "la_federal_tax_deduction_repealed": True,
            "la_flat_rate_effective_2025": True,
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
        """Split canonical income into LA-source vs non-LA-source.

        Residents: everything is LA-source. Nonresident / part-year:
        prorate each category by ``days_in_state / 365``.

        TODO(la-form-it-540b): LA actually sources each income type on
        IT-540B Schedule E — wages to the work site, LA-property rental,
        LA-source business income. Day-based proration is the shared
        first-cut across all fan-out state plugins.
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

        days = days_in_state if residency != ResidencyStatus.RESIDENT else 365
        return IncomeApportionment(
            state_source_wages=day_prorate(wages, days),
            state_source_interest=day_prorate(interest, days),
            state_source_dividends=day_prorate(ord_div, days),
            state_source_capital_gains=day_prorate(capital_gains, days),
            state_source_self_employment=day_prorate(se_net, days),
            state_source_rental=day_prorate(rental_net, days),
        )

    def render_pdfs(
        self, state_return: StateReturn, out_dir: Path
    ) -> list[Path]:
        # TODO(la-pdf): fan-out follow-up — fill LA Form IT-540 (and
        # IT-540B for nonresidents, Schedule E for adjustments,
        # Schedule G for credits) using pypdf against the LA DOR's
        # fillable PDFs. The output renderer suite is the right home
        # for this; this plugin returns structured state_specific data
        # that the renderer will consume.
        return []

    def form_ids(self) -> list[str]:
        return ["LA Form IT-540"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = LouisianaPlugin(
    meta=StatePluginMeta(
        code="LA",
        name="Louisiana",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://revenue.louisiana.gov/individuals/general-resources/individual-income-tax/",
        # Louisiana File and Pay Online (LaTAP) is the LA DOR's free
        # direct-entry portal — see LA DOR IT-540 instructions cover
        # page and the General Information page.
        free_efile_url="https://latap.revenue.louisiana.gov/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # LA has NO bilateral reciprocity agreements — verified against
        # skill/reference/state-reciprocity.json (LA does not appear in
        # `agreements`). LA residents who work in another state file
        # nonresident there and claim Schedule G credit at home.
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Wraps tenforty / OpenTaxSolver (graph backend — LA is not "
            "on the OTS backend) for LA Form IT-540. RECENT LAW CHANGE: "
            "Louisiana HB 10 (2024 Special Session, signed Dec 4 2024) "
            "REPEALED the graduated bracket schedule and imposed a flat "
            "3.00% rate effective TY2025. Standard deduction raised to "
            "$12,500 (Single/MFS) / $25,000 (MFJ/HOH/QSS). Personal "
            "and dependent exemptions and the federal-tax deduction "
            "were eliminated. The graph backend matches DOR primary "
            "source ($1,575 on $65k Single) bit-for-bit. Free e-file "
            "via LaTAP. No reciprocity. Source: LA DOR IT-540 2025 "
            "instructions and HB 10."
        ),
    )
)
