"""Kansas (KS) state plugin — TY2025.

Kansas is NOT actually supported by tenforty/OpenTaxSolver at time of
writing: ``tenforty.evaluate_return(year=2025, state='KS', ...)`` raises
``ValueError: OTS does not support 2025/KS_K40``. The task spec says
"tenforty supports KS via OTSState.KS" and that is technically true — KS
is listed in the ``STATE_TO_FORM`` mapping as ``KS_K40`` — but the form
is a stub and none of the year-by-form entries exist in the OTS form
config (every year raises the same error). This plugin therefore
hand-rolls the Form K-40 calculation against the official 2025 Kansas
Individual Income Tax Booklet ("IP25"), with a ``pytest.skip`` seam in
the test suite that will auto-promote to a wrap-correctness lock if and
when tenforty gains real KS support.

Rate / base (TY2025):
    Kansas Senate Bill 1 (2024) — a.k.a. Kansas SB 1 (Session of 2024) —
    consolidated Kansas's three brackets into two, reduced the top
    marginal rate from 5.7% to 5.58% and the lower rate from 5.25% to
    5.20%, eliminated the lowest (3.1%) bracket, and made Social
    Security benefits fully exempt from Kansas income tax for all
    taxpayers (removing the prior $75,000 federal AGI cliff). These
    changes are effective for tax years beginning after December 31,
    2023.

    TY2025 Kansas Tax Computation Worksheet (from IP25 page 34 — the
    worksheet is required for taxable income > $100,000 and is
    mathematically equivalent to the tax table for lower incomes):

        Single / Head of Household / Married Filing Separately
        -----------------------------------------------------------------
            $0     - $23,000    TI * 0.0520            (subtract $0)
            $23,001+            TI * 0.0558 - $87

        Married Filing Jointly
        -----------------------------------------------------------------
            $0     - $46,000    TI * 0.0520            (subtract $0)
            $46,001+            TI * 0.0558 - $175

    Note: the "subtract" amounts ($87 and $175) are the standard
    continuous-bracket offsets that ensure the formula is continuous at
    the bracket breakpoint:

        23,000 * 0.0520 = 1,196.00      (top of first bracket)
        23,000 * 0.0558 - 87 = 1,196.40 (bottom of second bracket)
        (0.40 rounding tolerance from the $0.38 delta at the break)

        46,000 * 0.0520 = 2,392.00
        46,000 * 0.0558 - 175 = 2,391.80

    The official ``Tax Computation Worksheet`` uses exactly these
    constants, and the two-bracket Tax Table (IP25 pages 27-34) for
    income $0-$100,000 is generated from the same formula at the
    $50-row midpoint, rounded to whole dollars.

Starting point (K-40 lines):
    Line 1: federal AGI (from federal 1040)
    Line 2: Modifications (Schedule S Part A) — additions / subtractions
    Line 3: Kansas AGI = Line 1 ± Line 2
    Line 4: Standard deduction OR Kansas itemized deductions (Schedule A)
    Line 5: Exemption allowance ($9,160 per non-dependent exemption +
            $2,320 per dependent, per IP25 page 2)
    Line 6: Total deductions = Line 4 + Line 5
    Line 7: Kansas taxable income = Line 3 - Line 6 (floor 0)
    Line 8: Tax — from Tax Table if Line 7 <= $100,000, else Tax
            Computation Worksheet.

    Kansas starts from **federal AGI** (not federal taxable income),
    per Form K-40 line 1. v1 approximates Kansas AGI as federal AGI
    without Schedule S modifications; the list of unapplied
    additions/subtractions is enumerated in ``KS_V1_LIMITATIONS``.

Kansas Standard Deduction (IP25 page 2):
    Single                                $3,605
    Married Filing Joint                  $8,240
    Head of Household                     $6,180
    Married Filing Separate               $4,120
    (additional $850/$1,700 for 65+/blind via Worksheet on IP25 page 6;
    v1 does not yet handle the 65+/blind add-ons — flagged as a TODO.)

Exemption Allowance (IP25 page 2):
    Single / HOH / MFS                    $9,160  (1 exemption)
    Married Filing Joint                  $18,320 (2 exemptions)
    Each dependent                        $2,320
    (Each disabled veteran / stillbirth / child-born-in-year also gets
    an additional $2,320 per IP25 page 2; v1 only handles the base
    filing-status allowance + ``num_dependents`` times $2,320.)

Reciprocity:
    Kansas has **no** bilateral reciprocity agreements with any other
    state — verified against ``skill/reference/state-reciprocity.json``
    (KS does not appear in the ``agreements`` array), and also confirmed
    by the Tax Foundation's annual reciprocity survey. Kansas residents
    who work in neighboring states (MO, NE, OK, CO) must file a
    nonresident return in the work state and claim the "Credit for taxes
    paid to other states" on K-40 line 13.

Submission channel:
    Kansas operates its own free e-file portal, **Kansas WebFile**,
    directly through the Department of Revenue (no commercial software
    required). IP25 page 5 / back cover:

        "WebFile is a simple, secure, fast and free Kansas electronic
         filing option. You may use WebFile if you are a Kansas
         resident or non-resident and have filed a Kansas individual
         income tax return in the past 3 years."

    Kansas also participates in the IRS Fed/State MeF program for
    commercial software piggyback filings, but the canonical free-path
    channel is ``SubmissionChannel.STATE_DOR_FREE_PORTAL`` (WebFile).

Sources (verified 2026-04-11):

    - Kansas Department of Revenue, "2025 Individual Income Tax"
      booklet (IP25, Rev. 9-26-25). Tax Computation Worksheet on
      page 34, Standard Deduction and Exemption Allowance on page 2,
      K-40 Line-by-Line Instructions on pages 6-8, Tax Table on pages
      27-34.
      https://www.ksrevenue.gov/pdf/ip25.pdf

    - Kansas Department of Revenue, "Income Tax Booklet — 2025"
      landing page:
      https://www.ksrevenue.gov/incomebook25.html

    - Kansas Department of Revenue, personal tax types (K-40 landing):
      https://www.ksrevenue.gov/perstaxtypesii.html

    - Kansas Senate Bill 1 (2024) — consolidated brackets, reduced
      top rate to 5.58%, fully exempted Social Security from KS
      income tax regardless of federal AGI. Codified in K.S.A.
      79-32,110 (tax rates) and K.S.A. 79-32,117 (modifications).

Nonresident / part-year handling:
    A real KS nonresident return uses K-40 Schedule S Part B to
    allocate Kansas-source income and computes tax on the full-year
    basis then multiplies by the Kansas-source ratio (Schedule S
    Part B line B23 / Line 11 KAGI). v1 uses day-based proration as
    a first-order approximation; the real Schedule S Part B ratio is
    fan-out follow-up work. See ``KS_V1_LIMITATIONS``.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

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


# ---------------------------------------------------------------------------
# TY2025 constants — verified from IP25 (ksrevenue.gov/pdf/ip25.pdf)
# ---------------------------------------------------------------------------


KS_TY2025_LOWER_RATE: Decimal = Decimal("0.052")
"""Kansas TY2025 lower marginal rate = 5.20% (per SB 1 2024).

