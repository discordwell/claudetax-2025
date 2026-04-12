"""Iowa (IA) state plugin — TY2025.

See skill/reference/tenforty-ty2025-gap.md for the TY2025 probe rubric
and the graph-backend output-field gap list (state_taxable_income
echo, state_tax_bracket=0, state_effective_tax_rate=0).

Decision: WRAP (graph backend)
------------------------------
Per the wave-5 probe-then-verify-then-decide rubric (CP8-B), we re-probed
tenforty for Iowa on the **graph backend** and cross-checked against an
independent hand calculation from Iowa Form IA 1040 (TY2025) primary
sources. The numbers agree exactly, so the plugin is a thin wrapper
around ``tenforty.evaluate_return(..., backend='graph', state='IA', ...)``
in the same shape as ``wi.py`` (the wave-4 graph-backend wrapper that
established this pattern).

Probe results (verified 2026-04-11 against the tenforty wheel pinned in
``.venv``):

    Single / $65,000 W-2 / Standard
      -> state_total_tax            = 1871.50  (locked in tests)
         state_adjusted_gross_income = 65000.00
         state_taxable_income        = 49250.00  (= AGI - $15,750)
         state_tax_bracket           = 0.0       (graph backend omits)
         state_effective_tax_rate    = 0.0       (graph backend omits)

    Default backend: ``ValueError: OTS does not support 2025/IA_IA1040``
    (this is the documented gap from
    ``skill/reference/tenforty-ty2025-gap.md``).

Iowa TY2025 rate / base — DOR primary source verification
---------------------------------------------------------
Iowa enacted **Senate File 2442 (2024)** ("the FY24 budget bill") which
ACCELERATED the income-tax phase-down originally set in **House File
2317 (2022)**. Effective for tax years beginning on or after January 1,
2025, Iowa Code §422.5 imposes a SINGLE FLAT RATE of **3.80%** on Iowa
taxable income for individuals (down from a previously scheduled multi-
bracket schedule with a 4.82% top rate).

  - Iowa Department of Revenue, "Tax Year 2025 Iowa Income Tax Rates"
    (https://tax.iowa.gov/tax-year-2025-iowa-income-tax-rates).
  - Iowa Code §422.5(1)(a) (as amended by SF 2442 §1).
  - Iowa DOR Form IA 1040 (2025), Step 6 ("Iowa Tax").
  - Tax Foundation, "Iowa Tax Cuts" summary (citing SF 2442).

Iowa Form IA 1040 line structure (TY2025):

    Line 1   Iowa filing status & exemptions header
    Line 2   Wages, salaries, tips (from federal AGI line 1)
    ...
    Line 26  Federal AGI (Iowa imports federal AGI as the TY2025
             starting point — the line numbering changed from prior
             years' "start from federal taxable income" treatment but
             the substance is identical when you include the federal
             standard deduction as a subtraction)
    Line 27  Iowa adjustments
    Line 28  Net Iowa income
    Line 29  Iowa standard deduction
              FOR TY2025 IOWA HAS CONFORMED ITS STANDARD DEDUCTION TO
              THE FEDERAL AMOUNTS (per SF 2442). The Iowa standard
              deduction by filing status equals the federal standard
              deduction:
                Single / MFS                   $15,750
                Head of Household              $23,625
                Married Filing Jointly         $31,500
                Qualifying Surviving Spouse    $31,500
              These are the OBBBA-adjusted TY2025 federal numbers (see
              ``skill/reference/ty2025-constants.json``). Iowa's pre-
              TY2025 separate standard-deduction schedule (~$2,210 /
              $5,450) is **gone** for TY2025 onward.
    Line 30  Iowa taxable income = Line 28 - Line 29 (floor 0)
    Line 31  Tax = 0.038 * Line 30 (single flat rate, all statuses)
    ...
    (No personal-exemption credit for TY2025 — eliminated by SF 2442
    along with the move to a flat rate. Prior years' $40 single / $80
    MFJ personal-exemption credit is GONE for TY2025.)

Hand calculation, Single $65,000 W-2 / Standard (TY2025):

    Federal AGI                 = $65,000.00
    Iowa adjustments            =      $0.00   (v1 — no Iowa-specific
                                                addbacks/subtractions
                                                modeled)
    Net Iowa income             = $65,000.00
    Iowa standard deduction     = $15,750.00   (Single, conformed to
                                                federal TY2025 OBBBA)
    Iowa taxable income         = $49,250.00
    Iowa tax (3.80% flat)       =  $1,871.50

This matches the graph-backend probe **bit-for-bit** ($1,871.50). We
therefore wrap the graph backend rather than hand-rolling — the math is
identical and we get future tenforty fixes for free. The exact-match
decision aligns this plugin with WI (also a graph-backend wrap) rather
than the KS / KY / MN / CT / MD wave-4 hand-rolls.

Cross-checked at additional incomes (also exact-match):
    $20,000 Single -> $161.50  ((20000-15750)*0.038)
    $40,000 Single -> $921.50  ((40000-15750)*0.038)
    $100,000 Single -> $3,201.50
    $100,000 MFJ -> $2,603.00  ((100000-31500)*0.038)
    $100,000 HOH -> $2,902.25  ((100000-23625)*0.038)
    $250,000 Single -> $8,901.50
    $500,000 Single -> $18,401.50

Reciprocity
-----------
Iowa has **exactly one** bilateral reciprocity agreement: with **Illinois
(IL)**. Per Iowa DOR Publication "Iowa-Illinois Reciprocal Agreement"
and verified against ``skill/reference/state-reciprocity.json`` (entry
``{"states": ["IL", "IA"]}``). Iowa residents working in Illinois pay
Iowa income tax only (and file Form IL-W-5-NR with their Illinois
employer to stop IL withholding). Likewise, Illinois residents working
in Iowa pay only Illinois income tax. Iowa has NO other reciprocity
agreements with neighboring states (NE, SD, MN, WI, MO).

  - Iowa DOR, "Iowa - Illinois Reciprocal Agreement" page on
    https://tax.iowa.gov.
  - Illinois DOR, "Illinois-Iowa Reciprocity" — IL Publication 130.

Submission channel
------------------
Iowa participates in the IRS Fed/State MeF program — the IA 1040 piggy-
backs on the federal 1040 transmission via commercial software / IRS
Authorized e-file Provider. The Iowa Department of Revenue also operates
a free direct-entry portal called "GovConnectIowa" at
https://tax.iowa.gov/govconnect, which accepts individual income-tax
returns (and many other Iowa tax types) without requiring commercial
software. Our canonical channel for IA is therefore
``SubmissionChannel.FED_STATE_PIGGYBACK`` (matching OH/NJ/MI/WI), with
the GovConnectIowa portal surfaced in ``meta.free_efile_url`` so the
output pipeline can point the human there when filing individually.

Nonresident / part-year handling
--------------------------------
v1 uses day-based proration (``days_in_state / 365``) of the resident-
basis tax. The real Iowa rule for nonresidents and part-year residents
is **Schedule IA 126** ("Iowa Nonresident and Part-Year Resident Credit
Schedule"), which computes the Iowa-source-income ratio and applies it
to the full-year resident tax. TODO(ia-schedule-126) tracks this.

Loud TODOs
----------
- TODO(ia-schedule-126): replace day-based proration with Schedule IA
  126 Iowa-source-income ratio for nonresident / part-year filers.
- TODO(ia-modifications): model Iowa-specific additions and subtractions
  on the IA Schedule 1 (e.g. Iowa 529 Plan contributions subtraction up
  to $4,028 per beneficiary, federal income-tax refund add-back, US
  bond interest subtraction, etc.). v1 treats Iowa adjustments as zero.
- TODO(ia-credits): model Iowa nonrefundable credits (Iowa Earned Income
  Tax Credit at 15% of federal EITC; Tuition and Textbook Credit at 25%
  of qualifying expenses; Volunteer Firefighter / EMS / Reserve Peace
  Officer credit; Adoption Tax Credit; etc.).
- TODO(ia-graph-backend-deduction-reconcile): the graph backend's
  ``state_taxable_income`` correctly subtracts the federal standard
  deduction by filing status (verified against probe), but the graph
  backend echoes the federal std-ded amounts internally rather than
  reading an Iowa-specific column from the form definition. If the
  Iowa DOR ever publishes a non-conformed Iowa standard deduction
  (e.g. an inflation-indexed bump above the federal amount), the
  graph backend will silently lag and this plugin will need to be
  pinned or re-rolled. The gatekeeper test in
  ``test_state_ia.py::TestTenfortyIaTy2025GraphBackendStable`` locks
  the current behavior so drift fails CI.
- TODO(ia-pdf): fan-out follow-up — fill IA 1040 (and IA 130, IA 126,
  IA Schedule 1) using pypdf against the IA DOR fillable PDFs.
"""
# Reciprocity partners (verified in skill/reference/state-reciprocity.json
# and against IA DOR's Iowa-Illinois Reciprocal Agreement publication):
#   IL — Iowa's only bilateral reciprocity partner.
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


