"""Minnesota (MN) state plugin - TY2025.

See skill/reference/tenforty-ty2025-gap.md for the TY2025 probe rubric
and why MN is hand-rolled instead of graph-wrapped (OTS_FORM_CONFIG
has no MN_M1 entries for any year).

*** TENFORTY MN SUPPORT STATUS: NOT AVAILABLE ***

The fan-out spec for this agent stated that tenforty / OpenTaxSolver supports
Minnesota via ``OTSState.MN -> MN_M1``. That IS present in the enum and form
dispatch alias table, BUT as of the pinned tenforty wheel in this repo:

    >>> import tenforty
    >>> tenforty.evaluate_return(year=2025, state='MN',
    ...                          filing_status='Single', w2_income=65000,
    ...                          standard_or_itemized='Standard')
    Traceback (most recent call last):
      ...
    ValueError: OTS does not support 2025/MN_M1

A direct probe of ``tenforty.core.OTS_FORM_CONFIG`` confirms that no
(year, form) pair for ``MN_M1`` is registered for any of 2018-2025 - the MN
form is defined upstream but never packaged into the OpenTaxSolver form
dispatch table that tenforty ships. The 2025 form set ships with only 13
state forms: AZ, CA, MA, MI, NC, NJ, NY, OH, OR, PA, VA (plus federal
schedules). MN is NOT in the list.

=============================================================================
THIS PLUGIN HAND-ROLLS THE MN FORM M1 CALCULATION. Treat its output as a
first-cut approximation, NOT a bit-for-bit OpenTaxSolver reference.
=============================================================================

The fan-out agent that wrote this plugin followed the oh.py / nj.py
"tenforty-backed" shape spec was told to use, but pivoted to native
computation the moment tenforty raised. The module-level docstring, the
state_specific payload's ``v1_limitations`` list, and the tests all document
this divergence LOUDLY.

Rate, bracket, standard-deduction, and dependent-exemption sources
(verified 2026-04-11):

- Minnesota Department of Revenue, "Tax Year 2025 Minnesota Revenue Tax
  Professional Desk Reference Chart" (January 2026), page 2
  "2025 Minnesota Income Tax Brackets":
  https://www.revenue.state.mn.us/sites/default/files/2026-01/tax-year-2025-tax-professional-desk-reference-chart-final.pdf

- Minnesota Department of Revenue, "Minnesota Individual Income Tax Rates
  and Brackets" (2025):
  https://www.revenue.state.mn.us/minnesota-income-tax-rates-and-brackets

- Minnesota Department of Revenue press release, "Minnesota income tax
  brackets, standard deduction and dependent exemption amounts for 2025"
  (2024-12-16):
  https://www.revenue.state.mn.us/press-release/2024-12-16/minnesota-income-tax-brackets-standard-deduction-and-dependent-exemption

- Minnesota Department of Revenue, "2025 Form M1, Individual Income Tax"
  (2025-12, filed copy 2026-03):
  https://www.revenue.state.mn.us/sites/default/files/2026-03/m1-25.pdf

MN Form M1 line structure (from the 2025 M1 PDF above):

    Line 1  Federal AGI (from federal Form 1040 line 11)
    Line 2  Additions (Schedule M1M line 10 + Schedule M1MB line 9)
    Line 3  = Line 1 + Line 2
    Line 4  Itemized deductions (M1SA) OR MN standard deduction
    Line 5  Exemptions (from Schedule M1DQC - $5,200 per dependent in 2025)
    Line 6  State income tax refund from federal Schedule 1 line 1
    Line 7  Subtractions (Schedule M1M line 40 + M1MB line 22)
    Line 8  = Line 4 + Line 5 + Line 6 + Line 7
    Line 9  MN taxable income = Line 3 - Line 8 (floor at zero)
    Line 10 Tax from the table / schedules (bracket schedule)

v1 approximations (see V1_LIMITATIONS below):

    Line 2  : taken as zero (no MN additions modeled)
    Line 4  : MN standard deduction by filing status; itemized not modeled
    Line 5  : $5,200 * num_dependents (M1DQC)
    Line 6  : zero (state-refund add-back not modeled)
    Line 7  : zero (no MN subtractions modeled)

TY2025 MN standard deduction amounts (from the MN DOR 2025 Tax Professional
Desk Reference Chart and the 2024-12-16 press release):

    Single                                        $14,950
    Married Filing Separately                     $14,950
    Head of Household                             $22,500
    Married Filing Jointly                        $29,900
    Qualifying Surviving Spouse                   $29,900

High-income standard-deduction phaseout: if AGI > $238,950 ($119,475 MFS)
the standard deduction is reduced per the M1 instructions worksheet (the
"80% reduction at 3% of AGI over threshold" pattern that mirrors the
pre-TCJA federal Pease limitation for standard deductions). That phaseout
is NOT modeled in v1 - it is called out in ``V1_LIMITATIONS``.

TY2025 MN dependent exemption: $5,200 per dependent, NO personal exemption
(consistent with TCJA conformity). The dependent exemption phases out at
high incomes on Schedule M1DQC; the v1 plugin does not model the phaseout.

TY2025 MN bracket schedule (identical structure across filing statuses,
only the breakpoints differ - verified from the Desk Reference Chart):

    Single
        5.35%   on $0 - $32,570
        6.80%   on $32,570 - $106,990
        7.85%   on $106,990 - $198,630
        9.85%   on $198,630+

    Married Filing Jointly / Qualifying Surviving Spouse
        5.35%   on $0 - $47,620
        6.80%   on $47,620 - $189,180
        7.85%   on $189,180 - $330,410
        9.85%   on $330,410+

    Married Filing Separately
        5.35%   on $0 - $23,810
        6.80%   on $23,810 - $94,590
        7.85%   on $94,590 - $165,205
        9.85%   on $165,205+

    Head of Household
        5.35%   on $0 - $40,100
        6.80%   on $40,100 - $161,130
        7.85%   on $161,130 - $264,050
        9.85%   on $264,050+

$65k Single / Standard wrap-correctness lock (this plugin's own math):

    Line 1  Federal AGI                 $65,000.00
    Line 2  Additions                        $0.00
    Line 3                              $65,000.00
    Line 4  MN Standard Deduction       $14,950.00
    Line 5  Dependents                       $0.00
    Line 6  State refund add-back            $0.00
    Line 7  Subtractions                     $0.00
    Line 8                              $14,950.00
    Line 9  MN Taxable Income           $50,050.00
    Line 10 MN Tax
            0-32,570 @ 5.35%            $1,742.4950
            32,570-50,050 @ 6.80%       $1,188.6400
            Total before rounding       $2,931.1350
                                        --------
    Line 10 rounds to                   $2,931.14

The test suite pins ``state_total_tax == Decimal('2931.14')`` for this
scenario, so any drift in bracket math, standard deduction, or rounding
will fail CI. NOTE: because tenforty does not support MN_M1 for TY2025,
this $2,931.14 figure is NOT an independent third-party check; it is the
plugin's OWN computation locked against its own bracket constants. If we
later gain access to the MN DOR's tax table or an external reference, the
test should be updated to reconcile.

Reciprocity: MN has EXACTLY TWO bilateral reciprocity partners:

    - Michigan (MI)        - {"states": ["MI", "MN"]}
    - North Dakota (ND)    - {"states": ["MN", "ND"]}

Verified against skill/reference/state-reciprocity.json (both pairs are
present in the ``agreements`` array) and against Tax Foundation's
"State Reciprocity Agreements" research page. NOTE: Minnesota previously
had a reciprocity agreement with Wisconsin that was terminated in 2010 and
has NOT been reinstated as of TY2025; the MN DOR continues to publish that
status:

    https://www.revenue.state.mn.us/nonresidents-and-part-year-residents
    https://www.revenue.state.mn.us/news/minnesota-and-wisconsin-do-not-have-income-tax-reciprocity

MN-WI commuters must file nonresident returns in the work state and claim
the resident-credit on their home-state return.

Submission channel: MN does not participate in the IRS Fed/State MeF
program for individual returns. The canonical free submission path for
individuals is Minnesota e-Services at https://www.mndor.state.mn.us/tp/eservices/.
See also the DOR's general e-file page:

    https://www.revenue.state.mn.us/individuals

MN e-Services classifies as ``SubmissionChannel.STATE_DOR_FREE_PORTAL``.

Nonresident / part-year handling: MN Form M1 line 13 directs part-year
residents and nonresidents to Schedule M1NR (Nonresidents/Part-Year
Residents), which prorates the resident-basis tax by a MN-source-income
ratio. v1 uses day-based proration as a first-order approximation, which
is the shared first-cut across all fan-out state plugins. The
``v1_limitations`` list calls out this TODO loudly.
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
from skill.scripts.states._plugin_api import (
    FederalTotals,
    IncomeApportionment,
    StatePlugin,
    StatePluginMeta,
    StateStartingPoint,
    SubmissionChannel,
)


# Canonical wave-4 $65k Single gatekeeper lock. Hand-traced from MN
# Form M1 bracket schedule — see module docstring. Referenced from
# test_state_mn.py.
LOCK_VALUE: Final[Decimal] = Decimal("2931.14")


# ---------------------------------------------------------------------------
# TY2025 constants
# ---------------------------------------------------------------------------


_CENTS = Decimal("0.01")


# MN Form M1 standard deduction (line 4) by filing status.
# Source: MN DOR Tax Year 2025 Tax Professional Desk Reference Chart, Jan 2026.
MN_TY2025_STANDARD_DEDUCTION: dict[FilingStatus, Decimal] = {
    FilingStatus.SINGLE: Decimal("14950"),
    FilingStatus.MFS: Decimal("14950"),
    FilingStatus.HOH: Decimal("22500"),
    FilingStatus.MFJ: Decimal("29900"),
    FilingStatus.QSS: Decimal("29900"),
}


# MN dependent exemption (line 5, from Schedule M1DQC) per dependent.
# Source: MN DOR Tax Year 2025 Tax Professional Desk Reference Chart and
# 2024-12-16 press release - "Dependent exemption: $5,200".
MN_TY2025_DEPENDENT_EXEMPTION: Decimal = Decimal("5200")


# High-income AGI thresholds above which the MN standard deduction phases
# out. NOT modeled in v1 - see V1_LIMITATIONS.
#
# Source: MN DOR "2025 Standard Deduction Limitations & Itemized Deduction
# Phaseout Table" (Desk Reference Chart page 2):
#     Married Filing Separately          $119,475
#     All Others                         $238,950
MN_TY2025_STANDARD_DEDUCTION_PHASEOUT_AGI: dict[FilingStatus, Decimal] = {
    FilingStatus.SINGLE: Decimal("238950"),
    FilingStatus.MFJ: Decimal("238950"),
    FilingStatus.HOH: Decimal("238950"),
    FilingStatus.QSS: Decimal("238950"),
    FilingStatus.MFS: Decimal("119475"),
}


# MN bracket schedule, keyed by FilingStatus. Each entry is a tuple of
# (upper_inclusive_or_None, base_tax, rate, floor) rows. Tax on a given
# taxable income is: base_tax + (taxable_income - floor) * rate for the
# first row whose upper bound is None or >= taxable_income.
#
# Source: MN DOR "Tax Year 2025 Tax Professional Desk Reference Chart",
# January 2026, page 2, "2025 Minnesota Income Tax Brackets".
#
# Precomputed base_tax values:
#
#   Single:
#     row1: floor=0,       base=0
#     row2: floor=32570,   base=32570 * 0.0535 = 1742.4950
#     row3: floor=106990,  base=1742.4950 + (106990-32570)*0.068 = 6803.0550
#     row4: floor=198630,  base=6803.0550 + (198630-106990)*0.0785 = 13996.7950
#
#   MFJ / QSS:
#     row1: floor=0,       base=0
#     row2: floor=47620,   base=47620 * 0.0535 = 2547.6700
#     row3: floor=189180,  base=2547.6700 + (189180-47620)*0.068 = 12173.7500
#     row4: floor=330410,  base=12173.7500 + (330410-189180)*0.0785 = 23260.3050
#
#   MFS:
#     row1: floor=0,       base=0
#     row2: floor=23810,   base=23810 * 0.0535 = 1273.8350
#     row3: floor=94590,   base=1273.8350 + (94590-23810)*0.068 = 6086.8750
#     row4: floor=165205,  base=6086.8750 + (165205-94590)*0.0785 = 11629.1525
#
#   HOH:
#     row1: floor=0,       base=0
#     row2: floor=40100,   base=40100 * 0.0535 = 2145.3500
#     row3: floor=161130,  base=2145.3500 + (161130-40100)*0.068 = 10375.3900
#     row4: floor=264050,  base=10375.3900 + (264050-161130)*0.0785 = 18454.6100
MN_TY2025_BRACKETS: dict[
    FilingStatus, tuple[tuple[Decimal | None, Decimal, Decimal, Decimal], ...]
] = {
    FilingStatus.SINGLE: (
        (Decimal("32570"),  Decimal("0"),          Decimal("0.0535"), Decimal("0")),
        (Decimal("106990"), Decimal("1742.4950"),  Decimal("0.068"),  Decimal("32570")),
        (Decimal("198630"), Decimal("6803.0550"),  Decimal("0.0785"), Decimal("106990")),
        (None,              Decimal("13996.7950"), Decimal("0.0985"), Decimal("198630")),
    ),
    FilingStatus.MFJ: (
        (Decimal("47620"),  Decimal("0"),          Decimal("0.0535"), Decimal("0")),
        (Decimal("189180"), Decimal("2547.6700"),  Decimal("0.068"),  Decimal("47620")),
        (Decimal("330410"), Decimal("12173.7500"), Decimal("0.0785"), Decimal("189180")),
        (None,              Decimal("23260.3050"), Decimal("0.0985"), Decimal("330410")),
    ),
    FilingStatus.QSS: (
        (Decimal("47620"),  Decimal("0"),          Decimal("0.0535"), Decimal("0")),
        (Decimal("189180"), Decimal("2547.6700"),  Decimal("0.068"),  Decimal("47620")),
        (Decimal("330410"), Decimal("12173.7500"), Decimal("0.0785"), Decimal("189180")),
        (None,              Decimal("23260.3050"), Decimal("0.0985"), Decimal("330410")),
    ),
    FilingStatus.MFS: (
        (Decimal("23810"),  Decimal("0"),          Decimal("0.0535"), Decimal("0")),
        (Decimal("94590"),  Decimal("1273.8350"),  Decimal("0.068"),  Decimal("23810")),
        (Decimal("165205"), Decimal("6086.8750"),  Decimal("0.0785"), Decimal("94590")),
        (None,              Decimal("11629.1525"), Decimal("0.0985"), Decimal("165205")),
    ),
    FilingStatus.HOH: (
        (Decimal("40100"),  Decimal("0"),          Decimal("0.0535"), Decimal("0")),
        (Decimal("161130"), Decimal("2145.3500"),  Decimal("0.068"),  Decimal("40100")),
        (Decimal("264050"), Decimal("10375.3900"), Decimal("0.0785"), Decimal("161130")),
        (None,              Decimal("18454.6100"), Decimal("0.0985"), Decimal("264050")),
    ),
}


MN_V1_LIMITATIONS: tuple[str, ...] = (
    "MN additions (Schedule M1M line 10 + Schedule M1MB line 9) are not "
    "modeled: reservation income addback, state bond interest addback, "
    "529 plan addback, pass-through entity tax addback, NOL addback, "
    "SALT parity addback, federal bonus depreciation/section 179 "
    "addbacks, domestic production activities, etc.",
    "MN subtractions (Schedule M1M line 40 + Schedule M1MB line 22) are "
    "not modeled: K-12 education expense subtraction, social security "
    "benefits subtraction (MN phaseout), active military pay, National "
    "Guard pay, railroad retirement board benefits, MN 529 plan "
    "contributions, charitable contribution subtraction for non-itemizers, "
    "subtraction for elderly/disabled, organ donor subtraction, first-time "
    "home buyer savings, pension subtraction, public pension subtraction, "
    "etc.",
    "MN itemized deductions (Schedule M1SA) are not modeled. v1 always "
    "takes the standard deduction for line 4.",
    "State income tax refund addback (Form M1 line 6, from federal "
    "Schedule 1 line 1) is not modeled.",
    "MN Alternative Minimum Tax (Schedule M1MT) not computed.",
    "MN dependent-exemption phaseout on Schedule M1DQC (high-income) is "
    "not modeled. v1 applies the flat $5,200-per-dependent exemption "
    "without AGI-based reduction.",
    "MN standard-deduction phaseout for AGI above $238,950 ($119,475 MFS) "
    "is not modeled. v1 applies the flat standard deduction at all AGIs. "
    "See Form M1 instructions worksheet for line 4.",
    "MN nonrefundable credits (Schedule M1C) not computed: credit for "
    "long-term care insurance, credit for K-12 education credit, credit "
    "for past military service, historic structure rehab, etc.",
    "MN refundable credits (Schedule M1REF) not computed: Minnesota "
    "Working Family Credit, Minnesota Child Tax Credit, K-12 Education "
    "Credit, Child and Dependent Care Credit, Property Tax Refund, "
    "Renter's credit, Marriage credit, etc.",
    "Nonresident / part-year apportionment uses day-based proration "
    "(days_in_state / 365). The real treatment is Schedule M1NR with a "
    "MN-source-income ratio.",
    "MN tax computed bracket-by-bracket; MN DOR publishes a printed tax "
    "table for incomes below $100,000 whose values are rounded to the "
    "nearest dollar per $50 income step. v1 uses exact bracket math and "
    "may differ by up to a few dollars from the printed table values.",
)


def _d(v: Any) -> Decimal:
    """Coerce a float / Decimal / None to Decimal."""
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

    Residents get 1.0. Nonresidents and part-year residents are prorated by
    days_in_state / 365. Clamped to [0, 1].

    TODO(mn-m1nr): real MN nonresident / part-year treatment is Schedule
    M1NR, which prorates by MN-source income rather than day count. This
    matches the other fan-out state plugins' first-cut approach.
    """
    if residency == ResidencyStatus.RESIDENT:
        return Decimal("1")
    frac = Decimal(days_in_state) / Decimal("365")
    if frac < 0:
        return Decimal("0")
    if frac > 1:
        return Decimal("1")
    return frac


