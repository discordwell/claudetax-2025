"""Schedule C (Form 1040) output renderer — two-layer scaffold.

SCAFFOLD NOTICE
===============
This module is a SCAFFOLD for Schedule C (Profit or Loss From Business —
Sole Proprietorship) PDF generation. It follows the same two-layer design
as ``skill.scripts.output.form_1040``:

* Layer 1 (``compute_schedule_c_fields``) maps a single ``ScheduleC`` model
  onto a dataclass whose field names mirror the TY2024 Schedule C line
  structure (assumed stable for TY2025). All arithmetic delegates to
  ``skill.scripts.calc.engine`` helpers so that Layer 1 is bit-for-bit
  aligned with how the main calc engine treats the same return.

* Layer 2 (``render_schedule_c_pdf``) writes a very simple tabular PDF
  using ``reportlab``. The table lists every line_name/value pair per
  business so a human can eyeball the Schedule C; this is NOT a filled
  IRS Schedule C — real AcroForm overlay on the IRS fillable PDF is a
  follow-up task.

Multi-business dispatch
-----------------------
A CanonicalReturn may carry multiple ``ScheduleC`` entries (one per
business — the engine already iterates and sums their net profits).
``compute_schedule_c_fields_all(return_)`` returns a list of
``ScheduleCFields``, one per business, preserving input order.
``render_schedule_c_pdfs_all`` writes a file per business so downstream
wave(s) can staple them into the final return packet.

Sources
-------
* IRS 2024 Schedule C (Form 1040) — Profit or Loss From Business
  https://www.irs.gov/pub/irs-pdf/f1040sc.pdf
* IRS 2024 Instructions for Schedule C
  https://www.irs.gov/pub/irs-pdf/i1040sc.pdf

Simplifications / deferred work (tracked for later waves)
---------------------------------------------------------
* Part III (COGS, lines 33-42) is a scaffolded passthrough of the single
  aggregated ``line4_cost_of_goods_sold`` value. The granular COGS
  worksheet (beginning inventory, purchases, labor, materials, other,
  ending inventory, method of valuing inventory) is deferred — see the
  ``part_iii_*`` TODO in ``ScheduleC``. The scaffold renders line 42 =
  line 4 and zeros for lines 33-41 so totals still reconcile.
* Part IV (vehicle expense info, lines 43-47) is rendered as empty
  scaffold cells. The vehicle worksheet (placed-in-service date,
  business/commute/other miles, evidence, written-record flag) is not
  in the model yet — that's a fan-out task once vehicle depreciation
  lands.
* Part V (other expenses detail) IS populated from
  ``expenses.other_expense_detail``. Each (name, amount) pair becomes
  one line in the scaffold table; the total must equal Part II line 27a
  once the engine's summing logic is fully unified. For now the scaffold
  treats ``line27a_other_expenses`` and ``other_expense_detail`` as two
  independent buckets (matching how ``_sch_c_total_expenses`` adds them).
* SE tax, QBI, Schedule 1 routing: intentionally out of scope. This
  module only produces Schedule C; the Form 1040 / Schedule SE / Schedule
  1 renderers pull the same ``schedule_c_net_profit`` helper.
"""
from __future__ import annotations

from dataclasses import dataclass, fields as dc_fields
from decimal import Decimal
from pathlib import Path

from skill.scripts.calc.engine import (
    _sch_c_total_expenses,
    schedule_c_net_profit,
)
from skill.scripts.models import CanonicalReturn, ScheduleC


_ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Layer 1: field dataclass and computation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleCFields:
    """Frozen snapshot of Schedule C line values, ready for rendering.

    Field names follow the TY2024 Schedule C line numbers (assumed stable
    for TY2025). Numeric fields are ``Decimal``; header/text fields are
    ``str`` / ``bool`` / ``None``.
    """

    # ------------------------------------------------------------------
    # Header (lines A-J)
    # ------------------------------------------------------------------
    proprietor_name: str = ""
    proprietor_ssn: str | None = None
    line_a_principal_business_or_profession: str = ""
    line_b_principal_business_code: str | None = None
    line_c_business_name: str = ""
    line_d_ein: str | None = None
    line_e_business_address: str = ""
    line_f_accounting_method: str = "cash"
    line_g_material_participation: bool = True
    line_h_started_or_acquired_this_year: bool = False
    line_i_made_1099_payments_required: bool | None = None
    line_j_filed_required_1099s: bool | None = None

    # ------------------------------------------------------------------
    # Part I — Income
    # ------------------------------------------------------------------
    line_1_gross_receipts: Decimal = _ZERO
    line_2_returns_and_allowances: Decimal = _ZERO
    line_3_net_receipts: Decimal = _ZERO
    line_4_cost_of_goods_sold: Decimal = _ZERO
    line_5_gross_profit: Decimal = _ZERO
    line_6_other_income: Decimal = _ZERO
    line_7_gross_income: Decimal = _ZERO

    # ------------------------------------------------------------------
    # Part II — Expenses (lines 8-27)
    # ------------------------------------------------------------------
    line_8_advertising: Decimal = _ZERO
    line_9_car_and_truck: Decimal = _ZERO
    line_10_commissions_and_fees: Decimal = _ZERO
    line_11_contract_labor: Decimal = _ZERO
    line_12_depletion: Decimal = _ZERO
    line_13_depreciation_section_179: Decimal = _ZERO
    line_14_employee_benefit_programs: Decimal = _ZERO
    line_15_insurance_not_health: Decimal = _ZERO
    line_16a_mortgage_interest: Decimal = _ZERO
    line_16b_other_interest: Decimal = _ZERO
    line_17_legal_and_professional: Decimal = _ZERO
    line_18_office_expense: Decimal = _ZERO
    line_19_pension_and_profit_sharing: Decimal = _ZERO
    line_20a_rent_vehicles_machinery_equipment: Decimal = _ZERO
    line_20b_rent_other_business_property: Decimal = _ZERO
    line_21_repairs_and_maintenance: Decimal = _ZERO
    line_22_supplies: Decimal = _ZERO
    line_23_taxes_and_licenses: Decimal = _ZERO
    line_24a_travel: Decimal = _ZERO
    line_24b_meals_50pct_deductible: Decimal = _ZERO
    line_25_utilities: Decimal = _ZERO
    line_26_wages_less_employment_credits: Decimal = _ZERO
    line_27a_other_expenses: Decimal = _ZERO
    line_28_total_expenses: Decimal = _ZERO
    line_29_tentative_profit_or_loss: Decimal = _ZERO
    line_30_home_office_expense: Decimal = _ZERO
    line_31_net_profit_or_loss: Decimal = _ZERO
    line_32a_all_investment_at_risk: bool = True
    line_32b_some_investment_not_at_risk: bool = False

    # ------------------------------------------------------------------
    # Part III — Cost of Goods Sold (lines 33-42)
    #
    # DEFERRED: the granular COGS worksheet is not yet modeled. We
    # passthrough the aggregated COGS on line 42 so totals reconcile.
    # ------------------------------------------------------------------
    line_33_inventory_valuation_method: str = "cost"
    line_34_inventory_method_change: bool = False
    line_35_beginning_inventory: Decimal = _ZERO
    line_36_purchases_less_personal_use: Decimal = _ZERO
    line_37_cost_of_labor: Decimal = _ZERO
    line_38_materials_and_supplies: Decimal = _ZERO
    line_39_other_costs: Decimal = _ZERO
    line_40_sum_35_through_39: Decimal = _ZERO
    line_41_ending_inventory: Decimal = _ZERO
    line_42_cost_of_goods_sold: Decimal = _ZERO

    # ------------------------------------------------------------------
    # Part IV — Vehicle information (lines 43-47)
    #
    # DEFERRED: vehicle worksheet not yet modeled.
    # ------------------------------------------------------------------
    line_43_vehicle_placed_in_service_date: str | None = None
    line_44a_business_miles: Decimal = _ZERO
    line_44b_commuting_miles: Decimal = _ZERO
    line_44c_other_miles: Decimal = _ZERO
    line_45_vehicle_available_for_personal_use: bool | None = None
    line_46_another_vehicle_available: bool | None = None
    line_47a_have_evidence: bool | None = None
    line_47b_evidence_written: bool | None = None

    # ------------------------------------------------------------------
    # Part V — Other expenses detail
    # ------------------------------------------------------------------
    part_v_other_expenses: tuple[tuple[str, Decimal], ...] = ()
    part_v_total: Decimal = _ZERO


