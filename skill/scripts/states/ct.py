"""Connecticut (CT) state plugin — TY2025.

See skill/reference/tenforty-ty2025-gap.md for the TY2025 probe rubric
and why CT is hand-rolled instead of graph-wrapped (the OTSState enum
lists CT but OTS_FORM_CONFIG has zero CT entries for any year).

Connecticut is listed in tenforty's ``OTSState`` enum (``OTSState.CT ->
CT_1``) but it is **NOT** actually wired into OpenTaxSolver's form catalog
for any year — calling ``tenforty.evaluate_return(..., state='CT')`` raises
``ValueError: OTS does not support 2025/CT_1`` (verified 2026-04-11 against
tenforty 2025.8). The fan-out task spec claimed tenforty supports CT; that
claim is wrong — the enum-to-form mapping exists but ``OTS_FORM_CONFIG``
has zero entries under ``CT_1`` for any year. This plugin therefore
**hand-rolls** the CT-1040 tax calc from the official Form CT-1040 TCS
(Rev. 12/25) Tax Calculation Schedule. See the ``LOUD TODO`` block below.

Reference: Connecticut Department of Revenue Services (DRS),
``Form CT-1040 TCS — 2025 Tax Calculation Schedule`` (Rev. 12/25), retrieved
2026-04-11 from:

- https://portal.ct.gov/-/media/drs/forms/2025/income/2025-ct-1040-instructions_1225.pdf
- https://taxsim.nber.org/historical_state_tax_forms/CT/2025/CT-1040%20TCS_1225.pdf

The TCS is the authoritative, continuous formula CT uses once CT AGI
exceeds $102,000; for AGI ≤ $102,000 CT publishes the 2025 Income Tax
Tables (also referenced in the plugin's test suite as a cross-check for
the $65k Single scenario). Both artifacts ultimately compute the same
figure via a 6-step schedule:

    1. CT AGI                           (Line 1)
    2. Personal Exemption (Table A)     (Line 2)
    3. CT Taxable Income = Line 1 - 2   (Line 3)
    4. Initial Tax (Table B)            (Line 4)
    5. 2% Phase-Out Add-Back (Table C)  (Line 5)
    6. Tax Recapture (Table D)          (Line 6)
    7. Sum = Line 4 + 5 + 6             (Line 7)
    8. Personal Tax Credit (Table E)    (Line 8, decimal)
    9. Credit = Line 7 * Line 8         (Line 9)
    10. CT Tax = Line 7 - Line 9        (Line 10)

Each of Tables A, C, D, E is a piecewise-constant step function keyed on
CT AGI; Table B is piecewise-linear keyed on CT Taxable Income. They are
transcribed verbatim from CT-1040 TCS below.

Rate structure (Table B):

- Single / MFS: 2% / 4.5% / 5.5% / 6% / 6.5% / 6.9% / 6.99% with
  breakpoints $10k / $50k / $100k / $200k / $250k / $500k.
- MFJ / QSS: same rates with breakpoints doubled to $20k / $100k / $200k
  / $400k / $500k / $1M.
- HOH: $16k / $80k / $160k / $320k / $400k / $800k.

The 2% and 4.5% rates (the two lowest brackets) were **reduced** in
TY2024 from 3% and 5% respectively under Public Act 23-204, Section 1 —
the largest income tax cut in CT history. TY2025 preserves those
reductions; no further rate changes landed in the 2025 legislative
session (verified against DRS 2025 income tax tables).

**TY2025 Single $65k resident reference scenario** (computed directly
from TCS, locked in a test below):

    Line 1: CT AGI                    = $65,000
    Line 2: Personal Exemption        = $0      (Single, AGI > $44,000)
    Line 3: CT Taxable Income         = $65,000
    Line 4: Initial Tax (Table B)     = $2,825  (= $2,000 + 5.5% * $15,000)
    Line 5: 2% Add-Back (Table C)     = $50     (Single, $61,500 < AGI <= $66,500)
    Line 6: Tax Recapture (Table D)   = $0      (AGI <= $105,000)
    Line 7: Line 4 + 5 + 6            = $2,875
    Line 8: Personal Credit (Table E) = 0.00    (Single, AGI > $64,500)
    Line 9: Line 7 * Line 8           = $0
    Line 10: CT Income Tax            = **$2,875.00**

Reciprocity: CT has **no** bilateral income tax reciprocity agreements
with any other state (verified Tax Foundation 2024; verified against
``skill/reference/state-reciprocity.json`` — CT is not present in the
``agreements`` array). ``reciprocity_partners`` is therefore the empty
tuple.

Submission channel: CT operates ``myconneCT`` as its free direct-file
portal (https://portal.ct.gov/drs-myconneCT). CT also participates in
the IRS Fed/State MeF piggyback program, but the canonical free path
for individual taxpayers is the state's own myconneCT portal, so this
plugin reports ``SubmissionChannel.STATE_DOR_FREE_PORTAL``.

Starting point: CT-1040 Line 1 is federal AGI with CT additions (Line 31)
and CT subtractions (Line 41-52) applied on top — a ``FEDERAL_AGI``
starting point per the Plugin API. None of the CT-specific Schedule 1
additions/subtractions are modeled in v1; they are enumerated in
``CT_V1_LIMITATIONS`` so downstream consumers can surface a warning.

Nonresident / part-year handling: Form CT-1040NR/PY computes CT tax on
the resident basis and then multiplies by a CT-source-income fraction
(``CT-source AGI / CT AGI``). v1 approximates this with day-based
proration, consistent with the rest of the fan-out state plugins; the
proper CT-1040NR/PY sourcing ratio is fan-out follow-up.

LOUD TODO — tenforty support for CT is a stub. If OpenTaxSolver upstream
ever adds a real ``CT_1`` form, this plugin should be rewritten as a
thin tenforty wrapper (mirror ``nc.py`` / ``oh.py``) and the hand-rolled
bracket math retired. Until then, this is the source of truth for CT.
Any CT rate change must be applied by editing the TCS constants below
(Tables A-E) AND by updating the locked $65k single-resident test value
to match.
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


_CENTS = Decimal("0.01")


def _cents(v: Decimal) -> Decimal:
    """Quantize a Decimal to cents with half-up rounding."""
    return v.quantize(_CENTS, rounding=ROUND_HALF_UP)


# Canonical wave-4 $65k Single gatekeeper lock. Hand-traced from CT
# Form CT-1040 TCS — see module docstring. Referenced from
# test_state_ct.py.
LOCK_VALUE: Final[Decimal] = Decimal("2875.00")


# ---------------------------------------------------------------------------
# Apportionment helper (shared first-cut across fan-out state plugins)
# ---------------------------------------------------------------------------


def _apportionment_fraction(
    residency: ResidencyStatus, days_in_state: int
) -> Decimal:
    """Days-based apportionment fraction for nonresident / part-year.

    Residents get 1.0 (full state tax). Nonresidents and part-year residents
    are prorated by ``days_in_state / 365``. Clamped to [0, 1].

    TODO(ct-1040nrpy): replace with the real CT-1040NR/PY
    CT-source-income ratio (Schedule CT-SI) in fan-out. Day-based
    proration is the shared first-cut across all fan-out state plugins.
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
# Form CT-1040 TCS (Rev. 12/25) — verbatim transcription of Tables A-E
# ---------------------------------------------------------------------------
#
# All amounts below are copy-pasted from the official CT-1040 TCS
# (Rev. 12/25), retrieved 2026-04-11 from
# https://portal.ct.gov/-/media/drs/forms/2025/income/2025-ct-1040-instructions_1225.pdf
# Tables A, C, D, E use the TCS rule "More Than X, Less Than or Equal To Y"
# — i.e. the interval is (X, Y]. Table B is piecewise-linear on CT Taxable
# Income with the same (X, Y] convention.