def _mn_standard_deduction(filing_status: FilingStatus) -> Decimal:
    """Return the TY2025 MN standard deduction for the filing status."""
    return MN_TY2025_STANDARD_DEDUCTION[filing_status]


def _mn_dependent_exemption(num_dependents: int) -> Decimal:
    """Return MN Form M1 line 5 (M1DQC exemptions) = $5,200 per dependent.

    Clamps negative dependent counts to zero. Does NOT apply the high-income
    phaseout - see V1_LIMITATIONS.
    """
    if num_dependents <= 0:
        return Decimal("0")
    return Decimal(num_dependents) * MN_TY2025_DEPENDENT_EXEMPTION


def _mn_bracket_tax(
    taxable_income: Decimal, filing_status: FilingStatus
) -> Decimal:
    """Apply the TY2025 MN bracket schedule to a taxable-income amount.

    Returns a non-negative Decimal rounded to cents. Negative or zero
    taxable income yields zero. Uses the (upper, base, rate, floor) row
    model; tax = base + (taxable_income - floor) * rate for the first row
    whose upper bound is None or >= taxable_income.
    """
    if taxable_income <= 0:
        return Decimal("0")
    schedule = MN_TY2025_BRACKETS[filing_status]
    for upper, base_tax, rate, floor in schedule:
        if upper is None or taxable_income <= upper:
            tax = base_tax + (taxable_income - floor) * rate
            return tax.quantize(_CENTS, rounding=ROUND_HALF_UP)
    # Unreachable — the last row always has upper=None.
    raise RuntimeError(
        f"MN bracket table did not cover taxable_income={taxable_income}"
    )


