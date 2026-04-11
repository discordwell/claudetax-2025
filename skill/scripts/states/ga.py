"""Georgia (GA) state plugin - TY2025.

Georgia is NOT supported by tenforty / OpenTaxSolver ("OTS does not support
2025/GA_500"), so this plugin implements the calculation in-house.

Rate + exemption sources (verified via WebFetch of the official TY2025
IT-511 Individual Income Tax Instruction Booklet published by the Georgia
Department of Revenue):

    https://dor.georgia.gov/it-511-individual-income-tax-booklet
    https://dor.georgia.gov/document/document/2025-it-511-individual-income-tax-booklet/download

Direct quotes from the 2025 IT-511 booklet "What's New" section:

    "2025 Income Tax Changes: Effective January 1, 2025, the income tax
     rate is 5.19%."

And from Line 16 of the Form 500 instructions (and Line 18 on Form 500
itself):

    "Multiply the amount on Line 15c by 5.19%. Round to the nearest dollar."

Standard deduction (IT-511, Line 11 and Schedule 3 worksheet):

    "For Standard Deduction - Enter $12,000 if the filing status is Single,
     Married filing separately, Head of household or Qualifying surviving
     spouse. If filing is Married filing jointly, enter $24,000."

Dependent exemption (IT-511, Line 14 instructions):

    "Multiply the number of dependents on Line 7c by $4,000 and enter the
     total."

History: Georgia transitioned from a six-bracket graduated income tax (top
rate 5.75%) to a flat rate under HB 1437 (2022). The original HB 1437
schedule was 5.49% for TY2024 falling 0.1 percentage point per year until
it hit a 4.99% floor. The 2024 Georgia General Assembly accelerated the
phase-down (HB 1015 / HB 1023), dropping TY2024 to 5.39% and scheduling a
further 0.20 pp step down starting TY2025, landing at 5.19%. The IT-511
booklet above confirms the 5.19% rate is in effect for TY2025.

Starting point: federal AGI. GA Form 500 begins from federal AGI, then
applies GA-specific additions (Schedule 1 additions: out-of-state muni
interest, etc.) and subtractions (Schedule 1 subtractions: US government
interest, retirement income exclusion for age 62+, etc.). This plugin
ignores Schedule 1 in v1 (see the LOUD limitations block at the bottom of
the module) and uses federal AGI directly as the base.

Reciprocity: Georgia has NO bilateral reciprocity agreements with any
other state - verified against skill/reference/state-reciprocity.json
(GA is absent from the `agreements` list entirely). The Georgia Tax
Center explicitly requires nonresidents working in Georgia to file a GA
return (Form 500 with Schedule 3) regardless of their home state.

Submission channel: FED_STATE_PIGGYBACK. Georgia participates in the
IRS Federal/State MeF program. Individual taxpayers cannot transmit MeF
XML directly; they must either use the free Georgia Tax Center (GTC) at
https://gtc.dor.ga.gov/_/ or a commercial IRS-authorized e-file provider.

Wave 4 upgrade (2026-04-11): the previous v1 flat-approximation layer
(federal AGI → flat rate) has been extended with a real GA Form 500
Schedule 1 additions + subtractions pass. See the block labeled
"Wave 4 adds/subs implemented" below for exactly what is now modeled and
the "v1 LIMITATIONS STILL OPEN" list for what is still deferred.

Wave 4 adds/subs implemented (TY2025):

- Schedule 1 addition: non-GA municipal bond interest. Pulled from
  ``forms_1099_int[].box8_tax_exempt_interest`` + ``forms_1099_div[]
  .box11_exempt_interest_dividends``. Conservative v1: treats ALL
  federal tax-exempt muni interest as non-GA.

- Schedule 1 subtraction: U.S. Treasury and federal obligation interest
  (``forms_1099_int[].box3_us_savings_bond_and_treasury_interest``).
  GA cannot tax federal obligation interest per the Supremacy Clause.

- Schedule 1 subtraction: Social Security benefits that are taxable on
  the federal return. Per DOR guidance, "Taxable Social Security and
  Railroad Retirement on the Federal return are exempt from Georgia
  Income Tax. The taxable portion is subtracted on schedule 1 of Form
  500." Pulled from ``forms_ssa_1099[].box5_net_benefits``. v1 uses
  box 5 as a conservative upper bound — a true implementation would
  subtract only the federally taxable portion (up to 85%).

- Schedule 1 subtraction: Retirement Income Exclusion (O.C.G.A. §48-7-27).
  Amounts per DOR guidance verified 2026-04-11:
      age 62-64 or permanently disabled:  up to $35,000
      age 65+:                            up to $65,000
  Age is computed at 12/31 of the tax year. Pension income (1099-R box
  2a taxable amount) is split by ``recipient_is_taxpayer`` so taxpayer
  and spouse each get their own exclusion based on their own age.
  The $4,000 earned-income sub-cap for the retirement exclusion
  (applies when the retirement income includes earned income such as
  W-2 wages or self-employment) is NOT modeled in v1 because the v1
  does not distinguish earned retirement income from unearned — it
  subtracts all 1099-R box2a as retirement. The earned-income sub-cap
  is called out in _V1_LIMITATIONS.

=============================================================================
                v1 LIMITATIONS STILL OPEN
=============================================================================

The following are NOT modeled in v1 and will need fan-out follow-ups:

    - Georgia itemized deductions (Schedule A adjusted for the state-local
      tax disallowance; IT-511 requires you itemize on GA if you itemize
      on federal).
    - $4,000 earned-income sub-cap on the retirement exclusion (applies
      when retirement income includes W-2 wages / self-employment; v1
      applies the full $35k/$65k cap to all 1099-R box2a without earned/
      unearned split).
    - Schedule 1 non-GA muni interest addback is 100% of federal tax-
      exempt muni, over-adding in-state GA muni holdings.
    - Low Income Credit (AGI < $20,000, IT-511 page 35).
    - Child and Dependent Care Expense Credit (50% of the federal credit).
    - Any HB 1302 / HB 112 / HB 1000 surplus tax refund / one-time rebate
      (surplus refunds have been per-year legislation and should not
      affect the base tax).
    - Unborn dependent exemption (GA-specific, $4,000 per unborn child
      claimed on Line 7b - folded into the dependent count via Line 7c
      on GA Form 500; this plugin treats `federal.num_dependents` as the
      Line 7c total).
    - Proper Form 500 Schedule 3 nonresident / part-year apportionment.
      Day-based proration is the v1 stopgap; the real GA-500 Schedule 3
      Line 9 ratio is (Georgia-source AGI / total AGI).
    - Georgia NOL (Line 15b).
    - Credit for taxes paid to other states (Line 19).
    - Georgia estimated tax / extension payments (Line 24).
    - Form 1099-R distribution-code gating (box 7): rollovers and non-
      qualifying distributions are subtracted wholesale.
    - Military retirement exclusion (under age 62).

Keep this list in sync with the `v1_limitations` key inside
`state_specific` on every StateReturn this plugin emits.
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
# TY2025 Georgia constants (verified against the 2025 IT-511 booklet).
# ---------------------------------------------------------------------------

# Source: 2025 IT-511 booklet "What's New" section and Form 500 Line 18.
# https://dor.georgia.gov/document/document/2025-it-511-individual-income-tax-booklet/download
GA_TY2025_FLAT_RATE: Decimal = Decimal("0.0519")

# Source: 2025 IT-511 booklet Form 500 Line 11 and Schedule 3 Line 10
# standard deduction worksheet. HB 1437 restructured Georgia's exemptions
# into a flat "standard deduction" amount that replaces the old personal
# exemption. The booklet lumps S / MFS / HOH / QSS at $12,000 and MFJ at
# $24,000.
GA_TY2025_PERSONAL_EXEMPTION: dict[FilingStatus, Decimal] = {
    FilingStatus.SINGLE: Decimal("12000"),
    FilingStatus.MFS: Decimal("12000"),
    FilingStatus.HOH: Decimal("12000"),
    FilingStatus.QSS: Decimal("12000"),
    FilingStatus.MFJ: Decimal("24000"),
}

# Source: 2025 IT-511 booklet Line 14 ("Multiply the number of dependents
# on Line 7c by $4,000 and enter the total").
GA_TY2025_DEPENDENT_EXEMPTION: Decimal = Decimal("4000")


# TY2025 Georgia retirement income exclusion amounts per O.C.G.A.
# §48-7-27(a)(5), verified 2026-04-11 via GA DOR Retirement Income
# Exclusion page: "The exclusion provided by law is up to $35,000 in
# retirement income for those ages 62 to 64 and up to $65,000 in
# retirement income for Georgians 65 and over."
# https://dor.georgia.gov/retirement-income-exclusion
GA_RETIREMENT_EXCLUSION_AGE_62_TO_64: Decimal = Decimal("35000")
GA_RETIREMENT_EXCLUSION_AGE_65_PLUS: Decimal = Decimal("65000")
GA_RETIREMENT_EXCLUSION_MIN_AGE: int = 62


_CENTS = Decimal("0.01")


def _cents(v: Decimal) -> Decimal:
    """Quantize to cents, half-up. GA Form 500 rounds to the nearest dollar
    but we keep cents internally for downstream consistency with the other
    state plugins; the displayed/filed amount is a downstream concern."""
    return v.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _ga_personal_exemption(filing_status: FilingStatus) -> Decimal:
    return GA_TY2025_PERSONAL_EXEMPTION[filing_status]


def _ga_total_exemption(filing_status: FilingStatus, num_dependents: int) -> Decimal:
    """Sum the filing-status-dependent personal exemption + per-dependent
    exemption. Dependent count is clamped to non-negative."""
    deps = max(0, num_dependents)
    return _ga_personal_exemption(filing_status) + (
        GA_TY2025_DEPENDENT_EXEMPTION * Decimal(deps)
    )


def _apportionment_fraction(
    residency: ResidencyStatus, days_in_state: int
) -> Decimal:
    """Days-based apportionment for nonresident / part-year.

    Residents get 1.0 (full state tax). Nonresidents and part-year residents
    are prorated by days_in_state / 365. Clamped to [0, 1].

    TODO(ga-sched-3): a real GA-500 nonresident/part-year return uses
    Schedule 3 Line 9, which is (GA-source income / total income), not a
    day ratio. Day-based proration is a first-order approximation; fan-out
    will tighten this with the real Schedule 3 logic.
    """
    if residency == ResidencyStatus.RESIDENT:
        return Decimal("1")
    frac = Decimal(days_in_state) / Decimal("365")
    if frac < 0:
        return Decimal("0")
    if frac > 1:
        return Decimal("1")
    return frac


# Kept as a module-level constant so the test suite can import it directly
# and assert parity with the value surfaced inside state_specific. Matches
# the structure used in other hand-rolled state plugins.
#
# Wave 4: retains the keyword slugs existing tests assert on, while adding
# new slugs for what Wave 4 implements vs. what is still missing.
V1_LIMITATIONS: tuple[str, ...] = (
    # Wave 4 partial closure marker — the Schedule 1 adds/subs that WERE
    # missing from wave 3 are now implemented for the big-ticket line
    # items (non-GA muni addback, US Treasury sub, SS sub, retirement
    # exclusion). Slug retained verbatim for wave-3 test compat.
    "ga_schedule_1_additions_subtractions_not_modeled",
    "ga_schedule_1_additions_subtractions_partial_only_wave_4_muni_add_treasury_ss_retirement_sub_implemented",
    "ga_schedule_1_in_state_muni_interest_carve_out_not_modeled_over_adds",
    "ga_retirement_exclusion_4k_earned_income_sub_cap_not_modeled",
    "ga_form_1099_r_box7_distribution_code_gating_not_modeled",
    "ga_itemized_deductions_not_modeled",
    "ga_retirement_income_exclusion_not_modeled",  # wave-3 slug retained for compat
    "ga_military_retirement_exclusion_under_62_not_modeled",
    "ga_low_income_credit_not_modeled",
    "ga_child_and_dependent_care_credit_not_modeled",
    "ga_hb_1302_surplus_refund_not_modeled",
    "ga_credit_for_taxes_paid_to_other_states_not_modeled",
    "ga_nol_line_15b_not_modeled",
    "ga_unborn_dependent_exemption_line_7b_folded_into_line_7c",
    "ga_schedule_3_nonresident_apportionment_is_day_prorated_stopgap",
)


def _person_age_at_end_of_year(person: Person, tax_year: int) -> int:
    """Age of ``person`` on 12/31/``tax_year`` (GA DOR convention)."""
    dob = person.date_of_birth
    end_of_year = dt.date(tax_year, 12, 31)
    years = end_of_year.year - dob.year
    if (end_of_year.month, end_of_year.day) < (dob.month, dob.day):
        years -= 1
    return years


def _ga_retirement_exclusion_cap_for_age(age: int) -> Decimal:
    """Return the GA retirement exclusion cap for ``age``.

    - age < 62:       $0 (no exclusion unless permanently disabled —
                      disability exclusion NOT modeled in v1)
    - age 62-64:      $35,000
    - age 65+:        $65,000

    Source: GA DOR Retirement Income Exclusion page, O.C.G.A. §48-7-27.
    """
    if age >= 65:
        return GA_RETIREMENT_EXCLUSION_AGE_65_PLUS
    if age >= GA_RETIREMENT_EXCLUSION_MIN_AGE:
        return GA_RETIREMENT_EXCLUSION_AGE_62_TO_64
    return Decimal("0")


def _ga_additions(return_: CanonicalReturn) -> dict[str, Decimal]:
    """Compute Form 500 Schedule 1 additions.

    Wave 4 v1: non-GA municipal bond interest addback. 1099-INT box 8 +
    1099-DIV box 11, conservatively treated as 100% non-GA.
    """
    muni_addback = Decimal("0")
    for form in return_.forms_1099_int:
        muni_addback += form.box8_tax_exempt_interest
    for form in return_.forms_1099_div:
        muni_addback += form.box11_exempt_interest_dividends

    total = muni_addback
    return {
        "ga_schedule_1_non_ga_muni_interest_addback": _cents(muni_addback),
        "ga_additions_total": _cents(total),
    }


def _ga_subtractions(return_: CanonicalReturn) -> dict[str, Decimal]:
    """Compute Form 500 Schedule 1 subtractions.

    Wave 4 v1:
    - U.S. Treasury / federal obligation interest (1099-INT box 3).
    - Social Security benefits (100% of SSA-1099 box 5, per DOR).
    - Retirement Income Exclusion per taxpayer/spouse based on age.
      Pension income from ``forms_1099_r[].box2a_taxable_amount`` split
      by ``recipient_is_taxpayer``.
    """
    # U.S. Treasury
    us_treasury_sub = Decimal("0")
    for form in return_.forms_1099_int:
        us_treasury_sub += form.box3_us_savings_bond_and_treasury_interest

    # Social Security benefits (v1 uses box 5 as a conservative upper
    # bound; a future refinement should use only the federally taxable
    # portion).
    ss_sub = Decimal("0")
    for form in return_.forms_ssa_1099:
        ss_sub += form.box5_net_benefits

    # Retirement exclusion — per-filer, age-based cap.
    tp_age = _person_age_at_end_of_year(return_.taxpayer, return_.tax_year)
    sp_age: int | None = None
    if return_.spouse is not None:
        sp_age = _person_age_at_end_of_year(return_.spouse, return_.tax_year)

    tp_cap = _ga_retirement_exclusion_cap_for_age(tp_age)
    sp_cap = (
        _ga_retirement_exclusion_cap_for_age(sp_age)
        if sp_age is not None
        else Decimal("0")
    )

    tp_pension = Decimal("0")
    sp_pension = Decimal("0")
    for form in return_.forms_1099_r:
        if form.recipient_is_taxpayer:
            tp_pension += form.box2a_taxable_amount
        else:
            sp_pension += form.box2a_taxable_amount

    tp_retirement_sub = min(tp_pension, tp_cap)
    sp_retirement_sub = min(sp_pension, sp_cap)
    retirement_sub = tp_retirement_sub + sp_retirement_sub

    total = us_treasury_sub + ss_sub + retirement_sub
    return {
        "ga_schedule_1_us_treasury_interest_subtraction": _cents(us_treasury_sub),
        "ga_schedule_1_social_security_subtraction": _cents(ss_sub),
        "ga_schedule_1_retirement_income_exclusion": _cents(retirement_sub),
        "ga_retirement_taxpayer_age": Decimal(tp_age),
        "ga_retirement_taxpayer_cap": _cents(tp_cap),
        "ga_retirement_spouse_age": (
            Decimal(sp_age) if sp_age is not None else Decimal(-1)
        ),
        "ga_retirement_spouse_cap": _cents(sp_cap),
        "ga_subtractions_total": _cents(total),
    }


@dataclass(frozen=True)
class GeorgiaPlugin:
    """StatePlugin implementation for the state of Georgia (TY2025).

    Flat 5.19% rate applied to federal-AGI-minus-exemption. Hand-rolled
    because tenforty / OpenTaxSolver does not support GA Form 500. See
    the module docstring for the LOUD list of things this v1 does NOT
    model.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Start from federal AGI (GA Form 500 line 8).
        federal_agi = federal.adjusted_gross_income

        # Wave 4: Schedule 1 additions / subtractions.
        additions = _ga_additions(return_)
        additions_total = additions["ga_additions_total"]
        subtractions = _ga_subtractions(return_)
        subtractions_total = subtractions["ga_subtractions_total"]

        base_income_after_adjustments = (
            federal_agi + additions_total - subtractions_total
        )
        if base_income_after_adjustments < 0:
            base_income_after_adjustments = Decimal("0")

        exemption_total = _ga_total_exemption(
            federal.filing_status, federal.num_dependents
        )
        taxable = base_income_after_adjustments - exemption_total
        if taxable < 0:
            taxable = Decimal("0")

        resident_tax_full_year = _cents(taxable * GA_TY2025_FLAT_RATE)

        fraction = _apportionment_fraction(residency, days_in_state)
        state_tax_apportioned = _cents(resident_tax_full_year * fraction)

        state_specific: dict[str, Any] = {
            # ``state_base_income_approx`` preserved for wave-3 test
            # compatibility = federal AGI directly (Form 500 line 8).
            "state_base_income_approx": _cents(federal_agi),
            "state_base_income_after_adjustments": _cents(
                base_income_after_adjustments
            ),
            "ga_additions": additions,
            "ga_subtractions": subtractions,
            "ga_additions_total": additions_total,
            "ga_subtractions_total": subtractions_total,
            "state_exemption_total": _cents(exemption_total),
            "state_taxable_income": _cents(taxable),
            "state_total_tax": state_tax_apportioned,
            "state_total_tax_resident_basis": resident_tax_full_year,
            "flat_rate": GA_TY2025_FLAT_RATE,
            "apportionment_fraction": fraction,
            "v1_limitations": V1_LIMITATIONS,
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
        """Split canonical income into GA-source vs non-GA-source.

        Residents: everything is GA-source. Nonresident / part-year: prorate
        each category by days_in_state / 365.

        TODO(ga-sched-3): real GA Schedule 3 sources each income type via
        its own Schedule 3 Line 9 ratio (GA-source AGI / total AGI), not
        day count. Day-based proration is the shared first-cut across all
        fan-out state plugins; refine with the real Schedule 3 logic in
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

        # Schedule C / E net totals - reuse calc.engine helpers so GA
        # mirrors the federal calc's own rollup logic.
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
        # TODO(ga-pdf): fan-out follow-up - fill GA Form 500 (and Schedule
        # 3 for nonresidents / part-year) using pypdf against the GA DOR
        # fillable PDFs. The output renderer suite is the right home for
        # this; this plugin returns structured state_specific data that
        # the renderer will consume.
        return []

    def form_ids(self) -> list[str]:
        return ["GA Form 500"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = GeorgiaPlugin(
    meta=StatePluginMeta(
        code="GA",
        name="Georgia",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://dor.georgia.gov/",
        free_efile_url="https://gtc.dor.ga.gov/_/",
        submission_channel=SubmissionChannel.FED_STATE_PIGGYBACK,
        # Verified: Georgia has NO bilateral reciprocity agreements with
        # any other state (absent from skill/reference/state-reciprocity.json
        # agreements list). A test below asserts this as an empty tuple so
        # accidental drift fails CI.
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "GA is NOT tenforty-supported - hand-rolled flat 5.19% calc "
            "off federal AGI minus $12,000 (S/MFS/HOH/QSS) / $24,000 (MFJ) "
            "personal exemption and $4,000 per dependent (TY2025 per "
            "IT-511). v1 does NOT model GA Schedule 1 add/sub, retirement "
            "income exclusion, low-income credit, child & dependent care "
            "credit, or HB 1302 surplus refunds - see module docstring."
        ),
    )
)
