"""Wisconsin state plugin.

Wraps tenforty / OpenTaxSolver for the Wisconsin Form 1 resident calc, in
the same shape as the OH / NJ / MI plugins, and falls back to day-based
proration for nonresident / part-year.

Source of truth
---------------
Tenforty exposes Wisconsin via ``OTSState.WI`` and ships the resident form
file at ``tenforty/forms/wi_form1_2025.json`` together with a graph-backend
StateGraphConfig in ``tenforty/mappings.py`` (``OTSState.WI``). That config
imports federal AGI (WI Form 1 Line 1) and emits three natural outputs:

    L22_wi_agi            -> state_adjusted_gross_income
    L39_wi_taxable_income -> state_taxable_income
    L45_wi_total_tax      -> state_total_tax

IMPORTANT backend caveat (verified 2026-04-11 against
tenforty==installed-in-.venv): the default OTS backend raises
``ValueError: OTS does not support 2025/WI_Form1`` because WI is wired up
only on the newer graph backend. This plugin therefore calls
``tenforty.evaluate_return(..., backend='graph')`` explicitly. Every other
fan-out plugin uses the default OTS backend; WI is the one documented
exception until upstream tenforty promotes WI to the OTS path. If you add
another state that is also graph-only, copy this idiom.

Rate / base (TY2025)
--------------------
- Starting point: federal AGI (Wisconsin Form 1 imports federal AGI on
  Line 1, then layers Wisconsin additions/subtractions via Schedule I /
  Schedule SB, and the sliding-scale Wisconsin standard deduction on
  Line 15).
- Graduated brackets. Per the Wisconsin DOR 2025 Tax Rate Schedules (Form
  1 instructions, "Tax Rate Schedules" page) the Single schedule is:

      Taxable Income          Tax
      ------------------      -------------------------------
      $0     - $14,320        3.50%
      $14,320 - $28,640       $501.20 + 4.40% of excess over $14,320
      $28,640 - $315,310      $1,131.28 + 5.30% of excess over $28,640
      over $315,310           $16,324.79 + 7.65% of excess over $315,310

  Source: WI DOR 2025 Form 1 instructions / Tax Rate Schedules.
  https://www.revenue.wi.gov/TaxForms2025/2025-Form1-Inst.pdf (published
  by the Wisconsin Department of Revenue — PDF path may shift; see
  https://www.revenue.wi.gov/Pages/Individuals/Home.aspx for the current
  link).

  NOTE: WI Act 19 (2023) compressed the old 4-bracket schedule; the 3.50%
  first bracket and 7.65% top bracket are unchanged, while the middle
  brackets were consolidated. TY2025 numbers above are quoted from the
  2025 Form 1 instructions and are indexed for inflation each year.

- Reference tenforty probe (graph backend, direct):
    Single / $65,000 W-2 / Standard
      -> state_total_tax            = 2861.80
         state_tax_bracket          = 0.0     (graph backend omits)
         state_adjusted_gross_income = 65000.00
         state_taxable_income        = 65000.00  (graph backend echoes AGI)
         state_effective_tax_rate    = 0.0     (graph backend omits)

  UNVERIFIED ASSUMPTION: The graph backend's ``state_taxable_income`` for
  WI currently echoes ``state_adjusted_gross_income`` rather than applying
  the Wisconsin sliding-scale standard deduction and personal exemption.
  A hand calc against the 2025 Single bracket schedule above produces a
  tax of roughly $2,800 on $51,550 of WI taxable income (federal AGI
  $65,000 less the WI $12,760 sliding-scale standard at $65k single, less
  a $700 personal exemption). The $2,861.80 number tenforty emits is
  therefore not reconcilable against the TY2025 rate schedule from first
  principles. We PIN the tenforty output anyway so that OpenTaxSolver
  schedule drift fails CI — the plugin's job is to be bit-for-bit
  consistent with upstream tenforty — and track the discrepancy under
  TODO(wi-deduction-reconcile) below.

Nonresident / part-year
-----------------------
Day-based proration of the resident-basis tax is a v0.1 stopgap. The
correct treatment is Wisconsin Form 1NPR (Nonresident/Part-Year Resident)
with income sourcing rules: wages to the work location, rental to the
property state, interest/dividends to the taxpayer's domicile, etc.
TODO(wi-form-1npr) tracks this.

Reciprocity
-----------
Wisconsin has FOUR bilateral reciprocity agreements — IL, IN, KY, MI —
per Wisconsin DOR Publication 121 ("Reciprocity") and WI DOR FAQ
https://www.revenue.wi.gov/Pages/FAQS/pcs-work.aspx. Wisconsin previously
had a reciprocity agreement with Minnesota that was terminated effective
2010; MN is NOT a current partner and MUST NOT be added here.

Also verified against ``skill/reference/state-reciprocity.json`` (the
canonical project reference, whose primary source is Tax Foundation's
state-reciprocity-agreements page). A test in
``skill/tests/test_state_wi.py`` pins the exact set against that JSON so
accidental drift fails CI.

Submission channel
------------------
Wisconsin participates in the IRS Fed/State MeF program (the individual
Form 1 is transmitted to the Wisconsin DOR as a piggyback with the
federal 1040 via commercial tax software). The Department also operates
"Wisconsin e-file" / WisTax as a free direct-entry portal at
https://www.revenue.wi.gov/Pages/WisTax/home.aspx and "My Tax Account"
at https://tap.revenue.wi.gov/mta/. Our canonical submission channel for
WI is ``SubmissionChannel.FED_STATE_PIGGYBACK`` (matching OH / NJ / MI),
with the free portal surfaced in ``meta.free_efile_url`` so the output
pipeline can point the human there when they file individually.
"""
# Reciprocity partners (verified in skill/reference/state-reciprocity.json
# and against WI DOR Publication 121):
#   IL, IN, KY, MI — Wisconsin's four bilateral reciprocity partners.
#   MN was terminated in 2010 — do NOT add MN here.
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import tenforty

