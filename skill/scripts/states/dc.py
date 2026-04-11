"""District of Columbia (DC) state plugin — TY2025.

DC has its own individual income tax and is NOT supported by tenforty, so this
module implements the calculation in-house. Two DC-specific rules this plugin
encodes:

1. **Universal nonresident exemption.** DC does not tax employees who are
   nonresidents of DC regardless of their home state. A nonresident working in
   DC files only with their home state. This stacks on top of the bilateral
   DC-MD and DC-VA reciprocity agreements. See
   ``skill/reference/state-reciprocity.json`` -> ``dc_nonresident_exemption``.

2. **Seven-bracket resident calculation.** TY2025 DC tax rates and brackets are
   the rates in effect for tax years beginning after 12/31/2021 (they did NOT
   change for TY2025). Sources:

   - DC OTR "Individual and Fiduciary Income Tax Rates":
     https://otr.cfo.dc.gov/page/dc-individual-and-fiduciary-income-tax-rates
   - DC OTR 2025 D-40ES Estimated Payment Booklet (Tax Rate Table, page 9):
     https://otr.cfo.dc.gov/sites/default/files/dc/sites/otr/publication/attachments/2025_D40ES_Booklet_121824.pdf

   The 2025 D-40ES worksheet is the authoritative TY2025 source. The brackets
   and standard deduction amounts below are transcribed directly from it.

DC standard deduction amounts (TY2025, from 2025 D-40ES worksheet line 2b):

   - Single / MFS / dependent:                     $15,000
   - Head of household:                            $22,500
   - MFJ, MFS-on-same-return, QSS w/ dependent:    $30,000

Residency handling:

   - RESIDENT: compute tax on (federal AGI - DC standard deduction) via the
     bracket table.
   - NONRESIDENT: state_tax = 0 (universal exemption).
   - PART_YEAR: days-based proration of the resident calculation
     (days_in_state / 365). See TODO below — DC Form D-40 Schedule S prefers
     income apportionment by period of residency, not a simple day ratio.

TODOs:
   - [ ] Proper part-year apportionment per DC D-40 Schedule S (income by period).
   - [ ] DC personal exemption / low-income credit (removed post-TCJA but DC
         still has refundable EITC and other credits not modeled here).
   - [ ] Additions/subtractions from federal AGI: out-of-state municipal bond
         interest addition, DC/federal bond interest subtraction, etc.
   - [ ] render_pdfs: actually fill Form D-40.
   - [ ] Verify every TY2025 citation against the final 2025 D-40 booklet
         (currently cross-referenced to the D-40ES booklet issued 12/18/2024).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

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
# TY2025 DC bracket schedule
# ---------------------------------------------------------------------------

# Source: DC OTR 2025 D-40ES Booklet, Tax Rate Table (page 9).
# https://otr.cfo.dc.gov/sites/default/files/dc/sites/otr/publication/attachments/2025_D40ES_Booklet_121824.pdf
# Also confirmed at https://otr.cfo.dc.gov/page/dc-individual-and-fiduciary-income-tax-rates
#
# Each tuple is (upper_bound_inclusive_or_None_for_top, base_tax, rate, floor).
# Base tax = tax on income up to `floor`, then (income - floor) * rate is added.
# Bracket structure is identical across filing statuses in DC.
DC_TY2025_BRACKETS: tuple[tuple[Decimal | None, Decimal, Decimal, Decimal], ...] = (
    # upper                  base_tax             rate              floor
    (Decimal("10000"),        Decimal("0"),        Decimal("0.04"),  Decimal("0")),
    (Decimal("40000"),        Decimal("400"),      Decimal("0.06"),  Decimal("10000")),
    (Decimal("60000"),        Decimal("2200"),     Decimal("0.065"), Decimal("40000")),
    (Decimal("250000"),       Decimal("3500"),     Decimal("0.085"), Decimal("60000")),
    (Decimal("500000"),       Decimal("19650"),    Decimal("0.0925"), Decimal("250000")),
    (Decimal("1000000"),      Decimal("42775"),    Decimal("0.0975"), Decimal("500000")),
    (None,                    Decimal("91525"),    Decimal("0.1075"), Decimal("1000000")),
)

# Source: 2025 D-40ES Worksheet line 2b (standard deduction by filing status).
DC_TY2025_STANDARD_DEDUCTION: dict[FilingStatus, Decimal] = {
    FilingStatus.SINGLE: Decimal("15000"),
    FilingStatus.MFS: Decimal("15000"),
    FilingStatus.HOH: Decimal("22500"),
    FilingStatus.MFJ: Decimal("30000"),
    # QSS with dependent child: D-40ES line 2b lumps with MFJ ($30,000).
    FilingStatus.QSS: Decimal("30000"),
}


def _dc_standard_deduction(filing_status: FilingStatus) -> Decimal:
    return DC_TY2025_STANDARD_DEDUCTION[filing_status]


def _dc_bracket_tax(taxable_income: Decimal) -> Decimal:
    """Apply the TY2025 DC seven-bracket schedule to a taxable-income amount.

    Returns a non-negative Decimal rounded to cents. Negative taxable income
    (possible when the deduction exceeds AGI) yields zero.
    """
    if taxable_income <= 0:
        return Decimal("0")
    for upper, base_tax, rate, floor in DC_TY2025_BRACKETS:
        if upper is None or taxable_income <= upper:
            tax = base_tax + (taxable_income - floor) * rate
            return tax.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    # Unreachable — the last bracket has upper=None and catches everything.
    raise RuntimeError("DC bracket table did not cover taxable income")


def _compute_resident_tax(federal_agi: Decimal, filing_status: FilingStatus) -> Decimal:
    """Compute the full-year resident DC tax starting from federal AGI.

    STUB: real DC Form D-40 starts from federal AGI, then adds DC additions
    (e.g. out-of-state municipal bond interest) and subtracts DC subtractions
    (e.g. federal bond interest, DC/fed pension exclusion) before applying the
    standard or itemized deduction. We skip additions/subtractions for now.
    """
    std_ded = _dc_standard_deduction(filing_status)
    taxable = federal_agi - std_ded
    return _dc_bracket_tax(taxable)


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DistrictOfColumbiaPlugin:
    """StatePlugin implementation for the District of Columbia."""

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # DC universal nonresident exemption — applies regardless of home state.
        if residency == ResidencyStatus.NONRESIDENT:
            return StateReturn(
                state=self.meta.code,
                residency=residency,
                days_in_state=days_in_state,
                state_specific={
                    "state_tax": Decimal("0"),
                    "reason": "DC universal nonresident exemption",
                    "standard_deduction": _dc_standard_deduction(federal.filing_status),
                    "taxable_income": Decimal("0"),
                    "no_return_required": True,
                },
            )

        resident_tax = _compute_resident_tax(
            federal.adjusted_gross_income, federal.filing_status
        )

        if residency == ResidencyStatus.PART_YEAR:
            # TODO: real DC part-year apportionment uses D-40 Schedule S income
            # allocation by period of DC residency, not a simple day ratio.
            days = max(0, min(days_in_state, 365))
            ratio = Decimal(days) / Decimal(365)
            state_tax = (resident_tax * ratio).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            reason = (
                f"DC part-year resident ({days}/365 days) — stub days-based "
                f"proration of full-year resident tax"
            )
        else:  # RESIDENT
            state_tax = resident_tax
            reason = "DC full-year resident"

        return StateReturn(
            state=self.meta.code,
            residency=residency,
            days_in_state=days_in_state,
            state_specific={
                "state_tax": state_tax,
                "reason": reason,
                "standard_deduction": _dc_standard_deduction(federal.filing_status),
                "taxable_income": max(
                    Decimal("0"),
                    federal.adjusted_gross_income
                    - _dc_standard_deduction(federal.filing_status),
                ),
                "resident_tax_full_year": resident_tax,
                "no_return_required": False,
            },
        )

    def apportion_income(
        self,
        return_: CanonicalReturn,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> IncomeApportionment:
        # Aggregate federal-side income categories from the canonical return.
        wages = sum(
            (w.box1_wages for w in return_.w2s), start=Decimal("0")
        )
        interest = sum(
            (f.box1_interest_income for f in return_.forms_1099_int),
            start=Decimal("0"),
        )
        dividends = sum(
            (f.box1a_ordinary_dividends for f in return_.forms_1099_div),
            start=Decimal("0"),
        )
        # Capital gains: net proceeds - basis across all 1099-B transactions.
        cap_gains = Decimal("0")
        for b in return_.forms_1099_b:
            for t in b.transactions:
                cap_gains += t.proceeds - t.cost_basis
        se_income = sum(
            (
                c.line1_gross_receipts
                - c.line2_returns_and_allowances
                - c.line4_cost_of_goods_sold
                + c.line6_other_income
                for c in return_.schedules_c
            ),
            start=Decimal("0"),
        )
        rental = Decimal("0")
        for e in return_.schedules_e:
            for p in e.properties:
                rental += p.rents_received + p.royalties_received

        if residency == ResidencyStatus.NONRESIDENT:
            zero = Decimal("0")
            return IncomeApportionment(
                state_source_wages=zero,
                state_source_interest=zero,
                state_source_dividends=zero,
                state_source_capital_gains=zero,
                state_source_self_employment=zero,
                state_source_rental=zero,
            )

        if residency == ResidencyStatus.PART_YEAR:
            days = max(0, min(days_in_state, 365))
            ratio = Decimal(days) / Decimal(365)

            def _prorate(x: Decimal) -> Decimal:
                return (x * ratio).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            return IncomeApportionment(
                state_source_wages=_prorate(wages),
                state_source_interest=_prorate(interest),
                state_source_dividends=_prorate(dividends),
                state_source_capital_gains=_prorate(cap_gains),
                state_source_self_employment=_prorate(se_income),
                state_source_rental=_prorate(rental),
            )

        # RESIDENT: all federal income is DC-source.
        return IncomeApportionment(
            state_source_wages=wages,
            state_source_interest=interest,
            state_source_dividends=dividends,
            state_source_capital_gains=cap_gains,
            state_source_self_employment=se_income,
            state_source_rental=rental,
        )

    def render_pdfs(self, state_return: StateReturn, out_dir: Path) -> list[Path]:
        # TODO: fill DC Form D-40 (and D-40 Schedule S / Schedule H as needed).
        return []

    def form_ids(self) -> list[str]:
        # TODO: extend with Schedule S (part-year/nonresident) and Schedule H
        # (homeowner/renter property tax credit) once those calcs land.
        return ["DC Form D-40"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = DistrictOfColumbiaPlugin(
    meta=StatePluginMeta(
        code="DC",
        name="District of Columbia",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://otr.cfo.dc.gov/",
        free_efile_url="https://mytax.dc.gov/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        reciprocity_partners=("MD", "VA"),
        supported_tax_years=(2025,),
        notes=(
            "DC universal nonresident exemption applies regardless of home state. "
            "Resident calc: STUB \u2014 see module-level TODO."
        ),
    )
)