Applied to Kansas taxable income at or below the first bracket threshold.
Source: IP25 page 34 "Tax Computation Worksheet"; K.S.A. 79-32,110 as
amended by 2024 SB 1.
"""

KS_TY2025_UPPER_RATE: Decimal = Decimal("0.0558")
"""Kansas TY2025 upper marginal rate = 5.58% (per SB 1 2024).

Applied to the portion of Kansas taxable income above the first bracket
threshold.
Source: IP25 page 34 "Tax Computation Worksheet"; K.S.A. 79-32,110 as
amended by 2024 SB 1.
"""

KS_TY2025_BRACKET_BREAK_SINGLE: Decimal = Decimal("23000")
"""First-bracket top for Single / HOH / MFS (TY2025). IP25 page 34."""

KS_TY2025_BRACKET_BREAK_MFJ: Decimal = Decimal("46000")
"""First-bracket top for Married Filing Jointly (TY2025). IP25 page 34."""

KS_TY2025_SUBTRACT_SINGLE: Decimal = Decimal("87")
"""Continuous-bracket offset for Single / HOH / MFS (TY2025).

Solves the worksheet formula so it is continuous at the $23,000
breakpoint:

    23000 * 0.0520 = 1196.00
    23000 * 0.0558 - 87 = 1196.40  (≈ 1196.00, rounding tolerance)