# Tenforty backend used for the Iowa calc. The default OTS backend raises
# ``ValueError: OTS does not support 2025/IA_IA1040`` because Iowa is wired
# up only on the newer graph backend. This plugin therefore calls
# ``tenforty.evaluate_return(..., backend='graph')`` explicitly. See
# module docstring and skill/reference/tenforty-ty2025-gap.md for the
# enumerated tenforty support gap.
_TENFORTY_BACKEND = "graph"

# Iowa TY2025 flat rate. Per Iowa Code §422.5(1)(a) as amended by Senate
# File 2442 (2024), all individual filing statuses pay a single flat rate
# of 3.80% on Iowa taxable income for tax years beginning on or after
# January 1, 2025.
IA_TY2025_FLAT_RATE: Decimal = Decimal("0.038")


@dataclass(frozen=True)
class IowaPlugin:
    """State plugin for Iowa (TY2025).

    Wraps tenforty / OpenTaxSolver via the **graph backend** (the default
    OTS backend has no IA_IA1040 form config for TY2025) and cross-
    verifies against the hand calculation in this module's docstring.
    Starting point is federal AGI; Iowa applies the federal standard
    deduction (under SF 2442 conformity) and a single 3.80% flat rate.

    See module docstring for the wrap-vs-hand-roll decision rationale.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Reuse the federal marshaling so IA sees exactly the same numbers
        # the federal calc did — do NOT duplicate that logic here.
        tf_input = _to_tenforty_input(return_)

        tf_result = tenforty.evaluate_return(
            year=tf_input.year,
            state="IA",
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
        # Bracket and effective rate: the graph backend currently reports
        # 0.0 for both (same as the WI graph wrap). Surface whatever
        # tenforty returns, as Decimal, so the plugin shape is consistent
        # across states.
        state_bracket = d(tf_result.state_tax_bracket)
        state_eff_rate = d(tf_result.state_effective_tax_rate)

        # Cross-check the graph backend's number against the documented
        # flat-rate formula. We do NOT use this value as the canonical
        # output (the graph backend is the wrap target) — it is a
        # diagnostic so a future Iowa-DOR rate change is visible.
        ia_taxable_income_handcheck = max(
            Decimal("0"),
            state_agi - d(federal.federal_standard_deduction),
        )
        ia_tax_handcheck = cents(
            ia_taxable_income_handcheck * IA_TY2025_FLAT_RATE
        )

        # Apportion tax for nonresident / part-year. Day-based v1.
        # TODO(ia-schedule-126): replace with Schedule IA 126 Iowa-source-
        # income ratio.
        state_tax_apportioned = day_prorate(
            state_tax_full, days_in_state
        ) if residency != ResidencyStatus.RESIDENT else state_tax_full

        # When residency==RESIDENT we never short-circuit through
        # day_prorate, so quantize explicitly to ensure cent precision.
        state_tax_apportioned = cents(state_tax_apportioned)

        state_specific: dict[str, Any] = {
            "state_adjusted_gross_income": state_agi,
            "state_taxable_income": state_ti,
            "state_total_tax": state_tax_apportioned,
            "state_total_tax_resident_basis": state_tax_full,
            "state_tax_bracket": state_bracket,
            "state_effective_tax_rate": state_eff_rate,
            "apportionment_fraction": _apportionment_fraction_decimal(
                residency, days_in_state
            ),
            "starting_point": "federal_agi",
            "ia_flat_rate": IA_TY2025_FLAT_RATE,
            "ia_taxable_income_handcheck": ia_taxable_income_handcheck,
            "ia_tax_handcheck": ia_tax_handcheck,
            "ia_handcheck_matches_graph": (
                ia_tax_handcheck == state_tax_full
            ),
            "tenforty_backend": _TENFORTY_BACKEND,
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
        """Split canonical income into IA-source vs non-IA-source.

        Residents: everything is IA-source. Nonresident / part-year:
        prorate each category by ``days_in_state / 365``.

        TODO(ia-schedule-126): IA actually sources each income type on
        Schedule IA 126 — wages to the work location, rental to the
        property state, interest/dividends to the taxpayer's domicile,
        etc. Day-based proration is the shared first-cut across all
        fan-out state plugins; refine with the real IA 126 logic in
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

        # Schedule C / E net totals — reuse calc.engine helpers so IA
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
            state_source_wages=sourced_or_prorated_wages(return_, "IA", wages, days_in_state),
            state_source_interest=day_prorate(interest, days_in_state),
            state_source_dividends=day_prorate(ord_div, days_in_state),
            state_source_capital_gains=day_prorate(
                capital_gains, days_in_state
            ),
            state_source_self_employment=sourced_or_prorated_schedule_c(return_, "IA", se_net, days_in_state),
            state_source_rental=day_prorate(rental_net, days_in_state),
        )

    def render_pdfs(
        self, state_return: StateReturn, out_dir: Path
    ) -> list[Path]:
        # IA 1040: the Iowa DOR (revenue.iowa.gov, formerly tax.iowa.gov)
        # does not publish a standalone fillable IA 1040 PDF with AcroForm
        # widgets for TY2025. The PDF available via the DOR media endpoint
        # is a different form (composite return / power of attorney) and
        # the IA 1040 itself is only available through the GovConnectIowa
        # e-file portal at https://tax.iowa.gov/govconnect. Verified
        # 2026-04-12 by probing revenue.iowa.gov media IDs and checking
        # the resulting PDFs via pypdf PdfReader.get_fields().
        # TODO(ia-pdf): if Iowa DOR publishes a standalone fillable IA
        # 1040 with AcroForm widgets, implement AcroForm overlay here.
        return []

    def form_ids(self) -> list[str]:
        return ["IA 1040"]


