"""Tests for skill.scripts.output.schedule_c — Schedule C PDF scaffold.

Two layers under test:

* Layer 1 — ``compute_schedule_c_fields`` / ``compute_schedule_c_fields_all``:
  assert that values on the returned dataclass(es) match the expected
  Schedule C line numbers for a variety of fixtures. These tests do NOT
  duplicate the engine's golden diff — they only check that a
  ``ScheduleC`` model is correctly routed onto Schedule C line names,
  and that ``schedule_c_net_profit`` is the single source of truth for
  line 31.

* Layer 2 — ``render_schedule_c_pdf`` / ``render_schedule_c_pdfs_all``:
  write scaffold PDFs, reopen with pypdf, and assert the extracted text
  contains header and numeric values.

Coverage targets:
    * Golden-fixture net_profit reconciliation (se_home_office)
    * Multi-business dispatch (2+ ScheduleC on a single return)
    * Home-office deduction presence on line 30
    * Address / EIN / accounting-method header routing
    * Part II subtotal via engine._sch_c_total_expenses
    * Part V other-expenses detail flow
    * No-Schedule-C return returns empty list from dispatch helper
    * Per-business PDF filename collision avoidance
    * Net loss (expenses > income) rendering correctly as negative
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.calc.engine import (
    _sch_c_total_expenses,
    schedule_c_net_profit,
)
from skill.scripts.models import CanonicalReturn, ScheduleC, ScheduleCExpenses
from skill.scripts.output.schedule_c import (
    ScheduleCFields,
    compute_schedule_c_fields,
    compute_schedule_c_fields_all,
    render_schedule_c_pdf,
    render_schedule_c_pdfs_all,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _load_return(fixtures_dir: Path, name: str) -> CanonicalReturn:
    data = json.loads((fixtures_dir / name / "input.json").read_text())
    return CanonicalReturn.model_validate(data)


def _minimal_schedule_c(**overrides) -> ScheduleC:
    """Build a minimal ScheduleC for targeted tests. Overrides merge in."""
    base = {
        "business_name": "Test Co",
        "principal_business_or_profession": "Widgets",
    }
    base.update(overrides)
    return ScheduleC.model_validate(base)


def _canonical_with_schedules_c(scs: list[dict]) -> CanonicalReturn:
    """Build a CanonicalReturn carrying the given ScheduleC dict blobs."""
    return CanonicalReturn.model_validate(
        {
            "schema_version": "0.1.0",
            "tax_year": 2025,
            "filing_status": "single",
            "taxpayer": {
                "first_name": "Multi",
                "last_name": "Biz",
                "ssn": "111-22-3333",
                "date_of_birth": "1985-01-01",
                "is_blind": False,
                "is_age_65_or_older": False,
            },
            "address": {
                "street1": "1 Main",
                "city": "Austin",
                "state": "TX",
                "zip": "78701",
            },
            "schedules_c": scs,
            "itemize_deductions": False,
        }
    )


# ---------------------------------------------------------------------------
# Layer 1 — golden fixture (se_home_office)
# ---------------------------------------------------------------------------


def test_se_home_office_layer1_header_and_income(fixtures_dir: Path) -> None:
    return_ = _load_return(fixtures_dir, "se_home_office")
    all_fields = compute_schedule_c_fields_all(return_)

    assert len(all_fields) == 1
    fields = all_fields[0]
    assert isinstance(fields, ScheduleCFields)

    # Header — matches input.json verbatim
    assert fields.line_c_business_name == "Freeman Consulting LLC"
    assert fields.line_a_principal_business_or_profession == "Management consulting"
    assert fields.line_f_accounting_method == "cash"
    assert fields.line_g_material_participation is True
    assert fields.line_32a_all_investment_at_risk is True
    assert fields.line_32b_some_investment_not_at_risk is False

    # Part I
    assert fields.line_1_gross_receipts == Decimal("120000.00")
    assert fields.line_2_returns_and_allowances == Decimal("0")
    assert fields.line_3_net_receipts == Decimal("120000.00")
    assert fields.line_4_cost_of_goods_sold == Decimal("0")
    assert fields.line_5_gross_profit == Decimal("120000.00")
    assert fields.line_6_other_income == Decimal("0")
    assert fields.line_7_gross_income == Decimal("120000.00")


def test_se_home_office_layer1_part_ii_totals(fixtures_dir: Path) -> None:
    """Expenses and net profit must match the golden fixture's hand-check."""
    return_ = _load_return(fixtures_dir, "se_home_office")
    fields = compute_schedule_c_fields_all(return_)[0]

    # Per expected.json hand_check:
    #   Part II total expenses = 3000 + 5000 + 2000 + 4000 + 2500 + 8000 + 1500 + 1000 = 27000
    assert fields.line_28_total_expenses == Decimal("27000.00")
    # Line 29 = Line 7 - Line 28 = 120000 - 27000 = 93000
    assert fields.line_29_tentative_profit_or_loss == Decimal("93000.00")
    # Line 30 = 3000 home office
    assert fields.line_30_home_office_expense == Decimal("3000.00")
    # Line 31 = net profit = 90000 — the regression-lock value from expected.json
    assert fields.line_31_net_profit_or_loss == Decimal("90000.00")


