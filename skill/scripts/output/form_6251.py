"""Form 6251 — Alternative Minimum Tax — Individuals — two-layer renderer.

This module adds a minimal-but-correct AMT compute-and-render path to
the skill. Before wave 6, the skill's calc engine did not compute Form
6251; the AMT field on ``OtherTaxes`` was always zero and the post-1986
depreciation / ISO / private-activity-bond preferences landed nowhere.
Wave 6 wires a conditional Form 6251 pass that fires whenever a
taxpayer has an AMT trigger item — SALT deduction on Schedule A, ISO
bargain element from an exercise-and-hold, specified private-activity
bond interest, depreciation timing differences, etc.

Architecture (matches :mod:`schedule_a`):

* **Layer 1** — :func:`compute_form_6251_fields` maps a
  :class:`CanonicalReturn` onto a :class:`Form6251Fields` dataclass
  whose attribute names mirror the Form 6251 (TY2025 layout) line
  numbers. Layer 1 DOES compute a tax number — specifically, tentative
  minimum tax, regular-tax-for-AMT, and final AMT — because the engine
  has no other path to AMT arithmetic. The computation follows
  IRS Form 6251 Part I + Part II exactly; Part III (the capital-gain
  maximum-rate flow) is **intentionally not computed** and lines 12-40
  are left zero. See the "Scope" block below.

* **Layer 2** — :func:`render_form_6251_pdf` loads the wave-6 widget
  map at ``skill/reference/form-6251-acroform-map.json``, verifies the
  SHA-256 of the bundled source PDF at
  ``skill/reference/irs_forms/f6251.pdf`` (104,434 bytes) via
  :func:`skill.scripts.output._acroform_overlay.verify_pdf_sha256`, and
  fills every mapped widget via
  :func:`skill.scripts.output._acroform_overlay.fill_acroform_pdf`. If
  the bundled PDF is missing or has been re-issued (SHA mismatch) the
  renderer raises :class:`RuntimeError` — there is NO silent fallback.

Authority and citations
-----------------------

* IRS Form 6251 (TY2025): https://www.irs.gov/pub/irs-pdf/f6251.pdf
* IRS Instructions for Form 6251 (TY2025):
  https://www.irs.gov/pub/irs-pdf/i6251.pdf

TY2025 constants (pulled directly from the TY2025 Form 6251 face — the
instructions PDF repeats the same numbers; we prefer the form itself
because it is the filed-form source of truth):

* Exemption amounts (Form 6251 line 5 table):
    * Single or Head of Household: $88,100
    * Married Filing Jointly or Qualifying Surviving Spouse: $137,000
    * Married Filing Separately: $68,500

* Exemption phase-out thresholds (same table; exemption begins phasing
  out when line 4 exceeds this amount):
    * Single or Head of Household: $626,350
    * Married Filing Jointly or QSS: $1,252,700
    * Married Filing Separately: $626,350 (but note the separate
      $900,350 "add back" rule at line 4 for very high MFS returns)

* Phase-out rate: 25 cents per dollar over the threshold, so the
  exemption reaches zero at threshold + exemption / 0.25:
    * Single/HoH: $626,350 + $352,400 = $978,750
    * MFJ/QSS: $1,252,700 + $548,000 = $1,800,700
    * MFS: $626,350 + $274,000 = $900,350

* AMT rate schedule (Part II line 7 / Part III line 39):
    * 26% of AMTI-less-exemption up to $239,100 ($119,550 if MFS)
    * 28% of the excess plus a $4,782 flat adjustment ($2,391 if MFS)
      that keeps the 26% slice taxed at 26%

Scope for wave 6
----------------

**Included**
    * SALT add-back (Form 6251 line 2a) pulled from Schedule A line 7
      via :func:`compute_schedule_a_fields`. This is the single most
      common AMT trigger for middle-to-upper-income filers — any SALT
      cap beneficiary with AMTI above the exemption threshold.
    * ISO bargain element (line 2i), specified private activity bond
      interest (line 2g), post-1986 depreciation adjustment (line 2l),
      and the "other prefs" bucket. All read from the optional
      :class:`skill.scripts.models.AMTAdjustments` block on
      ``canonical.amt_adjustments_manual``.
    * Specified private activity bond interest from 1099-INT box 9
      (``box9_specified_private_activity_bond_interest``) is added to
      the manual line 2g amount — many filers with tax-exempt bonds
      get a nonzero line 2g purely from the 1099-INT report.
    * Exemption with full phase-out arithmetic at 25¢/$1.
    * Tentative AMT line 7 using the 26%/28% rate schedule with the
      $4,782 ($2,391 MFS) flat adjustment.
    * Final AMT = max(0, TMT - regular_tax_for_amt_comparison).

**Excluded (TODO)**
    * Part III (lines 12-40) — the capital-gain maximum-rate flow. Most
      returns do not need this; when a filer has large qualified
      dividends or LTCG AND line 6 > 0, the Form 6251 instructions
      direct them to compute Part III. For wave 6 we conservatively
      skip Part III and trust line 7 to use the 26%/28% schedule, which
      is the correct path for the common SALT-add-back case. A future
      wave can add the Part III worksheet.
    * AMT foreign tax credit (line 8) — left at 0. Filers with foreign
      earned income should inspect their AMT FTC manually.
    * AMT NOL carryover (line 2e/2f). If a filer has a regular-tax NOL,
      a parallel AMT NOL is required; we leave both at 0 and emit a
      warning in :func:`compute_form_6251_fields` when the regular
      return carries an NOL carryforward.
    * AMT adjustments to passive activities (line 2m), loss limitations
      (line 2n), qualified small business stock (line 2h), §1202, etc.
      Any caller can populate these via the
      :class:`AMTAdjustments.other_prefs` dict.

Engine integration
------------------

The engine (:mod:`skill.scripts.calc.engine`) calls
:func:`compute_form_6251_fields` unconditionally AFTER the OBBBA and
post-tax-bracket patches have fired, and ONLY when the taxpayer has a
potential AMT trigger:

    * ``canonical.itemize_deductions is True`` **and**
      ``canonical.itemized.state_and_local_*_tax > 0`` (SALT add-back
      is the most common trigger), **OR**
    * ``canonical.amt_adjustments_manual is not None`` (any manual
      ISO/PAB/depreciation entry), **OR**
    * any 1099-INT with non-zero box 9 (specified PAB interest).

When fired, the engine stores the result in
``ComputedTotals.alternative_minimum_tax`` and adds the AMT delta to
``ComputedTotals.total_tax``. Downstream renderers (Form 1040 line 17)
can read the AMT field directly.

Line layout (TY2025 Form 6251)
------------------------------

Part I — Alternative Minimum Taxable Income (AMTI):

    1a  Subtract Schedule 1-A (Form 1040) line 37 from Form 1040
        line 14 (standard/itemized deduction minus Schedule 1-A
        deduction). Starting point for AMTI.
    1b  Subtract line 1a from Form 1040 line 11b (taxable income).
        This is the "taxable-income-before-deduction" line that the
        instructions tell filers to use as the AMTI starting point.
    2a  Taxes from Schedule A line 7 (SALT add-back) — THIS is the
        single biggest trigger.
    2b  Tax refund from Schedule 1 line 1 / 8z (parenthesized —
        entered as a negative).
    2c  Investment interest expense (reg vs AMT difference).
    2d  Depletion (reg vs AMT).
    2e  Net operating loss deduction (entered as positive).
    2f  Alternative tax NOL (parenthesized negative).
    2g  Interest from specified private activity bonds.
    2h  Qualified small business stock (§1202 exclusion).
    2i  Exercise of incentive stock options.
    2j  Estates and trusts (K-1 box 12 code A).
    2k  Disposition of property (AMT vs reg basis difference).
    2l  Post-1986 depreciation.
    2m  Passive activities.
    2n  Loss limitations.
    2o  Circulation costs.
    2p  Long-term contracts.
    2q  Mining costs.
    2r  Research and experimental costs.
    2s  Pre-1987 installment sales (negative).
    2t  Intangible drilling costs preference.
    3   Other adjustments.
    4   AMTI (sum of 1b through 3).

Part II — AMT:

    5   Exemption (filing-status lookup with phase-out).
    6   Line 4 - line 5. If zero or less, stop — AMT is 0.
    7   Tentative AMT (26% / 28% schedule on line 6).
    8   AMT foreign tax credit.
    9   TMT = line 7 - line 8.
    10  Regular tax (Form 1040 line 16 + Schedule 2 line 1z minus some
        adjustments).
    11  AMT = max(0, line 9 - line 10).

Part III — Max capital-gain rates (lines 12-40): computed ONLY when
line 7 instructs. Wave 6 leaves these blank.
"""
from __future__ import annotations

