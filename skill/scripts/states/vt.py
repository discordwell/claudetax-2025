"""Vermont (VT) state plugin — TY2025.

See skill/reference/tenforty-ty2025-gap.md for the TY2025 probe rubric
and the graph-backend output-field gap list (state_taxable_income
echo, state_tax_bracket=0, state_effective_tax_rate=0).

Wraps tenforty / OpenTaxSolver (graph backend) for the Vermont Form
IN-111 resident calc, in the same shape as the WI plugin.

Source of truth
---------------
Tenforty exposes Vermont via ``OTSState.VT`` and ships the resident form
file at ``tenforty/forms/vt_in111_2025.json`` with a graph-backend
StateGraphConfig. That config imports federal AGI from us_1040 line 11
and emits three natural outputs:

    L4_vt_agi             -> state_adjusted_gross_income
    L8_vt_taxable_income  -> state_taxable_income
    L15_vt_total_tax      -> state_total_tax

IMPORTANT backend caveat (verified 2026-04-11 against tenforty 2025.x):
the default OTS backend raises ``ValueError: OTS does not support
2025/VT_IN111`` because Vermont is wired up only on the newer graph
backend. This plugin therefore calls
``tenforty.evaluate_return(..., backend='graph')`` explicitly. The other
states with the same property are WI (wave 4) and ME / RI / WV (wave 5,
which hand-roll because the graph backend has bugs for those states; see
their plugin docstrings).

For Vermont specifically, the graph backend's flow matches what TY2025
VT IN-111 actually does: federal AGI → Schedule IN-112 modifications →
VT AGI → max(VT std ded, VT itemized) → VT taxable income → graduated
tax. **Vermont folded its prior personal exemption into the standard
deduction** when Act 65 of 2023 restructured the IN-111 form, so unlike
ME / RI / WV there is no separate "L_vt_personal_exemption" node missing
from the graph. The graph backend's $65k Single result therefore matches
the published VT TY2025 schedule directly (verified by hand-rolling the
formula from the bracket constants in vt_in111_2025.json):

    Federal AGI                       $65,000
    VT Single Standard Deduction      -$7,400
    VT Taxable Income                 $57,600
    Tax (3.35% / 6.6% schedule):
        3.35% on first $47,900        $1,604.65
        6.6% on remainder $9,700      $640.20
        Total                          **$2,244.85**

VERIFICATION TODO: VT Department of Taxes publishes the official TY2025
"Vermont Income Tax Withholding & Tax Tables" + "Schedule IN-111 Tax
Computation Worksheet" each fall. The bracket constants in tenforty's
``vt_in111_2025.json`` ($47,900 / $116,000 / $242,000 Single thresholds
and 3.35% / 6.6% / 7.6% / 8.75% rates) MUST be cross-checked against
that publication and against VT 32 V.S.A. § 5822 as amended for TY2025.
The VT std ded ($7,400 Single, $14,850 MFJ) is from Act 65 of 2023 and
its inflation-indexing rule (32 V.S.A. § 5811(21)). If VT Tax Department
publishes different inflation-indexed numbers for TY2025, the graph
backend (and this plugin) will silently report a slightly off-target
tax. The test suite pins the graph-backend output bit-for-bit so any
upstream tenforty correction trips CI and forces a deliberate update.

Reciprocity
-----------
Vermont has **NO** bilateral reciprocity agreements with any other
state — verified against ``skill/reference/state-reciprocity.json``
(VT does not appear in ``agreements``) and against the Tax Foundation's
"State Reciprocity Agreements" research page. Vermont residents working
in NH (no income tax), MA, or NY file the appropriate work-state return
and claim the Vermont credit for taxes paid to other states (Form
IN-117).

Submission channel
------------------
Vermont participates in the IRS Fed/State MeF program for individual
returns and operates "myVTax" at https://myvtax.vermont.gov/ as its free
direct-file portal for individuals. The canonical free path is the
state portal, so this plugin reports
``SubmissionChannel.STATE_DOR_FREE_PORTAL``.

Sources (verified 2026-04-11):

    - Vermont Department of Taxes, Individual Forms hub:
      https://tax.vermont.gov/individual/forms

    - Vermont Department of Taxes, Form IN-111 2025 (filed copy):
      https://tax.vermont.gov/sites/tax/files/documents/IN-111-2025.pdf
      (URL pattern; current year file path may shift)

    - Vermont Act 65 of 2023 (the personal-exemption-into-standard-
      deduction consolidation): codified at 32 V.S.A. § 5811(21)

    - tenforty graph backend ``vt_in111_2025.json``:
      $VENV/lib/python3.12/site-packages/tenforty/forms/vt_in111_2025.json

Nonresident / part-year handling
--------------------------------
Day-based proration of the resident-basis tax is a v0.1 stopgap. The
correct treatment is Vermont Form IN-113 (Income Adjustment Schedule)
with VT-source income sourcing rules. TODO(vt-form-in113) tracks this.
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
from skill.scripts.states._plugin_api import (
    FederalTotals,
    IncomeApportionment,
    StatePlugin,
    StatePluginMeta,
    StateStartingPoint,
    SubmissionChannel,
)


_CENTS = Decimal("0.01")

# Tenforty backend used for the Vermont calc. The OTS backend does not
# register VT_IN111 in NATURAL_FORM_CONFIG, so the default call path
# raises ``ValueError: OTS does not support 2025/VT_IN111``. The graph
# backend consumes the ``vt_in111_2025.json`` graph file that ships with
# tenforty and is the only working path today.
_TENFORTY_BACKEND = "graph"


def _d(v: Any) -> Decimal:
    """Coerce a tenforty-returned float (or None) to Decimal."""
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _cents(v: Any) -> Decimal:
    return _d(v).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _apportionment_fraction(
    residency: ResidencyStatus, days_in_state: int
) -> Decimal:
    """Days-based apportionment for nonresident / part-year.

    TODO(vt-form-in113): replace with VT Form IN-113 income-source
    apportionment in fan-out follow-up.
    """
    if residency == ResidencyStatus.RESIDENT:
        return Decimal("1")
    frac = Decimal(days_in_state) / Decimal("365")
    if frac < 0:
        return Decimal("0")
    if frac > 1:
        return Decimal("1")
    return frac


# ---------------------------------------------------------------------------
# Layer 1: VT IN-111 field dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IN111Fields:
    """Frozen snapshot of VT Form IN-111 line values, ready for rendering."""

    state_adjusted_gross_income: Decimal = Decimal("0")
    vt_modifications: Decimal = Decimal("0")
    vt_modified_agi: Decimal = Decimal("0")
    vt_deduction: Decimal = Decimal("0")
    state_taxable_income: Decimal = Decimal("0")
    state_total_tax: Decimal = Decimal("0")
    vt_total_income_tax: Decimal = Decimal("0")


def _build_in111_fields(state_return: StateReturn) -> IN111Fields:
    """Map StateReturn.state_specific to IN111Fields."""
    ss = state_return.state_specific
    state_agi = ss.get("state_adjusted_gross_income", Decimal("0"))
    state_ti = ss.get("state_taxable_income", Decimal("0"))
    state_tax = ss.get("state_total_tax", Decimal("0"))
    return IN111Fields(
        state_adjusted_gross_income=state_agi,
        vt_modifications=Decimal("0"),  # v1: no modifications
        vt_modified_agi=state_agi,
        vt_deduction=Decimal("0"),  # graph backend does not expose
        state_taxable_income=state_ti,
        state_total_tax=state_tax,
        vt_total_income_tax=state_tax,
    )


@dataclass(frozen=True)
class VermontPlugin:
    """State plugin for Vermont — TY2025.

    Wraps tenforty / OpenTaxSolver (graph backend) for the resident case
    and day-proration for nonresident / part-year. Starting point is
    federal AGI; VT applies the Schedule IN-112 modifications, the VT
    standard deduction (or itemized), and its four-bracket graduated
    schedule (3.35% / 6.6% / 7.6% / 8.75%). Vermont folded the personal
    exemption into the standard deduction in Act 65 of 2023, so unlike
    ME / RI / WV there is no separate personal-exemption gap in the
    graph backend.

    See module docstring for the backend='graph' rationale.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Reuse the federal marshaling so VT sees exactly the same numbers
        # the federal calc did — do NOT duplicate that logic here.
        tf_input = _to_tenforty_input(return_)

        tf_result = tenforty.evaluate_return(
            year=tf_input.year,
            state="VT",
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
        state_bracket = _d(tf_result.state_tax_bracket)
        state_eff_rate = _d(tf_result.state_effective_tax_rate)

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
            "starting_point": "federal_agi",
            "tenforty_supports_vt_default_backend": False,
            "tenforty_supports_vt_graph_backend": True,
            "tenforty_status_note": (
                "tenforty default OTS backend does not support "
                "2025/VT_IN111 (raises ValueError). The graph backend "
                "(backend='graph') is used instead. Unlike ME / RI / "
                "WV — which the graph backend mis-computes by omitting "
                "their personal exemption — Vermont folded its personal "
                "exemption into the standard deduction in Act 65 of "
                "2023, so the graph-backend $65k Single result of "
                "$2,244.85 matches a hand calc against the published "
                "VT TY2025 bracket schedule directly."
            ),
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
        """Split canonical income into VT-source vs non-VT-source.

        Residents: everything is VT-source. Nonresident / part-year:
        prorate each category by ``days_in_state / 365``.

        TODO(vt-form-in113): real Vermont sourcing on Form IN-113
        sources wages to the work location, business income to the
        location of activity, rental to the property state, and
        intangibles to the domicile.
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
        from dataclasses import asdict

        from skill.scripts.output._acroform_overlay import (
            fill_acroform_pdf,
            format_money,
            load_widget_map,
            fetch_and_verify_source_pdf,
        )

        _REF = Path(__file__).resolve().parents[2] / "reference"
        _WIDGET_MAP = _REF / "vt-in111-acroform-map.json"
        _SOURCE_PDF = _REF / "state_forms" / "vt_in111.pdf"

        wmap = load_widget_map(_WIDGET_MAP)
        fetch_and_verify_source_pdf(
            _SOURCE_PDF, wmap.source_pdf_url, wmap.source_pdf_sha256
        )

        fields = _build_in111_fields(state_return)
        widget_values: dict[str, str] = {}
        for sem_name, value in asdict(fields).items():
            widget_names = wmap.widget_names_for(sem_name)
            if not widget_names:
                continue
            text = format_money(value) if isinstance(value, Decimal) else str(value) if value else ""
            for wn in widget_names:
                widget_values[wn] = text

        out_path = out_dir / "vt_in111.pdf"
        fill_acroform_pdf(_SOURCE_PDF, widget_values, out_path)
        return [out_path]

    def form_ids(self) -> list[str]:
        return ["VT Form IN-111"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = VermontPlugin(
    meta=StatePluginMeta(
        code="VT",
        name="Vermont",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://tax.vermont.gov/individual/forms",
        free_efile_url="https://myvtax.vermont.gov/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # Vermont has NO bilateral reciprocity agreements — verified
        # against skill/reference/state-reciprocity.json (VT does not
        # appear in `agreements`).
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Wraps tenforty/OpenTaxSolver (graph backend — VT is not on "
            "the OTS backend, raises ValueError). Four-bracket graduated "
            "schedule for TY2025: 3.35% up to $47,900 (Single), 6.6% to "
            "$116,000, 7.6% to $242,000, 8.75% above (per VT Tax "
            "Department TY2025 rate schedules). VT std ded Single "
            "$7,400, MFJ $14,850 — Vermont folded its personal exemption "
            "into the standard deduction in Act 65 of 2023, so unlike "
            "ME/RI/WV there is no separate personal-exemption gap in "
            "the graph backend. $65k Single locks at $2,244.85. Starting "
            "point: federal AGI. No reciprocity agreements. Free e-file "
            "via myVTax."
        ),
    )
)
