"""New York state plugin.

NY is one of the 10 states tenforty (OpenTaxSolver) computes natively. This
plugin wraps tenforty's state calc, following the CA pattern: marshal the
canonical return via the shared `_to_tenforty_input` helper, call
`tenforty.evaluate_return(..., state='NY')`, and unpack the `state_*` floats
into Decimal on `StateReturn.state_specific`.

Scope:
- Resident full-year NY taxpayers get an authoritative state tax via OTS
  (IT-201).
- Nonresidents and part-year residents use the IT-203 / IT-203-B sourcing
  method: tax = (tax on total income) x (NY-source income / total income).
  Income categories are sourced per NY rules:
    * Wages: IT-203-B workday allocation (NY workdays / 260), or W-2 state
      rows, or day-proration fallback.
    * Interest / Dividends / Capital gains: NOT NY-source for nonresidents
      (sourced to domicile state).
    * Business income (Schedule C): NY-source if business is in NY.
    * Rental income (Schedule E): NY-source if property is in NY.
- PDF rendering fills the IT-201 fillable PDF via pypdf AcroForm overlay.

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
from skill.scripts.output._acroform_overlay import (
    fetch_and_verify_source_pdf,
    fill_acroform_pdf,
    format_money,
    load_widget_map,
)
from skill.scripts.states._hand_rolled_base import (
    state_has_w2_state_rows,
    state_source_rental_from_schedule_e,
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


# ---------------------------------------------------------------------------
# Reference paths
# ---------------------------------------------------------------------------

_REF_DIR = Path(__file__).resolve().parent.parent.parent / "reference"
_WIDGET_MAP_JSON = _REF_DIR / "ny-it201-acroform-map.json"
_STATE_FORMS_DIR = _REF_DIR / "state_forms"
_CACHED_PDF = _STATE_FORMS_DIR / "ny-it201.pdf"


# ---------------------------------------------------------------------------
# Layer 1 dataclass: IT-201 field snapshot
# ---------------------------------------------------------------------------


_ZERO = Decimal("0")


@dataclass(frozen=True)
class IT201Fields:
    """Frozen snapshot of NY IT-201 line values for rendering.

    Field names match semantic keys in ny-it201-acroform-map.json.
    """

    # Header / identity
    taxpayer_first_name: str = ""
    taxpayer_mi: str = ""
    taxpayer_last_name: str = ""
    taxpayer_ssn: str = ""
    taxpayer_dob: str = ""
    mailing_address: str = ""
    mailing_city: str = ""
    mailing_state: str = ""
    mailing_zip: str = ""
    nys_county: str = ""

    # Income lines (page 2)
    line1_wages: Decimal = _ZERO
    line2_taxable_interest: Decimal = _ZERO
    line3_ordinary_dividends: Decimal = _ZERO
    line17_federal_agi: Decimal = _ZERO
    line19_nys_additions: Decimal = _ZERO
    line21_nys_income: Decimal = _ZERO
    line24_nys_subtractions: Decimal = _ZERO
    line25_nys_agi: Decimal = _ZERO
    line33_nys_deduction: Decimal = _ZERO
    line35_nys_taxable_income: Decimal = _ZERO
    line37_nys_tax_on_ti: Decimal = _ZERO

    # Credits / tax (page 3)
    line39_nys_household_credit: Decimal = _ZERO
    line46_nys_other_credits: Decimal = _ZERO
    line47_nys_net_tax: Decimal = _ZERO
    line59_total_nys_tax: Decimal = _ZERO

    # Payments / refund (page 4)
    line62_nys_tax_withheld: Decimal = _ZERO
    line71_total_payments: Decimal = _ZERO
    line73_total_payments_credits: Decimal = _ZERO
    line78_refund: Decimal = _ZERO
    line80_amount_owed: Decimal = _ZERO


def _build_it201_fields(
    state_return: StateReturn,
) -> IT201Fields:
    """Build an IT201Fields from a computed StateReturn.

    Maps state_specific keys produced by compute() to form fields.
    """
    ss = state_return.state_specific

    state_agi = ss.get("state_adjusted_gross_income", _ZERO)
    state_ti = ss.get("state_taxable_income", _ZERO)
    state_tax = ss.get("state_total_tax", _ZERO)

    return IT201Fields(
        line17_federal_agi=state_agi,
        line25_nys_agi=state_agi,
        line35_nys_taxable_income=state_ti,
        line37_nys_tax_on_ti=state_tax,
        line47_nys_net_tax=state_tax,
        line59_total_nys_tax=state_tax,
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
          2. Call `tenforty.evaluate_return(..., state='NY')` to get the
             full-year tax on total income.
          3. Wrap the `state_*` floats as Decimals on state_specific.
          4. For NONRESIDENT / PART_YEAR, apply the IT-203 ratio method:
             tax = (tax on total income) x (NY-source income / total income).
             NY-source income is computed per category:
               - Wages: IT-203-B workday allocation > W-2 state rows > day-proration
               - Interest/Dividends/Capital gains: $0 (sourced to domicile)
               - Business income: NY-source if business is in NY
               - Rental: NY-source if property is in NY
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

        # --- IT-203 nonresident sourcing ---
        # For NONRESIDENT / PART_YEAR, compute NY-source income per category
        # and apply the IT-203 ratio method:
        #   tax = (tax on total income) x (NY-source / total income)
        #
        # Wage sourcing priority:
        #   1. IT-203-B workday apportionment (ny_workdays_in_ny / 260)
        #   2. W-2 state_rows[state=NY].state_wages sum
        #   3. Day-proration fallback (legacy)
        ny_state_rows_present = state_has_w2_state_rows(return_, "NY")
        ny_sourced_wages_w2 = state_source_wages_from_w2s(return_, "NY")
        ny_sourced_se = state_source_schedule_c(return_, "NY")
        ny_sourced_rental = state_source_rental_from_schedule_e(return_, "NY")
        ny_workdays_in_ny = return_.taxpayer.ny_workdays_in_ny
        used_it203_workdays = False
        used_w2_state_rows = False

        if residency == ResidencyStatus.RESIDENT:
            state_tax = full_year_state_tax.quantize(Decimal("0.01"))
        elif ny_workdays_in_ny is not None and ny_workdays_in_ny > 0:
            # IT-203-B: allocate total wages by the workday ratio.
            # Standard IT-203-B denominator is 260 (5-day workweek).
            total_wages = Decimal(str(tf_input.w2_income or 0))
            workdays_denom = Decimal("260")
            ratio = Decimal(ny_workdays_in_ny) / workdays_denom
            if ratio > Decimal("1"):
                ratio = Decimal("1")
            allocated_wages = (total_wages * ratio).quantize(Decimal("0.01"))

            # IT-203 ratio method: NY-source = wages + SE + rental (no
            # interest/dividends/cap-gains for nonresidents)
            ny_source_total = allocated_wages + ny_sourced_se + ny_sourced_rental
            total_income = state_agi if state_agi > _ZERO else Decimal("1")
            if total_income > _ZERO and ny_source_total > _ZERO:
                it203_ratio = ny_source_total / total_income
                if it203_ratio > Decimal("1"):
                    it203_ratio = Decimal("1")
                state_tax = (full_year_state_tax * it203_ratio).quantize(Decimal("0.01"))
            else:
                state_tax = Decimal("0.00")
            used_it203_workdays = True
        elif ny_state_rows_present:
            # W-2 state rows: employer-reported NY wages
            ny_source_total = ny_sourced_wages_w2 + ny_sourced_se + ny_sourced_rental
            total_income = state_agi if state_agi > _ZERO else Decimal("1")
            if total_income > _ZERO and ny_source_total > _ZERO:
                it203_ratio = ny_source_total / total_income
                if it203_ratio > Decimal("1"):
                    it203_ratio = Decimal("1")
                state_tax = (full_year_state_tax * it203_ratio).quantize(Decimal("0.01"))
            else:
                state_tax = Decimal("0.00")
            used_w2_state_rows = True
        else:
            # Legacy day-proration fallback. Tests that cover nonresident
            # behavior without W-2 state rows or an IT-203-B workday
            # count still lock this path.
            proration = Decimal(days_in_state) / Decimal("365")
            state_tax = (full_year_state_tax * proration).quantize(Decimal("0.01"))

        return StateReturn(
            state=self.meta.code,
            residency=residency,
            days_in_state=days_in_state,
            state_specific={
                "state_total_tax": state_tax,
                "state_adjusted_gross_income": state_agi,
                "state_taxable_income": state_ti,
                "state_tax_bracket": state_bracket,
                "state_effective_tax_rate": state_effective_rate,
                "full_year_state_tax": full_year_state_tax,
                "engine": "tenforty/OpenTaxSolver",
                "ny_sourced_wages_from_w2_state_rows": ny_sourced_wages_w2,
                "ny_sourced_schedule_c_net": ny_sourced_se,
                "ny_sourced_rental": ny_sourced_rental,
                "ny_state_rows_present": ny_state_rows_present,
                "ny_workdays_in_ny": ny_workdays_in_ny,
                "used_it203_workdays": used_it203_workdays,
                "used_w2_state_rows": used_w2_state_rows,
            },
        )

    def apportion_income(
        self,
        return_: CanonicalReturn,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> IncomeApportionment:
        """IT-203-compliant income apportionment for New York.

        For RESIDENT, 100% of every income category is NY-source.

        For NONRESIDENT / PART_YEAR, each income category is sourced per
        NY IT-203 rules:
          - Wages: IT-203-B workday allocation (priority), then W-2 state
            rows, then day-proration fallback.
          - Interest / Dividends / Capital gains: $0 — not NY-source for
            nonresidents (sourced to domicile state).
          - Business income (Schedule C): NY-source if the business is
            located in NY (``business_location_state == "NY"``).
          - Rental income (Schedule E): NY-source if the property is in NY
            (``property.address.state == "NY"``).
        """
        from skill.scripts.calc.engine import schedule_c_net_profit, schedule_e_total_net

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

        se_income = sum(
            (schedule_c_net_profit(sc) for sc in return_.schedules_c),
            start=Decimal("0"),
        )
        rental = sum(
            (schedule_e_total_net(sched) for sched in return_.schedules_e),
            start=Decimal("0"),
        )

        if residency == ResidencyStatus.RESIDENT:
            # Full-year resident: 100% of all income is NY-source.
            return IncomeApportionment(
                state_source_wages=wages,
                state_source_interest=interest,
                state_source_dividends=ord_div,
                state_source_capital_gains=capital_gains,
                state_source_self_employment=se_income,
                state_source_rental=rental,
            )

        # --- Nonresident / Part-Year: IT-203 sourcing ---

        # Wages: workday allocation > W-2 state rows > day-proration
        ny_workdays_in_ny = return_.taxpayer.ny_workdays_in_ny
        if ny_workdays_in_ny is not None and ny_workdays_in_ny > 0:
            workdays_denom = Decimal("260")
            ratio = Decimal(ny_workdays_in_ny) / workdays_denom
            if ratio > Decimal("1"):
                ratio = Decimal("1")
            ny_wages = (wages * ratio).quantize(Decimal("0.01"))
        elif state_has_w2_state_rows(return_, "NY"):
            ny_wages = state_source_wages_from_w2s(return_, "NY")
        else:
            factor = Decimal(days_in_state) / Decimal("365")
            ny_wages = (wages * factor).quantize(Decimal("0.01"))

        # Interest / Dividends / Capital gains: NOT NY-source for nonresidents
        ny_interest = Decimal("0")
        ny_dividends = Decimal("0")
        ny_capital_gains = Decimal("0")

        # Business income: sourced by business location
        ny_se = state_source_schedule_c(return_, "NY")

        # Rental: sourced by property location
        ny_rental = state_source_rental_from_schedule_e(return_, "NY")

        return IncomeApportionment(
            state_source_wages=ny_wages,
            state_source_interest=ny_interest,
            state_source_dividends=ny_dividends,
            state_source_capital_gains=ny_capital_gains,
            state_source_self_employment=ny_se,
            state_source_rental=ny_rental,
        )

    def render_pdfs(self, state_return: StateReturn, out_dir: Path) -> list[Path]:
        """Render NY IT-201 by filling the NYS fillable PDF via AcroForm overlay."""
        wmap = load_widget_map(_WIDGET_MAP_JSON)

        # Ensure the source PDF is cached locally.
        source_pdf = fetch_and_verify_source_pdf(
            _CACHED_PDF, wmap.source_pdf_url, wmap.source_pdf_sha256
        )

        # Build the Layer 1 field snapshot.
        fields = _build_it201_fields(state_return)

        # Build widget values from the dataclass.
        from dataclasses import asdict

        raw = asdict(fields)
        widget_values: dict[str, str] = {}
        for sem_name, value in raw.items():
            widget_names = wmap.widget_names_for(sem_name)
            if not widget_names:
                continue
            if isinstance(value, Decimal):
                text = format_money(value)
            elif value is None:
                text = ""
            else:
                text = str(value)
            for wn in widget_names:
                widget_values[wn] = text

        out_path = Path(out_dir) / "NY-IT-201.pdf"
        fill_acroform_pdf(source_pdf, widget_values, out_path)
        return [out_path]

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