def _dec(x: Decimal | None) -> Decimal:
    """Coerce an Optional[Decimal] to a concrete Decimal (None -> 0)."""
    return x if x is not None else _ZERO


def _format_address(sc: ScheduleC) -> str:
    """Flatten a ScheduleC's business address into a single line for line E.

    Returns ``""`` when no address is provided.
    """
    addr = sc.business_address
    if addr is None:
        return ""
    parts = [addr.street1]
    if addr.street2:
        parts.append(addr.street2)
    city_state_zip = f"{addr.city}, {addr.state} {addr.zip}".strip()
    parts.append(city_state_zip)
    return " | ".join(p for p in parts if p)


def compute_schedule_c_fields(sc: ScheduleC) -> ScheduleCFields:
    """Map a single ``ScheduleC`` onto a ``ScheduleCFields`` dataclass.

    This function NEVER re-implements net profit — it delegates to
    ``skill.scripts.calc.engine.schedule_c_net_profit`` and
    ``_sch_c_total_expenses`` so that the output renderer is bit-for-bit
    consistent with the main calc engine.
    """
    e = sc.expenses

    # -- Part I ---------------------------------------------------------
    line_1 = sc.line1_gross_receipts
    line_2 = sc.line2_returns_and_allowances
    line_3 = line_1 - line_2
    line_4 = sc.line4_cost_of_goods_sold
    line_5 = line_3 - line_4
    line_6 = sc.line6_other_income
    line_7 = line_5 + line_6

    # -- Part II --------------------------------------------------------
    line_28 = _sch_c_total_expenses(e)
    line_29 = line_7 - line_28
    line_30 = sc.line30_home_office_expense
    # Trust the engine for line 31.
    line_31 = schedule_c_net_profit(sc)

    # -- Part III — scaffolded passthrough -----------------------------
    line_42 = sc.line4_cost_of_goods_sold  # DEFERRED: granular COGS

    # -- Part V — other expenses detail --------------------------------
    part_v_items = tuple(
        (label, amount) for label, amount in e.other_expense_detail.items()
    )
    part_v_total = sum((amt for _, amt in part_v_items), start=_ZERO)

    # -- Header ---------------------------------------------------------
    line_e = _format_address(sc)

    return ScheduleCFields(
        proprietor_name=sc.business_name if not sc.proprietor_is_taxpayer else "",
        # proprietor_ssn is filled by Form 1040 header, not this module.
        proprietor_ssn=None,
        line_a_principal_business_or_profession=sc.principal_business_or_profession,
        line_b_principal_business_code=sc.principal_business_code,
        line_c_business_name=sc.business_name,
        line_d_ein=sc.ein,
        line_e_business_address=line_e,
        line_f_accounting_method=sc.accounting_method,
        line_g_material_participation=sc.material_participation,
        line_h_started_or_acquired_this_year=sc.started_or_acquired_this_year,
        line_i_made_1099_payments_required=sc.made_1099_payments,
        line_j_filed_required_1099s=sc.filed_required_1099s,
        line_1_gross_receipts=line_1,
        line_2_returns_and_allowances=line_2,
        line_3_net_receipts=line_3,
        line_4_cost_of_goods_sold=line_4,
        line_5_gross_profit=line_5,
        line_6_other_income=line_6,
        line_7_gross_income=line_7,
        line_8_advertising=e.line8_advertising,
        line_9_car_and_truck=e.line9_car_and_truck,
        line_10_commissions_and_fees=e.line10_commissions_and_fees,
        line_11_contract_labor=e.line11_contract_labor,
        line_12_depletion=e.line12_depletion,
        line_13_depreciation_section_179=e.line13_depreciation,
        line_14_employee_benefit_programs=e.line14_employee_benefit_programs,
        line_15_insurance_not_health=e.line15_insurance_not_health,
        line_16a_mortgage_interest=e.line16a_mortgage_interest,
        line_16b_other_interest=e.line16b_other_interest,
        line_17_legal_and_professional=e.line17_legal_and_professional,
        line_18_office_expense=e.line18_office_expense,
        line_19_pension_and_profit_sharing=e.line19_pension_and_profit_sharing,
        line_20a_rent_vehicles_machinery_equipment=e.line20a_rent_vehicles_machinery_equipment,
        line_20b_rent_other_business_property=e.line20b_rent_other_business_property,
        line_21_repairs_and_maintenance=e.line21_repairs_and_maintenance,
        line_22_supplies=e.line22_supplies,
        line_23_taxes_and_licenses=e.line23_taxes_and_licenses,
        line_24a_travel=e.line24a_travel,
        line_24b_meals_50pct_deductible=e.line24b_meals_50pct_deductible,
        line_25_utilities=e.line25_utilities,
        line_26_wages_less_employment_credits=e.line26_wages,
        line_27a_other_expenses=e.line27a_other_expenses,
        line_28_total_expenses=line_28,
        line_29_tentative_profit_or_loss=line_29,
        line_30_home_office_expense=line_30,
        line_31_net_profit_or_loss=line_31,
        line_32a_all_investment_at_risk=sc.line32_at_risk_box == "all_at_risk",
        line_32b_some_investment_not_at_risk=sc.line32_at_risk_box == "some_not_at_risk",
        # Part III passthrough
        line_42_cost_of_goods_sold=line_42,
        # Part V
        part_v_other_expenses=part_v_items,
        part_v_total=part_v_total,
    )