# Table A — Personal Exemption (stepped $1,000 phase-out).
# Each entry: (more_than_agi, less_than_or_equal_to_agi, exemption).
# The last "and up" row is encoded as an upper bound of None.

_TABLE_A_SINGLE: tuple[tuple[Decimal, Decimal | None, Decimal], ...] = (
    (Decimal("0"),       Decimal("30000"),  Decimal("15000")),
    (Decimal("30000"),   Decimal("31000"),  Decimal("14000")),
    (Decimal("31000"),   Decimal("32000"),  Decimal("13000")),
    (Decimal("32000"),   Decimal("33000"),  Decimal("12000")),
    (Decimal("33000"),   Decimal("34000"),  Decimal("11000")),
    (Decimal("34000"),   Decimal("35000"),  Decimal("10000")),
    (Decimal("35000"),   Decimal("36000"),  Decimal("9000")),
    (Decimal("36000"),   Decimal("37000"),  Decimal("8000")),
    (Decimal("37000"),   Decimal("38000"),  Decimal("7000")),
    (Decimal("38000"),   Decimal("39000"),  Decimal("6000")),
    (Decimal("39000"),   Decimal("40000"),  Decimal("5000")),
    (Decimal("40000"),   Decimal("41000"),  Decimal("4000")),
    (Decimal("41000"),   Decimal("42000"),  Decimal("3000")),
    (Decimal("42000"),   Decimal("43000"),  Decimal("2000")),
    (Decimal("43000"),   Decimal("44000"),  Decimal("1000")),
    (Decimal("44000"),   None,              Decimal("0")),
)

_TABLE_A_MFJ: tuple[tuple[Decimal, Decimal | None, Decimal], ...] = (
    (Decimal("0"),       Decimal("48000"),  Decimal("24000")),
    (Decimal("48000"),   Decimal("49000"),  Decimal("23000")),
    (Decimal("49000"),   Decimal("50000"),  Decimal("22000")),
    (Decimal("50000"),   Decimal("51000"),  Decimal("21000")),
    (Decimal("51000"),   Decimal("52000"),  Decimal("20000")),
    (Decimal("52000"),   Decimal("53000"),  Decimal("19000")),
    (Decimal("53000"),   Decimal("54000"),  Decimal("18000")),
    (Decimal("54000"),   Decimal("55000"),  Decimal("17000")),
    (Decimal("55000"),   Decimal("56000"),  Decimal("16000")),
    (Decimal("56000"),   Decimal("57000"),  Decimal("15000")),
    (Decimal("57000"),   Decimal("58000"),  Decimal("14000")),
    (Decimal("58000"),   Decimal("59000"),  Decimal("13000")),
    (Decimal("59000"),   Decimal("60000"),  Decimal("12000")),
    (Decimal("60000"),   Decimal("61000"),  Decimal("11000")),
    (Decimal("61000"),   Decimal("62000"),  Decimal("10000")),
    (Decimal("62000"),   Decimal("63000"),  Decimal("9000")),
    (Decimal("63000"),   Decimal("64000"),  Decimal("8000")),
    (Decimal("64000"),   Decimal("65000"),  Decimal("7000")),
    (Decimal("65000"),   Decimal("66000"),  Decimal("6000")),
    (Decimal("66000"),   Decimal("67000"),  Decimal("5000")),
    (Decimal("67000"),   Decimal("68000"),  Decimal("4000")),
    (Decimal("68000"),   Decimal("69000"),  Decimal("3000")),
    (Decimal("69000"),   Decimal("70000"),  Decimal("2000")),
    (Decimal("70000"),   Decimal("71000"),  Decimal("1000")),
    (Decimal("71000"),   None,              Decimal("0")),
)

_TABLE_A_MFS: tuple[tuple[Decimal, Decimal | None, Decimal], ...] = (
    (Decimal("0"),       Decimal("24000"),  Decimal("12000")),
    (Decimal("24000"),   Decimal("25000"),  Decimal("11000")),
    (Decimal("25000"),   Decimal("26000"),  Decimal("10000")),
    (Decimal("26000"),   Decimal("27000"),  Decimal("9000")),
    (Decimal("27000"),   Decimal("28000"),  Decimal("8000")),
    (Decimal("28000"),   Decimal("29000"),  Decimal("7000")),
    (Decimal("29000"),   Decimal("30000"),  Decimal("6000")),
    (Decimal("30000"),   Decimal("31000"),  Decimal("5000")),
    (Decimal("31000"),   Decimal("32000"),  Decimal("4000")),
    (Decimal("32000"),   Decimal("33000"),  Decimal("3000")),
    (Decimal("33000"),   Decimal("34000"),  Decimal("2000")),
    (Decimal("34000"),   Decimal("35000"),  Decimal("1000")),
    (Decimal("35000"),   None,              Decimal("0")),
)

_TABLE_A_HOH: tuple[tuple[Decimal, Decimal | None, Decimal], ...] = (
    (Decimal("0"),       Decimal("38000"),  Decimal("19000")),
    (Decimal("38000"),   Decimal("39000"),  Decimal("18000")),
    (Decimal("39000"),   Decimal("40000"),  Decimal("17000")),
    (Decimal("40000"),   Decimal("41000"),  Decimal("16000")),
    (Decimal("41000"),   Decimal("42000"),  Decimal("15000")),
    (Decimal("42000"),   Decimal("43000"),  Decimal("14000")),
    (Decimal("43000"),   Decimal("44000"),  Decimal("13000")),
    (Decimal("44000"),   Decimal("45000"),  Decimal("12000")),
    (Decimal("45000"),   Decimal("46000"),  Decimal("11000")),
    (Decimal("46000"),   Decimal("47000"),  Decimal("10000")),
    (Decimal("47000"),   Decimal("48000"),  Decimal("9000")),
    (Decimal("48000"),   Decimal("49000"),  Decimal("8000")),
    (Decimal("49000"),   Decimal("50000"),  Decimal("7000")),
    (Decimal("50000"),   Decimal("51000"),  Decimal("6000")),
    (Decimal("51000"),   Decimal("52000"),  Decimal("5000")),
    (Decimal("52000"),   Decimal("53000"),  Decimal("4000")),
    (Decimal("53000"),   Decimal("54000"),  Decimal("3000")),
    (Decimal("54000"),   Decimal("55000"),  Decimal("2000")),
    (Decimal("55000"),   Decimal("56000"),  Decimal("1000")),
    (Decimal("56000"),   None,              Decimal("0")),
)