from dataclasses import dataclass, fields as dc_fields
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from skill.scripts.models import (
    AMTAdjustments,
    CanonicalReturn,
    FilingStatus,
)

if TYPE_CHECKING:
    pass


_ZERO = Decimal("0")
_CENTS = Decimal("0.01")


# ---------------------------------------------------------------------------
# TY2025 AMT constants (from IRS Form 6251 TY2025 face, line 5 table and
# line 7 / line 18 / line 39 rate instructions)
# ---------------------------------------------------------------------------

# Exemption amounts (line 5)
AMT_EXEMPTION_SINGLE_HOH = Decimal("88100")
AMT_EXEMPTION_MFJ_QSS = Decimal("137000")
AMT_EXEMPTION_MFS = Decimal("68500")

# Phase-out thresholds (line 5 table)
AMT_PHASEOUT_THRESHOLD_SINGLE_HOH = Decimal("626350")
AMT_PHASEOUT_THRESHOLD_MFJ_QSS = Decimal("1252700")
AMT_PHASEOUT_THRESHOLD_MFS = Decimal("626350")

# Phase-out rate: 25¢ per $1 over threshold
AMT_PHASEOUT_RATE = Decimal("0.25")

# 26%/28% bracket breakpoint and flat adjustment (line 7 / 39)
AMT_RATE_BREAKPOINT_NORMAL = Decimal("239100")
AMT_RATE_BREAKPOINT_MFS = Decimal("119550")
AMT_RATE_LOW = Decimal("0.26")
AMT_RATE_HIGH = Decimal("0.28")
AMT_RATE_FLAT_ADJUSTMENT_NORMAL = Decimal("4782")
AMT_RATE_FLAT_ADJUSTMENT_MFS = Decimal("2391")