def _mn_taxable_income(
    federal: FederalTotals,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Compute the MN taxable income line 9 from FederalTotals.

    Returns a tuple of (line_3, line_4, line_5, line_9) for introspection:

        line_3 = federal AGI + MN additions (v1: additions = 0)
        line_4 = MN standard deduction for filing status
        line_5 = $5,200 * num_dependents (M1DQC exemptions)
        line_9 = max(0, line_3 - line_4 - line_5)
                 [v1 treats line 6 and line 7 as zero]
    """
    line_3 = federal.adjusted_gross_income  # line 1 + 0 additions
    line_4 = _mn_standard_deduction(federal.filing_status)
    line_5 = _mn_dependent_exemption(federal.num_dependents)
    line_9 = line_3 - line_4 - line_5
    if line_9 < 0:
        line_9 = Decimal("0")
    return (line_3, line_4, line_5, line_9)


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MinnesotaPlugin:
    """State plugin for Minnesota.

    Hand-rolled MN Form M1 calc (tenforty does NOT support 2025/MN_M1).
    Starting point is federal AGI; v1 applies the MN standard deduction
    and dependent exemption, then the MN four-bracket graduated schedule
    for the taxpayer's filing status. None of MN's additions, subtractions,
    AMT, or credits are modeled in v1 - see ``V1_LIMITATIONS``.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        line_3, line_4, line_5, line_9 = _mn_taxable_income(federal)
        line_10 = _mn_bracket_tax(line_9, federal.filing_status)

        # Apportion for nonresident / part-year.
        # TODO(mn-m1nr): replace with Schedule M1NR MN-source-income ratio.
        fraction = _apportionment_fraction(residency, days_in_state)
        state_tax_apportioned = _cents(line_10 * fraction)

        state_specific: dict[str, Any] = {
            "state_adjusted_gross_income": _cents(federal.adjusted_gross_income),
            "state_taxable_income": _cents(line_9),
            "state_total_tax": state_tax_apportioned,
            "state_total_tax_resident_basis": _cents(line_10),
            "apportionment_fraction": fraction,
            # M1 line-level detail for introspection / downstream rendering.
            "m1_line_1_federal_agi": _cents(federal.adjusted_gross_income),
            "m1_line_2_additions": Decimal("0.00"),
            "m1_line_3": _cents(line_3),
            "m1_line_4_standard_deduction": _cents(line_4),
            "m1_line_5_exemptions": _cents(line_5),
            "m1_line_6_state_refund_addback": Decimal("0.00"),
            "m1_line_7_subtractions": Decimal("0.00"),
            "m1_line_8_total_subtractions": _cents(line_4 + line_5),
            "m1_line_9_mn_taxable_income": _cents(line_9),
            "m1_line_10_tax": _cents(line_10),
            "starting_point": "federal_agi",
            "tenforty_supports_mn": False,
            "tenforty_status_note": (
                "tenforty/OpenTaxSolver does not ship MN_M1 in its 2025 form "
                "dispatch table (ValueError: OTS does not support 2025/MN_M1). "
                "This plugin hand-rolls the MN bracket math; it is NOT a "
                "bit-for-bit OTS reference. See module docstring."
            ),
            "v1_limitations": list(MN_V1_LIMITATIONS),
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
        """Split canonical income into MN-source vs non-MN-source.

        Residents: everything is MN-source. Nonresident / part-year: prorate
        each category by days_in_state / 365.

        TODO(mn-m1nr): MN actually sources each income type differently on
        Schedule M1NR (wages to work location, interest / dividends to
        domicile, rental to property state, etc.). Day-based proration is
        the shared first-cut across all fan-out state plugins; refine with
        the real M1NR apportionment in follow-up.
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

        # Reuse engine helpers for Schedule C / E net totals so MN mirrors
        # the federal calc's own rollup logic.
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
        # TODO(mn-pdf): fan-out follow-up — fill MN Form M1 (and Schedule
        # M1M, M1MB, M1SA, M1DQC, M1NR for nonresidents, M1W for withholding)
        # using pypdf against the MN DOR's fillable PDFs. The output
        # renderer suite is the right home for this; the plugin returns
        # structured state_specific data that the renderer will consume.
        return []

    def form_ids(self) -> list[str]:
        return ["MN Form M1"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = MinnesotaPlugin(
    meta=StatePluginMeta(
        code="MN",
        name="Minnesota",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://www.revenue.state.mn.us/individual-income-tax",
        free_efile_url="https://www.mndor.state.mn.us/tp/eservices/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # MN has exactly two bilateral reciprocity partners — MI and ND —
        # verified against skill/reference/state-reciprocity.json.
        # (MN-WI reciprocity was terminated in 2010 and has not been
        # reinstated as of TY2025.)
        reciprocity_partners=("MI", "ND"),
        supported_tax_years=(2025,),
        notes=(
            "Hand-rolled MN Form M1 calc. tenforty does NOT support "
            "2025/MN_M1 (ValueError: OTS does not support 2025/MN_M1), "
            "so the plugin implements the four-bracket graduated schedule "
            "(5.35% / 6.80% / 7.85% / 9.85%) directly from the MN DOR "
            "2025 Tax Professional Desk Reference Chart. Starting point: "
            "federal AGI (Form M1 line 1). TY2025 standard deduction: "
            "Single $14,950, MFJ $29,900, HOH $22,500, MFS $14,950, QSS "
            "$29,900. Dependent exemption: $5,200 per dependent "
            "(Schedule M1DQC). Reciprocity partners: MI, ND "
            "(MN-WI reciprocity terminated 2010)."
        ),
    )
)