def _lookup_step(
    table: tuple[tuple[Decimal, Decimal | None, Decimal], ...],
    agi: Decimal,
) -> Decimal:
    """Piecewise-constant TCS step-function lookup.

    The TCS phrasing is always "More Than X, Less Than or Equal To Y",
    i.e. the half-open interval ``(X, Y]``. The first interval uses the
    literal ``$0`` lower bound and is treated as ``[0, Y]`` (the TCS
    tables always start at exactly $0 with ``More Than $0``, and a true
    zero AGI still fetches the first-row value — CT does not offset by
    a penny).
    """
    if agi < 0:
        agi = Decimal("0")
    for more_than, less_than_or_equal, value in table:
        if less_than_or_equal is None:
            # "and up" row
            if agi > more_than:
                return value
            # Should have matched an earlier row, but be safe.
            return value
        # First row starts at $0 and includes exactly $0.
        if more_than == Decimal("0"):
            if agi <= less_than_or_equal:
                return value
            continue
        if more_than < agi <= less_than_or_equal:
            return value
    # Unreachable if the table is well-formed. Raise loudly.
    raise ValueError(f"CT TCS table lookup fell off the end for AGI={agi}")


def _table_a_exemption(filing_status: FilingStatus, agi: Decimal) -> Decimal:
    """Table A: Personal Exemption for 2025."""
    if filing_status == FilingStatus.SINGLE:
        return _lookup_step(_TABLE_A_SINGLE, agi)
    if filing_status in (FilingStatus.MFJ, FilingStatus.QSS):
        return _lookup_step(_TABLE_A_MFJ, agi)
    if filing_status == FilingStatus.MFS:
        return _lookup_step(_TABLE_A_MFS, agi)
    if filing_status == FilingStatus.HOH:
        return _lookup_step(_TABLE_A_HOH, agi)
    raise ValueError(f"Unknown filing status: {filing_status}")


# Table B — Initial Tax Calculation (piecewise-linear on CT Taxable Income).
# Each entry: (upper_bound_or_None, base_tax, bracket_rate, bracket_start).
# Reading rule: for the first row where ``ti <= upper_bound`` (or the
# "and up" row with upper_bound=None), tax = base_tax + rate * (ti - bracket_start).

_TABLE_B_SINGLE_OR_MFS: tuple[tuple[Decimal | None, Decimal, Decimal, Decimal], ...] = (
    (Decimal("10000"),  Decimal("0"),     Decimal("0.02"),  Decimal("0")),
    (Decimal("50000"),  Decimal("200"),   Decimal("0.045"), Decimal("10000")),
    (Decimal("100000"), Decimal("2000"),  Decimal("0.055"), Decimal("50000")),
    (Decimal("200000"), Decimal("4750"),  Decimal("0.06"),  Decimal("100000")),
    (Decimal("250000"), Decimal("10750"), Decimal("0.065"), Decimal("200000")),
    (Decimal("500000"), Decimal("14000"), Decimal("0.069"), Decimal("250000")),
    (None,              Decimal("31250"), Decimal("0.0699"), Decimal("500000")),
)

_TABLE_B_MFJ_OR_QSS: tuple[tuple[Decimal | None, Decimal, Decimal, Decimal], ...] = (
    (Decimal("20000"),    Decimal("0"),     Decimal("0.02"),  Decimal("0")),
    (Decimal("100000"),   Decimal("400"),   Decimal("0.045"), Decimal("20000")),
    (Decimal("200000"),   Decimal("4000"),  Decimal("0.055"), Decimal("100000")),
    (Decimal("400000"),   Decimal("9500"),  Decimal("0.06"),  Decimal("200000")),
    (Decimal("500000"),   Decimal("21500"), Decimal("0.065"), Decimal("400000")),
    (Decimal("1000000"),  Decimal("28000"), Decimal("0.069"), Decimal("500000")),
    (None,                Decimal("62500"), Decimal("0.0699"), Decimal("1000000")),
)

_TABLE_B_HOH: tuple[tuple[Decimal | None, Decimal, Decimal, Decimal], ...] = (
    (Decimal("16000"),  Decimal("0"),     Decimal("0.02"),  Decimal("0")),
    (Decimal("80000"),  Decimal("320"),   Decimal("0.045"), Decimal("16000")),
    (Decimal("160000"), Decimal("3200"),  Decimal("0.055"), Decimal("80000")),
    (Decimal("320000"), Decimal("7600"),  Decimal("0.06"),  Decimal("160000")),
    (Decimal("400000"), Decimal("17200"), Decimal("0.065"), Decimal("320000")),
    (Decimal("800000"), Decimal("22400"), Decimal("0.069"), Decimal("400000")),
    (None,              Decimal("50000"), Decimal("0.0699"), Decimal("800000")),
)


def _table_b_initial_tax(filing_status: FilingStatus, ti: Decimal) -> Decimal:
    """Table B: Initial Tax Calculation (piecewise-linear on CT TI).

    The DRS-printed TCS examples (Single $525k → $32,998; MFJ $22.5k →
    $513; HOH $825k → $51,748) demonstrate that the intermediate
    multiplication ``(TI - bracket_start) * rate`` is rounded to whole
    dollars (half-up) BEFORE being added to the base amount. That
    matches CT's whole-dollar filing convention on Form CT-1040 itself.
    We apply that rounding here so the plugin's output matches the
    TCS example values bit-for-bit.
    """
    if ti <= 0:
        return Decimal("0")
    if filing_status in (FilingStatus.SINGLE, FilingStatus.MFS):
        table = _TABLE_B_SINGLE_OR_MFS
    elif filing_status in (FilingStatus.MFJ, FilingStatus.QSS):
        table = _TABLE_B_MFJ_OR_QSS
    elif filing_status == FilingStatus.HOH:
        table = _TABLE_B_HOH
    else:
        raise ValueError(f"Unknown filing status: {filing_status}")
    for upper, base, rate, start in table:
        if upper is None or ti <= upper:
            # DRS TCS examples round the bracket product to whole dollars
            # (half-up) before adding base. See module docstring.
            excess = ti - start
            bracket_tax = (excess * rate).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
            return base + bracket_tax
    raise ValueError(f"CT Table B lookup fell off end for TI={ti}")


# Table C — 2% Tax Rate Phase-Out Add-Back.
# Each entry: (more_than_agi, less_than_or_equal_to_agi_or_None, add_back).

_TABLE_C_SINGLE: tuple[tuple[Decimal, Decimal | None, Decimal], ...] = (
    (Decimal("0"),       Decimal("56500"),  Decimal("0")),
    (Decimal("56500"),   Decimal("61500"),  Decimal("25")),
    (Decimal("61500"),   Decimal("66500"),  Decimal("50")),
    (Decimal("66500"),   Decimal("71500"),  Decimal("75")),
    (Decimal("71500"),   Decimal("76500"),  Decimal("100")),
    (Decimal("76500"),   Decimal("81500"),  Decimal("125")),
    (Decimal("81500"),   Decimal("86500"),  Decimal("150")),
    (Decimal("86500"),   Decimal("91500"),  Decimal("175")),
    (Decimal("91500"),   Decimal("96500"),  Decimal("200")),
    (Decimal("96500"),   Decimal("101500"), Decimal("225")),
    (Decimal("101500"),  None,              Decimal("250")),
)