def test_se_home_office_line31_matches_engine_helper(fixtures_dir: Path) -> None:
    """Layer 1 must NOT re-implement net profit: must equal engine helper."""
    return_ = _load_return(fixtures_dir, "se_home_office")
    sc = return_.schedules_c[0]
    fields = compute_schedule_c_fields(sc)
    # Bit-for-bit: the renderer delegates line 31 to schedule_c_net_profit.
    assert fields.line_31_net_profit_or_loss == schedule_c_net_profit(sc)


def test_se_home_office_line28_matches_engine_helper(fixtures_dir: Path) -> None:
    """Layer 1 must NOT re-implement total expenses: must equal engine helper."""
    return_ = _load_return(fixtures_dir, "se_home_office")
    sc = return_.schedules_c[0]
    fields = compute_schedule_c_fields(sc)
    assert fields.line_28_total_expenses == _sch_c_total_expenses(sc.expenses)


def test_se_home_office_individual_expense_line_routing(fixtures_dir: Path) -> None:
    return_ = _load_return(fixtures_dir, "se_home_office")
    fields = compute_schedule_c_fields_all(return_)[0]

    # Each Part II expense line must echo the input value.
    assert fields.line_8_advertising == Decimal("3000.00")
    assert fields.line_9_car_and_truck == Decimal("5000.00")
    assert fields.line_17_legal_and_professional == Decimal("2000.00")
    assert fields.line_18_office_expense == Decimal("4000.00")
    assert fields.line_22_supplies == Decimal("2500.00")
    assert fields.line_24a_travel == Decimal("8000.00")
    assert fields.line_24b_meals_50pct_deductible == Decimal("1500.00")
    assert fields.line_25_utilities == Decimal("1000.00")
    # Unused lines must be zero.
    assert fields.line_10_commissions_and_fees == Decimal("0")
    assert fields.line_12_depletion == Decimal("0")
    assert fields.line_26_wages_less_employment_credits == Decimal("0")


# ---------------------------------------------------------------------------
# Layer 1 — synthetic single-business cases
# ---------------------------------------------------------------------------


def test_returns_and_cogs_flow_to_lines_3_and_5() -> None:
    sc = _minimal_schedule_c(
        line1_gross_receipts="100000.00",
        line2_returns_and_allowances="5000.00",
        line4_cost_of_goods_sold="20000.00",
        line6_other_income="1000.00",
    )
    fields = compute_schedule_c_fields(sc)

    assert fields.line_3_net_receipts == Decimal("95000.00")  # 100k - 5k
    assert fields.line_5_gross_profit == Decimal("75000.00")  # 95k - 20k
    assert fields.line_7_gross_income == Decimal("76000.00")  # 75k + 1k other
    # Part III scaffold passthrough: line 42 mirrors line 4.
    assert fields.line_42_cost_of_goods_sold == Decimal("20000.00")


def test_net_loss_renders_correctly_as_negative() -> None:
    sc = _minimal_schedule_c(
        line1_gross_receipts="10000.00",
        expenses={
            "line22_supplies": "25000.00",
        },
        line30_home_office_expense="500.00",
    )
    fields = compute_schedule_c_fields(sc)

    # Line 29 = 10000 - 25000 = -15000
    assert fields.line_29_tentative_profit_or_loss == Decimal("-15000.00")
    # Line 31 = -15000 - 500 = -15500 (net loss)
    assert fields.line_31_net_profit_or_loss == Decimal("-15500.00")
    # Engine agreement
    assert fields.line_31_net_profit_or_loss == schedule_c_net_profit(sc)


def test_header_routes_ein_address_and_accounting_method() -> None:
    sc = _minimal_schedule_c(
        business_name="Acme Widgets",
        principal_business_or_profession="Widget design",
        principal_business_code="541511",
        ein="12-3456789",
        business_address={
            "street1": "99 Inventor Way",
            "city": "Austin",
            "state": "TX",
            "zip": "78701",
        },
        accounting_method="accrual",
        material_participation=True,
        started_or_acquired_this_year=True,
        made_1099_payments=True,
        filed_required_1099s=False,
    )
    fields = compute_schedule_c_fields(sc)

    assert fields.line_a_principal_business_or_profession == "Widget design"
    assert fields.line_b_principal_business_code == "541511"
    assert fields.line_c_business_name == "Acme Widgets"
    assert fields.line_d_ein == "12-3456789"
    assert "99 Inventor Way" in fields.line_e_business_address
    assert "Austin" in fields.line_e_business_address
    assert "TX" in fields.line_e_business_address
    assert fields.line_f_accounting_method == "accrual"
    assert fields.line_h_started_or_acquired_this_year is True
    assert fields.line_i_made_1099_payments_required is True
    assert fields.line_j_filed_required_1099s is False


