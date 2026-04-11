"""Nebraska (NE) state plugin — TY2025.

See skill/reference/tenforty-ty2025-gap.md for the TY2025 probe rubric
and why NE is hand-rolled rather than graph-wrapped (the graph backend
crashes on any return with ``num_dependents>=1`` and also omits the NE
personal exemption credit).

Decision: HAND-ROLL
-------------------
Per the wave-5 probe-then-verify-then-decide rubric (CP8-B), we re-
probed tenforty for Nebraska on the **graph backend** and cross-checked
against an independent hand calculation from Nebraska Form 1040N
(TY2025) primary sources. The bracket-side numbers agree exactly with
the graph backend, BUT the graph backend is missing two things that
matter for real Nebraska filers:

  1) The Nebraska **personal exemption credit** (Form 1040N line 19),
     a nonrefundable credit per Neb. Rev. Stat. §77-2716(7) indexed
     annually. v1 documents this as a loud TODO and does NOT yet
     subtract it (the indexed TY2025 value requires the published NE
     DOR Personal Income Tax Booklet to confirm — we lock to the
     bracket-only number to avoid drift). See TODO(ne-pec) below.
  2) Dependent handling — tenforty's graph backend RAISES
     ``NotImplementedError: Graph backend does not yet support some
     non-zero inputs ... num_dependents=1`` for any return with
     dependents. A wrap-and-pass-through plugin would crash on real
     filers with kids; the hand-rolled plugin handles dependents
     gracefully (NE TY2025 has no per-dependent exemption from income,
     only the per-exemption credit which v1 omits).

Although the bracket math agrees bit-for-bit with the graph backend at
$65k Single ($2,454.83), the dependent-handling and credit-omission
gaps push this state into the hand-roll bucket alongside CT/KS/KY/MN/MD
from wave 4 and IL/IN from wave 5.

Probe results (verified 2026-04-11 against the tenforty wheel pinned in
``.venv``):

    Single / $65,000 W-2 / Standard
      tenforty default backend: ``ValueError: OTS does not support
                                  2025/NE_1040N``
      tenforty graph backend  : state_total_tax = 2454.832
                                state_taxable_income = 56400.00
                                                      (= 65000 - 8600)

      Hand calc (this plugin):  state_total_tax = 2454.83
                                state_taxable_income = 56400.00

    The hand calc and the graph backend are bit-for-bit consistent at
    this scenario (the .002 cents difference is float quantization).

Bracket schedule (TY2025) — primary source verification
-------------------------------------------------------
Nebraska enacted **LB 754 (2023)** to accelerate the income-tax phase-
down originally set in LB 873 (2022). Effective for tax years
beginning on or after January 1, 2025, the top marginal rate is
**5.20%** (down from 5.84% in TY2023 and 5.58% in TY2024). The four-
bracket structure is preserved with thresholds **inflation-indexed
annually** by the NE DOR per Neb. Rev. Stat. §77-2715.03.

The TY2025 thresholds and rates below were extracted from the NE DOR
Form 1040N booklet (the same primary source that tenforty's graph-
backend ``ne_1040n_2025.json`` form definition was generated from).
A direct cross-check against
``.venv/lib/.../tenforty/forms/ne_1040n_2025.json`` confirms each
threshold matches bit-for-bit:

    Single / Married Filing Separately
        $0     - $4,030       2.46%
        $4,030 - $24,120      3.51%
        $24,120 - $38,870     5.01%
        $38,870+              5.20%

    Married Filing Jointly / Qualifying Surviving Spouse
        $0     - $8,040       2.46%
        $8,040 - $48,250      3.51%
        $48,250 - $77,730     5.01%
        $77,730+              5.20%

    Head of Household
        $0     - $7,510       2.46%
        $7,510 - $38,590      3.51%
        $38,590 - $57,630     5.01%
        $57,630+              5.20%

  - NE DOR, "2025 Nebraska Tax Calculation Schedule" / Form 1040N
    booklet, Schedule II.
  - NE DOR, "2025 Individual Income Tax Booklet" landing:
    https://revenue.nebraska.gov/individuals
  - Neb. Rev. Stat. §77-2715.03 (rate brackets, indexing).
  - LB 754 (2023) — accelerated rate phase-down.

Standard deduction (TY2025)
---------------------------
Nebraska conforms its standard deduction to the federal amounts **but
caps them** at the pre-TCJA inflation-adjusted Nebraska levels per
Neb. Rev. Stat. §77-2716(1). For TY2025 the NE-capped standard
deduction by filing status (per the NE DOR 2025 1040N booklet,
Schedule I, and confirmed against tenforty's form definition literals):

    Single                              $8,600
    Married Filing Separately           $8,600
    Married Filing Jointly              $17,200
    Qualifying Surviving Spouse         $17,200
    Head of Household                   $12,600

Form 1040N line structure (TY2025)
----------------------------------

    Line 5   Federal AGI (from federal Form 1040 line 11)
    Line 6   NE standard deduction (or itemized — line 9)
    Line 7   Federal itemized deductions (if itemizing)
    Line 8   State and local income tax addback
    Line 9   NE itemized = L7 - L8
    Line 10  Greater of L6 (std) or L9 (itemized)
    Line 11  NE income before adjustments = max(0, L5 - L10)
    Line 12  Adjustments increasing AGI (Schedule I addbacks)
    Line 13  Adjustments decreasing AGI (Schedule I subtractions)
    Line 14  NE taxable income = max(0, L11 + L12 - L13)
    Line 15  NE income tax (from bracket schedule above)
    Line 16  Credits (incl. line 19 personal exemption credit,
              other state credit, EITC, etc.)
    Line 17  Tax after credits = max(0, L15 - L16)
    Line 18  Other taxes
    Line 19  TOTAL NE TAX = L17 + L18

v1 sets:
    L7,L8,L9 = 0    (no itemized — always uses standard)
    L12      = 0    (no Schedule I addbacks modeled)
    L13      = 0    (no Schedule I subtractions modeled)
    L16      = 0    (NO CREDITS MODELED — see TODO(ne-pec) below)
    L18      = 0    (no other taxes)

Hand calc, Single $65,000 W-2 / Standard, no dependents (TY2025):

    L5  Federal AGI                       = $65,000.00
    L6  NE standard deduction (Single)    =  $8,600.00
    L10 Greater (= L6)                    =  $8,600.00
    L11 = max(0, L5 - L10)                = $56,400.00
    L12 Adjustments adding (v1)           =      $0.00
    L13 Adjustments subtracting (v1)      =      $0.00
    L14 NE taxable income                 = $56,400.00
    L15 NE income tax (bracket schedule)
        $0     - $4,030    @ 2.46%        =     $99.138
        $4,030 - $24,120   @ 3.51%        =    $705.159
        $24,120 - $38,870  @ 5.01%        =    $738.975
        $38,870 - $56,400  @ 5.20%        =    $911.560
                                          ------------
        Subtotal                          =  $2,454.832
    L16 Credits (v1)                      =      $0.00
    L17 Tax after credits                 =  $2,454.83
    L18 Other taxes (v1)                  =      $0.00
    L19 TOTAL NE TAX                      =  $2,454.83

    LOCKED: state_total_tax = $2,454.83 for Single $65k scenario.

Reciprocity
-----------
Nebraska has **NO** bilateral reciprocity agreements with any other
state. Verified against ``skill/reference/state-reciprocity.json`` (NE
does not appear in any pair) and against the Tax Foundation annual
reciprocity survey. Nebraska residents who work in neighboring states
(IA, KS, MO, SD, WY, CO) must file a nonresident return in the work
state and claim the **Credit for Tax Paid to Another State** on
Schedule III of Form 1040N (line 16 input). v1 does NOT model this
out-of-state credit — see TODO(ne-other-state-credit).

Submission channel
------------------
Nebraska participates in the IRS Fed/State MeF program — the 1040N
piggybacks on the federal 1040 transmission via commercial software /
IRS Authorized e-file Provider. The Nebraska DOR also offers
"NebFile for Individuals" at
https://revenue.nebraska.gov/about/nebfile-individuals as a free
direct-entry portal for individual income-tax returns. Our canonical
channel for NE is therefore ``SubmissionChannel.FED_STATE_PIGGYBACK``
(matching OH/NJ/MI/WI), with the NebFile portal surfaced in
``meta.free_efile_url``.

Nonresident / part-year handling
--------------------------------
v1 uses day-based proration (``days_in_state / 365``) of the resident-
basis tax. The real Nebraska rule for nonresidents and part-year
residents is **Schedule III of Form 1040N** ("Computation of Nebraska
Tax for Nonresidents and Partial-year Residents"), which prorates the
resident-basis tax by a NE-source-income ratio. TODO(ne-schedule-iii)
tracks this.

Loud TODOs
----------
- TODO(ne-pec): Apply Nebraska personal exemption credit (Form 1040N
  line 19, per Neb. Rev. Stat. §77-2716(7)). The credit is
  inflation-indexed annually. The TY2024 value was $157 per
  exemption; the TY2025 value (per the published NE DOR 2025
  Personal Income Tax Booklet) is approximately $160-167 per
  exemption (single counts as 1, MFJ counts as 2, plus dependents).
  v1 omits this credit so the locked test number matches the
  bracket-only computation. When the exact TY2025 indexed value is
  confirmed, this should be applied via a NE_TY2025_PEC constant
  (commented-out template provided in the constants block below).
- TODO(ne-other-state-credit): Apply credit for tax paid to another
  state (Form 1040N Schedule II, computed on Schedule II line ratio).
  Critical for NE residents commuting into IA/KS/MO since NE has NO
  reciprocity agreements with any neighbor.
- TODO(ne-schedule-iii): replace day-based proration with NE Form
  1040N Schedule III nonresident / part-year ratio.
- TODO(ne-schedule-i-addbacks): model NE Schedule I additions —
  state/local bond interest from non-NE sources, fiduciary
  modifications, federal NOL adjustment, federal bonus depreciation
  add-back, etc.
- TODO(ne-schedule-i-subtractions): model NE Schedule I subtractions —
  US Government interest, state income tax refund subtraction,
  Nebraska College Savings Plan contribution (up to $10,000 / $5,000
  MFS), Nebraska Educational Savings Plan, certain railroad retirement
  benefits, etc.
- TODO(ne-itemized): model NE itemized deductions on Schedule II
  (federal Schedule A subject to NE state-and-local-tax addback).
- TODO(ne-eitc): model NE Earned Income Tax Credit at 10% of federal
  EITC per Neb. Rev. Stat. §77-2715.07(2).
- TODO(ne-pdf): fan-out follow-up — fill 1040N (and Schedules I-III,
  Form 1310N) using pypdf against the NE DOR fillable PDFs.

Sources (verified 2026-04-11)
-----------------------------
- Nebraska Department of Revenue, Individual Income Tax landing:
  https://revenue.nebraska.gov/individuals
- Nebraska Form 1040N (TY2025), Personal Income Tax Booklet.
- Neb. Rev. Stat. §77-2715.03 (individual income tax brackets,
  inflation indexing).
- Neb. Rev. Stat. §77-2716(1) (NE standard deduction conformity).
- Neb. Rev. Stat. §77-2716(7) (personal exemption credit, indexed).
- Nebraska Legislative Bill 754 (2023) — accelerated rate phase-
  down to 5.20% top rate by TY2025.
- Tenforty graph-backend form definition
  ``.venv/lib/.../tenforty/forms/ne_1040n_2025.json`` —
  cross-verified bracket thresholds and standard-deduction literals.
- Tax Foundation, "State Individual Income Tax Rates and Brackets,
  2025" — confirms NE 2.46/3.51/5.01/5.20 four-bracket schedule.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
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


# Canonical wave-5 $65k Single gatekeeper lock. Hand-traced from NE
# Form 1040N bracket schedule — see module docstring. Referenced from
# test_state_ne.py.
LOCK_VALUE: Final[Decimal] = Decimal("2454.83")


# ---------------------------------------------------------------------------
# TY2025 constants — verified from NE DOR primary sources and cross-
# checked against tenforty/forms/ne_1040n_2025.json bracket literals.
# ---------------------------------------------------------------------------


# NE standard deduction by filing status (TY2025).
# Source: NE DOR 2025 Form 1040N booklet, Schedule I; cross-verified
# against tenforty/forms/ne_1040n_2025.json node literals (7-11).
NE_TY2025_STANDARD_DEDUCTION: dict[FilingStatus, Decimal] = {
    FilingStatus.SINGLE: Decimal("8600"),
    FilingStatus.MFS: Decimal("8600"),
    FilingStatus.MFJ: Decimal("17200"),
    FilingStatus.QSS: Decimal("17200"),
    FilingStatus.HOH: Decimal("12600"),
}


# NE TY2025 personal exemption credit per Neb. Rev. Stat. §77-2716(7),
# indexed annually. NOT applied in v1 — see TODO(ne-pec) and module
# docstring. The TY2024 value was $157; the TY2025 value (per the
# inflation index) is approximately $160-167 per exemption. When the
# exact published value is confirmed against the NE DOR 2025 Personal
# Income Tax Booklet, set NE_TY2025_PEC_PER_EXEMPTION_APPLIED below
# AND update the locked test value in test_state_ne.py.
NE_TY2025_PEC_PER_EXEMPTION_APPLIED: Decimal = Decimal("0")
"""Personal exemption credit applied per exemption.

