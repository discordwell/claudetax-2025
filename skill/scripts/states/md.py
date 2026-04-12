"""Maryland (MD) state plugin — TY2025.

See skill/reference/tenforty-ty2025-gap.md for the TY2025 probe rubric
and why MD is hand-rolled instead of graph-wrapped (OTS_FORM_CONFIG
has no MD_502 entries for any year, and MD has a county-level local
piggyback that would not fit a single-state graph wrap regardless).

Maryland is NOT supported by tenforty/OpenTaxSolver. Calling
``tenforty.evaluate_return(..., state='MD')`` raises
``ValueError: OTS does not support 2025/MD_502`` for every tax year currently
shipped with tenforty (2018-2025), even though ``OTSState.MD`` exists in the
``tenforty.models`` enum. This was verified in-worktree during wave 4 with the
shipping tenforty package. Because tenforty cannot handle MD, this plugin
**hand-rolls** the Form 502 calculation from the Maryland Comptroller's own
rate schedules and instruction booklets.

This is a big deal, and wave 4 flags it loudly: of the 11 states tenforty
actually handles in 2025 (AZ, CA, MA, MI, NC, NJ, NY, OH, OR, PA, VA), MD is
not one of them. Down-stream callers must treat the MD numbers below as
produced by in-house code, not by OpenTaxSolver.

================================================================================
MARYLAND LOCAL (COUNTY) INCOME TAX — THE BIG COMPLEXITY
================================================================================
Maryland has a **county-level** local income tax piggyback on top of the state
rate. Every resident owes BOTH a state tax AND a local tax, and the local tax
is assessed by the county (or Baltimore City) where the taxpayer was domiciled
on the last day of the tax year. Local rates for TY2025 range from **2.25%** to
**3.30%**, with the max cap rising to 3.30% (previously 3.20%) under the 2025
Budget Reconciliation and Financing Act of 2025 (Chapter 604 of the Acts of
2025). Dorchester County retroactively hiked its rate from 3.20% to 3.30% for
TY2025 after notifying the Comptroller by May 15, 2025.

Because tenforty does nothing for MD, this plugin DOES compute local tax
itself, using a county rate table keyed by county name + filing status. If the
caller does not supply a county, the plugin applies the 2.25% **nonresident /
out-of-state default** rate and flags it in ``state_specific['local_tax_note']``
so consumers can surface a warning.

IMPORTANT v1 LIMITATION: County tax is assessed on **Maryland taxable net
income** (the same base the state tax is assessed on for residents), NOT on
federal AGI and NOT on Maryland state tax. This matches Form 502 line 28
("Local tax" = MD taxable net income × local rate) in the 2025 instructions.
Anne Arundel and Frederick Counties have **progressive** local brackets
(multiple rates by income tier); v1 models Anne Arundel's three-tier
structure and Frederick's five-tier structure explicitly.

================================================================================
SOURCES (all verified 2026-04-11 against Maryland Comptroller primary sources)
================================================================================
1. Maryland Withholding Tax Facts (January 2025 - December 2025), COM/RAD 098
   Revised 07/25:
   https://www.marylandcomptroller.gov/content/dam/mdcomp/tax/legal-publications/facts/Withholding-Tax-Facts-2025.pdf
   - Tax Rate Schedule I (Single / MFS / Dependent taxpayers)
   - Tax Rate Schedule II (MFJ / HOH / QSS)
   - Personal exemption withholding default: $3,200
   - County rate table (24 jurisdictions: 23 counties + Baltimore City)

2. Maryland Tax Alert, "Changes to Standard and Itemized Deductions and to
   State and Local Income Tax Rates from the 2025 Legislative Session",
   Revised December 22, 2025:
   https://www.marylandcomptroller.gov/content/dam/mdcomp/tax/legal-publications/alerts/tax-alert-changes-to-standard-and-itemized-deductions-and-to-state-and-local-income-tax-rates-from-the-2025-legislative-session.pdf
   - New flat standard deduction: $3,350 (Single/MFS/Dep), $6,700 (MFJ/HOH/QSS)
     eliminates the prior income-based phase-in (15% of MD AGI, min $1,700,
     max $2,550).
   - Two NEW state brackets above 5.75% top: 6.25% and 6.50% for high-income
     filers (TY2025+).
   - Maximum local rate cap raised to 3.30% (TY2025+).
   - 2% capital gains surtax for FAGI over $350,000 (TY2025+).
   - Itemized deduction phase-out: 7.5% of FAGI over $200k (or $100k MFS).

3. Maryland Form 502 (2025 Resident Income Tax Return), COM/RAD 009:
   https://www.marylandcomptroller.gov/content/dam/mdcomp/tax/forms/2025/502.pdf

4. Maryland Comptroller iFile free e-file portal:
   https://interactive.marylandtaxes.gov/Individuals/iFile_ChooseForm/default.asp

================================================================================
TY2025 STATE BRACKETS
================================================================================
Schedule I (Single / Married Filing Separately / Dependent):
    $0           -  $1,000      2.00% of taxable income
    $1,001       -  $2,000      $20 + 3.00% over $1,000
    $2,001       -  $3,000      $50 + 4.00% over $2,000
    $3,001       -  $100,000    $90 + 4.75% over $3,000
    $100,001     -  $125,000    $4,697.50 + 5.00% over $100,000
    $125,001     -  $150,000    $5,947.50 + 5.25% over $125,000
    $150,001     -  $250,000    $7,260.00 + 5.50% over $150,000
    $250,001     -  $500,000    $12,760.00 + 5.75% over $250,000      [NEW CAP]
    $500,001     -  $1,000,000  $27,135.00 + 6.25% over $500,000      [NEW]
    $1,000,001   -  +infinity   $58,385.00 + 6.50% over $1,000,000    [NEW]

Schedule II (MFJ / HOH / QSS):
    $0           -  $1,000      2.00%
    $1,001       -  $2,000      $20 + 3.00% over $1,000
    $2,001       -  $3,000      $50 + 4.00% over $2,000
    $3,001       -  $150,000    $90 + 4.75% over $3,000
    $150,001     -  $175,000    $7,072.50 + 5.00% over $150,000
    $175,001     -  $225,000    $8,322.50 + 5.25% over $175,000
    $225,001     -  $300,000    $10,947.50 + 5.50% over $225,000
    $300,001     -  $600,000    $15,072.50 + 5.75% over $300,000      [NEW CAP]
    $600,001     -  $1,200,000  $32,332.50 + 6.25% over $600,000      [NEW]
    $1,200,001   -  +infinity   $69,822.50 + 6.50% over $1,200,000    [NEW]

================================================================================
TY2025 $65K SINGLE RESIDENT REFERENCE (locked in tests)
================================================================================
Federal AGI: $65,000; MD AGI approximated as $65,000.
Standard deduction: $3,350 (Single, new flat value per 2025 session).
Personal exemption: $3,200 (Single, FAGI < $100k).
MD taxable net income: $65,000 - $3,350 - $3,200 = $58,450.
State tax: $90 + 4.75% * ($58,450 - $3,000) = $90 + $2,633.875 = $2,723.88.
Local tax (Baltimore City / Montgomery / Prince George's @ 3.20%):
    $58,450 * 0.0320 = $1,870.40.
Local tax (out-of-state default @ 2.25%):
    $58,450 * 0.0225 = $1,315.13.
Total (max 3.20% locality): $2,723.88 + $1,870.40 = $4,594.28.
Total (2.25% default): $2,723.88 + $1,315.13 = $4,039.01.

================================================================================
RECIPROCITY
================================================================================
Maryland has bilateral reciprocity with DC, PA, VA, and WV. A resident of one
of those states working in MD only pays income tax to their home state on
wages (and vice versa). Verified against
``skill/reference/state-reciprocity.json``:
    {"states": ["MD", "DC"]}
    {"states": ["MD", "PA"]}
    {"states": ["MD", "VA"]}
    {"states": ["MD", "WV"]}

================================================================================
SUBMISSION CHANNEL
================================================================================
Maryland participates in the IRS Fed/State MeF program AND operates its own
free direct-file portal (iFile). The canonical submission path for our output
pipeline is ``SubmissionChannel.STATE_DOR_FREE_PORTAL`` (iFile).

================================================================================
V1 LIMITATIONS (flagged loudly)
================================================================================
See ``MD_V1_LIMITATIONS`` below. Headline items:
    - MD additions / subtractions from federal AGI not applied (Form 502
      lines 5-17). MD AGI approximated as federal AGI.
    - Personal exemption phase-out not applied (Form 502 Exemption Worksheet):
      $3,200 at FAGI < $100k / $150k MFJ, declining to $0 at FAGI > $200k /
      $350k MFJ. v1 uses flat $3,200 per exemption for the withholding default.
    - 2% capital gains surtax for FAGI > $350k (TY2025+) not applied.
    - Local tax computed on a county rate table keyed by county name; the
      plugin does NOT look up the county from the taxpayer's address. The
      caller supplies a ``md_county`` key in ``state_specific`` inputs OR the
      plugin defaults to the 2.25% nonresident rate and flags it.
    - Anne Arundel and Frederick County progressive local brackets are
      modelled. Other counties use flat rates.
    - Nonresident / part-year apportionment uses day-based proration instead
      of the Form 505 (nonresident) / 502 Schedule A-part-year income-source
      ratio.
    - Itemized deduction phase-out (7.5% of FAGI over $200k / $100k MFS)
      not applied (std deduction only in v1).
    - MD refundable credits (EITC, CTC, dependent-care) and nonrefundable
      credits not applied.
    - MD AMT / tax on tax-exempt out-of-state muni bond interest not applied.
"""
# Reciprocity partners (verified against skill/reference/state-reciprocity.json):
#   DC, PA, VA, WV.
# Maryland Comptroller iFile: https://interactive.marylandtaxes.gov/Individuals/iFile_ChooseForm/default.asp
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