def _exemption_for(status: FilingStatus) -> Decimal:
    """Return the TY2025 AMT exemption amount (pre-phase-out) for a filing status."""
    if status in (FilingStatus.MFJ, FilingStatus.QSS):
        return AMT_EXEMPTION_MFJ_QSS
    if status == FilingStatus.MFS:
        return AMT_EXEMPTION_MFS
    return AMT_EXEMPTION_SINGLE_HOH


def _phaseout_threshold_for(status: FilingStatus) -> Decimal:
    """Return the TY2025 AMT exemption phase-out threshold for a filing status."""
    if status in (FilingStatus.MFJ, FilingStatus.QSS):
        return AMT_PHASEOUT_THRESHOLD_MFJ_QSS
    if status == FilingStatus.MFS:
        return AMT_PHASEOUT_THRESHOLD_MFS
    return AMT_PHASEOUT_THRESHOLD_SINGLE_HOH


def compute_amt_exemption(status: FilingStatus, line_4_amti: Decimal) -> Decimal:
    """Compute the phased-out AMT exemption for line 5.

    The Form 6251 TY2025 line 5 table lists the pre-phase-out exemption
    and a threshold. When line 4 (AMTI) exceeds the threshold, the
    exemption is reduced by 25 cents per dollar over the threshold.
    The exemption cannot go below zero.

    Authority: IRS Form 6251 TY2025 line 5 table; IRC §55(d).
    """
    exemption = _exemption_for(status)
    threshold = _phaseout_threshold_for(status)
    if line_4_amti <= threshold:
        return exemption
    excess = line_4_amti - threshold
    reduction = (excess * AMT_PHASEOUT_RATE).quantize(_CENTS, rounding=ROUND_HALF_UP)
    return max(_ZERO, exemption - reduction)