_TABLE_C_MFJ: tuple[tuple[Decimal, Decimal | None, Decimal], ...] = (
    (Decimal("0"),       Decimal("100500"), Decimal("0")),
    (Decimal("100500"),  Decimal("105500"), Decimal("50")),
    (Decimal("105500"),  Decimal("110500"), Decimal("100")),
    (Decimal("110500"),  Decimal("115500"), Decimal("150")),
    (Decimal("115500"),  Decimal("120500"), Decimal("200")),
    (Decimal("120500"),  Decimal("125500"), Decimal("250")),
    (Decimal("125500"),  Decimal("130500"), Decimal("300")),
    (Decimal("130500"),  Decimal("135500"), Decimal("350")),
    (Decimal("135500"),  Decimal("140500"), Decimal("400")),
    (Decimal("140500"),  Decimal("145500"), Decimal("450")),
    (Decimal("145500"),  None,              Decimal("500")),
)

_TABLE_C_MFS: tuple[tuple[Decimal, Decimal | None, Decimal], ...] = (
    (Decimal("0"),       Decimal("50250"),  Decimal("0")),
    (Decimal("50250"),   Decimal("52750"),  Decimal("25")),
    (Decimal("52750"),   Decimal("55250"),  Decimal("50")),
    (Decimal("55250"),   Decimal("57750"),  Decimal("75")),
    (Decimal("57750"),   Decimal("60250"),  Decimal("100")),
    (Decimal("60250"),   Decimal("62750"),  Decimal("125")),
    (Decimal("62750"),   Decimal("65250"),  Decimal("150")),
    (Decimal("65250"),   Decimal("67750"),  Decimal("175")),
    (Decimal("67750"),   Decimal("70250"),  Decimal("200")),
    (Decimal("70250"),   Decimal("72750"),  Decimal("225")),
    (Decimal("72750"),   None,              Decimal("250")),
)

_TABLE_C_HOH: tuple[tuple[Decimal, Decimal | None, Decimal], ...] = (
    (Decimal("0"),       Decimal("78500"),  Decimal("0")),
    (Decimal("78500"),   Decimal("82500"),  Decimal("40")),
    (Decimal("82500"),   Decimal("86500"),  Decimal("80")),
    (Decimal("86500"),   Decimal("90500"),  Decimal("120")),
    (Decimal("90500"),   Decimal("94500"),  Decimal("160")),
    (Decimal("94500"),   Decimal("98500"),  Decimal("200")),
    (Decimal("98500"),   Decimal("102500"), Decimal("240")),
    (Decimal("102500"),  Decimal("106500"), Decimal("280")),
    (Decimal("106500"),  Decimal("110500"), Decimal("320")),
    (Decimal("110500"),  Decimal("114500"), Decimal("360")),
    (Decimal("114500"),  None,              Decimal("400")),
)


def _table_c_phaseout_addback(
    filing_status: FilingStatus, agi: Decimal
) -> Decimal:
    """Table C: 2% Tax Rate Phase-Out Add-Back."""
    if filing_status == FilingStatus.SINGLE:
        return _lookup_step(_TABLE_C_SINGLE, agi)
    if filing_status in (FilingStatus.MFJ, FilingStatus.QSS):
        return _lookup_step(_TABLE_C_MFJ, agi)
    if filing_status == FilingStatus.MFS:
        return _lookup_step(_TABLE_C_MFS, agi)
    if filing_status == FilingStatus.HOH:
        return _lookup_step(_TABLE_C_HOH, agi)
    raise ValueError(f"Unknown filing status: {filing_status}")


# Table D — Tax Recapture.
# Each entry: (more_than_agi, less_than_or_equal_to_agi_or_None, recapture).
# Single/MFS share one table; MFJ/QSS share another; HOH is distinct.

_TABLE_D_SINGLE_OR_MFS: tuple[tuple[Decimal, Decimal | None, Decimal], ...] = (
    (Decimal("0"),       Decimal("105000"), Decimal("0")),
    (Decimal("105000"),  Decimal("110000"), Decimal("25")),
    (Decimal("110000"),  Decimal("115000"), Decimal("50")),
    (Decimal("115000"),  Decimal("120000"), Decimal("75")),
    (Decimal("120000"),  Decimal("125000"), Decimal("100")),
    (Decimal("125000"),  Decimal("130000"), Decimal("125")),
    (Decimal("130000"),  Decimal("135000"), Decimal("150")),
    (Decimal("135000"),  Decimal("140000"), Decimal("175")),
    (Decimal("140000"),  Decimal("145000"), Decimal("200")),
    (Decimal("145000"),  Decimal("150000"), Decimal("225")),
    (Decimal("150000"),  Decimal("200000"), Decimal("250")),
    (Decimal("200000"),  Decimal("205000"), Decimal("340")),
    (Decimal("205000"),  Decimal("210000"), Decimal("430")),
    (Decimal("210000"),  Decimal("215000"), Decimal("520")),
    (Decimal("215000"),  Decimal("220000"), Decimal("610")),
    (Decimal("220000"),  Decimal("225000"), Decimal("700")),
    (Decimal("225000"),  Decimal("230000"), Decimal("790")),
    (Decimal("230000"),  Decimal("235000"), Decimal("880")),
    (Decimal("235000"),  Decimal("240000"), Decimal("970")),
    (Decimal("240000"),  Decimal("245000"), Decimal("1060")),
    (Decimal("245000"),  Decimal("250000"), Decimal("1150")),
    (Decimal("250000"),  Decimal("255000"), Decimal("1240")),
    (Decimal("255000"),  Decimal("260000"), Decimal("1330")),
    (Decimal("260000"),  Decimal("265000"), Decimal("1420")),
    (Decimal("265000"),  Decimal("270000"), Decimal("1510")),
    (Decimal("270000"),  Decimal("275000"), Decimal("1600")),
    (Decimal("275000"),  Decimal("280000"), Decimal("1690")),
    (Decimal("280000"),  Decimal("285000"), Decimal("1780")),
    (Decimal("285000"),  Decimal("290000"), Decimal("1870")),
    (Decimal("290000"),  Decimal("295000"), Decimal("1960")),
    (Decimal("295000"),  Decimal("300000"), Decimal("2050")),
    (Decimal("300000"),  Decimal("305000"), Decimal("2140")),
    (Decimal("305000"),  Decimal("310000"), Decimal("2230")),
    (Decimal("310000"),  Decimal("315000"), Decimal("2320")),
    (Decimal("315000"),  Decimal("320000"), Decimal("2410")),
    (Decimal("320000"),  Decimal("325000"), Decimal("2500")),
    (Decimal("325000"),  Decimal("330000"), Decimal("2590")),
    (Decimal("330000"),  Decimal("335000"), Decimal("2680")),
    (Decimal("335000"),  Decimal("340000"), Decimal("2770")),
    (Decimal("340000"),  Decimal("345000"), Decimal("2860")),
    (Decimal("345000"),  Decimal("500000"), Decimal("2950")),
    (Decimal("500000"),  Decimal("505000"), Decimal("3000")),
    (Decimal("505000"),  Decimal("510000"), Decimal("3050")),
    (Decimal("510000"),  Decimal("515000"), Decimal("3100")),
    (Decimal("515000"),  Decimal("520000"), Decimal("3150")),
    (Decimal("520000"),  Decimal("525000"), Decimal("3200")),
    (Decimal("525000"),  Decimal("530000"), Decimal("3250")),
    (Decimal("530000"),  Decimal("535000"), Decimal("3300")),
    (Decimal("535000"),  Decimal("540000"), Decimal("3350")),
    (Decimal("540000"),  None,              Decimal("3400")),
)

