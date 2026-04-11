"""Mississippi (MS) state plugin — TY2025.

See skill/reference/tenforty-ty2025-gap.md for the TY2025 probe rubric
and the graph-backend output-field gap list (state_taxable_income
echo, state_tax_bracket=0, state_effective_tax_rate=0).

Wraps tenforty / OpenTaxSolver (graph backend) for the Mississippi
Form 80-105 resident calc, mirroring the wave-4 ``wi.py`` graph-wrapper
pattern. Day-based proration is the v0.1 nonresident / part-year
fallback (real treatment is Form 80-205).

Source of truth
---------------
Tenforty exposes Mississippi via ``OTSState.MS`` and ships an MS Form
80-105 graph definition; the default OTS backend raises
``ValueError: OTS does not support 2025/MS_80105`` so this plugin calls
``tenforty.evaluate_return(..., backend='graph')`` explicitly. WI is
the wave-4 precedent for the same idiom.

Probe (verified 2026-04-11 against tenforty installed in .venv):
    Single / $65,000 W-2 / Standard
        -> state_total_tax              = 2054.80
           state_taxable_income         = 56700.00  (= 65000 - 6000 ex - 2300 std)
           state_adjusted_gross_income  = 65000.00
           state_tax_bracket            = 0.0       (graph backend omits)
           state_effective_tax_rate     = 0.0       (graph backend omits)

The graph backend's $2,054.80 number reconciles bit-for-bit against
the TY2025 MS DOR primary source (see "Hand verification" below). It
is trusted as the canonical wrapped value; this plugin pins it so any
upstream OTS / tenforty schedule drift fails CI.

LOUDLY FLAGGED RECENT LAW CHANGE
--------------------------------
Mississippi **HB 531 of 2022 ("the Mississippi Tax Freedom Act")** —
sometimes called "Build-Up Mississippi" — phased out the prior
graduated brackets in favor of a flat tax above a $10,000 zero-rate
floor, with year-over-year rate cuts:

    TY2023: 5.0%
    TY2024: 4.7%
    TY2025: **4.4%**   <-- this plugin
    TY2026: 4.0%

Subsequent legislation (**HB 1 of 2025**) extends the phase-down beyond
the original HB 531 schedule. Per Mississippi DOR and visaverge.com
reporting, the current schedule continues to **3.0% by 2030**, with
further annual reductions thereafter contingent on state revenue
growth triggers.

The pre-2023 graduated 3% / 4% / 5% schedule (over $5,000 / $10,000)
is GONE. For TY2025, Mississippi imposes:

- 0% on the first $10,000 of MS taxable income (the "zero bracket"),
- 4.4% on every dollar of MS taxable income above $10,000.

Standard deductions and personal exemptions are UNCHANGED from
pre-HB 531 amounts (Single $2,300 std + $6,000 exemption; MFJ $4,600
std + $12,000 exemption; HOH $3,400 std + $8,000 exemption; MFS $2,300
std + $6,000 exemption; $1,500 per dependent).

Sources:
    - Mississippi Department of Revenue, "GENERAL INFORMATION"
      https://www.dor.ms.gov/general-information
    - Mississippi DOR Form 80-100 (2025 Resident, Non-Resident and
      Part-Year Resident Income Tax Instructions), "What's New" page.
      https://www.dor.ms.gov/sites/default/files/tax-forms/individual/80100251%202.pdf
    - HB 531, 2022 Regular Session — "Mississippi Tax Freedom Act"
    - HB 1, 2025 Regular Session — extended phase-down to 3.0%
    - VisaVerge, "Mississippi's 2025 Tax Overhaul"
      https://www.visaverge.com/taxes/mississippi-state-income-tax-rates-and-brackets-for-2025/

Hand verification ($65k Single, TY2025)
----------------------------------------
    MS AGI                = federal AGI = $65,000   (80-105 line ~ 14)
    MS personal exemption = $6,000                  (Single, 80-105
                                                     filing-status box)
    MS standard deduction = $2,300                  (Single, 80-105
                                                     line 17)
    MS taxable income     = 65,000 - 6,000 - 2,300 = $56,700
    Tax = (56,700 - 10,000) * 0.044                 (4.4% above $10k)
        = 46,700 * 0.044
        = 2,054.80
    Total MS tax          = $2,054.80

Matches the tenforty graph backend value of $2,054.80 exactly (and
``state_taxable_income = 56,700`` agrees too — graph backend correctly
applies both the standard deduction AND the personal exemption for
MS, unlike OK and IL where the graph misses the exemption). Decision
per the gap-doc rubric: **WRAP** (graph backend is correct for MS at
TY2025).

Reciprocity
-----------
Mississippi has **NO** bilateral reciprocity agreements with any state
(verified against ``skill/reference/state-reciprocity.json``: MS does
not appear in the ``agreements`` array). MS residents who work in
another state file nonresident there and claim the MS credit for
income tax paid to other states.

Submission channel
------------------
Mississippi operates a free direct-entry portal, **TAP (Mississippi
Taxpayer Access Point)**, at https://tap.dor.ms.gov/. The state also
participates in the IRS Fed/State MeF program; commercial software
piggybacks the MS return with the federal 1040. The canonical channel
for this plugin is ``SubmissionChannel.STATE_DOR_FREE_PORTAL`` (the
free TAP path).

Nonresident / part-year
-----------------------
Mississippi's real nonresident / part-year treatment uses **Form
80-205** with MS-source income sourcing (MS wages to the work site,
MS-property rental, MS business income). v0.1 falls back to day-based
proration of the resident-basis tax — the same first-cut every fan-out
state plugin uses. ``TODO(ms-form-80-205)`` tracks the real treatment.

Form IDs
--------
- MS Form 80-105 (Resident Individual Income Tax Return)
- MS Form 80-205 (Nonresident / Part-Year — fan-out follow-up)
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


_TENFORTY_BACKEND = "graph"


# TY2025 constants — these are NOT used by the wrap path (tenforty owns
# the actual computation). They are exposed so tests can pin the law
# without re-deriving it from the 80-105 instructions on every run.

MS_TY2025_FLAT_RATE: Decimal = Decimal("0.044")
"""Mississippi TY2025 flat individual income tax rate above the
$10,000 zero-bracket = 4.40%. Source: HB 531 (2022) phase-down
schedule, MS DOR Form 80-100 2025 instructions 'What's New'."""

