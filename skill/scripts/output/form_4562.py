"""Form 4562 Depreciation and Amortization — two-layer renderer.

Scope
-----
Form 4562 is required whenever a filer claims any of:

* **§179 expense** election on current-year asset placed in service (Part I)
* **Special (bonus) depreciation** under §168(k) (Part II)
* **MACRS depreciation** on property placed in service this tax year (Part III
  Section A / Section B) — or prior-year MACRS that needs to be reported
  for informational purposes (Section C)
* **Listed property** (autos, etc.) regardless of whether §179 is claimed (Part V)
* **Amortization** of startup costs, §197 intangibles, or other amortizable
  items (Part VI)

A single return can carry multiple Form 4562s — one per business (Schedule C),
one per rental (Schedule E), one per Form 2106 (employee business expenses).
Wave 6 Agent 4 only wires the Schedule C case: ``compute_form_4562_fields``
operates on a single ``ScheduleC`` by index on the return.

TY2025 numbers (OBBBA / TCJA)
-----------------------------
* §179 dollar limit: **$1,250,000** (OBBBA raised from $1.22M)
* §179 investment-in-service phase-out threshold: **$3,130,000**
* SUV (> 6,000 lb GVWR) §179 sub-cap: **$31,300**
* §168(k) special depreciation allowance (bonus): **40%**
  — the TCJA phase-down continues; TY2024 was 60%, TY2025 is 40%,
    TY2026 will be 20%, TY2027 → 0%. OBBBA did NOT restore 100% bonus.
* §280F first-year auto cap (with bonus, placed in service 2025):
  **$20,400** year 1, **$19,800** year 2, **$11,900** year 3,
  **$7,160** year 4+. These are the TY2024 Rev. Proc. 2024-13 values;
  IRS traditionally reissues in early spring (Rev. Proc. 2025-XX);
  Wave 6 uses the TY2024 values as a placeholder and flags them in
  :data:`AUTO_DEPR_CAPS_TY2025`.

Sources
-------
* IRS 2025 Form 4562 — Depreciation and Amortization
  https://www.irs.gov/pub/irs-pdf/f4562.pdf
* IRS 2025 Instructions for Form 4562
  https://www.irs.gov/pub/irs-pdf/i4562.pdf
* IRS Pub 946, "How To Depreciate Property" — MACRS tables
  https://www.irs.gov/pub/irs-pdf/p946.pdf
* One Big Beautiful Bill Act (OBBBA), Public Law 119-XX §§ 70001-70005
  (§179 limit increase + investment threshold increase)
* Rev. Proc. 2024-13 — TY2024 §280F auto depreciation caps (carried
  forward as placeholder until Rev. Proc. 2025-XX is published)

Layers
------
* **Layer 1** — :func:`compute_form_4562_fields` takes a
  ``CanonicalReturn`` and the 0-based ``schedule_c_index`` of the
  business whose 4562 to compute. Returns a frozen ``Form4562Fields``
  dataclass whose field names mirror the IRS line numbers.
  All arithmetic delegates to pure helpers in this module so the
  Schedule C renderer (``line_13_depreciation_section_179``) can
  reuse :func:`total_depreciation_for_schedule_c`.
* **Layer 2** — :func:`render_form_4562_pdf` fills the bundled IRS
  fillable PDF (SHA-256 pinned) via the shared AcroForm overlay
  helper. One filled 4562 per business is emitted.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from skill.scripts.calc.macrs_tables import macrs_depreciation_percentage
from skill.scripts.models import (
    CanonicalReturn,
    DepreciableAsset,
    ScheduleC,
)


# ---------------------------------------------------------------------------
# TY2025 statutory constants
# ---------------------------------------------------------------------------


SECTION_179_DOLLAR_LIMIT_TY2025 = Decimal("1250000")
"""OBBBA §70001. Maximum §179 deduction before phase-out (TY2025)."""

SECTION_179_INVESTMENT_THRESHOLD_TY2025 = Decimal("3130000")
"""OBBBA §70001. Phase-out begins when total §179 property cost placed
in service exceeds this amount; the §179 limit reduces dollar-for-dollar
above it."""

SECTION_179_SUV_CAP_TY2025 = Decimal("31300")
"""§179(b)(5)(A). Maximum §179 expense on any single SUV over 6,000 lb
GVWR that is not otherwise exempt."""

BONUS_DEPRECIATION_PCT_TY2025 = Decimal("0.40")
"""§168(k). Bonus depreciation rate for TY2025 — TCJA phase-down from
60% in TY2024 to 40% in TY2025 to 20% in TY2026 to 0% in TY2027."""

AUTO_DEPR_CAPS_TY2025: dict[int, Decimal] = {
    # Year-of-service (0-indexed) → §280F cap for autos with bonus
    # depreciation elected. Year 0 = first year in service.
    0: Decimal("20400"),
    1: Decimal("19800"),
    2: Decimal("11900"),
    3: Decimal("7160"),
}
"""§280F(a)(1) first-year limitations on auto depreciation. Keys are
year-of-service (0-indexed); year-4+ uses the year-3 floor ($7,160).
Placeholder values: Rev. Proc. 2024-13 TY2024 figures — IRS typically
reissues these in Q1 each year as Rev. Proc. 2025-XX. Wave 6 verifies
the placeholder against the Form 4562 instructions.
"""


_ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Layer 1: dataclass of Form 4562 line values
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Form4562AssetRow:
    """A single asset rendered on Part III / Part V / Part VI of Form 4562.

    Frozen so tests can compare rows directly. ``section`` names the
    Form 4562 sub-section the asset belongs to (``"part_i_section_179"``,
    ``"part_ii_bonus"``, ``"part_iii_section_a"``, ``"part_iii_section_b_5yr"``,
    ``"part_iii_section_c_prior"``, ``"part_v_listed"``,
    ``"part_vi_amortization"``).
    """

    section: str
    description: str
    date_placed_in_service: dt.date | None
    cost: Decimal
    business_use_pct: Decimal
    section_179_expense: Decimal
    bonus_depreciation: Decimal
    macrs_depreciation: Decimal
    depreciation_method: str = ""
    recovery_period_years: str = ""
    convention: str = ""


@dataclass(frozen=True)
class Form4562Fields:
    """Frozen snapshot of Form 4562 line values, ready for rendering."""

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------
    taxpayer_name: str = ""
    identifying_number: str | None = None
    business_or_activity: str = ""

    # ------------------------------------------------------------------
    # Part I — §179 Election
    # ------------------------------------------------------------------
    line_1_max_amount: Decimal = SECTION_179_DOLLAR_LIMIT_TY2025
    line_2_total_cost_section_179_property: Decimal = _ZERO
    line_3_threshold: Decimal = SECTION_179_INVESTMENT_THRESHOLD_TY2025
    line_4_reduction_in_limitation: Decimal = _ZERO
    line_5_dollar_limit_after_reduction: Decimal = SECTION_179_DOLLAR_LIMIT_TY2025
    line_6_listed_property_section_179: tuple[Form4562AssetRow, ...] = ()
    line_7_listed_property_amount: Decimal = _ZERO
    line_8_total_elected_cost: Decimal = _ZERO
    line_9_tentative_deduction: Decimal = _ZERO
    line_10_carryover_from_prior_year: Decimal = _ZERO
    line_11_business_income_limitation: Decimal = _ZERO
    line_12_section_179_deduction_current_year: Decimal = _ZERO
    line_13_carryover_to_next_year: Decimal = _ZERO

    # ------------------------------------------------------------------
    # Part II — Special (bonus) depreciation
    # ------------------------------------------------------------------
    line_14_special_depreciation_allowance: Decimal = _ZERO
    line_15_listed_property_special: Decimal = _ZERO
    line_16_other_depreciation: Decimal = _ZERO

    # ------------------------------------------------------------------
    # Part III — MACRS
    # ------------------------------------------------------------------
    line_17_macrs_prior_years: Decimal = _ZERO
    """Assets placed in service in prior years — sum of the remaining
    MACRS depreciation for the current year on those assets."""

    line_18_group_election: bool = False
    part_iii_section_b_current_year: tuple[Form4562AssetRow, ...] = ()
    part_iii_section_c_prior_year: tuple[Form4562AssetRow, ...] = ()

    # ------------------------------------------------------------------
    # Part IV — Summary
    # ------------------------------------------------------------------
    line_21_listed_property_total: Decimal = _ZERO
    line_22_total_depreciation: Decimal = _ZERO
    """Flows to Schedule C line 13."""
    line_23_for_asset_placed_in_service_current_year: Decimal = _ZERO

    # ------------------------------------------------------------------
    # Part V — Listed property (autos, etc.)
    # ------------------------------------------------------------------
    part_v_listed_rows: tuple[Form4562AssetRow, ...] = ()
    part_v_section_a_uses_mileage_method: bool | None = None

    # ------------------------------------------------------------------
    # Part VI — Amortization
    # ------------------------------------------------------------------
    part_vi_amortization_rows: tuple[Form4562AssetRow, ...] = ()
    line_44_amortization_current_year: Decimal = _ZERO


# ---------------------------------------------------------------------------
# Pure helpers (testable in isolation)
# ---------------------------------------------------------------------------


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _year_of_service(tax_year: int, placed_in_service: dt.date) -> int:
    """0-indexed year of service for an asset in ``tax_year``.

    Year 0 is the first tax year the asset is in service (the year it
    was placed in service). Negative values (future placement) are
    clamped to 0.
    """
    return max(0, tax_year - placed_in_service.year)


def _is_auto(asset: DepreciableAsset) -> bool:
    """True if the asset should have the §280F auto cap applied.

    Listed property + MACRS 5-year class is the canonical auto shape;
    real business-use autos almost always clear 50% business use so we
    don't gate on that here (the caller has already populated
    ``is_listed_property``)."""
    return asset.is_listed_property and asset.macrs_class == "5"


def section_179_phase_out_limit(
    total_cost: Decimal,
    *,
    dollar_limit: Decimal = SECTION_179_DOLLAR_LIMIT_TY2025,
    threshold: Decimal = SECTION_179_INVESTMENT_THRESHOLD_TY2025,
) -> Decimal:
    """Apply the §179 investment-in-service phase-out.

    When the aggregate cost of §179 property placed in service in the
    year exceeds the threshold, the dollar limit is reduced
    dollar-for-dollar. Once total cost exceeds threshold + dollar_limit,
    the §179 deduction is zero.
    """
    if total_cost <= threshold:
        return dollar_limit
    reduction = total_cost - threshold
    return max(_ZERO, dollar_limit - reduction)


def compute_bonus_depreciation(
    asset: DepreciableAsset,
    *,
    post_179_basis: Decimal,
    bonus_pct: Decimal = BONUS_DEPRECIATION_PCT_TY2025,
) -> Decimal:
    """Compute the §168(k) bonus depreciation on one asset.

    Taxpayers can elect OUT by setting ``asset.bonus_depreciation_elected
    = False``. Amortizable intangibles (``macrs_class is None``) and
    prior-year assets do not take bonus.
    """
    if not asset.bonus_depreciation_elected:
        return _ZERO
    if asset.macrs_class is None:
        return _ZERO
    if post_179_basis <= 0:
        return _ZERO
    return _quantize(post_179_basis * bonus_pct)


def compute_macrs_depreciation(
    asset: DepreciableAsset,
    *,
    tax_year: int,
    depreciable_basis: Decimal,
) -> Decimal:
    """Compute this year's MACRS depreciation on an asset.

    ``depreciable_basis`` is the cost minus any §179 and bonus
    depreciation already subtracted — that is what the MACRS table
    percentages apply to. If the asset has no MACRS class (e.g.
    §197 intangible) this returns zero.
    """
    if asset.macrs_class is None:
        return _ZERO
    if depreciable_basis <= 0:
        return _ZERO
    year_idx = _year_of_service(tax_year, asset.date_placed_in_service)
    pct = macrs_depreciation_percentage(asset.macrs_class, year_idx)
    return _quantize(depreciable_basis * pct)


def _apply_auto_cap(
    asset: DepreciableAsset,
    tax_year: int,
    raw_total: Decimal,
    *,
    caps: dict[int, Decimal] = AUTO_DEPR_CAPS_TY2025,
) -> Decimal:
    """Apply the §280F first-year auto cap to the total year-1+ depreciation.

    The cap limits the SUM of §179 + bonus + MACRS on an auto for the
    year. Business-use percentage scales the cap.
    """
    if not _is_auto(asset):
        return raw_total
    year_idx = _year_of_service(tax_year, asset.date_placed_in_service)
    # Year 3+ uses the year-3 floor.
    cap_key = min(year_idx, 3)
    cap = caps.get(cap_key, caps[3])
    use_fraction = asset.business_use_pct / Decimal("100")
    capped = _quantize(cap * use_fraction)
    return min(raw_total, capped)


# ---------------------------------------------------------------------------
# Top-level per-asset computation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _AssetDepreciation:
    """Intermediate per-asset result used to build Form 4562 line totals."""

    asset: DepreciableAsset
    section_179_expense: Decimal
    bonus_depreciation: Decimal
    macrs_depreciation: Decimal
    total_depreciation: Decimal

    @property
    def is_listed(self) -> bool:
        return self.asset.is_listed_property

    @property
    def is_prior_year(self) -> bool:
        return self.asset.prior_year_depreciation > _ZERO

    @property
    def is_amortization(self) -> bool:
        return self.asset.macrs_class is None


def _compute_per_asset(
    assets: list[DepreciableAsset],
    *,
    tax_year: int,
    section_179_budget_remaining: Decimal,
) -> list[_AssetDepreciation]:
    """Walk each asset and compute §179 + bonus + MACRS for the year.

    ``section_179_budget_remaining`` is the aggregate §179 dollar limit
    after phase-out (line 5 of Form 4562). Each asset's §179 election
    is clamped to this running budget.
    """
    results: list[_AssetDepreciation] = []
    budget = section_179_budget_remaining

    for asset in assets:
        elected_179 = asset.section_179_elected
        # SUV sub-cap
        if asset.is_suv_over_6000lb:
            elected_179 = min(elected_179, SECTION_179_SUV_CAP_TY2025)
        # Clamp to remaining aggregate budget and asset cost
        elected_179 = min(elected_179, asset.cost, budget)
        elected_179 = max(_ZERO, elected_179)
        budget = budget - elected_179

        # Apply business-use percentage to cost for post-179 basis
        use_fraction = asset.business_use_pct / Decimal("100")
        business_cost = _quantize(asset.cost * use_fraction)
        post_179 = max(_ZERO, business_cost - elected_179)

        bonus = compute_bonus_depreciation(asset, post_179_basis=post_179)
        macrs_basis = max(_ZERO, post_179 - bonus)

        # Prior-year MACRS: the depreciable basis is cost less prior
        # depreciation, and the current-year percent pulls from the
        # year-of-service in the MACRS table.
        if asset.prior_year_depreciation > _ZERO:
            prior_depr_basis = max(
                _ZERO,
                business_cost - elected_179,
            )
            macrs = compute_macrs_depreciation(
                asset,
                tax_year=tax_year,
                depreciable_basis=prior_depr_basis,
            )
        else:
            macrs = compute_macrs_depreciation(
                asset,
                tax_year=tax_year,
                depreciable_basis=macrs_basis,
            )

        raw_total = elected_179 + bonus + macrs
        total = _apply_auto_cap(asset, tax_year, raw_total)

        # If the auto cap reduced the total, scale MACRS first
        # (§179 is a taxpayer election, bonus is near-locked for autos).
        if total < raw_total:
            excess = raw_total - total
            macrs = max(_ZERO, macrs - excess)

        results.append(
            _AssetDepreciation(
                asset=asset,
                section_179_expense=elected_179,
                bonus_depreciation=bonus,
                macrs_depreciation=macrs,
                total_depreciation=total,
            )
        )

    return results


def _asset_row(section: str, result: _AssetDepreciation) -> Form4562AssetRow:
    asset = result.asset
    return Form4562AssetRow(
        section=section,
        description=asset.description,
        date_placed_in_service=asset.date_placed_in_service,
        cost=asset.cost,
        business_use_pct=asset.business_use_pct,
        section_179_expense=result.section_179_expense,
        bonus_depreciation=result.bonus_depreciation,
        macrs_depreciation=result.macrs_depreciation,
        depreciation_method="200DB" if asset.macrs_class in ("3", "5", "7", "10") else
        "150DB" if asset.macrs_class in ("15", "20") else
        "SL" if asset.macrs_class in ("27.5", "39", "25") else "",
        recovery_period_years=(asset.macrs_class or ""),
        convention="HY" if asset.macrs_class in ("3", "5", "7", "10", "15", "20") else
        "MM" if asset.macrs_class in ("27.5", "39") else "",
    )


def compute_form_4562_fields_for_schedule_c(
    sc: ScheduleC,
    *,
    tax_year: int = 2025,
    taxpayer_name: str = "",
    identifying_number: str | None = None,
) -> Form4562Fields:
    """Compute Form 4562 line values from a bare ScheduleC.

    Used by the engine's ``schedule_c_net_profit`` which has only the
    ScheduleC in hand (no CanonicalReturn). When called from
    :func:`compute_form_4562_fields` (which receives the full
    CanonicalReturn) the taxpayer name / SSN / tax year are forwarded
    from the return; when called from the engine they default to
    TY2025 and empty headers.
    """
    return _compute_form_4562_fields_impl(
        sc,
        tax_year=tax_year,
        taxpayer_name=taxpayer_name,
        identifying_number=identifying_number,
    )


def compute_form_4562_fields(
    canonical: CanonicalReturn,
    schedule_c_index: int,
) -> Form4562Fields:
    """Map a single Schedule C's depreciable assets onto Form 4562 lines.

    Walks the assets, applies §179 phase-out / business-income limit /
    bonus / MACRS / §280F caps, and returns a frozen dataclass ready
    for the Layer 2 renderer.
    """
    if schedule_c_index < 0 or schedule_c_index >= len(canonical.schedules_c):
        raise ValueError(
            f"schedule_c_index {schedule_c_index} out of range "
            f"(return has {len(canonical.schedules_c)} Schedule C(s))"
        )
    sc: ScheduleC = canonical.schedules_c[schedule_c_index]
    taxpayer_name = (
        f"{canonical.taxpayer.first_name} {canonical.taxpayer.last_name}".strip()
    )
    return _compute_form_4562_fields_impl(
        sc,
        tax_year=canonical.tax_year,
        taxpayer_name=taxpayer_name,
        identifying_number=canonical.taxpayer.ssn,
    )


def _compute_form_4562_fields_impl(
    sc: ScheduleC,
    *,
    tax_year: int,
    taxpayer_name: str,
    identifying_number: str | None,
) -> Form4562Fields:
    """Internal: shared implementation of the Form 4562 compute."""
    assets = sc.depreciable_assets

    # -----------------------------------------------------------------
    # Part I — §179 phase-out and election
    # -----------------------------------------------------------------
    # Line 2: total cost of §179 property = sum of cost of assets where
    # taxpayer elected §179 (> 0). For simplicity we use ALL
    # current-year tangible assets' cost as the "§179 property" base,
    # matching the IRS form's pre-clamp arithmetic.
    current_year_tangibles = [
        a for a in assets
        if a.macrs_class is not None and a.prior_year_depreciation == 0
    ]
    line_2 = sum((a.cost for a in current_year_tangibles), start=_ZERO)
    line_4 = max(_ZERO, line_2 - SECTION_179_INVESTMENT_THRESHOLD_TY2025)
    line_5 = section_179_phase_out_limit(line_2)

    # Walk assets and apply the aggregated budget
    per_asset = _compute_per_asset(
        assets, tax_year=tax_year, section_179_budget_remaining=line_5
    )

    line_8 = sum(
        (r.section_179_expense for r in per_asset), start=_ZERO
    )
    line_9 = min(line_5, line_8)

    # Line 10 — carryover from prior year (per business)
    line_10 = sc.section_179_carryover_from_prior_year

    # Line 11 — business income limitation. Use tentative profit
    # (gross income less total expenses before line 13 depreciation)
    # to avoid circularity: the engine's net profit already factors in
    # line13_depreciation, so subtract it back out here.
    gross_income_less_cogs = (
        sc.line1_gross_receipts
        - sc.line2_returns_and_allowances
        - sc.line4_cost_of_goods_sold
        + sc.line6_other_income
    )
    # Total Part II expenses EXCLUDING line 13 depreciation
    exp = sc.expenses
    non_depr_expenses = (
        exp.line8_advertising
        + exp.line9_car_and_truck
        + exp.line10_commissions_and_fees
        + exp.line11_contract_labor
        + exp.line12_depletion
        + exp.line14_employee_benefit_programs
        + exp.line15_insurance_not_health
        + exp.line16a_mortgage_interest
        + exp.line16b_other_interest
        + exp.line17_legal_and_professional
        + exp.line18_office_expense
        + exp.line19_pension_and_profit_sharing
        + exp.line20a_rent_vehicles_machinery_equipment
        + exp.line20b_rent_other_business_property
        + exp.line21_repairs_and_maintenance
        + exp.line22_supplies
        + exp.line23_taxes_and_licenses
        + exp.line24a_travel
        + exp.line24b_meals_50pct_deductible
        + exp.line25_utilities
        + exp.line26_wages
        + exp.line27a_other_expenses
    )
    other_sum = sum(exp.other_expense_detail.values(), start=_ZERO)
    pre_depr_profit = gross_income_less_cogs - non_depr_expenses - other_sum
    line_11 = max(_ZERO, pre_depr_profit)

    # Line 12 — current-year §179 deduction = min(line 9 + line 10, line 11)
    line_12 = min(line_9 + line_10, line_11)
    # Line 13 — carryover to next year = max(0, line 9 + line 10 - line 11)
    line_13 = max(_ZERO, (line_9 + line_10) - line_11)

    # -----------------------------------------------------------------
    # Part II — special (bonus) depreciation
    # -----------------------------------------------------------------
    bonus_non_listed = sum(
        (r.bonus_depreciation for r in per_asset
         if not r.is_listed and not r.is_prior_year and not r.is_amortization),
        start=_ZERO,
    )
    bonus_listed = sum(
        (r.bonus_depreciation for r in per_asset
         if r.is_listed and not r.is_prior_year),
        start=_ZERO,
    )
    line_14 = bonus_non_listed
    line_15 = bonus_listed
    line_16 = _ZERO  # other (ACRS, pre-1987) — not modeled

    # -----------------------------------------------------------------
    # Part III — MACRS
    # -----------------------------------------------------------------
    line_17 = sum(
        (r.macrs_depreciation for r in per_asset if r.is_prior_year),
        start=_ZERO,
    )

    section_b_current = tuple(
        _asset_row("part_iii_section_b_current_year", r)
        for r in per_asset
        if not r.is_prior_year and not r.is_listed and not r.is_amortization
    )
    section_c_prior = tuple(
        _asset_row("part_iii_section_c_prior_year", r)
        for r in per_asset
        if r.is_prior_year and not r.is_listed and not r.is_amortization
    )

    # -----------------------------------------------------------------
    # Part V — Listed property (autos)
    # -----------------------------------------------------------------
    part_v = tuple(
        _asset_row("part_v_listed", r)
        for r in per_asset
        if r.is_listed
    )
    line_21 = sum((r.total_depreciation for r in per_asset if r.is_listed), start=_ZERO)

    # -----------------------------------------------------------------
    # Part VI — Amortization
    # -----------------------------------------------------------------
    part_vi = tuple(
        _asset_row("part_vi_amortization", r)
        for r in per_asset
        if r.is_amortization
    )
    line_44 = sum(
        (r.total_depreciation for r in per_asset if r.is_amortization),
        start=_ZERO,
    )

    # -----------------------------------------------------------------
    # Part IV — Summary: line 22 = total depreciation
    # = line 12 (§179) + line 14/15 (bonus) + line 16 + line 17 (prior MACRS)
    #   + current-year MACRS on Section B rows
    # (Listed property is reported on Part V and flows to line 21; line
    #  21 is ALSO added to line 22 per the IRS instructions.)
    # -----------------------------------------------------------------
    current_year_macrs_non_listed = sum(
        (r.macrs_depreciation for r in per_asset
         if not r.is_listed and not r.is_prior_year and not r.is_amortization),
        start=_ZERO,
    )
    line_22 = (
        line_12
        + line_14
        + line_16
        + line_17
        + current_year_macrs_non_listed
        + line_21
        + line_44
    )

    # -----------------------------------------------------------------
    # Build the final dataclass
    # -----------------------------------------------------------------
    return Form4562Fields(
        taxpayer_name=taxpayer_name,
        identifying_number=identifying_number,
        business_or_activity=sc.business_name,
        line_1_max_amount=SECTION_179_DOLLAR_LIMIT_TY2025,
        line_2_total_cost_section_179_property=line_2,
        line_3_threshold=SECTION_179_INVESTMENT_THRESHOLD_TY2025,
        line_4_reduction_in_limitation=line_4,
        line_5_dollar_limit_after_reduction=line_5,
        line_6_listed_property_section_179=tuple(),
        line_7_listed_property_amount=_ZERO,
        line_8_total_elected_cost=line_8,
        line_9_tentative_deduction=line_9,
        line_10_carryover_from_prior_year=line_10,
        line_11_business_income_limitation=line_11,
        line_12_section_179_deduction_current_year=line_12,
        line_13_carryover_to_next_year=line_13,
        line_14_special_depreciation_allowance=line_14,
        line_15_listed_property_special=line_15,
        line_16_other_depreciation=line_16,
        line_17_macrs_prior_years=line_17,
        line_18_group_election=False,
        part_iii_section_b_current_year=section_b_current,
        part_iii_section_c_prior_year=section_c_prior,
        line_21_listed_property_total=line_21,
        line_22_total_depreciation=line_22,
        line_23_for_asset_placed_in_service_current_year=_ZERO,
        part_v_listed_rows=part_v,
        part_v_section_a_uses_mileage_method=None,
        part_vi_amortization_rows=part_vi,
        line_44_amortization_current_year=line_44,
    )


def total_depreciation_for_schedule_c(
    canonical: CanonicalReturn, schedule_c_index: int
) -> Decimal:
    """Return the total Form 4562 depreciation that flows to Sch C line 13.

    Pure helper for the Schedule C renderer to call when the business
    has any ``depreciable_assets``. Zero if the business has no assets.
    """
    sc = canonical.schedules_c[schedule_c_index]
    if not sc.depreciable_assets:
        return _ZERO
    fields = compute_form_4562_fields(canonical, schedule_c_index)
    return fields.line_22_total_depreciation


# ---------------------------------------------------------------------------
# Layer 2: AcroForm overlay PDF rendering
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parents[3]
_FORM_4562_MAP_PATH = (
    _REPO_ROOT / "skill" / "reference" / "form-4562-acroform-map.json"
)
_FORM_4562_PDF_PATH = (
    _REPO_ROOT / "skill" / "reference" / "irs_forms" / "f4562.pdf"
)


def _format_decimal(value: Decimal) -> str:
    """Format a Decimal for an AcroForm text widget (blank for zero)."""
    q = value.quantize(Decimal("0.01"))
    if q == Decimal("0.00"):
        return ""
    return f"{q:.2f}"


def _build_widget_values(
    fields: Form4562Fields, widget_map: dict
) -> dict[str, str]:
    """Translate ``Form4562Fields`` into a widget_name → str dict.

    The widget map is keyed by semantic name (e.g.
    ``"line_1_max_amount"``); semantic names that are absent from the
    map are silently skipped so the renderer still works against a
    partial widget map.
    """
    out: dict[str, str] = {}
    mapping = widget_map["mapping"]

    def put(sem: str, value) -> None:
        if sem not in mapping:
            return
        widget_name = mapping[sem].get("widget_name")
        if not widget_name or "*" in widget_name:
            return
        if value is None:
            out[widget_name] = ""
        elif isinstance(value, Decimal):
            out[widget_name] = _format_decimal(value)
        elif isinstance(value, bool):
            out[widget_name] = "Yes" if value else ""
        else:
            out[widget_name] = str(value)

    # Header
    put("taxpayer_name", fields.taxpayer_name)
    put("identifying_number", fields.identifying_number)
    put("business_or_activity", fields.business_or_activity)

    # Part I
    put("line_1_max_amount", fields.line_1_max_amount)
    put("line_2_total_cost_section_179_property",
        fields.line_2_total_cost_section_179_property)
    put("line_3_threshold", fields.line_3_threshold)
    put("line_4_reduction_in_limitation", fields.line_4_reduction_in_limitation)
    put("line_5_dollar_limit_after_reduction", fields.line_5_dollar_limit_after_reduction)
    put("line_7_listed_property_amount", fields.line_7_listed_property_amount)
    put("line_8_total_elected_cost", fields.line_8_total_elected_cost)
    put("line_9_tentative_deduction", fields.line_9_tentative_deduction)
    put("line_10_carryover_from_prior_year", fields.line_10_carryover_from_prior_year)
    put("line_11_business_income_limitation", fields.line_11_business_income_limitation)
    put("line_12_section_179_deduction_current_year",
        fields.line_12_section_179_deduction_current_year)
    put("line_13_carryover_to_next_year", fields.line_13_carryover_to_next_year)

    # Part II
    put("line_14_special_depreciation_allowance", fields.line_14_special_depreciation_allowance)
    put("line_15_listed_property_special", fields.line_15_listed_property_special)
    put("line_16_other_depreciation", fields.line_16_other_depreciation)

    # Part III
    put("line_17_macrs_prior_years", fields.line_17_macrs_prior_years)

    # Part IV
    put("line_21_listed_property_total", fields.line_21_listed_property_total)
    put("line_22_total_depreciation", fields.line_22_total_depreciation)

    # Part VI
    put("line_44_amortization_current_year", fields.line_44_amortization_current_year)

    return out


def render_form_4562_pdf(fields: Form4562Fields, out_path: Path) -> Path:
    """Fill the IRS f4562.pdf with ``fields`` and write to ``out_path``.

    Verifies the source PDF's SHA-256 against the pinned digest in the
    wave-6 widget map. Raises ``RuntimeError`` if the PDF is missing or
    has been re-issued.
    """
    from skill.scripts.output._acroform_overlay import (
        fill_acroform_pdf,
        load_widget_map_as_dict,
        verify_pdf_sha256,
    )

    widget_map = load_widget_map_as_dict(_FORM_4562_MAP_PATH)
    verify_pdf_sha256(_FORM_4562_PDF_PATH, widget_map["source_pdf_sha256"])
    widget_values = _build_widget_values(fields, widget_map)
    return fill_acroform_pdf(_FORM_4562_PDF_PATH, widget_values, Path(out_path))


def render_form_4562_pdfs_all(
    canonical: CanonicalReturn, out_dir: Path
) -> list[Path]:
    """Render one Form 4562 per Schedule C that has depreciable assets.

    Files are named ``form_4562_{idx:02d}_{slug}.pdf`` mirroring the
    Schedule C multi-business dispatch.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for idx, sc in enumerate(canonical.schedules_c):
        if not sc.depreciable_assets:
            continue
        fields = compute_form_4562_fields(canonical, idx)
        slug = _slugify(sc.business_name) or f"business_{idx:02d}"
        out_path = out_dir / f"form_4562_{idx:02d}_{slug}.pdf"
        render_form_4562_pdf(fields, out_path)
        written.append(out_path)
    return written


def _slugify(text: str) -> str:
    out: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_"):
            out.append("_")
    slug = "".join(out).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug
