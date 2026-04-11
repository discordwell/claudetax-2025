"""Form 8829 — Expenses for Business Use of Your Home — compute + render.

Wave 6 Agent 5 — the home-office deduction module.

Filers with a Schedule C business who use part of their home regularly
and exclusively for business can deduct home-office expenses. TY2025
offers two methods:

1. **Simplified method** — $5 per business square foot, capped at
   300 sq ft / $1,500. No Form 8829 is filed; the deduction is reported
   directly on Schedule C line 30.

2. **Regular method** — Form 8829 with actual expenses × business-use
   percentage (of home). Subject to the **gross-income limitation**
   (Part II line 8 cap): allowable deduction cannot exceed the Sch C
   line 29 tentative profit net of any income/loss outside the home
   office. Excess unallowed expenses carry forward (Part IV) to next
   year's Form 8829.

Public surface (three layers)
-----------------------------

* **Layer 0 — dispatcher** (``compute_home_office_deduction``) —
  returns the Schedule C line 30 amount for one ``HomeOffice`` block
  under one ``ScheduleC``. Handles both simplified and regular method.

* **Layer 1 — field computation** (``compute_form_8829_fields``) —
  regular-method only. Walks Part I (business %) → Part II (allowable
  deduction with gross-income cap) → Part III (home depreciation
  mini-worksheet) → Part IV (carryovers), emitting a frozen
  ``Form8829Fields`` dataclass with one attribute per line.

* **Layer 2 — PDF rendering** (``render_form_8829_pdf``) — regular
  method only. Fills the IRS fillable f8829.pdf using the wave-5
  AcroForm overlay helper and the widget map at
  ``skill/reference/form-8829-acroform-map.json``.

The dispatcher is the boundary the pipeline and engine callers hit.
Layer 1 and Layer 2 are only used when the filer chose the regular
method.

Gross-income limitation (the tricky bit)
----------------------------------------
Form 8829 line 8 is NOT raw Schedule C line 29 — it's "Schedule C line
29 PLUS any gain derived from the business use of your home, MINUS any
loss from the trade or business not derived from the business use of
your home." For the common single-home-office, single-business case
this simplifies to Sch C line 29 (tentative profit before the home-
office deduction). We implement that simplification; exotic dual-source
home-office returns need a manual adjustment on ``HomeOffice`` which
is tracked as a future extension.

Depreciation (Part III — mid-month convention)
----------------------------------------------
Home offices use 39-year nonresidential real property MACRS (straight
line, mid-month convention). The steady-state per-year depreciation
percentage is 1/39 ≈ 2.564%. First-year (year home was placed in
service) uses the mid-month table — the dollar percentage depends on
the month of purchase:

    month  %
      1    2.461
      2    2.247
      3    2.033
      4    1.819
      5    1.605
      6    1.391
      7    1.177
      8    0.963
      9    0.749
     10    0.535
     11    0.321
     12    0.107

For years 2-39 the steady-state 2.564% applies. For year 40 (the
partial final year) the mid-month residual applies; we defer that
because a single home rarely stays in service for 39 years with the
same home-office percentage.

If the filer does not supply ``home_purchase_price`` / ``home_land_value``
or the computed business-basis is zero, Part III is emitted with all
zeros and line 42 depreciation is $0 (i.e. the filer is only taking
operating-expense deductions).

Carryovers
----------
Line 25 picks up the prior year's operating-expense carryover
(``HomeOffice.prior_year_operating_carryover``). Line 31 picks up the
prior year's excess-casualty/depreciation carryover
(``HomeOffice.prior_year_excess_casualty_depreciation_carryover``).
Lines 43 and 44 emit the current year's carryovers to next year.

Coordination with Form 4562 (Agent 4)
-------------------------------------
Form 4562 handles general business depreciation (Schedule C line 13).
Form 8829's home-portion depreciation lives entirely on Form 8829 (Part
III line 42 → Part II line 30 → flows through line 36 into Schedule C
line 30, NOT line 13). The two modules do not overlap — a taxpayer may
file both, but neither writes to the other's line.

References
----------
* IRS 2025 Form 8829 — Expenses for Business Use of Your Home
  https://www.irs.gov/pub/irs-pdf/f8829.pdf
* IRS 2024 Instructions for Form 8829 (TY2025 uses the same
  instructions with year-stamp updates)
  https://www.irs.gov/pub/irs-pdf/i8829.pdf
* Publication 587 — Business Use of Your Home
  https://www.irs.gov/pub/irs-pdf/p587.pdf
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from skill.scripts.models import CanonicalReturn, HomeOffice, ScheduleC


_ZERO = Decimal("0")
_CENTS = Decimal("0.01")
_PERCENT_QUANT = Decimal("0.0001")


# Simplified-method constants (IRS §280A(c)(5); Rev. Proc. 2013-13)
SIMPLIFIED_METHOD_RATE = Decimal("5.00")  # $/sq ft
SIMPLIFIED_METHOD_MAX_SQ_FT = Decimal("300")
SIMPLIFIED_METHOD_CAP = Decimal("1500.00")

# Mid-month MACRS table for 39-year nonresidential real property —
# first-year percentages indexed by month placed in service (1..12).
# Source: IRS Pub. 946, Table A-7a.
_MID_MONTH_FIRST_YEAR_PCT: dict[int, Decimal] = {
    1: Decimal("0.02461"),
    2: Decimal("0.02247"),
    3: Decimal("0.02033"),
    4: Decimal("0.01819"),
    5: Decimal("0.01605"),
    6: Decimal("0.01391"),
    7: Decimal("0.01177"),
    8: Decimal("0.00963"),
    9: Decimal("0.00749"),
    10: Decimal("0.00535"),
    11: Decimal("0.00321"),
    12: Decimal("0.00107"),
}

# Steady-state depreciation percentage (1/39 rounded to 4dp) used for
# years 2 through 39 (full recovery-period years).
_STEADY_STATE_PCT: Decimal = Decimal("0.02564")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cents(value: Decimal) -> Decimal:
    """Round a Decimal to cents using banker's-rounding-free HALF_UP."""
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _sch_c_non_home_office_expenses(sc: ScheduleC) -> Decimal:
    """Total Sch C Part II expenses EXCLUDING the home office (line 30).

    This is the building block for the Form 8829 line 8 gross-income
    limit: Sch C line 29 = line 7 - line 28, where line 28 is Part II
    total expenses. We intentionally re-implement the formula here
    rather than importing from calc.engine because the Form 8829
    renderer lives in the `output` package and we do not want a
    circular dependency (`output` depends on `calc` via the engine
    helpers already, but `calc.engine` does NOT import from `output`).
    """
    from skill.scripts.calc.engine import _sch_c_total_expenses

    return _sch_c_total_expenses(sc.expenses)