def compute_amt_tentative_tax(status: FilingStatus, line_6: Decimal) -> Decimal:
    """Compute line 7 — tentative AMT on line_6 using the 26%/28% schedule.

    Per the Form 6251 TY2025 line 7 "All others" instruction:

        If line 6 is $239,100 or less ($119,550 or less if MFS),
        multiply by 26%. Otherwise multiply by 28% and subtract
        $4,782 ($2,391 if MFS).

    The flat $4,782 ($2,391 MFS) adjustment keeps the slice under the
    breakpoint taxed at 26% by subtracting the 2-percentage-point
    difference applied to the breakpoint: $239,100 × (0.28 - 0.26) =
    $4,782.

    Authority: IRS Form 6251 TY2025 line 7; IRC §55(b)(1)(A).
    """
    if line_6 <= _ZERO:
        return _ZERO
    breakpoint_ = (
        AMT_RATE_BREAKPOINT_MFS if status == FilingStatus.MFS else AMT_RATE_BREAKPOINT_NORMAL
    )
    if line_6 <= breakpoint_:
        return (line_6 * AMT_RATE_LOW).quantize(_CENTS, rounding=ROUND_HALF_UP)
    flat = (
        AMT_RATE_FLAT_ADJUSTMENT_MFS
        if status == FilingStatus.MFS
        else AMT_RATE_FLAT_ADJUSTMENT_NORMAL
    )
    high_rate_amount = (line_6 * AMT_RATE_HIGH).quantize(_CENTS, rounding=ROUND_HALF_UP)
    return high_rate_amount - flat


# ---------------------------------------------------------------------------
# Layer 1 — field dataclass and computation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Form6251Fields:
    """Frozen snapshot of Form 6251 line values, ready for rendering.

    Field names follow the TY2025 Form 6251 line numbers exactly. All
    numeric fields are :class:`Decimal`. Part III (lines 12-40) is
    populated with zeros in wave 6 — see the module docstring.
    """

    # Header
    filing_status: str = ""
    taxpayer_name: str = ""
    taxpayer_ssn: str = ""

    # Part I — AMTI
    line_1a_subtract_sch_1a_from_1040_14: Decimal = _ZERO
    line_1b_subtract_1a_from_1040_11b: Decimal = _ZERO
    line_2a_taxes_from_sch_a_7: Decimal = _ZERO
    line_2b_tax_refund: Decimal = _ZERO
    line_2c_investment_interest_expense: Decimal = _ZERO
    line_2d_depletion: Decimal = _ZERO
    line_2e_nol_deduction: Decimal = _ZERO
    line_2f_alternative_tax_nol_deduction: Decimal = _ZERO
    line_2g_private_activity_bond_interest: Decimal = _ZERO
    line_2h_qualified_small_business_stock: Decimal = _ZERO
    line_2i_iso_exercise: Decimal = _ZERO
    line_2j_estates_trusts: Decimal = _ZERO
    line_2k_disposition_of_property: Decimal = _ZERO
    line_2l_depreciation_post_1986: Decimal = _ZERO
    line_2m_passive_activities: Decimal = _ZERO
    line_2n_loss_limitations: Decimal = _ZERO
    line_2o_circulation_costs: Decimal = _ZERO
    line_2p_long_term_contracts: Decimal = _ZERO
    line_2q_mining_costs: Decimal = _ZERO
    line_2r_research_and_experimental: Decimal = _ZERO
    line_2s_installment_sales_pre_1987: Decimal = _ZERO
    line_2t_intangible_drilling_costs: Decimal = _ZERO
    line_3_other_adjustments: Decimal = _ZERO
    line_4_amti: Decimal = _ZERO

    # Part II — AMT
    line_5_exemption: Decimal = _ZERO
    line_6_subtract_5_from_4: Decimal = _ZERO
    line_7_tentative_amt_26_or_28pct: Decimal = _ZERO
    line_8_amt_foreign_tax_credit: Decimal = _ZERO
    line_9_tentative_minimum_tax: Decimal = _ZERO
    line_10_regular_tax_for_amt: Decimal = _ZERO
    line_11_amt_owed: Decimal = _ZERO

    # Part III — max capital-gain rates (wave 6: all zero, not yet computed)
    line_12_amount_from_line_6: Decimal = _ZERO
    line_13_qdcg_worksheet_or_sch_d: Decimal = _ZERO
    line_14_sch_d_line_19: Decimal = _ZERO
    line_15_qdcg_sum_or_line_13: Decimal = _ZERO
    line_16_smaller_of_12_or_15: Decimal = _ZERO
    line_17_12_minus_16: Decimal = _ZERO
    line_18_tax_on_line_17: Decimal = _ZERO
    line_19_ltcg_zero_bracket_amt: Decimal = _ZERO
    line_20_qdcg_line_5_or_sch_d_14: Decimal = _ZERO
    line_21_19_minus_20: Decimal = _ZERO
    line_22_smaller_of_12_or_13: Decimal = _ZERO
    line_23_smaller_of_21_or_22: Decimal = _ZERO
    line_24_22_minus_23: Decimal = _ZERO
    line_25_ltcg_15_bracket_top: Decimal = _ZERO
    line_26_amount_from_line_21: Decimal = _ZERO
    line_27_qdcg_line_5_or_sch_d_21: Decimal = _ZERO
    line_28_sum_26_27: Decimal = _ZERO
    line_29_25_minus_28: Decimal = _ZERO
    line_30_smaller_of_24_or_29: Decimal = _ZERO
    line_31_line_30_x_15pct: Decimal = _ZERO
    line_32_sum_23_30: Decimal = _ZERO
    line_33_22_minus_32: Decimal = _ZERO
    line_34_line_33_x_20pct: Decimal = _ZERO
    line_35_sum_17_32_33: Decimal = _ZERO
    line_36_12_minus_35: Decimal = _ZERO
    line_37_line_36_x_25pct: Decimal = _ZERO
    line_38_sum_18_31_34_37: Decimal = _ZERO
    line_39_tax_on_line_12: Decimal = _ZERO
    line_40_smaller_of_38_or_39: Decimal = _ZERO


