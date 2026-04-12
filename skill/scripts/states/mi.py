"""Michigan state plugin.

MI is one of the 10 states tenforty (OpenTaxSolver) supports natively. This
plugin is a thin wrapper around `tenforty.evaluate_return(..., state='MI')`
following the CA reference pattern: reuse the calc engine's
`_to_tenforty_input` marshaling so MI sees exactly the same income /
deduction numbers the federal calc uses, call tenforty, and unpack the
`state_*` floats as Decimal on `StateReturn.state_specific`.

Rate / base (TY2025):
    - Starting point: federal AGI.
    - Flat rate: 4.25%. (MI law briefly reduced the rate to 4.05% for 2023
      via the PA 4 revenue trigger; it reverted to 4.25% for 2024 and
      remains 4.25% for 2025 per MI Treasury guidance. Keep this sentence
      in sync with tenforty's MI table if it changes.)
    - Reference probe: Single / $65,000 W-2 / Standard
        -> state_total_tax = 2762.50
           state_taxable_income = 65000.00
           state_adjusted_gross_income = 65000.00
      i.e. $65,000 * 4.25% = $2,762.50.

Nonresident / part-year:
    Day-based proration of the resident-basis tax is a v0.1 stopgap. The
    correct treatment is MI Schedule NR (MI-1040 Schedule NR), which
    prorates by Michigan-source income (wages earned while MI-resident or
    from MI sources, MI rental, etc.) rather than day count. The TODO in
    compute() tracks this.

Reciprocity:
    MI is unusual — it has SIX bilateral reciprocity agreements (IL, IN,
    KY, MN, OH, WI), the largest set of any state in the skill's reference
    table. Residents of those states who work in MI are exempt from MI
    income tax on their wages (and vice versa). The skill's multi-state
    workflow reads `reciprocity_partners` to drive this logic, so the
    tuple below is load-bearing and is verified by a test against
    skill/reference/state-reciprocity.json.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, fields as dc_fields
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import tenforty

from skill.scripts.calc.engine import _to_tenforty_input
from skill.scripts.models import (
    CanonicalReturn,
    FilingStatus,
    ResidencyStatus,
    StateReturn,
)
from skill.scripts.output._acroform_overlay import (
    fetch_and_verify_source_pdf,
    fill_acroform_pdf,
    format_money,
    load_widget_map,
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
_ZERO = Decimal("0")

_REF_DIR = Path(__file__).resolve().parent.parent.parent / "reference"
_STATE_FORMS_DIR = _REF_DIR / "state_forms"
_WIDGET_MAP_PATH = _REF_DIR / "mi-1040-acroform-map.json"

# MI TY2025 personal exemption per person = $5,800
_MI_PERSONAL_EXEMPTION_TY2025 = Decimal("5800")


# ---------------------------------------------------------------------------
# Layer 1: MI-1040 field dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MI1040Fields:
    """Frozen snapshot of MI-1040 line values, ready for rendering.

    Field names follow the 2025 MI-1040 line numbers. All numeric fields
    are Decimal. Header / identity fields are strings.
    """

    # Identity / header
    filer_first_name: str = ""
    filer_middle_initial: str = ""
    filer_last_name: str = ""
    filer_ssn_1: str = ""  # First 3 digits
    filer_ssn_2: str = ""  # Middle 2 digits
    filer_ssn_3: str = ""  # Last 4 digits
    spouse_first_name: str = ""
    spouse_middle_initial: str = ""
    spouse_last_name: str = ""
    spouse_ssn_1: str = ""
    spouse_ssn_2: str = ""
    spouse_ssn_3: str = ""
    home_address: str = ""
    city: str = ""
    state: str = "MI"
    zip_code: str = ""
    filing_status: str = ""  # single/mfj/mfs
    residency: str = ""  # resident/nonresident/part_year

    # Exemptions (line 9)
    line_9a_personal_exemptions_count: str = ""
    line_9a_personal_exemptions_amount: Decimal = _ZERO
    line_9f_total_exemption_allowance: Decimal = _ZERO

    # Income / tax lines (page 1)
    line_10_agi: Decimal = _ZERO
    line_11_additions: Decimal = _ZERO
    line_12_total_income: Decimal = _ZERO
    line_13_subtractions: Decimal = _ZERO
    line_14_adjusted_income: Decimal = _ZERO
    line_15_exemption_allowance: Decimal = _ZERO
    line_16_taxable_income: Decimal = _ZERO
    line_17_tax: Decimal = _ZERO

    # Page 2 lines
    line_20a_income_tax_withheld: Decimal = _ZERO
    line_20b_estimated_tax_paid: Decimal = _ZERO
    line_22_balance_due: Decimal = _ZERO
    line_24_total_payments_credits: Decimal = _ZERO
    line_25_overpayment: Decimal = _ZERO
    line_30_refund: Decimal = _ZERO
    line_31_amount_owed: Decimal = _ZERO
    line_34_total_amount_due: Decimal = _ZERO


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

    TODO(mi-sched-nr): Replace with MI-1040 Schedule NR income-source
    apportionment (Michigan-source wages, rental, business income) rather
    than day count. Day-based proration is the shared first-cut across all
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
class MichiganPlugin:
    """State plugin for Michigan.

    Wraps tenforty / OpenTaxSolver for the resident case and day-proration
    for nonresident / part-year. Starting point is federal AGI; MI layers
    state-specific additions and subtractions on top, which tenforty
    handles internally on the MI-1040 path.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Reuse the federal marshaling so MI sees exactly the same numbers
        # the federal calc did — do NOT duplicate that logic here.
        tf_input = _to_tenforty_input(return_)

        tf_result = tenforty.evaluate_return(
            year=tf_input.year,
            state="MI",
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
        # (not cents) so fractional values stay precise. MI is flat-rate
        # so tenforty typically reports 0.0 for bracket / eff rate; we
        # still surface the keys for schema uniformity across states.
        state_bracket = _d(tf_result.state_tax_bracket)
        state_eff_rate = _d(tf_result.state_effective_tax_rate)

        # Apportion tax for nonresident / part-year.
        # TODO(mi-sched-nr): replace with MI-1040 Schedule NR income-source
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
        """Split canonical income into MI-source vs non-MI-source.

        Residents: everything is MI-source. Nonresident / part-year: prorate
        each category by days_in_state / 365.

        TODO(mi-sched-nr): MI actually sources each income type via
        MI-1040 Schedule NR — wages to the work location, interest /
        dividends to the taxpayer's domicile, rental to the property state,
        etc. Day-based proration is the shared first-cut across all
        fan-out state plugins; refine with Schedule NR logic in follow-up.
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

        # Schedule C / E net totals — reuse calc.engine helpers so MI
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
        """Fill MI-1040 using the Michigan Treasury fillable PDF.

        Layer 1: compute MI1040Fields from state_return.state_specific.
        Layer 2: overlay onto the source PDF via fill_acroform_pdf.
        """
        ss = state_return.state_specific
        agi = ss.get("state_adjusted_gross_income", _ZERO)
        ti = ss.get("state_taxable_income", _ZERO)
        tax = ss.get("state_total_tax", _ZERO)

        # SSN parsing from state_specific (if stashed) or empty
        ssn = ss.get("_taxpayer_ssn", "").replace("-", "")
        ssn1 = ssn[:3] if len(ssn) >= 3 else ssn
        ssn2 = ssn[3:5] if len(ssn) >= 5 else ""
        ssn3 = ssn[5:9] if len(ssn) >= 9 else ""

        # Filing status mapping — MI only has Single/MFJ/MFS
        fs_raw = ss.get("_filing_status", "single")
        # HOH and QSS map to single for MI checkbox purposes
        if fs_raw in ("hoh", "qss"):
            fs_raw = "single"

        # Residency
        res_map = {
            ResidencyStatus.RESIDENT: "resident",
            ResidencyStatus.NONRESIDENT: "nonresident",
            ResidencyStatus.PART_YEAR: "part_year",
        }
        residency_label = res_map.get(state_return.residency, "resident")

        # Personal exemption: 1 for single, 2 for MFJ
        num_exemptions = 2 if fs_raw == "mfj" else 1
        exemption_amount = _MI_PERSONAL_EXEMPTION_TY2025 * num_exemptions

        # Withholding from state_specific
        mi_withholding = ss.get("mi_withholding", _ZERO)
        total_payments = mi_withholding

        mi1040 = MI1040Fields(
            filer_first_name=ss.get("_taxpayer_first_name", ""),
            filer_last_name=ss.get("_taxpayer_last_name", ""),
            filer_ssn_1=ssn1,
            filer_ssn_2=ssn2,
            filer_ssn_3=ssn3,
            home_address=ss.get("_address", ""),
            city=ss.get("_city", ""),
            state=ss.get("_state", "MI"),
            zip_code=ss.get("_zip", ""),
            filing_status=fs_raw,
            residency=residency_label,
            line_9a_personal_exemptions_count=str(num_exemptions),
            line_9a_personal_exemptions_amount=exemption_amount,
            line_9f_total_exemption_allowance=exemption_amount,
            line_10_agi=agi,
            line_12_total_income=agi,
            line_14_adjusted_income=agi,
            line_15_exemption_allowance=exemption_amount,
            line_16_taxable_income=ti,
            line_17_tax=tax,
            line_20a_income_tax_withheld=mi_withholding,
            line_24_total_payments_credits=total_payments,
            line_22_balance_due=max(tax - total_payments, _ZERO) if tax > total_payments else _ZERO,
            line_25_overpayment=max(total_payments - tax, _ZERO) if total_payments > tax else _ZERO,
            line_30_refund=max(total_payments - tax, _ZERO) if total_payments > tax else _ZERO,
            line_31_amount_owed=max(tax - total_payments, _ZERO) if tax > total_payments else _ZERO,
            line_34_total_amount_due=max(tax - total_payments, _ZERO) if tax > total_payments else _ZERO,
        )

        # Load widget map
        wmap = load_widget_map(_WIDGET_MAP_PATH)

        # Fetch/verify source PDF
        source_pdf = fetch_and_verify_source_pdf(
            _STATE_FORMS_DIR / "mi-1040.pdf",
            wmap.source_pdf_url,
            wmap.source_pdf_sha256,
        )

        # Build widget values
        widget_values: dict[str, str | bool] = {}

        money_fields = {
            f.name for f in dc_fields(mi1040)
            if f.name.startswith("line_") and not f.name.endswith("_count")
        }
        for f in dc_fields(mi1040):
            value = getattr(mi1040, f.name)
            widget_names = wmap.widget_names_for(f.name)
            if not widget_names:
                continue
            if f.name in money_fields and isinstance(value, Decimal):
                text = format_money(value)
            else:
                text = str(value) if value else ""
            for wn in widget_names:
                widget_values[wn] = text

        # Filing status checkbox
        fs_label = mi1040.filing_status
        if fs_label in wmap.filing_status_checkboxes:
            widget_values[wmap.filing_status_checkboxes[fs_label]] = True

        # Residency checkbox — not in standard WidgetMap, load from raw JSON
        raw_map = json.loads(_WIDGET_MAP_PATH.read_text())
        residency_cbs = raw_map.get("residency_checkboxes", {})
        if residency_label in residency_cbs:
            widget_values[residency_cbs[residency_label]["widget_name"]] = True

        # Write the filled PDF
        out_dir = Path(out_dir)
        out_path = out_dir / "MI_MI-1040.pdf"
        fill_acroform_pdf(source_pdf, widget_values, out_path)
        return [out_path]

    def form_ids(self) -> list[str]:
        return ["MI Form MI-1040"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = MichiganPlugin(
    meta=StatePluginMeta(
        code="MI",
        name="Michigan",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://www.michigan.gov/treasury",
        free_efile_url="https://www.michigan.gov/taxes/iit/e-file",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # MI has six bilateral reciprocity partners — verified against
        # skill/reference/state-reciprocity.json. A test asserts the exact
        # set so accidental drift fails CI.
        reciprocity_partners=("IL", "IN", "KY", "MN", "OH", "WI"),
        supported_tax_years=(2025,),
        notes=(
            "Uses tenforty/OpenTaxSolver for MI state calc. Flat 4.25% "
            "rate for TY2025 (reverted from the temporary 4.05% in 2023)."
        ),
    )
)