def _sch_c_line_29_tentative_profit(sc: ScheduleC) -> Decimal:
    """Schedule C line 29 = line 7 - line 28 (BEFORE home-office line 30).

    This is the gross-income cap for Form 8829 line 8. When a home
    office exists, the caller has typically NOT yet populated
    ``sc.line30_home_office_expense`` (the whole point of Form 8829 is
    to compute that number). We therefore do NOT subtract line 30 here.
    """
    gross_income = (
        sc.line1_gross_receipts
        - sc.line2_returns_and_allowances
        - sc.line4_cost_of_goods_sold
        + sc.line6_other_income
    )
    return gross_income - _sch_c_non_home_office_expenses(sc)


# ---------------------------------------------------------------------------
# Layer 1 — Form 8829 fields dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Form8829Fields:
    """Frozen snapshot of every Form 8829 line value (regular method only).

    Field names mirror the 2025 Form 8829 line numbers. Currency values
    are rounded to two decimal places (cents). Percentages (lines 3, 7,
    41) are stored as their percentage-point value (e.g. ``Decimal("10")``
    for a 10% business use percentage) so the AcroForm overlay can
    print them with the ``%`` label pre-baked on the form.
    """

    # Header (filled by the Form 1040 header pass for proprietor_ssn)
    proprietor_name: str = ""
    proprietor_ssn: str | None = None

    # Part I — Part of Your Home Used for Business
    line_1_business_area: Decimal = _ZERO
    line_2_total_home_area: Decimal = _ZERO
    line_3_area_percentage: Decimal = _ZERO
    line_4_daycare_hours: Decimal = _ZERO
    line_5_total_hours: Decimal = _ZERO
    line_6_daycare_decimal: Decimal = _ZERO
    line_7_business_percentage: Decimal = _ZERO

    # Part II — Figure Your Allowable Deduction
    line_8_sch_c_line_29_tentative_profit: Decimal = _ZERO
    line_9a_casualty_direct: Decimal = _ZERO
    line_9b_casualty_indirect: Decimal = _ZERO
    line_10a_mortgage_interest_direct: Decimal = _ZERO
    line_10b_mortgage_interest_indirect: Decimal = _ZERO
    line_11a_real_estate_taxes_direct: Decimal = _ZERO
    line_11b_real_estate_taxes_indirect: Decimal = _ZERO
    line_12a_sum_9_10_11_direct: Decimal = _ZERO
    line_12b_sum_9_10_11_indirect: Decimal = _ZERO
    line_13_line_12b_times_line_7: Decimal = _ZERO
    line_14_sum_12a_plus_13: Decimal = _ZERO
    line_15_subtract_14_from_8: Decimal = _ZERO
    line_16a_excess_mortgage_interest_direct: Decimal = _ZERO
    line_16b_excess_mortgage_interest_indirect: Decimal = _ZERO
    line_17a_excess_real_estate_taxes_direct: Decimal = _ZERO
    line_17b_excess_real_estate_taxes_indirect: Decimal = _ZERO
    line_18a_insurance_direct: Decimal = _ZERO
    line_18b_insurance_indirect: Decimal = _ZERO
    line_19a_rent_direct: Decimal = _ZERO
    line_19b_rent_indirect: Decimal = _ZERO
    line_20a_repairs_direct: Decimal = _ZERO
    line_20b_repairs_indirect: Decimal = _ZERO
    line_21a_utilities_direct: Decimal = _ZERO
    line_21b_utilities_indirect: Decimal = _ZERO
    line_22a_other_expenses_direct: Decimal = _ZERO
    line_22b_other_expenses_indirect: Decimal = _ZERO
    line_23a_sum_16_through_22_direct: Decimal = _ZERO
    line_23b_sum_16_through_22_indirect: Decimal = _ZERO
    line_24_line_23b_times_line_7: Decimal = _ZERO
    line_25_carryover_prior_year_operating: Decimal = _ZERO
    line_26_sum_23a_24_25: Decimal = _ZERO
    line_27_allowable_operating_expenses: Decimal = _ZERO
    line_28_limit_on_excess_casualty_depreciation: Decimal = _ZERO
    line_29_excess_casualty_losses: Decimal = _ZERO
    line_30_depreciation_of_home: Decimal = _ZERO
    line_31_carryover_prior_year_excess_casualty_depreciation: Decimal = _ZERO
    line_32_sum_29_30_31: Decimal = _ZERO
    line_33_allowable_excess_casualty_depreciation: Decimal = _ZERO
    line_34_sum_14_27_33: Decimal = _ZERO
    line_35_casualty_loss_portion_to_4684: Decimal = _ZERO
    line_36_allowable_expenses_to_sch_c_line_30: Decimal = _ZERO

    # Part III — Depreciation of Your Home
    line_37_smaller_of_basis_or_fmv: Decimal = _ZERO
    line_38_value_of_land: Decimal = _ZERO
    line_39_basis_of_building: Decimal = _ZERO
    line_40_business_basis_of_building: Decimal = _ZERO
    line_41_depreciation_percentage: Decimal = _ZERO
    line_42_depreciation_allowable: Decimal = _ZERO

    # Part IV — Carryover of Unallowed Expenses
    line_43_carryover_operating_expenses: Decimal = _ZERO
    line_44_carryover_excess_casualty_depreciation: Decimal = _ZERO


