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
# Layer 2: reportlab PDF rendering (SCAFFOLD)
# ---------------------------------------------------------------------------


def _format_decimal(value: Decimal) -> str:
    """Format a Decimal as a US currency-ish string ``27,000.00``."""
    q = value.quantize(Decimal("0.01"))
    return f"{q:,.2f}"


_HEADER_FIELD_NAMES = {
    "proprietor_name",
    "proprietor_ssn",
    "line_a_principal_business_or_profession",
    "line_b_principal_business_code",
    "line_c_business_name",
    "line_d_ein",
    "line_e_business_address",
    "line_f_accounting_method",
    "line_g_material_participation",
    "line_h_started_or_acquired_this_year",
    "line_i_made_1099_payments_required",
    "line_j_filed_required_1099s",
}

_PART_V_FIELD_NAMES = {"part_v_other_expenses", "part_v_total"}


def render_schedule_c_pdf(fields: ScheduleCFields, out_path: Path) -> Path:
    """Render a single-business Schedule C SCAFFOLD PDF using reportlab.

    This writes a tabular PDF listing the Schedule C header block, every
    Part I/II/III/IV line, and the Part V other-expenses detail. It is
    NOT a filled IRS Schedule C — real AcroForm overlay on the IRS
    fillable PDF is a follow-up task.

    Returns the ``out_path`` for convenience.
    """
    # Lazy import so callers that never render PDFs don't pay the cost
    # and test runners without reportlab can still exercise Layer 1.
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=letter,
        title=f"Schedule C — {fields.line_c_business_name} (TY2025 SCAFFOLD)",
    )
    styles = getSampleStyleSheet()

    story: list = []
    story.append(
        Paragraph(
            f"Schedule C (TY2025 - SCAFFOLD) — {fields.line_c_business_name}",
            styles["Title"],
        )
    )
    story.append(
        Paragraph(
            "This is a scaffold rendering, not a filed IRS form.",
            styles["Italic"],
        )
    )
    story.append(Spacer(1, 12))

    # ---- Header block ------------------------------------------------
    header_rows = [
        ["A. Principal business or profession", fields.line_a_principal_business_or_profession],
        ["B. Principal business code", fields.line_b_principal_business_code or ""],
        ["C. Business name", fields.line_c_business_name],
        ["D. EIN", fields.line_d_ein or ""],
        ["E. Business address", fields.line_e_business_address],
        ["F. Accounting method", fields.line_f_accounting_method],
        ["G. Materially participated", "Yes" if fields.line_g_material_participation else "No"],
        [
            "H. Started or acquired this year",
            "Yes" if fields.line_h_started_or_acquired_this_year else "No",
        ],
        [
            "I. Required to file 1099s",
            _format_bool_optional(fields.line_i_made_1099_payments_required),
        ],
        [
            "J. Will file required 1099s",
            _format_bool_optional(fields.line_j_filed_required_1099s),
        ],
    ]
    header_table = Table(header_rows, colWidths=[180, 320])
    header_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story.append(header_table)
    story.append(Spacer(1, 12))

    # ---- Line-item table (Parts I-IV) --------------------------------
    line_rows: list[list] = [["Line", "Description", "Amount"]]
    for f in dc_fields(fields):
        if f.name in _HEADER_FIELD_NAMES or f.name in _PART_V_FIELD_NAMES:
            continue
        value = getattr(fields, f.name)
        if not isinstance(value, Decimal):
            # Skip booleans / strings on Parts I-IV (e.g. line_32a/b, line_33).
            continue
        parts = f.name.split("_")
        # parts == ["line", "27a", "other", "expenses"]
        line_number = parts[1] if len(parts) >= 2 else ""
        desc = " ".join(parts[2:]).replace("_", " ")
        line_rows.append([line_number, desc, _format_decimal(value)])

    line_table = Table(line_rows, colWidths=[60, 340, 100])
    line_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (2, 1), (2, -1), "RIGHT"),
            ]
        )
    )
    story.append(line_table)
    story.append(Spacer(1, 12))

    # ---- Part V — other expenses detail ------------------------------
    story.append(Paragraph("Part V — Other Expenses", styles["Heading3"]))
    part_v_rows: list[list] = [["Description", "Amount"]]
    for label, amount in fields.part_v_other_expenses:
        part_v_rows.append([label, _format_decimal(amount)])
    if not fields.part_v_other_expenses:
        part_v_rows.append(["(none)", _format_decimal(_ZERO)])
    part_v_rows.append(["Total (to Part II, line 48)", _format_decimal(fields.part_v_total)])

    part_v_table = Table(part_v_rows, colWidths=[340, 100])
    part_v_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (1, 1), (1, -1), "RIGHT"),
            ]
        )
    )
    story.append(part_v_table)

    doc.build(story)
    return out_path


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