def test_header_no_address_returns_empty_string() -> None:
    sc = _minimal_schedule_c()
    fields = compute_schedule_c_fields(sc)
    assert fields.line_e_business_address == ""
    # line_d_ein is optional
    assert fields.line_d_ein is None
    # line_b_principal_business_code is optional
    assert fields.line_b_principal_business_code is None


def test_some_investment_not_at_risk_flag() -> None:
    sc = _minimal_schedule_c(line32_at_risk_box="some_not_at_risk")
    fields = compute_schedule_c_fields(sc)
    assert fields.line_32a_all_investment_at_risk is False
    assert fields.line_32b_some_investment_not_at_risk is True


def test_part_v_other_expenses_detail_flow_and_total() -> None:
    sc = _minimal_schedule_c(
        line1_gross_receipts="50000.00",
        expenses={
            "line22_supplies": "1000.00",
            "other_expense_detail": {
                "merchant_processor_fees": "250.00",
                "subscriptions": "400.00",
            },
        },
    )
    fields = compute_schedule_c_fields(sc)

    # Part V items preserved in tuple form (stable for rendering).
    assert len(fields.part_v_other_expenses) == 2
    labels = {label for label, _ in fields.part_v_other_expenses}
    assert labels == {"merchant_processor_fees", "subscriptions"}
    assert fields.part_v_total == Decimal("650.00")
    # Part II total expenses must include the Part V detail (engine behavior)
    assert fields.line_28_total_expenses == Decimal("1650.00")


def test_empty_other_expense_detail_yields_zero_part_v_total() -> None:
    sc = _minimal_schedule_c()
    fields = compute_schedule_c_fields(sc)
    assert fields.part_v_other_expenses == ()
    assert fields.part_v_total == Decimal("0")


# ---------------------------------------------------------------------------
# Layer 1 — multi-business dispatch
# ---------------------------------------------------------------------------


def test_compute_all_preserves_input_order() -> None:
    return_ = _canonical_with_schedules_c(
        [
            {
                "business_name": "First Biz",
                "principal_business_or_profession": "Consulting",
                "line1_gross_receipts": "40000.00",
            },
            {
                "business_name": "Second Biz",
                "principal_business_or_profession": "Baking",
                "line1_gross_receipts": "25000.00",
            },
            {
                "business_name": "Third Biz",
                "principal_business_or_profession": "Graphic design",
                "line1_gross_receipts": "10000.00",
            },
        ]
    )
    all_fields = compute_schedule_c_fields_all(return_)

    assert [f.line_c_business_name for f in all_fields] == [
        "First Biz",
        "Second Biz",
        "Third Biz",
    ]
    assert all_fields[0].line_1_gross_receipts == Decimal("40000.00")
    assert all_fields[1].line_1_gross_receipts == Decimal("25000.00")
    assert all_fields[2].line_1_gross_receipts == Decimal("10000.00")


def test_compute_all_net_profits_independent() -> None:
    """Each business's line 31 must come from ITS OWN schedule_c_net_profit."""
    return_ = _canonical_with_schedules_c(
        [
            {
                "business_name": "Profitable LLC",
                "principal_business_or_profession": "Tutoring",
                "line1_gross_receipts": "50000.00",
                "expenses": {"line22_supplies": "5000.00"},
                "line30_home_office_expense": "1000.00",
            },
            {
                "business_name": "Money Pit Inc",
                "principal_business_or_profession": "Art",
                "line1_gross_receipts": "2000.00",
                "expenses": {"line22_supplies": "8000.00"},
            },
        ]
    )
    all_fields = compute_schedule_c_fields_all(return_)

    # Each field matches its own ScheduleC via schedule_c_net_profit.
    for i, sc in enumerate(return_.schedules_c):
        assert all_fields[i].line_31_net_profit_or_loss == schedule_c_net_profit(sc)
    # Spot-check values.
    assert all_fields[0].line_31_net_profit_or_loss == Decimal("44000.00")  # 50k - 5k - 1k
    assert all_fields[1].line_31_net_profit_or_loss == Decimal("-6000.00")  # 2k - 8k


def test_no_schedules_c_returns_empty_list() -> None:
    return_ = _canonical_with_schedules_c([])
    assert compute_schedule_c_fields_all(return_) == []


