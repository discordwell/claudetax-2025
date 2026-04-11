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

=============================================================================
                LOUD v1 LIMITATIONS - NOT MODELED HERE
=============================================================================

The simplified v1 calculation applied by this plugin is:

    1. base = federal AGI  (no GA Schedule 1 additions or subtractions)
    2. exemption_total = GA standard-deduction-equivalent personal amount
                         + $4,000 per dependent
    3. taxable = max(0, base - exemption_total)
    4. tax = taxable * 5.19% (flat rate)

The following are NOT modeled in v1 and will need fan-out follow-ups
before the plugin is considered production-accurate:

    - Georgia itemized deductions (Schedule A adjusted for the state-local
      tax disallowance; IT-511 requires you itemize on GA if you itemize
      on federal).
    - Retirement income exclusion (O.C.G.A. 48-7-27: up to $35,000
      earned+unearned for age 62-64, up to $65,000 for 65+; earned-income
      sub-cap of $4,000).
    - Low Income Credit (AGI < $20,000, IT-511 page 35).
    - Child and Dependent Care Expense Credit (50% of the federal credit).
    - Any HB 1302 surplus tax refund / one-time rebate (surplus refunds
      have been per-year legislation and should not affect the base tax).
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

Keep this list in sync with the `v1_limitations` key inside
`state_specific` on every StateReturn this plugin emits.
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
V1_LIMITATIONS: tuple[str, ...] = (
    "ga_schedule_1_additions_subtractions_not_modeled",
    "ga_itemized_deductions_not_modeled",
    "ga_retirement_income_exclusion_not_modeled",
    "ga_low_income_credit_not_modeled",
    "ga_child_and_dependent_care_credit_not_modeled",
    "ga_hb_1302_surplus_refund_not_modeled",
    "ga_credit_for_taxes_paid_to_other_states_not_modeled",
    "ga_nol_line_15b_not_modeled",
    "ga_unborn_dependent_exemption_line_7b_folded_into_line_7c",
    "ga_schedule_3_nonresident_apportionment_is_day_prorated_stopgap",
)


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
        base_income = federal.adjusted_gross_income
        exemption_total = _ga_total_exemption(
            federal.filing_status, federal.num_dependents
        )
        taxable = base_income - exemption_total
        if taxable < 0:
            taxable = Decimal("0")

        resident_tax_full_year = _cents(taxable * GA_TY2025_FLAT_RATE)

        fraction = _apportionment_fraction(residency, days_in_state)
        state_tax_apportioned = _cents(resident_tax_full_year * fraction)

        state_specific: dict[str, Any] = {
            "state_base_income_approx": _cents(base_income),
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