_TABLE_D_MFJ: tuple[tuple[Decimal, Decimal | None, Decimal], ...] = (
    (Decimal("0"),        Decimal("210000"),  Decimal("0")),
    (Decimal("210000"),   Decimal("220000"),  Decimal("50")),
    (Decimal("220000"),   Decimal("230000"),  Decimal("100")),
    (Decimal("230000"),   Decimal("240000"),  Decimal("150")),
    (Decimal("240000"),   Decimal("250000"),  Decimal("200")),
    (Decimal("250000"),   Decimal("260000"),  Decimal("250")),
    (Decimal("260000"),   Decimal("270000"),  Decimal("300")),
    (Decimal("270000"),   Decimal("280000"),  Decimal("350")),
    (Decimal("280000"),   Decimal("290000"),  Decimal("400")),
    (Decimal("290000"),   Decimal("300000"),  Decimal("450")),
    (Decimal("300000"),   Decimal("400000"),  Decimal("500")),
    (Decimal("400000"),   Decimal("410000"),  Decimal("680")),
    (Decimal("410000"),   Decimal("420000"),  Decimal("860")),
    (Decimal("420000"),   Decimal("430000"),  Decimal("1040")),
    (Decimal("430000"),   Decimal("440000"),  Decimal("1220")),
    (Decimal("440000"),   Decimal("450000"),  Decimal("1400")),
    (Decimal("450000"),   Decimal("460000"),  Decimal("1580")),
    (Decimal("460000"),   Decimal("470000"),  Decimal("1760")),
    (Decimal("470000"),   Decimal("480000"),  Decimal("1940")),
    (Decimal("480000"),   Decimal("490000"),  Decimal("2120")),
    (Decimal("490000"),   Decimal("500000"),  Decimal("2300")),
    (Decimal("500000"),   Decimal("510000"),  Decimal("2480")),
    (Decimal("510000"),   Decimal("520000"),  Decimal("2660")),
    (Decimal("520000"),   Decimal("530000"),  Decimal("2840")),
    (Decimal("530000"),   Decimal("540000"),  Decimal("3020")),
    (Decimal("540000"),   Decimal("550000"),  Decimal("3200")),
    (Decimal("550000"),   Decimal("560000"),  Decimal("3380")),
    (Decimal("560000"),   Decimal("570000"),  Decimal("3560")),
    (Decimal("570000"),   Decimal("580000"),  Decimal("3740")),
    (Decimal("580000"),   Decimal("590000"),  Decimal("3920")),
    (Decimal("590000"),   Decimal("600000"),  Decimal("4100")),
    (Decimal("600000"),   Decimal("610000"),  Decimal("4280")),
    (Decimal("610000"),   Decimal("620000"),  Decimal("4460")),
    (Decimal("620000"),   Decimal("630000"),  Decimal("4640")),
    (Decimal("630000"),   Decimal("640000"),  Decimal("4820")),
    (Decimal("640000"),   Decimal("650000"),  Decimal("5000")),
    (Decimal("650000"),   Decimal("660000"),  Decimal("5180")),
    (Decimal("660000"),   Decimal("670000"),  Decimal("5360")),
    (Decimal("670000"),   Decimal("680000"),  Decimal("5540")),
    (Decimal("680000"),   Decimal("690000"),  Decimal("5720")),
    (Decimal("690000"),   Decimal("1000000"), Decimal("5900")),
    (Decimal("1000000"),  Decimal("1010000"), Decimal("6000")),
    (Decimal("1010000"),  Decimal("1020000"), Decimal("6100")),
    (Decimal("1020000"),  Decimal("1030000"), Decimal("6200")),
    (Decimal("1030000"),  Decimal("1040000"), Decimal("6300")),
    (Decimal("1040000"),  Decimal("1050000"), Decimal("6400")),
    (Decimal("1050000"),  Decimal("1060000"), Decimal("6500")),
    (Decimal("1060000"),  Decimal("1070000"), Decimal("6600")),
    (Decimal("1070000"),  Decimal("1080000"), Decimal("6700")),
    (Decimal("1080000"),  None,               Decimal("6800")),
)

_TABLE_D_HOH: tuple[tuple[Decimal, Decimal | None, Decimal], ...] = (
    (Decimal("0"),       Decimal("168000"), Decimal("0")),
    (Decimal("168000"),  Decimal("176000"), Decimal("40")),
    (Decimal("176000"),  Decimal("184000"), Decimal("80")),
    (Decimal("184000"),  Decimal("192000"), Decimal("120")),
    (Decimal("192000"),  Decimal("200000"), Decimal("160")),
    (Decimal("200000"),  Decimal("208000"), Decimal("200")),
    (Decimal("208000"),  Decimal("216000"), Decimal("240")),
    (Decimal("216000"),  Decimal("224000"), Decimal("280")),
    (Decimal("224000"),  Decimal("232000"), Decimal("320")),
    (Decimal("232000"),  Decimal("240000"), Decimal("360")),
    (Decimal("240000"),  Decimal("320000"), Decimal("400")),
    (Decimal("320000"),  Decimal("328000"), Decimal("540")),
    (Decimal("328000"),  Decimal("336000"), Decimal("680")),
    (Decimal("336000"),  Decimal("344000"), Decimal("820")),
    (Decimal("344000"),  Decimal("352000"), Decimal("960")),
    (Decimal("352000"),  Decimal("360000"), Decimal("1100")),
    (Decimal("360000"),  Decimal("368000"), Decimal("1240")),
    (Decimal("368000"),  Decimal("376000"), Decimal("1380")),
    (Decimal("376000"),  Decimal("384000"), Decimal("1520")),
    (Decimal("384000"),  Decimal("392000"), Decimal("1660")),
    (Decimal("392000"),  Decimal("400000"), Decimal("1800")),
    (Decimal("400000"),  Decimal("408000"), Decimal("1940")),
    (Decimal("408000"),  Decimal("416000"), Decimal("2080")),
    (Decimal("416000"),  Decimal("424000"), Decimal("2220")),
    (Decimal("424000"),  Decimal("432000"), Decimal("2360")),
    (Decimal("432000"),  Decimal("440000"), Decimal("2500")),
    (Decimal("440000"),  Decimal("448000"), Decimal("2640")),
    (Decimal("448000"),  Decimal("456000"), Decimal("2780")),
    (Decimal("456000"),  Decimal("464000"), Decimal("2920")),
    (Decimal("464000"),  Decimal("472000"), Decimal("3060")),
    (Decimal("472000"),  Decimal("480000"), Decimal("3200")),
    (Decimal("480000"),  Decimal("488000"), Decimal("3340")),
    (Decimal("488000"),  Decimal("496000"), Decimal("3480")),
    (Decimal("496000"),  Decimal("504000"), Decimal("3620")),
    (Decimal("504000"),  Decimal("512000"), Decimal("3760")),
    (Decimal("512000"),  Decimal("520000"), Decimal("3900")),
    (Decimal("520000"),  Decimal("528000"), Decimal("4040")),
    (Decimal("528000"),  Decimal("536000"), Decimal("4180")),
    (Decimal("536000"),  Decimal("544000"), Decimal("4320")),
    (Decimal("544000"),  Decimal("552000"), Decimal("4460")),
    (Decimal("552000"),  Decimal("800000"), Decimal("4600")),
    (Decimal("800000"),  Decimal("808000"), Decimal("4680")),
    (Decimal("808000"),  Decimal("816000"), Decimal("4760")),
    (Decimal("816000"),  Decimal("824000"), Decimal("4840")),
    (Decimal("824000"),  Decimal("832000"), Decimal("4920")),
    (Decimal("832000"),  Decimal("840000"), Decimal("5000")),
    (Decimal("840000"),  Decimal("848000"), Decimal("5080")),
    (Decimal("848000"),  Decimal("856000"), Decimal("5160")),
    (Decimal("856000"),  Decimal("864000"), Decimal("5240")),
    (Decimal("864000"),  None,              Decimal("5320")),
)


