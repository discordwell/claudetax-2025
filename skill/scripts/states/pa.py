"""Pennsylvania state plugin.

PA is the odd one out among the ten states tenforty supports. Every other
state in that list conforms (to some degree) to the federal AGI or federal
taxable-income starting point. PA does not: the PA-40 personal income tax
begins from eight enumerated classes of income and taxes the combined total
at a flat 3.07%. The eight classes are:

    1. Compensation
    2. Interest
    3. Dividends
    4. Net profits from the operation of a business, profession, or farm
    5. Net gains or income from disposition of property
    6. Net gains or income from rents, royalties, patents, copyrights
    7. Income from estates or trusts
    8. Gambling and lottery winnings

No federal standard or itemized deduction reduces PA taxable income, and
PA does not follow federal adjustments to income. This plugin's metadata
advertises that distinction via `starting_point=PA_COMPENSATION_BASE` so the
engine dispatcher does not try to feed PA a federal-AGI-derived number.

Why we still call tenforty: tenforty encapsulates the PA-40 class mapping
inside its OpenTaxSolver wrapper. Feeding it the canonical fields
(w2_income, taxable_interest, ordinary_dividends, etc.) lets it do the
class-1-through-8 mapping itself. Verified empirically for TY2025:

    tenforty.evaluate_return(
        year=2025, state='PA', filing_status='Single',
        w2_income=65000, standard_or_itemized='Standard',
    )
    -> state_total_tax=1995.5, state_taxable_income=65000.0,
       state_adjusted_gross_income=65000.0

which is exactly 3.07% * 65,000 = $1,995.50 — the PA-40 line 12 answer.

Nonresident / part-year handling is day-proration for now. A full
nonresident PA return uses PA Schedule NRH (Apportionment of Business,
Profession, or Farm Income) plus class-8 sourcing on the PA-40 itself;
that is fan-out follow-up work.

Reciprocity: PA has bilateral reciprocity with six states — IN, MD, NJ,
OH, VA, WV (per skill/reference/state-reciprocity.json). A PA resident
working in any of those states pays PA tax only; conversely, a resident
of any of those six working in PA pays their home state only (with a
REV-419 filed to the employer). NJ/PA and MD/PA are the two commuter-
heavy corridors this plugin has to handle correctly once the full
multi-state dispatcher lands.
"""
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
from skill.scripts.states._hand_rolled_base import (
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


_CENTS = Decimal("0.01")


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

    Residents get 1.0 (full state tax). Nonresidents and part-year residents
    are prorated by days_in_state / 365. Clamped to [0, 1].

    TODO: a real nonresident PA calculation uses PA Schedule NRH for
    business/profession/farm income and applies PA's class-specific sourcing
    rules (compensation sourced to work location, rents/royalties to the
    property state, gambling winnings to the event state, etc.). Day-based
    proration is a first-order approximation; fan-out will tighten this with
    the real PA-40 NR logic.
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
class PennsylvaniaPlugin:
    """State plugin for Pennsylvania.

    Wraps tenforty/OpenTaxSolver for the resident case and day-proration for
    nonresident / part-year. Starting point is PA's own 8-class income base
    (not federal AGI) and the rate is a flat 3.07%.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Reuse the federal marshaling so PA sees exactly the same numbers
        # the federal calc did. tenforty maps w2_income to PA class 1,
        # taxable_interest to class 2, ordinary_dividends to class 3, etc.
        tf_input = _to_tenforty_input(return_)

        tf_result = tenforty.evaluate_return(
            year=tf_input.year,
            state="PA",
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
        )

        state_agi = _cents(tf_result.state_adjusted_gross_income)
        state_ti = _cents(tf_result.state_taxable_income)
        state_tax_full = _cents(tf_result.state_total_tax)
        # Bracket and effective rate are percentages — keep as Decimal (not
        # cents) so small rates stay precise. PA's flat rate means tenforty
        # often reports 0.0 for the marginal bracket field; that's fine.
        state_bracket = _d(tf_result.state_tax_bracket)
        state_eff_rate = _d(tf_result.state_effective_tax_rate)

        # Wave 6: real PA-40 NR class-1 (compensation) sourcing. When
        # at least one W-2 state row carries PA, compute PA tax on the
        # PA-sourced wages directly (flat 3.07% on the sourced sum plus
        # sourced Schedule C net). Otherwise fall back to day-proration
        # of the resident-basis tax.
        pa_state_rows_present = state_has_w2_state_rows(return_, "PA")
        pa_sourced_wages = state_source_wages_from_w2s(return_, "PA")
        pa_sourced_se = state_source_schedule_c(return_, "PA")

        if residency == ResidencyStatus.RESIDENT:
            fraction = Decimal("1")
            state_tax_apportioned = state_tax_full
        elif pa_state_rows_present:
            # PA's flat rate applies directly to the sourced amount.
            # (PA has no standard deduction on compensation; class 1
            # is the gross amount.) Include sourced SE (class 4) as
            # a bonus when the business_location_state matches.
            pa_base = pa_sourced_wages + pa_sourced_se
            state_tax_apportioned = _cents(pa_base * Decimal("0.0307"))
            fraction = Decimal("1")  # no day-proration under sourcing
        else:
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
            "pa_sourced_wages_from_w2_state_rows": pa_sourced_wages,
            "pa_sourced_schedule_c_net": pa_sourced_se,
            "pa_state_rows_present": pa_state_rows_present,
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
        """Split canonical income into PA-source vs non-PA-source.

        Residents: everything is PA-source. Nonresident / part-year: prorate
        each category by days_in_state / 365.

        TODO: PA actually sources each class differently — compensation to
        the work location, rents/royalties to the property state, gambling
        winnings to the event state, and so on. Day-based proration is the
        shared first-cut across all fan-out state plugins; refine in
        follow-up with the real PA-40 NR sourcing rules.
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

        # Wave 6: PA class-1 (compensation) sourcing prefers W-2 state
        # rows when the filer is not a PA resident.
        if residency == ResidencyStatus.RESIDENT:
            pa_wages = _cents(wages)
            pa_se = _cents(se_net)
        elif state_has_w2_state_rows(return_, "PA"):
            pa_wages = state_source_wages_from_w2s(return_, "PA")
            pa_se = state_source_schedule_c(return_, "PA")
        else:
            pa_wages = _cents(wages * fraction)
            pa_se = _cents(se_net * fraction)

        return IncomeApportionment(
            state_source_wages=pa_wages,
            state_source_interest=_cents(interest * fraction),
            state_source_dividends=_cents(ord_div * fraction),
            state_source_capital_gains=_cents(capital_gains * fraction),
            state_source_self_employment=pa_se,
            state_source_rental=_cents(rental_net * fraction),
        )

    def render_pdfs(
        self, state_return: StateReturn, out_dir: Path
    ) -> list[Path]:
        # PA-40 fillable PDF is not available for automated download.
        # The PA DOR website (revenue.pa.gov) returns HTML portal pages
        # instead of PDFs for all PA-40 URLs tested, including:
        #   - https://www.revenue.pa.gov/.../2025/2025_pa-40.pdf
        #   - https://www.revenue.pa.gov/.../pa-40.pdf
        #   - https://www.revenue.pa.gov/.../pa-40in.pdf
        # The site appears to require JavaScript/portal navigation to
        # access the actual PDF. Other PA forms (e.g. rev-276) do serve
        # directly. Until the PA-40 PDF can be obtained, render_pdfs()
        # returns []. The compute() method correctly produces state_specific
        # data for downstream consumers (e.g. the myPATH e-file portal).
        return []

    def form_ids(self) -> list[str]:
        return ["PA Form PA-40"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = PennsylvaniaPlugin(
    meta=StatePluginMeta(
        code="PA",
        name="Pennsylvania",
        has_income_tax=True,
        starting_point=StateStartingPoint.PA_COMPENSATION_BASE,
        dor_url="https://www.revenue.pa.gov/",
        free_efile_url="https://mypath.pa.gov/_/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        reciprocity_partners=("IN", "MD", "NJ", "OH", "VA", "WV"),
        supported_tax_years=(2025,),
        notes=(
            "PA uses 8 income classes (no federal AGI conformity) and a "
            "flat 3.07% rate. tenforty handles the class mapping internally. "
            "Reciprocity with 6 states including NJ and MD."
        ),
    )
)
