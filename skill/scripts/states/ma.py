"""Massachusetts state plugin.

MA is one of the states tenforty supports directly via OpenTaxSolver. A
direct probe confirms the state pass-through works for TY2025:

    tenforty.evaluate_return(
        year=2025, state='MA', filing_status='Single',
        w2_income=65000, standard_or_itemized='Standard',
    )
    -> state_total_tax=3030.0, state_taxable_income=60600.0,
       state_adjusted_gross_income=65000.0

MA is unusual among US states: it does NOT conform to federal AGI. Instead
it computes its own gross base from three buckets:

- **Part A income** — interest, dividends, short-term capital gains. Taxed
  at a higher rate than Part B (historically 5.0% on interest/dividends
  and 8.5% on STCG, though the STCG rate dropped to 8.5% then adjusted;
  verify TY2025 rate at mass.gov before relying on the output).
- **Part B income** — wages, salaries, pensions, most other ordinary
  income. Taxed at MA's flat rate (5.0% for TY2025; verify at mass.gov).
- **Part C income** — long-term capital gains on collectibles and certain
  installment sales. Taxed at a separate rate.

This plugin's metadata carries `starting_point = StateStartingPoint.STATE_GROSS`
so downstream consumers (multi-state orchestration, interview) know MA does
not share the "federal AGI + state adjustments" pipeline that covers most
states. tenforty/OTS handles the Part A/B/C math internally; this plugin
wraps that call exactly as the CA plugin does, and surfaces the same
resident/nonresident day-proration TODO that every wave-1/wave-2 state
shares.

Nonresident / part-year handling is a day-based proration TODO — a full
calculation requires MA Form 1-NR/PY apportionment by income sourcing
(wages sourced to work location, investment income sourced to domicile,
etc.). That is fan-out follow-up work.
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


# ---------------------------------------------------------------------------
# Reference paths
# ---------------------------------------------------------------------------

_REF_DIR = Path(__file__).resolve().parent.parent.parent / "reference"
_WIDGET_MAP_JSON = _REF_DIR / "ma-form1-acroform-map.json"
_STATE_FORMS_DIR = _REF_DIR / "state_forms"
_CACHED_PDF = _STATE_FORMS_DIR / "ma-form1.pdf"


# ---------------------------------------------------------------------------
# Layer 1 dataclass: MA Form 1 field snapshot
# ---------------------------------------------------------------------------


_ZERO_MA = Decimal("0")


@dataclass(frozen=True)
class MAForm1Fields:
    """Frozen snapshot of MA Form 1 line values for rendering.

    Field names match semantic keys in ma-form1-acroform-map.json.
    """

    # Header / identity
    taxpayer_first_name: str = ""
    taxpayer_mi: str = ""
    taxpayer_last_name: str = ""
    taxpayer_ssn: str = ""
    mailing_address: str = ""
    mailing_city: str = ""
    mailing_state: str = ""
    mailing_zip: str = ""

    # Exemptions (page 1)
    line_2a_personal_exemption: Decimal = _ZERO_MA
    line_2g_total_exemptions: Decimal = _ZERO_MA

    # Income (page 2)
    line_3_wages: Decimal = _ZERO_MA
    line_5_bank_interest: Decimal = _ZERO_MA
    line_6a_business_income: Decimal = _ZERO_MA
    line_10_total_5pct_income: Decimal = _ZERO_MA
    line_16_total_deductions: Decimal = _ZERO_MA
    line_17_income_after_deductions: Decimal = _ZERO_MA
    line_18_exemption_amount: Decimal = _ZERO_MA
    line_19_income_after_exemptions: Decimal = _ZERO_MA
    line_21_total_taxable_5pct: Decimal = _ZERO_MA
    line_22_tax_on_5pct_income: Decimal = _ZERO_MA

    # Tax / credits (page 3)
    line_28a_income_tax: Decimal = _ZERO_MA
    line_28_total_tax: Decimal = _ZERO_MA
    line_32_tax_after_credits: Decimal = _ZERO_MA
    line_37_tax_after_additions: Decimal = _ZERO_MA

    # Withholding / payments (pages 3-4)
    line_38a_w2_withholding: Decimal = _ZERO_MA
    line_38_total_withholding: Decimal = _ZERO_MA
    line_48_total_refundable_credits: Decimal = _ZERO_MA
    line_51_total_payments: Decimal = _ZERO_MA

    # Refund / amount owed (page 4)
    line_52_overpayment: Decimal = _ZERO_MA
    line_54_refund: Decimal = _ZERO_MA
    line_55_tax_due: Decimal = _ZERO_MA


def _build_ma_form1_fields(
    state_return: StateReturn,
) -> MAForm1Fields:
    """Build an MAForm1Fields from a computed StateReturn.

    Maps state_specific keys produced by compute() to form fields.
    """
    ss = state_return.state_specific

    state_agi = ss.get("state_adjusted_gross_income", _ZERO_MA)
    state_ti = ss.get("state_taxable_income", _ZERO_MA)
    state_tax = ss.get("state_total_tax", _ZERO_MA)

    return MAForm1Fields(
        line_10_total_5pct_income=state_agi,
        line_17_income_after_deductions=state_agi,
        line_19_income_after_exemptions=state_ti,
        line_21_total_taxable_5pct=state_ti,
        line_22_tax_on_5pct_income=state_tax,
        line_28a_income_tax=state_tax,
        line_28_total_tax=state_tax,
        line_32_tax_after_credits=state_tax,
        line_37_tax_after_additions=state_tax,
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

    TODO: a real nonresident MA calculation uses Form 1-NR/PY with
    income-specific sourcing (wages to the work location, interest/dividends
    to the taxpayer's domicile, rental to the property state, etc.) rather
    than a flat day ratio. Day-based proration is a first-order
    approximation shared across fan-out state plugins; follow-up will
    tighten this with the real 1-NR/PY logic.
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
class MassachusettsPlugin:
    """State plugin for Massachusetts.

    Wraps tenforty/OpenTaxSolver for the resident case and day-proration for
    nonresident / part-year. Starting point is MA's own gross base (Part
    A/B/C income) — NOT federal AGI. tenforty's MA implementation handles
    the Part A/B/C decomposition internally; this plugin's responsibility is
    to surface that fact in meta and pass the call through.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Reuse the federal marshaling so MA sees exactly the same numbers
        # the federal calc did. tenforty will remap these into MA's Part
        # A/B/C buckets internally; we do NOT replicate that logic here.
        tf_input = _to_tenforty_input(return_)

        tf_result = tenforty.evaluate_return(
            year=tf_input.year,
            state="MA",
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
        # cents). tenforty's MA path often reports these as 0.0 because MA
        # is a flat-rate state; we still carry the keys for symmetry with
        # other state plugins.
        state_bracket = _d(tf_result.state_tax_bracket)
        state_eff_rate = _d(tf_result.state_effective_tax_rate)

        # Apportion tax for nonresident / part-year. TODO: replace with real
        # MA Form 1-NR/PY income-source apportionment in fan-out.
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
        """Split canonical income into MA-source vs non-MA-source.

        Residents: everything is MA-source. Nonresident / part-year: prorate
        each category by days_in_state / 365.

        TODO: MA Form 1-NR/PY sources each income type differently (wages to
        the work location, Part A interest/dividends to the taxpayer's
        domicile, rental to the property state, Part C LTCG on collectibles
        to the residency period, etc.). Day-based proration is the shared
        first-cut across all fan-out state plugins; refine in follow-up.
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

        # Schedule C/E totals — import the helpers directly to avoid a
        # second round-trip through _to_tenforty_input.
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
        """Render MA Form 1 by filling the DOR fillable PDF via AcroForm overlay."""
        wmap = load_widget_map(_WIDGET_MAP_JSON)

        # Ensure the source PDF is cached locally.
        source_pdf = fetch_and_verify_source_pdf(
            _CACHED_PDF, wmap.source_pdf_url, wmap.source_pdf_sha256
        )

        # Build the Layer 1 field snapshot.
        fields = _build_ma_form1_fields(state_return)

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

        out_path = Path(out_dir) / "MA-Form-1.pdf"
        fill_acroform_pdf(source_pdf, widget_values, out_path)
        return [out_path]

    def form_ids(self) -> list[str]:
        return ["MA Form 1"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = MassachusettsPlugin(
    meta=StatePluginMeta(
        code="MA",
        name="Massachusetts",
        has_income_tax=True,
        starting_point=StateStartingPoint.STATE_GROSS,
        dor_url="https://www.mass.gov/orgs/massachusetts-department-of-revenue",
        free_efile_url="https://mtc.dor.state.ma.us/mtc/_/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "MA uses Part A (interest/div/STCG), Part B (wages/most income), "
            "Part C (LTCG collectibles) — STATE_GROSS base, not federal AGI. "
            "tenforty's MA implementation handles these internally. Part A "
            "rate for TY2025: verify at mass.gov before relying on TY2025 "
            "output."
        ),
    )
)