# ---------------------------------------------------------------------------
# Layer 1 — computation
# ---------------------------------------------------------------------------


def _compute_business_percentage(ho: HomeOffice) -> tuple[Decimal, Decimal, Decimal]:
    """Return (line_3_area_pct, line_6_daycare_decimal, line_7_final_pct).

    Line 3 = line 1 / line 2 as a percentage (e.g. Decimal("10.00")).
    Line 6 = daycare hours / 8,760 as a decimal (non-daycare: 0).
    Line 7 = final business percentage. For non-daycare: = line 3.
             For daycare not used exclusively for business:
             = line 6 (decimal) * line 3 (percentage).
    """
    if ho.total_home_sq_ft <= 0:
        return _ZERO, _ZERO, _ZERO

    area_ratio = (ho.business_sq_ft / ho.total_home_sq_ft) * Decimal("100")
    line_3 = area_ratio.quantize(_CENTS, rounding=ROUND_HALF_UP)

    if ho.is_daycare_facility:
        total_hours = Decimal("8760")
        if total_hours > 0:
            line_6 = (ho.daycare_hours_per_year / total_hours).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
        else:
            line_6 = _ZERO
        line_7 = (line_6 * line_3).quantize(_CENTS, rounding=ROUND_HALF_UP)
    else:
        line_6 = _ZERO
        line_7 = line_3
    return line_3, line_6, line_7


def _first_year_depreciation_pct(ho: HomeOffice) -> Decimal:
    """Return the line 41 depreciation percentage as a decimal fraction.

    * If no ``home_purchase_date`` is supplied, we assume steady-state
      (i.e. the home has been in service for at least a full year) and
      return 2.564%.
    * If a purchase date is supplied and its tax year matches the
      taxpayer's TY (which we infer by "has the first-year flag been
      set"), we pick the mid-month first-year percentage.

    NOTE: The dispatcher does not know the tax year directly. For wave
    6 we use the heuristic "if `home_purchase_date` is set, use its
    month's mid-month percentage". This will compute the first-year
    deduction correctly for filers who populate the purchase date on
    their first home-office return, and would slightly over-deduct in
    year 2+. Filers who want the steady-state percentage after year 1
    should simply clear ``home_purchase_date`` — this is documented on
    the ``HomeOffice`` model. A future wave can tighten this by passing
    the tax year explicitly.
    """
    if ho.home_purchase_date is not None:
        month = int(ho.home_purchase_date.month)
        return _MID_MONTH_FIRST_YEAR_PCT.get(month, _STEADY_STATE_PCT)
    return _STEADY_STATE_PCT