def _table_d_recapture(filing_status: FilingStatus, agi: Decimal) -> Decimal:
    """Table D: Tax Recapture."""
    if filing_status in (FilingStatus.SINGLE, FilingStatus.MFS):
        return _lookup_step(_TABLE_D_SINGLE_OR_MFS, agi)
    if filing_status in (FilingStatus.MFJ, FilingStatus.QSS):
        return _lookup_step(_TABLE_D_MFJ, agi)
    if filing_status == FilingStatus.HOH:
        return _lookup_step(_TABLE_D_HOH, agi)
    raise ValueError(f"Unknown filing status: {filing_status}")


# Table E — Personal Tax Credit (decimal multiplier applied to Line 7).
# Each entry: (more_than_agi, less_than_or_equal_to_agi_or_None, decimal).
# Below each filing status's first listed row, the credit is 0.00 (CT AGI
# below the personal-exemption phase-in threshold). The tables implicitly
# start at zero above; CT AGI at or below the "filing threshold" never
# reaches Table E because exemptions already zero out the tax.

_TABLE_E_SINGLE: tuple[tuple[Decimal, Decimal | None, Decimal], ...] = (
    (Decimal("0"),       Decimal("15000"),  Decimal("0")),
    (Decimal("15000"),   Decimal("18800"),  Decimal("0.75")),
    (Decimal("18800"),   Decimal("19300"),  Decimal("0.70")),
    (Decimal("19300"),   Decimal("19800"),  Decimal("0.65")),
    (Decimal("19800"),   Decimal("20300"),  Decimal("0.60")),
    (Decimal("20300"),   Decimal("20800"),  Decimal("0.55")),
    (Decimal("20800"),   Decimal("21300"),  Decimal("0.50")),
    (Decimal("21300"),   Decimal("21800"),  Decimal("0.45")),
    (Decimal("21800"),   Decimal("22300"),  Decimal("0.40")),
    (Decimal("22300"),   Decimal("25000"),  Decimal("0.35")),
    (Decimal("25000"),   Decimal("25500"),  Decimal("0.30")),
    (Decimal("25500"),   Decimal("26000"),  Decimal("0.25")),
    (Decimal("26000"),   Decimal("26500"),  Decimal("0.20")),
    (Decimal("26500"),   Decimal("31300"),  Decimal("0.15")),
    (Decimal("31300"),   Decimal("31800"),  Decimal("0.14")),
    (Decimal("31800"),   Decimal("32300"),  Decimal("0.13")),
    (Decimal("32300"),   Decimal("32800"),  Decimal("0.12")),
    (Decimal("32800"),   Decimal("33300"),  Decimal("0.11")),
    (Decimal("33300"),   Decimal("60000"),  Decimal("0.10")),
    (Decimal("60000"),   Decimal("60500"),  Decimal("0.09")),
    (Decimal("60500"),   Decimal("61000"),  Decimal("0.08")),
    (Decimal("61000"),   Decimal("61500"),  Decimal("0.07")),
    (Decimal("61500"),   Decimal("62000"),  Decimal("0.06")),
    (Decimal("62000"),   Decimal("62500"),  Decimal("0.05")),
    (Decimal("62500"),   Decimal("63000"),  Decimal("0.04")),
    (Decimal("63000"),   Decimal("63500"),  Decimal("0.03")),
    (Decimal("63500"),   Decimal("64000"),  Decimal("0.02")),
    (Decimal("64000"),   Decimal("64500"),  Decimal("0.01")),
    (Decimal("64500"),   None,              Decimal("0")),
)

_TABLE_E_MFJ: tuple[tuple[Decimal, Decimal | None, Decimal], ...] = (
    (Decimal("0"),       Decimal("24000"),  Decimal("0")),
    (Decimal("24000"),   Decimal("30000"),  Decimal("0.75")),
    (Decimal("30000"),   Decimal("30500"),  Decimal("0.70")),
    (Decimal("30500"),   Decimal("31000"),  Decimal("0.65")),
    (Decimal("31000"),   Decimal("31500"),  Decimal("0.60")),
    (Decimal("31500"),   Decimal("32000"),  Decimal("0.55")),
    (Decimal("32000"),   Decimal("32500"),  Decimal("0.50")),
    (Decimal("32500"),   Decimal("33000"),  Decimal("0.45")),
    (Decimal("33000"),   Decimal("33500"),  Decimal("0.40")),
    (Decimal("33500"),   Decimal("40000"),  Decimal("0.35")),
    (Decimal("40000"),   Decimal("40500"),  Decimal("0.30")),
    (Decimal("40500"),   Decimal("41000"),  Decimal("0.25")),
    (Decimal("41000"),   Decimal("41500"),  Decimal("0.20")),
    (Decimal("41500"),   Decimal("50000"),  Decimal("0.15")),
    (Decimal("50000"),   Decimal("50500"),  Decimal("0.14")),
    (Decimal("50500"),   Decimal("51000"),  Decimal("0.13")),
    (Decimal("51000"),   Decimal("51500"),  Decimal("0.12")),
    (Decimal("51500"),   Decimal("52000"),  Decimal("0.11")),
    (Decimal("52000"),   Decimal("96000"),  Decimal("0.10")),
    (Decimal("96000"),   Decimal("96500"),  Decimal("0.09")),
    (Decimal("96500"),   Decimal("97000"),  Decimal("0.08")),
    (Decimal("97000"),   Decimal("97500"),  Decimal("0.07")),
    (Decimal("97500"),   Decimal("98000"),  Decimal("0.06")),
    (Decimal("98000"),   Decimal("98500"),  Decimal("0.05")),
    (Decimal("98500"),   Decimal("99000"),  Decimal("0.04")),
    (Decimal("99000"),   Decimal("99500"),  Decimal("0.03")),
    (Decimal("99500"),   Decimal("100000"), Decimal("0.02")),
    (Decimal("100000"),  Decimal("100500"), Decimal("0.01")),
    (Decimal("100500"),  None,              Decimal("0")),
)