def compute_schedule_c_fields_all(
    return_: CanonicalReturn,
) -> list[ScheduleCFields]:
    """Map every ``ScheduleC`` on a return to its own ``ScheduleCFields``.

    Preserves input order. Returns an empty list when the return has no
    ``schedules_c`` — callers should special-case that (no Schedule C
    rendering needed).
    """
    return [compute_schedule_c_fields(sc) for sc in return_.schedules_c]


# ---------------------------------------------------------------------------
# Layer 2: AcroForm overlay PDF rendering (wave 5)
# ---------------------------------------------------------------------------
#
# Layer 2 was previously a reportlab-based scaffold; wave 5 replaced it
# with a real AcroForm overlay on the IRS fillable Schedule C PDF.
# The widget map at ``skill/reference/schedule-c-acroform-map.json`` ties
# every Layer 1 field to its widget on the IRS PDF (page 1 + page 2).
# Special handling:
#
#   * Line F accounting method is a 3-checkbox radio; only the cash /
#     accrual / other matching the Layer 1 string is set.
#   * Line 32a / 32b at-risk box is two single checkboxes (mutually
#     exclusive in Layer 1).
#   * Line 33 inventory method is a 3-checkbox radio (cost / lower of
#     cost or market / other).
#   * Line 43 vehicle date is THREE widgets — Layer 1 stores Optional[str].
#     This wave splits "mm/dd/yy" if present.
#   * Part V other expenses is a 9-row repeating block exposed via
#     ``part_v_other_expenses_widgets`` in the map.
#
# Multi-business dispatch (``render_schedule_c_pdfs_all``) is preserved:
# one filled PDF per Schedule C on the return.

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEDULE_C_MAP_PATH = (
    _REPO_ROOT / "skill" / "reference" / "schedule-c-acroform-map.json"
)
_SCHEDULE_C_PDF_PATH = (
    _REPO_ROOT / "skill" / "reference" / "irs_forms" / "f1040sc.pdf"
)


def _format_decimal(value: Decimal) -> str:
    """Format a Decimal as a plain ``"27000.00"`` for AcroForm text fields.

    Zero is rendered as the empty string so the IRS PDF stays visually
    blank for cells the filer doesn't use. Negative values are rendered
    with a leading minus.
    """
    q = value.quantize(Decimal("0.01"))
    if q == Decimal("0.00"):
        return ""
    return f"{q:.2f}"


def _format_bool_optional(value: bool | None) -> str:
    if value is None:
        return ""
    return "Yes" if value else "No"