def compute_form_8829_fields(
    home_office: HomeOffice, sch_c: ScheduleC
) -> Form8829Fields:
    """Walk Form 8829 Parts I-IV for one home office under one Schedule C.

    Only the regular method. Callers must check
    ``home_office.method == "regular"`` before invoking this function;
    the simplified method returns a flat ``$5/sq ft`` deduction without
    a Form 8829 filing.
    """
    if home_office.method != "regular":
        raise ValueError(
            f"compute_form_8829_fields only applies to the regular method; "
            f"got method={home_office.method!r}. Use "
            f"compute_home_office_deduction() for the simplified method."
        )

    # ------------------------------------------------------------------
    # Part I — business percentage
    # ------------------------------------------------------------------
    line_3, line_6, line_7 = _compute_business_percentage(home_office)
    # line_7 as a plain multiplier (e.g. 10% -> 0.10) for arithmetic
    line_7_mult = (line_7 / Decimal("100")).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP
    )

    # Daycare-facility line 5 convention: 8,760 is the "full year"
    # hours baseline the form literally pre-prints. Only populate when
    # the daycare flag is on, to keep non-daycare forms clean.
    if home_office.is_daycare_facility:
        line_4 = home_office.daycare_hours_per_year
        line_5 = Decimal("8760")
    else:
        line_4 = _ZERO
        line_5 = _ZERO

    # ------------------------------------------------------------------
    # Part II — gross-income limit (line 8)
    # ------------------------------------------------------------------
    line_8 = _cents(_sch_c_line_29_tentative_profit(sch_c))

    # Lines 9-12: casualty + mortgage interest + real estate taxes
    # (direct / indirect split). We do not model casualty here — Form
    # 4684 carries casualty into Form 8829 and out-of-scope for wave 6.
    line_9a = _ZERO
    line_9b = _ZERO
    line_10a = home_office.direct_mortgage_interest
    line_10b = home_office.mortgage_interest_total
    line_11a = home_office.direct_real_estate_taxes
    line_11b = home_office.real_estate_taxes_total
    line_12a = _cents(line_9a + line_10a + line_11a)
    line_12b = _cents(line_9b + line_10b + line_11b)
    line_13 = _cents(line_12b * line_7_mult)
    line_14 = _cents(line_12a + line_13)
    # Line 15 is the remaining gross-income cap AFTER line 14
    # (mortgage interest + RE taxes have priority because they're
    # Schedule A deductions if the home-office isn't claimed, so they
    # are NOT subject to the line 8 cap).
    line_15 = max(_ZERO, line_8 - line_14)

    # Lines 16-23: operating expenses (insurance, rent, repairs,
    # utilities, other). We do not split excess mortgage interest /
    # excess real estate taxes in wave 6 — those only matter when the
    # taxpayer's Schedule A SALT/mortgage-interest is already capped,
    # a rare case. Future extension.
    line_16a = _ZERO
    line_16b = _ZERO
    line_17a = _ZERO
    line_17b = _ZERO
    line_18a = home_office.direct_insurance
    line_18b = home_office.insurance_total
    line_19a = home_office.direct_rent
    line_19b = home_office.rent_total
    line_20a = home_office.direct_repairs
    line_20b = home_office.repairs_total
    line_21a = home_office.direct_utilities
    line_21b = home_office.utilities_total
    line_22a = home_office.direct_other_expenses
    line_22b = home_office.other_expenses_total
    line_23a = _cents(
        line_16a + line_17a + line_18a + line_19a + line_20a + line_21a + line_22a
    )
    line_23b = _cents(
        line_16b + line_17b + line_18b + line_19b + line_20b + line_21b + line_22b
    )
    line_24 = _cents(line_23b * line_7_mult)
    line_25 = _cents(home_office.prior_year_operating_carryover)
    line_26 = _cents(line_23a + line_24 + line_25)
    line_27 = min(line_15, line_26)
    line_28 = max(_ZERO, line_15 - line_27)

    # Lines 29-33: casualty + depreciation (Part III line 42 feeds 30)
    line_29 = _ZERO  # casualty losses — not modeled in wave 6
    # Depreciation of home (Part III computed first, then copied here)
    line_37, line_38, line_39, line_40, line_41, line_42 = _compute_part_iii(
        home_office, line_7_mult
    )
    line_30 = line_42  # copied from Part III line 42
    line_31 = _cents(home_office.prior_year_excess_casualty_depreciation_carryover)
    line_32 = _cents(line_29 + line_30 + line_31)
    line_33 = min(line_28, line_32)
    line_34 = _cents(line_14 + line_27 + line_33)
    line_35 = _ZERO  # casualty loss portion — not modeled
    line_36 = _cents(line_34 - line_35)

    # ------------------------------------------------------------------
    # Part IV — carryover
    # ------------------------------------------------------------------
    line_43 = max(_ZERO, line_26 - line_27)
    line_44 = max(_ZERO, line_32 - line_33)

    return Form8829Fields(
        proprietor_name=sch_c.business_name if not sch_c.proprietor_is_taxpayer else "",
        proprietor_ssn=None,
        line_1_business_area=home_office.business_sq_ft,
        line_2_total_home_area=home_office.total_home_sq_ft,
        line_3_area_percentage=line_3,
        line_4_daycare_hours=line_4,
        line_5_total_hours=line_5,
        line_6_daycare_decimal=line_6,
        line_7_business_percentage=line_7,
        line_8_sch_c_line_29_tentative_profit=line_8,
        line_9a_casualty_direct=line_9a,
        line_9b_casualty_indirect=line_9b,
        line_10a_mortgage_interest_direct=line_10a,
        line_10b_mortgage_interest_indirect=line_10b,
        line_11a_real_estate_taxes_direct=line_11a,
        line_11b_real_estate_taxes_indirect=line_11b,
        line_12a_sum_9_10_11_direct=line_12a,
        line_12b_sum_9_10_11_indirect=line_12b,
        line_13_line_12b_times_line_7=line_13,
        line_14_sum_12a_plus_13=line_14,
        line_15_subtract_14_from_8=line_15,
        line_16a_excess_mortgage_interest_direct=line_16a,
        line_16b_excess_mortgage_interest_indirect=line_16b,
        line_17a_excess_real_estate_taxes_direct=line_17a,
        line_17b_excess_real_estate_taxes_indirect=line_17b,
        line_18a_insurance_direct=line_18a,
        line_18b_insurance_indirect=line_18b,
        line_19a_rent_direct=line_19a,
        line_19b_rent_indirect=line_19b,
        line_20a_repairs_direct=line_20a,
        line_20b_repairs_indirect=line_20b,
        line_21a_utilities_direct=line_21a,
        line_21b_utilities_indirect=line_21b,
        line_22a_other_expenses_direct=line_22a,
        line_22b_other_expenses_indirect=line_22b,
        line_23a_sum_16_through_22_direct=line_23a,
        line_23b_sum_16_through_22_indirect=line_23b,
        line_24_line_23b_times_line_7=line_24,
        line_25_carryover_prior_year_operating=line_25,
        line_26_sum_23a_24_25=line_26,
        line_27_allowable_operating_expenses=line_27,
        line_28_limit_on_excess_casualty_depreciation=line_28,
        line_29_excess_casualty_losses=line_29,
        line_30_depreciation_of_home=line_30,
        line_31_carryover_prior_year_excess_casualty_depreciation=line_31,
        line_32_sum_29_30_31=line_32,
        line_33_allowable_excess_casualty_depreciation=line_33,
        line_34_sum_14_27_33=line_34,
        line_35_casualty_loss_portion_to_4684=line_35,
        line_36_allowable_expenses_to_sch_c_line_30=line_36,
        line_37_smaller_of_basis_or_fmv=line_37,
        line_38_value_of_land=line_38,
        line_39_basis_of_building=line_39,
        line_40_business_basis_of_building=line_40,
        line_41_depreciation_percentage=line_41,
        line_42_depreciation_allowable=line_42,
        line_43_carryover_operating_expenses=line_43,
        line_44_carryover_excess_casualty_depreciation=line_44,
    )