# Canonical wave-4 $65k Single gatekeeper lock — MD Form 502 state
# tax plus the 2.25% default local tax. Hand-traced from MD Form 502
# — see module docstring. Referenced from test_state_md.py.
LOCK_VALUE: Final[Decimal] = Decimal("4039.01")


# ---------------------------------------------------------------------------
# TY2025 state bracket schedules
# ---------------------------------------------------------------------------

# Each tuple is (upper_bound_inclusive_or_None_for_top, base_tax, rate, floor).
# base_tax = tax on income up to ``floor``; then (income - floor) * rate is added.
#
# Source: Maryland Withholding Tax Facts 2025 (COM/RAD 098 rev 07/25) Tax Rate
# Schedules I and II, AND 2025 Maryland Tax Alert (Rev. 12/22/2025) Section III
# "State Income Tax Rate Changes" confirming the two new brackets 6.25% and
# 6.50% above the 5.75% cap (TY2025+).
MD_TY2025_BRACKETS_SCHEDULE_I: tuple[
    tuple[Decimal | None, Decimal, Decimal, Decimal], ...
] = (
    # upper                  base_tax             rate              floor
    (Decimal("1000"),         Decimal("0"),         Decimal("0.02"),   Decimal("0")),
    (Decimal("2000"),         Decimal("20"),        Decimal("0.03"),   Decimal("1000")),
    (Decimal("3000"),         Decimal("50"),        Decimal("0.04"),   Decimal("2000")),
    (Decimal("100000"),       Decimal("90"),        Decimal("0.0475"), Decimal("3000")),
    (Decimal("125000"),       Decimal("4697.50"),   Decimal("0.05"),   Decimal("100000")),
    (Decimal("150000"),       Decimal("5947.50"),   Decimal("0.0525"), Decimal("125000")),
    (Decimal("250000"),       Decimal("7260.00"),   Decimal("0.055"),  Decimal("150000")),
    (Decimal("500000"),       Decimal("12760.00"),  Decimal("0.0575"), Decimal("250000")),
    (Decimal("1000000"),      Decimal("27135.00"),  Decimal("0.0625"), Decimal("500000")),
    (None,                    Decimal("58385.00"),  Decimal("0.065"),  Decimal("1000000")),
)
"""Schedule I — Single, MFS, Dependent taxpayer (and fiduciaries)."""