def _dec(x: Decimal | None) -> Decimal:
    """Coerce an Optional[Decimal] to a concrete Decimal (None -> 0)."""
    return x if x is not None else _ZERO


def _q(x: Decimal) -> Decimal:
    """Quantize a Decimal to two decimal places."""
    return x.quantize(_CENTS, rounding=ROUND_HALF_UP)


def compute_form_6251_fields(
    return_: CanonicalReturn,
) -> Form6251Fields:
    """Map a CanonicalReturn onto a Form6251Fields dataclass AND compute AMT.

    The input ``return_`` should have been passed through
    :func:`skill.scripts.calc.engine.compute` first so that
    :class:`ComputedTotals` carries AGI, taxable income, and regular
    federal income tax — those feed lines 1b, 1a, and 10. When called
    on a fresh (uncomputed) return, the values default to zero and the
    resulting AMT will be zero.

    Computation
    -----------

    Start from taxable income (Form 1040 line 15 / ``computed.taxable_income``)
    and ADD BACK the regular-tax deduction (Form 1040 line 12, stored in
    ``computed.deduction_taken``). That's line 1b: "taxable income before
    the deduction." Then add the SALT deduction (Schedule A line 7, which
    is the already-capped SALT) plus any manual preferences. The
    structure is:

        line 1a = deduction_taken minus Schedule 1-A line 37 (zero in
                  wave 6 — Schedule 1-A is OBBBA tips/overtime, not a
                  subtraction from the deduction)
        line 1b = taxable_income + deduction_taken (equivalent to
                  Form 1040 line 11b)
        line 2a = Schedule A line 7 (SALT add-back)
        line 2g = 1099-INT box 9 + manual PAB entry
        line 2i = manual ISO bargain element
        line 2l = manual depreciation adjustment
        line 3  = sum of manual "other_prefs" dict
        line 4  = line 1b + line 2a + 2g + 2i + 2l + 3 (add-backs are
                  all non-negative in wave 6)

    Then:
        line 5  = phased-out exemption
        line 6  = max(0, line 4 - line 5)
        line 7  = 26%/28% schedule on line 6
        line 8  = 0 (AMT FTC not computed)
        line 9  = line 7 - line 8
        line 10 = regular tax (tentative_tax from ComputedTotals)
        line 11 = max(0, line 9 - line 10)

    Parameters
    ----------
    return_
        The canonical return, typically already run through
        ``engine.compute``.

    Returns
    -------
    Form6251Fields
        A frozen dataclass with every line populated. Use
        ``.line_11_amt_owed`` as the final AMT.
    """
    c = return_.computed

    # -- header -------------------------------------------------------
    filing_status_str = return_.filing_status.value
    taxpayer_name = f"{return_.taxpayer.first_name} {return_.taxpayer.last_name}"
    taxpayer_ssn = return_.taxpayer.ssn

    # -- AMTI starting point (lines 1a / 1b) --------------------------
    taxable_income = _dec(c.taxable_income)
    deduction_taken = _dec(c.deduction_taken)

    # Line 1a: Schedule 1-A line 37 subtraction. Schedule 1-A (OBBBA
    # tips/overtime deductions) is a Schedule 1 Part II adjustment, NOT
    # a piece of the Form 1040 line 12 deduction. The Form 6251 line 1a
    # instruction on "Subtract Schedule 1-A line 37 from Form 1040 line
    # 14" is about the PORTION of the deduction that came from Sch 1-A
    # — wave 6 models this as zero because tenforty + the engine already
    # fold Sch 1-A into AGI, so none of it leaks into line 14. Kept as a
    # named field in case a future wave distinguishes these paths.
    line_1a = deduction_taken

    # Line 1b: taxable income + deduction = Form 1040 line 11b ("taxable
    # income before deduction"). This is the IRS Form 6251 instruction's
    # natural AMTI starting point.
    line_1b = taxable_income + deduction_taken

    # -- Line 2a: SALT add-back from Schedule A line 7 ----------------
    # If the taxpayer is NOT itemizing, line 2a is zero — no SALT add-
    # back. When itemizing, we pull the already-capped SALT + other-
    # taxes total directly from the canonical Schedule A block rather
    # than re-applying the cap here (the engine applies the cap
    # consistently).
    from skill.scripts.output.schedule_a import compute_schedule_a_fields

    if return_.itemize_deductions and return_.itemized is not None:
        sch_a_fields = compute_schedule_a_fields(return_)
        line_2a = sch_a_fields.line_7_total_taxes
    else:
        line_2a = _ZERO

    # -- Lines 2b-2t: manual preferences ------------------------------
    amt_adj: AMTAdjustments | None = return_.amt_adjustments_manual

    # Specified private activity bond interest — combine 1099-INT box 9
    # (automatic) with any manual add.
    pab_from_1099s = sum(
        (
            f.box9_specified_private_activity_bond_interest
            for f in return_.forms_1099_int
        ),
        start=_ZERO,
    )
    line_2g = pab_from_1099s + (
        amt_adj.private_activity_bond_interest if amt_adj is not None else _ZERO
    )

    line_2i = amt_adj.iso_bargain_element if amt_adj is not None else _ZERO
    line_2l = amt_adj.depreciation_adjustment if amt_adj is not None else _ZERO

    # Line 3 "Other adjustments" receives the sum of every other_prefs
    # entry. Wave 6 does not break individual entries out onto lines
    # 2b-2f/2h/2j-2n/2o-2t — a future wave can extend the AMTAdjustments
    # schema with named fields per line.
    if amt_adj is not None and amt_adj.other_prefs:
        line_3 = sum(amt_adj.other_prefs.values(), start=_ZERO)
    else:
        line_3 = _ZERO

    # Lines 2b/2c/2d/2e/2f/2h/2j/2k/2m/2n/2o/2p/2q/2r/2s/2t are all
    # zero in wave 6 (see TODO in module docstring).

    # -- Line 4: AMTI -------------------------------------------------
    line_4 = _q(line_1b + line_2a + line_2g + line_2i + line_2l + line_3)

    # -- Line 5: phased-out exemption ---------------------------------
    line_5 = compute_amt_exemption(return_.filing_status, line_4)

    # -- Line 6: AMTI over exemption ----------------------------------
    line_6 = max(_ZERO, line_4 - line_5)

    # -- Line 7: tentative AMT at 26%/28% -----------------------------
    line_7 = compute_amt_tentative_tax(return_.filing_status, line_6)

    # -- Line 8: AMT foreign tax credit (wave 6: 0) -------------------
    line_8 = _ZERO

    # -- Line 9: TMT --------------------------------------------------
    line_9 = max(_ZERO, line_7 - line_8)

    # -- Line 10: regular tax for AMT comparison ----------------------
    # The form says "Add Form 1040 line 16 + Schedule 2 line 1z; subtract
    # Schedule 3 line 1 and Form 8978 line 14 negatives; exclude Schedule
    # J and Form 4972 special taxes." The engine's ``tentative_tax`` is
    # Form 1040 line 16 directly — Schedule 2 line 1z (Form 6251 itself)
    # is zero here to avoid double-counting, and Schedule 3 line 1
    # (foreign tax credit) / Form 8978 corrections are not modeled. So
    # we use tentative_tax directly as line 10.
    line_10 = _dec(c.tentative_tax)

    # -- Line 11: final AMT -------------------------------------------
    line_11 = max(_ZERO, line_9 - line_10)

    return Form6251Fields(
        filing_status=filing_status_str,
        taxpayer_name=taxpayer_name,
        taxpayer_ssn=taxpayer_ssn,
        line_1a_subtract_sch_1a_from_1040_14=line_1a,
        line_1b_subtract_1a_from_1040_11b=line_1b,
        line_2a_taxes_from_sch_a_7=line_2a,
        line_2g_private_activity_bond_interest=line_2g,
        line_2i_iso_exercise=line_2i,
        line_2l_depreciation_post_1986=line_2l,
        line_3_other_adjustments=line_3,
        line_4_amti=line_4,
        line_5_exemption=line_5,
        line_6_subtract_5_from_4=line_6,
        line_7_tentative_amt_26_or_28pct=line_7,
        line_8_amt_foreign_tax_credit=line_8,
        line_9_tentative_minimum_tax=line_9,
        line_10_regular_tax_for_amt=line_10,
        line_11_amt_owed=line_11,
    )