def _compute_part_iii(
    ho: HomeOffice, line_7_mult: Decimal
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]:
    """Return (line 37, 38, 39, 40, 41, 42) for Part III.

    If the filer didn't supply ``home_purchase_price``, Part III is
    emitted as all zeros (no home depreciation). The depreciation
    percentage is stored as a percentage (e.g. ``Decimal("2.564")``)
    because that's what the form literally shows next to the "%" label.
    """
    purchase_price = ho.home_purchase_price
    if purchase_price is None or purchase_price <= 0:
        return _ZERO, _ZERO, _ZERO, _ZERO, _ZERO, _ZERO

    land_value = ho.home_land_value or _ZERO
    line_37 = _cents(purchase_price)
    line_38 = _cents(land_value)
    line_39 = _cents(line_37 - line_38)
    line_40 = _cents(line_39 * line_7_mult)

    pct_fraction = _first_year_depreciation_pct(ho)
    # Percentage representation for the form: e.g. 0.02564 -> 2.564
    line_41 = (pct_fraction * Decimal("100")).quantize(
        Decimal("0.001"), rounding=ROUND_HALF_UP
    )
    line_42 = _cents(line_40 * pct_fraction)
    return line_37, line_38, line_39, line_40, line_41, line_42


# ---------------------------------------------------------------------------
# Layer 0 — dispatcher
# ---------------------------------------------------------------------------