MD_TY2025_BRACKETS_SCHEDULE_II: tuple[
    tuple[Decimal | None, Decimal, Decimal, Decimal], ...
] = (
    # upper                  base_tax             rate              floor
    (Decimal("1000"),         Decimal("0"),         Decimal("0.02"),   Decimal("0")),
    (Decimal("2000"),         Decimal("20"),        Decimal("0.03"),   Decimal("1000")),
    (Decimal("3000"),         Decimal("50"),        Decimal("0.04"),   Decimal("2000")),
    (Decimal("150000"),       Decimal("90"),        Decimal("0.0475"), Decimal("3000")),
    (Decimal("175000"),       Decimal("7072.50"),   Decimal("0.05"),   Decimal("150000")),
    (Decimal("225000"),       Decimal("8322.50"),   Decimal("0.0525"), Decimal("175000")),
    (Decimal("300000"),       Decimal("10947.50"),  Decimal("0.055"),  Decimal("225000")),
    (Decimal("600000"),       Decimal("15072.50"),  Decimal("0.0575"), Decimal("300000")),
    (Decimal("1200000"),      Decimal("32332.50"),  Decimal("0.0625"), Decimal("600000")),
    (None,                    Decimal("69822.50"),  Decimal("0.065"),  Decimal("1200000")),
)
"""Schedule II — MFJ, HOH, Qualifying Surviving Spouse (QSS)."""