def _build_widget_values(
    fields: ScheduleCFields,
    widget_map: dict,
) -> dict[str, str]:
    """Translate a ``ScheduleCFields`` snapshot to a widget_name->str dict."""
    out: dict[str, str] = {}
    mapping = widget_map["mapping"]

    def text(sem: str, value) -> None:
        if sem not in mapping:
            return
        wn = mapping[sem]["widget_name"]
        if "*" in wn:
            return
        if value is None:
            out[wn] = ""
        elif isinstance(value, Decimal):
            out[wn] = _format_decimal(value)
        else:
            out[wn] = str(value)

    def checkbox(sem: str, on: bool) -> None:
        if sem not in mapping:
            return
        wn = mapping[sem]["widget_name"]
        out[wn] = "Yes" if on else ""

    # Header
    text("proprietor_name", fields.proprietor_name)
    text("line_a_principal_business_or_profession", fields.line_a_principal_business_or_profession)
    text("line_b_principal_business_code", fields.line_b_principal_business_code)
    text("line_c_business_name", fields.line_c_business_name)
    text("line_d_ein", fields.line_d_ein)
    text("line_e_business_address", fields.line_e_business_address)
    # Accounting method radio: select the matching cash/accrual/other terminal
    method = (fields.line_f_accounting_method or "").lower()
    method_map = {"cash": "c1_1[0]", "accrual": "c1_1[1]", "other": "c1_1[2]"}
    selected_terminal = method_map.get(method, "c1_1[0]")
    # The mapping stores the cash entry as line_f_accounting_method; we
    # need to fetch the right widget by terminal_name lookup against
    # mapping AND unmapped_widgets (the alternates live in unmapped).
    for entry in [mapping.get("line_f_accounting_method", {})] + widget_map.get(
        "unmapped_widgets", []
    ):
        if entry.get("terminal_name") == selected_terminal:
            out[entry["widget_name"]] = "Yes"
            break
    checkbox("line_g_material_participation", fields.line_g_material_participation)
    checkbox("line_h_started_or_acquired_this_year", fields.line_h_started_or_acquired_this_year)
    if fields.line_i_made_1099_payments_required is True:
        checkbox("line_i_made_1099_payments_required", True)
    if fields.line_j_filed_required_1099s is True:
        checkbox("line_j_filed_required_1099s", True)

    # Part I income
    text("line_1_gross_receipts", fields.line_1_gross_receipts)
    text("line_2_returns_and_allowances", fields.line_2_returns_and_allowances)
    text("line_3_net_receipts", fields.line_3_net_receipts)
    text("line_4_cost_of_goods_sold", fields.line_4_cost_of_goods_sold)
    text("line_5_gross_profit", fields.line_5_gross_profit)
    text("line_6_other_income", fields.line_6_other_income)
    text("line_7_gross_income", fields.line_7_gross_income)

    # Part II expenses
    text("line_8_advertising", fields.line_8_advertising)
    text("line_9_car_and_truck", fields.line_9_car_and_truck)
    text("line_10_commissions_and_fees", fields.line_10_commissions_and_fees)
    text("line_11_contract_labor", fields.line_11_contract_labor)
    text("line_12_depletion", fields.line_12_depletion)
    text("line_13_depreciation_section_179", fields.line_13_depreciation_section_179)
    text("line_14_employee_benefit_programs", fields.line_14_employee_benefit_programs)
    text("line_15_insurance_not_health", fields.line_15_insurance_not_health)
    text("line_16a_mortgage_interest", fields.line_16a_mortgage_interest)
    text("line_16b_other_interest", fields.line_16b_other_interest)
    text("line_17_legal_and_professional", fields.line_17_legal_and_professional)
    text("line_18_office_expense", fields.line_18_office_expense)
    text("line_19_pension_and_profit_sharing", fields.line_19_pension_and_profit_sharing)
    text("line_20a_rent_vehicles_machinery_equipment", fields.line_20a_rent_vehicles_machinery_equipment)
    text("line_20b_rent_other_business_property", fields.line_20b_rent_other_business_property)
    text("line_21_repairs_and_maintenance", fields.line_21_repairs_and_maintenance)
    text("line_22_supplies", fields.line_22_supplies)
    text("line_23_taxes_and_licenses", fields.line_23_taxes_and_licenses)
    text("line_24a_travel", fields.line_24a_travel)
    text("line_24b_meals_50pct_deductible", fields.line_24b_meals_50pct_deductible)
    text("line_25_utilities", fields.line_25_utilities)
    text("line_26_wages_less_employment_credits", fields.line_26_wages_less_employment_credits)
    text("line_27a_other_expenses", fields.line_27a_other_expenses)
    text("line_28_total_expenses", fields.line_28_total_expenses)
    text("line_29_tentative_profit_or_loss", fields.line_29_tentative_profit_or_loss)
    text("line_30_home_office_expense", fields.line_30_home_office_expense)
    text("line_31_net_profit_or_loss", fields.line_31_net_profit_or_loss)
    checkbox("line_32a_all_investment_at_risk", fields.line_32a_all_investment_at_risk)
    checkbox("line_32b_some_investment_not_at_risk", fields.line_32b_some_investment_not_at_risk)

    # Part III COGS (passthrough; line 42 = line 4 by Layer 1 design)
    text("line_35_beginning_inventory", _ZERO)  # always 0 in scaffold
    text("line_36_purchases_less_personal_use", _ZERO)
    text("line_37_cost_of_labor", _ZERO)
    text("line_38_materials_and_supplies", _ZERO)
    text("line_39_other_costs", _ZERO)
    text("line_40_sum_35_through_39", _ZERO)
    text("line_41_ending_inventory", _ZERO)
    text("line_42_cost_of_goods_sold", fields.line_42_cost_of_goods_sold)

    # Part V — other expenses (9-row repeating)
    part_v_widgets = widget_map.get("part_v_other_expenses_widgets", [])
    for i, (label, amount) in enumerate(fields.part_v_other_expenses):
        if i >= len(part_v_widgets):
            break
        slot = part_v_widgets[i]
        out[slot["description_widget"]["widget_name"]] = label
        out[slot["amount_widget"]["widget_name"]] = _format_decimal(amount)
    text("part_v_total", fields.part_v_total)

    return out


