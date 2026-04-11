"""Montana state plugin — TY2025.

See skill/reference/tenforty-ty2025-gap.md for the TY2025 probe rubric
and the graph-backend output-field gap list (state_taxable_income
echo, state_tax_bracket=0, state_effective_tax_rate=0).

Wraps tenforty's graph backend for Montana Form 2 (resident return).
Mirrors the WI / wave-5 graph-backend wrapper pattern: probe, verify
against DOR primary source, then wrap.

Decision rubric (per skill/reference/tenforty-ty2025-gap.md)
-----------------------------------------------------------
1. **Probe** (2026-04-11, .venv tenforty, graph backend, 2025):
       Single / $65,000 W-2 / Standard
         -> state_total_tax            = 2652.55
            state_adjusted_gross_income = 49250.00
            state_taxable_income        = 49250.00
            state_tax_bracket           = 0.0     (graph backend omits)
            state_effective_tax_rate    = 0.0     (graph backend omits)
   The default OTS backend raises
   ``ValueError: OTS does not support 2025/MT_Form2`` — graph
   backend is the only working path.

   Note: the graph backend reports state_adjusted_gross_income =
   $49,250 (i.e. federal taxable income) for Montana, NOT federal
   AGI. This reflects how MT Form 2 is structured: line 1 imports
   federal AGI ($65,000), line 2 subtracts the federal standard
   deduction ($15,750), and line 3 = federal taxable income
   ($49,250) is the Montana base before MT-specific
   additions/subtractions. The graph backend collapses lines 1-3 and
   surfaces line 3 as both state_agi and state_taxable_income for
   the wage-only base case.

2. **Verify** against Montana DOR primary source — 2025 Montana Tax
   Tables and Deductions
   (https://revenue.mt.gov/files/BIT/Montana-Tax-Tables-and-Deductions/2025-Tax-Rates-and-Deductions.pdf)
   and 2025 Form 2 instructions
   (https://revenue.mt.gov/files/forms/Montana-Individual-Income-Tax-Return-Form-2-Instructions/2025_Montana_Individual_Income_Tax_Return_Form_2_Instructions.pdf):

   - Montana **Ordinary Income Tax Brackets** (TY2025, post Tax
     Simplification — Montana abandoned its old 7-bracket schedule
     in TY2024 and adopted a 2-bracket structure):

         Single / MFS / Estates / Trusts / PTE Composite
             $0    - $21,100   →  4.7%
             $21,100+          →  5.9%

         Head of Household
             $0    - $31,700   →  4.7%
             $31,700+          →  5.9%

         Married Filing Jointly / Qualifying Surviving Spouse
             $0    - $42,200   →  4.7%
             $42,200+          →  5.9%

   - Long-term capital gains use a separate preferential schedule
     (3.0% / 4.1%) — see Montana Net Long-Term Capital Gains Tax
     Table on the same source PDF. The base scenario is wage-only,
     so capital gains tax does not apply.

   - Montana Form 2 starts from federal AGI on line 1, subtracts the
     federal standard or itemized deduction on line 2, and line 3 =
     federal taxable income is the MT starting base. MT then layers
     additions (Schedule I Part I) and subtractions (Schedule I Part
     I, lines for state refund add-back, US bond interest subtract,
     etc.) before computing tax on Montana taxable income.

   - Hand calc for $65,000 Single / Standard:
       Federal AGI                    = $65,000
       Federal standard deduction     = $15,750  (OBBBA Single)
       Federal taxable income (line 3)= $49,250
       Montana taxable income         = $49,250  (no MT add/sub for
                                                 wage-only base case)
       Tax = 4.7% × $21,100 + 5.9% × ($49,250 - $21,100)
           = $991.70 + 5.9% × $28,150
           = $991.70 + $1,660.85
           = **$2,652.55**

   - Graph backend: **$2,652.55** — **EXACT MATCH** to the cent.

3. **Decision: WRAP** the graph backend (exact match — Montana is
   one of the cleanest graph-backend implementations in the wave-5
   batch). The plugin pins the graph-backend value bit-for-bit so
   any upstream tenforty drift trips CI.

Other Montana TY2025 deductions / subtractions (not in base case):
    - 65 and over exemption: $5,500 (Single) / $11,000 (both
      spouses 65+ MFJ). Per 2025 Montana Tax Tables and Deductions
      page 1.
    - MSA (Medical Savings Account) contribution: $4,600 cap.
    - Montana does NOT have a separate "standard deduction" like
      most states — federal std/itemized flows directly to line 2.

Source documents (verified 2026-04-11):
    - 2025 Montana Tax Tables and Deductions
      https://revenue.mt.gov/files/BIT/Montana-Tax-Tables-and-Deductions/2025-Tax-Rates-and-Deductions.pdf
    - 2025 Montana Form 2
      https://revenue.mt.gov/files/Forms/Montana-Individual-Income-Tax-Return-Form-2/2025_Montana_Individual_Income_Tax_Return_Form_2.pdf
    - 2025 Montana Form 2 Instructions
      https://revenue.mt.gov/files/forms/Montana-Individual-Income-Tax-Return-Form-2-Instructions/2025_Montana_Individual_Income_Tax_Return_Form_2_Instructions.pdf
    - 2025 Montana Form 2 Schedule I (additions / subtractions)
      https://revenue.mt.gov/files/Forms/Montana-Individual-Income-Tax-Return-Form-2/Form_2_2025_Schedule_I.pdf
    - 2025 Montana Form 2 Schedule II (credits)
      https://revenue.mt.gov/files/Forms/Montana-Individual-Income-Tax-Return-Form-2/Form_2_2025_Schedule_II.pdf
    - Montana Tax Simplification Resource Hub
      https://revenue.mt.gov/montana-tax-simplification-resource-hub
    - HB337: 2026-2027 Montana Individual Income Tax Changes
      https://revenue.mt.gov/news/recent-news/HB-337
      (informational — does NOT affect TY2025; HB337 changes apply
       starting TY2026)

Reciprocity (verified against skill/reference/state-reciprocity.json):
    Montana has **one** bilateral reciprocity agreement: with
    **North Dakota (ND)**. Per
    skill/reference/state-reciprocity.json::agreements:
        {"states": ["MT", "ND"]}
    A Montana resident who works in North Dakota does not file a
    nonresident ND return for wages — wages are taxed solely by
    Montana. (Reciprocity covers wages only; rental, business,
    and investment income are still subject to ND nonresident
    filing rules.) The reverse — ND resident, MT wages — is
    similarly exempted from MT taxation on wages.

    Note: this is the only reciprocity agreement in the wave-5
    HI/ID/MT batch. HI and ID have none.

Submission channel:
    Montana operates the **TransAction Portal (TAP)** at
    https://tap.dor.mt.gov/ — the DOR's free direct e-file portal
    for individual income tax. Montana also participates in the IRS
    Fed/State MeF program for commercial software piggyback filings.
    Canonical channel: ``SubmissionChannel.STATE_DOR_FREE_PORTAL``
    (TAP is the free direct path for individual taxpayers).

Nonresident / part-year handling:
    Day-based proration of the resident-basis tax is the v0.1
    stopgap. The correct treatment is Montana Form 2 with the
    Nonresident / Part-Year Resident Schedule (lines 7a-7c on the
    main form), which apportions Montana taxable income by the
    Montana-source ratio. Wages source to MT work location, rental
    to MT property, investment income to domicile. TODO(mt-form-2-nr)
    tracks this.

Graph-backend output-field gaps:
    - state_tax_bracket returns 0.0 (graph backend doesn't expose
      marginal rate for MT).
    - state_effective_tax_rate returns 0.0.
    - state_adjusted_gross_income echoes federal taxable income
      (NOT federal AGI) — see verify-step note above.
    - These are pinned in tests so any upstream tenforty fix
      (populating real values) trips CI.
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


# Tenforty backend used for the Montana calc. The OTS backend does not
# register MT_Form2 in NATURAL_FORM_CONFIG, so the default call path
# raises ``ValueError: OTS does not support 2025/MT_Form2``. The graph
# backend is the only working path. See module docstring.
_TENFORTY_BACKEND = "graph"


@dataclass(frozen=True)
class MontanaPlugin:
    """State plugin for Montana — TY2025.

    Wraps tenforty / OpenTaxSolver (graph backend) for Montana Form 2
    resident calculation, with day-based apportionment for nonresident
    / part-year filers.

    Montana applies a two-bracket structure (post Tax Simplification):
    4.7% on the lower bracket, 5.9% above. Single bracket break is
    $21,100; HoH $31,700; MFJ $42,200. Long-term capital gains use a
    separate preferential rate (3.0% / 4.1%).

    Reciprocity: Montana has one bilateral reciprocity agreement
    (with North Dakota — wages-only).
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Reuse federal marshaling so MT sees exactly what the federal
        # calc did.
        tf_input = _to_tenforty_input(return_)

        tf_result = tenforty.evaluate_return(
            year=tf_input.year,
            state="MT",
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

        # Apportion for nonresident / part-year (day-based v1).
        # TODO(mt-form-2-nr): replace with MT Form 2 Nonresident /
        # Part-Year Resident Schedule income-source apportionment in
        # fan-out.
        state_tax_apportioned = (
            state_tax_full
            if residency == ResidencyStatus.RESIDENT
            else day_prorate(state_tax_full, days_in_state)
        )
        if residency == ResidencyStatus.RESIDENT:
            fraction = Decimal("1")
        else:
            if days_in_state >= 365:
                fraction = Decimal("1")
            elif days_in_state <= 0:
                fraction = Decimal("0")
            else:
                fraction = Decimal(days_in_state) / Decimal("365")

        state_specific: dict[str, Any] = {
            "state_adjusted_gross_income": state_agi,
            "state_taxable_income": state_ti,
            "state_total_tax": state_tax_apportioned,
            "state_total_tax_resident_basis": state_tax_full,
            "state_tax_bracket": state_bracket,
            "state_effective_tax_rate": state_eff_rate,
            "apportionment_fraction": fraction,
            "starting_point": "federal_taxable_income",
            # Montana TY2025 ordinary income bracket constants
            "mt_lower_rate": Decimal("0.047"),
            "mt_upper_rate": Decimal("0.059"),
            "mt_bracket_break_single": Decimal("21100"),
            "mt_bracket_break_hoh": Decimal("31700"),
            "mt_bracket_break_mfj": Decimal("42200"),
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
        """Split canonical income into MT-source vs non-MT-source.

        Residents: everything is MT-source. Nonresident / part-year:
        prorate each category by days_in_state / 365.

        TODO(mt-form-2-nr): MT Form 2 Nonresident / Part-Year
        Resident Schedule sources income by type — wages to the
        Montana work location (with reciprocity exception for ND
        residents), rental to the Montana property, investment income
        to the taxpayer's domicile. Day-based proration is the shared
        first-cut across fan-out plugins.
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

        if residency == ResidencyStatus.RESIDENT:
            return IncomeApportionment(
                state_source_wages=cents(wages),
                state_source_interest=cents(interest),
                state_source_dividends=cents(ord_div),
                state_source_capital_gains=cents(capital_gains),
                state_source_self_employment=cents(se_net),
                state_source_rental=cents(rental_net),
            )

        return IncomeApportionment(
            state_source_wages=sourced_or_prorated_wages(return_, "MT", wages, days_in_state),
            state_source_interest=day_prorate(interest, days_in_state),
            state_source_dividends=day_prorate(ord_div, days_in_state),
            state_source_capital_gains=day_prorate(
                capital_gains, days_in_state
            ),
            state_source_self_employment=sourced_or_prorated_schedule_c(return_, "MT", se_net, days_in_state),
            state_source_rental=day_prorate(rental_net, days_in_state),
        )

    def render_pdfs(
        self, state_return: StateReturn, out_dir: Path
    ) -> list[Path]:
        # TODO(mt-pdf): fan-out follow-up — fill Montana Form 2
        # (and Schedules I, II, and the Nonresident/Part-Year
        # Resident Schedule) using pypdf against the MT DOR's
        # fillable PDFs.
        return []

    def form_ids(self) -> list[str]:
        return ["MT Form 2"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = MontanaPlugin(
    meta=StatePluginMeta(
        code="MT",
        name="Montana",
        has_income_tax=True,
        # Montana is one of the FEDERAL_TAXABLE_INCOME states
        # functionally — Form 2 imports federal AGI on line 1,
        # subtracts federal std/itemized on line 2, and line 3 =
        # federal taxable income is the Montana base.
        starting_point=StateStartingPoint.FEDERAL_TAXABLE_INCOME,
        dor_url="https://revenue.mt.gov/",
        # Montana TransAction Portal (TAP) — DOR's free direct
        # e-file portal for individual income tax.
        free_efile_url="https://tap.dor.mt.gov/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # Montana has ONE bilateral reciprocity agreement: with
        # North Dakota (ND). Verified against
        # skill/reference/state-reciprocity.json which contains
        # exactly {"states": ["MT", "ND"]}.
        reciprocity_partners=("ND",),
        supported_tax_years=(2025,),
        notes=(
            "Wraps tenforty/OpenTaxSolver (graph backend — MT is not "
            "on the OTS backend) for MT Form 2. Two-bracket ordinary "
            "income schedule per 2025 Montana Tax Tables: 4.7% up to "
            "$21,100 (Single) / $31,700 (HoH) / $42,200 (MFJ); 5.9% "
            "above. Long-term capital gains preferential 3.0%/4.1% "
            "schedule. Starting point: federal taxable income (Form "
            "2 line 3 = AGI - federal std/itemized). Reciprocity: "
            "ND only (wages-only bilateral agreement). $65k Single "
            "hand calc and graph backend exact match: $2,652.55."
        ),
    )
)