_TABLE_E_MFS: tuple[tuple[Decimal, Decimal | None, Decimal], ...] = (
    (Decimal("0"),       Decimal("12000"),  Decimal("0")),
    (Decimal("12000"),   Decimal("15000"),  Decimal("0.75")),
    (Decimal("15000"),   Decimal("15500"),  Decimal("0.70")),
    (Decimal("15500"),   Decimal("16000"),  Decimal("0.65")),
    (Decimal("16000"),   Decimal("16500"),  Decimal("0.60")),
    (Decimal("16500"),   Decimal("17000"),  Decimal("0.55")),
    (Decimal("17000"),   Decimal("17500"),  Decimal("0.50")),
    (Decimal("17500"),   Decimal("18000"),  Decimal("0.45")),
    (Decimal("18000"),   Decimal("18500"),  Decimal("0.40")),
    (Decimal("18500"),   Decimal("20000"),  Decimal("0.35")),
    (Decimal("20000"),   Decimal("20500"),  Decimal("0.30")),
    (Decimal("20500"),   Decimal("21000"),  Decimal("0.25")),
    (Decimal("21000"),   Decimal("21500"),  Decimal("0.20")),
    (Decimal("21500"),   Decimal("25000"),  Decimal("0.15")),
    (Decimal("25000"),   Decimal("25500"),  Decimal("0.14")),
    (Decimal("25500"),   Decimal("26000"),  Decimal("0.13")),
    (Decimal("26000"),   Decimal("26500"),  Decimal("0.12")),
    (Decimal("26500"),   Decimal("27000"),  Decimal("0.11")),
    (Decimal("27000"),   Decimal("48000"),  Decimal("0.10")),
    (Decimal("48000"),   Decimal("48500"),  Decimal("0.09")),
    (Decimal("48500"),   Decimal("49000"),  Decimal("0.08")),
    (Decimal("49000"),   Decimal("49500"),  Decimal("0.07")),
    (Decimal("49500"),   Decimal("50000"),  Decimal("0.06")),
    (Decimal("50000"),   Decimal("50500"),  Decimal("0.05")),
    (Decimal("50500"),   Decimal("51000"),  Decimal("0.04")),
    (Decimal("51000"),   Decimal("51500"),  Decimal("0.03")),
    (Decimal("51500"),   Decimal("52000"),  Decimal("0.02")),
    (Decimal("52000"),   Decimal("52500"),  Decimal("0.01")),
    (Decimal("52500"),   None,              Decimal("0")),
)

_TABLE_E_HOH: tuple[tuple[Decimal, Decimal | None, Decimal], ...] = (
    (Decimal("0"),       Decimal("19000"),  Decimal("0")),
    (Decimal("19000"),   Decimal("24000"),  Decimal("0.75")),
    (Decimal("24000"),   Decimal("24500"),  Decimal("0.70")),
    (Decimal("24500"),   Decimal("25000"),  Decimal("0.65")),
    (Decimal("25000"),   Decimal("25500"),  Decimal("0.60")),
    (Decimal("25500"),   Decimal("26000"),  Decimal("0.55")),
    (Decimal("26000"),   Decimal("26500"),  Decimal("0.50")),
    (Decimal("26500"),   Decimal("27000"),  Decimal("0.45")),
    (Decimal("27000"),   Decimal("27500"),  Decimal("0.40")),
    (Decimal("27500"),   Decimal("34000"),  Decimal("0.35")),
    (Decimal("34000"),   Decimal("34500"),  Decimal("0.30")),
    (Decimal("34500"),   Decimal("35000"),  Decimal("0.25")),
    (Decimal("35000"),   Decimal("35500"),  Decimal("0.20")),
    (Decimal("35500"),   Decimal("44000"),  Decimal("0.15")),
    (Decimal("44000"),   Decimal("44500"),  Decimal("0.14")),
    (Decimal("44500"),   Decimal("45000"),  Decimal("0.13")),
    (Decimal("45000"),   Decimal("45500"),  Decimal("0.12")),
    (Decimal("45500"),   Decimal("46000"),  Decimal("0.11")),
    (Decimal("46000"),   Decimal("74000"),  Decimal("0.10")),
    (Decimal("74000"),   Decimal("74500"),  Decimal("0.09")),
    (Decimal("74500"),   Decimal("75000"),  Decimal("0.08")),
    (Decimal("75000"),   Decimal("75500"),  Decimal("0.07")),
    (Decimal("75500"),   Decimal("76000"),  Decimal("0.06")),
    (Decimal("76000"),   Decimal("76500"),  Decimal("0.05")),
    (Decimal("76500"),   Decimal("77000"),  Decimal("0.04")),
    (Decimal("77000"),   Decimal("77500"),  Decimal("0.03")),
    (Decimal("77500"),   Decimal("78000"),  Decimal("0.02")),
    (Decimal("78000"),   Decimal("78500"),  Decimal("0.01")),
    (Decimal("78500"),   None,              Decimal("0")),
)


def _table_e_credit_decimal(
    filing_status: FilingStatus, agi: Decimal
) -> Decimal:
    """Table E: Personal Tax Credit decimal."""
    if filing_status == FilingStatus.SINGLE:
        return _lookup_step(_TABLE_E_SINGLE, agi)
    if filing_status in (FilingStatus.MFJ, FilingStatus.QSS):
        return _lookup_step(_TABLE_E_MFJ, agi)
    if filing_status == FilingStatus.MFS:
        return _lookup_step(_TABLE_E_MFS, agi)
    if filing_status == FilingStatus.HOH:
        return _lookup_step(_TABLE_E_HOH, agi)
    raise ValueError(f"Unknown filing status: {filing_status}")


# ---------------------------------------------------------------------------
# v1 limitations (loud TODOs)
# ---------------------------------------------------------------------------


CT_V1_LIMITATIONS: tuple[str, ...] = (
    "tenforty/OpenTaxSolver does NOT actually support CT despite having an "
    "OTSState.CT enum member (evaluate_return raises 'OTS does not support "
    "2025/CT_1'). This plugin hand-rolls the CT-1040 TCS calc. Rewrite as "
    "a tenforty wrapper if OTS upstream ever adds CT form support.",
    "CT additions to federal AGI not modeled (CT-1040 Schedule 1 lines "
    "31-37): interest on state/local obligations, MSR contributions, "
    "special trust/estate items, etc.",
    "CT subtractions from federal AGI not modeled (CT-1040 Schedule 1 "
    "lines 41-52): US government interest, Social Security benefits "
    "exemption, military retirement / teacher pension / individual retirement "
    "account distributions (Pension & Annuity modification), 529 plan "
    "contributions (CHET), organ-donation expenses, HSA contributions, etc.",
    "CT property tax credit not applied (CT-1040 line 13 — up to $300 credit "
    "on primary-residence or motor-vehicle property taxes, subject to AGI "
    "phase-out).",
    "CT EITC not applied (CT-1040 line 21, 40% match of federal EITC for "
    "TY2025).",
    "CT AMT not computed (CT-1040 line 10 / Form CT-6251).",
    "CT use tax not computed (CT-1040 line 15).",
    "Nonresident / part-year apportionment uses day-based proration "
    "(days / 365) instead of the CT-1040NR/PY CT-source-income ratio "
    "(Schedule CT-SI).",
)