def _schedule_for(filing_status: FilingStatus) -> tuple[
    tuple[Decimal | None, Decimal, Decimal, Decimal], ...
]:
    if filing_status in (FilingStatus.SINGLE, FilingStatus.MFS):
        return MD_TY2025_BRACKETS_SCHEDULE_I
    # MFJ, HOH, QSS all use Schedule II.
    return MD_TY2025_BRACKETS_SCHEDULE_II


def _md_state_tax(taxable_net_income: Decimal, filing_status: FilingStatus) -> Decimal:
    """Apply the TY2025 MD state bracket schedule.

    Returns a non-negative Decimal rounded to cents. Negative taxable income
    (possible when the deduction exceeds AGI) yields zero.
    """
    if taxable_net_income <= 0:
        return Decimal("0")
    schedule = _schedule_for(filing_status)
    for upper, base_tax, rate, floor in schedule:
        if upper is None or taxable_net_income <= upper:
            tax = base_tax + (taxable_net_income - floor) * rate
            return _cents(tax)
    raise RuntimeError("MD bracket table did not cover taxable income")


# ---------------------------------------------------------------------------
# TY2025 standard deduction (new flat values per 2025 legislative session)
# ---------------------------------------------------------------------------

# Source: 2025 Maryland Tax Alert Section IV.A — "For the tax year beginning
# after December 31, 2024, the standard deduction amounts are: $3,350 for
# Single, Married Filing Separately, or Dependent filers, and $6,700 for
# Married Filing Jointly, Head of Household or Qualifying Surviving Spouse
# filers." The Act eliminates the prior income-based phase-in (15% of MD AGI
# capped at $1,700-$2,550 / $3,400-$5,150) and indexes future increases to
# CPI. This is a new flat value for TY2025.
MD_TY2025_STANDARD_DEDUCTION: dict[FilingStatus, Decimal] = {
    FilingStatus.SINGLE: Decimal("3350"),
    FilingStatus.MFS: Decimal("3350"),
    FilingStatus.MFJ: Decimal("6700"),
    FilingStatus.HOH: Decimal("6700"),
    FilingStatus.QSS: Decimal("6700"),
}


def _md_standard_deduction(filing_status: FilingStatus) -> Decimal:
    return MD_TY2025_STANDARD_DEDUCTION[filing_status]


# ---------------------------------------------------------------------------
# Personal exemption (flat withholding default; phase-out is a v1 limitation)
# ---------------------------------------------------------------------------

MD_TY2025_PERSONAL_EXEMPTION_WITHHOLDING_DEFAULT: Decimal = Decimal("3200")
"""Flat $3,200 per exemption — the Withholding Tax Facts 2025 default.

On the real Form 502 Exemption Worksheet, the per-exemption amount is
$3,200 for FAGI below a filing-status-dependent threshold (e.g. $100,000
Single / $150,000 MFJ), then declines linearly to $0 at the upper threshold
($200,000 Single / $350,000 MFJ). v1 ignores the phase-out — flagged in
MD_V1_LIMITATIONS. Tests pin the flat value."""


def _personal_exemptions(federal: FederalTotals) -> int:
    """Count personal exemptions: 1 taxpayer + spouse (if MFJ/QSS) + dependents.

    MD Form 502 Part D counts the taxpayer, spouse (on MFJ/QSS), and each
    dependent as one exemption each. The taxpayer is always one; MFS counts
    only the taxpayer (spouse claims their own on a separate return).
    """
    count = 1  # taxpayer
    if federal.filing_status in (FilingStatus.MFJ, FilingStatus.QSS):
        count += 1  # spouse
    count += max(0, federal.num_dependents)
    return count


# ---------------------------------------------------------------------------
# TY2025 local (county) income tax rate table
# ---------------------------------------------------------------------------

# Flat-rate jurisdictions. Keys are normalized county names (lower-case, no
# punctuation, no "county" suffix). Values are the TY2025 local rate.
#
# Sources:
#   - Maryland Withholding Tax Facts 2025 (COM/RAD 098 rev 07/25) "County
#     Rates" table, page 1.
#   - 2025 Maryland Tax Alert (Rev. 12/22/2025) Section II.A "Local Rate
#     Changes - Tax Year 2025" — Anne Arundel progressive, Calvert 3.20%,
#     Cecil 2.74%, Dorchester 3.30% (retroactive hike), St. Mary's 3.20%.
#
# Anne Arundel and Frederick have progressive brackets — modelled separately.
MD_TY2025_FLAT_COUNTY_RATES: dict[str, Decimal] = {
    "allegany": Decimal("0.0303"),
    "baltimore city": Decimal("0.0320"),
    "baltimore": Decimal("0.0320"),  # Baltimore County (not City)
    "baltimore county": Decimal("0.0320"),
    "calvert": Decimal("0.0320"),
    "caroline": Decimal("0.0320"),
    "carroll": Decimal("0.0303"),
    "cecil": Decimal("0.0274"),
    "charles": Decimal("0.0303"),
    "dorchester": Decimal("0.0330"),
    "garrett": Decimal("0.0265"),
    "harford": Decimal("0.0306"),
    "howard": Decimal("0.0320"),
    "kent": Decimal("0.0320"),
    "montgomery": Decimal("0.0320"),
    "prince george's": Decimal("0.0320"),
    "prince georges": Decimal("0.0320"),
    "queen anne's": Decimal("0.0320"),
    "queen annes": Decimal("0.0320"),
    "somerset": Decimal("0.0320"),
    "st. mary's": Decimal("0.0320"),
    "st marys": Decimal("0.0320"),
    "saint marys": Decimal("0.0320"),
    "talbot": Decimal("0.0240"),
    "washington": Decimal("0.0295"),
    "wicomico": Decimal("0.0320"),
    "worcester": Decimal("0.0225"),
}

