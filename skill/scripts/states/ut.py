"""Utah (UT) state plugin — TY2025.

See skill/reference/tenforty-ty2025-gap.md for the TY2025 probe rubric
and why UT is hand-rolled instead of graph-wrapped (graph backend
omits the Taxpayer Tax Credit phase-out — material mismatch > $5).

Hand-rolled UT Form TC-40 calculation. Tenforty does NOT support UT
via the default OTS backend (``ValueError: OTS does not support
2025/UT_TC40``). The newer graph backend returns a number ($1,980 on
the spec's $65k Single scenario) but **the graph value diverges from
the DOR primary source by ~$608** because the graph backend applies
the **full** Utah Taxpayer Tax Credit ($945) without the income-based
phase-out, while the actual TC-40 phases the credit out at 1.3% per
dollar of UT taxable income above the filing-status base. On the
$65k Single scenario the phase-out wipes out roughly two-thirds of
the credit, raising the actual UT income tax to $2,588.23 — far
above the graph's $1,980. Material mismatch (~$608) → hand-roll.

Source of truth
---------------
2025 Utah TC-40 Instructions (tax.utah.gov/forms/current/tc-40inst.pdf),
verified 2026-04-11. Key references:

- Page 4 ("What's New"): "The 2025 Utah Legislature passed HB 106,
  lowering the income tax rate from 4.55 percent to 4.5 percent."
  TY2025 flat rate is **4.5%**, NOT the 4.55% in the wave 5 task spec.
- Page 8 (Lines 9-22): full TC-40 page-1 calculation flow.
- Page 8 (Line 11): personal exemption = $2,111 per dependent.
- Page 8 (Line 17): base phase-out amounts:
    Single                 $18,213
    MFJ                    $36,426
    MFS                    $18,213
    HOH                    $27,320
    Qualifying Surv Spouse $36,426
- Page 8 (Line 19): phase-out rate = **1.3%** (.013) of (UT taxable
  income - base phase-out amount).
- Page 8 (Line 16): initial credit = **6%** of (line 11 personal
  exemption + line 12 federal std/itemized deduction - line 14 SALT
  income tax addback).

TC-40 calculation flow (TY2025, resident):

    Line 4   Federal AGI (1040 line 11)
    Line 5   Additions to income (TC-40A Part 1)
    Line 6   Total income = L4 + L5
    Line 7   State tax refund included on federal return
    Line 8   Subtractions from income (TC-40A Part 2)
    Line 9   Utah taxable income/loss = L6 - L7 - L8
    Line 10  Utah tax calculation = L9 * 4.5% (=.045)
    Line 11  Utah personal exemption = $2,111 * dependents (line d)
    Line 12  Federal standard or itemized deduction (1040 line 12e)
    Line 13  Total exemptions and std/itemized = L11 + L12
    Line 14  State income tax included in federal Sch A itemized
             deductions (zero if standard)
    Line 15  Total exemptions and federal deductions = L13 - L14
    Line 16  Initial credit before phase-out = L15 * 6%
    Line 17  Base phase-out amount (table on instruction page 8)
    Line 18  Income subject to phase-out = max(0, L9 - L17)
    Line 19  Phase-out amount = L18 * 1.3%
    Line 20  Taxpayer tax credit = max(0, L16 - L19)
    Line 21  Qualified exempt taxpayer flag (set if AGI <= fed std ded
             + fed senior enhanced ded)
    Line 22  Utah income tax = L10 - L20 (or 0 if line 21 set)

**TY2025 Single $65k resident reference scenario** (locked in tests):

    Line 4   Federal AGI                   = $65,000
    Line 9   UT taxable income             = $65,000   (no add/sub)
    Line 10  UT tax calc                   = $65,000 * 0.045 = $2,925.00
    Line 11  Personal exemption (0 deps)   = $0
    Line 12  Federal std deduction (Single)= $15,750
    Line 13  L11 + L12                     = $15,750
    Line 14  SALT addback                  = $0   (took standard ded)
    Line 15  L13 - L14                     = $15,750
    Line 16  Initial credit                = $15,750 * 0.06 = $945.00
    Line 17  Base phase-out (Single)       = $18,213
    Line 18  Income subj to phase-out      = max(0, $65,000 - $18,213)
                                            = $46,787
    Line 19  Phase-out amount              = $46,787 * 0.013 = $608.231
    Line 20  Taxpayer tax credit           = max(0, $945 - $608.231)
                                            = $336.769
    Line 22  UT income tax                 = $2,925 - $336.769
                                            = **$2,588.23**  (rounds to $2,588)

This is the locked value. The graph backend's $1,980 is wrong because
it omits the phase-out (you can recover $1,980 by subtracting the
**full** $945 initial credit from line 10's $2,925: $2,925 - $945 =
$1,980). The phase-out is the dominant correction — at $65k Single
about 64% of the initial credit phases out.

Reciprocity
-----------
Utah has **no** bilateral reciprocity agreements with any other
state — verified against ``skill/reference/state-reciprocity.json``.
UT residents who work in adjacent states must file nonresident
returns and claim the Utah credit for income tax paid to another
state on TC-40A Part 4 (credit code 17).

Submission channel
------------------
Utah operates **Utah Taxpayer Access Point (TAP)** at
https://tap.utah.gov as a free DOR-direct portal supporting
individual income tax e-file. Channel = ``STATE_DOR_FREE_PORTAL``.
Utah also participates in the IRS Fed/State MeF program for
commercial software piggyback.

v1 limitations
--------------
See ``UT_V1_LIMITATIONS``. Notable items:
- TC-40A Part 1 additions and Part 2 subtractions all default to 0.
- Personal exemption only counts statutory dependents from
  ``federal.num_dependents``; the TC-40 line d on the actual return
  excludes dependents claimed on someone else's return, etc.
- Itemizers' SALT addback (line 14) defaults to 0 — v1 only handles
  the standard deduction case correctly.
- Apportionable / non-apportionable nonrefundable credits, refundable
  credits, child tax credit, EITC, my529 credit, etc. all default to
  zero. The v1 result is the pre-credits Utah income tax (line 22).
- Nonresident / part-year apportionment uses day-based proration
  instead of TC-40B Non / Part-year Resident Schedule.

Why hand-roll
-------------
The graph backend skips the Taxpayer Tax Credit phase-out, producing
an income tax that is roughly $608 too low at the $65k Single
scenario. Per spec ±$5 wrap-window, this is a clear material
mismatch — hand-roll from the TC-40 instructions.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Final

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


# Canonical wave-5 $65k Single gatekeeper lock. Hand-traced from UT
# Form TC-40 — see module docstring. Referenced from test_state_ut.py.
LOCK_VALUE: Final[Decimal] = Decimal("2588.23")


# ---------------------------------------------------------------------------
# TY2025 constants — TC-40 Instructions page 8 (verified 2026-04-11)
# ---------------------------------------------------------------------------


UT_TY2025_FLAT_RATE: Decimal = Decimal("0.045")
"""Utah flat individual income tax rate, TY2025 = 4.5% (per HB 106 2025).

