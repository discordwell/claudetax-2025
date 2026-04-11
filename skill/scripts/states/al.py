"""Alabama (AL) state plugin — TY2025.

Decision: HAND-ROLLED from Alabama Department of Revenue Form 40
instructions. Tenforty's graph backend (the only backend with any
TY2025 AL coverage) materially understates the AL liability because
it omits three line items that AL Form 40 requires for every resident
filer:

1. The federal income tax deduction (Form 40 line 9). Alabama is one
   of only three states (AL, IA, MO) that allows individuals to deduct
   the full federal income tax paid in computing state taxable income.
   Per ALA. CODE § 40-18-15(a)(2). Omitting this overstates AL tax
   substantially for any wage earner.

2. The Alabama standard deduction (Form 40 line 10), which uses a
   *sliding-scale* phase-down for Single / HOH / MFS based on AL AGI.
   Single starts at $3,000 (AGI ≤ $23,000) and phases linearly to a
   floor of $2,500 (AGI ≥ $30,500). MFJ starts at $8,500 (AGI ≤
   $23,500) and phases to $4,000 (AGI ≥ $33,500).

3. The personal exemption (Form 40 line 13): $1,500 Single / HOH /
   MFS, $3,000 MFJ.

The graph backend probe at $65,000 Single produces $3,210 — exactly
``rate_schedule(65,000)`` with no deductions whatsoever, i.e. tax
applied to gross AGI. CP8-B probe table records this number. The
hand calculation against AL Form 40 line-by-line lands near $2,723
(see ``test_state_al.py::TestAlabamaTaxLockSingle65k`` for the trace).
Material delta is ~$487, well outside the ±$5 graph-wrap tolerance,
so the rubric in ``skill/reference/tenforty-ty2025-gap.md`` (decision
tree, branch "material mismatch") demands a hand-roll.

Rate / base (TY2025)
--------------------
Per AL DOR Form 40 instructions ("Tax Table" and "Tax Rate Schedule"
in the back of the instruction booklet). Two rate schedules:

    Single / Head of Family / Married Filing Separately
    ---------------------------------------------------
        $0     - $500       2.0%
        $500   - $3,000     $10 + 4.0% of excess over $500
        $3,000+             $110 + 5.0% of excess over $3,000

    Married Filing Jointly
    ----------------------
        $0     - $1,000     2.0%
        $1,000 - $6,000     $20 + 4.0% of excess over $1,000
        $6,000+             $220 + 5.0% of excess over $6,000

These rates have been stable since 2003 (Amendment 25 to the AL
Constitution caps the top rate at 5%). Source: ALA. CODE § 40-18-5.

Standard deduction (Form 40 line 10) — sliding scale phase-down
---------------------------------------------------------------
Per AL DOR Form 40 Instructions, "Federal Income Tax Deduction
Worksheet" page 12 and "Standard Deduction Chart" page 11:

    Single / Head of Family / Married Filing Separately
        AGI ≤ $23,000           $3,000 (max)
        $23,000 < AGI ≤ $30,500 phases linearly $3,000 → $2,500
        AGI > $30,500           $2,500 (floor)

    Married Filing Jointly
        AGI ≤ $23,500           $8,500 (max)
        $23,500 < AGI ≤ $33,500 phases linearly $8,500 → $4,000
        AGI > $33,500           $4,000 (floor)

The phase-down is in $500 AGI steps with $25 (Single) or $250 (MFJ)
deduction decrements per step on the published Standard Deduction
Chart. v1 implements the linear phase-down formula (mathematically
equivalent for any AGI inside the phase-out window) and rounds to
the nearest $25 / $250 to match chart values. Spot-checked against
several published rows.

Personal exemption (Form 40 line 13): $1,500 Single / HOH / MFS,
$3,000 MFJ. Per ALA. CODE § 40-18-19.

Dependent exemption (Form 40 line 14) is also sliding-scale by AGI:
    AGI ≤ $20,000             $1,000 per dependent
    $20,000 < AGI ≤ $100,000  $500 per dependent
    AGI > $100,000            $300 per dependent
v1 implements all three tiers.

Federal income tax deduction (Form 40 line 9)
---------------------------------------------
Per ALA. CODE § 40-18-15(a)(2): individual taxpayers may deduct the
federal income tax actually paid (or accrued) for the same tax year.
The deduction is the federal income tax LIABILITY (line 24 of federal
1040), NOT federal withholding. v1 reads
``federal.federal_income_tax`` from the FederalTotals struct and
deducts it directly. The "Federal Income Tax Deduction Worksheet" in
the AL Form 40 instructions confirms federal income tax (1040 line 24)
is the right input.

NOTE: The AL FIT deduction is uncapped for individuals (unlike IA
which caps at $5k Single). v1 uses the full federal income tax
amount.

Reciprocity
-----------
Alabama has **no** bilateral reciprocity agreements with any other
state. Verified against ``skill/reference/state-reciprocity.json``
(AL is not present in the ``agreements`` array). AL residents who
work in neighboring TN/MS/GA/FL must file as nonresidents in any
income-tax state and claim a Schedule CR "credit for taxes paid to
other states" on AL Form 40.

Submission channel
------------------
Alabama operates **My Alabama Taxes (MAT)** as its free e-file portal
at ``https://myalabamataxes.alabama.gov/``. AL also participates in
the IRS Fed/State MeF program for commercial software piggyback. The
canonical free path is ``SubmissionChannel.STATE_DOR_FREE_PORTAL``
(MAT).

Sources (verified 2026-04-11)
-----------------------------
- Alabama Department of Revenue, "2025 Form 40 Booklet" (Form 40,
  Schedule A/B/D/E, instructions). Tax Rate Schedule and Tax Tables
  in the back of the booklet, Standard Deduction Chart on page 11,
  Federal Income Tax Deduction Worksheet on page 12, Personal
  Exemption Chart on page 13, Dependent Exemption Chart on page 13.
  https://www.revenue.alabama.gov/forms/
- ALA. CODE § 40-18-5 (rate schedule, capped at 5% by AL Const.
  Amendment 25)
- ALA. CODE § 40-18-15(a)(2) (federal income tax deduction)
- ALA. CODE § 40-18-19 (personal exemption)
- AL Form 40 line-by-line instructions

Nonresident / part-year handling
--------------------------------
AL nonresidents file Form 40NR. v1 uses day-based proration of the
resident-basis tax as the shared first-cut across all hand-rolled
plugins. The real Form 40NR sources income by line type (wages to
the work location, interest/dividends to domicile, etc.) and applies
the AL standard deduction / personal exemption proportionally to AL-
source income. Flagged in ``AL_V1_LIMITATIONS`` as
``TODO(al-form-40nr)``.
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


# Canonical wave-5 $65k Single gatekeeper lock. Hand-traced from AL
# DOR Form 40 — see module docstring. Referenced from test_state_al.py.
LOCK_VALUE: Final[Decimal] = Decimal("2722.25")


# ---------------------------------------------------------------------------
# TY2025 constants — verified from AL DOR 2025 Form 40 booklet
# ---------------------------------------------------------------------------


# Rate schedule. Same brackets for Single / HOH / MFS; doubled for MFJ.
# Source: ALA. CODE § 40-18-5; AL Form 40 instruction booklet "Tax Rate
# Schedule" page (back of booklet).
AL_TY2025_BRACKETS_SINGLE: tuple[GraduatedBracket, ...] = (
    GraduatedBracket(low=Decimal("0"),    high=Decimal("500"),   rate=Decimal("0.02")),
    GraduatedBracket(low=Decimal("500"),  high=Decimal("3000"),  rate=Decimal("0.04")),
    GraduatedBracket(low=Decimal("3000"), high=None,             rate=Decimal("0.05")),
)

AL_TY2025_BRACKETS_MFJ: tuple[GraduatedBracket, ...] = (
    GraduatedBracket(low=Decimal("0"),     high=Decimal("1000"), rate=Decimal("0.02")),
    GraduatedBracket(low=Decimal("1000"),  high=Decimal("6000"), rate=Decimal("0.04")),
    GraduatedBracket(low=Decimal("6000"),  high=None,            rate=Decimal("0.05")),
)


# Standard deduction (Form 40 line 10) — sliding-scale phase-down.
# Source: AL Form 40 instructions "Standard Deduction Chart" page 11.
AL_TY2025_STD_DED_SINGLE_MAX: Decimal = Decimal("3000")  # AGI ≤ $23,000
AL_TY2025_STD_DED_SINGLE_MIN: Decimal = Decimal("2500")  # AGI ≥ $30,500
AL_TY2025_STD_DED_SINGLE_PHASE_START: Decimal = Decimal("23000")
AL_TY2025_STD_DED_SINGLE_PHASE_END: Decimal = Decimal("30500")

AL_TY2025_STD_DED_MFJ_MAX: Decimal = Decimal("8500")  # AGI ≤ $23,500
AL_TY2025_STD_DED_MFJ_MIN: Decimal = Decimal("4000")  # AGI ≥ $33,500
AL_TY2025_STD_DED_MFJ_PHASE_START: Decimal = Decimal("23500")
AL_TY2025_STD_DED_MFJ_PHASE_END: Decimal = Decimal("33500")


# Personal exemption (Form 40 line 13) per ALA. CODE § 40-18-19.
AL_TY2025_PERSONAL_EXEMPTION_SINGLE: Decimal = Decimal("1500")
AL_TY2025_PERSONAL_EXEMPTION_MFJ: Decimal = Decimal("3000")
AL_TY2025_PERSONAL_EXEMPTION_HOH: Decimal = Decimal("3000")
AL_TY2025_PERSONAL_EXEMPTION_MFS: Decimal = Decimal("1500")


# Dependent exemption (Form 40 line 14) — sliding by AGI.
# Source: AL Form 40 instructions "Dependent Exemption Chart" page 13.
AL_TY2025_DEPENDENT_HIGH: Decimal = Decimal("1000")  # AGI ≤ $20,000
AL_TY2025_DEPENDENT_MID: Decimal = Decimal("500")    # $20k < AGI ≤ $100k
AL_TY2025_DEPENDENT_LOW: Decimal = Decimal("300")    # AGI > $100,000


AL_V1_LIMITATIONS: tuple[str, ...] = (
    "AL Schedule A itemized deductions NOT supported in v1; the plugin "
    "always takes the AL standard deduction. Filers who itemize on "
    "federal Schedule A typically also itemize on AL Schedule A.",
    "AL Schedule W-2 (Wages and Salary additions) NOT applied — v1 "
    "uses federal AGI as AL gross income with no AL-only adjustments.",
    "AL Schedule HOR (Head of Family) — Head of Family in AL has its "
    "own qualifying-relative test that v1 does not enforce; v1 maps "
    "federal HOH to AL Head of Family treatment.",
    "AL Form 40 line 9 federal income tax deduction uses the federal "
    "income tax LIABILITY (1040 line 24). v1 reads "
    "federal.federal_income_tax. AL also allows deducting federal "
    "self-employment tax via a separate line; v1 does not.",
    "AL credits NOT applied (Form 40 line 18+): credit for taxes paid "
    "to other states (Schedule CR — critical for multi-state filers), "
    "rural physician credit, adoption credit, irrigation/reservoir "
    "credit, basic skills education credit, alternative fuel credit, "
    "Alabama child care tax credit (refundable for low/moderate income).",
    "AL Form 40NR nonresident return NOT implemented — v1 uses day-based "
    "proration of the resident-basis tax. Real Form 40NR sources income "
    "by line type (AL-source wages, AL-source rental, etc.) and applies "
    "the standard deduction proportionally.",
    "AL allows an additional $1,500 personal exemption for taxpayers "
    "65+ or blind. v1 does not yet handle the 65+/blind add-on.",
    "AL retirement income exclusion (defined-benefit pension fully "
    "exempt) and military retirement exclusion NOT applied in v1.",
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def al_standard_deduction(
    filing_status: FilingStatus, al_agi: Decimal
) -> Decimal:
    """Return the AL Form 40 line 10 standard deduction for the given AGI.

    Implements the AL DOR Standard Deduction Chart phase-down: Single /
    HOH / MFS phase from $3,000 (AGI ≤ $23,000) linearly to $2,500
    (AGI ≥ $30,500); MFJ phases from $8,500 (AGI ≤ $23,500) to $4,000
    (AGI ≥ $33,500). Returns a Decimal rounded to the nearest $25
    (Single) or $250 (MFJ) to match the chart's printed step sizes.

    Source: AL Form 40 instructions "Standard Deduction Chart" page 11.
    """
    al_agi = d(al_agi)
    if filing_status in (FilingStatus.MFJ, FilingStatus.QSS):
        max_amt = AL_TY2025_STD_DED_MFJ_MAX
        min_amt = AL_TY2025_STD_DED_MFJ_MIN
        start = AL_TY2025_STD_DED_MFJ_PHASE_START
        end = AL_TY2025_STD_DED_MFJ_PHASE_END
        step = Decimal("250")
    else:
        max_amt = AL_TY2025_STD_DED_SINGLE_MAX
        min_amt = AL_TY2025_STD_DED_SINGLE_MIN
        start = AL_TY2025_STD_DED_SINGLE_PHASE_START
        end = AL_TY2025_STD_DED_SINGLE_PHASE_END
        step = Decimal("25")

    if al_agi <= start:
        return max_amt
    if al_agi >= end:
        return min_amt

    # Linear phase-down inside the window.
    span = end - start
    progress = (al_agi - start) / span
    raw = max_amt - progress * (max_amt - min_amt)
    # Round to the chart's step size, half-up.
    rounded_steps = (raw / step).quantize(Decimal("1"))
    return min_amt if rounded_steps * step < min_amt else max_amt if rounded_steps * step > max_amt else rounded_steps * step


def al_personal_exemption(filing_status: FilingStatus) -> Decimal:
    """Return the AL Form 40 line 13 personal exemption.

    $1,500 Single / MFS; $3,000 MFJ / QSS / HOH (Head of Family in AL).
    Source: ALA. CODE § 40-18-19.
    """
    if filing_status in (FilingStatus.MFJ, FilingStatus.QSS):
        return AL_TY2025_PERSONAL_EXEMPTION_MFJ
    if filing_status == FilingStatus.HOH:
        return AL_TY2025_PERSONAL_EXEMPTION_HOH
    return AL_TY2025_PERSONAL_EXEMPTION_SINGLE


def al_dependent_exemption(
    al_agi: Decimal, num_dependents: int
) -> Decimal:
    """Return the AL Form 40 line 14 dependent exemption total.

    Sliding by AGI per AL DOR "Dependent Exemption Chart":
        AGI ≤ $20,000           $1,000 / dep
        $20,000 < AGI ≤ $100k   $500   / dep
        AGI > $100,000          $300   / dep

    Returns 0 if ``num_dependents`` is non-positive.
    """
    n = max(0, int(num_dependents))
    if n == 0:
        return Decimal("0")
    al_agi = d(al_agi)
    if al_agi <= Decimal("20000"):
        per = AL_TY2025_DEPENDENT_HIGH
    elif al_agi <= Decimal("100000"):
        per = AL_TY2025_DEPENDENT_MID
    else:
        per = AL_TY2025_DEPENDENT_LOW
    return Decimal(n) * per


def al_tax_from_schedule(
    taxable_income: Decimal, filing_status: FilingStatus
) -> Decimal:
    """AL Form 40 line 17 tax via the Tax Rate Schedule.

    Single / HOH / MFS: 2% / 4% / 5% with breakpoints at $500 and $3,000.
    MFJ / QSS: 2% / 4% / 5% with breakpoints at $1,000 and $6,000
    (doubled for joint).
    """
    if d(taxable_income) <= 0:
        return Decimal("0.00")
    if filing_status in (FilingStatus.MFJ, FilingStatus.QSS):
        brackets = AL_TY2025_BRACKETS_MFJ
    else:
        brackets = AL_TY2025_BRACKETS_SINGLE
    return graduated_tax(d(taxable_income), brackets)


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AlabamaPlugin:
    """State plugin for Alabama — TY2025.

    Hand-rolled Form 40 calculation. Tenforty's graph backend
    materially understates the deductions (omits federal income tax
    deduction, AL standard deduction, and personal exemption), so the
    plugin computes from primary source.

    Flow:
        federal_AGI
          -> AL_AGI                                  (v1: same as federal AGI)
          -> AL_AGI - federal_income_tax_deduction
                    - al_standard_deduction(AGI)
                    - al_personal_exemption
                    - al_dependent_exemption(AGI, num_deps)
          -> AL_taxable_income
          -> tax via AL Tax Rate Schedule
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
        # Form 40 Line 5/7: Total income / AL AGI. v1 = federal AGI.
        federal_agi = d(federal.adjusted_gross_income)
        al_modifications = Decimal("0")
        al_agi = max(Decimal("0"), federal_agi + al_modifications)

        # Line 9: Federal income tax deduction (uncapped for individuals).
        fit_deduction = d(federal.federal_income_tax)

        # Line 10: AL standard deduction (sliding scale).
        std_ded = al_standard_deduction(federal.filing_status, al_agi)

        # Line 13: Personal exemption.
        personal_exemption = al_personal_exemption(federal.filing_status)

        # Line 14: Dependent exemption (sliding by AGI).
        dependent_exemption = al_dependent_exemption(
            al_agi, federal.num_dependents
        )

        # Line 16: AL taxable income.
        total_deductions = (
            fit_deduction + std_ded + personal_exemption + dependent_exemption
        )
        al_taxable_income = max(
            Decimal("0"), al_agi - total_deductions
        )

        # Line 17: AL income tax via the Tax Rate Schedule.
        al_tax_full = al_tax_from_schedule(
            al_taxable_income, federal.filing_status
        )

        # Apportion for nonresident / part-year (day-based v1).
        al_tax_apportioned = day_prorate(al_tax_full, days_in_state)

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
            "state_federal_agi": cents(federal_agi),
            "state_adjusted_gross_income": cents(al_agi),
            "state_federal_income_tax_deduction": cents(fit_deduction),
            "state_standard_deduction": cents(std_ded),
            "state_personal_exemption": cents(personal_exemption),
            "state_dependent_exemption": cents(dependent_exemption),
            "state_total_deductions": cents(total_deductions),
            "state_taxable_income": cents(al_taxable_income),
            "state_total_tax": al_tax_apportioned,
            "state_total_tax_resident_basis": al_tax_full,
            "apportionment_fraction": apportionment_fraction,
            "starting_point": "federal_agi",
            "al_modifications_applied": al_modifications,
            "v1_limitations": list(AL_V1_LIMITATIONS),
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
        """Split canonical income into AL-source vs non-AL-source.

        Residents: everything is AL-source. Nonresident / part-year:
        prorate each category by ``days_in_state / 365``.

        TODO(al-form-40nr): real AL Form 40NR sources income by line
        type — wages to the work location, rental to the property
        state, interest/dividends to the taxpayer's domicile.
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
        # TODO(al-pdf): fan-out follow-up — fill AL Form 40 (and Schedule
        # A/B/D/E, Form 40NR for nonresidents, Schedule CR for credit-
        # for-taxes-paid). Output renderer is the right home for this;
        # this plugin returns structured state_specific data.
        return []

    def form_ids(self) -> list[str]:
        return ["AL Form 40"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = AlabamaPlugin(
    meta=StatePluginMeta(
        code="AL",
        name="Alabama",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://www.revenue.alabama.gov/forms/",
        # My Alabama Taxes (MAT) — the AL DOR's free e-file portal.
        free_efile_url="https://myalabamataxes.alabama.gov/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # Alabama has NO bilateral reciprocity agreements with any
        # state — verified against skill/reference/state-reciprocity.json
        # (AL is not present in `agreements`).
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Hand-rolled AL Form 40 calc; tenforty graph backend "
            "omits the federal income tax deduction, the AL sliding-"
            "scale standard deduction, and the personal exemption "
            "(graph result $3,210 vs hand-roll ~$2,723 on a $65k "
            "Single, see TestTenfortyStillHasGapOnAL). Brackets per "
            "ALA. CODE § 40-18-5: 2% / 4% / 5% with breakpoints at "
            "$500/$3,000 (Single, doubled for MFJ). Top rate capped "
            "at 5% by AL Const. Amendment 25. Federal income tax "
            "deduction allowed (one of three states — AL, IA, MO). "
            "Free e-file via My Alabama Taxes (MAT). No reciprocity "
            "agreements. Source: AL DOR 2025 Form 40 booklet."
        ),
    )
)
