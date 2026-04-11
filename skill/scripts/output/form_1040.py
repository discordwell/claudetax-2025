"""Form 1040 output renderer — two layers.

* **Layer 1** (``compute_form_1040_fields``) maps a computed
  ``CanonicalReturn`` onto a dataclass whose field names mirror the
  Form 1040 (TY2024 layout, semantically stable for TY2025) line
  numbers. It does NOT recompute any tax figures — every line comes
  from values already populated by ``skill.scripts.calc.engine.compute``.

* **Layer 2** (``render_form_1040_pdf``) overlays the dataclass values
  onto the IRS fillable ``f1040.pdf`` via the wave-4 AcroForm widget
  map (``skill/reference/form-1040-acroform-map.json``). The IRS PDF
  is bundled at ``skill/reference/irs_forms/f1040.pdf`` and verified by
  SHA-256 at fill time; if the bundled copy is missing or corrupt the
  renderer downloads a fresh copy from the IRS-hosted URL pinned in
  the map JSON. If the download or SHA-256 verification fails the
  renderer raises ``RuntimeError`` — there is **no silent fallback** to
  the old reportlab scaffold.

Design goals
------------
* No tax re-computation. Trust ``ComputedTotals``.
* No modification of the engine, models, or registry.
* Layer 2 is a thin wrapper around
  :mod:`skill.scripts.output._acroform_overlay`, which is shared with
  the Schedule A/B/C/SE renderers in the next wave.

Simplifications documented here (tracked for later waves):
* All 1099-R distributions are assumed to land on line 4a/4b (IRA). True
  pension vs. IRA classification from box7 codes is deferred.
* SSA-1099 taxable portion (line 6b) is left at 0 — the SS-benefits
  worksheet patch is not yet implemented.
* Line 2a (tax-exempt interest) is read from Form1099INT.box8 if the
  model exposes it; otherwise 0.
* Line 13 (QBI) is 0 — QBI patch is not yet wired in.
* Line 17 (Schedule 2 Part I) is 0 — AMT/excess APTC is not yet modeled.
* Line 20 / line 31 (Schedule 3) are 0 — nonrefundable credits beyond CTC
  and refundable credits beyond EITC/ACTC/AOTC are not yet modeled.
* TY2025 PDF renumbers line 12 → 12e and line 13 → 13a (with new 13b
  for Schedule 1-A additional deductions). The widget map records the
  renumbering so the Layer 1 ``line_12_*`` / ``line_13_*`` values land
  in the correct widgets.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from skill.scripts.calc.engine import (
    _sum_part_i_additional_income,
    schedule_c_net_profit,
    schedule_e_total_net,
)
from skill.scripts.models import CanonicalReturn
from skill.scripts.output._acroform_overlay import (
    WidgetMap,
    fetch_and_verify_source_pdf,
    fill_acroform_pdf,
    format_money,
    load_widget_map,
)


_ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Layer 1: field dataclass and computation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Form1040Fields:
    """Frozen snapshot of Form 1040 line values, ready for rendering.

    Field names follow the TY2024 Form 1040 line numbers (assumed stable
    for TY2025). All numeric fields are ``Decimal``.
    """

    # Filing / header (non-Decimal)
    filing_status: str = ""
    taxpayer_name: str = ""
    spouse_name: str | None = None

    # Income
    line_1a_total_w2_box1: Decimal = _ZERO
    line_1z_total_wages: Decimal = _ZERO
    line_2a_tax_exempt_interest: Decimal = _ZERO
    line_2b_taxable_interest: Decimal = _ZERO
    line_3a_qualified_dividends: Decimal = _ZERO
    line_3b_ordinary_dividends: Decimal = _ZERO
    line_4a_ira_distributions: Decimal = _ZERO
    line_4b_ira_taxable_amount: Decimal = _ZERO
    line_5a_pensions_and_annuities: Decimal = _ZERO
    line_5b_pensions_taxable_amount: Decimal = _ZERO
    line_6a_social_security_benefits: Decimal = _ZERO
    line_6b_ss_taxable_amount: Decimal = _ZERO
    line_7_capital_gain_or_loss: Decimal = _ZERO
    line_8_additional_income_from_sch_1: Decimal = _ZERO
    line_9_total_income: Decimal = _ZERO
    line_10_adjustments_from_sch_1: Decimal = _ZERO
    line_11_adjusted_gross_income: Decimal = _ZERO
    line_12_standard_or_itemized_deduction: Decimal = _ZERO
    line_13_qbi_deduction: Decimal = _ZERO
    line_14_sum_12_13: Decimal = _ZERO
    line_15_taxable_income: Decimal = _ZERO

    # Tax and credits
    line_16_tax: Decimal = _ZERO
    line_17_amount_from_sch_2_line_3: Decimal = _ZERO
    line_18_sum_16_17: Decimal = _ZERO
    line_19_child_tax_credit_and_odc: Decimal = _ZERO
    line_20_amount_from_sch_3_line_8: Decimal = _ZERO
    line_21_sum_19_20: Decimal = _ZERO
    line_22_subtract_21_from_18: Decimal = _ZERO
    line_23_other_taxes_from_sch_2_line_21: Decimal = _ZERO
    line_24_total_tax: Decimal = _ZERO

    # Payments
    line_25a_w2_withholding: Decimal = _ZERO
    line_25b_1099_withholding: Decimal = _ZERO
    line_25c_other_withholding: Decimal = _ZERO
    line_25d_total_withholding: Decimal = _ZERO
    line_26_estimated_and_prior_year_applied: Decimal = _ZERO
    line_27_earned_income_credit: Decimal = _ZERO
    line_28_additional_child_tax_credit: Decimal = _ZERO
    line_29_american_opportunity_credit_refundable: Decimal = _ZERO
    line_31_amount_from_sch_3_line_15: Decimal = _ZERO
    line_32_sum_27_through_31: Decimal = _ZERO
    line_33_total_payments: Decimal = _ZERO

    # Refund / owed
    line_34_overpayment: Decimal = _ZERO
    line_35a_refund_requested: Decimal = _ZERO
    line_37_amount_you_owe: Decimal = _ZERO


def _dec(x: Decimal | None) -> Decimal:
    """Coerce an Optional[Decimal] to a concrete Decimal (None -> 0)."""
    return x if x is not None else _ZERO


def _sum(iterable) -> Decimal:
    return sum(iterable, start=_ZERO)


def compute_form_1040_fields(return_: CanonicalReturn) -> Form1040Fields:
    """Map a computed CanonicalReturn onto a Form1040Fields dataclass.

    The input ``return_`` must have been passed through
    ``skill.scripts.calc.engine.compute`` so that ``ComputedTotals`` is
    populated. This function NEVER recomputes tax — it only routes
    already-computed values onto Form 1040 line names.
    """
    c = return_.computed

    # -- header ---------------------------------------------------------
    filing_status = return_.filing_status.value
    taxpayer_name = f"{return_.taxpayer.first_name} {return_.taxpayer.last_name}"
    spouse_name = (
        f"{return_.spouse.first_name} {return_.spouse.last_name}"
        if return_.spouse is not None
        else None
    )

    # -- Line 1: wages --------------------------------------------------
    w2_box1_total = _sum(w.box1_wages for w in return_.w2s)
    line_1a = w2_box1_total
    line_1z = w2_box1_total  # v1: composite = 1a only

    # -- Line 2: interest ----------------------------------------------
    line_2a = _sum(f.box8_tax_exempt_interest for f in return_.forms_1099_int)
    line_2b = _sum(f.box1_interest_income for f in return_.forms_1099_int)

    # -- Line 3: dividends ---------------------------------------------
    line_3a = _sum(f.box1b_qualified_dividends for f in return_.forms_1099_div)
    line_3b = _sum(f.box1a_ordinary_dividends for f in return_.forms_1099_div)

    # -- Line 4: IRA distributions (v1: all 1099-R routed here) --------
    # Note: full 1099-R classification (IRA vs pension) via box7 codes is
    # deferred — see module docstring.
    line_4a = _sum(f.box1_gross_distribution for f in return_.forms_1099_r)
    line_4b = _sum(f.box2a_taxable_amount for f in return_.forms_1099_r)

    # -- Line 5: pensions (v1: unused) ---------------------------------
    line_5a = _ZERO
    line_5b = _ZERO

    # -- Line 6: social security ---------------------------------------
    line_6a = _sum(f.box5_net_benefits for f in return_.forms_ssa_1099)
    # v1: SS-benefits worksheet not yet implemented — leave taxable at 0.
    line_6b = _ZERO

    # -- Line 7: capital gain/loss -------------------------------------
    # Mirror engine._to_tenforty_input short/long-term aggregation.
    st_1099b = _ZERO
    lt_1099b = _ZERO
    for form in return_.forms_1099_b:
        for txn in form.transactions:
            gain = txn.proceeds - txn.cost_basis + txn.adjustment_amount
            if txn.is_long_term:
                lt_1099b += gain
            else:
                st_1099b += gain
    cap_gain_distr_sum = _sum(
        f.box2a_total_capital_gain_distributions for f in return_.forms_1099_div
    )
    line_7 = st_1099b + lt_1099b + cap_gain_distr_sum

    # -- Line 8: additional income from Schedule 1 --------------------
    line_8 = _sum_part_i_additional_income(return_)

    # -- Line 9: total income (trust the engine) ----------------------
    line_9 = _dec(c.total_income)

    # -- Line 10: adjustments / Line 11: AGI / Line 12: deduction ------
    line_10 = _dec(c.adjustments_total)
    line_11 = _dec(c.adjusted_gross_income)
    line_12 = _dec(c.deduction_taken)
    line_13 = _ZERO  # QBI not yet patched
    line_14 = line_12 + line_13
    line_15 = _dec(c.taxable_income)

    # -- Line 16: tax / Line 17-21: credits ---------------------------
    line_16 = _dec(c.tentative_tax)
    line_17 = _ZERO  # Schedule 2 Part I not yet patched
    line_18 = line_16 + line_17
    line_19 = return_.credits.child_tax_credit + return_.credits.credit_for_other_dependents
    line_20 = _ZERO  # Schedule 3 Part I (nonrefundable) not yet patched
    line_21 = line_19 + line_20
    line_22 = max(_ZERO, line_18 - line_21)
    line_23 = _dec(c.other_taxes_total)
    line_24 = _dec(c.total_tax)

    # -- Line 25: withholding -----------------------------------------
    line_25a = _sum(w.box2_federal_income_tax_withheld for w in return_.w2s)

    # 1099 withholding: sum box4 across INT/DIV/B/NEC/R/G
    withholding_1099 = (
        _sum(f.box4_federal_income_tax_withheld for f in return_.forms_1099_int)
        + _sum(f.box4_federal_income_tax_withheld for f in return_.forms_1099_div)
        + _sum(f.box4_federal_income_tax_withheld for f in return_.forms_1099_b)
        + _sum(f.box4_federal_income_tax_withheld for f in return_.forms_1099_nec)
        + _sum(f.box4_federal_income_tax_withheld for f in return_.forms_1099_r)
        + _sum(f.box4_federal_income_tax_withheld for f in return_.forms_1099_g)
    )
    line_25b = withholding_1099
    line_25c = return_.payments.federal_income_tax_withheld_other
    line_25d = line_25a + line_25b + line_25c

    # -- Line 26: estimated + prior-year overpayment ------------------
    line_26 = (
        return_.payments.estimated_tax_payments_2025
        + return_.payments.prior_year_overpayment_applied
    )

    # -- Lines 27-32: refundable credits ------------------------------
    line_27 = return_.credits.earned_income_tax_credit
    line_28 = return_.payments.additional_child_tax_credit_refundable
    line_29 = return_.payments.american_opportunity_credit_refundable
    line_31 = _ZERO  # Schedule 3 Part II (refundable, beyond the above) not yet patched
    line_32 = line_27 + line_28 + line_29 + line_31

    # -- Line 33: total payments --------------------------------------
    line_33 = line_25d + line_26 + line_32

    # -- Refund / owed (trust engine refund/amount_owed) --------------
    refund_val = c.refund if c.refund is not None else _ZERO
    owed_val = c.amount_owed if c.amount_owed is not None else _ZERO
    # Only one of refund / owed is non-zero at a time.
    if owed_val > 0:
        line_34 = _ZERO
        line_35a = _ZERO
        line_37 = owed_val
    else:
        line_34 = refund_val
        line_35a = refund_val  # v1: full refund requested, no ES rollforward
        line_37 = _ZERO

    return Form1040Fields(
        filing_status=filing_status,
        taxpayer_name=taxpayer_name,
        spouse_name=spouse_name,
        line_1a_total_w2_box1=line_1a,
        line_1z_total_wages=line_1z,
        line_2a_tax_exempt_interest=line_2a,
        line_2b_taxable_interest=line_2b,
        line_3a_qualified_dividends=line_3a,
        line_3b_ordinary_dividends=line_3b,
        line_4a_ira_distributions=line_4a,
        line_4b_ira_taxable_amount=line_4b,
        line_5a_pensions_and_annuities=line_5a,
        line_5b_pensions_taxable_amount=line_5b,
        line_6a_social_security_benefits=line_6a,
        line_6b_ss_taxable_amount=line_6b,
        line_7_capital_gain_or_loss=line_7,
        line_8_additional_income_from_sch_1=line_8,
        line_9_total_income=line_9,
        line_10_adjustments_from_sch_1=line_10,
        line_11_adjusted_gross_income=line_11,
        line_12_standard_or_itemized_deduction=line_12,
        line_13_qbi_deduction=line_13,
        line_14_sum_12_13=line_14,
        line_15_taxable_income=line_15,
        line_16_tax=line_16,
        line_17_amount_from_sch_2_line_3=line_17,
        line_18_sum_16_17=line_18,
        line_19_child_tax_credit_and_odc=line_19,
        line_20_amount_from_sch_3_line_8=line_20,
        line_21_sum_19_20=line_21,
        line_22_subtract_21_from_18=line_22,
        line_23_other_taxes_from_sch_2_line_21=line_23,
        line_24_total_tax=line_24,
        line_25a_w2_withholding=line_25a,
        line_25b_1099_withholding=line_25b,
        line_25c_other_withholding=line_25c,
        line_25d_total_withholding=line_25d,
        line_26_estimated_and_prior_year_applied=line_26,
        line_27_earned_income_credit=line_27,
        line_28_additional_child_tax_credit=line_28,
        line_29_american_opportunity_credit_refundable=line_29,
        line_31_amount_from_sch_3_line_15=line_31,
        line_32_sum_27_through_31=line_32,
        line_33_total_payments=line_33,
        line_34_overpayment=line_34,
        line_35a_refund_requested=line_35a,
        line_37_amount_you_owe=line_37,
    )


# ---------------------------------------------------------------------------
# Layer 2: pypdf AcroForm overlay onto the IRS fillable f1040.pdf
# ---------------------------------------------------------------------------


# Bundled artifact paths. The widget map is checked in by wave 4; the
# source PDF is fetched on first run and cached at the same location.
_REFERENCE_DIR = Path(__file__).resolve().parents[2] / "reference"
_WIDGET_MAP_JSON = _REFERENCE_DIR / "form-1040-acroform-map.json"
_SOURCE_PDF_PATH = _REFERENCE_DIR / "irs_forms" / "f1040.pdf"


# Layer 1 dataclass field names that are NOT money values.
_HEADER_STRING_FIELDS = {"filing_status", "taxpayer_name", "spouse_name"}

# Map Layer 1 ``filing_status`` values to the IRS five-checkbox label.
_FILING_STATUS_TO_CHECKBOX_LABEL = {
    "single": "SINGLE",
    "mfj": "MFJ",
    "mfs": "MFS",
    "hoh": "HOH",
    "qss": "QSS",
}


def _build_widget_values_for_form1040(
    fields: Form1040Fields, widget_map: WidgetMap
) -> dict[str, str | bool]:
    """Translate a :class:`Form1040Fields` instance into a widget-name dict.

    The output is keyed by fully-qualified widget name and ready to
    pass to :func:`fill_acroform_pdf`. Money fields are formatted via
    :func:`format_money` (zero collapses to empty string), the
    taxpayer/spouse name strings land in their text widgets, and the
    filing-status string toggles exactly one of the five
    filing-status checkboxes.
    """
    out: dict[str, str | bool] = {}

    # Numeric line fields → text widgets, including computed-copy mirrors.
    for sem_name, value in vars(fields).items():
        if sem_name in _HEADER_STRING_FIELDS:
            continue
        if not isinstance(value, Decimal):
            continue
        widget_names = widget_map.widget_names_for(sem_name)
        if not widget_names:
            continue
        text = format_money(value)
        for wn in widget_names:
            out[wn] = text

    # Header strings → text widgets (skip if widget map has no entry).
    if fields.taxpayer_name:
        for wn in widget_map.widget_names_for("taxpayer_name"):
            out[wn] = fields.taxpayer_name
    if fields.spouse_name:
        for wn in widget_map.widget_names_for("spouse_name"):
            out[wn] = fields.spouse_name

    # Filing status → exactly one of the five checkboxes.
    if fields.filing_status:
        label = _FILING_STATUS_TO_CHECKBOX_LABEL.get(
            fields.filing_status.lower()
        )
        if label is not None:
            for fs_label, widget_name in widget_map.filing_status_checkboxes.items():
                out[widget_name] = (fs_label == label)

    return out


def render_form_1040_pdf(fields: Form1040Fields, out_path: Path) -> Path:
    """Render a filled IRS Form 1040 PDF.

    Loads the wave-4 widget map, ensures the IRS source PDF is present
    and SHA-256 verified, builds the per-widget value dict from
    ``fields``, and writes the filled copy to ``out_path``.

    Parameters
    ----------
    fields
        A :class:`Form1040Fields` instance, typically the output of
        :func:`compute_form_1040_fields`.
    out_path
        Where to write the filled PDF. Parent directories are created.

    Returns
    -------
    Path
        ``out_path`` for caller convenience.

    Raises
    ------
    RuntimeError
        If the source PDF cannot be downloaded or its SHA-256 does not
        match the digest pinned in the widget map JSON. There is no
        silent fallback to a reportlab scaffold — callers should treat
        a missing source PDF as a hard failure and place a verified
        copy at ``skill/reference/irs_forms/f1040.pdf`` manually.
    """
    out_path = Path(out_path)

    widget_map = load_widget_map(_WIDGET_MAP_JSON)
    fetch_and_verify_source_pdf(
        _SOURCE_PDF_PATH,
        widget_map.source_pdf_url,
        widget_map.source_pdf_sha256,
    )

    widget_values = _build_widget_values_for_form1040(fields, widget_map)
    return fill_acroform_pdf(_SOURCE_PDF_PATH, widget_values, out_path)