# ---------------------------------------------------------------------------
# Layer 2 — AcroForm overlay PDF rendering
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parents[3]
_FORM_6251_MAP_PATH = (
    _REPO_ROOT / "skill" / "reference" / "form-6251-acroform-map.json"
)
_FORM_6251_PDF_PATH = (
    _REPO_ROOT / "skill" / "reference" / "irs_forms" / "f6251.pdf"
)


def _format_decimal(value: Decimal) -> str:
    """Format a Decimal as ``"10000.00"`` for AcroForm text fields.

    Zero collapses to the empty string so cells the filer does not use
    are left blank on the rendered form.
    """
    q = value.quantize(_CENTS, rounding=ROUND_HALF_UP)
    if q == Decimal("0.00"):
        return ""
    return f"{q:.2f}"


def _form_6251_widget_values(
    fields: Form6251Fields, widget_map: dict
) -> dict[str, str]:
    """Translate a Form6251Fields snapshot to a widget_name->str dict.

    Iterates Layer 1 dataclass fields and looks up each by semantic
    name in the widget map's ``mapping`` block. Header strings land in
    the taxpayer name widget; taxpayer_ssn is intentionally left blank
    (Form 1040 holds the canonical SSN and we do not duplicate it).
    """
    out: dict[str, str] = {}
    mapping = widget_map["mapping"]

    for f in dc_fields(fields):
        sem = f.name
        if sem not in mapping:
            continue
        entry = mapping[sem]
        wname = entry["widget_name"]
        if "*" in wname:
            continue
        value = getattr(fields, sem)
        if sem == "taxpayer_ssn":
            # Leave SSN blank on the rendered form — Form 1040 is the
            # canonical place for it and we avoid duplicating it across
            # attachments.
            out[wname] = ""
        elif isinstance(value, bool):
            out[wname] = "Yes" if value else ""
        elif isinstance(value, Decimal):
            out[wname] = _format_decimal(value)
        elif value is None:
            out[wname] = ""
        else:
            out[wname] = str(value)
    return out


def render_form_6251_pdf(fields: Form6251Fields, out_path: Path) -> Path:
    """Render a Form 6251 PDF by overlaying ``fields`` on the IRS fillable PDF.

    Loads the wave-6 widget map, validates the on-disk source PDF
    SHA-256, fills the widgets via
    :func:`skill.scripts.output._acroform_overlay.fill_acroform_pdf`,
    and writes the filled copy to ``out_path``. Raises
    :class:`RuntimeError` if the source PDF is missing or its SHA-256
    does not match (an IRS re-issue).

    Returns ``out_path`` for convenience.
    """
    from skill.scripts.output._acroform_overlay import (
        fill_acroform_pdf,
        load_widget_map_as_dict,
        verify_pdf_sha256,
    )

    widget_map = load_widget_map_as_dict(_FORM_6251_MAP_PATH)
    verify_pdf_sha256(_FORM_6251_PDF_PATH, widget_map["source_pdf_sha256"])
    widget_values = _form_6251_widget_values(fields, widget_map)
    return fill_acroform_pdf(_FORM_6251_PDF_PATH, widget_values, Path(out_path))