# Out-of-state / nonresident default rate when the taxpayer has no MD county.
# Per Withholding Tax Facts 2025: "Nonresidents ... an additional state tax is
# withheld using the lowest local tax rate of .0225".
MD_TY2025_NONRESIDENT_DEFAULT_LOCAL_RATE: Decimal = Decimal("0.0225")

# Anne Arundel progressive brackets (TY2025, per Tax Alert Section II.A).
#
# Schedule I (Single / MFS / Dep):
#   MD taxable net income $1        - $50,000    : 2.70%
#                         $50,001   - $400,000   : 2.94%
#                         > $400,000             : 3.20%
#
# Schedule II (MFJ / HOH / QSS):
#   MD taxable net income $1        - $75,000    : 2.70%
#                         $75,001   - $480,000   : 2.94%
#                         > $480,000             : 3.20%
#
# Each tuple: (upper_bound_or_None, rate). Tax = sum over brackets.
MD_TY2025_ANNE_ARUNDEL_BRACKETS_SCHEDULE_I: tuple[
    tuple[Decimal | None, Decimal], ...
] = (
    (Decimal("50000"),   Decimal("0.0270")),
    (Decimal("400000"),  Decimal("0.0294")),
    (None,               Decimal("0.0320")),
)

MD_TY2025_ANNE_ARUNDEL_BRACKETS_SCHEDULE_II: tuple[
    tuple[Decimal | None, Decimal], ...
] = (
    (Decimal("75000"),   Decimal("0.0270")),
    (Decimal("480000"),  Decimal("0.0294")),
    (None,               Decimal("0.0320")),
)

# Frederick progressive brackets (TY2025, per Withholding Tax Facts 2025
# Schedule footnote ** "Frederick Co. The local tax rates for tax year 2025
# are as follows"):
#
# Schedule I (Single / MFS / Dep):
#   MD taxable net income $1           -  $25,000    : 2.25%
#                         $25,001      -  $50,000    : 2.75%
#                         $50,001      -  $150,000   : 2.96%
#                         $150,001     -  $250,000   : 3.03%
#                         > $250,000                  : 3.20%
#
# Schedule II (MFJ / HOH / QSS):
#   MD taxable net income $1           -  $25,000    : 2.25%
#                         $25,001      -  $100,000   : 2.75%
#                         $100,001     -  $250,000   : 2.96%
#                         > $250,000                  : 3.20%  (four-tier)
#
# NOTE the Withholding Tax Facts 2025 Frederick MFJ schedule has four tiers
# (not five) — the 3.03% bracket is Single-only.
MD_TY2025_FREDERICK_BRACKETS_SCHEDULE_I: tuple[
    tuple[Decimal | None, Decimal], ...
] = (
    (Decimal("25000"),   Decimal("0.0225")),
    (Decimal("50000"),   Decimal("0.0275")),
    (Decimal("150000"),  Decimal("0.0296")),
    (Decimal("250000"),  Decimal("0.0303")),
    (None,               Decimal("0.0320")),
)

MD_TY2025_FREDERICK_BRACKETS_SCHEDULE_II: tuple[
    tuple[Decimal | None, Decimal], ...
] = (
    (Decimal("25000"),   Decimal("0.0225")),
    (Decimal("100000"),  Decimal("0.0275")),
    (Decimal("250000"),  Decimal("0.0296")),
    (None,               Decimal("0.0320")),
)


def _normalize_county(name: str | None) -> str | None:
    """Canonical form: lower-case, stripped, 'county' suffix removed."""
    if name is None:
        return None
    out = name.strip().lower()
    if out.endswith(" county"):
        out = out[: -len(" county")]
    return out


