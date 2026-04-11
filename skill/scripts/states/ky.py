"""Kentucky state plugin — HAND-ROLLED (not tenforty-backed).

IMPORTANT: Despite tenforty listing KY in ``tenforty.core.OTSState``,
OpenTaxSolver does NOT ship a 2025 KY_740 module — a live call

    tenforty.evaluate_return(year=2025, state='KY', ...)

raises ``ValueError: OTS does not support 2025/KY_740``. The same holds for
2024, 2023, and 2022. So unlike NC (which delegates cleanly to tenforty) this
plugin computes Kentucky Form 740 directly in Python. The task brief's
suggestion to "copy shape from NC" was structurally correct but ran into the
tenforty support gap; the fallback is the IL hand-rolled pattern, not NC's
tenforty wrap. Flagged loudly here so the merge reviewer sees it immediately.

Reference (verified 2026-04-11 via WebFetch of the KY DOR TY2025 Form 740
packet instructions, 42A740(PKT)(10-25)):

Flat rate
---------
4% (0.04). TY2025 rate confirmed on two independent KY DOR sources:

- KY DOR Individual Income Tax landing page:
  https://revenue.ky.gov/Individual/Individual-Income-Tax/Pages/default.aspx
  "The tax rate is four (4) percent and allows itemized deductions and
  certain income reducing deductions as defined in KRS 141.019."

- 2025 Form 740 instructions, "What's New":
  https://revenue.ky.gov/Forms/740%20Packet%20Instructions%20(2025).pdf
  "INDIVIDUAL INCOME TAX RATE - For 2025, the tax rate for individual income
  tax is 4%."

- 2025 Form 740 instructions, Line 12 ("Determining Your Tax"):
  "Tax Computation - Multiply line 11 by four percent (.04)."

Background: KY HB 8 (2022) put the flat rate on an automatic reduction
schedule tied to the state's Budget Reserve Trust Fund balance and General
Fund revenue; HB 1 (2023 Regular Session) locked in the 4.0% rate for
TY2024, and HB 1 (2025 Regular Session) dropped it further to 3.5% for
TY2026 per https://apps.legislature.ky.gov/record/25RS/hb1.html. For TY2025
the rate is 4% — DO NOT confuse this with the TY2026 3.5% rate.

Standard deduction
------------------
$3,270 per "column" (Form 740 Chart B). TY2025 confirmed on:

- KY DOR announcement:
  https://revenue.ky.gov/News/pages/kentucky-dor-announces-2025-standard-deduction.aspx
  "The standard deduction for 2025 is $3,270."

- 2025 Form 740 instructions, "What's New":
  https://revenue.ky.gov/Forms/740%20Packet%20Instructions%20(2025).pdf
  "STANDARD DEDUCTION - For 2025, the standard deduction is $3,270."

KY's combined-return quirk: KY Filing Status 2 ("Married Filing Joint
Return") is a COMBINED return where each spouse has their OWN column on
Form 740 (Columns A and B) with their OWN $3,270 standard deduction. Filing
Status 3 ("Married Filing Joint Return One Spouse With No Income") puts all
income in Column B only and gets ONE $3,270 deduction. Filing Status 4 is
true MFS on a separate form. The 2025 Form 740 instructions, Line 10
(Deductions), state: "Nonitemizers, enter the standard deduction of $3,270.
If filing a joint return, only one $3,270 standard deduction may be claimed"
which refers to Filing Status 3; for Status 2 each column gets $3,270.

For TY2025 MGI thresholds (Chart B), a full-year resident MUST file if KY
AGI exceeds $3,270 (single under 65), $4,270 (single 65+/blind), $5,270
(single 65+ AND blind), or $3,270 (MFJ both under 65) — i.e. roughly the
per-column standard deduction amount per Chart B.

Reciprocity
-----------
KY has bilateral reciprocity with SEVEN partners: IL, IN, MI, OH, VA, WV, WI.
That matches the 2025 Form 740-NP instructions, which list the reciprocal
states explicitly, and cross-checks against
skill/reference/state-reciprocity.json:

    IL-KY, IN-KY, KY-MI, KY-OH, KY-VA, KY-WI, KY-WV

KY has the largest reciprocity network of any state (tied with PA's 7 when
counting MD-DC-VA-WV). Under reciprocity, a KY resident working in a
reciprocal state pays KY tax only (and vice versa) provided the income is
purely wage/salary and the employee files the appropriate exemption
certificate.

Submission
----------
"Kentucky E-Tax" / KY File — KY's individual income tax portal. 2025
Form 740 instructions, "Free Electronic Filing Options":
    https://revenue.ky.gov/Individual/Pages/FreeFileSoftware.aspx
KY offers both fed/state e-file piggyback and KY's own free file software
tier. Classified as STATE_DOR_FREE_PORTAL for channel purposes since KY
operates a free file program directly, with commercial piggyback as a
secondary path.

Family Size Tax Credit
----------------------
KY Form 740 Lines 20-21. A MGI-based phaseout credit — taxpayers with
modified gross income up to 133% of the federal poverty line for their
family size get a percentage of their tax liability credited. The TY2025
Chart A thresholds are $15,650 (family of 1), $21,150 (family of 2),
$26,650 (family of 3), $32,150 (family of 4) (2025 Form 740 instructions,
"What's New"). For 100% credit, MGI must be at or below the poverty line;
the credit phases to 0 at 133% of the poverty line. See Schedule ITC
Family Size Tax Credit table. MGI threshold above which NO credit at all is
available (for family of 4): $42,760 (= 133% of $32,150). So the $65k
single-filer reference scenario gets NO Family Size Tax Credit (MGI
$65,000 > $20,815 = 133% of $15,650).

v1 LIMITATIONS (locked by tests):
---------------------------------
This v1 approximates KY AGI as federal AGI and the standard deduction as
the single-column $3,270. The following are NOT modeled yet and are
surfaced in the returned ``state_specific["v1_limitations"]`` list:

- KY Schedule M additions/subtractions:
    * SUBTRACTIONS: interest income from US government obligations
      (Treasuries, Savings Bonds) — KY exempts these per Supremacy Clause;
      KY retirement income exclusion up to $31,110 per taxpayer for
      pensions/IRAs/401(k) distributions; Social Security benefits
      (KY does not tax SS); active duty military pay (exempt since 2010
      per KRS 141.019(1)(l)); Kentucky state income tax refund; federal
      Railroad Retirement benefits; capital gains on eminent domain takings
      and KY Turnpike bonds.
    * ADDITIONS: interest income from OTHER states' municipal bonds; the
      OBBBA 2025 qualified tips / overtime / car-loan-interest deductions
      that KY explicitly does NOT conform to (TY2025 instructions, Federal
      Tax Law Changes). KY's IRC conformity date was updated by HB 775 from
      12/31/2023 to 12/31/2024, so any post-12/31/2024 federal changes
      (OBBBA) are NOT reflected on KY returns.

- MFJ combined return column split (Filing Status 2). This v1 treats MFJ as
  a single column with a single $3,270 deduction. A real Form 740 Status 2
  splits income per spouse into Columns A and B, each gets its own $3,270,
  tax is computed per column, and the two are summed. The per-column
  approach is taxpayer-favorable when both spouses have similar income
  (two deductions instead of one). Follow-up should model the column split.
  Net effect: this v1 UNDER-states the allowed deduction by $3,270 for
  Status 2 MFJ with two earners — the MFJ tax is higher than it should be
  by up to $3,270 * 0.04 = $130.80.

- Family Size Tax Credit (Lines 20-21) NOT modeled. For low-MGI taxpayers
  this can zero out their KY tax entirely. Threshold-gated so it does NOT
  affect the $65k reference scenario (MGI is too high).

- Personal Tax Credits (Schedule ITC): taxpayer (age 65+, blind, National
  Guard) credits and dependent credits are NOT modeled.

- Nonrefundable KY credits: Child and Dependent Care Credit (20% of
  federal credit per Line 24), Education Tuition Credit (Form 8863-K),
  Inventory Tax Credit, all nonrefundable per Schedule ITC.

- Refundable credits: Pass-Through Entity Tax Credit (PTET-CR),
  Decontamination, Rehabilitation Certificate, Entertainment Incentive,
  Development Area — all NOT modeled.

- Use tax (Line 27): owed on untaxed out-of-state purchases, NOT modeled.

- Nonresident / part-year returns use day-proration of the resident-basis
  tax. The real KY Form 740-NP has its own base-calc with a ratio on
  Schedule A to apportion tax. Day-based proration is the shared fan-out
  first cut; follow-up should implement the 740-NP ratio calc.

- Itemized deductions are NOT modeled (always takes the standard deduction).
  Users with $3,270+ of KY-deductible itemized items (limited to
  charitable, mortgage interest, and some others per KY Schedule A —
  NOT state/local income taxes, which are disallowed per the TY2025 fed/KY
  differences table) would be under-deducted.

Every limitation is also recorded in state_specific["v1_limitations"] so
downstream consumers can see, not guess, what this plugin does not model.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from skill.scripts.models import (
    CanonicalReturn,
    FilingStatus,
    ResidencyStatus,
    StateReturn,
)
from skill.scripts.states._plugin_api import (
    FederalTotals,
    IncomeApportionment,
    StatePlugin,
    StatePluginMeta,
    StateStartingPoint,
    SubmissionChannel,
)


_CENTS = Decimal("0.01")

# TY2025 Form 740 constants — see module docstring for citations.
KY_FLAT_RATE: Decimal = Decimal("0.04")
"""4% flat rate. TY2025 Form 740 instructions, Line 12 'Determining Your Tax':
'Multiply line 11 by four percent (.04)'. Also KY DOR Individual Income Tax
landing page: 'The tax rate is four (4) percent'. Cites:
- https://revenue.ky.gov/Individual/Individual-Income-Tax/Pages/default.aspx
- https://revenue.ky.gov/Forms/740%20Packet%20Instructions%20(2025).pdf
NOTE: TY2026 rate drops to 3.5% per HB 1 2025 regular session — DO NOT
confuse with TY2025.
"""

KY_STANDARD_DEDUCTION_TY2025: Decimal = Decimal("3270")
"""TY2025 KY standard deduction per column, all filing statuses.
TY2025 Form 740 instructions, 'What's New': 'STANDARD DEDUCTION - For 2025,
the standard deduction is $3,270'. Cite:
https://revenue.ky.gov/News/pages/kentucky-dor-announces-2025-standard-deduction.aspx
"""

# TY2025 Family Size Tax Credit Chart A thresholds — the per-family-size
# federal poverty line amounts KY uses to compute the Family Size Tax Credit.
# Source: 2025 Form 740 instructions, 'What's New' section.
KY_FAMILY_SIZE_THRESHOLDS_TY2025: dict[int, Decimal] = {
    1: Decimal("15650"),
    2: Decimal("21150"),
    3: Decimal("26650"),
    4: Decimal("32150"),  # family size capped at 4 per KY rules
}
"""Chart A / Family Size Tax Credit poverty-line thresholds for TY2025.
These define the 100%-credit threshold. The credit phases to 0 at 133% of
the threshold. Family size is capped at 4 per KY rules. Cite:
https://revenue.ky.gov/Forms/740%20Packet%20Instructions%20(2025).pdf
"""

_V1_LIMITATIONS: tuple[str, ...] = (
    "KY Schedule M additions/subtractions NOT applied — KY AGI approximated "
    "as federal AGI directly. Treasury/US-gov interest subtraction, KY "
    "retirement income exclusion (up to $31,110/taxpayer), Social Security "
    "subtraction, active-duty military pay exemption, KY tax refund "
    "subtraction, and other-state municipal bond interest addition are "
    "not modeled.",
    "OBBBA 2025 federal changes (qualified tips, overtime, car loan "
    "interest) are NOT deductible on KY returns per the 2025 Form 740 "
    "instructions Federal Tax Law Changes section; KY's IRC conformity "
    "date is 12/31/2024. This plugin uses federal AGI directly so any "
    "OBBBA adjustments baked into federal AGI must be backed out via "
    "Schedule M — NOT YET MODELED.",
    "MFJ Filing Status 2 combined-return column split (Columns A and B "
    "each getting their own $3,270 standard deduction) is NOT modeled. "
    "This v1 applies a single $3,270 deduction for MFJ, which OVER-states "
    "MFJ tax liability by up to $130.80 ($3,270 * 0.04) versus a "
    "two-earner MFJ return. Taxpayer-unfavorable v1 approximation.",
    "Family Size Tax Credit (Form 740 Lines 20-21) NOT modeled. Low-MGI "
    "taxpayers can zero out their KY tax via this credit; NOT applied "
    "here. Threshold-gated so the $65k single reference scenario is "
    "unaffected (MGI too high), but low-income filers will see higher "
    "KY tax than a real Form 740 would produce.",
    "KY nonrefundable credits (Schedule ITC) — personal tax credits for "
    "age 65+/blind/National Guard, Child and Dependent Care Credit (20% "
    "of federal), Education Tuition Credit (Form 8863-K), Inventory Tax "
    "Credit — all NOT modeled.",
    "KY refundable credits (Pass-Through Entity Tax Credit, "
    "Decontamination, Rehabilitation, Entertainment Incentive, "
    "Development Area) NOT modeled.",
    "KY Use Tax (Form 740 Line 27) on untaxed out-of-state purchases "
    "NOT modeled.",
    "KY itemized deductions (Schedule A) NOT modeled — plugin always uses "
    "the $3,270 standard deduction. Users with $3,270+ of KY-deductible "
    "items (charitable + mortgage interest; NOT state/local income tax, "
    "which KY disallows per the TY2025 fed/KY differences table) are "
    "under-deducted.",
    "Nonresident / part-year apportionment uses day-based proration "
    "(days_in_state / 365), not the KY Form 740-NP Schedule A income-source "
    "apportionment ratio. TODO: implement 740-NP logic in follow-up.",
)


def _d(v: Any) -> Decimal:
    """Coerce a float / int / None to Decimal."""
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _cents(v: Any) -> Decimal:
    """Decimal with 2 decimal places, ROUND_HALF_UP."""
    return _d(v).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _apportionment_fraction(
    residency: ResidencyStatus, days_in_state: int
) -> Decimal:
    """Days-based apportionment for nonresident / part-year.

    Residents get 1.0 (full state tax). Nonresidents and part-year residents
    are prorated by days_in_state / 365. Clamped to [0, 1].

    TODO(ky-form-740-np): replace with real KY Form 740-NP Schedule A
    income-source apportionment ratio in fan-out follow-up.
    """
    if residency == ResidencyStatus.RESIDENT:
        return Decimal("1")
    frac = Decimal(days_in_state) / Decimal("365")
    if frac < 0:
        return Decimal("0")
    if frac > 1:
        return Decimal("1")
    return frac


@dataclass(frozen=True)
class KentuckyPlugin:
    """State plugin for Kentucky — HAND-ROLLED (no tenforty).

    Computes a v1 Form 740 in-house because OpenTaxSolver does not ship a
    2025 KY_740 module (verified: tenforty raises "OTS does not support
    2025/KY_740"). Starting point: federal AGI (Form 740 Line 5 / Line 9
    approximation, Schedule M NOT applied). Standard deduction $3,270
    single-column. Flat 4% rate. Loud limitations list documents what's
    NOT modeled; see module docstring for full details.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Step 1: KY AGI. v1 approximation = federal AGI. A real Form 740
        # starts from federal AGI (Line 5) and applies Schedule M additions
        # (Line 6) and subtractions (Line 8) to reach KY AGI (Line 9) — NOT
        # modeled here. See _V1_LIMITATIONS.
        ky_agi = _cents(federal.adjusted_gross_income)

        # Step 2: Standard deduction. Single $3,270. MFJ gets a single
        # $3,270 in this v1 (the true Form 740 Filing Status 2 column split
        # is not modeled). MFS gets $3,270. See _V1_LIMITATIONS.
        standard_deduction = KY_STANDARD_DEDUCTION_TY2025

        # Step 3: KY taxable income = max(0, KY AGI - standard deduction).
        # Form 740 Line 11.
        taxable = ky_agi - standard_deduction
        if taxable < 0:
            taxable = Decimal("0")
        taxable = _cents(taxable)

        # Step 4: Flat-rate tax. Form 740 Line 12: "Multiply line 11 by
        # four percent (.04)". ROUND_HALF_UP to the cent.
        tax_full = _cents(taxable * KY_FLAT_RATE)

        # Step 5: Apportion for nonresident / part-year. TODO(ky-form-740-np):
        # replace with real Form 740-NP Schedule A ratio.
        fraction = _apportionment_fraction(residency, days_in_state)
        tax_apportioned = _cents(tax_full * fraction)

        state_specific: dict[str, Any] = {
            "state_adjusted_gross_income": ky_agi,
            "state_standard_deduction": _cents(standard_deduction),
            "state_taxable_income": taxable,
            "state_total_tax": tax_apportioned,
            "state_total_tax_resident_basis": tax_full,
            "flat_rate": KY_FLAT_RATE,
            "apportionment_fraction": fraction,
            "v1_limitations": list(_V1_LIMITATIONS),
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
        """Split canonical income into KY-source vs non-KY-source.

        Residents: everything is KY-source. Nonresident / part-year: prorate
        each category by days_in_state / 365.

        TODO(ky-form-740-np): KY Form 740-NP Schedule A sources each income
        item individually — wages to work location, interest/dividends to
        domicile, rental to property situs, business income to where the
        trade/business is conducted, etc. Day-based proration is the shared
        fan-out first cut.
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
            (f.box2a_total_capital_gain_distributions for f in return_.forms_1099_div),
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

        fraction = _apportionment_fraction(residency, days_in_state)

        return IncomeApportionment(
            state_source_wages=_cents(wages * fraction),
            state_source_interest=_cents(interest * fraction),
            state_source_dividends=_cents(ord_div * fraction),
            state_source_capital_gains=_cents(capital_gains * fraction),
            state_source_self_employment=_cents(se_net * fraction),
            state_source_rental=_cents(rental_net * fraction),
        )

    def render_pdfs(
        self, state_return: StateReturn, out_dir: Path
    ) -> list[Path]:
        # TODO(ky-pdf): fan-out follow-up — fill Form 740 (resident) and
        # Form 740-NP (nonresident/part-year), plus Schedule M / Schedule A
        # / Schedule ITC where applicable, using pypdf against the KY DOR
        # fillable PDFs at https://revenue.ky.gov/Forms/. Renderer suite is
        # the right home for this; this plugin returns structured
        # state_specific that the renderer will consume.
        return []

    def form_ids(self) -> list[str]:
        return ["KY Form 740"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = KentuckyPlugin(
    meta=StatePluginMeta(
        code="KY",
        name="Kentucky",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        # KY DOR Individual Income Tax landing page.
        dor_url="https://revenue.ky.gov/Individual/Individual-Income-Tax/Pages/default.aspx",
        # KY free file software directory (Kentucky E-Tax / KY File program).
        free_efile_url="https://revenue.ky.gov/Individual/Pages/FreeFileSoftware.aspx",
        # KY operates its own free file software tier and also piggybacks on
        # the IRS Fed/State MeF program. STATE_DOR_FREE_PORTAL reflects the
        # former (the primary path KY advertises to unassisted filers).
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # KY has the largest bilateral reciprocity network — 7 partners —
        # verified against skill/reference/state-reciprocity.json and the
        # 2025 Form 740-NP instructions which enumerate them explicitly:
        # "Illinois, Indiana, Michigan, Ohio, Virginia, West Virginia and
        # Wisconsin." A test asserts the exact set so drift fails CI.
        reciprocity_partners=("IL", "IN", "MI", "OH", "VA", "WI", "WV"),
        supported_tax_years=(2025,),
        notes=(
            "HAND-ROLLED (tenforty/OTS does NOT support 2025 KY_740 despite "
            "listing KY in OTSState — verified empirically). Flat 4% rate "
            "per TY2025 Form 740 Line 12; $3,270 standard deduction per "
            "TY2025 DOR announcement. KY has 7 reciprocity partners "
            "(IL/IN/MI/OH/VA/WI/WV) — the largest bilateral network. "
            "v1 approximates KY AGI as federal AGI — KY Schedule M "
            "additions/subtractions NOT applied. See state_specific["
            "'v1_limitations'] on compute output."
        ),
    )
)
