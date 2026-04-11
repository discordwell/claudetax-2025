"""Oklahoma (OK) state plugin — TY2025.

See skill/reference/tenforty-ty2025-gap.md for the TY2025 probe rubric
and why OK is hand-rolled instead of graph-wrapped (graph backend
omits the OK $1,000 personal exemption — material mismatch > $5).

Hand-rolled Form 511 calculation. Tenforty's default OTS backend does
NOT register OK_511 (``ValueError: OTS does not support 2025/OK_511``),
and tenforty's *graph* backend has a real correctness gap for OK at
TY2025 (see "Graph-backend mismatch" below). This plugin therefore
hand-rolls the OK Form 511 graduated-bracket calc against the official
2025 Oklahoma Resident Individual Income Tax Forms and Instructions
booklet (Packet 511) and the 2025 Oklahoma Income Tax Withholding
Tables (Packet OW-2 Revised 11-2024). It mirrors the wave-4 KS / CT /
KY / MN hand-rolled pattern.

Graph-backend mismatch (the reason this is hand-rolled, not wrapped)
---------------------------------------------------------------------
Probe (verified 2026-04-11 against tenforty installed in .venv):
    Single / $65,000 W-2 / Standard
        graph -> state_total_tax              = 2597.38
                 state_taxable_income         = 58650.00
                 state_adjusted_gross_income  = 65000.00
        default -> ValueError: OTS does not support 2025/OK_511

Hand calc against the OK 2025 Form 511 / OW-2 schedules:
    OK AGI                = federal AGI = $65,000
    OK standard deduction = $6,350                 (Single, OK 511 line)
    OK personal exemption = $1,000                 ($1,000 per
                                                    exemption claimed,
                                                    OK 511 instructions)
    OK taxable income     = 65,000 - 6,350 - 1,000 = $57,650
    Tax via Single bracket schedule (Form 511 / OW-2 Annual table):
        $0       - $1,000     0.25%   ->     2.50
        $1,000   - $2,500     0.75%   ->    11.25
        $2,500   - $3,750     1.75%   ->    21.875
        $3,750   - $4,900     2.75%   ->    31.625
        $4,900   - $7,200     3.75%   ->    86.25
        $7,200+               4.75%   -> (57,650 - 7,200) * 0.0475
                                       =  50,450 * 0.0475
                                       =  2,396.375
        Total                          =  2,549.875
        Quantized to cents             =  $2,549.88

The graph backend's $2,597.38 is **exactly $47.50 high**, which equals
$1,000 (the OK personal exemption) * 4.75% (the OK top marginal rate).
The graph backend correctly applies the $6,350 standard deduction
(state_taxable_income = $58,650 = 65,000 - 6,350) but **omits the
$1,000 personal exemption**. This is the same class of bug as IL and
KS in the wave-4 audit (graph backend forgets a state-specific
exemption).

Per the gap-doc rubric: **HAND-ROLL** (mismatch > $5). The
``TestTenfortyStillHasGapOnOK`` gatekeeper test pins the graph
backend's wrong number so the day OK lands an upstream fix, CI fails
and we re-evaluate.

Confirmation: the OW-2 2025 withholding tables (Packet OW-2 Revised
11-2024, page 9, Annual Payroll Period table) explicitly bake the
$6,350 std ded into the bracket starting points (Single brackets begin
at $6,350) AND apply a $1,000-per-allowance withholding allowance
(table on page 6). Both line up with our hand calc: tax on $57,650 of
"net wages" via the Annual Single percentage table is $153.50 + 4.75%
* (57,650 - 13,550) = $153.50 + 4.75% * 44,100 = $153.50 + $2,094.75
= $2,248.25 ... wait, that's the on-table number for net wages of
$57,650. The Annual table's brackets are *gross-of-deduction* at
$6,350-$7,350 etc., so the table operates on (gross wages -
allowances). Gross wages 65,000 - 1 allowance ($1,000) = 64,000 net,
falls in the $13,550+ Single row: $153.50 + 4.75% * (64,000 - 13,550)
= $153.50 + 4.75% * 50,450 = $153.50 + 2,396.375 = $2,549.875. **This
matches our Form 511 hand calc to the cent.** The OW-2 withholding
tables are an alternate derivation of the same arithmetic, and they
confirm the personal exemption is alive and well in OK TY2025.

LOUDLY FLAGGED RECENT LAW CHANGE (forward-looking)
--------------------------------------------------
Oklahoma **HB 2764 of 2025**, signed in 2025, will reduce the OK top
marginal rate from 4.75% to **4.50%** AND consolidate the current
six-bracket schedule into **three brackets**, **effective for tax year
2026**. This change does NOT affect TY2025 — the TY2025 plugin is
unaffected — but a wave-N TY2026 update will need to:

1. Replace ``OK_TY2025_BRACKETS_SINGLE`` / ``..._MFJ`` with the new
   three-bracket schedules.
2. Update ``OK_TY2025_TOP_RATE`` constant.
3. Re-verify the standard deduction and personal exemption amounts
   against the 2026 Form 511 instructions.
4. Re-run the hand-calc lock against TY2026 inputs.

Sources verified 2026-04-11:
    - 2025 Oklahoma Resident Individual Income Tax Forms and
      Instructions, Form 511 Packet, Oklahoma Tax Commission
      https://oklahoma.gov/content/dam/ok/en/tax/documents/forms/individuals/current/511-Pkt.pdf
    - Packet OW-2 Revised 11-2024, "2025 Oklahoma Income Tax
      Withholding Tables", Oklahoma Tax Commission
      https://oklahoma.gov/content/dam/ok/en/tax/documents/resources/publications/businesses/withholding-tables/WHTables-2025.pdf
      (Annual Payroll Period table on page 9 confirms the bracket
      schedule and the $1,000 personal allowance)
    - Oklahoma Tax Commission, Income Tax landing page
      https://oklahoma.gov/tax/individuals.html
    - HB 2764, 2025 Regular Session — TY2026 rate reduction and
      bracket consolidation

OK Form 511 line layout (resident calc)
----------------------------------------
    Line 1:  Federal AGI (from federal 1040)
    Line 2:  Subtractions (Schedule 511-A)
    Line 3:  Out-of-state income (subtraction)
    Line 4:  Additions (Schedule 511-B)
    Line 5:  OK adjusted gross income = Line 1 - Lines 2,3 + Line 4
    Line 6:  Adjustments (Schedule 511-C)
    Line 7:  OK AGI after adjustments = Line 5 - Line 6
    Line 8:  Itemized OR standard deduction
    Line 9:  Exemptions (count * $1,000)
    Line 10: Dependents (count * $1,000)
    Line 11: OK taxable income = Line 7 - Line 8 - Line 9 - Line 10
    Line 12: Tax (from tax table or rate schedule)

v0.1 approximates Lines 2-6 as zero (OK AGI = federal AGI). The list
of unapplied additions/subtractions is enumerated in
``OK_V1_LIMITATIONS``.

Oklahoma standard deductions (Form 511 instructions, 2025)
-----------------------------------------------------------
    Single / MFS:           $6,350
    MFJ / QSS:             $12,700
    Head of Household:      $9,350

Oklahoma personal/dependent exemptions: $1,000 per exemption claimed
(Form 511 instructions, line 9 / line 10). The OK personal exemption
phaseout was REPEALED by HB 1004x (2017); v0.1 applies the full $1,000
per exemption regardless of income.

Oklahoma TY2025 brackets (Form 511 / Packet OW-2 page 9 Annual table)
----------------------------------------------------------------------
Single / HOH / MFS — applied to OK taxable income (after std ded and
exemptions):

      $0     - $1,000     0.25%
      $1,000 - $2,500     0.75%
      $2,500 - $3,750     1.75%
      $3,750 - $4,900     2.75%
      $4,900 - $7,200     3.75%
      over $7,200         4.75%

Married Filing Jointly / QSS — exactly 2x the Single bracket widths:

      $0      - $2,000     0.25%
      $2,000  - $5,000     0.75%
      $5,000  - $7,500     1.75%
      $7,500  - $9,800     2.75%
      $9,800  - $14,400    3.75%
      over $14,400         4.75%

  Cross-checked against the Packet OW-2 (Revised 11-2024) Annual
  Payroll Period table for MFJ (which is the schedule + the $12,700
  std ded), which prints the cumulative-tax constant $307.00 at the
  start of the top bracket. With 2,000*0.0025 + 3,000*0.0075 +
  2,500*0.0175 + 2,300*0.0275 + 4,600*0.0375 = 307.00, the MFJ
  bracket widths are exactly 2x the Single widths and the cumulative
  tax matches OW-2 to the cent.

Reciprocity
-----------
Oklahoma has **NO** bilateral reciprocity agreements with any state
(verified against ``skill/reference/state-reciprocity.json``: OK does
not appear in the ``agreements`` array). OK residents who work in
another state file nonresident there and claim the OK "Credit for
income tax paid to another state" on Form 511 line / Form 511CR.

Submission channel
------------------
Oklahoma operates a free direct-entry portal, **OkTAP (Oklahoma
Taxpayer Access Point)**, at https://oktap.tax.ok.gov/. The state
also participates in the IRS Fed/State MeF program. The canonical
channel for this plugin is ``SubmissionChannel.STATE_DOR_FREE_PORTAL``
(the free OkTAP path).

Nonresident / part-year
-----------------------
Oklahoma's real nonresident / part-year treatment uses **Form 511-NR**
with OK-source income sourcing on the percentage method. v0.1 falls
back to day-based proration of the resident-basis tax — the same
first-cut every fan-out state plugin uses. ``TODO(ok-form-511-nr)``
tracks the real treatment.

Form IDs
--------
- OK Form 511 (Resident Individual Income Tax Return)
- OK Form 511-NR (Nonresident / Part-Year — fan-out follow-up)
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


# Canonical wave-5 $65k Single gatekeeper lock. Hand-traced from OK
# Form 511 — see module docstring. Referenced from test_state_ok.py.
LOCK_VALUE: Final[Decimal] = Decimal("2549.88")


# ---------------------------------------------------------------------------
# TY2025 constants — verified from OK Form 511 Packet and OW-2 (Annual)
# ---------------------------------------------------------------------------


# Standard deductions (OK Form 511 instructions 2025)
OK_TY2025_STD_DED_SINGLE: Decimal = Decimal("6350")
OK_TY2025_STD_DED_MFJ: Decimal = Decimal("12700")
OK_TY2025_STD_DED_HOH: Decimal = Decimal("9350")
OK_TY2025_STD_DED_MFS: Decimal = Decimal("6350")

OK_TY2025_STD_DED_BY_STATUS: dict[FilingStatus, Decimal] = {
    FilingStatus.SINGLE: OK_TY2025_STD_DED_SINGLE,
    FilingStatus.MFJ: OK_TY2025_STD_DED_MFJ,
    FilingStatus.QSS: OK_TY2025_STD_DED_MFJ,  # QSS uses MFJ amount
    FilingStatus.HOH: OK_TY2025_STD_DED_HOH,
    FilingStatus.MFS: OK_TY2025_STD_DED_MFS,
}

# Personal exemption (OK Form 511 instructions, line 9)
OK_TY2025_PERSONAL_EXEMPTION: Decimal = Decimal("1000")
"""$1,000 per exemption claimed. Single = 1, MFJ = 2, plus 1 per
dependent (line 10). The OK personal exemption phaseout was repealed
by HB 1004x (2017)."""

OK_TY2025_TOP_RATE: Decimal = Decimal("0.0475")
"""OK TY2025 top marginal rate = 4.75%. Reduced to 4.50% effective
TY2026 by HB 2764 (2025)."""

# Single / HOH / MFS bracket schedule (Form 511 / OW-2 page 9). Each
# bracket applies its rate to the portion of taxable income in
# [low, high). The top bracket has high=None (open-ended).
OK_TY2025_BRACKETS_SINGLE: tuple[GraduatedBracket, ...] = (
    GraduatedBracket(
        low=Decimal("0"), high=Decimal("1000"), rate=Decimal("0.0025")
    ),
    GraduatedBracket(
        low=Decimal("1000"), high=Decimal("2500"), rate=Decimal("0.0075")
    ),
    GraduatedBracket(
        low=Decimal("2500"), high=Decimal("3750"), rate=Decimal("0.0175")
    ),
    GraduatedBracket(
        low=Decimal("3750"), high=Decimal("4900"), rate=Decimal("0.0275")
    ),
    GraduatedBracket(
        low=Decimal("4900"), high=Decimal("7200"), rate=Decimal("0.0375")
    ),
    GraduatedBracket(
        low=Decimal("7200"), high=None, rate=Decimal("0.0475")
    ),
)

# MFJ / QSS bracket schedule — exactly 2x the Single bracket widths.
# Verified against OW-2 (Revised 11-2024) page 9 Annual Period MFJ
# table. The Annual table starts at $12,700 (the MFJ std ded); these
# brackets are the MFJ schedule with the std ded subtracted.
OK_TY2025_BRACKETS_MFJ: tuple[GraduatedBracket, ...] = (
    GraduatedBracket(
        low=Decimal("0"), high=Decimal("2000"), rate=Decimal("0.0025")
    ),
    GraduatedBracket(
        low=Decimal("2000"), high=Decimal("5000"), rate=Decimal("0.0075")
    ),
    GraduatedBracket(
        low=Decimal("5000"), high=Decimal("7500"), rate=Decimal("0.0175")
    ),
    GraduatedBracket(
        low=Decimal("7500"), high=Decimal("9800"), rate=Decimal("0.0275")
    ),
    GraduatedBracket(
        low=Decimal("9800"), high=Decimal("14400"), rate=Decimal("0.0375")
    ),
    GraduatedBracket(
        low=Decimal("14400"), high=None, rate=Decimal("0.0475")
    ),
)


OK_V1_LIMITATIONS: tuple[str, ...] = (
    "OK Schedule 511-A subtractions NOT applied: interest on US "
    "government obligations, Social Security benefits taxed federally "
    "(OK exempts SS), federal civil service retirement, OK military "
    "retirement (100% exempt), OK railroad retirement, qualified "
    "adoption expenses, OK lottery winnings deduction, etc.",
    "OK Schedule 511-B additions NOT applied: state/municipal bond "
    "interest from non-OK sources, lump-sum distributions, federal "
    "NOL adjustments, etc.",
    "OK Schedule 511-C adjustments NOT applied: military pay "
    "exclusion, qualified medical savings account contributions, "
    "qualified educational savings 529 contributions (OK 529 plan), "
    "Oklahoma capital gain deduction (OK source assets held 5+ "
    "years), etc.",
    "Credit for income tax paid to another state (OK Form 511CR / "
    "Form 511 line credit) NOT applied — critical for multi-state "
    "filers (OK residents working in TX/AR/MO/KS/NM/CO).",
    "OK Earned Income Credit (5% of federal EIC, refundable since "
    "TY2022) NOT applied.",
    "OK Sales Tax Relief Credit (refundable, household income < "
    "$50,000) NOT applied.",
    "OK Child Care / Child Tax Credit (% of federal CTC for low "
    "income) NOT applied.",
    "OK additional standard deduction for age 65+ / blind NOT "
    "applied. (OK does not have a separate age-based add-on like "
    "the federal return — confirm against 2025 Form 511 instructions.)",
    "OK itemized deductions (Schedule 511-D) NOT supported in v1 — "
    "v1 always takes the OK standard deduction. Federal itemizers "
    "should still typically itemize on OK using Schedule 511-D.",
    "Nonresident / part-year apportionment uses day-based proration "
    "(days / 365) instead of OK Form 511-NR percentage-method "
    "sourcing (OK-source AGI / total AGI).",
    "Use Tax (OK consumer use tax) NOT modeled — separate line on "
    "Form 511.",
    "OK does NOT have a separate state AMT — non-limitation, noted "
    "for completeness.",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ok_standard_deduction(filing_status: FilingStatus) -> Decimal:
    """Return the OK TY2025 standard deduction for the given filing status."""
    return OK_TY2025_STD_DED_BY_STATUS.get(
        filing_status, OK_TY2025_STD_DED_SINGLE
    )


def ok_exemption_allowance(
    filing_status: FilingStatus, num_dependents: int
) -> Decimal:
    """Return the OK TY2025 exemption allowance.

    $1,000 per exemption (Form 511 line 9) plus $1,000 per dependent
    (Form 511 line 10). MFJ/QSS get 2 base exemptions; everyone else
    gets 1.
    """
    base_count = 2 if filing_status in (FilingStatus.MFJ, FilingStatus.QSS) else 1
    deps = max(0, num_dependents)
    return OK_TY2025_PERSONAL_EXEMPTION * Decimal(base_count + deps)


def _brackets_for(filing_status: FilingStatus) -> tuple[GraduatedBracket, ...]:
    """Pick the right bracket schedule for the filing status.

    MFJ and QSS use the wider MFJ schedule; everyone else uses Single.
    Per Form 511 instructions, HOH and MFS use the Single schedule (the
    Single bracket widths are not adjusted for HOH).
    """
    if filing_status in (FilingStatus.MFJ, FilingStatus.QSS):
        return OK_TY2025_BRACKETS_MFJ
    return OK_TY2025_BRACKETS_SINGLE


def ok_tax_from_brackets(
    taxable_income: Decimal, filing_status: FilingStatus
) -> Decimal:
    """Compute OK tax from the TY2025 graduated bracket schedule.

    Returns Decimal quantized to cents. Zero for non-positive taxable
    income (no tax on a loss).
    """
    return graduated_tax(taxable_income, _brackets_for(filing_status))


def _apportionment_fraction(
    residency: ResidencyStatus, days_in_state: int
) -> Decimal:
    """Days-based apportionment fraction for nonresident / part-year.

    TODO(ok-form-511-nr): replace with OK Form 511-NR percentage-method
    income-source apportionment.
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
class OklahomaPlugin:
    """State plugin for Oklahoma — TY2025.

    Hand-rolled OK Form 511 calc. Tenforty does not support 2025/OK_511
    on the OTS backend, and the graph backend has a real correctness
    gap (omits the $1,000 personal exemption). See module docstring.

    Flow:
        federal_AGI
          -> OK_AGI                       (v1: same as federal AGI)
          -> OK_AGI - std_ded - exemptions
          -> OK_taxable_income
          -> tax via graduated bracket schedule
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
        # Form 511 Line 1: federal AGI.
        federal_agi = federal.adjusted_gross_income
        # Lines 2-6: subtractions/additions/adjustments. v1 = 0.
        ok_modifications = Decimal("0")
        # Line 7: OK AGI after adjustments.
        ok_agi = max(Decimal("0"), federal_agi + ok_modifications)
        # Line 8: standard deduction.
        std_ded = ok_standard_deduction(federal.filing_status)
        # Lines 9 + 10: exemptions (personal + dependents).
        exemption = ok_exemption_allowance(
            federal.filing_status, federal.num_dependents
        )
        # Line 11: OK taxable income.
        ok_taxable_income = max(Decimal("0"), ok_agi - std_ded - exemption)
        # Line 12: tax from bracket schedule.
        ok_tax_full = ok_tax_from_brackets(
            ok_taxable_income, federal.filing_status
        )

        # Apportion for nonresident / part-year (day-based v1).
        fraction = _apportionment_fraction(residency, days_in_state)
        ok_tax_apportioned = cents(ok_tax_full * fraction)

        state_specific: dict[str, Any] = {
            "state_federal_agi": cents(federal_agi),
            "state_adjusted_gross_income": cents(ok_agi),
            "state_standard_deduction": cents(std_ded),
            "state_exemption_allowance": cents(exemption),
            "state_total_deductions": cents(std_ded + exemption),
            "state_taxable_income": cents(ok_taxable_income),
            "state_total_tax": ok_tax_apportioned,
            "state_total_tax_resident_basis": ok_tax_full,
            "state_top_rate": OK_TY2025_TOP_RATE,
            "apportionment_fraction": fraction,
            "starting_point": "federal_agi",
            "ok_modifications_applied": ok_modifications,
            "v1_limitations": list(OK_V1_LIMITATIONS),
            "ok_personal_exemption_per_filer": OK_TY2025_PERSONAL_EXEMPTION,
            "ok_hb2764_ty2026_note": (
                "Oklahoma HB 2764 (2025) reduces the top marginal rate "
                "from 4.75% to 4.50% and consolidates 6 brackets into "
                "3, effective tax year 2026. TY2025 (this plugin) is "
                "unaffected."
            ),
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
        """Split canonical income into OK-source vs non-OK-source.

        TODO(ok-form-511-nr): real per-category sourcing on Form 511-NR.
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
            state_source_wages=sourced_or_prorated_wages(return_, "OK", wages, days),
            state_source_interest=day_prorate(interest, days),
            state_source_dividends=day_prorate(ord_div, days),
            state_source_capital_gains=day_prorate(capital_gains, days),
            state_source_self_employment=sourced_or_prorated_schedule_c(return_, "OK", se_net, days),
            state_source_rental=day_prorate(rental_net, days),
        )

    def render_pdfs(
        self, state_return: StateReturn, out_dir: Path
    ) -> list[Path]:
        # TODO(ok-pdf): fan-out follow-up — fill OK Form 511 (and
        # 511-NR for nonresidents, Schedules 511-A/B/C/D for adds/subs)
        # using pypdf against the OK Tax Commission's fillable PDFs.
        return []

    def form_ids(self) -> list[str]:
        return ["OK Form 511"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = OklahomaPlugin(
    meta=StatePluginMeta(
        code="OK",
        name="Oklahoma",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://oklahoma.gov/tax/individuals.html",
        # OkTAP (Oklahoma Taxpayer Access Point) — the OK Tax
        # Commission's free direct-entry portal.
        free_efile_url="https://oktap.tax.ok.gov/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # OK has NO bilateral reciprocity agreements — verified
        # against skill/reference/state-reciprocity.json (OK does not
        # appear in `agreements`).
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Hand-rolled OK Form 511 calc. Tenforty does not support "
            "OK_511 on the OTS backend, and the GRAPH backend has a "
            "$47.50 over-tax bug at TY2025 (omits the $1,000 OK "
            "personal exemption per filer). Six-bracket graduated "
            "schedule from 0.25% on the first $1,000 of taxable "
            "income to 4.75% above $7,200 (Single) / $15,000 (MFJ). "
            "Standard deduction $6,350 Single / $12,700 MFJ / $9,350 "
            "HOH; $1,000 personal exemption per filer plus $1,000 "
            "per dependent. RECENT LAW: HB 2764 (2025) cuts the top "
            "rate to 4.50% and consolidates to 3 brackets effective "
            "TY2026 — TY2025 unaffected. Free e-file via OkTAP. No "
            "reciprocity. Source: OK Tax Commission Form 511 Packet "
            "and Packet OW-2 (2025 Withholding Tables, Annual)."
        ),
    )
)
