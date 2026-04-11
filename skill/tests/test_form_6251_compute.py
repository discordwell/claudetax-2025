"""Tests for Form 6251 (Alternative Minimum Tax) compute layer.

Covers:

* TY2025 constants (exemption, phase-out threshold, rate breakpoint)
* :func:`compute_amt_exemption` — phase-out arithmetic
* :func:`compute_amt_tentative_tax` — 26%/28% schedule
* :func:`compute_form_6251_fields` — end-to-end cases
* Engine integration — a high-income MFJ itemizer with $50k SALT
  triggering AMT; a $65k single standard-deduction filer with no AMT;
  the exemption phase-out at $1.3M MFJ; and the 26% -> 28% rate bracket
  crossing.

Authority: IRS Form 6251 (TY2025) and IRS Instructions for Form 6251
(TY2025). Constants are pulled from the face of the TY2025 form
(line 5 table, line 7 instruction text).
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.calc.engine import compute
from skill.scripts.models import (
    AdjustmentsToIncome,
    Address,
    AMTAdjustments,
    CanonicalReturn,
    ComputedTotals,
    FilingStatus,
    Form1099INT,
    ItemizedDeductions,
    Person,
    W2,
)
from skill.scripts.output.form_6251 import (
    AMT_EXEMPTION_MFJ_QSS,
    AMT_EXEMPTION_MFS,
    AMT_EXEMPTION_SINGLE_HOH,
    AMT_PHASEOUT_THRESHOLD_MFJ_QSS,
    AMT_PHASEOUT_THRESHOLD_MFS,
    AMT_PHASEOUT_THRESHOLD_SINGLE_HOH,
    AMT_RATE_BREAKPOINT_MFS,
    AMT_RATE_BREAKPOINT_NORMAL,
    AMT_RATE_FLAT_ADJUSTMENT_MFS,
    AMT_RATE_FLAT_ADJUSTMENT_NORMAL,
    AMT_RATE_HIGH,
    AMT_RATE_LOW,
    Form6251Fields,
    compute_amt_exemption,
    compute_amt_tentative_tax,
    compute_form_6251_fields,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _person(first: str = "Test", last: str = "Payer") -> Person:
    return Person(
        first_name=first,
        last_name=last,
        ssn="111-22-3333",
        date_of_birth="1985-06-15",
        is_blind=False,
        is_age_65_or_older=False,
    )


def _address() -> Address:
    return Address(street1="1 Test St", city="Springfield", state="IL", zip="62701")


def _minimal_return(
    *,
    filing_status: FilingStatus = FilingStatus.SINGLE,
    itemized: ItemizedDeductions | None = None,
    amt_adjustments_manual: AMTAdjustments | None = None,
    w2_wages: Decimal = Decimal("0"),
    spouse: Person | None = None,
) -> CanonicalReturn:
    """Build a minimal CanonicalReturn for Layer 1 compute unit tests."""
    needs_spouse = filing_status in (FilingStatus.MFJ, FilingStatus.MFS)
    if needs_spouse and spouse is None:
        spouse = _person("Spouse", "Two")
    w2_list = []
    if w2_wages > 0:
        w2_list.append(
            W2(
                employer_name="ACME",
                employer_ein="12-3456789",
                box1_wages=w2_wages,
            ).model_dump(mode="json")
        )
    return CanonicalReturn.model_validate(
        {
            "schema_version": "0.1.0",
            "tax_year": 2025,
            "filing_status": filing_status.value,
            "taxpayer": _person().model_dump(mode="json"),
            "spouse": spouse.model_dump(mode="json") if spouse else None,
            "address": _address().model_dump(mode="json"),
            "w2s": w2_list,
            "itemize_deductions": itemized is not None,
            "itemized": itemized.model_dump(mode="json") if itemized else None,
            "amt_adjustments_manual": amt_adjustments_manual.model_dump(mode="json")
            if amt_adjustments_manual is not None
            else None,
        }
    )


# ---------------------------------------------------------------------------
# TY2025 constants — authoritative pinned values
# ---------------------------------------------------------------------------


def test_ty2025_exemption_amounts_match_form_face() -> None:
    """IRS Form 6251 (TY2025) line 5 table.

    Single/HoH $88,100; MFJ/QSS $137,000; MFS $68,500.
    """
    assert AMT_EXEMPTION_SINGLE_HOH == Decimal("88100")
    assert AMT_EXEMPTION_MFJ_QSS == Decimal("137000")
    assert AMT_EXEMPTION_MFS == Decimal("68500")


def test_ty2025_phaseout_thresholds_match_form_face() -> None:
    """IRS Form 6251 (TY2025) line 5 table thresholds."""
    assert AMT_PHASEOUT_THRESHOLD_SINGLE_HOH == Decimal("626350")
    assert AMT_PHASEOUT_THRESHOLD_MFJ_QSS == Decimal("1252700")
    assert AMT_PHASEOUT_THRESHOLD_MFS == Decimal("626350")


def test_ty2025_rate_breakpoint_and_flat_adjustment() -> None:
    """IRS Form 6251 (TY2025) line 7 instruction."""
    assert AMT_RATE_BREAKPOINT_NORMAL == Decimal("239100")
    assert AMT_RATE_BREAKPOINT_MFS == Decimal("119550")
    assert AMT_RATE_LOW == Decimal("0.26")
    assert AMT_RATE_HIGH == Decimal("0.28")
    assert AMT_RATE_FLAT_ADJUSTMENT_NORMAL == Decimal("4782")
    assert AMT_RATE_FLAT_ADJUSTMENT_MFS == Decimal("2391")


def test_flat_adjustment_equals_breakpoint_times_rate_gap() -> None:
    """$4,782 = $239,100 × (0.28 - 0.26). Sanity — the flat adjustment
    keeps the slice under the breakpoint taxed at 26% flat."""
    assert AMT_RATE_FLAT_ADJUSTMENT_NORMAL == (
        AMT_RATE_BREAKPOINT_NORMAL * (AMT_RATE_HIGH - AMT_RATE_LOW)
    )
    assert AMT_RATE_FLAT_ADJUSTMENT_MFS == (
        AMT_RATE_BREAKPOINT_MFS * (AMT_RATE_HIGH - AMT_RATE_LOW)
    )


# ---------------------------------------------------------------------------
# compute_amt_exemption — phase-out arithmetic
# ---------------------------------------------------------------------------


def test_exemption_single_below_threshold() -> None:
    """Single with AMTI below $626,350 — full $88,100 exemption."""
    ex = compute_amt_exemption(FilingStatus.SINGLE, Decimal("500000"))
    assert ex == Decimal("88100")


def test_exemption_mfj_below_threshold() -> None:
    """MFJ with AMTI below $1,252,700 — full $137,000 exemption."""
    ex = compute_amt_exemption(FilingStatus.MFJ, Decimal("800000"))
    assert ex == Decimal("137000")


def test_exemption_mfs_below_threshold() -> None:
    ex = compute_amt_exemption(FilingStatus.MFS, Decimal("500000"))
    assert ex == Decimal("68500")


def test_exemption_hoh_matches_single() -> None:
    """HoH shares the Single exemption and phase-out threshold."""
    ex = compute_amt_exemption(FilingStatus.HOH, Decimal("500000"))
    assert ex == Decimal("88100")


def test_exemption_qss_matches_mfj() -> None:
    """QSS shares the MFJ exemption and phase-out threshold."""
    ex = compute_amt_exemption(FilingStatus.QSS, Decimal("800000"))
    assert ex == Decimal("137000")


def test_exemption_phaseout_at_threshold_plus_100k_single() -> None:
    """Single with AMTI = $726,350: $100k over threshold.

    Phase-out: 0.25 × $100,000 = $25,000. Exemption = $88,100 - $25,000
    = $63,100.
    """
    ex = compute_amt_exemption(FilingStatus.SINGLE, Decimal("726350"))
    assert ex == Decimal("63100.00")


def test_exemption_fully_phased_out_single() -> None:
    """Single with AMTI = $978,750: phase-out reaches the exemption.

    ($978,750 - $626,350) × 0.25 = $88,100. Exemption = $0.
    """
    ex = compute_amt_exemption(FilingStatus.SINGLE, Decimal("978750"))
    assert ex == Decimal("0")


def test_exemption_fully_phased_out_mfj_at_1_3m() -> None:
    """MFJ with AMTI = $1,300,000: phase-out eats most of the exemption.

    ($1,300,000 - $1,252,700) × 0.25 = $47,300 × 0.25 = $11,825.
    Exemption = $137,000 - $11,825 = $125,175.
    """
    ex = compute_amt_exemption(FilingStatus.MFJ, Decimal("1300000"))
    assert ex == Decimal("125175.00")


def test_exemption_fully_phased_out_mfj_at_1_8m() -> None:
    """MFJ with AMTI = $1,800,700 — phase-out eats the full exemption.

    ($1,800,700 - $1,252,700) × 0.25 = $548,000 × 0.25 = $137,000.
    Exemption = $0.
    """
    ex = compute_amt_exemption(FilingStatus.MFJ, Decimal("1800700"))
    assert ex == Decimal("0")


def test_exemption_beyond_full_phaseout_stays_zero() -> None:
    """Exemption never goes negative."""
    ex = compute_amt_exemption(FilingStatus.SINGLE, Decimal("2000000"))
    assert ex == Decimal("0")


# ---------------------------------------------------------------------------
# compute_amt_tentative_tax — rate schedule
# ---------------------------------------------------------------------------


def test_tentative_tax_zero_below_zero() -> None:
    assert compute_amt_tentative_tax(FilingStatus.SINGLE, Decimal("-100")) == Decimal("0")
    assert compute_amt_tentative_tax(FilingStatus.SINGLE, Decimal("0")) == Decimal("0")


def test_tentative_tax_flat_26pct_under_breakpoint() -> None:
    """Single with line_6 = $100,000: straight 26%.

    $100,000 × 0.26 = $26,000.
    """
    tmt = compute_amt_tentative_tax(FilingStatus.SINGLE, Decimal("100000"))
    assert tmt == Decimal("26000.00")


def test_tentative_tax_exactly_at_breakpoint() -> None:
    """Single with line_6 = $239,100 — still flat 26%.

    $239,100 × 0.26 = $62,166.
    """
    tmt = compute_amt_tentative_tax(FilingStatus.SINGLE, Decimal("239100"))
    assert tmt == Decimal("62166.00")


def test_tentative_tax_just_above_breakpoint_uses_28pct() -> None:
    """Single with line_6 = $239,101 — crosses into the 28% zone.

    Formula: $239,101 × 0.28 - $4,782 = $66,948.28 - $4,782 = $62,166.28.
    Expected is smoothly one cent above the 26% figure (rounding aside).
    """
    tmt = compute_amt_tentative_tax(FilingStatus.SINGLE, Decimal("239101"))
    # $239,101 × 0.28 = $66,948.28; - $4,782 = $62,166.28
    assert tmt == Decimal("62166.28")


def test_tentative_tax_crossing_bracket_continuity() -> None:
    """The 26%/28% schedule is continuous at the breakpoint.

    Difference between line_6 = $239,100 (flat 26%) and $239,101 (28%
    with flat adjustment) should be exactly ($1 × 0.28) = $0.28.
    """
    at = compute_amt_tentative_tax(FilingStatus.SINGLE, Decimal("239100"))
    above = compute_amt_tentative_tax(FilingStatus.SINGLE, Decimal("239101"))
    assert above - at == Decimal("0.28")


def test_tentative_tax_well_above_breakpoint() -> None:
    """Single with line_6 = $500,000.

    $500,000 × 0.28 - $4,782 = $140,000 - $4,782 = $135,218.
    """
    tmt = compute_amt_tentative_tax(FilingStatus.SINGLE, Decimal("500000"))
    assert tmt == Decimal("135218.00")


def test_tentative_tax_mfs_uses_half_breakpoint() -> None:
    """MFS with line_6 = $119,550 — flat 26%, half the normal breakpoint.

    $119,550 × 0.26 = $31,083.
    """
    tmt = compute_amt_tentative_tax(FilingStatus.MFS, Decimal("119550"))
    assert tmt == Decimal("31083.00")


def test_tentative_tax_mfs_just_above_breakpoint() -> None:
    """MFS with line_6 = $119,551 — crosses into 28% with halved flat."""
    tmt = compute_amt_tentative_tax(FilingStatus.MFS, Decimal("119551"))
    # $119,551 × 0.28 - $2,391 = $33,474.28 - $2,391 = $31,083.28
    assert tmt == Decimal("31083.28")


# ---------------------------------------------------------------------------
# compute_form_6251_fields — Layer 1 end-to-end
# ---------------------------------------------------------------------------


def test_65k_single_standard_deduction_amt_zero() -> None:
    """$65k Single with the standard deduction — no AMT trigger, zero AMT.

    The engine should not even fire the AMT path because there is no
    SALT add-back and no manual AMTAdjustments. But the Layer 1 compute
    function itself can still be called directly; it should return 0.
    """
    r = _minimal_return(
        filing_status=FilingStatus.SINGLE,
        w2_wages=Decimal("65000"),
    )
    computed = compute(r)
    assert computed.computed.alternative_minimum_tax is None, (
        "engine should skip AMT path entirely when no trigger item present"
    )

    # Calling compute_form_6251_fields directly still produces zero AMT.
    fields = compute_form_6251_fields(computed)
    assert fields.line_4_amti < Decimal("88100")  # Well below exemption
    assert fields.line_5_exemption == Decimal("88100")
    assert fields.line_6_subtract_5_from_4 == Decimal("0")
    assert fields.line_7_tentative_amt_26_or_28pct == Decimal("0")
    assert fields.line_11_amt_owed == Decimal("0")


def test_high_income_mfj_with_50k_salt_triggers_amt() -> None:
    """High-income MFJ with a $50k raw SALT hits the SALT cap AND gets AMT.

    With $900k of wages MFJ and $50k state income tax, the Schedule A
    SALT capped amount is $10k (the permanent SALT cap). That $10k is
    added back on Form 6251 line 2a. The filer's regular tax is still
    big enough to exceed TMT in many cases, so AMT may still be 0 — but
    we still expect the compute path to FIRE (trigger is detected).
    """
    it = ItemizedDeductions(
        state_and_local_income_tax=Decimal("50000"),
    )
    r = _minimal_return(
        filing_status=FilingStatus.MFJ,
        itemized=it,
        w2_wages=Decimal("900000"),
    )
    computed = compute(r)
    # The AMT path fires (trigger detected) and returns either 0 or a
    # positive AMT. Either way ``alternative_minimum_tax`` is populated.
    assert computed.computed.alternative_minimum_tax is not None

    # Compute-directly check: SALT add-back shows up on line 2a.
    fields = compute_form_6251_fields(computed)
    assert fields.line_2a_taxes_from_sch_a_7 == Decimal("10000.00")


def test_high_iso_exercise_single_produces_amt_owed() -> None:
    """Single filer with a $500k ISO bargain element owes AMT.

    Hand-walked arithmetic (Layer 1 direct, uncomputed return):
      * Start with zero taxable income (no wages) and zero deduction.
      * line 1b = 0, line 2i = $500,000, line 4 (AMTI) = $500,000.
      * Single exemption starts at $88,100 — $500k is below the
        $626,350 threshold, so no phase-out; line 5 = $88,100.
      * line 6 = $500,000 - $88,100 = $411,900.
      * line 6 is above the $239,100 breakpoint, so line 7 =
        $411,900 × 0.28 - $4,782 = $115,332 - $4,782 = $110,550.
      * line 9 (TMT) = $110,550 (no AMT FTC).
      * line 10 (regular tax) = $0 on this synthetic return.
      * line 11 (AMT) = $110,550.
    """
    amt_adj = AMTAdjustments(iso_bargain_element=Decimal("500000"))
    r = _minimal_return(
        filing_status=FilingStatus.SINGLE,
        amt_adjustments_manual=amt_adj,
    )
    # Build a fake ComputedTotals so Layer 1 sees zero taxable income /
    # regular tax — directly exercises the AMT arithmetic.
    r = r.model_copy(
        update={
            "computed": ComputedTotals(
                total_income=Decimal("0"),
                adjustments_total=Decimal("0"),
                adjusted_gross_income=Decimal("0"),
                deduction_taken=Decimal("0"),
                taxable_income=Decimal("0"),
                tentative_tax=Decimal("0"),
            )
        }
    )
    fields = compute_form_6251_fields(r)

    assert fields.line_2i_iso_exercise == Decimal("500000")
    assert fields.line_4_amti == Decimal("500000.00")
    assert fields.line_5_exemption == Decimal("88100")
    assert fields.line_6_subtract_5_from_4 == Decimal("411900.00")
    assert fields.line_7_tentative_amt_26_or_28pct == Decimal("110550.00")
    assert fields.line_9_tentative_minimum_tax == Decimal("110550.00")
    assert fields.line_10_regular_tax_for_amt == Decimal("0")
    assert fields.line_11_amt_owed == Decimal("110550.00")


def test_exemption_phaseout_at_1_3m_mfj() -> None:
    """MFJ with $1.3M of AMTI — exemption phases partially out to $125,175.

    Hand-walked (Layer 1 direct):
      * line 4 (AMTI) = $1,300,000 (forced via an ISO exercise preference).
      * Phase-out: ($1.3M - $1,252,700) × 0.25 = $47,300 × 0.25 = $11,825.
      * line 5 = $137,000 - $11,825 = $125,175.
      * line 6 = $1,300,000 - $125,175 = $1,174,825.
      * line 7 = $1,174,825 × 0.28 - $4,782 = $328,951 - $4,782 = $324,169.
      * line 11 = $324,169 (regular_tax = 0 on synthetic return).
    """
    amt_adj = AMTAdjustments(iso_bargain_element=Decimal("1300000"))
    r = _minimal_return(
        filing_status=FilingStatus.MFJ,
        amt_adjustments_manual=amt_adj,
    )
    r = r.model_copy(
        update={
            "computed": ComputedTotals(
                total_income=Decimal("0"),
                adjustments_total=Decimal("0"),
                adjusted_gross_income=Decimal("0"),
                deduction_taken=Decimal("0"),
                taxable_income=Decimal("0"),
                tentative_tax=Decimal("0"),
            )
        }
    )
    fields = compute_form_6251_fields(r)

    assert fields.line_4_amti == Decimal("1300000.00")
    assert fields.line_5_exemption == Decimal("125175.00")
    assert fields.line_6_subtract_5_from_4 == Decimal("1174825.00")
    assert fields.line_7_tentative_amt_26_or_28pct == Decimal("324169.00")
    assert fields.line_11_amt_owed == Decimal("324169.00")


def test_26_28_bracket_crossing_single() -> None:
    """Single with exactly enough ISO to push line 6 just over the breakpoint.

    Target line 6 = $239,101 (one dollar into 28% zone). So we need
    AMTI = $239,101 + $88,100 (exemption) = $327,201. Line 7 should be
    26% × $239,100 + 28% × $1 - 0 (flat is only for line_6 > breakpoint
    AS A WHOLE, but the formula subtracts the flat $4,782 from 0.28 ×
    line_6 to collapse to a flat 26% for the slice below the breakpoint).

    Formula check: $239,101 × 0.28 - $4,782 = $66,948.28 - $4,782 =
    $62,166.28.
    """
    amt_adj = AMTAdjustments(iso_bargain_element=Decimal("327201"))
    r = _minimal_return(
        filing_status=FilingStatus.SINGLE,
        amt_adjustments_manual=amt_adj,
    )
    r = r.model_copy(
        update={
            "computed": ComputedTotals(
                total_income=Decimal("0"),
                adjustments_total=Decimal("0"),
                adjusted_gross_income=Decimal("0"),
                deduction_taken=Decimal("0"),
                taxable_income=Decimal("0"),
                tentative_tax=Decimal("0"),
            )
        }
    )
    fields = compute_form_6251_fields(r)

    assert fields.line_6_subtract_5_from_4 == Decimal("239101.00")
    assert fields.line_7_tentative_amt_26_or_28pct == Decimal("62166.28")


def test_pab_interest_from_1099_int_flows_to_line_2g() -> None:
    """Specified private activity bond interest on a 1099-INT (box 9)
    automatically flows to Form 6251 line 2g — no manual block needed."""
    r = _minimal_return(filing_status=FilingStatus.SINGLE, w2_wages=Decimal("0"))
    # Inject a 1099-INT with nonzero box 9 manually.
    int_form = Form1099INT(
        payer_name="City of Springfield",
        box9_specified_private_activity_bond_interest=Decimal("5000"),
    )
    r = r.model_copy(update={"forms_1099_int": [int_form]})
    # Layer 1 sees zero regular tax (uncomputed return).
    fields = compute_form_6251_fields(r)
    assert fields.line_2g_private_activity_bond_interest == Decimal("5000")


def test_pab_interest_combines_1099_and_manual() -> None:
    """1099-INT box 9 + manual AMTAdjustments PAB both add to line 2g."""
    amt_adj = AMTAdjustments(private_activity_bond_interest=Decimal("3000"))
    r = _minimal_return(
        filing_status=FilingStatus.SINGLE, amt_adjustments_manual=amt_adj
    )
    int_form = Form1099INT(
        payer_name="Muni",
        box9_specified_private_activity_bond_interest=Decimal("2000"),
    )
    r = r.model_copy(update={"forms_1099_int": [int_form]})
    fields = compute_form_6251_fields(r)
    assert fields.line_2g_private_activity_bond_interest == Decimal("5000")


def test_other_prefs_bucket_flows_to_line_3() -> None:
    """Every entry in AMTAdjustments.other_prefs is summed into line 3."""
    amt_adj = AMTAdjustments(
        other_prefs={
            "mining_costs": Decimal("1000"),
            "circulation_costs": Decimal("2500"),
            "long_term_contracts": Decimal("500"),
        }
    )
    r = _minimal_return(
        filing_status=FilingStatus.SINGLE, amt_adjustments_manual=amt_adj
    )
    fields = compute_form_6251_fields(r)
    assert fields.line_3_other_adjustments == Decimal("4000")


def test_depreciation_adjustment_can_be_negative() -> None:
    """Line 2l can be negative in later recovery years."""
    amt_adj = AMTAdjustments(depreciation_adjustment=Decimal("-2500"))
    r = _minimal_return(
        filing_status=FilingStatus.SINGLE, amt_adjustments_manual=amt_adj
    )
    fields = compute_form_6251_fields(r)
    assert fields.line_2l_depreciation_post_1986 == Decimal("-2500")


def test_mfs_exemption_and_rate_schedule() -> None:
    """MFS with $200k AMTI — exemption $68,500, rate schedule uses
    the halved breakpoint.

    line 6 = $200,000 - $68,500 = $131,500.
    $131,500 > $119,550 (MFS breakpoint), so line 7 uses the 28% rule.
    $131,500 × 0.28 - $2,391 = $36,820 - $2,391 = $34,429.
    """
    amt_adj = AMTAdjustments(iso_bargain_element=Decimal("200000"))
    r = _minimal_return(
        filing_status=FilingStatus.MFS, amt_adjustments_manual=amt_adj
    )
    r = r.model_copy(
        update={
            "computed": ComputedTotals(
                total_income=Decimal("0"),
                adjustments_total=Decimal("0"),
                adjusted_gross_income=Decimal("0"),
                deduction_taken=Decimal("0"),
                taxable_income=Decimal("0"),
                tentative_tax=Decimal("0"),
            )
        }
    )
    fields = compute_form_6251_fields(r)

    assert fields.line_5_exemption == Decimal("68500")
    assert fields.line_6_subtract_5_from_4 == Decimal("131500.00")
    assert fields.line_7_tentative_amt_26_or_28pct == Decimal("34429.00")


# ---------------------------------------------------------------------------
# Engine integration — the AMT pass should fold into ``total_tax``
# ---------------------------------------------------------------------------


def test_engine_no_trigger_no_amt_on_65k_single() -> None:
    """$65k Single, standard deduction. Engine should NOT compute AMT —
    ``alternative_minimum_tax`` stays None."""
    r = _minimal_return(
        filing_status=FilingStatus.SINGLE, w2_wages=Decimal("65000")
    )
    computed = compute(r)
    assert computed.computed.alternative_minimum_tax is None


def test_engine_iso_exercise_increases_total_tax_single() -> None:
    """ISO exercise pushes AMT > regular tax and total_tax increases.

    We compare two otherwise-identical returns: one with
    ``amt_adjustments_manual=None`` (no ISO), one with a $600k ISO
    bargain element. The second return's ``computed.total_tax`` must
    be strictly greater by approximately the AMT amount. Only the
    `amt_adjustments_manual` field differs between the two canonical
    returns, so any ``total_tax`` increase must come from AMT.
    """
    base = _minimal_return(
        filing_status=FilingStatus.SINGLE, w2_wages=Decimal("150000")
    )
    base_computed = compute(base)
    base_total_tax = base_computed.computed.total_tax

    with_iso = _minimal_return(
        filing_status=FilingStatus.SINGLE,
        w2_wages=Decimal("150000"),
        amt_adjustments_manual=AMTAdjustments(
            iso_bargain_element=Decimal("600000")
        ),
    )
    iso_computed = compute(with_iso)
    iso_total_tax = iso_computed.computed.total_tax
    amt = iso_computed.computed.alternative_minimum_tax

    assert amt is not None and amt > Decimal("0"), (
        f"AMT should be strictly positive with a $600k ISO bargain element; got {amt}"
    )
    assert iso_total_tax > base_total_tax, (
        f"total_tax should increase with ISO AMT. "
        f"base={base_total_tax}, with_iso={iso_total_tax}"
    )
    assert (iso_total_tax - base_total_tax) == amt, (
        "total_tax delta should equal the AMT contribution exactly"
    )


def test_engine_salt_itemized_no_manual_still_fires_path() -> None:
    """Itemized return with SALT triggers the path even with AMT = 0."""
    it = ItemizedDeductions(
        state_and_local_income_tax=Decimal("12000"),
    )
    r = _minimal_return(
        filing_status=FilingStatus.MFJ,
        itemized=it,
        w2_wages=Decimal("150000"),
    )
    computed = compute(r)
    # Path fires -> AMT field is set (even if to zero)
    assert computed.computed.alternative_minimum_tax is not None


def test_engine_amt_field_mirrored_to_other_taxes() -> None:
    """When AMT > 0, ``other_taxes.alternative_minimum_tax`` is stamped."""
    r = _minimal_return(
        filing_status=FilingStatus.SINGLE,
        w2_wages=Decimal("150000"),
        amt_adjustments_manual=AMTAdjustments(
            iso_bargain_element=Decimal("600000")
        ),
    )
    computed = compute(r)
    assert computed.other_taxes.alternative_minimum_tax > Decimal("0")
    # And they agree
    assert (
        computed.other_taxes.alternative_minimum_tax
        == computed.computed.alternative_minimum_tax
    )


# ---------------------------------------------------------------------------
# Golden fixture regression — existing fixtures still produce the same
# ---------------------------------------------------------------------------


def _load_fixture(fixtures_dir: Path, name: str) -> CanonicalReturn:
    data = json.loads((fixtures_dir / name / "input.json").read_text())
    return CanonicalReturn.model_validate(data)


def test_simple_w2_standard_no_amt_path(fixtures_dir: Path) -> None:
    """Simple W-2 standard-deduction fixture should not fire the AMT path."""
    r = _load_fixture(fixtures_dir, "simple_w2_standard")
    computed = compute(r)
    assert computed.computed.alternative_minimum_tax is None


def test_w2_investments_itemized_amt_is_zero(fixtures_dir: Path) -> None:
    """The itemized MFJ fixture fires the path but AMT = 0 (under exemption)."""
    r = _load_fixture(fixtures_dir, "w2_investments_itemized")
    computed = compute(r)
    amt = computed.computed.alternative_minimum_tax
    assert amt is not None
    assert amt == Decimal("0")
    # total_tax should be unchanged from expected
    assert computed.computed.total_tax == Decimal("29253.00")