# ---------------------------------------------------------------------------
# Layer 2 — scaffold PDF rendering
# ---------------------------------------------------------------------------


def test_render_scaffold_produces_readable_pdf(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    pypdf = pytest.importorskip("pypdf")

    return_ = _load_return(fixtures_dir, "se_home_office")
    fields = compute_schedule_c_fields_all(return_)[0]

    out_path = tmp_path / "test_sch_c.pdf"
    result_path = render_schedule_c_pdf(fields, out_path)

    assert result_path == out_path
    assert out_path.exists()
    assert out_path.stat().st_size > 0

    reader = pypdf.PdfReader(str(out_path))
    assert len(reader.pages) >= 1
    text = "".join(page.extract_text() or "" for page in reader.pages)

    assert "Schedule C" in text
    assert "Freeman Consulting LLC" in text
    # One of the hand-checked amounts must show up
    assert ("120,000" in text) or ("120000" in text)
    # Home office line must appear in scaffold output
    assert ("3,000" in text) or ("3000" in text)
    # Scaffold notice
    assert "scaffold" in text.lower()


def test_render_all_writes_one_file_per_business(tmp_path: Path) -> None:
    pypdf = pytest.importorskip("pypdf")

    return_ = _canonical_with_schedules_c(
        [
            {
                "business_name": "Consulting Pros",
                "principal_business_or_profession": "Consulting",
                "line1_gross_receipts": "40000.00",
            },
            {
                "business_name": "Pie Shop",
                "principal_business_or_profession": "Baking",
                "line1_gross_receipts": "20000.00",
            },
        ]
    )

    paths = render_schedule_c_pdfs_all(return_, tmp_path)
    assert len(paths) == 2
    assert all(p.exists() for p in paths)
    # Filenames must differ (no collision between the two businesses).
    assert paths[0] != paths[1]
    assert len({p.name for p in paths}) == 2

    # Verify each PDF contains its own business name.
    names = []
    for p in paths:
        reader = pypdf.PdfReader(str(p))
        text = "".join(page.extract_text() or "" for page in reader.pages)
        names.append(text)
    assert "Consulting Pros" in names[0]
    assert "Pie Shop" in names[1]


def test_render_all_empty_schedules_c_writes_nothing(tmp_path: Path) -> None:
    return_ = _canonical_with_schedules_c([])
    paths = render_schedule_c_pdfs_all(return_, tmp_path)
    assert paths == []


def test_render_pdf_includes_part_v_detail(tmp_path: Path) -> None:
    pypdf = pytest.importorskip("pypdf")

    sc = _minimal_schedule_c(
        business_name="Part V Test Co",
        line1_gross_receipts="50000.00",
        expenses={
            "other_expense_detail": {
                "merchant_fees": "123.45",
            },
        },
    )
    fields = compute_schedule_c_fields(sc)

    out_path = tmp_path / "part_v.pdf"
    render_schedule_c_pdf(fields, out_path)

    reader = pypdf.PdfReader(str(out_path))
    text = "".join(page.extract_text() or "" for page in reader.pages)
    # Label from other_expense_detail must reach the rendered output
    assert "merchant_fees" in text
    assert ("123.45" in text) or ("123" in text)
    assert "Part V" in text


def test_render_pdf_header_shows_ein_and_code(tmp_path: Path) -> None:
    pypdf = pytest.importorskip("pypdf")

    sc = _minimal_schedule_c(
        business_name="Headered Biz",
        principal_business_or_profession="Consulting",
        principal_business_code="541611",
        ein="98-7654321",
    )
    fields = compute_schedule_c_fields(sc)

    out_path = tmp_path / "headered.pdf"
    render_schedule_c_pdf(fields, out_path)

    reader = pypdf.PdfReader(str(out_path))
    text = "".join(page.extract_text() or "" for page in reader.pages)
    assert "Headered Biz" in text
    assert "541611" in text
    assert "98-7654321" in text


# ---------------------------------------------------------------------------
# Integration: engine.compute pass-through doesn't break Layer 1
# ---------------------------------------------------------------------------


def test_compute_then_render_layer1_idempotent(fixtures_dir: Path) -> None:
    """Running engine.compute on the return must not change Layer 1 output."""
    from skill.scripts.calc.engine import compute

    return_ = _load_return(fixtures_dir, "se_home_office")
    pre_fields = compute_schedule_c_fields_all(return_)[0]

    computed = compute(return_)
    post_fields = compute_schedule_c_fields_all(computed)[0]

    # Engine.compute doesn't mutate schedules_c, so Layer 1 output is stable.
    assert pre_fields == post_fields
    assert post_fields.line_31_net_profit_or_loss == Decimal("90000.00")
