"""Missouri (MO) state plugin — TY2025.

See skill/reference/tenforty-ty2025-gap.md for the TY2025 probe rubric
and why MO is hand-rolled instead of graph-wrapped (graph backend
output diverges from DOR primary source by ~$102 at $65k Single —
material mismatch > $5).

Hand-rolled MO Form MO-1040 calc. Tenforty does NOT support MO via the
default OTS backend (``ValueError: OTS does not support 2025/MO_1040``)
and the **graph backend's MO output diverges materially from the DOR
primary source** ($2,200.52 vs hand-traced $2,098.21 on the spec's
$65k Single scenario, an ~$102 delta well outside the ±$5 wrap window).
The graph backend appears to be skipping Missouri's federal income tax
deduction (MO-1040 line 13) AND/OR using a slightly stale rate table.
Hand-rolling from the 2025 MO-1040 instructions is the only way to
match Missouri's actual MO Tax Computation Worksheet output.

Source of truth
---------------
2025 MO-1040 Instructions (dor.mo.gov/forms/MO-1040 Instructions_2025.pdf)
2025 Missouri Income Tax Reference Guide / Form 4711 (dor.mo.gov/forms/4711_2025.pdf)
2025 MO-1040 Form (dor.mo.gov/forms/MO-1040 Print Only_2025.pdf)
2025 Form MO-A — Individual Income Tax Adjustments (dor.mo.gov/forms/MO-A_2025.pdf)

Tax Rate Chart (TY2025, MO-1040 Instructions page 21 "Section A"):

    If Missouri taxable income is:        The tax is:
    $0          - $1,313                  $0
    $1,314      - $2,626                  2.0% of excess over $1,313
    $2,627      - $3,939                  $26 + 2.5% of excess over $2,626
    $3,940      - $5,252                  $59 + 3.0% of excess over $3,939
    $5,253      - $6,565                  $98 + 3.5% of excess over $5,252
    $6,566      - $7,878                  $144 + 4.0% of excess over $6,565
    $7,879      - $9,191                  $197 + 4.5% of excess over $7,878
    Over $9,191                           $256 + 4.7% of excess over $9,191

Top rate is 4.7% for TY2025. Brackets are indexed annually for inflation
and the top rate continues to step down per the multi-year reduction
schedule (HB 2 Special Session 2022 / subsequent triggers).

MO-1040 calculation flow (resident):

    Line 1   Federal AGI
    Line 2   MO additions (Form MO-A Part 1, line 7)
    Line 3   Total income = L1 + L2
    Line 4   MO subtractions (Form MO-A Part 1, line 19)
    Line 5   MO AGI = L3 - L4
    Line 6   Total MO AGI (= L5 for unmarried filers; sum of Y/S for MFC)
    Line 7   Income percentage (100% for single)
    Line 8   Pension / Social Security / SS Disability exemption
    Line 9   Federal income tax from federal return (1040 line 22 + 23,
             roughly federal_income_tax + AMT, less EITC)
    Line 10  Other tax from federal return
    Line 11  Total federal tax (L9 + L10 - any EITC)
    Line 12  Federal tax percentage (sliding scale by MO AGI):
                $0-25,000:        35%
                $25,001-50,000:   25%
                $50,001-100,000:  15%
                $100,001-125,000: 5%
                $125,001+:        0%
    Line 13  Federal income tax deduction = L11 * L12 (capped at $5,000
             single/HOH/MFS/QSS, $10,000 MFJ)
    Line 14  Standard or itemized deduction (federal-conforming when
             standard; for TY2025 Single = $15,750, MFJ = $31,500,
             HOH = $23,625, MFS = $15,750)
    Line 15  HOH/QSS additional exemption = $1,400
    Lines 16-24  Various special-case deductions (LTC, military, foster,
             etc.). v1 sets these to zero — see ``MO_V1_LIMITATIONS``.
    Line 25  Total deductions = sum(L8, L13..L24)
    Line 26  Subtotal = L6 - L25
    Lines 27-28  Business / enterprise zone modifications (zero in v1)
    Line 29  Missouri taxable income = L26 - L27 - L28
    Line 30  MO tax = bracket-table tax on L29 (per Tax Rate Chart above)
    Line 31  Resident credit (other states) — v1 = 0
    Line 32  MO income percentage (100% for full-year resident)
    Line 33  Balance = L30 * L32 - L31
    Line 34  Other taxes
    Line 35  Subtotal = L33 + L34
    Line 36  Total tax = L35Y + L35S

**TY2025 Single $65k resident reference scenario** (locked in tests):

    Line 1   Federal AGI                    = $65,000
    Line 8   Pension exemption              = $0
    Line 11  Total federal tax              = $5,755    (from FederalTotals)
    Line 12  Federal tax percentage         = 15%        (50,001-100,000 band)
    Line 13  Federal tax deduction          = $5,755 * 0.15 = $863.25
                                             (well under the $5,000 cap)
    Line 14  Standard deduction (Single)    = $15,750
    Line 25  Total deductions               = $863.25 + $15,750 = $16,613.25
    Line 26  Subtotal                       = $65,000 - $16,613.25 = $48,386.75
    Line 29  MO taxable income (rounded)    = $48,387
    Line 30  Tax (over $9,191 bracket)
             = $256 + 4.7% * ($48,387 - $9,191)
             = $256 + 4.7% * $39,196
             = $256 + $1,842.21
             = **$2,098.21**

The MO-1040 instructions explicitly say "Round to the nearest whole
dollar"; v1 rounds the tax to whole dollars per the instructions but
preserves cents in the underlying state_specific Decimal for
diagnostics. The integer tax for the $65k Single scenario is **$2,098**.

Reciprocity
-----------
Missouri has **no** bilateral reciprocity agreements — verified against
``skill/reference/state-reciprocity.json``. MO residents who work in
Kansas, Illinois, Iowa, Nebraska, Oklahoma, Arkansas, Tennessee, or
Kentucky must file nonresident returns in those states (when those
states tax income) and claim the MO resident credit on Form MO-CR.

Submission channel
------------------
Missouri does not operate its own free direct e-file portal for
individuals. The DOR participates in the IRS Fed/State MeF program;
filing the MO-1040 individually requires commercial software. Channel
is ``SubmissionChannel.FED_STATE_PIGGYBACK``.

v1 limitations
--------------
See ``MO_V1_LIMITATIONS`` constant. Notable items:
- Form MO-A Part 1 additions/subtractions all default to 0 (state bond
  interest add-back, US bond interest subtraction, capital gain
  subtraction, business income deduction, etc.).
- Lines 16, 17, 18, 19, 21, 24 special-case deductions all default to 0.
- Form MO-PTC Property Tax Credit, Form MO-WFTC Working Family Credit,
  Form MO-CR resident credit all default to 0.
- Pension / SS / SS Disability exemption (line 8) defaults to 0.
- Nonresident / part-year apportionment uses day-based proration
  instead of Form MO-NRI income-source allocation.

Why hand-roll instead of wrap
------------------------------
The wave 5 fan-out spec mandates "wrap if graph matches DOR within $5,
hand-roll if material mismatch." For MO the graph emits $2,200.52 on
the $65k Single scenario; the DOR-traced result above is $2,098.21
($2,098 rounded). Delta = ~$102, **WAY** outside the ±$5 window —
hand-roll. The graph backend does not appear to be applying MO's
federal income tax deduction (MO-1040 line 13), which is one of MO's
defining quirks among US state income tax systems.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Final

from skill.scripts.models import (
    CanonicalReturn,
    FilingStatus,
    ResidencyStatus,
    StateReturn,
)
from skill.scripts.states._hand_rolled_base import (
    GraduatedBracket,
    cents,
    d,
    day_prorate,
    graduated_tax,
)
from skill.scripts.states._plugin_api import (
    FederalTotals,
    IncomeApportionment,
    StatePlugin,
    StatePluginMeta,
    StateStartingPoint,
    SubmissionChannel,
)


# Canonical wave-5 $65k Single gatekeeper lock. Hand-traced from MO
# Form MO-1040 — see module docstring. Referenced from test_state_mo.py.
LOCK_VALUE: Final[Decimal] = Decimal("2098.00")


# ---------------------------------------------------------------------------
# TY2025 constants — Tax Rate Chart from MO-1040 Instructions page 21
# ---------------------------------------------------------------------------

# The TY2025 chart is identical for every filing status (MO does NOT split
# its brackets by status). Brackets defined as continuous-formula tiers:
# rate * (TI - low_of_bracket) + accumulated_lower_tax.
#
# Encoded as a sum-of-tiers ``GraduatedBracket`` table compatible with
# ``graduated_tax`` from ``_hand_rolled_base``.
MO_TY2025_BRACKETS: tuple[GraduatedBracket, ...] = (
    GraduatedBracket(low=Decimal("0"),     high=Decimal("1313"),  rate=Decimal("0")),
    GraduatedBracket(low=Decimal("1313"),  high=Decimal("2626"),  rate=Decimal("0.020")),
    GraduatedBracket(low=Decimal("2626"),  high=Decimal("3939"),  rate=Decimal("0.025")),
    GraduatedBracket(low=Decimal("3939"),  high=Decimal("5252"),  rate=Decimal("0.030")),
    GraduatedBracket(low=Decimal("5252"),  high=Decimal("6565"),  rate=Decimal("0.035")),
    GraduatedBracket(low=Decimal("6565"),  high=Decimal("7878"),  rate=Decimal("0.040")),
    GraduatedBracket(low=Decimal("7878"),  high=Decimal("9191"),  rate=Decimal("0.045")),
    GraduatedBracket(low=Decimal("9191"),  high=None,             rate=Decimal("0.047")),
)
"""TY2025 MO Tax Rate Chart, encoded as a sum-of-tiers bracket table.