def compute_home_office_deduction(
    home_office: HomeOffice, sch_c: ScheduleC
) -> Decimal:
    """Return the Schedule C line 30 deduction for one home office.

    Dispatcher between simplified and regular method. Used by the
    engine + pipeline to set ``sc.line30_home_office_expense`` BEFORE
    ``calc.engine.compute()`` runs.

    Simplified method: $5 per business sq ft, capped at $1,500
    (300 sq ft × $5). Also capped at the gross-income limit (if the
    filer has a Schedule C net loss, the simplified deduction is
    reduced to the tentative profit — never drives the business into
    a bigger loss).

    Regular method: delegates to ``compute_form_8829_fields`` and
    returns line 36.
    """
    if home_office.method == "simplified":
        sq_ft = min(home_office.business_sq_ft, SIMPLIFIED_METHOD_MAX_SQ_FT)
        base = sq_ft * SIMPLIFIED_METHOD_RATE
        base = min(base, SIMPLIFIED_METHOD_CAP)
        # Gross-income limit — simplified method cannot create a loss.
        cap = _sch_c_line_29_tentative_profit(sch_c)
        if cap < base:
            # Don't let the simplified deduction push Sch C negative.
            base = max(_ZERO, cap)
        return _cents(base)

    fields = compute_form_8829_fields(home_office, sch_c)
    return fields.line_36_allowable_expenses_to_sch_c_line_30


# ---------------------------------------------------------------------------
# Layer 2 — AcroForm overlay PDF rendering
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FORM_8829_MAP_PATH = (
    _REPO_ROOT / "skill" / "reference" / "form-8829-acroform-map.json"
)
_FORM_8829_PDF_PATH = (
    _REPO_ROOT / "skill" / "reference" / "irs_forms" / "f8829.pdf"
)


def _format_decimal(value: Decimal) -> str:
    """Format a cents-quantized Decimal as a plain ``"12345.67"`` string.

    Zero collapses to ``""`` so the rendered form stays visually blank
    for cells the filer doesn't use (matches wave-5 convention).
    """
    q = value.quantize(_CENTS, rounding=ROUND_HALF_UP)
    if q == _ZERO:
        return ""
    return f"{q:.2f}"


def _format_percentage(value: Decimal) -> str:
    """Format a percentage-valued Decimal (e.g. 10.00) as ``"10.00"``."""
    q = value.quantize(_CENTS, rounding=ROUND_HALF_UP)
    if q == _ZERO:
        return ""
    return f"{q:.2f}"


def _format_sq_ft(value: Decimal) -> str:
    """Format a sq-ft Decimal as an integer ``"400"``."""
    q = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if q == _ZERO:
        return ""
    return f"{q}"


def _format_depreciation_pct(value: Decimal) -> str:
    """Format the depreciation percentage (line 41) with 3 decimals."""
    q = value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    if q == _ZERO:
        return ""
    return f"{q:.3f}"


def _format_daycare_decimal(value: Decimal) -> str:
    """Format the line-6 daycare decimal (e.g. 0.5000)."""
    q = value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    if q == _ZERO:
        return ""
    return f"{q:.4f}"