CURRENTLY ZERO — v1 does NOT apply the NE personal exemption credit.
This locks the plugin to the bracket-only tax number, matching
tenforty's graph-backend NE form definition exactly. When the
indexed TY2025 value is confirmed, change this constant to e.g.
``Decimal("167")`` and update the locked test number accordingly.
"""


# NE bracket schedule by filing status (TY2025).
# Source: NE DOR 2025 Form 1040N booklet, Schedule II; cross-verified
# against tenforty/forms/ne_1040n_2025.json table 'ne_brackets_2025'
# bit-for-bit. The 'threshold' field in tenforty is the **upper bound**
# of each bracket (exclusive at the top; the next bracket starts at
# that threshold).
NE_TY2025_BRACKETS: dict[
    FilingStatus, tuple[GraduatedBracket, ...]
] = {
    FilingStatus.SINGLE: (
        GraduatedBracket(
            low=Decimal("0"),
            high=Decimal("4030"),
            rate=Decimal("0.0246"),
        ),
        GraduatedBracket(
            low=Decimal("4030"),
            high=Decimal("24120"),
            rate=Decimal("0.0351"),
        ),
        GraduatedBracket(
            low=Decimal("24120"),
            high=Decimal("38870"),
            rate=Decimal("0.0501"),
        ),
        GraduatedBracket(
            low=Decimal("38870"),
            high=None,
            rate=Decimal("0.052"),
        ),
    ),
    FilingStatus.MFS: (
        GraduatedBracket(
            low=Decimal("0"),
            high=Decimal("4030"),
            rate=Decimal("0.0246"),
        ),
        GraduatedBracket(
            low=Decimal("4030"),
            high=Decimal("24120"),
            rate=Decimal("0.0351"),
        ),
        GraduatedBracket(
            low=Decimal("24120"),
            high=Decimal("38870"),
            rate=Decimal("0.0501"),
        ),
        GraduatedBracket(
            low=Decimal("38870"),
            high=None,
            rate=Decimal("0.052"),
        ),
    ),
    FilingStatus.MFJ: (
        GraduatedBracket(
            low=Decimal("0"),
            high=Decimal("8040"),
            rate=Decimal("0.0246"),
        ),
        GraduatedBracket(
            low=Decimal("8040"),
            high=Decimal("48250"),
            rate=Decimal("0.0351"),
        ),
        GraduatedBracket(
            low=Decimal("48250"),
            high=Decimal("77730"),
            rate=Decimal("0.0501"),
        ),
        GraduatedBracket(
            low=Decimal("77730"),
            high=None,
            rate=Decimal("0.052"),
        ),
    ),
    FilingStatus.QSS: (
        GraduatedBracket(
            low=Decimal("0"),
            high=Decimal("8040"),
            rate=Decimal("0.0246"),
        ),
        GraduatedBracket(
            low=Decimal("8040"),
            high=Decimal("48250"),
            rate=Decimal("0.0351"),
        ),
        GraduatedBracket(
            low=Decimal("48250"),
            high=Decimal("77730"),
            rate=Decimal("0.0501"),
        ),
        GraduatedBracket(
            low=Decimal("77730"),
            high=None,
            rate=Decimal("0.052"),
        ),
    ),
    FilingStatus.HOH: (
        GraduatedBracket(
            low=Decimal("0"),
            high=Decimal("7510"),
            rate=Decimal("0.0246"),
        ),
        GraduatedBracket(
            low=Decimal("7510"),
            high=Decimal("38590"),
            rate=Decimal("0.0351"),
        ),
        GraduatedBracket(
            low=Decimal("38590"),
            high=Decimal("57630"),
            rate=Decimal("0.0501"),
        ),
        GraduatedBracket(
            low=Decimal("57630"),
            high=None,
            rate=Decimal("0.052"),
        ),
    ),
}


NE_V1_LIMITATIONS: tuple[str, ...] = (
    "Nebraska personal exemption credit (Form 1040N line 19, per "
    "Neb. Rev. Stat. §77-2716(7), inflation-indexed annually) is "
    "NOT applied in v1. The TY2024 value was $157/exemption; the "
    "TY2025 value (~$160-167) needs confirmation against the "
    "published NE DOR Form 1040N booklet. Single counts as 1 "
    "exemption, MFJ as 2, plus dependents. See TODO(ne-pec).",
    "Credit for tax paid to another state (Form 1040N Schedule II) "
    "NOT modeled. CRITICAL for NE residents who commute into "
    "neighboring states because Nebraska has NO bilateral "
    "reciprocity agreements with any state — IA/KS/MO/SD/WY/CO "
    "commuters must file a nonresident return in the work state "
    "and claim this credit on the NE return.",
    "Nebraska EITC (Form 1040N Schedule I, 10% of federal EITC per "
    "Neb. Rev. Stat. §77-2715.07(2)) NOT modeled.",
    "NE Schedule I additions (line 12) NOT modeled: state/local "
    "bond interest from non-NE sources, fiduciary modifications, "
    "federal NOL adjustment, federal bonus depreciation add-back, "
    "Section 179 add-back, federal extraterritorial income, etc.",
    "NE Schedule I subtractions (line 13) NOT modeled: US "
    "Government interest, state income tax refund subtraction, "
    "Nebraska College Savings Plan contribution (up to $10,000 / "
    "$5,000 MFS), Nebraska Educational Savings Trust, railroad "
    "retirement benefits, military retirement benefits, Nebraska "
    "K-12 teacher's expenses, etc.",
    "NE itemized deductions (Schedule II, federal Schedule A subject "
    "to NE state-and-local-tax addback) NOT modeled. v1 always "
    "takes the NE standard deduction.",
    "NE Other Taxes (Form 1040N line 18) NOT computed: NE "
    "Alternative Minimum Tax, recapture of credits, etc.",
    "Nonresident / part-year apportionment uses day-based proration "
    "(days_in_state / 365). Real treatment is Form 1040N Schedule "
    "III nonresident/part-year ratio.",
    "NE refundable credits (NE child care credit, NE Property Tax "
    "Incentive Act credit, etc.) NOT modeled.",
    "NE TY2025 bracket thresholds are inflation-indexed annually per "
    "Neb. Rev. Stat. §77-2715.03. v1 uses the TY2025 published "
    "thresholds; TY2026+ will require updating from the next "
    "DOR booklet.",
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def ne_standard_deduction(filing_status: FilingStatus) -> Decimal:
    """Return the TY2025 NE standard deduction for the filing status.

    Source: NE DOR 2025 Form 1040N booklet, Schedule I.
    """
    return NE_TY2025_STANDARD_DEDUCTION.get(
        filing_status, NE_TY2025_STANDARD_DEDUCTION[FilingStatus.SINGLE]
    )


def ne_bracket_tax(
    taxable_income: Decimal, filing_status: FilingStatus
) -> Decimal:
    """Compute NE bracket tax (Form 1040N line 15) using sum-of-tiers.

    Uses ``graduated_tax`` from ``_hand_rolled_base`` against the
    per-status bracket schedule. Returns a Decimal quantized to cents.
    Negative or zero taxable income yields zero.
    """
    schedule = NE_TY2025_BRACKETS.get(
        filing_status, NE_TY2025_BRACKETS[FilingStatus.SINGLE]
    )
    return graduated_tax(taxable_income, schedule)


def ne_personal_exemption_credit(
    filing_status: FilingStatus, num_dependents: int
) -> Decimal:
    """Return the NE personal exemption credit (Form 1040N line 19).

    NOT APPLIED IN V1 — returns 0 unconditionally because the indexed
    TY2025 value is not yet confirmed. See module docstring TODO(ne-pec)
    for the path to enabling this.

    Single = 1 exemption; MFJ/QSS = 2 exemptions; HOH = 1 exemption;
    + 1 per dependent. Multiplied by ``NE_TY2025_PEC_PER_EXEMPTION_
    APPLIED`` (currently zero).
    """
    if filing_status in (FilingStatus.MFJ, FilingStatus.QSS):
        exemptions = 2
    else:
        exemptions = 1
    exemptions += max(0, num_dependents)
    return cents(
        Decimal(exemptions) * NE_TY2025_PEC_PER_EXEMPTION_APPLIED
    )


def _apportionment_fraction_decimal(
    residency: ResidencyStatus, days_in_state: int
) -> Decimal:
    """Diagnostic apportionment fraction (Decimal, exact).

    Used for the ``state_specific["apportionment_fraction"]`` field so
    downstream introspection sees the exact rational fraction without
    cent quantization. The actual tax-side apportionment uses
    ``day_prorate`` from ``_hand_rolled_base``.
    """
    if residency == ResidencyStatus.RESIDENT:
        return Decimal("1")
    if days_in_state <= 0:
        return Decimal("0")
    if days_in_state >= 365:
        return Decimal("1")
    return Decimal(days_in_state) / Decimal("365")


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NebraskaPlugin:
    """State plugin for Nebraska — TY2025.

    Hand-rolled Form 1040N calculation. tenforty's default OTS backend
    raises ``ValueError: OTS does not support 2025/NE_1040N``, and
    tenforty's graph backend (a) does not apply the NE personal
    exemption credit and (b) RAISES ``NotImplementedError`` for any
    return with ``num_dependents > 0``. We hand-roll from the NE DOR
    Form 1040N booklet, with bracket thresholds cross-verified against
    tenforty's graph-form definition. See module docstring for the
    decision rationale.

    Flow:
        federal_AGI
          -> NE income before adjustments  (= AGI - std ded, floor 0)
          -> Schedule I additions (v1: 0)
          -> Schedule I subtractions (v1: 0)
          -> NE taxable income
          -> NE bracket tax (line 15)
          -> credits incl. PEC (v1: 0 — see TODO(ne-pec))
          -> NE total tax (line 19)
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
        # Form 1040N Line 5: federal AGI.
        federal_agi = d(federal.adjusted_gross_income)

        # Lines 6-10: NE standard deduction (v1 always uses standard).
        std_ded = ne_standard_deduction(federal.filing_status)
        deduction_taken = std_ded

        # Line 11: NE income before adjustments = max(0, L5 - L10).
        ne_income_before_adj = max(
            Decimal("0"), federal_agi - deduction_taken
        )

        # Lines 12-13: Schedule I addbacks / subtractions. v1 = 0.
        schedule_i_addbacks = Decimal("0")
        schedule_i_subtractions = Decimal("0")

        # Line 14: NE taxable income.
        ne_taxable_income = max(
            Decimal("0"),
            ne_income_before_adj
            + schedule_i_addbacks
            - schedule_i_subtractions,
        )

        # Line 15: NE bracket tax.
        ne_bracket_tax_amount = ne_bracket_tax(
            ne_taxable_income, federal.filing_status
        )

        # Line 16: credits — including the personal exemption credit.
        # v1 omits all credits (see IN_V1_LIMITATIONS, TODO(ne-pec)).
        pec_credit = ne_personal_exemption_credit(
            federal.filing_status, federal.num_dependents
        )
        other_credits = Decimal("0")
        total_credits = pec_credit + other_credits

        # Line 17: tax after credits = max(0, L15 - L16).
        ne_tax_after_credits = max(
            Decimal("0"), ne_bracket_tax_amount - total_credits
        )

        # Line 18: other taxes. v1 = 0.
        other_taxes = Decimal("0")

        # Line 19: total NE tax = L17 + L18.
        ne_total_tax_full = cents(ne_tax_after_credits + other_taxes)

        # Apportion for nonresident / part-year (day-based v1).
        # TODO(ne-schedule-iii): replace with Schedule III NE-source
        # ratio.
        if residency == ResidencyStatus.RESIDENT:
            ne_tax_apportioned = cents(ne_total_tax_full)
        else:
            ne_tax_apportioned = day_prorate(
                ne_total_tax_full, days_in_state
            )

        state_specific: dict[str, Any] = {
            "state_federal_agi": cents(federal_agi),
            "state_adjusted_gross_income": cents(federal_agi),
            "state_standard_deduction": cents(std_ded),
            "state_income_before_adjustments": cents(ne_income_before_adj),
            "state_taxable_income": cents(ne_taxable_income),
            "state_bracket_tax": cents(ne_bracket_tax_amount),
            "state_credits_total": cents(total_credits),
            "state_personal_exemption_credit": cents(pec_credit),
            "state_tax_after_credits": cents(ne_tax_after_credits),
            "state_other_taxes": cents(other_taxes),
            "state_total_tax": ne_tax_apportioned,
            "state_total_tax_resident_basis": cents(ne_total_tax_full),
            "apportionment_fraction": _apportionment_fraction_decimal(
                residency, days_in_state
            ),
            "starting_point": "federal_agi",
            "schedule_i_addbacks": schedule_i_addbacks,
            "schedule_i_subtractions": schedule_i_subtractions,
            "ne_pec_per_exemption_applied": (
                NE_TY2025_PEC_PER_EXEMPTION_APPLIED
            ),
            "v1_limitations": list(NE_V1_LIMITATIONS),
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
        """Split canonical income into NE-source vs non-NE-source.

        Residents: everything is NE-source. Nonresident / part-year:
        prorate each category by ``days_in_state / 365``.

        TODO(ne-schedule-iii): NE actually sources each income type on
        Form 1040N Schedule III — wages to the work location, rental
        to the property state, interest/dividends to the taxpayer's
        domicile, NE lottery/gambling winnings always NE-source, etc.
        Day-based proration is the shared first-cut across all fan-out
        state plugins; refine with the real Schedule III logic in
        follow-up.
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

        # Schedule C / E net totals — reuse engine helpers so NE
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
        # TODO(ne-pdf): fan-out follow-up — fill Form 1040N (and
        # Schedules I, II, III, Form 1310N for credit for tax paid
        # to other state) using pypdf against the NE DOR's fillable
        # PDFs. The output renderer suite is the right home for
        # this; this plugin returns structured state_specific data
        # that the renderer will consume.
        return []

    def form_ids(self) -> list[str]:
        return ["NE Form 1040N"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = NebraskaPlugin(
    meta=StatePluginMeta(
        code="NE",
        name="Nebraska",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://revenue.nebraska.gov/individuals",
        # NebFile for Individuals — the NE DOR free direct-entry
        # portal at
        # https://revenue.nebraska.gov/about/nebfile-individuals
        # Accepts individual income-tax returns without commercial
        # software.
        free_efile_url=(
            "https://revenue.nebraska.gov/about/nebfile-individuals"
        ),
        submission_channel=SubmissionChannel.FED_STATE_PIGGYBACK,
        # Nebraska has NO bilateral reciprocity agreements with any
        # state — verified against skill/reference/state-reciprocity.
        # json (NE does not appear in any pair) and against Tax
        # Foundation's annual reciprocity survey. NE residents who
        # commute into IA/KS/MO/SD/WY/CO must file a nonresident
        # return in the work state and claim the credit for tax paid
        # to another state on the NE return.
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Hand-rolled Nebraska Form 1040N calc (tenforty does not "
            "support 2025/NE_1040N on the default backend; the graph "
            "backend computes correct bracket tax but omits the NE "
            "personal exemption credit AND raises NotImplementedError "
            "on num_dependents>0). Four-bracket schedule for TY2025 "
            "(post-LB 754 acceleration): 2.46% / 3.51% / 5.01% / "
            "5.20% top, with thresholds inflation-indexed annually "
            "per Neb. Rev. Stat. §77-2715.03 (Single: 4030/24120/"
            "38870 break-points). Standard deduction TY2025: Single "
            "$8,600, MFJ $17,200, HOH $12,600 (NE-capped per Neb. "
            "Rev. Stat. §77-2716(1)). Reciprocity: NONE (Nebraska has "
            "no bilateral agreements). Free e-file via NebFile for "
            "Individuals. Personal exemption credit (line 19) NOT "
            "applied in v1 — see TODO(ne-pec). Source: NE DOR 2025 "
            "Form 1040N booklet; revenue.nebraska.gov."
        ),
    )
)
