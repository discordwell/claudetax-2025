"""Tests for Form 2441 (Child and Dependent Care Expenses) compute + render.

Covers:

* :func:`compute_credit_rate` -- phase-down arithmetic
* :func:`compute_form_2441_fields` -- end-to-end cases
* AGI $30k, 1 qualifying child, $4k expenses -> credit = $3k * 27% = $810
* AGI $15k or below, 2 qualifying, $8k expenses -> credit = $6k * 35% = $2,100
* AGI $43k+, 1 qualifying, $3k expenses -> credit = $3k * 20% = $600 (minimum rate)
* MFJ lower earner income cap
* Employer benefits exclusion reduces qualifying expenses
* Layer 2 scaffold renders readable PDF

Authority: IRS Form 2441 (TY2025) and IRS Instructions for Form 2441
(TY2025).
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.models import (
    Address,
    CanonicalReturn,
    ComputedTotals,
    Credits,
    DependentCareExpenses,
    FilingStatus,
    Person,
    W2,
)
from skill.scripts.output.form_2441 import (
    BASE_CREDIT_RATE,
    MIN_CREDIT_RATE,
    MAX_EXPENSES_ONE_PERSON,
    MAX_EXPENSES_TWO_OR_MORE,
    Form2441Fields,
    compute_credit_rate,
    compute_form_2441_fields,
    render_form_2441_pdf,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _person(first: str = "Test", last: str = "Payer") -> Person:
    return Person(
        first_name=first,
        last_name=last,
        ssn="111-22-3333",
        date_of_birth="1985-06-15",
        is_blind=False,
        is_age_65_or_older=False,
    )


def _address() -> Address:
    return Address(street1="1 Test St", city="Springfield", state="IL", zip="62701")


def _minimal_return(
    *,
    filing_status: FilingStatus = FilingStatus.SINGLE,
    w2_wages_taxpayer: Decimal = Decimal("50000"),
    w2_wages_spouse: Decimal = Decimal("0"),
    agi: Decimal | None = None,
    dependent_care: DependentCareExpenses | None = None,
) -> CanonicalReturn:
    """Build a minimal CanonicalReturn with dependent care for tests."""
    needs_spouse = filing_status in (FilingStatus.MFJ, FilingStatus.MFS)
    spouse = _person("Spouse", "Two") if needs_spouse else None

    w2_list = []
    if w2_wages_taxpayer > 0:
        w2_list.append(
            W2(
                employer_name="ACME",
                employer_ein="12-3456789",
                box1_wages=w2_wages_taxpayer,
                employee_is_taxpayer=True,
            ).model_dump(mode="json")
        )
    if w2_wages_spouse > 0 and needs_spouse:
        w2_list.append(
            W2(
                employer_name="ACME SPOUSE",
                employer_ein="12-3456790",
                box1_wages=w2_wages_spouse,
                employee_is_taxpayer=False,
            ).model_dump(mode="json")
        )

    computed_agi = agi if agi is not None else w2_wages_taxpayer + w2_wages_spouse

    return CanonicalReturn.model_validate(
        {
            "schema_version": "0.1.0",
            "tax_year": 2025,
            "filing_status": filing_status.value,
            "taxpayer": _person().model_dump(mode="json"),
            "spouse": spouse.model_dump(mode="json") if spouse else None,
            "address": _address().model_dump(mode="json"),
            "w2s": w2_list,
            "dependent_care": dependent_care.model_dump(mode="json")
            if dependent_care is not None
            else None,
            "computed": {
                "adjusted_gross_income": str(computed_agi),
            },
        }
    )


# ---------------------------------------------------------------------------
# Credit rate tests
# ---------------------------------------------------------------------------


class TestCreditRate:
    """Tests for compute_credit_rate."""

    def test_at_or_below_15k_returns_35pct(self) -> None:
        assert compute_credit_rate(Decimal("15000")) == 35
        assert compute_credit_rate(Decimal("10000")) == 35
        assert compute_credit_rate(Decimal("0")) == 35

    def test_agi_17000_returns_34pct(self) -> None:
        """$17k is $2k over $15k -> 1% reduction -> 34%."""
        assert compute_credit_rate(Decimal("17000")) == 34

    def test_agi_17001_returns_33pct(self) -> None:
        """$17,001 is in the ($17k, $19k] bracket -> ceil gives 2 -> 33%."""
        assert compute_credit_rate(Decimal("17001")) == 33

    def test_agi_30000_returns_27pct(self) -> None:
        """$30k is $15k over $15k -> ceil(15000/2000) = 8 -> 35-8 = 27%."""
        assert compute_credit_rate(Decimal("30000")) == 27

    def test_agi_43000_returns_21pct(self) -> None:
        """$43k is $28k over $15k -> ceil(28000/2000) = 14 -> 35-14 = 21%."""
        assert compute_credit_rate(Decimal("43000")) == 21

    def test_agi_45000_returns_20pct(self) -> None:
        """$45k is $30k over -> ceil(30000/2000) = 15 -> 35-15 = 20%."""
        assert compute_credit_rate(Decimal("45000")) == 20

    def test_agi_100000_returns_minimum_20pct(self) -> None:
        """Any AGI high enough floors at 20%."""
        assert compute_credit_rate(Decimal("100000")) == 20

    def test_agi_43001_returns_20pct(self) -> None:
        """$43,001 is $28,001 over -> ceil(28001/2000)=15 -> 35-15=20%."""
        assert compute_credit_rate(Decimal("43001")) == 20


# ---------------------------------------------------------------------------
# Compute tests -- end-to-end Form 2441 field computation
# ---------------------------------------------------------------------------


class TestComputeForm2441:
    """Tests for compute_form_2441_fields."""

    def test_agi_30k_one_child_4k_expenses(self) -> None:
        """AGI $30k, 1 qualifying child, $4k expenses -> credit = $3k * 27% = $810.

        The $4k expenses are capped at $3k for one qualifying person.
        At AGI $30k the credit rate is 27%.
        """
        dc = DependentCareExpenses(
            qualifying_persons=1,
            total_expenses_paid=Decimal("4000"),
        )
        r = _minimal_return(
            w2_wages_taxpayer=Decimal("30000"),
            agi=Decimal("30000"),
            dependent_care=dc,
        )
        fields = compute_form_2441_fields(r)

        assert fields.line_3_num_qualifying_persons == 1
        assert fields.line_4_qualified_expenses == Decimal("3000")
        assert fields.line_9_credit_rate_pct == 27
        assert fields.line_10_credit == Decimal("810.00")

    def test_agi_15k_two_qualifying_8k_expenses(self) -> None:
        """AGI $15k or below, 2 qualifying, $8k expenses -> credit = $6k * 35% = $2,100.

        The $8k expenses are capped at $6k for two or more qualifying persons.
        At AGI $15k the full 35% rate applies.
        """
        dc = DependentCareExpenses(
            qualifying_persons=2,
            total_expenses_paid=Decimal("8000"),
        )
        r = _minimal_return(
            w2_wages_taxpayer=Decimal("15000"),
            agi=Decimal("15000"),
            dependent_care=dc,
        )
        fields = compute_form_2441_fields(r)

        assert fields.line_3_num_qualifying_persons == 2
        assert fields.line_4_qualified_expenses == Decimal("6000")
        assert fields.line_9_credit_rate_pct == 35
        assert fields.line_10_credit == Decimal("2100.00")

    def test_agi_43k_plus_minimum_rate(self) -> None:
        """AGI $43k+, 1 qualifying, $3k expenses -> credit = $3k * 20% = $600.

        At AGI $45k the minimum 20% rate applies.
        """
        dc = DependentCareExpenses(
            qualifying_persons=1,
            total_expenses_paid=Decimal("3000"),
        )
        r = _minimal_return(
            w2_wages_taxpayer=Decimal("45000"),
            agi=Decimal("45000"),
            dependent_care=dc,
        )
        fields = compute_form_2441_fields(r)

        assert fields.line_3_num_qualifying_persons == 1
        assert fields.line_4_qualified_expenses == Decimal("3000")
        assert fields.line_9_credit_rate_pct == 20
        assert fields.line_10_credit == Decimal("600.00")

    def test_mfj_lower_earner_income_cap(self) -> None:
        """MFJ: lower earner's income caps qualifying expenses.

        Taxpayer earns $50k, spouse earns $2k. 1 qualifying person,
        $4k expenses. Expenses capped at $3k (one person), but line 7
        is min($3k, $50k, $2k) = $2k. Credit at 20% = $400.
        """
        dc = DependentCareExpenses(
            qualifying_persons=1,
            total_expenses_paid=Decimal("4000"),
        )
        r = _minimal_return(
            filing_status=FilingStatus.MFJ,
            w2_wages_taxpayer=Decimal("50000"),
            w2_wages_spouse=Decimal("2000"),
            agi=Decimal("52000"),
            dependent_care=dc,
        )
        fields = compute_form_2441_fields(r)

        assert fields.line_5_earned_income_taxpayer == Decimal("50000")
        assert fields.line_6_earned_income_spouse == Decimal("2000")
        assert fields.line_7_smallest_of_4_5_6 == Decimal("2000")
        assert fields.line_10_credit == Decimal("400.00")

    def test_employer_benefits_reduce_expenses(self) -> None:
        """Employer benefits exclusion reduces qualifying expenses.

        $4k total expenses - $1k employer benefits = $3k net.
        1 qualifying person -> cap at $3k (already $3k).
        AGI $30k -> 27% rate -> credit = $3k * 27% = $810.
        """
        dc = DependentCareExpenses(
            qualifying_persons=1,
            total_expenses_paid=Decimal("4000"),
            employer_benefits_excluded=Decimal("1000"),
        )
        r = _minimal_return(
            w2_wages_taxpayer=Decimal("30000"),
            agi=Decimal("30000"),
            dependent_care=dc,
        )
        fields = compute_form_2441_fields(r)

        assert fields.line_12_employer_benefits == Decimal("1000")
        assert fields.line_4_qualified_expenses == Decimal("3000")
        assert fields.line_10_credit == Decimal("810.00")

    def test_employer_benefits_exceed_expenses(self) -> None:
        """Employer benefits that exceed total expenses result in zero credit."""
        dc = DependentCareExpenses(
            qualifying_persons=1,
            total_expenses_paid=Decimal("2000"),
            employer_benefits_excluded=Decimal("3000"),
        )
        r = _minimal_return(
            w2_wages_taxpayer=Decimal("30000"),
            agi=Decimal("30000"),
            dependent_care=dc,
        )
        fields = compute_form_2441_fields(r)

        assert fields.line_4_qualified_expenses == Decimal("0")
        assert fields.line_10_credit == Decimal("0.00")

    def test_no_dependent_care_returns_empty_fields(self) -> None:
        """When dependent_care is None, all fields are zeroed."""
        r = _minimal_return(dependent_care=None)
        fields = compute_form_2441_fields(r)
        assert fields.line_10_credit == Decimal("0")
        assert fields.line_3_num_qualifying_persons == 0

    def test_zero_qualifying_persons(self) -> None:
        """Zero qualifying persons should produce zero credit."""
        dc = DependentCareExpenses(
            qualifying_persons=0,
            total_expenses_paid=Decimal("5000"),
        )
        r = _minimal_return(
            w2_wages_taxpayer=Decimal("30000"),
            agi=Decimal("30000"),
            dependent_care=dc,
        )
        fields = compute_form_2441_fields(r)
        assert fields.line_3_num_qualifying_persons == 0

    def test_expenses_below_cap(self) -> None:
        """Expenses below the cap are not inflated."""
        dc = DependentCareExpenses(
            qualifying_persons=1,
            total_expenses_paid=Decimal("2000"),
        )
        r = _minimal_return(
            w2_wages_taxpayer=Decimal("30000"),
            agi=Decimal("30000"),
            dependent_care=dc,
        )
        fields = compute_form_2441_fields(r)
        assert fields.line_4_qualified_expenses == Decimal("2000")
        assert fields.line_10_credit == Decimal("540.00")  # 2000 * 27%


# ---------------------------------------------------------------------------
# Layer 2 -- render scaffold tests
# ---------------------------------------------------------------------------


class TestRenderForm2441:
    """Tests for the reportlab scaffold renderer."""

    def test_render_produces_nonempty_pdf(self, tmp_path: Path) -> None:
        """Render produces a non-empty PDF file."""
        dc = DependentCareExpenses(
            qualifying_persons=1,
            total_expenses_paid=Decimal("4000"),
            care_providers=[
                {
                    "name": "ABC Daycare",
                    "address": "123 Main St",
                    "tin": "12-3456789",
                    "amount_paid": "4000",
                },
            ],
        )
        r = _minimal_return(
            w2_wages_taxpayer=Decimal("30000"),
            agi=Decimal("30000"),
            dependent_care=dc,
        )
        fields = compute_form_2441_fields(r)

        out_path = tmp_path / "form_2441.pdf"
        result = render_form_2441_pdf(fields, out_path)

        assert result == out_path
        assert out_path.exists()
        assert out_path.stat().st_size > 1000  # non-trivial PDF

    def test_render_contains_valid_pdf_header(self, tmp_path: Path) -> None:
        """The rendered PDF should start with the %PDF magic bytes."""
        fields = Form2441Fields(
            taxpayer_name="John Doe",
            line_10_credit=Decimal("810.00"),
        )
        out_path = tmp_path / "form_2441_title.pdf"
        render_form_2441_pdf(fields, out_path)
        assert out_path.exists()
        content = out_path.read_bytes()
        assert content[:5] == b"%PDF-"

    def test_render_empty_providers(self, tmp_path: Path) -> None:
        """Render works with no care providers listed."""
        fields = Form2441Fields()
        out_path = tmp_path / "form_2441_empty.pdf"
        result = render_form_2441_pdf(fields, out_path)
        assert result == out_path
        assert out_path.exists()
        assert out_path.stat().st_size > 500

    def test_render_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Render creates parent directories if needed."""
        fields = Form2441Fields(line_10_credit=Decimal("100"))
        out_path = tmp_path / "subdir" / "nested" / "form_2441.pdf"
        result = render_form_2441_pdf(fields, out_path)
        assert result == out_path
        assert out_path.exists()