def _build_widget_values(
    fields: Form8829Fields,
    widget_map: dict,
) -> dict[str, str]:
    """Translate ``Form8829Fields`` onto a {widget_name: str} dict."""
    mapping = widget_map["mapping"]
    out: dict[str, str] = {}

    def put(sem: str, value: str) -> None:
        entry = mapping.get(sem)
        if entry is None:
            return
        wn = entry["widget_name"]
        out[wn] = value

    # Header — proprietor_ssn is filled by Form 1040 header pass
    put("proprietor_name", fields.proprietor_name)

    # Part I — business percentage
    put("line_1_business_area", _format_sq_ft(fields.line_1_business_area))
    put("line_2_total_home_area", _format_sq_ft(fields.line_2_total_home_area))
    put(
        "line_3_area_percentage",
        _format_percentage(fields.line_3_area_percentage),
    )
    put("line_4_daycare_hours", _format_sq_ft(fields.line_4_daycare_hours))
    put("line_5_total_hours", _format_sq_ft(fields.line_5_total_hours))
    put(
        "line_6_daycare_decimal",
        _format_daycare_decimal(fields.line_6_daycare_decimal),
    )
    put(
        "line_7_business_percentage",
        _format_percentage(fields.line_7_business_percentage),
    )

    # Part II — allowable deduction
    put(
        "line_8_sch_c_line_29_tentative_profit",
        _format_decimal(fields.line_8_sch_c_line_29_tentative_profit),
    )
    put("line_9a_casualty_direct", _format_decimal(fields.line_9a_casualty_direct))
    put("line_9b_casualty_indirect", _format_decimal(fields.line_9b_casualty_indirect))
    put(
        "line_10a_mortgage_interest_direct",
        _format_decimal(fields.line_10a_mortgage_interest_direct),
    )
    put(
        "line_10b_mortgage_interest_indirect",
        _format_decimal(fields.line_10b_mortgage_interest_indirect),
    )
    put(
        "line_11a_real_estate_taxes_direct",
        _format_decimal(fields.line_11a_real_estate_taxes_direct),
    )
    put(
        "line_11b_real_estate_taxes_indirect",
        _format_decimal(fields.line_11b_real_estate_taxes_indirect),
    )
    put(
        "line_12a_sum_9_10_11_direct",
        _format_decimal(fields.line_12a_sum_9_10_11_direct),
    )
    put(
        "line_12b_sum_9_10_11_indirect",
        _format_decimal(fields.line_12b_sum_9_10_11_indirect),
    )
    put(
        "line_13_line_12b_times_line_7",
        _format_decimal(fields.line_13_line_12b_times_line_7),
    )
    put(
        "line_14_sum_12a_plus_13",
        _format_decimal(fields.line_14_sum_12a_plus_13),
    )
    put(
        "line_15_subtract_14_from_8",
        _format_decimal(fields.line_15_subtract_14_from_8),
    )
    put(
        "line_16a_excess_mortgage_interest_direct",
        _format_decimal(fields.line_16a_excess_mortgage_interest_direct),
    )
    put(
        "line_16b_excess_mortgage_interest_indirect",
        _format_decimal(fields.line_16b_excess_mortgage_interest_indirect),
    )
    put(
        "line_17a_excess_real_estate_taxes_direct",
        _format_decimal(fields.line_17a_excess_real_estate_taxes_direct),
    )
    put(
        "line_17b_excess_real_estate_taxes_indirect",
        _format_decimal(fields.line_17b_excess_real_estate_taxes_indirect),
    )
    put(
        "line_18a_insurance_direct",
        _format_decimal(fields.line_18a_insurance_direct),
    )
    put(
        "line_18b_insurance_indirect",
        _format_decimal(fields.line_18b_insurance_indirect),
    )
    put("line_19a_rent_direct", _format_decimal(fields.line_19a_rent_direct))
    put("line_19b_rent_indirect", _format_decimal(fields.line_19b_rent_indirect))
    put("line_20a_repairs_direct", _format_decimal(fields.line_20a_repairs_direct))
    put(
        "line_20b_repairs_indirect",
        _format_decimal(fields.line_20b_repairs_indirect),
    )
    put(
        "line_21a_utilities_direct",
        _format_decimal(fields.line_21a_utilities_direct),
    )
    put(
        "line_21b_utilities_indirect",
        _format_decimal(fields.line_21b_utilities_indirect),
    )
    put(
        "line_22a_other_expenses_direct",
        _format_decimal(fields.line_22a_other_expenses_direct),
    )
    put(
        "line_22b_other_expenses_indirect",
        _format_decimal(fields.line_22b_other_expenses_indirect),
    )
    put(
        "line_23a_sum_16_through_22_direct",
        _format_decimal(fields.line_23a_sum_16_through_22_direct),
    )
    put(
        "line_23b_sum_16_through_22_indirect",
        _format_decimal(fields.line_23b_sum_16_through_22_indirect),
    )
    put(
        "line_24_line_23b_times_line_7",
        _format_decimal(fields.line_24_line_23b_times_line_7),
    )
    put(
        "line_25_carryover_prior_year_operating",
        _format_decimal(fields.line_25_carryover_prior_year_operating),
    )
    put("line_26_sum_23a_24_25", _format_decimal(fields.line_26_sum_23a_24_25))
    put(
        "line_27_allowable_operating_expenses",
        _format_decimal(fields.line_27_allowable_operating_expenses),
    )
    put(
        "line_28_limit_on_excess_casualty_depreciation",
        _format_decimal(fields.line_28_limit_on_excess_casualty_depreciation),
    )
    put(
        "line_29_excess_casualty_losses",
        _format_decimal(fields.line_29_excess_casualty_losses),
    )
    put(
        "line_30_depreciation_of_home",
        _format_decimal(fields.line_30_depreciation_of_home),
    )
    put(
        "line_31_carryover_prior_year_excess_casualty_depreciation",
        _format_decimal(
            fields.line_31_carryover_prior_year_excess_casualty_depreciation
        ),
    )
    put("line_32_sum_29_30_31", _format_decimal(fields.line_32_sum_29_30_31))
    put(
        "line_33_allowable_excess_casualty_depreciation",
        _format_decimal(fields.line_33_allowable_excess_casualty_depreciation),
    )
    put("line_34_sum_14_27_33", _format_decimal(fields.line_34_sum_14_27_33))
    put(
        "line_35_casualty_loss_portion_to_4684",
        _format_decimal(fields.line_35_casualty_loss_portion_to_4684),
    )
    put(
        "line_36_allowable_expenses_to_sch_c_line_30",
        _format_decimal(fields.line_36_allowable_expenses_to_sch_c_line_30),
    )

    # Part III — depreciation
    put(
        "line_37_smaller_of_basis_or_fmv",
        _format_decimal(fields.line_37_smaller_of_basis_or_fmv),
    )
    put("line_38_value_of_land", _format_decimal(fields.line_38_value_of_land))
    put(
        "line_39_basis_of_building",
        _format_decimal(fields.line_39_basis_of_building),
    )
    put(
        "line_40_business_basis_of_building",
        _format_decimal(fields.line_40_business_basis_of_building),
    )
    put(
        "line_41_depreciation_percentage",
        _format_depreciation_pct(fields.line_41_depreciation_percentage),
    )
    put(
        "line_42_depreciation_allowable",
        _format_decimal(fields.line_42_depreciation_allowable),
    )

    # Part IV — carryover
    put(
        "line_43_carryover_operating_expenses",
        _format_decimal(fields.line_43_carryover_operating_expenses),
    )
    put(
        "line_44_carryover_excess_casualty_depreciation",
        _format_decimal(fields.line_44_carryover_excess_casualty_depreciation),
    )

    return out


