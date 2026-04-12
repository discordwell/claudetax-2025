# Filename has a trailing underscore because `id` is a Python builtin
# (the identity function). Shadowing it via ``import id`` is risky and
# linters flag it. All other state modules use the bare 2-letter code,
# matching the trailing-underscore convention used by ``or_.py`` (where
# ``or`` is a reserved keyword and bare import is a syntax error).
"""Idaho state plugin — TY2025.

See skill/reference/tenforty-ty2025-gap.md for the TY2025 probe rubric
and the graph-backend output-field gap list (state_taxable_income
echo, state_tax_bracket=0, state_effective_tax_rate=0).

Wraps tenforty's graph backend for Idaho Form 40 (resident return).
Mirrors the WI / wave-5 graph-backend wrapper pattern: probe, verify
against DOR primary source, then wrap.

Decision rubric (per skill/reference/tenforty-ty2025-gap.md)
-----------------------------------------------------------
1. **Probe** (2026-04-11, .venv tenforty, graph backend, 2025):
       Single / $65,000 W-2 / Standard
         -> state_total_tax            = 2355.267
            state_adjusted_gross_income = 65000.00
            state_taxable_income        = 49250.00
            state_tax_bracket           = 0.0     (graph backend omits)
            state_effective_tax_rate    = 0.0     (graph backend omits)
   The default OTS backend raises
   ``ValueError: OTS does not support 2025/ID_FORM40`` — graph
   backend is the only working path.

2. **Verify** against Idaho DOR primary source — 2025 Form 40
   (https://tax.idaho.gov/wp-content/uploads/forms/EFO00089/) and
   the Individual Income Tax Rate Schedule
   (https://tax.idaho.gov/taxes/income-tax/individual-income/individual-income-tax-rate-schedule/):

   - Idaho applies a **flat 5.3%** rate effective 2025-01-01 to all
     Idaho taxable income above the **first-bracket exemption**:

         Single / MFS / HoH:    $0 - $4,811   →  0%
                                $4,812+        →  5.3%
         Married Filing Jointly: $0 - $9,622  →  0%
                                $9,623+        →  5.3%

     This is technically a two-bracket schedule but functionally a
     "flat 5.3% with $4,811/$9,622 zero-rate exemption". The 0%
     bracket is the standard exemption, applied as part of the rate
     schedule rather than as a separate deduction line. (Source:
     2025 Idaho Individual Income Tax Rate Schedule.)

   - Starting point: **federal AGI** (Form 40 line 7 imports federal
     1040 line 11), then federal standard deduction (or itemized) on
     line 17, plus Idaho-specific subtractions, lands at Idaho
     taxable income on line 19.

   - 2025 Idaho **standard deduction = federal amount** ($15,750
     Single) — Idaho conforms via 2025 House Bill 559 to the OBBBA
     bumped federal standard deduction. ID Tax Commission programmed
     systems to give the larger amount automatically.

   - Hand calc for $65,000 Single / Standard:
       Federal AGI                    = $65,000
       Federal/Idaho standard ded     = $15,750
       Idaho taxable income           = $49,250
       Tax = 5.3% × ($49,250 - $4,811)
           = 5.3% × $44,439
           = **$2,355.267**

   - Graph backend: $2,355.267 — **EXACT MATCH** to the cent (and
     to the third decimal — graph returns the unrounded continuous
     formula value).

3. **Decision: WRAP** the graph backend (exact match within ε).
   The plugin pins the graph-backend value bit-for-bit so any
   upstream tenforty drift trips CI.

Rate / base (TY2025 — Idaho Individual Income Tax Rate Schedule):

    Single / MFS / HoH                       Married Filing Jointly
    --------------------------------------   ----------------------------------------
    $0     - $4,811     0%                   $0     - $9,622     0%
    $4,812+             5.3% of excess       $9,623+             5.3% of excess

Note: Idaho is among the states the StateStartingPoint enum lists as
``FEDERAL_TAXABLE_INCOME`` (the comment in _plugin_api.py reads "CO,
ID, ND, SC, OR, UT: start from federal taxable income"). Strictly,
Form 40 line 7 imports federal AGI (1040 line 11), and federal
standard deduction is subtracted on line 17. Functionally the result
is identical to "starting from federal taxable income" because Idaho
uses federal std deduction one-for-one. We tag the plugin with
``StateStartingPoint.FEDERAL_TAXABLE_INCOME`` to match the existing
enum convention used by Oregon (or_.py); the form-line nuance is
documented here for the human reader.

History (2025 rate cut):
    Idaho House Bill 40 (2025) — "Idaho delivers largest income
    tax cut in state history, sending another $253 million back to
    Idahoans" — reduced the flat rate from 5.69% to 5.3% effective
    2025-01-01. This is the rate in force for the entire TY2025.

Source documents (verified 2026-04-11):
    - 2025 Form 40 PDF
      https://tax.idaho.gov/wp-content/uploads/forms/EFO00089/EFO00089_10-02-2025.pdf
      (also available via the Form 40 landing page:
       https://tax.idaho.gov/taxes/income-tax/individual-income/forms/form-40/)
    - 2025 Individual Income Tax Rate Schedule
      https://tax.idaho.gov/taxes/income-tax/individual-income/individual-income-tax-rate-schedule/
    - "What's new for 2025 income tax returns" (Idaho State Tax
      Commission press release; covers conformity to OBBBA standard
      deduction increase, senior deduction, etc.)
      https://tax.idaho.gov/pressrelease/whats-new-for-2025-income-tax-returns/
    - "Idaho delivers largest income tax cut in state history"
      (Office of the Governor, 2025) — H.B. 40 / 5.3% flat rate
      https://gov.idaho.gov/pressrelease/idaho-delivers-largest-income-tax-cut-in-state-history-sending-another-253-million-back-to-idahoans/
    - 2025 Idaho E-File (MeF) Handbook EPB00070
      https://tax.idaho.gov/document-mngr/pubs_epb00070/

Reciprocity (verified against skill/reference/state-reciprocity.json):
    Idaho has **no** bilateral reciprocity agreements. Idaho residents
    who work in WA (no income tax), MT, OR, NV, UT, or WY are taxed
    on all worldwide income by Idaho and claim a credit on Form 40
    line 50 / Form 39R for tax paid to other states. Notably, Idaho
    is the only Mountain West state without a reciprocity agreement
    with any neighbor.

Submission channel:
    Idaho participates in the IRS Fed/State MeF program; individual
    e-filing is routed through approved commercial software per the
    2025 Idaho E-File Handbook EPB00070. Idaho does **not** operate
    a free direct-entry portal for individual income tax returns
    (the Tax Commission directs taxpayers to free-file partner
    software such as TurboTax / TaxAct / FreeTaxUSA). Canonical
    channel: ``SubmissionChannel.FED_STATE_PIGGYBACK``.

Nonresident / part-year handling:
    Day-based proration of the resident-basis tax is the v0.1
    stopgap. The correct treatment is Idaho Form 43 (Part-Year /
    Nonresident) with income sourcing rules (wages to ID work
    location, rental to ID property, investment income to domicile).
    TODO(id-form-43) tracks this.

Graph-backend output-field gaps:
    - state_tax_bracket returns 0.0 (graph backend doesn't expose
      marginal rate for ID).
    - state_effective_tax_rate returns 0.0.
    - These are pinned in tests so any upstream tenforty fix
      (populating real values) trips CI.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import tenforty


# ---------------------------------------------------------------------------
# Layer 1: ID Form 40 field dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IDForm40Fields:
    """Frozen snapshot of ID Form 40 line values, ready for rendering.

    Field names correspond to state_specific keys produced by
    IdahoPlugin.compute().
    """

    state_adjusted_gross_income: Decimal = Decimal("0")
    state_taxable_income: Decimal = Decimal("0")
    state_total_tax: Decimal = Decimal("0")
    state_total_tax_resident_basis: Decimal = Decimal("0")
    state_tax_bracket: Decimal = Decimal("0")
    state_effective_tax_rate: Decimal = Decimal("0")
    apportionment_fraction: Decimal = Decimal("1")


def _build_form40_fields(state_return: "StateReturn") -> IDForm40Fields:
    """Map StateReturn.state_specific to IDForm40Fields."""
    ss = state_return.state_specific
    return IDForm40Fields(
        state_adjusted_gross_income=ss.get("state_adjusted_gross_income", Decimal("0")),
        state_taxable_income=ss.get("state_taxable_income", Decimal("0")),
        state_total_tax=ss.get("state_total_tax", Decimal("0")),
        state_total_tax_resident_basis=ss.get("state_total_tax_resident_basis", Decimal("0")),
        state_tax_bracket=ss.get("state_tax_bracket", Decimal("0")),
        state_effective_tax_rate=ss.get("state_effective_tax_rate", Decimal("0")),
        apportionment_fraction=ss.get("apportionment_fraction", Decimal("1")),
    )

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


# Tenforty backend used for the Idaho calc. The OTS backend does not
# register ID_FORM40 in NATURAL_FORM_CONFIG, so the default call path
# raises ``ValueError: OTS does not support 2025/ID_FORM40``. The graph
# backend is the only working path. See module docstring.
_TENFORTY_BACKEND = "graph"


@dataclass(frozen=True)
class IdahoPlugin:
    """State plugin for Idaho — TY2025.

    Wraps tenforty / OpenTaxSolver (graph backend) for Idaho Form 40
    resident calculation, with day-based apportionment for nonresident
    / part-year filers (Form 43 stub).

    Idaho applies a flat 5.3% rate (effective 2025-01-01 per H.B. 40,
    2025) to Idaho taxable income above $4,811 (Single/MFS/HoH) or
    $9,622 (MFJ). Conforms to federal standard deduction.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Reuse federal marshaling so ID sees exactly what the federal
        # calc did.
        tf_input = _to_tenforty_input(return_)

        tf_result = tenforty.evaluate_return(
            year=tf_input.year,
            state="ID",
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
        # TODO(id-form-43): replace with ID Form 43 income-source
        # apportionment in fan-out.
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
            "id_flat_rate": Decimal("0.053"),
            "id_zero_bracket_top_single": Decimal("4811"),
            "id_zero_bracket_top_mfj": Decimal("9622"),
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
        """Split canonical income into ID-source vs non-ID-source.

        Residents: everything is ID-source. Nonresident / part-year:
        prorate each category by days_in_state / 365.

        TODO(id-form-43): ID Form 43 sources income by type — wages
        to the Idaho work location, rental to the Idaho property,
        investment income to the taxpayer's domicile. Day-based
        proration is the shared first-cut across fan-out plugins.
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
            state_source_wages=sourced_or_prorated_wages(return_, "ID", wages, days_in_state),
            state_source_interest=day_prorate(interest, days_in_state),
            state_source_dividends=day_prorate(ord_div, days_in_state),
            state_source_capital_gains=day_prorate(
                capital_gains, days_in_state
            ),
            state_source_self_employment=sourced_or_prorated_schedule_c(return_, "ID", se_net, days_in_state),
            state_source_rental=day_prorate(rental_net, days_in_state),
        )

    def render_pdfs(
        self, state_return: StateReturn, out_dir: Path
    ) -> list[Path]:
        from dataclasses import asdict

        from skill.scripts.output._acroform_overlay import (
            fill_acroform_pdf,
            format_money,
            load_widget_map,
            fetch_and_verify_source_pdf,
        )

        _REF = Path(__file__).resolve().parents[2] / "reference"
        wmap = load_widget_map(_REF / "id-40-acroform-map.json")
        fetch_and_verify_source_pdf(
            _REF / "state_forms" / "id_40.pdf",
            wmap.source_pdf_url,
            wmap.source_pdf_sha256,
        )

        fields = _build_form40_fields(state_return)
        widget_values: dict[str, str] = {}
        for sem_name, value in asdict(fields).items():
            for wn in wmap.widget_names_for(sem_name):
                widget_values[wn] = (
                    format_money(value)
                    if isinstance(value, Decimal)
                    else str(value) if value else ""
                )

        out_path = out_dir / "ID_Form40.pdf"
        fill_acroform_pdf(
            _REF / "state_forms" / "id_40.pdf", widget_values, out_path
        )
        return [out_path]

    def form_ids(self) -> list[str]:
        return ["ID Form 40"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = IdahoPlugin(
    meta=StatePluginMeta(
        code="ID",
        name="Idaho",
        has_income_tax=True,
        # Idaho is one of the FEDERAL_TAXABLE_INCOME states (per the
        # _plugin_api.py enum docstring's "CO, ID, ND, SC, OR, UT" list).
        # Form 40 imports federal AGI on line 7 and subtracts the
        # federal standard deduction on line 17 — functionally
        # equivalent to starting from federal taxable income because
        # Idaho conforms to the federal std deduction.
        starting_point=StateStartingPoint.FEDERAL_TAXABLE_INCOME,
        dor_url="https://tax.idaho.gov/",
        # Idaho does not operate a free direct-entry e-file portal;
        # the Tax Commission directs taxpayers to free-file partner
        # software (FreeTaxUSA, etc.) listed at
        # https://tax.idaho.gov/taxes/income-tax/individual-income/free-file/
        free_efile_url=None,
        submission_channel=SubmissionChannel.FED_STATE_PIGGYBACK,
        # No reciprocity agreements — verified absent from
        # skill/reference/state-reciprocity.json. Idaho is the only
        # Mountain West state without a reciprocity agreement with
        # any neighbor.
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Wraps tenforty/OpenTaxSolver (graph backend — ID is not "
            "on the OTS backend) for ID Form 40. Flat 5.3% rate per "
            "2025 H.B. 40 (effective 2025-01-01, reduced from 5.69%) "
            "applied to Idaho taxable income above $4,811 (Single) / "
            "$9,622 (MFJ) zero-bracket exemption. Conforms to federal "
            "standard deduction $15,750 Single (2025 H.B. 559 / OBBBA "
            "conformity). Starting point: federal AGI (Form 40 line "
            "7), with federal std deduction subtracted on line 17. "
            "No reciprocity agreements. $65k Single hand calc and "
            "graph backend exact match: $2,355.267."
        ),
    )
)
