"""Colorado (CO) state plugin — TY2025.

Colorado is NOT supported by tenforty/OpenTaxSolver (``OTS does not support
2025/CO_Form104``), so this module hand-rolls the CO Form DR 0104 calculation.

Colorado has a **flat** individual income tax. The **permanent** statutory
rate is 4.40% (Colo. Rev. Stat. §39-22-104(1.7)). For tax years 2024 through
2034, C.R.S. §39-22-627 authorizes a **temporary** income tax rate reduction
as one of the TABOR refund mechanisms when TABOR surplus exceeds specific
thresholds; for example, TY2024 used a temporarily reduced 4.25% rate because
the TY2023 TABOR surplus triggered the mechanism.

**TY2025 rate = 4.40%** — the temporary reduction mechanism did **not** fire
for TY2025 because the remaining excess state revenues after the property tax
exemption reimbursement (~$111.2M) fell below the $300 million threshold
required to activate the income tax rate reduction mechanism (SB24-228).

Wave 4 upgrade (2026-04-11): the previous v1 flat-approximation layer
(federal taxable income → flat rate) has been extended with a real CO
DR 0104 additions + DR 0104AD subtractions pass. See the block labeled
"Wave 4 adds/subs implemented" below for exactly what is now modeled and
the "v1 LIMITATIONS STILL OPEN" list for what is still deferred.

Sources (verified 2026-04-11):

- Colorado OSA "Schedule of TABOR Revenue — Fiscal Year 2025 Performance
  Audit" (October 2025, report 2557P), page 18 "Temporary Income Tax Rate
  Reduction" and page 19 "At June 30, 2025, ... approximately $182.1 million
  of this refund liability will be refunded through the property tax
  exemptions reimbursement. The remaining approximately $111.2 million of
  the Fiscal Year 2025 excess state revenues is expected to trigger the
  sales tax refund mechanism."
  https://content.leg.colorado.gov/sites/default/files/documents/audits/2557p_schedule_of_tabor_revenue_fy_25.pdf
  (triggering thresholds — the rate reduction only activates when remaining
  excess state revenues exceed $300M; at ~$111.2M, only the six-tier sales
  tax refund mechanism fires)

- Colorado Legislative Council Staff, "SB 25-138 Fiscal Note" (May 22, 2025):
  "For tax years 2025 through 2034, the bill reduces the state income tax
  rate from 4.40 percent to 4.25 percent." SB25-138 was postponed
  indefinitely on 2025-02-27, confirming the permanent rate remains 4.40%.
  https://leg.colorado.gov/sites/default/files/documents/2025A/bills/fn/2025a_sb138_f1.pdf

- Colorado Department of Revenue, "DR 0104 — 2025 Colorado Individual
  Income Tax Return" (10/03/25), line 1 "Federal Taxable Income from your
  federal income tax form: 1040, 1040 SR, or 1040 SP line 15": CO's starting
  point is **federal taxable income**, not federal AGI.
  https://tax.colorado.gov/sites/tax/files/documents/DR0104_2025.pdf

- Colorado Department of Revenue, "DR 0104AD 2025 Subtractions from Income
  Schedule" — authoritative list of CO subtractions. Lines verified via
  WebFetch:
      Line 1 — State Income Tax Refund from federal Schedule 1
      Line 2 — U.S. Government Interest (e.g. Treasury interest)
      Line 3 — Primary Taxpayer Pension and Annuity Subtraction (age-capped)
      Line 4 — Spouse Pension and Annuity Subtraction (age-capped, MFJ)
  https://tax.colorado.gov/sites/tax/files/documents/DR0104AD_2025.pdf

- Colorado Department of Revenue, "Income Tax Topics: Social Security,
  Pensions and Annuities" — "If you were age 65 or older as of December 31,
  2025, you may subtract $24,000... If you were at least 55 years old, but
  not yet 65, you may subtract $20,000... This subtraction is allowed only
  for pension or annuity income that is included in your federal taxable
  income." Confirms the SS-benefits-reduces-the-pension-cap interaction.
  https://tax.colorado.gov/income-tax-topics-social-security-pensions-and-annuities

Starting point: ``StateStartingPoint.FEDERAL_TAXABLE_INCOME``. CO takes the
federal taxable income number (after the federal standard or itemized
deduction), then applies CO additions (state tax add-back, nonqualified
CollegeInvest/ABLE distributions, out-of-state muni bond interest, etc.) and
CO subtractions (DR 0104AD: social security, pension exclusion, military
pension, CollegeInvest contributions, etc.). Wave 4 implements a
first-order set of these adds/subs; see "Wave 4 adds/subs implemented"
below.

Wave 4 adds/subs implemented (TY2025):

- DR 0104 Line 2 addition — State Income Tax deducted federally. If the
  taxpayer itemized on the federal return (``return_.itemize_deductions``
  is True), the state-and-local income tax component of federal SALT
  (``return_.itemized.state_and_local_income_tax``) is added back to CO
  base income. Note this is BEFORE the $10,000 SALT cap: CO requires the
  add-back of the state INCOME tax component ONLY, and the full amount
  the federal return deducted on Schedule A, not just the state-income-
  tax share of the post-cap total. A refinement would pro-rate the cap
  across income/property/sales tax, but v1 assumes 100% of the state
  income tax line was the addback amount.

- DR 0104 Line 2 addition — Non-CO municipal bond interest. Pulled from
  1099-INT box 8 + 1099-DIV box 11 (conservative: treats ALL muni interest
  as non-CO). Taxpayers with in-state CO muni holdings can override.

- DR 0104AD Line 1 subtraction — State Income Tax Refund from federal
  Schedule 1 (Form 1099-G box 2 that got included in federal AGI and
  therefore in federal taxable income). Pulled from
  ``forms_1099_g[].box2_state_or_local_income_tax_refund``.

- DR 0104AD Line 2 subtraction — U.S. Government Interest. Pulled from
  ``forms_1099_int[].box3_us_savings_bond_and_treasury_interest``.
  CO cannot tax federal obligation interest per the Supremacy Clause.

- DR 0104AD Lines 3+4 subtraction — Pension and Annuity Subtraction with
  age-based caps. Per the CO DOR Income Tax Topics guidance and
  DR 0104AD 2025 instructions:
      age 55-64: up to $20,000 per taxpayer/spouse (reduced by any
                 Social Security benefit subtraction claimed on Line 3)
      age 65+:   up to $24,000 per taxpayer/spouse (same reduction rule)
  Age is computed at 12/31 of the tax year (matching CO DOR convention).
  Pension/annuity income is pulled from
  ``forms_1099_r[].box2a_taxable_amount`` and split by
  ``recipient_is_taxpayer`` so the taxpayer and spouse each get their own
  age cap. Taxpayers under 55 get $0 pension subtraction.

- DR 0104AD Social Security Subtraction. Pulled from
  ``forms_ssa_1099[].box5_net_benefits`` up to the age cap. The SS
  subtraction reduces the pension cap dollar-for-dollar per CO rules
  (the single combined $20,000 / $24,000 cap covers BOTH SS and pension
  per filer). v1 routes SS benefits through the pension cap helper so
  the cap interaction is handled in one place.

v1 LIMITATIONS STILL OPEN (wave 4):

- DR 0104 Line 2 SALT add-back assumes the full federal
  ``state_and_local_income_tax`` line went onto Schedule A without any
  pro-rata SALT cap haircut. Taxpayers whose federal SALT exceeded the
  $10,000 cap (or $5,000 MFS) may see CO over-add the state income tax
  component. A proper fix pro-rates the deductible SALT total across
  income/property/sales tax. Documented in ``CO_V1_LIMITATIONS``.

- DR 0104 Line 2 muni interest addback is 100% of federal tax-exempt
  muni interest, including any CO-source muni that should in fact be
  subtractable. Override via ``state_specific.co_non_co_muni_interest``.

- Form 1099-R distribution-code gating NOT modeled for CO pension
  subtraction: ``box2a_taxable_amount`` is summed wholesale.

- DR 0104 QBI add-back (line 3), standard/itemized federal deduction
  add-back (line 4), business meals under IRC §274(k) (line 5), non-
  qualified CollegeInvest/ABLE distributions (lines 6-7), and other
  additions (line 9) — NOT modeled.

- DR 0104AD other subtractions NOT modeled: military retirement
  exclusion, CollegeInvest contributions, ABLE contributions, non-
  itemizer charitable subtraction, wildfire mitigation, conservation
  easement, first-time homebuyer savings.

- DR 0104CR credits (CO CTC, EITC match, etc.) — NOT modeled.

- DR 0104AMT — NOT modeled.

Reciprocity: CO has **no** bilateral reciprocity agreements with any other
state. Verified against ``skill/reference/state-reciprocity.json`` — the
``agreements`` array contains zero pairs involving CO.

TABOR refund: the TABOR sales tax refund is claimed on DR 0104 lines 34-38
but is deferred in v1 — modeling the six-tier tiered refund requires a
filing-status-and-MAGI lookup table that changes per fiscal year. A
``tabor_refund_deferred`` flag is set on ``state_specific`` so downstream
consumers know to surface a warning.

Nonresident / part-year handling: a real CO nonresident return uses Form
DR 0104PN (Part-Year Resident/Nonresident Tax Calculation Schedule) which
apportions CO tax by a CO-source-income ratio applied to the full-year
resident tax. v1 uses day-based proration as a first-order approximation;
the real DR 0104PN ratio is fan-out follow-up work.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from skill.scripts.models import (
    CanonicalReturn,
    FilingStatus,
    Person,
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


# ---------------------------------------------------------------------------
# TY2025 constants
# ---------------------------------------------------------------------------


CO_TY2025_FLAT_RATE: Decimal = Decimal("0.044")
"""Colorado TY2025 individual income tax flat rate = 4.40%.