MS_TY2025_ZERO_BRACKET: Decimal = Decimal("10000")
"""MS taxable income up to $10,000 is taxed at 0% (zero-bracket
floor). The 4.40% flat rate applies to income above $10,000.
Source: HB 531 / MS Code Ann. 27-7-5 as amended."""

# Standard deductions (MS DOR Form 80-100 General Information)
MS_TY2025_STD_DED_SINGLE: Decimal = Decimal("2300")
MS_TY2025_STD_DED_MFJ: Decimal = Decimal("4600")
MS_TY2025_STD_DED_HOH: Decimal = Decimal("3400")
MS_TY2025_STD_DED_MFS: Decimal = Decimal("2300")

# Personal exemptions (MS DOR Form 80-100 General Information)
MS_TY2025_EXEMPTION_SINGLE: Decimal = Decimal("6000")
MS_TY2025_EXEMPTION_MFJ: Decimal = Decimal("12000")
MS_TY2025_EXEMPTION_HOH: Decimal = Decimal("8000")
MS_TY2025_EXEMPTION_MFS: Decimal = Decimal("6000")
MS_TY2025_EXEMPTION_PER_DEPENDENT: Decimal = Decimal("1500")
"""Each dependent (and each over-65 / blind add-on) gets an additional
$1,500 personal exemption."""


MS_HB531_PHASEDOWN_NOTES: tuple[str, ...] = (
    "HB 531 (2022 Regular Session, the 'Mississippi Tax Freedom Act') "
    "repealed the pre-2023 graduated 3%/4%/5% schedule and replaced "
    "it with a flat tax above a $10,000 zero-bracket floor.",
    "The flat rate phases down annually: 5.0% (TY2023), 4.7% (TY2024), "
    "4.4% (TY2025), 4.0% (TY2026), reaching 3.0% by TY2030 per HB 1 "
    "(2025).",
    "First $10,000 of MS taxable income is the zero-bracket — taxed "
    "at 0%. Above $10,000, the flat 4.4% rate applies for TY2025.",
    "Personal exemptions and standard deductions are unchanged from "
    "pre-HB 531 amounts. The phase-down only affects the rate.",
    "Further reductions beyond 3.0% (after TY2030) are contingent on "
    "specific state revenue growth triggers being met.",
)