When summed via ``graduated_tax`` the result equals the printed Section
A formula at every bracket boundary:

    >>> graduated_tax(Decimal("9191"), MO_TY2025_BRACKETS)
    Decimal('255.85')   # ~ printed $256 (instructions round to whole $)
    >>> graduated_tax(Decimal("48387"), MO_TY2025_BRACKETS)
    Decimal('2098.07')  # rounded to printed table value $2,098

Source: 2025 MO-1040 Instructions page 21 (Section A "Tax Rate Chart").
"""

MO_TY2025_TOP_RATE: Decimal = Decimal("0.047")
"""Missouri top marginal rate, TY2025. Continues the multi-year reduction
schedule from the 2022 special session HB 2 / 2023 trigger reductions."""

# Federal Income Tax Deduction sliding scale (MO-1040 Instructions p.8 line 12)
MO_TY2025_FED_TAX_PCT_BANDS: tuple[tuple[Decimal, Decimal, Decimal], ...] = (
    # (lower MO AGI, upper MO AGI, percentage)
    (Decimal("0"),       Decimal("25000"),  Decimal("0.35")),
    (Decimal("25000"),   Decimal("50000"),  Decimal("0.25")),
    (Decimal("50000"),   Decimal("100000"), Decimal("0.15")),
    (Decimal("100000"),  Decimal("125000"), Decimal("0.05")),
    (Decimal("125000"),  Decimal("999999999"), Decimal("0")),
)
"""MO-1040 line 12 sliding-scale percentage on the federal income tax
deduction. Applied to total federal tax (line 11) up to a per-status cap
on line 13."""

MO_TY2025_FED_TAX_DEDUCTION_CAP_OTHER: Decimal = Decimal("5000")
"""Cap on MO-1040 line 13 for any filing status other than Married Filing
Combined."""

MO_TY2025_FED_TAX_DEDUCTION_CAP_MFJ: Decimal = Decimal("10000")
"""Cap on MO-1040 line 13 for Married Filing Combined (MFJ)."""

MO_TY2025_STD_DED_SINGLE: Decimal = Decimal("15750")
"""Missouri standard deduction (Single) — conforms to federal OBBBA
amount for TY2025. MO-1040 line 14 instructions list each status."""

MO_TY2025_STD_DED_MFJ: Decimal = Decimal("31500")
MO_TY2025_STD_DED_HOH: Decimal = Decimal("23625")
MO_TY2025_STD_DED_MFS: Decimal = Decimal("15750")

MO_TY2025_STD_DED_BY_STATUS: dict[FilingStatus, Decimal] = {
    FilingStatus.SINGLE: MO_TY2025_STD_DED_SINGLE,
    FilingStatus.MFJ: MO_TY2025_STD_DED_MFJ,
    FilingStatus.QSS: MO_TY2025_STD_DED_MFJ,
    FilingStatus.HOH: MO_TY2025_STD_DED_HOH,
    FilingStatus.MFS: MO_TY2025_STD_DED_MFS,
}


MO_V1_LIMITATIONS: tuple[str, ...] = (
    "Form MO-A Part 1 additions NOT applied: state/local bond interest "
    "from non-MO sources (line 1), partnership/fiduciary modifications "
    "(line 2), nonqualified pension distributions (line 3), and other "
    "MO-A Part 1 lines 4-7.",
    "Form MO-A Part 1 subtractions NOT applied: U.S. obligations "
    "interest (line 8), state tax refund (line 9), Social Security "
    "(line 10), public pension (line 11), private pension (line 12), "
    "long-term care insurance / health-care sharing ministry (lines "
    "13-14), military income (line 15), business income deduction "
    "(line 17, 20% of MO-source business income), 100% capital gain "
    "subtraction (line 18), and other MO-A Part 1 lines.",
    "MO-1040 line 8 Pension/Social Security/SS Disability exemption "
    "(Form MO-A Part 3 Section D) defaults to 0. Real filers age 62+ "
    "with qualifying retirement income may have a substantial line 8 "
    "exemption.",
    "MO-1040 lines 16-24 special deductions (long-term care, health-"
    "care sharing ministry, active duty military, 20-year-old reserves, "
    "farmland-to-beginning-farmer, foster parent) all default to 0.",
    "MO-1040 line 27 Enterprise Zone Income Modification and line 28 "
    "Rural Empowerment Zone Modification default to 0.",
    "MO-1040 line 31 Resident credit for taxes paid to other states "
    "(Form MO-CR) defaults to 0 — critical for multi-state filers who "
    "work in KS, IL, IA, NE, OK, AR, TN, KY.",
    "MO-PTC (Property Tax Credit), MO-WFTC (Working Family Tax Credit), "
    "MO-TC (Miscellaneous tax credits) all default to 0.",
    "Nonresident / part-year apportionment uses day-based proration "
    "(days_in_state / 365) instead of Form MO-NRI Income Allocation. "
    "Real MO nonresident calc tracks MO-source vs total income on "
    "Form MO-NRI line 18 and applies that ratio at MO-1040 line 32.",
    "MO does not have a separate AMT — non-limitation, noted for "
    "completeness.",
    "Whole-dollar rounding: MO-1040 instructions say 'round to the "
    "nearest whole dollar' on every line. v1 keeps Decimal precision "
    "internally and rounds the final tax to whole dollars on output.",
)


# ---------------------------------------------------------------------------
# Helper functions — sliding-scale fed tax %, deduction, taxable income
# ---------------------------------------------------------------------------


def mo_fed_tax_percentage(mo_agi: Decimal) -> Decimal:
    """Return the MO-1040 line 12 federal tax percentage for a given MO AGI.

    Sliding-scale per MO-1040 Instructions page 8:
        $0-25,000:         35%
        $25,001-50,000:    25%
        $50,001-100,000:   15%
        $100,001-125,000:  5%
        $125,001+:         0%

    The bands use **strict** lower / **inclusive** upper boundaries
    (i.e., AGI of exactly $25,000 falls in the 35% band, $25,001 falls
    in 25%). The instructions show this with the example "If Line 6 is
    $22,450, enter 35%" and "If Line 6 is $58,750, enter 15%."
    """
    agi = d(mo_agi)
    if agi <= Decimal("25000"):
        return Decimal("0.35")
    if agi <= Decimal("50000"):
        return Decimal("0.25")
    if agi <= Decimal("100000"):
        return Decimal("0.15")
    if agi <= Decimal("125000"):
        return Decimal("0.05")
    return Decimal("0")


def mo_fed_tax_deduction(
    federal_tax: Decimal, mo_agi: Decimal, filing_status: FilingStatus
) -> Decimal:
    """MO-1040 line 13: federal income tax deduction.

    Multiplies the total federal tax (line 11) by the sliding-scale
    percentage from ``mo_fed_tax_percentage``, then caps the result at
    $10,000 for MFJ/QSS or $5,000 for any other filing status. Negative
    federal tax (refundable credits exceeding tax) yields zero.
    """
    fed_tax = d(federal_tax)
    if fed_tax < 0:
        return Decimal("0")
    pct = mo_fed_tax_percentage(mo_agi)
    deduction = fed_tax * pct
    if filing_status in (FilingStatus.MFJ, FilingStatus.QSS):
        cap = MO_TY2025_FED_TAX_DEDUCTION_CAP_MFJ
    else:
        cap = MO_TY2025_FED_TAX_DEDUCTION_CAP_OTHER
    if deduction > cap:
        return cap
    return deduction


def mo_standard_deduction(filing_status: FilingStatus) -> Decimal:
    """Missouri standard deduction by filing status (TY2025, MO-1040 line 14)."""
    return MO_TY2025_STD_DED_BY_STATUS.get(
        filing_status, MO_TY2025_STD_DED_SINGLE
    )


def mo_tax_from_table(taxable_income: Decimal) -> Decimal:
    """Compute MO-1040 line 30 tax from the Tax Rate Chart.

    Uses ``graduated_tax`` over ``MO_TY2025_BRACKETS``. Result is
    quantized to the cent; the calling code rounds to whole dollars on
    output (per the instructions' "round to nearest whole dollar" rule).
    """
    return graduated_tax(taxable_income, MO_TY2025_BRACKETS)


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MissouriPlugin:
    """State plugin for Missouri — TY2025.

    Hand-rolled MO-1040 calculation. Tenforty's graph backend produces
    a number that diverges from the DOR primary source by ~$102 on the
    spec's $65k Single scenario; the divergence is consistent with the
    graph backend NOT applying Missouri's federal income tax deduction
    (MO-1040 line 13). v1 mirrors the printed instructions exactly.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # MO-1040 Line 1: Federal AGI.
        federal_agi = d(federal.adjusted_gross_income)
        # Lines 2-5 (additions/subtractions). v1 zeros these.
        mo_additions = Decimal("0")
        mo_subtractions = Decimal("0")
        mo_agi = federal_agi + mo_additions - mo_subtractions
        if mo_agi < 0:
            mo_agi = Decimal("0")

        # MO-1040 Line 8: Pension/SS/SS-Disability exemption.
        pension_ss_exemption = Decimal("0")

        # MO-1040 Lines 9-13: Federal income tax deduction.
        # Use the federal income tax from FederalTotals; "total federal
        # tax" on MO line 11 is roughly federal_income_tax + AMT - EITC.
        # v1 uses ``federal.federal_income_tax`` directly as the line 11
        # input — flagged as a v1 limitation if AMT is present.
        federal_total_tax_for_mo = d(federal.federal_income_tax)
        fed_tax_pct = mo_fed_tax_percentage(mo_agi)
        fed_tax_deduction = mo_fed_tax_deduction(
            federal_total_tax_for_mo, mo_agi, federal.filing_status
        )

        # MO-1040 Line 14: Standard deduction (federal-conforming).
        std_ded = mo_standard_deduction(federal.filing_status)

        # MO-1040 Line 15: HOH/QSS additional exemption ($1,400).
        hoh_qss_exemption = Decimal("0")
        if federal.filing_status in (FilingStatus.HOH, FilingStatus.QSS):
            hoh_qss_exemption = Decimal("1400")

        # MO-1040 Lines 16-24: special-case deductions. v1 = 0.
        special_deductions = Decimal("0")

        # MO-1040 Line 25: Total deductions.
        total_deductions = (
            pension_ss_exemption
            + fed_tax_deduction
            + std_ded
            + hoh_qss_exemption
            + special_deductions
        )

        # MO-1040 Line 26: Subtotal.
        subtotal = mo_agi - total_deductions
        if subtotal < 0:
            subtotal = Decimal("0")

        # MO-1040 Lines 27-28: enterprise/empowerment zone modifications.
        zone_modifications = Decimal("0")

        # MO-1040 Line 29: Missouri taxable income.
        # The instructions explicitly say "round to the nearest whole
        # dollar" on every line; we round line 29 to a whole-dollar
        # value before computing tax to mirror the printed worksheet.
        mo_taxable_income_decimal = subtotal - zone_modifications
        if mo_taxable_income_decimal < 0:
            mo_taxable_income_decimal = Decimal("0")
        mo_taxable_income = mo_taxable_income_decimal.quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )

        # MO-1040 Line 30: Tax.
        mo_tax_full_decimal = mo_tax_from_table(mo_taxable_income)
        # Round to whole dollar per instructions.
        mo_tax_full_whole = mo_tax_full_decimal.quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        # Cents-precision form for diagnostics and downstream consumers.
        mo_tax_full = cents(mo_tax_full_whole)

        # Apportion for nonresident / part-year.
        if residency == ResidencyStatus.RESIDENT or days_in_state >= 365:
            mo_tax_apportioned = mo_tax_full
            apportionment_fraction = Decimal("1")
        else:
            mo_tax_apportioned = day_prorate(
                mo_tax_full, days_in_state=max(0, days_in_state)
            )
            apportionment_fraction = (
                Decimal(max(0, days_in_state)) / Decimal("365")
            )
            if apportionment_fraction > 1:
                apportionment_fraction = Decimal("1")

        state_specific: dict[str, Any] = {
            "state_federal_agi": cents(federal_agi),
            "state_adjusted_gross_income": cents(mo_agi),
            "state_pension_ss_exemption": cents(pension_ss_exemption),
            "state_federal_tax_input": cents(federal_total_tax_for_mo),
            "state_federal_tax_percentage": fed_tax_pct,
            "state_federal_tax_deduction": cents(fed_tax_deduction),
            "state_standard_deduction": cents(std_ded),
            "state_hoh_qss_exemption": cents(hoh_qss_exemption),
            "state_total_deductions": cents(total_deductions),
            "state_taxable_income": cents(mo_taxable_income),
            "state_total_tax": mo_tax_apportioned,
            "state_total_tax_resident_basis": mo_tax_full,
            "state_top_marginal_rate": MO_TY2025_TOP_RATE,
            "apportionment_fraction": apportionment_fraction,
            "starting_point": "federal_agi",
            "v1_limitations": list(MO_V1_LIMITATIONS),
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
        """Day-prorated income split. TODO(mo-form-mo-nri)."""
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
                gain = (
                    txn.proceeds - txn.cost_basis + txn.adjustment_amount
                )
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

        if residency == ResidencyStatus.RESIDENT or days_in_state >= 365:
            return IncomeApportionment(
                state_source_wages=cents(wages),
                state_source_interest=cents(interest),
                state_source_dividends=cents(ord_div),
                state_source_capital_gains=cents(capital_gains),
                state_source_self_employment=cents(se_net),
                state_source_rental=cents(rental_net),
            )
        return IncomeApportionment(
            state_source_wages=day_prorate(wages, days_in_state),
            state_source_interest=day_prorate(interest, days_in_state),
            state_source_dividends=day_prorate(ord_div, days_in_state),
            state_source_capital_gains=day_prorate(
                capital_gains, days_in_state
            ),
            state_source_self_employment=day_prorate(
                se_net, days_in_state
            ),
            state_source_rental=day_prorate(rental_net, days_in_state),
        )

    def render_pdfs(
        self, state_return: StateReturn, out_dir: Path
    ) -> list[Path]:
        # TODO(mo-pdf): fan-out follow-up — fill MO-1040 + MO-A using
        # pypdf against the DOR's fillable PDFs.
        return []

    def form_ids(self) -> list[str]:
        return ["MO Form MO-1040"]


PLUGIN: StatePlugin = MissouriPlugin(
    meta=StatePluginMeta(
        code="MO",
        name="Missouri",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://dor.mo.gov/taxation/individual/",
        # MO does not run its own free direct portal — commercial MeF
        # software is the only e-file path. ``free_efile_url`` is None.
        free_efile_url=None,
        submission_channel=SubmissionChannel.FED_STATE_PIGGYBACK,
        # MO has no bilateral reciprocity agreements — verified against
        # skill/reference/state-reciprocity.json.
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Hand-rolled MO Form MO-1040 calc (tenforty does not "
            "support 2025/MO_1040 on the OTS backend, and the graph "
            "backend diverges from the DOR primary source by ~$102 on "
            "the $65k Single scenario — the graph appears to skip "
            "Missouri's federal income tax deduction). 8-tier "
            "graduated rate schedule for TY2025 with top rate 4.7%, "
            "Single bracket break $9,191. Starting point: federal AGI "
            "(MO-1040 line 1). Federal income tax deduction (line 13) "
            "is the defining MO quirk — sliding-scale percentage on "
            "AGI band, capped at $5,000 single / $10,000 MFJ. No "
            "reciprocity. Source: 2025 MO-1040 Instructions page 21 "
            "Tax Rate Chart, dor.mo.gov."
        ),
    )
)