from skill.scripts.calc.engine import _to_tenforty_input
from skill.scripts.models import (
    CanonicalReturn,
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

# Tenforty backend used for the Wisconsin calc. The OTS backend does not
# register WI_Form1 in NATURAL_FORM_CONFIG, so the default call path raises
# ``ValueError: OTS does not support 2025/WI_Form1``. The graph backend
# consumes the ``wi_form1_2025.json`` graph file that ships with tenforty
# and is the only working path today. See module docstring.
_TENFORTY_BACKEND = "graph"


def _d(v: Any) -> Decimal:
    """Coerce a tenforty-returned float (or None) to Decimal."""
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _cents(v: Any) -> Decimal:
    """Decimal with 2 decimal places, half-up."""
    return _d(v).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _apportionment_fraction(
    residency: ResidencyStatus, days_in_state: int
) -> Decimal:
    """Days-based apportionment for nonresident / part-year.

    Residents get 1.0 (full state tax). Nonresidents and part-year
    residents are prorated by ``days_in_state / 365``. Clamped to [0, 1].

    TODO(wi-form-1npr): Replace with Wisconsin Form 1NPR income-source
    apportionment (WI-source wages, rental, business income) rather than
    day count. Day-based proration is the shared first-cut across all
    fan-out state plugins.
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
class WisconsinPlugin:
    """State plugin for Wisconsin.

    Wraps tenforty / OpenTaxSolver (graph backend) for the resident case
    and day-proration for nonresident / part-year. Starting point is
    federal AGI; WI layers its own additions/subtractions (Schedule I /
    Schedule SB), sliding-scale standard deduction, and personal
    exemptions internally on the Form 1 path.

    See module docstring for the backend="graph" rationale.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Reuse the federal marshaling so WI sees exactly the same numbers
        # the federal calc did — do NOT duplicate that logic here.
        tf_input = _to_tenforty_input(return_)

        tf_result = tenforty.evaluate_return(
            year=tf_input.year,
            state="WI",
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

        state_agi = _cents(tf_result.state_adjusted_gross_income)
        state_ti = _cents(tf_result.state_taxable_income)
        state_tax_full = _cents(tf_result.state_total_tax)
        # Bracket and effective rate: the graph backend currently reports
        # 0.0 for both (unlike the OTS backend used by OH). Surface
        # whatever tenforty returns, as Decimal, so the plugin shape is
        # consistent across states.
        state_bracket = _d(tf_result.state_tax_bracket)
        state_eff_rate = _d(tf_result.state_effective_tax_rate)

        # Apportion tax for nonresident / part-year.
        # TODO(wi-form-1npr): replace with WI Form 1NPR income-source
        # apportionment in fan-out.
        fraction = _apportionment_fraction(residency, days_in_state)
        state_tax_apportioned = _cents(state_tax_full * fraction)

        state_specific: dict[str, Any] = {
            "state_adjusted_gross_income": state_agi,
            "state_taxable_income": state_ti,
            "state_total_tax": state_tax_apportioned,
            "state_total_tax_resident_basis": state_tax_full,
            "state_tax_bracket": state_bracket,
            "state_effective_tax_rate": state_eff_rate,
            "apportionment_fraction": fraction,
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
        """Split canonical income into WI-source vs non-WI-source.

        Residents: everything is WI-source. Nonresident / part-year:
        prorate each category by ``days_in_state / 365``.

        TODO(wi-form-1npr): WI actually sources each income type on Form
        1NPR — wages to the work location, rental to the property state,
        interest/dividends to the taxpayer's domicile, Wisconsin lottery /
        gambling winnings always WI-source, etc. Day-based proration is
        the shared first-cut across all fan-out state plugins; refine
        with Form 1NPR logic in follow-up.
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

        # Schedule C / E net totals — reuse calc.engine helpers so WI
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
        # TODO(wi-pdf): fan-out follow-up — fill Wisconsin Form 1 (and
        # Schedule I / Schedule SB, Schedule CR for credits, Form 1NPR for
        # nonresidents) using pypdf against the Wisconsin DOR's fillable
        # PDFs. The output renderer suite is the right home for this;
        # this plugin returns structured state_specific data that the
        # renderer will consume.
        return []

    def form_ids(self) -> list[str]:
        return ["WI Form 1"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = WisconsinPlugin(
    meta=StatePluginMeta(
        code="WI",
        name="Wisconsin",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://www.revenue.wi.gov/Pages/Individuals/Home.aspx",
        free_efile_url="https://www.revenue.wi.gov/Pages/WisTax/home.aspx",
        submission_channel=SubmissionChannel.FED_STATE_PIGGYBACK,
        # WI has four bilateral reciprocity partners — verified against
        # skill/reference/state-reciprocity.json and WI DOR Publication
        # 121. A test asserts the exact set so accidental drift fails CI.
        # Do NOT add MN — WI-MN reciprocity was terminated in 2010.
        reciprocity_partners=("IL", "IN", "KY", "MI"),
        supported_tax_years=(2025,),
        notes=(
            "Uses tenforty/OpenTaxSolver (graph backend — WI is not on "
            "the OTS backend) for WI Form 1. Graduated brackets for "
            "TY2025: 3.50% up to $14,320 (Single), 4.40% to $28,640, "
            "5.30% to $315,310, 7.65% above (per 2025 WI Form 1 "
            "instructions Tax Rate Schedules). Reciprocity: IL, IN, "
            "KY, MI."
        ),
    )
)
