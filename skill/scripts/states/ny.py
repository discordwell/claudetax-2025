"""New York state plugin.

NY is one of the 10 states tenforty (OpenTaxSolver) computes natively. This
plugin wraps tenforty's state calc, following the CA pattern: marshal the
canonical return via the shared `_to_tenforty_input` helper, call
`tenforty.evaluate_return(..., state='NY')`, and unpack the `state_*` floats
into Decimal on `StateReturn.state_specific`.

Scope (v0.1):
- Resident full-year NY taxpayers get an authoritative state tax via OTS.
- Nonresidents and part-year residents get a days-based proration of the
  full-year-equivalent state tax. This is a stopgap — the correct treatment
  is Form IT-203 (Nonresident / Part-Year) which uses NY-source income ratios
  rather than day counts. See the TODO in compute() and form_ids().
- PDF rendering is deferred (TODO) until the state PDF fill module lands.

Reciprocity: NY has NO bilateral reciprocity agreements with any state
(verified in skill/reference/state-reciprocity.json). This is important: a
NJ resident who works in NY must file a NY nonresident return, unlike a PA
resident who works in NJ (PA<->NJ is a reciprocity pair).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

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


def _d(v: object) -> Decimal:
    """Coerce a tenforty float (or None) to Decimal deterministically."""
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


@dataclass(frozen=True)
class NewYorkPlugin:
    """StatePlugin implementation for New York.

    Stateless and frozen — the same instance is safe to call across many
    taxpayers. All mutable state lives on the returned StateReturn.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        """Compute the NY state return via tenforty.

        Strategy:
          1. Marshal the canonical return into tenforty kwargs using the same
             `_to_tenforty_input` the federal calc engine uses — keeps NY's
             inputs byte-identical with the federal side.
          2. Call `tenforty.evaluate_return(..., state='NY')`.
          3. Wrap the `state_*` floats as Decimals on state_specific.
          4. For NONRESIDENT / PART_YEAR, prorate the state tax by
             days_in_state/365 as a v0.1 approximation. TODO: replace with
             IT-203 NY-source income ratio for correctness.
        """
        tf_input = _to_tenforty_input(return_)
        tf_result = tenforty.evaluate_return(
            year=tf_input.year,
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
            state="NY",
        )

        full_year_state_tax = _d(getattr(tf_result, "state_total_tax", 0))
        state_agi = _d(getattr(tf_result, "state_adjusted_gross_income", 0))
        state_ti = _d(getattr(tf_result, "state_taxable_income", 0))
        state_bracket = _d(getattr(tf_result, "state_tax_bracket", 0))
        state_effective_rate = _d(getattr(tf_result, "state_effective_tax_rate", 0))

        if residency in (ResidencyStatus.NONRESIDENT, ResidencyStatus.PART_YEAR):
            # TODO(ny-it203): This days-based proration is a v0.1 stopgap.
            # The correct NY nonresident / part-year calculation is Form
            # IT-203, which computes tax on the full federal AGI as if a
            # resident and then multiplies by the NY-source-income ratio
            # (NY income / federal income), not a days ratio. Replace once
            # we add per-state NY-source sourcing logic to apportion_income.
            proration = Decimal(days_in_state) / Decimal("365")
            state_tax = (full_year_state_tax * proration).quantize(Decimal("0.01"))
        else:
            state_tax = full_year_state_tax.quantize(Decimal("0.01"))

        return StateReturn(
            state=self.meta.code,
            residency=residency,
            days_in_state=days_in_state,
            state_specific={
                "state_tax": state_tax,
                "state_adjusted_gross_income": state_agi,
                "state_taxable_income": state_ti,
                "state_tax_bracket": state_bracket,
                "state_effective_tax_rate": state_effective_rate,
                "full_year_state_tax": full_year_state_tax,
                "engine": "tenforty/OpenTaxSolver",
            },
        )

    def apportion_income(
        self,
        return_: CanonicalReturn,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> IncomeApportionment:
        """Days-based income apportionment.

        For RESIDENT, 100% of every income category is NY-source. For
        NONRESIDENT / PART_YEAR, each category is prorated by
        days_in_state/365.

        TODO(ny-it203): swap days-based proration for proper NY-source
        sourcing (wages sourced by work location, investment income sourced
        by domicile, etc.) to match IT-203 expectations.
        """
        if residency == ResidencyStatus.RESIDENT:
            factor = Decimal("1")
        else:
            factor = Decimal(days_in_state) / Decimal("365")

        wages = sum((w2.box1_wages for w2 in return_.w2s), start=Decimal("0"))
        interest = sum(
            (f.box1_interest_income for f in return_.forms_1099_int),
            start=Decimal("0"),
        )
        ord_div = sum(
            (f.box1a_ordinary_dividends for f in return_.forms_1099_div),
            start=Decimal("0"),
        )

        st_gains = Decimal("0")
        lt_gains = Decimal("0")
        for form in return_.forms_1099_b:
            for txn in form.transactions:
                gain = txn.proceeds - txn.cost_basis + txn.adjustment_amount
                if txn.is_long_term:
                    lt_gains += gain
                else:
                    st_gains += gain
        cap_gain_distr = sum(
            (f.box2a_total_capital_gain_distributions for f in return_.forms_1099_div),
            start=Decimal("0"),
        )
        capital_gains = st_gains + lt_gains + cap_gain_distr

        # Schedule C net profit sum — done the long way to avoid pulling in
        # schedule_c_net_profit (which lives in calc.engine) twice; we let
        # the calc engine own that math. For apportionment we approximate by
        # summing gross receipts minus the top-level total. If schedules_c
        # is empty, this contributes zero.
        from skill.scripts.calc.engine import schedule_c_net_profit, schedule_e_total_net

        se_income = sum(
            (schedule_c_net_profit(sc) for sc in return_.schedules_c),
            start=Decimal("0"),
        )
        rental = sum(
            (schedule_e_total_net(sched) for sched in return_.schedules_e),
            start=Decimal("0"),
        )

        return IncomeApportionment(
            state_source_wages=wages * factor,
            state_source_interest=interest * factor,
            state_source_dividends=ord_div * factor,
            state_source_capital_gains=capital_gains * factor,
            state_source_self_employment=se_income * factor,
            state_source_rental=rental * factor,
        )

    def render_pdfs(self, state_return: StateReturn, out_dir: Path) -> list[Path]:
        """Render NY state PDFs.

        TODO: wire this up to the state PDF fill module once that lands in
        fan-out. For v0.1 we return an empty list; the paper bundle / output
        layer knows to skip states that produce no PDFs.
        """
        return []

    def form_ids(self) -> list[str]:
        """Return canonical NY form identifiers.

        v0.1 ships only the resident form IT-201. IT-203 (Nonresident /
        Part-Year) is a future refinement tracked by the TODO in compute()
        and apportion_income(); when it lands, this method should return
        IT-201 for residents and IT-203 for nonresident / part-year.
        """
        return ["NY Form IT-201"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = NewYorkPlugin(
    meta=StatePluginMeta(
        code="NY",
        name="New York",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://www.tax.ny.gov/",
        free_efile_url="https://www.tax.ny.gov/pit/efile/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        reciprocity_partners=(),  # NY has no reciprocity agreements.
        supported_tax_years=(2025,),
        notes="Uses tenforty/OpenTaxSolver for NY state calc.",
    )
)