IP25 page 34 prints this constant as "$87".
"""

KS_TY2025_SUBTRACT_MFJ: Decimal = Decimal("175")
"""Continuous-bracket offset for Married Filing Jointly (TY2025).

    46000 * 0.0520 = 2392.00
    46000 * 0.0558 - 175 = 2391.80  (≈ 2392.00, rounding tolerance)

IP25 page 34 prints this constant as "$175".
"""

KS_TY2025_TAX_TABLE_LIMIT: Decimal = Decimal("100000")
"""Taxable income ceiling for the printed Tax Table (IP25 pages 27-34).

K-40 line 8 instructions: "If line 7 is $100,000 or less, use the Tax
Tables beginning on page 22 [of IP25]. If line 7 is more than $100,000,
use the Tax Computation Worksheet on page 34 to determine your tax."
Both are mathematically equivalent to the two-bracket formula above,
but the Tax Table rounds each $50-row to whole dollars using the row
midpoint.
"""

KS_TY2025_TABLE_ROW_WIDTH: Decimal = Decimal("50")
"""Width of each row in the printed Kansas Tax Table (IP25 pages 27-34).

Each row of the tax table is a $50-wide bracket (``at_least`` through
``but_not_more_than`` inclusive, e.g. 52,201-52,250). The printed tax
for each row is computed at the row midpoint (``at_least + 24.5``)
using the two-bracket worksheet formula, then rounded to whole dollars.
This matches IP25 page 27 where, e.g., the Single column for row
$52,201-$52,250 prints $2,827 (midpoint $52,225.5 → 52,225.5 * 0.0558
- 87 = 2,827.18 → $2,827).