def _progressive_county_tax(
    taxable_net_income: Decimal,
    brackets: tuple[tuple[Decimal | None, Decimal], ...],
) -> Decimal:
    """Apply a sum-of-tiers progressive county tax.

    Each tuple is (upper_bound_or_None, rate). The first tier spans 0..upper,
    the second spans previous_upper..upper, etc.
    """
    if taxable_net_income <= 0:
        return Decimal("0")
    tax = Decimal("0")
    prev_upper = Decimal("0")
    for upper, rate in brackets:
        if upper is None or taxable_net_income <= upper:
            tax += (taxable_net_income - prev_upper) * rate
            return _cents(tax)
        tax += (upper - prev_upper) * rate
        prev_upper = upper
    return _cents(tax)


def _md_local_tax(
    taxable_net_income: Decimal,
    county: str | None,
    filing_status: FilingStatus,
) -> tuple[Decimal, Decimal, str]:
    """Compute MD local (county) tax.

    Returns a 3-tuple:
        (local_tax, effective_local_rate, local_tax_note)

    ``effective_local_rate`` is the flat rate for flat-rate counties and the
    *average* effective rate (tax/income) for progressive counties (Anne
    Arundel, Frederick). If ``county`` is None or unrecognized, returns the
    2.25% nonresident default rate and flags it in ``local_tax_note``.
    """
    if taxable_net_income <= 0:
        return Decimal("0"), Decimal("0"), "zero taxable income"

    key = _normalize_county(county)
    if key is None:
        local_tax = _cents(taxable_net_income * MD_TY2025_NONRESIDENT_DEFAULT_LOCAL_RATE)
        return (
            local_tax,
            MD_TY2025_NONRESIDENT_DEFAULT_LOCAL_RATE,
            "no county supplied — applied 2.25% nonresident default",
        )

    if key == "anne arundel":
        if filing_status in (FilingStatus.SINGLE, FilingStatus.MFS):
            brackets = MD_TY2025_ANNE_ARUNDEL_BRACKETS_SCHEDULE_I
        else:
            brackets = MD_TY2025_ANNE_ARUNDEL_BRACKETS_SCHEDULE_II
        local_tax = _progressive_county_tax(taxable_net_income, brackets)
        eff = (local_tax / taxable_net_income) if taxable_net_income > 0 else Decimal("0")
        return (
            local_tax,
            eff,
            "Anne Arundel progressive county tax (2.70% / 2.94% / 3.20%)",
        )

    if key == "frederick":
        if filing_status in (FilingStatus.SINGLE, FilingStatus.MFS):
            brackets = MD_TY2025_FREDERICK_BRACKETS_SCHEDULE_I
        else:
            brackets = MD_TY2025_FREDERICK_BRACKETS_SCHEDULE_II
        local_tax = _progressive_county_tax(taxable_net_income, brackets)
        eff = (local_tax / taxable_net_income) if taxable_net_income > 0 else Decimal("0")
        return (
            local_tax,
            eff,
            "Frederick progressive county tax (2.25% / 2.75% / 2.96% / 3.03% / 3.20%)",
        )

    if key in MD_TY2025_FLAT_COUNTY_RATES:
        rate = MD_TY2025_FLAT_COUNTY_RATES[key]
        return (_cents(taxable_net_income * rate), rate, f"{key} flat rate {rate}")

    # Unknown county — fall back to nonresident default and flag it loudly.
    local_tax = _cents(taxable_net_income * MD_TY2025_NONRESIDENT_DEFAULT_LOCAL_RATE)
    return (
        local_tax,
        MD_TY2025_NONRESIDENT_DEFAULT_LOCAL_RATE,
        f"unknown county {county!r} — applied 2.25% nonresident default",
    )


# ---------------------------------------------------------------------------
# V1 limitations (documented loudly)
# ---------------------------------------------------------------------------