Was 4.55% in TY2024. Wave 5 task spec said "4.55% — verify"; verified
against the 2025 TC-40 Instructions "What's New" section, page 4. The
2025 Utah Legislature passed HB 106 lowering the rate to 4.5% effective
1/1/2025."""

UT_TY2025_PERSONAL_EXEMPTION_PER_DEPENDENT: Decimal = Decimal("2111")
"""TC-40 line 11 — UCA Sec. 59-10-1018(1)(g). $2,111 per dependent."""

UT_TY2025_TAXPAYER_TAX_CREDIT_RATE: Decimal = Decimal("0.06")
"""TC-40 line 16 — initial Taxpayer Tax Credit rate = 6% of total
exemptions + federal deductions (line 15)."""

UT_TY2025_TAXPAYER_TAX_CREDIT_PHASE_OUT_RATE: Decimal = Decimal("0.013")
"""TC-40 line 19 — Taxpayer Tax Credit phase-out rate = 1.3% per dollar
of UT taxable income above the filing-status base phase-out amount."""

UT_TY2025_BASE_PHASE_OUT_SINGLE: Decimal = Decimal("18213")
UT_TY2025_BASE_PHASE_OUT_MFJ: Decimal = Decimal("36426")
UT_TY2025_BASE_PHASE_OUT_MFS: Decimal = Decimal("18213")
UT_TY2025_BASE_PHASE_OUT_HOH: Decimal = Decimal("27320")
UT_TY2025_BASE_PHASE_OUT_QSS: Decimal = Decimal("36426")

UT_TY2025_BASE_PHASE_OUT_BY_STATUS: dict[FilingStatus, Decimal] = {
    FilingStatus.SINGLE: UT_TY2025_BASE_PHASE_OUT_SINGLE,
    FilingStatus.MFJ: UT_TY2025_BASE_PHASE_OUT_MFJ,
    FilingStatus.MFS: UT_TY2025_BASE_PHASE_OUT_MFS,
    FilingStatus.HOH: UT_TY2025_BASE_PHASE_OUT_HOH,
    FilingStatus.QSS: UT_TY2025_BASE_PHASE_OUT_QSS,
}
"""TC-40 line 17 base phase-out amounts (TY2025) by filing status.