def _apportionment_fraction_decimal(
    residency: ResidencyStatus, days_in_state: int
) -> Decimal:
    """Diagnostic apportionment fraction (Decimal, exact).

    The actual tax-side apportionment uses ``day_prorate`` from
    ``_hand_rolled_base`` (which quantizes to cents). This helper exists
    so ``state_specific["apportionment_fraction"]`` can carry the exact
    rational fraction for downstream introspection / output rendering.
    """
    if residency == ResidencyStatus.RESIDENT:
        return Decimal("1")
    if days_in_state <= 0:
        return Decimal("0")
    if days_in_state >= 365:
        return Decimal("1")
    return Decimal(days_in_state) / Decimal("365")


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = IowaPlugin(
    meta=StatePluginMeta(
        code="IA",
        name="Iowa",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://tax.iowa.gov/",
        # GovConnectIowa — the IA DOR's free direct-entry portal at
        # https://tax.iowa.gov/govconnect. Accepts individual income-tax
        # returns without commercial software.
        free_efile_url="https://tax.iowa.gov/govconnect",
        submission_channel=SubmissionChannel.FED_STATE_PIGGYBACK,
        # IA has exactly one bilateral reciprocity partner — IL — per
        # the IA DOR "Iowa - Illinois Reciprocal Agreement" page and
        # skill/reference/state-reciprocity.json. A test asserts the
        # exact set so accidental drift fails CI.
        reciprocity_partners=("IL",),
        supported_tax_years=(2025,),
        notes=(
            "Wraps tenforty/OpenTaxSolver (graph backend — IA is not "
            "on the OTS backend, raises 'OTS does not support "
            "2025/IA_IA1040'). Iowa Senate File 2442 (2024) "
            "ACCELERATED Iowa to a single FLAT RATE of 3.80% on Iowa "
            "taxable income for TY2025, applied to (federal AGI - "
            "federal standard deduction) under SF 2442 conformity. "
            "Iowa eliminated its separate standard deduction and "
            "personal exemption credit for TY2025. Reciprocity: IL "
            "(only). Free e-file via GovConnectIowa. Source: Iowa "
            "Code §422.5(1)(a) as amended by SF 2442; Iowa DOR Form "
            "IA 1040 (2025); tax.iowa.gov."
        ),
    )
)