MD_V1_LIMITATIONS: tuple[str, ...] = (
    "MD additions and subtractions from federal AGI not applied (Form 502 "
    "lines 5-17: muni bond interest addback, state retirement pickup "
    "additions, federal bond interest subtraction, pension exclusion, "
    "military retirement exclusion, two-income MFJ subtraction, etc.). "
    "MD AGI approximated as federal AGI.",
    "Personal exemption phase-out not applied (Form 502 Exemption Worksheet "
    "for Part C): $3,200 at FAGI < $100k / $150k MFJ, declining linearly "
    "to $0 at FAGI > $200k / $350k MFJ. v1 uses the flat $3,200 per "
    "exemption withholding default.",
    "Itemized deduction phase-out not applied (7.5% of FAGI over $200k, "
    "or $100k MFS, per the 2025 Tax Alert Section IV.B). v1 uses the "
    "standard deduction only.",
    "2% capital gains surtax for federal AGI > $350,000 not applied (TY2025+ "
    "per the 2025 Tax Alert Section III).",
    "Local tax assumes the caller provides ``md_county`` in state_specific "
    "inputs OR applies the 2.25% nonresident default. The plugin does NOT "
    "look up the county from the address.",
    "Dorchester County retroactive rate hike (3.20% -> 3.30%) for TY2025 is "
    "modelled (only Dorchester notified the Comptroller by May 15, 2025). "
    "Other counties default to their Withholding Tax Facts 2025 rate.",
    "MD refundable credits (state EITC, state CTC, refundable dependent "
    "care) and nonrefundable credits (Form 502CR) not applied.",
    "MD nonresident tax on MD-source income (Form 505) not modelled — "
    "v1 uses day-based proration of the resident tax instead of the "
    "Form 505 MD-source income ratio.",
    "Part-year resident apportionment by period of residency (Form 502 "
    "line 16a MD AGI for period of residency) not modelled — v1 uses "
    "day-based proration.",
    "Income-based phase-in of the old standard deduction has been replaced "
    "by flat values ($3,350 / $6,700) per the 2025 Tax Alert Section "
    "IV.A. v1 does NOT apply the 15%-of-MD-AGI formula that was in effect "
    "through TY2024.",
)


# ---------------------------------------------------------------------------
# Days-based apportionment helper
# ---------------------------------------------------------------------------


