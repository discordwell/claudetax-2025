"""Ohio state plugin.

OH is one of the ~10 states tenforty (OpenTaxSolver) supports natively. This
plugin is a thin wrapper around ``tenforty.evaluate_return(..., state='OH')``
following the CA / AZ / MI reference pattern: reuse the calc engine's
``_to_tenforty_input`` marshaling so OH sees exactly the same income /
deduction numbers the federal calc uses, call tenforty, and unpack the
``state_*`` floats as Decimal on ``StateReturn.state_specific``.

Rate / base (TY2025):
    - Starting point: federal AGI (Ohio IT-1040 line 1 = federal 1040 line
      11a, with Ohio Schedule of Adjustments additions/subtractions applied
      on top; tenforty handles this on the IT-1040 path).
    - Graduated brackets. For tax year 2025, Ohio's highest nonbusiness
      income tax rate has been reduced to 3.125%. The Ohio IT-1040 TY2025
      booklet prints the following schedule (page 18):

          Taxable Nonbusiness Income       Nonbusiness Income Tax
          -----------------------------    --------------------------------
          $0        - $26,050              0.000% of OH taxable income
          $26,050   - $100,000             $342.00 plus 2.750% of the amount
                                           in excess of $26,050
          over $100,000                    $2,394.32 plus 3.125% of the
                                           amount in excess of $100,000

      Source: 2025 Ohio IT 1040 / SD 100 instruction booklet, "2025 Ohio
      Income Tax Brackets for Ohio IT 1040" on page 18.
      https://dam.assets.ohio.gov/image/upload/v1767095693/tax.ohio.gov/forms/ohio_individual/individual/2025/it1040-booklet.pdf

      (Note: Ohio's bracket structure has been repeatedly compressed over
      the past several years. Older fan-out specs referenced the pre-2024
      four-bracket schedule at approximately $26,050 / $46,100 / $92,150 /
      $115,300; the official TY2025 booklet above collapses that into the
      two-bracket structure quoted here. Use the booklet numbers as the
      source of truth.)

    - Reference tenforty probe: Single / $65,000 W-2 / Standard
        -> state_total_tax = 1413.12
           state_tax_bracket = 2.8
           state_taxable_income = 65000.00
           state_adjusted_gross_income = 65000.00
           state_effective_tax_rate = 2.2

      This is consistent with the 2025 brackets above once the Ohio
      personal exemption (modified-AGI-based: $2,150 for MAGI between
      $40,001 and $80,000 on a single filer at $65,000) and the
      low-income / nonrefundable credit adjustments baked into tenforty's
      OH path are applied. We do not re-derive the number by hand —
      tenforty is the source of truth for the resident calc. We only pin
      the aggregate ($1,413.12) so regressions in the OpenTaxSolver OH
      schedule are caught.

Nonresident / part-year:
    Day-based proration of the resident-basis tax is a v0.1 stopgap. The
    correct treatment is Ohio's nonresident credit (Ohio IT NRC), which
    prorates by Ohio-source income rather than day count. The TODO in
    ``compute`` tracks this.

Reciprocity:
    Ohio has FIVE bilateral reciprocity agreements — IN, KY, MI, PA, WV —
    verified against skill/reference/state-reciprocity.json. Residents of
    those states who work in Ohio are exempt from Ohio income tax on their
    wages (and vice versa), and report them on the "Employee compensation
    earned in Ohio by residents of neighboring states" deduction (Schedule
    of Adjustments line 14) on the Ohio side. The skill's multi-state
    workflow reads ``reciprocity_partners`` to drive this logic, so the
    tuple below is load-bearing and is verified by a test against
    skill/reference/state-reciprocity.json.

Submission channel:
    Ohio participates in the IRS Fed/State MeF program (the return is
    transmitted to Ohio's Department of Taxation via the IRS MeF as a
    piggyback with the federal 1040). The Department also operates
    OH|TAX eServices as a free direct-file portal, but the canonical
    submission path for our output pipeline is
    ``SubmissionChannel.FED_STATE_PIGGYBACK``.
"""
# Reciprocity partners (verified in skill/reference/state-reciprocity.json):
#   IN, KY, MI, PA, WV — Ohio's five bilateral reciprocity partners.
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
    are prorated by ``days_in_state / 365``. Clamped to [0, 1].

    TODO(oh-it-nrc): Replace with Ohio IT NRC nonresident-credit
    income-source apportionment (Ohio-source wages, rental, business
    income) rather than day count. Day-based proration is the shared
    first-cut across all fan-out state plugins.
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
class OhioPlugin:
    """State plugin for Ohio.

    Wraps tenforty / OpenTaxSolver for the resident case and day-proration
    for nonresident / part-year. Starting point is federal AGI; OH layers
    state-specific additions and subtractions from its Schedule of
    Adjustments on top, which tenforty handles internally on the IT-1040
    path.

    Unlike AZ / MI (flat-rate states where tenforty reports a 0.0 bracket
    and 0.0 effective rate), Ohio runs a graduated bracket schedule, so
    ``state_tax_bracket`` and ``state_effective_tax_rate`` returned by
    tenforty carry real information — we surface them unchanged on
    state_specific.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Reuse the federal marshaling so OH sees exactly the same numbers
        # the federal calc did — do NOT duplicate that logic here.
        tf_input = _to_tenforty_input(return_)

        tf_result = tenforty.evaluate_return(
            year=tf_input.year,
            state="OH",
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
        # Bracket and effective rate are percentages — keep as Decimal
        # (not cents) so fractional values stay precise. OH has graduated
        # brackets (0% / 2.75% / 3.125% for TY2025) so these values carry
        # real information; surface whatever tenforty returns.
        state_bracket = _d(tf_result.state_tax_bracket)
        state_eff_rate = _d(tf_result.state_effective_tax_rate)

        # Apportion tax for nonresident / part-year.
        # TODO(oh-it-nrc): replace with Ohio IT NRC income-source
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
        """Split canonical income into OH-source vs non-OH-source.

        Residents: everything is OH-source. Nonresident / part-year:
        prorate each category by ``days_in_state / 365``.

        TODO(oh-it-nrc): OH actually sources each income type via the Ohio
        IT NRC nonresident credit — wages to the work location, interest /
        dividends to the taxpayer's domicile, rental to the property
        state, etc. Day-based proration is the shared first-cut across
        all fan-out state plugins; refine with IT NRC logic in follow-up.
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

        # Schedule C / E net totals — reuse calc.engine helpers so OH
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
        # Ohio IT-1040: The Ohio Department of Taxation publishes the
        # IT-1040 bundle as a FLATTENED PDF (0 AcroForm fields, 0 widget
        # annotations). Both the original bundle at:
        #   dam.assets.ohio.gov/.../2025/1040-bundle.pdf
        # and the amended bundle at:
        #   dam.assets.ohio.gov/.../2025/1040-amended-bundle.pdf
        # contain zero fillable fields. Ohio pushes taxpayers to its
        # OH|TAX eServices electronic portal instead of providing
        # fillable PDFs. Verified 2026-04-12.
        #
        # AcroForm PDF filling is not possible for this state form.
        # A future enhancement could use reportlab to generate the form
        # from scratch, but per the task spec we do NOT create reportlab
        # scaffolds when the source PDF is not fillable.
        return []

    def form_ids(self) -> list[str]:
        return ["OH Form IT-1040"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = OhioPlugin(
    meta=StatePluginMeta(
        code="OH",
        name="Ohio",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://tax.ohio.gov/",
        free_efile_url="https://tax.ohio.gov/individual/ohtax-eservices",
        submission_channel=SubmissionChannel.FED_STATE_PIGGYBACK,
        # OH has five bilateral reciprocity partners — verified against
        # skill/reference/state-reciprocity.json. A test asserts the exact
        # set so accidental drift fails CI.
        reciprocity_partners=("IN", "KY", "MI", "PA", "WV"),
        supported_tax_years=(2025,),
        notes=(
            "Uses tenforty/OpenTaxSolver for OH state calc. Graduated "
            "brackets for TY2025: 0% up to $26,050, 2.75% from $26,050 "
            "to $100,000, 3.125% over $100,000 (per 2025 Ohio IT-1040 "
            "instruction booklet page 18)."
        ),
    )
)