# ---------------------------------------------------------------------------
# The 10-line CT-1040 Tax Calculation Schedule
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CTTaxCalcResult:
    """Structured result of walking the CT-1040 TCS lines 1-10.

    Each field names the corresponding TCS line number so downstream
    consumers and the test suite can pin individual steps without
    reimplementing the flow.
    """

    line_1_ct_agi: Decimal
    line_2_exemption: Decimal
    line_3_ct_taxable_income: Decimal
    line_4_initial_tax: Decimal
    line_5_phaseout_addback: Decimal
    line_6_tax_recapture: Decimal
    line_7_sum: Decimal
    line_8_credit_decimal: Decimal
    line_9_credit_amount: Decimal
    line_10_ct_tax: Decimal


def compute_ct_tax(
    filing_status: FilingStatus, ct_agi: Decimal
) -> CTTaxCalcResult:
    """Run the full CT-1040 TCS calc for a given filing status and CT AGI.

    This is the single source of truth for the bracket math. ``CTPlugin.compute``
    calls this after deriving CT AGI from the federal totals (v1:
    CT AGI == federal AGI).
    """
    line_1 = max(Decimal("0"), ct_agi)
    line_2 = _table_a_exemption(filing_status, line_1)
    line_3 = max(Decimal("0"), line_1 - line_2)
    line_4 = _table_b_initial_tax(filing_status, line_3)
    line_5 = _table_c_phaseout_addback(filing_status, line_1)
    line_6 = _table_d_recapture(filing_status, line_1)
    line_7 = line_4 + line_5 + line_6
    line_8 = _table_e_credit_decimal(filing_status, line_1)
    line_9 = line_7 * line_8
    line_10 = line_7 - line_9
    return CTTaxCalcResult(
        line_1_ct_agi=_cents(line_1),
        line_2_exemption=_cents(line_2),
        line_3_ct_taxable_income=_cents(line_3),
        line_4_initial_tax=_cents(line_4),
        line_5_phaseout_addback=_cents(line_5),
        line_6_tax_recapture=_cents(line_6),
        line_7_sum=_cents(line_7),
        line_8_credit_decimal=line_8,
        line_9_credit_amount=_cents(line_9),
        line_10_ct_tax=_cents(line_10),
    )


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConnecticutPlugin:
    """State plugin for Connecticut.

    Hand-rolled CT-1040 TCS calc because tenforty / OpenTaxSolver does
    not actually support CT for any year (``OTSState.CT -> CT_1`` exists
    in the enum but has zero entries in ``OTS_FORM_CONFIG``). Starting
    point is federal AGI (v1 approximates CT AGI = federal AGI with no
    CT-specific additions or subtractions). See ``CT_V1_LIMITATIONS`` for
    the enumerated list of items this plugin does NOT yet model.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # v1: CT AGI ≈ federal AGI. TODO(ct-sch1): apply the real
        # CT-1040 Schedule 1 additions/subtractions.
        ct_agi = federal.adjusted_gross_income

        tcs = compute_ct_tax(federal.filing_status, ct_agi)
        state_tax_full = tcs.line_10_ct_tax

        # Apportion tax for nonresident / part-year. TODO(ct-1040nrpy):
        # replace with real CT-1040NR/PY CT-source-income ratio
        # (Schedule CT-SI) in fan-out.
        fraction = _apportionment_fraction(residency, days_in_state)
        state_tax_apportioned = _cents(state_tax_full * fraction)

        state_specific: dict[str, Any] = {
            "state_adjusted_gross_income": _cents(ct_agi),
            "state_taxable_income": tcs.line_3_ct_taxable_income,
            "state_total_tax": state_tax_apportioned,
            "state_total_tax_resident_basis": state_tax_full,
            "apportionment_fraction": fraction,
            # Explicit TCS breakdown so downstream output renderers can
            # fill the CT-1040 Tax Calculation Schedule line-by-line.
            "tcs_line_1_ct_agi": tcs.line_1_ct_agi,
            "tcs_line_2_exemption": tcs.line_2_exemption,
            "tcs_line_3_ct_taxable_income": tcs.line_3_ct_taxable_income,
            "tcs_line_4_initial_tax": tcs.line_4_initial_tax,
            "tcs_line_5_phaseout_addback": tcs.line_5_phaseout_addback,
            "tcs_line_6_tax_recapture": tcs.line_6_tax_recapture,
            "tcs_line_7_sum": tcs.line_7_sum,
            "tcs_line_8_credit_decimal": tcs.line_8_credit_decimal,
            "tcs_line_9_credit_amount": tcs.line_9_credit_amount,
            "tcs_line_10_ct_tax": tcs.line_10_ct_tax,
            "v1_limitations": list(CT_V1_LIMITATIONS),
            "tenforty_backed": False,
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
        """Split canonical income into CT-source vs non-CT-source.

        Residents: everything is CT-source. Nonresident / part-year:
        prorate each category by ``days_in_state / 365``.

        TODO(ct-1040nrpy): CT actually sources each income type via the
        CT-1040NR/PY Schedule CT-SI — wages to the work location,
        interest/dividends to the taxpayer's domicile, rental to the
        property state, etc. Day-based proration is the shared first-cut
        across all fan-out state plugins; refine in follow-up.
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

        # Reuse engine helpers so CT mirrors federal rollups.
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
        # TODO(ct-pdf): fan-out follow-up — fill CT Form CT-1040 (and
        # CT-1040NR/PY for nonresidents, Schedule CT-SI, Schedule 1
        # additions/subtractions, Schedule 3 property tax credit) using
        # pypdf against the CT DRS's fillable PDFs. The output renderer
        # suite is the right home for this; this plugin returns
        # structured TCS line data that the renderer will consume.
        return []

    def form_ids(self) -> list[str]:
        return ["CT Form CT-1040"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = ConnecticutPlugin(
    meta=StatePluginMeta(
        code="CT",
        name="Connecticut",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        # CT DRS: https://portal.ct.gov/drs
        dor_url="https://portal.ct.gov/drs",
        # myconneCT — CT's free direct-file portal:
        # https://portal.ct.gov/drs-myconneCT
        free_efile_url="https://portal.ct.gov/drs-myconneCT",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # CT has NO bilateral reciprocity agreements (verified Tax
        # Foundation 2024; verified against state-reciprocity.json).
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Hand-rolled CT-1040 TCS calc (tenforty does NOT actually "
            "support CT despite having an OTSState.CT enum member; "
            "evaluate_return raises 'OTS does not support 2025/CT_1' for "
            "every year). TY2025 graduated brackets: 2% / 4.5% / 5.5% / "
            "6% / 6.5% / 6.9% / 6.99% per Form CT-1040 TCS (Rev. 12/25). "
            "Two-bracket TY2024 rate cut (3->2%, 5->4.5%) under PA 23-204 "
            "preserved in TY2025. Personal exemption, 2% tax rate phase-out "
            "add-back, tax recapture, and personal credit decimals all "
            "transcribed verbatim from CT-1040 TCS Tables A-E. Submission "
            "via myconneCT portal. No reciprocity."
        ),
    )
)