MS_V1_LIMITATIONS: tuple[str, ...] = (
    "Nonresident / part-year apportionment uses day-based proration "
    "(days / 365) instead of MS Form 80-205 income sourcing. The real "
    "treatment sources wages to the MS work site, MS-property rental, "
    "MS-source business income, etc.",
    "Credit for income tax paid to other states (MS Schedule N) NOT "
    "applied — critical for multi-state filers (MS residents working "
    "in TN/AL/LA/AR).",
    "MS additional exemption for age 65 or over / blind ($1,500 each) "
    "is NOT explicitly modeled in v0.1. The graph backend may apply "
    "it if num_dependents reflects the count, but verify before "
    "shipping.",
    "Mississippi itemized deductions (Form 80-108): the wrap inherits "
    "whatever tenforty does when itemized is selected on the federal "
    "side; v0.1 has not separately verified that MS's add-back of "
    "state income tax deducted is correct.",
    "MS Earned Income Tax Credit: Mississippi does NOT have a state "
    "EITC; this is a non-limitation, noted for completeness.",
    "Schedule K (credit for taxes paid by partnerships / S-corps) "
    "NOT applied.",
    "Mississippi gambling winnings (MS-source) and the MS gaming "
    "deduction are NOT explicitly modeled.",
    "Catastrophe Savings Account contributions (MS-specific "
    "subtraction) NOT applied.",
    "First-Time Home Buyer Savings Account (MS-specific subtraction) "
    "NOT applied.",
)


# ---------------------------------------------------------------------------
# Apportionment
# ---------------------------------------------------------------------------


def _apportionment_fraction(
    residency: ResidencyStatus, days_in_state: int
) -> Decimal:
    """Days-based apportionment fraction for nonresident / part-year.

    TODO(ms-form-80-205): replace with MS Form 80-205 income-source
    apportionment in fan-out follow-up.
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
class MississippiPlugin:
    """State plugin for Mississippi — TY2025.

    Wraps tenforty / OpenTaxSolver (graph backend) for the resident
    case and day-proration for nonresident / part-year. Starting point
    is federal AGI; MS layers its own personal exemption and standard
    deduction internally on Form 80-105.
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
            state="MS",
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
            "state_flat_rate": MS_TY2025_FLAT_RATE,
            "state_zero_bracket": MS_TY2025_ZERO_BRACKET,
            "apportionment_fraction": fraction,
            "starting_point": "federal_agi",
            "hb531_notes": list(MS_HB531_PHASEDOWN_NOTES),
            "v1_limitations": list(MS_V1_LIMITATIONS),
            "ms_flat_rate_phasing_down": True,
            "ms_zero_bracket_first_10k": True,
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
        """Split canonical income into MS-source vs non-MS-source.

        TODO(ms-form-80-205): real per-category sourcing on Form
        80-205. v0.1 uses day-based proration.
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
            state_source_wages=sourced_or_prorated_wages(return_, "MS", wages, days),
            state_source_interest=day_prorate(interest, days),
            state_source_dividends=day_prorate(ord_div, days),
            state_source_capital_gains=day_prorate(capital_gains, days),
            state_source_self_employment=sourced_or_prorated_schedule_c(return_, "MS", se_net, days),
            state_source_rental=day_prorate(rental_net, days),
        )

    def render_pdfs(
        self, state_return: StateReturn, out_dir: Path
    ) -> list[Path]:
        # TODO(ms-pdf): fan-out follow-up — fill MS Form 80-105 (and
        # 80-205 for nonresidents, 80-108 for itemizers) using pypdf
        # against the MS DOR's fillable PDFs.
        return []

    def form_ids(self) -> list[str]:
        return ["MS Form 80-105"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = MississippiPlugin(
    meta=StatePluginMeta(
        code="MS",
        name="Mississippi",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://www.dor.ms.gov/individual",
        # MS DOR Taxpayer Access Point — TAP — is the free direct-entry
        # portal: https://tap.dor.ms.gov/
        free_efile_url="https://tap.dor.ms.gov/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # MS has NO bilateral reciprocity agreements — verified against
        # skill/reference/state-reciprocity.json (MS does not appear in
        # `agreements`).
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Wraps tenforty / OpenTaxSolver (graph backend — MS is not "
            "on the OTS backend) for MS Form 80-105. RECENT LAW: "
            "Mississippi HB 531 (2022) and HB 1 (2025) phase down the "
            "individual income tax. TY2025 imposes a flat 4.4% rate on "
            "MS taxable income above $10,000 (the zero-bracket floor). "
            "Standard deduction $2,300 Single / $4,600 MFJ / $3,400 "
            "HOH; personal exemption $6,000 Single / $12,000 MFJ / "
            "$8,000 HOH; $1,500 per dependent. The graph backend "
            "matches DOR primary source ($2,054.80 on $65k Single) "
            "bit-for-bit. Free e-file via MS TAP. No reciprocity. "
            "Source: MS DOR Form 80-100 2025 instructions and HB 531."
        ),
    )
)