Source: 2025 TC-40 Instructions page 8 line 17 table."""

# Federal standard deductions used for the line 12 input. Utah uses
# whatever federal standard deduction the taxpayer actually took on
# their 1040 line 12e — these constants are TY2025 OBBBA defaults.
UT_TY2025_FED_STD_DED_SINGLE: Decimal = Decimal("15750")
UT_TY2025_FED_STD_DED_MFJ: Decimal = Decimal("31500")
UT_TY2025_FED_STD_DED_HOH: Decimal = Decimal("23625")
UT_TY2025_FED_STD_DED_MFS: Decimal = Decimal("15750")
UT_TY2025_FED_STD_DED_QSS: Decimal = Decimal("31500")


UT_V1_LIMITATIONS: tuple[str, ...] = (
    "TC-40A Part 1 additions NOT applied: lump sum distribution, "
    "MSA addback, my529 addback, child's income excluded from "
    "parent's return, municipal bond interest from non-Utah/non-US "
    "obligations, untaxed income of resident/nonresident trust, "
    "Payroll Protection Program addback, equitable adjustments, "
    "tax paid on behalf of pass-through entity taxpayer.",
    "TC-40A Part 2 subtractions NOT applied: interest from Utah "
    "muni / US Govt obligations, Native American income, railroad "
    "retirement income, equitable adjustments, nonresident active "
    "duty military pay, FDIC premiums, previously-taxed retirement "
    "income, nonresident military spouse income.",
    "TC-40 line 14 SALT itemized addback NOT modeled — v1 only "
    "handles the standard-deduction case. Itemizers may overstate "
    "their UT line 15 (and thus the Taxpayer Tax Credit) by the "
    "amount of state income tax they deducted on federal Schedule A.",
    "TC-40A Part 3 apportionable nonrefundable credits NOT applied: "
    "capital gain transactions credit, retirement credit, my529 "
    "credit, health benefit plan credit, gold/silver coin sale "
    "credit, Social Security benefits credit, military retirement "
    "credit, federal earned income tax credit, nonrefundable adoption "
    "expenses credit, child tax credit, employer-provided childcare "
    "construction credit.",
    "TC-40A Part 4 nonapportionable nonrefundable credits NOT "
    "applied: at-home parent credit, sheltered workshop, historic "
    "preservation, research activities, **credit for income tax paid "
    "to another state** (critical for multi-state filers), live "
    "organ donation, renewable residential energy systems, combat "
    "related death credit, veteran employment, ABLE program, Carson "
    "Smith Opportunity Scholarship, pass-through entity taxpayer "
    "income tax credit.",
    "TC-40A Part 5 refundable credits NOT applied: targeted business "
    "tax credit, mineral production withholding, employer-provided "
    "childcare credits.",
    "Use tax (line 31), recapture of low-income housing credit "
    "(line 30), voluntary contributions (line 28) all default to 0.",
    "Qualified Exempt Taxpayer (line 21) auto-detection: v1 detects "
    "the AGI <= federal std ded case but does NOT yet handle the "
    "federal Schedule 1-A senior enhanced deduction add-on (line 3 "
    "of the page-9 worksheet).",
    "Personal exemption (line 11) uses ``federal.num_dependents`` "
    "directly. The actual TC-40 line d excludes dependents claimed "
    "on another return and applies a different qualifying-relative "
    "test that may differ from the federal Schedule 8812 dependent "
    "count.",
    "Nonresident / part-year apportionment uses day-based proration "
    "instead of the TC-40B Non/Part-year Resident Schedule, which "
    "uses an income-source ratio against UT-source wages, business "
    "income, and rental property.",
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def ut_personal_exemption(num_dependents: int) -> Decimal:
    """TC-40 line 11: $2,111 * dependents."""
    n = max(0, num_dependents)
    return Decimal(n) * UT_TY2025_PERSONAL_EXEMPTION_PER_DEPENDENT


def ut_base_phase_out(filing_status: FilingStatus) -> Decimal:
    """TC-40 line 17 base phase-out amount by filing status."""
    return UT_TY2025_BASE_PHASE_OUT_BY_STATUS.get(
        filing_status, UT_TY2025_BASE_PHASE_OUT_SINGLE
    )


def ut_taxpayer_tax_credit(
    *,
    ut_taxable_income: Decimal,
    federal_deduction: Decimal,
    salt_addback: Decimal,
    num_dependents: int,
    filing_status: FilingStatus,
) -> tuple[Decimal, Decimal, Decimal]:
    """Compute the TY2025 Utah Taxpayer Tax Credit (TC-40 lines 11-20).

    Returns the tuple ``(initial_credit, phase_out_amount, taxpayer_tax_credit)``
    where ``taxpayer_tax_credit`` is the line 20 value (max(0, L16 - L19)).
    """
    exemption = ut_personal_exemption(num_dependents)
    line_13 = exemption + d(federal_deduction)
    line_15 = line_13 - d(salt_addback)
    if line_15 < 0:
        line_15 = Decimal("0")
    initial_credit = line_15 * UT_TY2025_TAXPAYER_TAX_CREDIT_RATE
    base = ut_base_phase_out(filing_status)
    line_18 = d(ut_taxable_income) - base
    if line_18 < 0:
        line_18 = Decimal("0")
    phase_out = line_18 * UT_TY2025_TAXPAYER_TAX_CREDIT_PHASE_OUT_RATE
    credit = initial_credit - phase_out
    if credit < 0:
        credit = Decimal("0")
    return (cents(initial_credit), cents(phase_out), cents(credit))


def ut_federal_std_deduction(filing_status: FilingStatus) -> Decimal:
    """Default federal std deduction by status (TY2025 OBBBA).

    Used as the line 12 input when the caller does not pass a federal
    deduction explicitly.
    """
    return {
        FilingStatus.SINGLE: UT_TY2025_FED_STD_DED_SINGLE,
        FilingStatus.MFJ: UT_TY2025_FED_STD_DED_MFJ,
        FilingStatus.HOH: UT_TY2025_FED_STD_DED_HOH,
        FilingStatus.MFS: UT_TY2025_FED_STD_DED_MFS,
        FilingStatus.QSS: UT_TY2025_FED_STD_DED_QSS,
    }.get(filing_status, UT_TY2025_FED_STD_DED_SINGLE)


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UtahPlugin:
    """State plugin for Utah — TY2025.

    Hand-rolled TC-40 calc with the Taxpayer Tax Credit phase-out
    properly applied. Tenforty's graph backend omits the phase-out and
    produces a tax that is ~$608 too low at $65k Single — see module
    docstring.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # TC-40 Line 4: federal AGI.
        federal_agi = d(federal.adjusted_gross_income)
        if federal_agi < 0:
            federal_agi = Decimal("0")

        # Lines 5, 7, 8: additions / state refund / subtractions = 0.
        ut_additions = Decimal("0")
        ut_subtractions = Decimal("0")

        # Line 9: UT taxable income.
        ut_taxable_income = federal_agi + ut_additions - ut_subtractions
        if ut_taxable_income < 0:
            ut_taxable_income = Decimal("0")

        # Line 10: UT tax calc = TI * 4.5%.
        ut_tax_calc = ut_taxable_income * UT_TY2025_FLAT_RATE
        if ut_tax_calc < 0:
            ut_tax_calc = Decimal("0")

        # Line 12: federal std/itemized deduction. Use the federal
        # standard deduction from FederalTotals; v1 does not yet
        # support the itemized case correctly (see UT_V1_LIMITATIONS).
        federal_deduction = d(federal.deduction_taken)
        if federal_deduction <= 0:
            federal_deduction = ut_federal_std_deduction(
                federal.filing_status
            )

        # Line 14: SALT addback (zero when standard deduction).
        salt_addback = Decimal("0")

        # Lines 11-20: Taxpayer Tax Credit calc.
        initial_credit, phase_out_amount, taxpayer_tax_credit = (
            ut_taxpayer_tax_credit(
                ut_taxable_income=ut_taxable_income,
                federal_deduction=federal_deduction,
                salt_addback=salt_addback,
                num_dependents=federal.num_dependents,
                filing_status=federal.filing_status,
            )
        )

        # Line 21: Qualified Exempt Taxpayer auto-detection.
        # If federal AGI <= federal std deduction (excluding senior
        # enhanced ded), the taxpayer is exempt from UT income tax.
        federal_std_ded_for_exempt_check = ut_federal_std_deduction(
            federal.filing_status
        )
        qualified_exempt = federal_agi <= federal_std_ded_for_exempt_check

        # Line 22: UT income tax.
        if qualified_exempt:
            ut_income_tax = Decimal("0")
        else:
            ut_income_tax = ut_tax_calc - taxpayer_tax_credit
            if ut_income_tax < 0:
                ut_income_tax = Decimal("0")
        ut_income_tax = cents(ut_income_tax)

        # Apportion for nonresident / part-year (day-based v1).
        if residency == ResidencyStatus.RESIDENT or days_in_state >= 365:
            ut_tax_apportioned = ut_income_tax
            apportionment_fraction = Decimal("1")
        else:
            ut_tax_apportioned = day_prorate(
                ut_income_tax, days_in_state=max(0, days_in_state)
            )
            apportionment_fraction = (
                Decimal(max(0, days_in_state)) / Decimal("365")
            )
            if apportionment_fraction > 1:
                apportionment_fraction = Decimal("1")

        state_specific: dict[str, Any] = {
            "state_federal_agi": cents(federal_agi),
            "state_adjusted_gross_income": cents(federal_agi),
            "state_taxable_income": cents(ut_taxable_income),
            "state_tax_before_credit": cents(ut_tax_calc),
            "state_personal_exemption": cents(
                ut_personal_exemption(federal.num_dependents)
            ),
            "state_federal_deduction": cents(federal_deduction),
            "state_salt_addback": cents(salt_addback),
            "state_initial_taxpayer_tax_credit": initial_credit,
            "state_taxpayer_tax_credit_phase_out": phase_out_amount,
            "state_taxpayer_tax_credit": taxpayer_tax_credit,
            "state_qualified_exempt": qualified_exempt,
            "state_total_tax": ut_tax_apportioned,
            "state_total_tax_resident_basis": ut_income_tax,
            "state_flat_rate": UT_TY2025_FLAT_RATE,
            "state_phase_out_rate": (
                UT_TY2025_TAXPAYER_TAX_CREDIT_PHASE_OUT_RATE
            ),
            "state_base_phase_out": ut_base_phase_out(
                federal.filing_status
            ),
            "apportionment_fraction": apportionment_fraction,
            "starting_point": "federal_agi",
            "v1_limitations": list(UT_V1_LIMITATIONS),
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
        """Day-prorated income split. TODO(ut-tc40b)."""
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

        if residency == ResidencyStatus.RESIDENT or days_in_state >= 365:
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
        # TODO(ut-pdf): fan-out follow-up — fill TC-40 + TC-40A + TC-40B
        # against UT State Tax Commission fillable PDFs.
        return []

    def form_ids(self) -> list[str]:
        return ["UT Form TC-40"]


PLUGIN: StatePlugin = UtahPlugin(
    meta=StatePluginMeta(
        code="UT",
        name="Utah",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://tax.utah.gov/forms",
        # Utah TAP — DOR's free direct portal for individual income tax.
        free_efile_url="https://tap.utah.gov/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # Utah has no bilateral reciprocity with any state — verified
        # against skill/reference/state-reciprocity.json.
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Hand-rolled UT Form TC-40 calc (tenforty does not "
            "support 2025/UT_TC40 on the OTS backend, and the graph "
            "backend skips the Taxpayer Tax Credit phase-out, "
            "producing a tax that is ~$608 too low at $65k Single). "
            "Flat rate 4.5% for TY2025 per HB 106 (2025 Legislature, "
            "down from 4.55% in TY2024). Taxpayer Tax Credit equals "
            "6% of (personal exemption + federal deduction) less a "
            "1.3% phase-out per dollar of UT taxable income above "
            "the filing-status base ($18,213 Single, $36,426 MFJ, "
            "$27,320 HOH). Starting point: federal AGI (TC-40 line "
            "4). Free DOR portal: Utah TAP. No reciprocity. Source: "
            "2025 TC-40 Instructions, tax.utah.gov."
        ),
    )
)