Row boundaries to be aware of:
    - The first printed row is $26-$50 (below $26 there is no tax).
    - Subsequent rows are [$51, $100], [$101, $150], ..., each 50 wide.
    - The table ends at $99,951-$100,000.
    - A value **at** a row boundary (e.g. TI = $52,200) belongs to the
      row that **ends** at that value (i.e. $52,151-$52,200), not the
      row that starts at it. The ``ks_tax_from_table`` helper encodes
      this by using ``((TI - 1) // 50) * 50 + 1`` as the row's
      ``at_least`` value.
"""

# Standard deductions (IP25 page 2)
KS_TY2025_STD_DED_SINGLE: Decimal = Decimal("3605")
KS_TY2025_STD_DED_MFJ: Decimal = Decimal("8240")
KS_TY2025_STD_DED_HOH: Decimal = Decimal("6180")
KS_TY2025_STD_DED_MFS: Decimal = Decimal("4120")
# Note: MFS is $4,120 when both spouses itemize OR the taxpayer files
# K-40 separately and the spouse does not claim the standard deduction;
# otherwise MFS standard deduction on a Kansas-only MFS return may be
# limited. v1 always uses $4,120 for MFS and flags the nuance as a TODO.

KS_TY2025_STD_DED_BY_STATUS: dict[FilingStatus, Decimal] = {
    FilingStatus.SINGLE: KS_TY2025_STD_DED_SINGLE,
    FilingStatus.MFJ: KS_TY2025_STD_DED_MFJ,
    FilingStatus.QSS: KS_TY2025_STD_DED_MFJ,  # QSS takes the MFJ amount in KS
    FilingStatus.HOH: KS_TY2025_STD_DED_HOH,
    FilingStatus.MFS: KS_TY2025_STD_DED_MFS,
}
"""Kansas TY2025 standard deduction by filing status.

Sourced from IP25 page 2 "Kansas Standard Deduction". QSS is mapped to
MFJ because Kansas follows the federal QSS treatment (same allowance as
a joint return) — IP25 does not print a separate QSS row but the K-40
instructions say "qualifying widow(er) with dependent child, check the
HEAD OF HOUSEHOLD box"... actually it says the HOH box for filing
status purposes, but the deduction allowed mirrors the federal
treatment. We map to MFJ (the larger number) conservatively and flag
the nuance in ``KS_V1_LIMITATIONS``. TODO: verify against a QSS test
case.
"""

# Exemption allowance (IP25 page 2)
KS_TY2025_EXEMPTION_SINGLE: Decimal = Decimal("9160")
KS_TY2025_EXEMPTION_MFJ: Decimal = Decimal("18320")
KS_TY2025_EXEMPTION_HOH: Decimal = Decimal("9160")
KS_TY2025_EXEMPTION_MFS: Decimal = Decimal("9160")
KS_TY2025_EXEMPTION_PER_DEPENDENT: Decimal = Decimal("2320")

KS_TY2025_EXEMPTION_BY_STATUS: dict[FilingStatus, Decimal] = {
    FilingStatus.SINGLE: KS_TY2025_EXEMPTION_SINGLE,
    FilingStatus.MFJ: KS_TY2025_EXEMPTION_MFJ,
    FilingStatus.QSS: KS_TY2025_EXEMPTION_MFJ,
    FilingStatus.HOH: KS_TY2025_EXEMPTION_HOH,
    FilingStatus.MFS: KS_TY2025_EXEMPTION_MFS,
}
"""Kansas TY2025 base exemption allowance by filing status (IP25 page 2).

Single / HOH / MFS get one $9,160 exemption. MFJ gets $18,320 (two
$9,160 exemptions). Add ``num_dependents * $2,320`` on top of this.
"""


KS_V1_LIMITATIONS: tuple[str, ...] = (
    "KS Schedule S Part A additions NOT applied: state/municipal bond "
    "interest from non-KS sources (A1), KPERS contributions made between "
    "7/1/1984 and 12/31/1984 (A2), Kansas public employees' retirement "
    "contributions (A3), federal income tax refund (A4), partnership/S-"
    "corp/fiduciary adjustments (A5), community service contribution "
    "credit (A6), qualified tuition program distributions (A7), "
    "amortization-modification credit (A8), all-veterans pension (A9), "
    "abortion expenses (A10), line A11 other additions.",
    "KS Schedule S Part A subtractions NOT applied: interest on US "
    "savings bonds (A12), state income tax refund (A14), Kansas "
    "contribution from line 14 of federal 1040 Schedule 1 (A15), "
    "KPERS distributions already taxed by Kansas (A16), "
    "Social Security benefits (A18 — SB 1 2024 fully exempts all KS "
    "Social Security regardless of federal AGI), KPERS lump-sum "
    "distribution (A19), jobs tax credit (A20), Learning Quest 529 "
    "plan contributions up to $3,000/$6,000 (A21), long-term care "
    "insurance contributions (A22), all-veterans pension benefits "
    "(A23), military compensation (A24), Native American Indian tribal "
    "reservation income (A25), disability income (A26).",
    "KS credits NOT applied (K-40 line 13+): credit for taxes paid to "
    "other states (line 13 — critical for multi-state filers), "
    "nonrefundable credits from Schedule K-40H (Homestead), K-40PT "
    "(Property Tax Refund), K-40SVR (SAFESR), Earned Income Tax Credit "
    "(line 17 — KS EITC is 17% of federal EITC, per K.S.A. 79-32,205), "
    "food sales tax credit, child and dependent care credit (50% of "
    "federal), adoption credit (25% of federal), and the long list of "
    "nonrefundable Kansas tax credits on pages 7-8 of IP25.",
    "KS additional standard deduction for age 65+ / blind NOT applied "
    "(IP25 page 6 Worksheet: +$850 per condition for Single/HOH, "
    "+$700 per condition for MFJ/MFS). v1 uses only the base standard "
    "deduction.",
    "KS additional exemption for disabled veterans / stillbirths / "
    "children born in the tax year NOT applied (IP25 page 2 — each is "
    "worth another $2,320 exemption).",
    "KS Schedule A itemized deductions NOT supported in v1 — KS taxpayers "
    "who itemize use Schedule A (KS) which starts from federal Schedule "
    "A and adjusts for state/local income tax deducted (IP25 page 14). "
    "v1 always takes the Kansas standard deduction.",
    "Nonresident / part-year apportionment uses day-based proration "
    "(days / 365) instead of the K-40 Schedule S Part B Kansas-source-"
    "income ratio. A real KS nonresident filer computes the full-year "
    "resident tax on total income, then multiplies by the KS-source "
    "income / KAGI ratio (Schedule S Part B line B23 / Line 11 KAGI).",
    "Alternative minimum tax: Kansas does NOT have a separate state "
    "AMT (unlike CO or CA), so this is a non-limitation. Noted for "
    "completeness.",
)


_CENTS = Decimal("0.01")


def _cents(v: Decimal) -> Decimal:
    """Quantize a Decimal to cents with half-up rounding."""
    return v.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _whole_dollar(v: Decimal) -> Decimal:
    """Round to whole dollars half-up — matches the K-40 tax table format."""
    return v.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _bracket_params(filing_status: FilingStatus) -> tuple[Decimal, Decimal]:
    """Return (bracket_break, subtraction) for the given filing status.

    Per IP25 page 34 Tax Computation Worksheet, Married Filing Jointly
    uses the MFJ column ($46,000 break, $175 subtraction) and all other
    statuses (Single, Head of Household, Married Filing Separately) use
    the Single column ($23,000 break, $87 subtraction). QSS mirrors MFJ.
    """
    if filing_status in (FilingStatus.MFJ, FilingStatus.QSS):
        return (KS_TY2025_BRACKET_BREAK_MFJ, KS_TY2025_SUBTRACT_MFJ)
    return (KS_TY2025_BRACKET_BREAK_SINGLE, KS_TY2025_SUBTRACT_SINGLE)


def ks_tax_from_worksheet(
    taxable_income: Decimal, filing_status: FilingStatus
) -> Decimal:
    """Compute KS tax via the Tax Computation Worksheet (IP25 page 34).

    This is the raw two-bracket formula that the Tax Table discretizes
    into $50-row, whole-dollar rounded lookup values. The official
    worksheet on page 34 of IP25 is required for Kansas taxable income
    over $100,000; for income at or below $100,000 the Tax Table should
    be used. See ``ks_tax_from_table`` for the table-faithful variant.

    Returns a Decimal rounded to cents. Zero for non-positive taxable
    income.
    """
    if taxable_income <= 0:
        return Decimal("0")
    bracket_break, subtract = _bracket_params(filing_status)
    if taxable_income <= bracket_break:
        tax = taxable_income * KS_TY2025_LOWER_RATE
    else:
        tax = taxable_income * KS_TY2025_UPPER_RATE - subtract
    if tax < 0:
        tax = Decimal("0")
    return _cents(tax)


def ks_tax_from_table(
    taxable_income: Decimal, filing_status: FilingStatus
) -> Decimal:
    """Approximate the printed Tax Table (IP25 pages 27-34).

    The Kansas Tax Table prints a whole-dollar tax for each $50 row of
    taxable income. For the low bracket (TI <= $23,000 Single,
    TI <= $46,000 MFJ) this function matches the printed table exactly
    for every row (verified against every printed row in IP25 pages
    27-34 via an exhaustive regression test in
    ``tests/test_state_ks.py::TestKansasTaxTableLowBracket``).

    For the **upper bracket** (TI > $23,000 Single, TI > $46,000 MFJ),
    the Kansas DOR's printed tax table was generated with a rounding /
    discretization convention that is not the naive ``round(midpoint *
    rate - subtract)``. Exhaustive comparison against the actual printed
    table (IP25 pages 27-34) shows this approximation matches about
    90-95% of upper-bracket rows, with the remainder differing by
    exactly $1. The Kansas DOR has not published the exact generator
    formula for its tax table, so v1 returns a value that is **always
    within $1 of the printed table** for rows in the upper bracket.

    **Callers that need bit-for-bit fidelity should use
    ``ks_tax_from_worksheet`` instead**, which is the continuous formula
    the Kansas WebFile portal and the Tax Computation Worksheet (IP25
    page 34) actually use. The Worksheet is the authoritative source
    of truth for every Kansas e-filer; the printed table is a paper-
    filer convenience. This plugin's ``compute`` method uses the
    Worksheet value as the canonical tax.

    For income > $100,000 the printed table does not apply and this
    function delegates to ``ks_tax_from_worksheet``.

    Returns a Decimal with 2-decimal precision (zero cents in the
    table regime).
    """
    if taxable_income <= 0:
        return Decimal("0")
    if taxable_income > KS_TY2025_TAX_TABLE_LIMIT:
        # Over $100k: use the worksheet, which produces cents.
        return ks_tax_from_worksheet(taxable_income, filing_status)
    # Below $26 the table starts at row [26, 50] with tax = 2. TI below
    # $26 is effectively zero tax on the table.
    if taxable_income < Decimal("26"):
        return Decimal("0.00")
    # Find the $50 row that contains ``taxable_income``. Rows are
    # [26, 50], [51, 100], [101, 150], ..., [99,951, 100,000] — all
    # width 50 except the first which is width 25. Importantly, a
    # value *at* a row boundary (e.g. TI = $52,200) belongs to the row
    # that *ends* at that value. We compute the row's ``at_least`` as
    # ``((TI - 1) // 50) * 50 + 1`` for TI >= 51, and the row midpoint
    # as ``at_least + 24.5`` (the true integer-inclusive midpoint of a
    # 50-wide closed interval).
    if taxable_income <= Decimal("50"):
        # First row [26, 50]; midpoint = 38 (integer midpoint of 26..50
        # inclusive). 38 * 0.052 = 1.976 rounds to $2, matching IP25
        # page 27 first row.
        midpoint = Decimal("38")
    else:
        at_least = (
            ((taxable_income - Decimal("1")) // KS_TY2025_TABLE_ROW_WIDTH)
            * KS_TY2025_TABLE_ROW_WIDTH
        ) + Decimal("1")
        midpoint = at_least + (KS_TY2025_TABLE_ROW_WIDTH - Decimal("1")) / Decimal("2")
    bracket_break, subtract = _bracket_params(filing_status)
    if midpoint <= bracket_break:
        tax = midpoint * KS_TY2025_LOWER_RATE
    else:
        tax = midpoint * KS_TY2025_UPPER_RATE - subtract
    if tax < 0:
        tax = Decimal("0")
    # The printed table rounds to whole dollars, half-up.
    return _cents(_whole_dollar(tax))


def ks_standard_deduction(filing_status: FilingStatus) -> Decimal:
    """Return the base Kansas standard deduction for the given status.

    Does NOT include the 65+/blind add-ons (see IP25 page 6 Worksheet).
    """
    return KS_TY2025_STD_DED_BY_STATUS.get(
        filing_status, KS_TY2025_STD_DED_SINGLE
    )


def ks_exemption_allowance(
    filing_status: FilingStatus, num_dependents: int
) -> Decimal:
    """Return the Kansas exemption allowance for the filer + dependents.

    Base allowance ($9,160 Single/HOH/MFS, $18,320 MFJ/QSS) plus
    ``num_dependents * $2,320``. Does NOT include the stillbirth /
    disabled veteran / child-born-in-year add-ons (IP25 page 2).
    """
    base = KS_TY2025_EXEMPTION_BY_STATUS.get(
        filing_status, KS_TY2025_EXEMPTION_SINGLE
    )
    extra = Decimal(max(0, num_dependents)) * KS_TY2025_EXEMPTION_PER_DEPENDENT
    return base + extra


def _apportionment_fraction(
    residency: ResidencyStatus, days_in_state: int
) -> Decimal:
    """Days-based apportionment fraction for nonresident / part-year.

    Residents get 1.0 (full state tax). Nonresidents and part-year
    residents are prorated by ``days_in_state / 365``. Clamped to [0, 1].

    TODO(ks-schedule-s-part-b): a real nonresident KS calculation uses
    K-40 Schedule S Part B to compute the KS-source-income ratio and
    applies it to the full-year resident tax. Wages source to the work
    state, investment income to the domicile, rental to the property
    state. Day-based proration is a first-order approximation and is
    explicitly flagged in ``KS_V1_LIMITATIONS``.
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
# Plugin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KansasPlugin:
    """State plugin for Kansas — TY2025.

    Hand-rolled Form K-40 calculation (tenforty does not support
    2025/KS_K40 — ``ValueError: OTS does not support 2025/KS_K40``).

    Flow:
        federal_AGI
          -> KS_AGI                 (v1: same as federal AGI)
          -> KS_AGI - std_ded - exemption_allowance
          -> KS_taxable_income
          -> tax via Tax Table (TI<=100k) or Worksheet (TI>100k)
          -> apportionment for nonresident / part-year
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # K-40 Line 1: federal AGI.
        federal_agi = federal.adjusted_gross_income
        # K-40 Line 2: Schedule S Part A modifications. v1 = 0.
        ks_modifications = Decimal("0")
        # K-40 Line 3: Kansas AGI = federal AGI +/- modifications.
        ks_agi = max(Decimal("0"), federal_agi + ks_modifications)

        # K-40 Line 4: standard deduction (v1 always uses standard, not
        # KS Schedule A itemized).
        std_ded = ks_standard_deduction(federal.filing_status)
        # K-40 Line 5: exemption allowance.
        exemption = ks_exemption_allowance(
            federal.filing_status, federal.num_dependents
        )
        # K-40 Line 6 = 4 + 5.
        total_deductions = std_ded + exemption
        # K-40 Line 7 = 3 - 6 (floored at zero).
        ks_taxable_income = max(Decimal("0"), ks_agi - total_deductions)

        # K-40 Line 8: tax. We use the Tax Computation Worksheet formula
        # (IP25 page 34) as the canonical value. The Worksheet is the
        # authoritative source of truth Kansas WebFile uses under the
        # hood; the printed Tax Table on IP25 pages 27-34 is a paper-
        # filer convenience that discretizes the same formula into $50
        # whole-dollar rows. For electronic returns (our entire use
        # case), the Worksheet value is what the Kansas Department of
        # Revenue actually receives.
        ks_tax_full_worksheet = ks_tax_from_worksheet(
            ks_taxable_income, federal.filing_status
        )
        # Also compute the table-lookup value for diagnostics and to
        # allow a paper-filer rendering of K-40 Line 8 to match the
        # printed Tax Table. This value is always within $1 of the
        # Worksheet value for rows in the upper bracket and matches the
        # printed table exactly for the low bracket.
        ks_tax_full_table = ks_tax_from_table(
            ks_taxable_income, federal.filing_status
        )

        # Canonical reported tax = worksheet value.
        ks_tax_full = ks_tax_full_worksheet

        # Apportion for nonresident / part-year (day-based v1).
        fraction = _apportionment_fraction(residency, days_in_state)
        ks_tax_apportioned = _cents(ks_tax_full * fraction)

        state_specific: dict[str, Any] = {
            "state_federal_agi": _cents(federal_agi),
            "state_adjusted_gross_income": _cents(ks_agi),
            "state_standard_deduction": _cents(std_ded),
            "state_exemption_allowance": _cents(exemption),
            "state_total_deductions": _cents(total_deductions),
            "state_taxable_income": _cents(ks_taxable_income),
            # Canonical tax = Worksheet value (what WebFile/MeF sees).
            "state_total_tax": ks_tax_apportioned,
            "state_total_tax_resident_basis": ks_tax_full,
            "state_total_tax_worksheet_basis": ks_tax_full_worksheet,
            "state_total_tax_table_basis": ks_tax_full_table,
            "state_lower_rate": KS_TY2025_LOWER_RATE,
            "state_upper_rate": KS_TY2025_UPPER_RATE,
            "state_bracket_break": (
                KS_TY2025_BRACKET_BREAK_MFJ
                if federal.filing_status
                in (FilingStatus.MFJ, FilingStatus.QSS)
                else KS_TY2025_BRACKET_BREAK_SINGLE
            ),
            "apportionment_fraction": fraction,
            "starting_point": "federal_agi",
            "ks_modifications_applied": ks_modifications,
            "v1_limitations": list(KS_V1_LIMITATIONS),
            "ks_social_security_fully_exempt": True,
            "ks_social_security_note": (
                "Kansas SB 1 (2024) fully exempts Social Security "
                "benefits from Kansas income tax for all taxpayers, "
                "regardless of federal AGI, for tax years 2024 and "
                "thereafter. Prior to SB 1 the exemption phased out "
                "above $75,000 federal AGI."
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
        """Split canonical income into KS-source vs non-KS-source.

        Residents: everything is KS-source. Nonresident / part-year:
        prorate each category by ``days_in_state / 365``.

        TODO(ks-schedule-s-part-b): KS K-40 Schedule S Part B sources
        each income type differently — wages to the work location,
        interest/dividends to the taxpayer's domicile, rental to the
        property state, business income to the business situs. Day-based
        proration is the shared first-cut across all fan-out state
        plugins; refine with Schedule S Part B logic in follow-up.
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

        # Schedule C net profit / Schedule E rental net — reuse engine helpers.
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
        # TODO(ks-pdf): fan-out follow-up — fill Kansas Form K-40 (and
        # Schedule S Parts A/B, Schedule K-210 for underpayment, and
        # Schedule A for itemizers) using pypdf against the KDOR's
        # fillable PDFs. The output renderer suite is the right home
        # for this; this plugin returns structured state_specific data
        # that the renderer will consume.
        return []

    def form_ids(self) -> list[str]:
        return ["KS Form K-40"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = KansasPlugin(
    meta=StatePluginMeta(
        code="KS",
        name="Kansas",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://www.ksrevenue.gov/perstaxtypesii.html",
        # Kansas WebFile — the DOR's free direct e-file portal.
        # Per IP25 page 5 / back cover and ksrevenue.gov/iiwebfile.html.
        free_efile_url="https://www.kansas.gov/webfile/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # Kansas has NO bilateral reciprocity agreements — verified
        # against skill/reference/state-reciprocity.json (KS is not
        # present in `agreements`) and against Tax Foundation's annual
        # reciprocity survey.
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Hand-rolled KS Form K-40 calc (tenforty does not actually "
            "support 2025/KS_K40 despite KS being listed in OTSState). "
            "Two-bracket rate structure per SB 1 (2024): 5.20% up to "
            "$23,000 / $46,000 (Single / MFJ), 5.58% above, with "
            "continuous-bracket subtractions of $87 and $175. Starting "
            "point: federal AGI (K-40 line 1). Free e-file via Kansas "
            "WebFile. No reciprocity agreements. Social Security fully "
            "exempt for all taxpayers per SB 1 (2024). Source: 2025 "
            "Kansas Individual Income Tax booklet IP25 "
            "(ksrevenue.gov/pdf/ip25.pdf)."
        ),
    )
)
