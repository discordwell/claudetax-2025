"""Tests for skill.scripts.output.schedule_1 — Schedule 1 scaffold.

Two layers under test:

* Layer 1 — ``compute_schedule_1_fields`` / ``schedule_1_required``:
  assert that values on the returned dataclass match the expected
  Schedule 1 line numbers for handcrafted CanonicalReturns. Tests
  delegate to engine helpers (``schedule_c_net_profit``,
  ``schedule_e_total_net``, ``_sum_adjustments``) to avoid re-deriving
  values.

* Layer 2 — ``render_schedule_1_pdf``: write a scaffold PDF, reopen
  with ``pypdf``, and assert the extracted text contains the header and
  numeric values.

Scenarios covered:
1. Self-employed: Schedule C profit -> line 3, SE tax deduction -> line 15
2. Rental income: Schedule E total -> line 5
3. Unemployment: 1099-G box 1 -> line 7
4. HSA + student loan interest adjustments
5. OBBBA: tips/overtime -> line 9, senior deduction -> line 25
6. Full return with multiple Part I + Part II items -> verify totals
7. Layer 2 scaffold renders
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from skill.scripts.models import AdjustmentsToIncome, CanonicalReturn
from skill.scripts.output.schedule_1 import (
    Schedule1Fields,
    compute_schedule_1_fields,
    render_schedule_1_pdf,
    schedule_1_required,
)


_ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_return_dict() -> dict[str, Any]:
    """Minimal single-filer canonical return dict."""
    return {
        "schema_version": "0.1.0",
        "tax_year": 2025,
        "filing_status": "single",
        "taxpayer": {
            "first_name": "Sam",
            "last_name": "Scheduler",
            "ssn": "222-33-4444",
            "date_of_birth": "1980-03-15",
            "is_blind": False,
            "is_age_65_or_older": False,
        },
        "address": {
            "street1": "100 Tax Ct",
            "city": "Denver",
            "state": "CO",
            "zip": "80201",
        },
        "itemize_deductions": False,
    }


def _schedule_c(gross: str, expenses: str = "0") -> dict[str, Any]:
    """Build a minimal Schedule C dict."""
    return {
        "proprietor_is_taxpayer": True,
        "business_name": "Side Hustle LLC",
        "principal_business_or_profession": "Consulting",
        "accounting_method": "cash",
        "material_participation": True,
        "line1_gross_receipts": gross,
        "line2_returns_and_allowances": "0",
        "line4_cost_of_goods_sold": "0",
        "line6_other_income": "0",
        "expenses": {
            "line8_advertising": expenses,
        },
        "line30_home_office_expense": "0",
        "line32_at_risk_box": "all_at_risk",
    }


def _schedule_e_property(rents: str, expenses_insurance: str = "0") -> dict[str, Any]:
    """Build a minimal Schedule E property."""
    return {
        "address": {
            "street1": "123 Rental Ave",
            "city": "Denver",
            "state": "CO",
            "zip": "80201",
        },
        "property_type": "single_family",
        "fair_rental_days": 365,
        "personal_use_days": 0,
        "rents_received": rents,
        "royalties_received": "0",
        "advertising": "0",
        "auto_and_travel": "0",
        "cleaning_and_maintenance": "0",
        "commissions": "0",
        "insurance": expenses_insurance,
        "legal_and_professional": "0",
        "management_fees": "0",
        "mortgage_interest_to_banks": "0",
        "other_interest": "0",
        "repairs": "0",
        "supplies": "0",
        "taxes": "0",
        "utilities": "0",
        "depreciation": "0",
    }


def _make_return(**overrides: Any) -> CanonicalReturn:
    """Build a CanonicalReturn with optional field overrides."""
    data = _base_return_dict()
    data.update(overrides)
    return CanonicalReturn.model_validate(data)


# ---------------------------------------------------------------------------
# Test 1: Self-employed — Schedule C profit flows to line 3,
#          SE tax deduction flows to line 15
# ---------------------------------------------------------------------------


class TestSelfEmployed:
    def test_schedule_c_profit_flows_to_line_3(self) -> None:
        """Schedule C net profit appears on Schedule 1 line 3."""
        ret = _make_return(schedules_c=[_schedule_c("50000")])
        fields = compute_schedule_1_fields(ret)

        assert fields.line_3_business_income == Decimal("50000")
        assert fields.line_10_total_additional_income >= Decimal("50000")

    def test_schedule_c_with_expenses(self) -> None:
        """Schedule C net profit = gross - expenses."""
        ret = _make_return(schedules_c=[_schedule_c("80000", "20000")])
        fields = compute_schedule_1_fields(ret)

        # Net profit = 80000 - 20000 = 60000
        assert fields.line_3_business_income == Decimal("60000")

    def test_se_tax_deduction_line_15(self) -> None:
        """Deductible half of SE tax appears on line 15."""
        ret = _make_return(schedules_c=[_schedule_c("90000")])
        fields = compute_schedule_1_fields(ret)

        # SE computation: 90000 * 0.9235 = 83115
        # SS portion: 83115 * 0.124 = 10306.26
        # Medicare: 83115 * 0.029 = 2410.335
        # SE tax: 12716.595
        # Half: 6358.2975
        assert fields.line_15_deductible_se_tax > _ZERO
        expected_half_se = Decimal("6358.297500000")
        assert fields.line_15_deductible_se_tax == expected_half_se

    def test_multiple_schedule_cs(self) -> None:
        """Multiple Schedule Cs are summed on line 3."""
        ret = _make_return(
            schedules_c=[
                _schedule_c("30000"),
                _schedule_c("20000", "5000"),
            ]
        )
        fields = compute_schedule_1_fields(ret)

        # 30000 + (20000 - 5000) = 45000
        assert fields.line_3_business_income == Decimal("45000")


# ---------------------------------------------------------------------------
# Test 2: Rental income — Schedule E total flows to line 5
# ---------------------------------------------------------------------------


class TestRentalIncome:
    def test_schedule_e_flows_to_line_5(self) -> None:
        """Schedule E net rental income appears on line 5."""
        ret = _make_return(
            schedules_e=[
                {
                    "properties": [_schedule_e_property("24000", "4000")],
                }
            ]
        )
        fields = compute_schedule_1_fields(ret)

        # Net = 24000 - 4000 = 20000
        assert fields.line_5_rental_real_estate == Decimal("20000")

    def test_multiple_properties(self) -> None:
        """Multiple Schedule E properties sum into line 5."""
        ret = _make_return(
            schedules_e=[
                {
                    "properties": [
                        _schedule_e_property("12000", "2000"),
                        _schedule_e_property("18000", "3000"),
                    ],
                }
            ]
        )
        fields = compute_schedule_1_fields(ret)

        # (12000 - 2000) + (18000 - 3000) = 10000 + 15000 = 25000
        assert fields.line_5_rental_real_estate == Decimal("25000")


# ---------------------------------------------------------------------------
# Test 3: Unemployment — 1099-G box 1 flows to line 7
# ---------------------------------------------------------------------------


class TestUnemployment:
    def test_1099g_unemployment_flows_to_line_7(self) -> None:
        """1099-G box 1 unemployment compensation appears on line 7."""
        ret = _make_return(
            forms_1099_g=[
                {
                    "payer_name": "State Unemployment Office",
                    "box1_unemployment_compensation": "8500",
                }
            ]
        )
        fields = compute_schedule_1_fields(ret)

        assert fields.line_7_unemployment == Decimal("8500")

    def test_multiple_1099g_unemployment(self) -> None:
        """Multiple 1099-G forms sum on line 7."""
        ret = _make_return(
            forms_1099_g=[
                {
                    "payer_name": "State A",
                    "box1_unemployment_compensation": "5000",
                },
                {
                    "payer_name": "State B",
                    "box1_unemployment_compensation": "3000",
                },
            ]
        )
        fields = compute_schedule_1_fields(ret)

        assert fields.line_7_unemployment == Decimal("8000")

    def test_1099g_taxable_refund_flows_to_line_1(self) -> None:
        """1099-G box 2 state/local tax refund appears on line 1."""
        ret = _make_return(
            forms_1099_g=[
                {
                    "payer_name": "State Tax Dept",
                    "box2_state_or_local_income_tax_refund": "1200",
                }
            ]
        )
        fields = compute_schedule_1_fields(ret)

        assert fields.line_1_taxable_refunds == Decimal("1200")


# ---------------------------------------------------------------------------
# Test 4: HSA + student loan interest adjustments
# ---------------------------------------------------------------------------


class TestHSAStudentLoan:
    def test_hsa_deduction_line_13(self) -> None:
        """HSA deduction appears on line 13."""
        ret = _make_return(
            adjustments={
                "hsa_deduction": "3650",
            }
        )
        fields = compute_schedule_1_fields(ret)

        assert fields.line_13_hsa_deduction == Decimal("3650")

    def test_student_loan_interest_line_21(self) -> None:
        """Student loan interest deduction appears on line 21."""
        ret = _make_return(
            adjustments={
                "student_loan_interest": "2500",
            }
        )
        fields = compute_schedule_1_fields(ret)

        assert fields.line_21_student_loan_interest == Decimal("2500")

    def test_hsa_and_student_loan_combined(self) -> None:
        """HSA + student loan both appear in line 26 total."""
        ret = _make_return(
            adjustments={
                "hsa_deduction": "3650",
                "student_loan_interest": "2500",
            }
        )
        fields = compute_schedule_1_fields(ret)

        assert fields.line_13_hsa_deduction == Decimal("3650")
        assert fields.line_21_student_loan_interest == Decimal("2500")
        # Total adjustments includes both
        assert fields.line_26_total_adjustments >= Decimal("6150")


# ---------------------------------------------------------------------------
# Test 5: OBBBA — tips/overtime on line 9, senior deduction on line 25
# ---------------------------------------------------------------------------


class TestOBBBA:
    def test_tips_and_overtime_flow_to_line_9(self) -> None:
        """OBBBA qualified tips + overtime deductions appear on line 9."""
        ret = _make_return(
            adjustments={
                "qualified_tips_deduction_schedule_1a": "15000",
                "qualified_overtime_deduction_schedule_1a": "8000",
            }
        )
        fields = compute_schedule_1_fields(ret)

        assert fields.line_9_obbba_schedule_1a == Decimal("23000")

    def test_senior_deduction_flows_to_line_25(self) -> None:
        """OBBBA senior deduction appears on line 25."""
        ret = _make_return(
            adjustments={
                "senior_deduction_obbba": "6000",
            }
        )
        fields = compute_schedule_1_fields(ret)

        assert fields.line_25_obbba_adjustments == Decimal("6000")

    def test_tips_overtime_senior_all_present(self) -> None:
        """All OBBBA items appear on their respective lines."""
        ret = _make_return(
            adjustments={
                "qualified_tips_deduction_schedule_1a": "10000",
                "qualified_overtime_deduction_schedule_1a": "5000",
                "senior_deduction_obbba": "6000",
            }
        )
        fields = compute_schedule_1_fields(ret)

        # Line 9 = tips + overtime
        assert fields.line_9_obbba_schedule_1a == Decimal("15000")
        # Line 25 = senior deduction
        assert fields.line_25_obbba_adjustments == Decimal("6000")
        # Both contribute to totals
        assert fields.line_10_total_additional_income >= Decimal("15000")
        assert fields.line_26_total_adjustments >= Decimal("6000")


# ---------------------------------------------------------------------------
# Test 6: Full return with multiple Part I + Part II items -> totals
# ---------------------------------------------------------------------------


class TestFullReturn:
    def test_mixed_income_and_adjustments(self) -> None:
        """A return with multiple income sources and adjustments has correct totals."""
        ret = _make_return(
            schedules_c=[_schedule_c("60000", "10000")],  # net = 50000
            schedules_e=[
                {
                    "properties": [_schedule_e_property("18000", "3000")],
                }
            ],  # net = 15000
            forms_1099_g=[
                {
                    "payer_name": "State UC",
                    "box1_unemployment_compensation": "4000",
                    "box2_state_or_local_income_tax_refund": "500",
                }
            ],
            adjustments={
                "educator_expenses": "300",
                "hsa_deduction": "3650",
                "student_loan_interest": "2500",
                "ira_deduction": "6000",
            },
        )
        fields = compute_schedule_1_fields(ret)

        # Part I
        assert fields.line_1_taxable_refunds == Decimal("500")
        assert fields.line_3_business_income == Decimal("50000")
        assert fields.line_5_rental_real_estate == Decimal("15000")
        assert fields.line_7_unemployment == Decimal("4000")

        # Line 10 total = 500 + 50000 + 15000 + 4000 = 69500
        assert fields.line_10_total_additional_income == Decimal("69500")

        # Part II
        assert fields.line_11_educator_expenses == Decimal("300")
        assert fields.line_13_hsa_deduction == Decimal("3650")
        assert fields.line_15_deductible_se_tax > _ZERO  # SE from Schedule C
        assert fields.line_20_ira_deduction == Decimal("6000")
        assert fields.line_21_student_loan_interest == Decimal("2500")

        # Line 26 total >= sum of named items
        named_adj = Decimal("300") + Decimal("3650") + Decimal("2500") + Decimal("6000")
        assert fields.line_26_total_adjustments >= named_adj

        # Net should be line 10 - line 26
        assert fields.schedule_1_net == (
            fields.line_10_total_additional_income - fields.line_26_total_adjustments
        )

    def test_adjustments_only_return(self) -> None:
        """A return with only adjustments (no additional income) has negative net."""
        ret = _make_return(
            adjustments={
                "student_loan_interest": "2500",
                "educator_expenses": "300",
            }
        )
        fields = compute_schedule_1_fields(ret)

        assert fields.line_10_total_additional_income == _ZERO
        assert fields.line_26_total_adjustments == Decimal("2800")
        assert fields.schedule_1_net == Decimal("-2800")

    def test_empty_return_all_zeros(self) -> None:
        """An empty return produces all-zero Schedule 1 fields."""
        ret = _make_return()
        fields = compute_schedule_1_fields(ret)

        assert fields.line_10_total_additional_income == _ZERO
        assert fields.line_26_total_adjustments == _ZERO
        assert fields.schedule_1_net == _ZERO

    def test_header_populated(self) -> None:
        """Taxpayer name and SSN flow to the header."""
        ret = _make_return()
        fields = compute_schedule_1_fields(ret)

        assert fields.taxpayer_name == "Sam Scheduler"
        assert fields.taxpayer_ssn == "222-33-4444"


# ---------------------------------------------------------------------------
# schedule_1_required gate
# ---------------------------------------------------------------------------


class TestSchedule1Required:
    def test_empty_return_not_required(self) -> None:
        """An empty return does not need Schedule 1."""
        ret = _make_return()
        assert schedule_1_required(ret) is False

    def test_schedule_c_triggers(self) -> None:
        ret = _make_return(schedules_c=[_schedule_c("1000")])
        assert schedule_1_required(ret) is True

    def test_schedule_e_triggers(self) -> None:
        ret = _make_return(
            schedules_e=[{"properties": [_schedule_e_property("12000")]}]
        )
        assert schedule_1_required(ret) is True

    def test_1099g_triggers(self) -> None:
        ret = _make_return(
            forms_1099_g=[
                {
                    "payer_name": "State",
                    "box1_unemployment_compensation": "100",
                }
            ]
        )
        assert schedule_1_required(ret) is True

    def test_adjustments_trigger(self) -> None:
        ret = _make_return(adjustments={"hsa_deduction": "1000"})
        assert schedule_1_required(ret) is True

    def test_other_income_triggers(self) -> None:
        ret = _make_return(other_income={"gambling": "500"})
        assert schedule_1_required(ret) is True


# ---------------------------------------------------------------------------
# Test 7: Layer 2 scaffold renders
# ---------------------------------------------------------------------------


class TestRenderPDF:
    def test_scaffold_pdf_written(self, tmp_path: Path) -> None:
        """Layer 2 produces a non-empty PDF file on disk."""
        ret = _make_return(
            schedules_c=[_schedule_c("50000")],
            adjustments={"student_loan_interest": "2500"},
        )
        fields = compute_schedule_1_fields(ret)

        out_path = tmp_path / "schedule_1.pdf"
        result_path = render_schedule_1_pdf(fields, out_path)

        assert result_path == out_path
        assert out_path.exists()
        assert out_path.stat().st_size > 1000  # non-trivial PDF

    def test_scaffold_pdf_contains_header(self, tmp_path: Path) -> None:
        """The rendered PDF contains the Schedule 1 header text.

        Skipped when pypdf is broken in the environment (e.g. cffi
        backend unavailable).
        """
        try:
            import pypdf  # noqa: F811
        except BaseException:
            pytest.skip("pypdf not importable in this environment")

        ret = _make_return(schedules_c=[_schedule_c("50000")])
        fields = compute_schedule_1_fields(ret)

        out_path = tmp_path / "schedule_1_header.pdf"
        render_schedule_1_pdf(fields, out_path)

        reader = pypdf.PdfReader(str(out_path))
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""

        assert "Schedule 1" in text
        assert "Sam Scheduler" in text

    def test_scaffold_pdf_contains_numeric_value(self, tmp_path: Path) -> None:
        """The rendered PDF contains a numeric line value.

        Skipped when pypdf is broken in the environment.
        """
        try:
            import pypdf  # noqa: F811
        except BaseException:
            pytest.skip("pypdf not importable in this environment")

        ret = _make_return(schedules_c=[_schedule_c("75000")])
        fields = compute_schedule_1_fields(ret)

        out_path = tmp_path / "schedule_1_numeric.pdf"
        render_schedule_1_pdf(fields, out_path)

        reader = pypdf.PdfReader(str(out_path))
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""

        # Line 3 should show 75000.00
        assert "75000.00" in text

    def test_scaffold_pdf_creates_parent_dirs(self, tmp_path: Path) -> None:
        """render_schedule_1_pdf creates parent directories if needed."""
        fields = Schedule1Fields()
        nested = tmp_path / "sub" / "dir" / "schedule_1.pdf"
        render_schedule_1_pdf(fields, nested)
        assert nested.exists()

    def test_scaffold_pdf_empty_return(self, tmp_path: Path) -> None:
        """An empty-return Schedule 1 still produces a valid PDF."""
        fields = Schedule1Fields()
        out_path = tmp_path / "empty_schedule_1.pdf"
        render_schedule_1_pdf(fields, out_path)
        assert out_path.exists()
        assert out_path.stat().st_size > 500