def render_form_8829_pdf(fields: Form8829Fields, out_path: Path) -> Path:
    """Render one filled Form 8829 PDF by AcroForm overlay.

    Loads the wave-6 widget map, verifies the on-disk source PDF
    SHA-256, fills the widgets, and writes to ``out_path``. Raises
    ``RuntimeError`` if the source PDF is missing or has been re-issued
    (SHA mismatch).

    Returns ``out_path`` for convenience.
    """
    from skill.scripts.output._acroform_overlay import (
        fill_acroform_pdf,
        load_widget_map_as_dict,
        verify_pdf_sha256,
    )

    widget_map = load_widget_map_as_dict(_FORM_8829_MAP_PATH)
    verify_pdf_sha256(_FORM_8829_PDF_PATH, widget_map["source_pdf_sha256"])
    widget_values = _build_widget_values(fields, widget_map)
    return fill_acroform_pdf(_FORM_8829_PDF_PATH, widget_values, Path(out_path))


def render_form_8829_pdfs_all(
    return_: CanonicalReturn, out_dir: Path
) -> list[Path]:
    """Render one Form 8829 per regular-method home office on the return.

    Filename pattern mirrors the Schedule C renderer:
    ``form_8829_{idx:02d}_{slug}.pdf`` where ``idx`` is the parent
    ScheduleC index on the return and ``slug`` is a filesystem-safe
    derivative of the business name. Simplified-method home offices
    are silently skipped (no Form 8829 is filed). Returns the list of
    written paths in input order.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for idx, sc in enumerate(return_.schedules_c):
        ho = sc.home_office
        if ho is None or ho.method != "regular":
            continue
        fields = compute_form_8829_fields(ho, sc)
        slug = _slugify(sc.business_name) or f"business_{idx:02d}"
        out_path = out_dir / f"form_8829_{idx:02d}_{slug}.pdf"
        render_form_8829_pdf(fields, out_path)
        written.append(out_path)
    return written


def _slugify(text: str) -> str:
    """Filesystem-safe slug (matches the wave-5 Schedule C helper)."""
    out_chars: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            out_chars.append(ch)
        elif ch in (" ", "-", "_"):
            out_chars.append("_")
    slug = "".join(out_chars).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug


# ---------------------------------------------------------------------------
# Pipeline helper — apply the dispatcher to every Schedule C on a return
# ---------------------------------------------------------------------------


def apply_home_office_deductions(return_: CanonicalReturn) -> None:
    """Populate ``sch_c.line30_home_office_expense`` in place for every
    ``ScheduleC`` on ``return_`` whose ``home_office`` is set.

    This is the hook the pipeline calls BEFORE ``calc.engine.compute``
    so the computed Sch C net profit, SE tax, and AGI all honor the
    home-office deduction. A second pass after compute() would be
    wrong — SE tax is already locked in.

    Idempotent: if ``sch_c.line30_home_office_expense`` is already
    non-zero AND the home-office block is absent, we leave it alone
    (matches the wave-5 behavior of callers who set the number
    directly without going through Form 8829).
    """
    for sc in return_.schedules_c:
        ho = sc.home_office
        if ho is None:
            continue
        sc.line30_home_office_expense = compute_home_office_deduction(ho, sc)
