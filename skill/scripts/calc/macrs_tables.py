"""MACRS (Modified Accelerated Cost Recovery System) depreciation tables.

Source
------
IRS Publication 946, "How To Depreciate Property" (rev. 2024), Appendix A
tables. Percentages below are taken verbatim from Pub 946 Table A-1
(3/5/7/10-year half-year convention), Table A-6 (residential rental, 27.5
year mid-month) and Table A-7a (nonresidential real property, 39-year
mid-month), all of which are stable across TY2024 → TY2025 — MACRS
recovery percentages are set by statute (IRC §168) and have not changed.

* https://www.irs.gov/pub/irs-pdf/p946.pdf — Pub 946 (2024)
* https://www.irs.gov/forms-pubs/about-publication-946

Scope and assumptions
---------------------
* **Half-year convention** (HY) is used for 3/5/7/10/15/20-year GDS classes.
  This is the default convention for personal property and matches the
  Pub 946 Table A-1 percentages. Mid-quarter convention is NOT implemented
  here — Wave 6 does not handle the >40% rule that forces MQ. If more than
  40 percent of the aggregate basis of MACRS property is placed in service
  in the fourth quarter, Pub 946 Table A-2..A-5 must be used instead; that
  is deferred to a follow-up wave.
* **200% declining balance** switching to straight-line is baked into the
  3/5/7/10-year tables. **150% DB** is baked into 15/20-year tables.
  Callers do NOT need to pick a method — the table selects GDS/ADS for
  them.
* **27.5-year residential rental** and **39-year nonresidential real**
  use the **mid-month convention**. The tables here use the first-month
  column from Pub 946 Table A-6 / A-7a, which is the common case
  (property placed in service January). Fan-out can add full month
  dispatch (the IRS table has 12 columns, one per placed-in-service
  month); for now this wave pins "month 1" and documents the limitation
  in the docstring of :func:`macrs_depreciation_percentage`.
* All percentages are stored as :class:`decimal.Decimal` strings so that
  arithmetic is deterministic and free of float rounding errors.

Public API
----------
* :data:`MACRS_HALF_YEAR` — ``dict[str, list[Decimal]]`` keyed by class
  life (``"3"``, ``"5"``, ``"7"``, ``"10"``, ``"15"``, ``"20"``).
  Each list is indexed by **year of service** (``0`` = first year).
  Values are fractions of the original depreciable basis (e.g.
  ``Decimal("0.2000")`` for year 1 of a 5-year asset).
* :data:`MACRS_MID_MONTH` — ``dict[str, list[Decimal]]`` keyed by class
  life (``"27.5"``, ``"39"``). Values are first-month-in-service
  percentages (Pub 946 Table A-6 column 1 / Table A-7a column 1).
* :func:`macrs_depreciation_percentage` — look up the percentage for a
  single (class_life, year_of_service) pair. Raises ``ValueError`` on
  unknown class life or out-of-range year.
"""
from __future__ import annotations

from decimal import Decimal

# ---------------------------------------------------------------------------
# Half-year convention tables (Pub 946 Table A-1)
#
# Each list covers year 1 through year N (N = recovery_period + 1 for the
# HY convention, because the HY convention stretches depreciation over
# one extra partial year). Values sum to Decimal("1.0000") ± rounding.
# ---------------------------------------------------------------------------

MACRS_HALF_YEAR: dict[str, list[Decimal]] = {
    # 3-year property: tools, racehorses, qualified rent-to-own property.
    # 200% DB switching to SL. Total = 100.00%.
    "3": [
        Decimal("0.3333"),
        Decimal("0.4445"),
        Decimal("0.1481"),
        Decimal("0.0741"),
    ],
    # 5-year property: autos, computers, office machinery, trucks.
    # 200% DB switching to SL. Total = 100.00%.
    "5": [
        Decimal("0.2000"),
        Decimal("0.3200"),
        Decimal("0.1920"),
        Decimal("0.1152"),
        Decimal("0.1152"),
        Decimal("0.0576"),
    ],
    # 7-year property: office furniture, equipment not otherwise classed.
    # 200% DB switching to SL. Total = 100.00%.
    "7": [
        Decimal("0.1429"),
        Decimal("0.2449"),
        Decimal("0.1749"),
        Decimal("0.1249"),
        Decimal("0.0893"),
        Decimal("0.0892"),
        Decimal("0.0893"),
        Decimal("0.0446"),
    ],
    # 10-year property: water transportation equipment, single-purpose
    # agricultural structures. 200% DB switching to SL.
    "10": [
        Decimal("0.1000"),
        Decimal("0.1800"),
        Decimal("0.1440"),
        Decimal("0.1152"),
        Decimal("0.0922"),
        Decimal("0.0737"),
        Decimal("0.0655"),
        Decimal("0.0655"),
        Decimal("0.0656"),
        Decimal("0.0655"),
        Decimal("0.0328"),
    ],
    # 15-year property: qualified improvement property (post-TCJA),
    # land improvements. 150% DB switching to SL.
    "15": [
        Decimal("0.0500"),
        Decimal("0.0950"),
        Decimal("0.0855"),
        Decimal("0.0770"),
        Decimal("0.0693"),
        Decimal("0.0623"),
        Decimal("0.0590"),
        Decimal("0.0590"),
        Decimal("0.0591"),
        Decimal("0.0590"),
        Decimal("0.0591"),
        Decimal("0.0590"),
        Decimal("0.0591"),
        Decimal("0.0590"),
        Decimal("0.0591"),
        Decimal("0.0295"),
    ],
    # 20-year property: farm buildings (not single-purpose). 150% DB
    # switching to SL.
    "20": [
        Decimal("0.0375"),
        Decimal("0.0722"),
        Decimal("0.0668"),
        Decimal("0.0618"),
        Decimal("0.0571"),
        Decimal("0.0528"),
        Decimal("0.0489"),
        Decimal("0.0452"),
        Decimal("0.0447"),
        Decimal("0.0447"),
        Decimal("0.0446"),
        Decimal("0.0446"),
        Decimal("0.0446"),
        Decimal("0.0446"),
        Decimal("0.0446"),
        Decimal("0.0446"),
        Decimal("0.0446"),
        Decimal("0.0446"),
        Decimal("0.0446"),
        Decimal("0.0446"),
        Decimal("0.0223"),
    ],
}

# ---------------------------------------------------------------------------
# Mid-month convention tables (Pub 946 Table A-6 / A-7a, month 1 column)
#
# First-year percentage is the only one that varies by placed-in-service
# month; this wave pins month 1 (January) and uses the constant per-year
# percentages for years 2+. Full month-1..month-12 dispatch is deferred.
# ---------------------------------------------------------------------------

# 27.5-year residential rental property (Table A-6). Straight-line, mid-month.
# First-year percentage for month 1: 3.485%. Years 2-28: 3.636% each.
# Year 29 closes out the remaining 1.970% (property placed in service Jan
# is fully depreciated by the start of year 29).
_RESIDENTIAL_YEAR_MIDDLE = Decimal("0.03636")
MACRS_MID_MONTH_RESIDENTIAL_27_5: list[Decimal] = (
    [Decimal("0.03485")]
    + [_RESIDENTIAL_YEAR_MIDDLE] * 27
    + [Decimal("0.01970")]
)

# 39-year nonresidential real property (Table A-7a). Straight-line, mid-month.
# First-year percentage for month 1: 2.461%. Years 2-39: 2.564% each.
# Year 40 closes out: 0.107%.
_NONRES_YEAR_MIDDLE = Decimal("0.02564")
MACRS_MID_MONTH_NONRES_39: list[Decimal] = (
    [Decimal("0.02461")]
    + [_NONRES_YEAR_MIDDLE] * 38
    + [Decimal("0.00107")]
)

MACRS_MID_MONTH: dict[str, list[Decimal]] = {
    "27.5": MACRS_MID_MONTH_RESIDENTIAL_27_5,
    "39": MACRS_MID_MONTH_NONRES_39,
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


_ALL_TABLES: dict[str, list[Decimal]] = {
    **MACRS_HALF_YEAR,
    **MACRS_MID_MONTH,
}

_VALID_CLASSES: tuple[str, ...] = tuple(_ALL_TABLES.keys())


def macrs_depreciation_percentage(class_life: str, year_of_service: int) -> Decimal:
    """Return the MACRS depreciation percentage for a single year.

    Parameters
    ----------
    class_life
        One of ``"3"``, ``"5"``, ``"7"``, ``"10"``, ``"15"``, ``"20"``
        (half-year convention) or ``"27.5"``, ``"39"`` (mid-month
        convention, month 1 column).
    year_of_service
        Zero-indexed year of service. ``0`` is the first tax year the
        asset is placed in service.

    Returns
    -------
    Decimal
        Fraction of the original depreciable basis deductible in this
        year (e.g. ``Decimal("0.2000")`` for year 1 of a 5-year asset).
        Returns ``Decimal("0")`` if ``year_of_service`` is past the end
        of the depreciation schedule (fully depreciated).

    Raises
    ------
    ValueError
        If ``class_life`` is not one of the supported class lives or
        ``year_of_service`` is negative.
    """
    if class_life not in _ALL_TABLES:
        raise ValueError(
            f"unknown MACRS class life: {class_life!r}. "
            f"Supported: {_VALID_CLASSES}"
        )
    if year_of_service < 0:
        raise ValueError(
            f"year_of_service must be >= 0, got {year_of_service}"
        )
    table = _ALL_TABLES[class_life]
    if year_of_service >= len(table):
        return Decimal("0")
    return table[year_of_service]


def macrs_full_depreciation(
    class_life: str, basis: Decimal, year_of_service: int
) -> Decimal:
    """Return the MACRS depreciation dollar amount for one year.

    Convenience wrapper around :func:`macrs_depreciation_percentage`.
    Quantizes the result to two decimal places. ``basis`` is the
    depreciable basis (cost minus §179 and bonus depreciation already
    claimed).
    """
    pct = macrs_depreciation_percentage(class_life, year_of_service)
    return (basis * pct).quantize(Decimal("0.01"))