def _apportionment_fraction(
    residency: ResidencyStatus, days_in_state: int
) -> Decimal:
    """Days-based apportionment for nonresident / part-year.

    Residents get 1.0. Nonresidents and part-year residents are prorated by
    ``days_in_state / 365``. Clamped to [0, 1].

    TODO: Replace with real MD Form 505 nonresident calculation, which
    prorates by MD-source income / total income (wages sourced to the work
    location, investment income sourced to domicile, rental to property
    state). Day-based proration is the shared first-cut across all fan-out
    state plugins.
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


# ---------------------------------------------------------------------------
# Layer 1: MD Form 502 field dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MD502Fields:
    """Frozen snapshot of MD Form 502 line values, ready for rendering."""

    md_adjusted_gross_income: Decimal = Decimal("0")
    md_standard_deduction: Decimal = Decimal("0")
    md_taxable_net_income: Decimal = Decimal("0")
    state_tax_only: Decimal = Decimal("0")
    local_tax: Decimal = Decimal("0")
    state_total_tax: Decimal = Decimal("0")


def _build_502_fields(state_return: StateReturn) -> MD502Fields:
    """Map StateReturn.state_specific to MD502Fields."""
    ss = state_return.state_specific
    return MD502Fields(
        md_adjusted_gross_income=ss.get("md_adjusted_gross_income", Decimal("0")),
        md_standard_deduction=ss.get("md_standard_deduction", Decimal("0")),
        md_taxable_net_income=ss.get("md_taxable_net_income", Decimal("0")),
        state_tax_only=ss.get("state_tax_only", Decimal("0")),
        local_tax=ss.get("local_tax", Decimal("0")),
        state_total_tax=ss.get("state_total_tax", Decimal("0")),
    )


@dataclass(frozen=True)
class MarylandPlugin:
    """State plugin for Maryland.

    Hand-rolled (tenforty does not support MD_502 for any year). Computes
    MD state tax from TY2025 bracket schedules, applies the new flat
    standard deduction and a flat personal exemption, and adds county
    local tax from a rate table keyed by county name. v1 limitations are
    enumerated in ``MD_V1_LIMITATIONS`` and surfaced on ``state_specific``.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        """Protocol-conformant compute.

        Looks for a county on the canonical return address (not currently a
        first-class field on ``Address`` — a v1 limitation), falling back
        to the 2.25% nonresident default. For county-aware calculations,
        call :meth:`compute_with_county` directly with the county name.
        """
        county: str | None = None
        addr = return_.address
        if addr is not None:
            county = getattr(addr, "county", None)
        return self.compute_with_county(
            return_, federal, residency, days_in_state, county=county
        )

    def compute_with_county(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
        *,
        county: str | None,
    ) -> StateReturn:
        """Compute with an explicit county override.

        ``county`` may be any of the keys in ``MD_TY2025_FLAT_COUNTY_RATES``,
        the special strings ``"Anne Arundel"`` or ``"Frederick"`` (for the
        progressive-rate counties), or ``None`` to apply the 2.25%
        nonresident/out-of-state default rate.
        """
        fs = federal.filing_status

        # Start from federal AGI (MD Form 502 line 1 = FAGI, then additions/
        # subtractions we don't model in v1). MD AGI approximated as FAGI.
        md_agi = max(Decimal("0"), federal.adjusted_gross_income)

        std_ded = _md_standard_deduction(fs)
        n_exemptions = _personal_exemptions(federal)
        pe_total = (
            MD_TY2025_PERSONAL_EXEMPTION_WITHHOLDING_DEFAULT * Decimal(n_exemptions)
        )

        md_taxable_net_income = max(Decimal("0"), md_agi - std_ded - pe_total)

        state_tax_full = _md_state_tax(md_taxable_net_income, fs)
        local_tax_full, local_rate_effective, local_tax_note = _md_local_tax(
            md_taxable_net_income, county, fs
        )
        total_tax_full = _cents(state_tax_full + local_tax_full)

        # Apportion for nonresident / part-year. TODO: replace with real
        # Form 505 MD-source-income ratio.
        fraction = _apportionment_fraction(residency, days_in_state)
        state_tax_apportioned = _cents(state_tax_full * fraction)
        local_tax_apportioned = _cents(local_tax_full * fraction)
        total_tax_apportioned = _cents(total_tax_full * fraction)

        state_specific: dict[str, Any] = {
            "starting_point": "federal_agi",
            "md_adjusted_gross_income": _cents(md_agi),
            "md_standard_deduction": _cents(std_ded),
            "md_personal_exemptions": n_exemptions,
            "md_personal_exemption_total": _cents(pe_total),
            "md_taxable_net_income": _cents(md_taxable_net_income),
            "state_tax_only": state_tax_apportioned,
            "state_tax_only_resident_basis": state_tax_full,
            "local_tax": local_tax_apportioned,
            "local_tax_resident_basis": local_tax_full,
            "local_county": county,
            "local_county_rate_effective": local_rate_effective,
            "local_tax_note": local_tax_note,
            "state_total_tax": total_tax_apportioned,
            "state_total_tax_resident_basis": total_tax_full,
            "apportionment_fraction": fraction,
            "v1_limitations": list(MD_V1_LIMITATIONS),
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
        """Split canonical income into MD-source vs non-MD-source.

        Residents: everything is MD-source. Nonresident / part-year:
        prorate each category by days_in_state / 365.

        TODO: MD actually sources each income type via Form 505 NR
        (nonresident) — wages to the work location, interest/dividends to
        domicile, rental to property state. Day-based proration is the
        shared first-cut across fan-out state plugins.
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
        _WIDGET_MAP = _REF / "md-502-acroform-map.json"
        _SOURCE_PDF = _REF / "state_forms" / "md_502.pdf"

        wmap = load_widget_map(_WIDGET_MAP)
        fetch_and_verify_source_pdf(
            _SOURCE_PDF, wmap.source_pdf_url, wmap.source_pdf_sha256
        )

        fields = _build_502_fields(state_return)
        widget_values: dict[str, str] = {}
        for sem_name, value in asdict(fields).items():
            widget_names = wmap.widget_names_for(sem_name)
            if not widget_names:
                continue
            text = format_money(value) if isinstance(value, Decimal) else str(value) if value else ""
            for wn in widget_names:
                widget_values[wn] = text

        out_path = out_dir / "md_502.pdf"
        fill_acroform_pdf(_SOURCE_PDF, widget_values, out_path)
        return [out_path]

    def form_ids(self) -> list[str]:
        return ["MD Form 502"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = MarylandPlugin(
    meta=StatePluginMeta(
        code="MD",
        name="Maryland",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://www.marylandtaxes.gov/individual/income/",
        free_efile_url=(
            "https://interactive.marylandtaxes.gov/Individuals/iFile_ChooseForm/default.asp"
        ),
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # Bilateral reciprocity partners — verified against
        # skill/reference/state-reciprocity.json.
        reciprocity_partners=("DC", "PA", "VA", "WV"),
        supported_tax_years=(2025,),
        notes=(
            "Hand-rolled MD Form 502 calc (tenforty does NOT support "
            "2025/MD_502 for any year despite OTSState.MD existing in the "
            "enum). TY2025 uses the new flat standard deduction ($3,350 / "
            "$6,700) and two new state brackets 6.25% / 6.50% above the "
            "$500k / $1M single (or $600k / $1.2M joint) thresholds per "
            "the 2025 Budget Reconciliation and Financing Act. Local "
            "county tax is modelled from a 24-jurisdiction rate table "
            "with progressive brackets for Anne Arundel and Frederick. "
            "Residents submit via iFile or a commercial piggyback."
        ),
    )
)