def render_schedule_c_pdf(fields: ScheduleCFields, out_path: Path) -> Path:
    """Render a single-business Schedule C PDF by AcroForm overlay.

    Loads the wave-5 widget map, validates the on-disk source PDF
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

    widget_map = load_widget_map_as_dict(_SCHEDULE_C_MAP_PATH)
    verify_pdf_sha256(_SCHEDULE_C_PDF_PATH, widget_map["source_pdf_sha256"])
    widget_values = _build_widget_values(fields, widget_map)
    return fill_acroform_pdf(_SCHEDULE_C_PDF_PATH, widget_values, Path(out_path))


def render_schedule_c_pdfs_all(
    return_: CanonicalReturn, out_dir: Path
) -> list[Path]:
    """Render one scaffold PDF per business on the return.

    Files are named ``schedule_c_{idx:02d}_{slug}.pdf`` where ``slug`` is
    a filesystem-safe derivative of the business name. Returns the list of
    written paths in input order. Empty list when the return has no
    ``schedules_c``.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_fields = compute_schedule_c_fields_all(return_)
    written: list[Path] = []
    for idx, fields in enumerate(all_fields):
        slug = _slugify(fields.line_c_business_name) or f"business_{idx:02d}"
        out_path = out_dir / f"schedule_c_{idx:02d}_{slug}.pdf"
        render_schedule_c_pdf(fields, out_path)
        written.append(out_path)
    return written


def _format_bool_optional(value: bool | None) -> str:
    if value is None:
        return ""
    return "Yes" if value else "No"


def _slugify(text: str) -> str:
    """Filesystem-safe slug: lowercase, alnum/underscore, no path seps.

    We keep it dumb — no unicode normalization — because this is only used
    for scaffold output filenames, not archival identifiers.
    """
    out_chars: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            out_chars.append(ch)
        elif ch in (" ", "-", "_"):
            out_chars.append("_")
        # everything else dropped
    slug = "".join(out_chars).strip("_")
    # collapse runs of underscores
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug
