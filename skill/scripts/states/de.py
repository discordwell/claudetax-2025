"""Delaware (DE) state plugin — TY2025.

Decision: HAND-ROLLED from Delaware Form 200-01 instructions. The
tenforty graph backend correctly applies the DE rate schedule and
the DE standard deduction ($3,250 Single), but it OMITS the DE
personal credit ($110 per personal exemption per DE Form 200-01
line 28) which is applied AFTER the rate schedule on every DE
return.

Cross-check at $65,000 Single (CP8-B probe table value $3,059):

    DE AGI            = $65,000
    DE std ded Single = $3,250
    DE TI             = $61,750
    Rate schedule on $61,750 (Single):
        2.2% × ($5,000 - $2,000) = $66.00
        3.9% × ($10,000 - $5,000) = $195.00
        4.8% × ($20,000 - $10,000) = $480.00
        5.2% × ($25,000 - $20,000) = $260.00
        5.55% × ($60,000 - $25,000) = $1,942.50
        6.6% × ($61,750 - $60,000) = $115.50
        Sum = $3,059.00 (matches graph backend exactly)
    Less DE personal credit (1 exemption × $110)         = -$110.00
    DE Form 200-01 line 31 (tax after credits)            =  $2,949.00

The graph backend reports $3,059 (line 27 — tax before credits).
The CORRECT answer per DE Form 200-01 line 31 is $2,949. A $110
delta is well outside the ±$5 graph-wrap tolerance, so per the
rubric in ``skill/reference/tenforty-ty2025-gap.md`` (decision tree,
branch "material mismatch"), DE is hand-rolled.

The hand-roll uses the same DE rate schedule as the graph backend
(verified bit-for-bit on a probe sweep at $10k / $25k / $50k / $100k
/ $200k Single — see ``test_state_de.py``) and applies the
$110/exemption personal credit.

Rate / base (TY2025)
--------------------
Per Delaware Form 200-01 instructions, "Tax Rate Schedules" page:

    All filing statuses (DE uses the same brackets for everyone)
    -----------------------------------------------------------
        $0      - $2,000      0.0%   (no tax)
        $2,000  - $5,000      2.2%
        $5,000  - $10,000     3.9%
        $10,000 - $20,000     4.8%
        $20,000 - $25,000     5.2%
        $25,000 - $60,000     5.55%
        $60,000+              6.6%

Yes — Delaware uses the same brackets for Single, MFJ, HOH, and MFS.
Delaware does NOT double brackets for joint filers; spouses filing
jointly compute tax on the combined income at the same brackets a
single filer would use. (This is unusual; most graduated states
double the brackets for MFJ.)

DE source: DE DOR Form 200-01 Resident Booklet 2025 ("Tax Rate
Schedules" appendix).

Standard deduction (Form 200-01 line 18)
----------------------------------------
    Single / HOH / MFS    $3,250
    Married Filing Joint  $6,500   (literal double, on the combined
                                    return — not duplicated per spouse)

Source: DE Form 200-01 instructions page 7. Additional std ded for
65+/blind: +$2,500 each (Single/HOH/MFS) or +$2,500 each spouse
condition (MFJ). v1 does NOT yet handle the 65+/blind add-on —
flagged in DE_V1_LIMITATIONS.

Personal credit (Form 200-01 line 28)
-------------------------------------
$110 per personal exemption. The personal exemption count is:
    Single / HOH / MFS:    1 (taxpayer)        + dependents
    Married Filing Joint:  2 (both spouses)    + dependents
    Married Filing Sep on Combined Return: 2   + dependents (spouses
        each get one)

Plus an additional $110 if 65 or older. v1 implements the base
exemption count + dependents only.

Source: DE Form 200-01 instructions page 8. ``Personal Credits``
section.

Reciprocity
-----------
Delaware has **no** bilateral reciprocity agreements with any other
state. Verified against ``skill/reference/state-reciprocity.json``
(DE is not present in the ``agreements`` array). DE residents who
work in PA, NJ, or MD must file as nonresidents in those states and
claim the DE "credit for taxes paid to other states" on Schedule I
of Form 200-01.

Note: DE-PA and DE-NJ are notable non-reciprocity borders (heavy
commuter traffic) — DE residents working in Philadelphia must file
both PA and DE returns, and DE residents working in NJ similarly.

Submission channel
------------------
Delaware operates **Delaware Taxpayer Portal** as its free e-file
portal at ``https://revenue.delaware.gov/file-individual-income-
tax/``. DE also participates in the IRS Fed/State MeF program for
commercial software piggyback. The canonical free path is
``SubmissionChannel.STATE_DOR_FREE_PORTAL``.

Sources (verified 2026-04-11)
-----------------------------
- Delaware Division of Revenue, individual income tax landing:
  https://revenue.delaware.gov/
- DE Form 200-01 (resident) and 200-02 (nonresident) booklets, TY2025.
- 30 Del. C. § 1102 (rate schedule)
- 30 Del. C. § 1110 (personal credit)

Nonresident / part-year handling
--------------------------------
DE nonresidents file Form 200-02 with a separate apportionment ratio
(DE-source income / total income). v1 uses day-based proration of
the resident-basis tax as the shared first-cut across all wave-5
plugins. Flagged as ``TODO(de-form-200-02)`` in DE_V1_LIMITATIONS.
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


# Canonical wave-5 $65k Single gatekeeper lock. Hand-traced from DE
# Form 200-01 — see module docstring. Referenced from test_state_de.py.
LOCK_VALUE: Final[Decimal] = Decimal("2949.00")


# ---------------------------------------------------------------------------
# TY2025 constants — verified from DE Form 200-01 booklet
# ---------------------------------------------------------------------------


# DE rate schedule. Same brackets for ALL filing statuses (unusual!)
# Source: 30 Del. C. § 1102; DE Form 200-01 instructions "Tax Rate
# Schedules" appendix.
DE_TY2025_BRACKETS: tuple[GraduatedBracket, ...] = (
    GraduatedBracket(low=Decimal("0"),     high=Decimal("2000"),  rate=Decimal("0.0")),
    GraduatedBracket(low=Decimal("2000"),  high=Decimal("5000"),  rate=Decimal("0.022")),
    GraduatedBracket(low=Decimal("5000"),  high=Decimal("10000"), rate=Decimal("0.039")),
    GraduatedBracket(low=Decimal("10000"), high=Decimal("20000"), rate=Decimal("0.048")),
    GraduatedBracket(low=Decimal("20000"), high=Decimal("25000"), rate=Decimal("0.052")),
    GraduatedBracket(low=Decimal("25000"), high=Decimal("60000"), rate=Decimal("0.0555")),
    GraduatedBracket(low=Decimal("60000"), high=None,             rate=Decimal("0.066")),
)


# Standard deduction (Form 200-01 line 18). Source: DE Form 200-01
# instructions page 7.
DE_TY2025_STD_DED_SINGLE: Decimal = Decimal("3250")
DE_TY2025_STD_DED_MFJ: Decimal = Decimal("6500")
DE_TY2025_STD_DED_HOH: Decimal = Decimal("3250")
DE_TY2025_STD_DED_MFS: Decimal = Decimal("3250")

DE_TY2025_STD_DED_BY_STATUS: dict[FilingStatus, Decimal] = {
    FilingStatus.SINGLE: DE_TY2025_STD_DED_SINGLE,
    FilingStatus.MFJ: DE_TY2025_STD_DED_MFJ,
    FilingStatus.QSS: DE_TY2025_STD_DED_MFJ,
    FilingStatus.HOH: DE_TY2025_STD_DED_HOH,
    FilingStatus.MFS: DE_TY2025_STD_DED_MFS,
}


# Personal credit (Form 200-01 line 28). $110 per personal exemption
# (taxpayer + spouse if MFJ) plus $110 per dependent. Source: 30 Del.
# C. § 1110; DE Form 200-01 instructions page 8.
DE_TY2025_PERSONAL_CREDIT_PER_EXEMPTION: Decimal = Decimal("110")


DE_V1_LIMITATIONS: tuple[str, ...] = (
    "DE Schedule I additions/subtractions NOT applied: state/municipal "
    "bond interest from non-DE sources (addition), pension exclusion "
    "($2,000-$12,500 by age), Social Security disability benefit "
    "exclusion, US obligations interest subtraction, Delaware College "
    "Investment Plan deduction.",
    "DE Schedule A itemized deductions NOT supported in v1 — plugin "
    "always uses the DE standard deduction. DE allows itemizing on "
    "Schedule A starting from federal Schedule A.",
    "DE additional standard deduction for age 65+ / blind NOT applied "
    "(+$2,500 per condition). v1 always uses the base std ded.",
    "DE additional personal credit for age 65+ NOT applied (+$110 per "
    "qualifying spouse 65+). v1 uses only the base 1-credit (Single) "
    "or 2-credit (MFJ) plus dependents.",
    "DE credits NOT applied (Form 200-01 line 28+): credit for taxes "
    "paid to other states (Schedule I — critical for PA / NJ / MD "
    "commuters), Delaware Earned Income Tax Credit (DE EITC is 4.5% "
    "of federal EITC, refundable, per 30 Del. C. § 1117), child care "
    "credit (50% of federal), historic property credit, volunteer "
    "firefighter credit ($1,000), and the broader business credits.",
    "DE Form 200-02 nonresident return NOT implemented — v1 uses day-"
    "based proration of the resident-basis tax. Real Form 200-02 "
    "applies a DE-source income / total income apportionment ratio.",
    "DE pension/401(k) exclusion NOT applied (DE allows excluding up "
    "to $2,000 of pension income for filers under 60, up to $12,500 "
    "for filers 60+). Critical for retiree filers; v1 does not "
    "differentiate by age.",
    "DE residents working in Philadelphia: Philadelphia city wage "
    "tax is creditable against DE income tax under DE-PA non-"
    "reciprocity treatment, but the Schedule I Pa-resident-credit "
    "computation is not implemented.",
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def de_standard_deduction(filing_status: FilingStatus) -> Decimal:
    """Return the DE Form 200-01 line 18 standard deduction.

    $3,250 Single / HOH / MFS, $6,500 MFJ / QSS. Source: DE Form
    200-01 instructions page 7.
    """
    return DE_TY2025_STD_DED_BY_STATUS.get(
        filing_status, DE_TY2025_STD_DED_SINGLE
    )


def de_personal_credit_count(
    filing_status: FilingStatus, num_dependents: int
) -> int:
    """Return the number of $110 personal credits the filer receives.

    Single / HOH / MFS:  1 + dependents
    MFJ / QSS:           2 + dependents
    Per DE Form 200-01 instructions page 8.
    """
    n = max(0, int(num_dependents))
    if filing_status in (FilingStatus.MFJ, FilingStatus.QSS):
        return 2 + n
    return 1 + n


def de_personal_credit(
    filing_status: FilingStatus, num_dependents: int
) -> Decimal:
    """Return the DE Form 200-01 line 28 personal credit total.

    ``de_personal_credit_count(...) * $110`` per 30 Del. C. § 1110.
    """
    return (
        Decimal(de_personal_credit_count(filing_status, num_dependents))
        * DE_TY2025_PERSONAL_CREDIT_PER_EXEMPTION
    )


def de_tax_from_schedule(taxable_income: Decimal) -> Decimal:
    """DE Form 200-01 line 27 tax via the Tax Rate Schedule.

    All filing statuses use the same brackets in DE — DE does NOT
    double for MFJ. Source: 30 Del. C. § 1102; DE Form 200-01
    "Tax Rate Schedules" appendix.

    Returns Decimal rounded to cents. Zero for non-positive TI.
    """
    return graduated_tax(d(taxable_income), DE_TY2025_BRACKETS)


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DelawarePlugin:
    """State plugin for Delaware — TY2025.

    Hand-rolled Form 200-01 calculation. Tenforty's graph backend is
    correct on the rate schedule and standard deduction but omits
    the $110/exemption personal credit (Form 200-01 line 28), so
    the plugin is hand-rolled to apply it.

    Flow:
        federal_AGI
          -> DE_AGI                                  (v1: same as federal AGI)
          -> DE_AGI - de_standard_deduction
          -> DE_taxable_income
          -> tax via DE Tax Rate Schedule (line 27)
          -> minus de_personal_credit (line 28)
          -> tax after credits (line 31)
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
        # Form 200-01 Line 1/16: federal AGI / DE AGI. v1 = federal AGI.
        federal_agi = d(federal.adjusted_gross_income)
        de_modifications = Decimal("0")
        de_agi = max(Decimal("0"), federal_agi + de_modifications)

        # Line 18: DE standard deduction.
        std_ded = de_standard_deduction(federal.filing_status)

        # Line 26: DE taxable income.
        de_taxable_income = max(Decimal("0"), de_agi - std_ded)

        # Line 27: DE tax via Tax Rate Schedule.
        de_tax_before_credits = de_tax_from_schedule(de_taxable_income)

        # Line 28: DE personal credit ($110 × exemptions).
        personal_credit = de_personal_credit(
            federal.filing_status, federal.num_dependents
        )

        # Line 31: tax after personal credit (floored at 0).
        de_tax_full = max(
            Decimal("0"), de_tax_before_credits - personal_credit
        )

        # Apportion for nonresident / part-year (day-based v1).
        de_tax_apportioned = day_prorate(de_tax_full, days_in_state)

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
            "state_adjusted_gross_income": cents(de_agi),
            "state_standard_deduction": cents(std_ded),
            "state_taxable_income": cents(de_taxable_income),
            "state_tax_before_credits": cents(de_tax_before_credits),
            "state_personal_credit": cents(personal_credit),
            "state_total_tax": de_tax_apportioned,
            "state_total_tax_resident_basis": de_tax_full,
            "apportionment_fraction": apportionment_fraction,
            "starting_point": "federal_agi",
            "de_modifications_applied": de_modifications,
            "v1_limitations": list(DE_V1_LIMITATIONS),
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
        """Split canonical income into DE-source vs non-DE-source.

        Residents: everything is DE-source. Nonresident / part-year:
        prorate each category by ``days_in_state / 365``.

        TODO(de-form-200-02): real DE Form 200-02 sources income via
        Schedule W (DE-source wages) and applies a DE-source / total
        income apportionment ratio.
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
        # TODO(de-pdf): fan-out follow-up — fill DE Form 200-01 (and
        # Schedule I credits, Form 200-02 for nonresidents) using
        # pypdf against DE DOR fillable PDFs.
        return []

    def form_ids(self) -> list[str]:
        return ["DE Form 200-01"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = DelawarePlugin(
    meta=StatePluginMeta(
        code="DE",
        name="Delaware",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://revenue.delaware.gov/",
        # DE Division of Revenue free e-file portal.
        free_efile_url="https://revenue.delaware.gov/file-individual-income-tax/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # Delaware has NO bilateral reciprocity agreements — verified
        # against skill/reference/state-reciprocity.json. DE-PA and
        # DE-NJ are notable non-reciprocity borders for commuters.
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Hand-rolled DE Form 200-01 calc; tenforty graph backend "
            "applies the rate schedule + standard deduction "
            "correctly but omits the $110/exemption personal credit "
            "(line 28), reporting $3,059 vs hand-roll $2,949 on a "
            "$65k Single (see TestTenfortyStillHasGapOnDE). DE has "
            "seven graduated brackets identical for all filing "
            "statuses (DE does NOT double brackets for MFJ): 0% / "
            "2.2% / 3.9% / 4.8% / 5.2% / 5.55% / 6.6%, top rate "
            "begins at $60,000. Standard deduction $3,250 Single / "
            "$6,500 MFJ. Personal credit $110 per exemption per 30 "
            "Del. C. § 1110. Free e-file via DE Division of Revenue "
            "portal. No reciprocity agreements (PA/NJ commuters "
            "must claim Schedule I credit). Source: DE Form 200-01 "
            "Resident Booklet 2025."
        ),
    )
)