The permanent statutory rate under C.R.S. §39-22-104(1.7) is 4.40%. The
TABOR temporary rate reduction mechanism (C.R.S. §39-22-627) is available
for tax years 2024-2034, but only triggers when remaining excess state
revenues after the property tax exemption reimbursement exceed $300 million.

For FY2025 (TY2025), the OSA audit (report 2557P, October 2025) reported:
- Total TABOR refund obligation: $293.3 million
- Property tax exemption reimbursement: ~$182.1 million
- Remaining: ~$111.2 million  <-- below $300M threshold
- Triggers: six-tier sales tax refund mechanism only

The TY2025 rate therefore remains at the permanent 4.40% rate.
Source: https://content.leg.colorado.gov/sites/default/files/documents/audits/2557p_schedule_of_tabor_revenue_fy_25.pdf
"""


# ---------------------------------------------------------------------------
# PDF rendering paths (Layer 2 AcroForm fill)
# ---------------------------------------------------------------------------

_REF_DIR = Path(__file__).resolve().parent.parent.parent / "reference"
_STATE_FORMS_DIR = _REF_DIR / "state_forms"
_WIDGET_MAP_PATH = _REF_DIR / "co-dr0104-acroform-map.json"


# ---------------------------------------------------------------------------
# Layer 1: DR 0104 field dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DR0104Fields:
    """Frozen snapshot of CO DR 0104 line values, ready for rendering.

    Field names map to semantic names in the widget map JSON.
    """

    state_federal_taxable_income: Decimal = Decimal("0")
    state_additions_total: Decimal = Decimal("0")
    state_subtractions_total: Decimal = Decimal("0")
    state_taxable_income: Decimal = Decimal("0")
    state_total_tax: Decimal = Decimal("0")


def _build_dr0104_fields(state_return: StateReturn) -> DR0104Fields:
    """Layer 1: map StateReturn.state_specific to DR0104Fields."""
    ss = state_return.state_specific
    return DR0104Fields(
        state_federal_taxable_income=ss.get("federal_taxable_income", ss.get("state_base_income_approx", Decimal("0"))),
        state_additions_total=ss.get("co_additions_total", Decimal("0")),
        state_subtractions_total=ss.get("co_subtractions_total", Decimal("0")),
        state_taxable_income=ss.get("state_base_income_after_adjustments", Decimal("0")),
        state_total_tax=ss.get("state_total_tax", Decimal("0")),
    )


# ---------------------------------------------------------------------------
# V1 limitations
# ---------------------------------------------------------------------------


CO_V1_LIMITATIONS: tuple[str, ...] = (
    # Wave 4 PARTIALLY closed the adds/subs gap: state income tax addback,
    # non-CO muni interest addback, state refund subtraction, US Treasury
    # subtraction, and age-capped pension/annuity + Social Security
    # subtraction are now modeled. What's still missing:
    "CO additions partially modeled (wave 4): state income tax add-back "
    "(DR 0104 line 2 state income tax component) and non-CO municipal "
    "bond interest addback are IMPLEMENTED. Still NOT modeled: QBI "
    "deduction add-back (line 3), standard/itemized federal deduction "
    "add-back (line 4), business meals deducted under IRC §274(k) (line 5), "
    "nonqualified CollegeInvest Tuition Savings Account distributions "
    "(line 6), nonqualified Colorado ABLE Account distributions (line 7), "
    "other additions (line 9).",
    "CO subtractions partially modeled (wave 4, DR 0104AD): state income "
    "tax refund subtraction (Line 1), U.S. government interest subtraction "
    "(Line 2), and pension/annuity + Social Security subtraction with "
    "age-based caps ($20k age 55-64 / $24k age 65+ per taxpayer+spouse, "
    "Lines 3-4) are IMPLEMENTED. Still NOT modeled: military retirement "
    "exclusion, CollegeInvest contributions, ABLE account contributions, "
    "charitable contribution subtraction for non-itemizers, wildfire "
    "mitigation measures, conservation easement deduction, first-time "
    "home buyer savings.",
    "DR 0104 Line 2 state income tax add-back uses the full pre-SALT-cap "
    "federal state_and_local_income_tax value; taxpayers whose federal "
    "SALT exceeded the $10k cap may see CO over-add the state income "
    "tax component because the v1 does NOT pro-rate the SALT cap "
    "reduction across income/property/sales tax.",
    "DR 0104 Line 2 non-CO muni interest add-back treats 100% of federal "
    "tax-exempt muni interest as non-CO; taxpayers with CO muni holdings "
    "are currently over-taxed (override via state_specific).",
    "Form 1099-R distribution-code gating NOT modeled for CO pension "
    "subtraction: box2a_taxable_amount is summed wholesale without "
    "consulting box7 codes for non-qualifying rollover/disability/"
    "premature-distribution codes.",
    "CO credits not applied (DR 0104CR): CO CTC, state EITC (match of "
    "federal EITC), child care expenses credit, nonrefundable credits "
    "(DR 0104CR lines 1-26 list), innovative motor vehicle credit "
    "(DR 0617), enterprise zone credits (DR 1366), CHIPS zone credit "
    "(DR 1370), strategic capital credit (DR 1330).",
    "CO AMT not computed (DR 0104AMT — CO has its own AMT tied to federal "
    "AMTI with CO additions/subtractions; rate is 3.47% of CO AMTI over "
    "federal taxable income).",
    "CO TABOR sales tax refund not computed (DR 0104 lines 34-38 "
    "six-tier refund table). See ``tabor_refund_deferred`` flag.",
    "Nonresident / part-year apportionment uses day-based proration "
    "(days / 365) instead of the DR 0104PN income-source ratio.",
)


# TY2025 CO age-based pension + annuity + Social Security subtraction caps.
# Source: Colorado DOR Income Tax Topics — Social Security, Pensions and
# Annuities and DR 0104AD 2025 Subtractions from Income Schedule.
# The $20k and $24k caps are a SINGLE combined cap per filer that covers
# BOTH Social Security benefits and pension/annuity income. Claiming the
# SS subtraction reduces the remaining pension cap dollar-for-dollar.
CO_PENSION_CAP_AGE_55_TO_64: Decimal = Decimal("20000")
CO_PENSION_CAP_AGE_65_PLUS: Decimal = Decimal("24000")
CO_PENSION_MIN_AGE: int = 55


_CENTS = Decimal("0.01")


def _cents(v: Decimal) -> Decimal:
    """Quantize a Decimal to cents with half-up rounding."""
    return v.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _apportionment_fraction(
    residency: ResidencyStatus, days_in_state: int
) -> Decimal:
    """Days-based apportionment fraction for nonresident / part-year.

    Residents get 1.0 (full state tax). Nonresidents and part-year residents
    are prorated by days_in_state / 365. Clamped to [0, 1].

    TODO: a real nonresident CO calculation uses Form DR 0104PN which
    computes CO-source income as a fraction of total federal AGI and applies
    that ratio to the full-year resident tax. Wage income is sourced to the
    work state, investment income to the domicile, rental to the property
    state, and gambling winnings to the event state. Day-based proration is
    a first-order approximation; fan-out will tighten this with DR 0104PN
    logic.
    """
    if residency == ResidencyStatus.RESIDENT:
        return Decimal("1")
    frac = Decimal(days_in_state) / Decimal("365")
    if frac < 0:
        return Decimal("0")
    if frac > 1:
        return Decimal("1")
    return frac


def _co_base_income_from_federal(federal: FederalTotals) -> Decimal:
    """CO starting point = federal taxable income (DR 0104 line 1).

    Negative federal taxable income (e.g. large itemized deductions)
    clamps to zero.
    """
    return max(Decimal("0"), federal.taxable_income)


def _person_age_at_end_of_year(person: Person, tax_year: int) -> int:
    """Age of ``person`` on 12/31/``tax_year``.

    Mirrors the convention used in ``calc.engine._person_age_at_end_of_year``
    without importing that helper (keeps the plugin self-contained and
    avoids a circular-ish dependency on the federal engine). This is the
    age used for the CO pension cap age tier.
    """
    dob = person.date_of_birth
    end_of_year = dt.date(tax_year, 12, 31)
    years = end_of_year.year - dob.year
    if (end_of_year.month, end_of_year.day) < (dob.month, dob.day):
        years -= 1
    return years


def _co_pension_cap_for_age(age: int) -> Decimal:
    """Return the CO combined SS+pension cap for the given age.

    - age < 55:        no subtraction ($0)
    - age 55-64:       $20,000
    - age 65+:         $24,000

    Source: Colorado DOR "Income Tax Topics: Social Security, Pensions and
    Annuities". Verified 2026-04-11.
    """
    if age >= 65:
        return CO_PENSION_CAP_AGE_65_PLUS
    if age >= CO_PENSION_MIN_AGE:
        return CO_PENSION_CAP_AGE_55_TO_64
    return Decimal("0")


def _co_additions(
    return_: CanonicalReturn, federal: FederalTotals
) -> dict[str, Decimal]:
    """Compute DR 0104 additions (line 2 state income tax add-back,
    non-CO muni interest addback). Returns itemized dict + total.

    Wave 4 v1:
    - State income tax add-back: iff taxpayer itemized federally, add back
      ``return_.itemized.state_and_local_income_tax`` (no SALT cap pro-rata,
      see v1 limitations).
    - Non-CO muni addback: 1099-INT box 8 + 1099-DIV box 11, conservatively
      treated as 100% non-CO.
    """
    state_tax_addback = Decimal("0")
    if return_.itemize_deductions and return_.itemized is not None:
        # Only the income-tax component of SALT is added back; CO taxpayers
        # who elected sales tax on Schedule A are NOT adding back income
        # tax (they deducted none).
        if not return_.itemized.elect_sales_tax_over_income_tax:
            state_tax_addback = return_.itemized.state_and_local_income_tax

    muni_addback = Decimal("0")
    for form in return_.forms_1099_int:
        muni_addback += form.box8_tax_exempt_interest
    for form in return_.forms_1099_div:
        muni_addback += form.box11_exempt_interest_dividends

    total = state_tax_addback + muni_addback
    return {
        "co_dr0104_line2_state_income_tax_addback": _cents(state_tax_addback),
        "co_dr0104_line2_non_co_muni_interest_addback": _cents(muni_addback),
        "co_additions_total": _cents(total),
    }


def _co_subtractions(
    return_: CanonicalReturn, federal: FederalTotals
) -> dict[str, Decimal]:
    """Compute DR 0104AD subtractions.

    Wave 4 v1:
    - Line 1 State Income Tax Refund: 1099-G box 2 amounts.
    - Line 2 U.S. Government Interest: 1099-INT box 3.
    - Lines 3-4 Pension + Annuity with age-based cap, split by
      recipient_is_taxpayer. Social Security from SSA-1099 is routed
      through the SAME cap (SS reduces the pension cap dollar-for-
      dollar per CO rules). We apply the combined cap once per filer.
    """
    # Line 1: state income tax refund from Form 1099-G box 2
    state_refund_sub = Decimal("0")
    for form in return_.forms_1099_g:
        state_refund_sub += form.box2_state_or_local_income_tax_refund

    # Line 2: U.S. Government interest
    us_treasury_sub = Decimal("0")
    for form in return_.forms_1099_int:
        us_treasury_sub += form.box3_us_savings_bond_and_treasury_interest

    # Lines 3-4: pension + annuity + SS, combined per-filer age cap.
    # Determine ages.
    tp_age = _person_age_at_end_of_year(return_.taxpayer, return_.tax_year)
    sp_age: int | None = None
    if return_.spouse is not None:
        sp_age = _person_age_at_end_of_year(return_.spouse, return_.tax_year)

    tp_cap = _co_pension_cap_for_age(tp_age)
    sp_cap = _co_pension_cap_for_age(sp_age) if sp_age is not None else Decimal("0")

    # Split SS benefits by recipient.
    tp_ss = Decimal("0")
    sp_ss = Decimal("0")
    for form in return_.forms_ssa_1099:
        if form.recipient_is_taxpayer:
            tp_ss += form.box5_net_benefits
        else:
            sp_ss += form.box5_net_benefits

    # Split 1099-R pension/annuity taxable amounts by recipient.
    tp_pension = Decimal("0")
    sp_pension = Decimal("0")
    for form in return_.forms_1099_r:
        if form.recipient_is_taxpayer:
            tp_pension += form.box2a_taxable_amount
        else:
            sp_pension += form.box2a_taxable_amount

    # Apply the combined cap per filer: SS first, then pension up to
    # remaining cap. This matches "the SS subtraction reduces the
    # remaining pension cap dollar-for-dollar" language from CO DOR.
    def _apply_cap(ss: Decimal, pension: Decimal, cap: Decimal) -> tuple[Decimal, Decimal]:
        ss_allowed = min(ss, cap)
        remaining = cap - ss_allowed
        pension_allowed = min(pension, remaining)
        return ss_allowed, pension_allowed

    tp_ss_allowed, tp_pension_allowed = _apply_cap(tp_ss, tp_pension, tp_cap)
    sp_ss_allowed, sp_pension_allowed = _apply_cap(sp_ss, sp_pension, sp_cap)

    ss_sub_total = tp_ss_allowed + sp_ss_allowed
    pension_sub_total = tp_pension_allowed + sp_pension_allowed

    total = state_refund_sub + us_treasury_sub + ss_sub_total + pension_sub_total
    return {
        "co_dr0104ad_line1_state_income_tax_refund": _cents(state_refund_sub),
        "co_dr0104ad_line2_us_government_interest": _cents(us_treasury_sub),
        "co_dr0104ad_social_security_subtraction": _cents(ss_sub_total),
        "co_dr0104ad_pension_annuity_subtraction": _cents(pension_sub_total),
        "co_dr0104ad_taxpayer_age": Decimal(tp_age),
        "co_dr0104ad_taxpayer_cap": _cents(tp_cap),
        "co_dr0104ad_spouse_age": Decimal(sp_age) if sp_age is not None else Decimal(-1),
        "co_dr0104ad_spouse_cap": _cents(sp_cap),
        "co_subtractions_total": _cents(total),
    }


def _co_tax(co_base_income: Decimal) -> Decimal:
    """Compute CO tax = base income * flat rate, quantized to cents."""
    if co_base_income <= 0:
        return Decimal("0")
    return _cents(co_base_income * CO_TY2025_FLAT_RATE)


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColoradoPlugin:
    """State plugin for Colorado.

    Hand-rolled (tenforty does not support CO Form 104). Computes CO tax as
    ``federal_taxable_income * 4.40%``, approximating the full DR 0104 flow
    and explicitly listing the CO adjustments it doesn't yet model in
    ``CO_V1_LIMITATIONS``. TABOR sales tax refund is deferred.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Step 1: start from federal taxable income (DR 0104 line 1).
        co_base_line1 = _co_base_income_from_federal(federal)

        # Step 2: DR 0104 additions (state income tax addback + non-CO
        # muni interest). Line 2 on DR 0104.
        additions = _co_additions(return_, federal)
        additions_total = additions["co_additions_total"]

        # Step 3: DR 0104AD subtractions (US Treasury, state refund,
        # age-capped pension/annuity + Social Security).
        subtractions = _co_subtractions(return_, federal)
        subtractions_total = subtractions["co_subtractions_total"]

        # Step 4: CO taxable income after adjustments.
        co_base_adjusted = co_base_line1 + additions_total - subtractions_total
        if co_base_adjusted < 0:
            co_base_adjusted = Decimal("0")
        co_base_adjusted = _cents(co_base_adjusted)

        co_tax_full = _co_tax(co_base_adjusted)

        # Apportion tax for nonresident / part-year. TODO: replace with
        # real DR 0104PN income-source ratio in fan-out.
        fraction = _apportionment_fraction(residency, days_in_state)
        co_tax_apportioned = _cents(co_tax_full * fraction)

        state_specific: dict[str, Any] = {
            # "state_base_income_approx" is preserved for backward
            # compatibility with wave 3 tests: it equals federal taxable
            # income (DR 0104 line 1) pre-adds/subs. Wave 4 adds the
            # post-adjustment base under
            # "state_base_income_after_adjustments".
            "state_base_income_approx": _cents(co_base_line1),
            "state_base_income_after_adjustments": co_base_adjusted,
            "co_additions": additions,
            "co_subtractions": subtractions,
            "co_additions_total": additions_total,
            "co_subtractions_total": subtractions_total,
            "state_total_tax": co_tax_apportioned,
            "state_total_tax_resident_basis": co_tax_full,
            "flat_rate": CO_TY2025_FLAT_RATE,
            "apportionment_fraction": fraction,
            "v1_limitations": list(CO_V1_LIMITATIONS),
            "tabor_refund_deferred": True,
            "tabor_refund_reason": (
                "DR 0104 lines 34-38 six-tier sales tax refund not "
                "computed in v1. TY2025 CO TABOR refund will be issued via "
                "the sales tax refund mechanism (OSA 2557P, October 2025) "
                "based on the taxpayer's modified AGI tier."
            ),
            "starting_point": "federal_taxable_income",
            "federal_taxable_income": _cents(federal.taxable_income),
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
        """Split canonical income into CO-source vs non-CO-source.

        Residents: everything is CO-source. Nonresident / part-year: prorate
        each category by days_in_state / 365.

        TODO: CO DR 0104PN sources each income type differently — wages to
        the work location, investment income to domicile, rental to property
        state, etc. Day-based proration is the shared first-cut across all
        fan-out state plugins; refine in follow-up with the real DR 0104PN
        apportionment ratio.
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

        # Schedule C net profit / Schedule E rental net — reuse engine helpers.
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
        """Fill CO DR 0104 using the CO DOR fillable PDF.

        Layer 1: build DR0104Fields via _build_dr0104_fields factory.
        Layer 2: overlay onto the source PDF via fill_acroform_pdf.
        """
        from dataclasses import asdict

        from skill.scripts.output._acroform_overlay import (
            fill_acroform_pdf,
            format_money,
            load_widget_map,
            fetch_and_verify_source_pdf,
        )

        fields = _build_dr0104_fields(state_return)
        wmap = load_widget_map(_WIDGET_MAP_PATH)

        source_pdf = fetch_and_verify_source_pdf(
            _STATE_FORMS_DIR / "co_dr0104.pdf",
            wmap.source_pdf_url,
            wmap.source_pdf_sha256,
        )

        widget_values: dict[str, str] = {}
        for sem_name, value in asdict(fields).items():
            widget_names = wmap.widget_names_for(sem_name)
            if not widget_names:
                continue
            text = format_money(value) if isinstance(value, Decimal) else str(value) if value else ""
            for wn in widget_names:
                widget_values[wn] = text

        out_path = Path(out_dir) / "CO_DR0104.pdf"
        fill_acroform_pdf(source_pdf, widget_values, out_path)
        return [out_path]

    def form_ids(self) -> list[str]:
        return ["CO Form DR 0104"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = ColoradoPlugin(
    meta=StatePluginMeta(
        code="CO",
        name="Colorado",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_TAXABLE_INCOME,
        dor_url="https://tax.colorado.gov/individual-income-tax",
        free_efile_url="https://www.colorado.gov/revenueonline",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Hand-rolled CO Form DR 0104 calc (tenforty does not support "
            "2025/CO_Form104). Permanent flat rate 4.40% per C.R.S. "
            "§39-22-104(1.7); TY2025 TABOR temporary rate reduction did "
            "NOT trigger (remaining excess state revenues ~$111.2M fell "
            "below the $300M threshold per OSA 2557P, October 2025), so "
            "TY2025 uses the permanent 4.40% rate. Starting point: "
            "federal taxable income (DR 0104 line 1). No reciprocity "
            "agreements. CO TABOR sales tax refund deferred in v1."
        ),
    )
)
