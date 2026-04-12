"""Tests for Form 8863 (Education Credits -- AOTC + LLC) compute and render layers.

Covers:
1. AOTC: 1 student, $4k expenses, AGI $50k -> $2500 total ($1500 NR, $1000 R)
2. AOTC with MAGI phase-out at $85k Single -> 50% reduction
3. LLC only (grad student): $8k expenses, AGI $50k -> $1600 nonrefundable
4. LLC with MAGI phase-out
5. Mixed: 1 AOTC + 1 LLC student
6. AOTC disqualified (felony/completed 4 years) -> falls to LLC
7. MFJ thresholds ($160k-$180k)
8. Layer 2 scaffold renders

Authority: IRS Form 8863 (TY2025) and Instructions for Form 8863.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.models import (
    Address,
    CanonicalReturn,
    EducationCredits,
    EducationStudent,
    FilingStatus,
    Person,
)
from skill.scripts.output.form_8863 import (
    Form8863Fields,
    compute_form_8863_fields,
    render_form_8863_pdf,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _person(
    first: str = "Test", last: str = "Payer", ssn: str = "111-22-3333"
) -> Person:
    return Person(
        first_name=first,
        last_name=last,
        ssn=ssn,
        date_of_birth="1985-06-15",
        is_blind=False,
        is_age_65_or_older=False,
    )


def _address() -> Address:
    return Address(street1="1 Test St", city="Springfield", state="IL", zip="62701")


def _make_canonical(
    *,
    filing_status: FilingStatus = FilingStatus.SINGLE,
    agi: Decimal = Decimal("50000"),
    students: list[EducationStudent] | None = None,
    spouse: Person | None = None,
) -> CanonicalReturn:
    """Build a minimal CanonicalReturn with education data and a preset AGI."""
    education = None
    if students is not None:
        education = EducationCredits(students=students)

    cr = CanonicalReturn(
        tax_year=2025,
        filing_status=filing_status,
        taxpayer=_person(),
        spouse=spouse,
        address=_address(),
        education=education,
    )
    # Patch computed AGI directly (normally set by engine.compute())
    cr.computed.adjusted_gross_income = agi
    return cr


def _student(
    name: str = "Student One",
    ssn: str = "222-33-4444",
    expenses: Decimal = Decimal("4000"),
    is_aotc_eligible: bool = True,
    completed_4_years: bool = False,
    half_time_student: bool = True,
    felony_drug_conviction: bool = False,
    institution_name: str = "State University",
) -> EducationStudent:
    return EducationStudent(
        name=name,
        ssn=ssn,
        institution_name=institution_name,
        qualified_expenses=expenses,
        is_aotc_eligible=is_aotc_eligible,
        completed_4_years=completed_4_years,
        half_time_student=half_time_student,
        felony_drug_conviction=felony_drug_conviction,
    )


# ---------------------------------------------------------------------------
# Test 1: AOTC basic -- 1 student, $4k expenses, AGI $50k
# ---------------------------------------------------------------------------


class TestAOTCBasic:
    """AOTC: 1 student, $4k expenses, AGI $50k (no phase-out).

    Raw AOTC = 100% * $2000 + 25% * $2000 = $2500
    Phase-out factor = 1.0 (AGI well below $80k)
    Nonrefundable = 60% * $2500 = $1500
    Refundable    = 40% * $2500 = $1000
    """

    def test_totals(self) -> None:
        cr = _make_canonical(students=[_student()])
        fields = compute_form_8863_fields(cr)

        assert fields.total_aotc_nonrefundable == Decimal("1500.00")
        assert fields.total_aotc_refundable == Decimal("1000.00")
        assert fields.total_nonrefundable == Decimal("1500.00")
        assert fields.total_refundable == Decimal("1000.00")

    def test_phase_out_factor_is_one(self) -> None:
        cr = _make_canonical(students=[_student()])
        fields = compute_form_8863_fields(cr)
        assert fields.aotc_phase_out_factor == Decimal("1")

    def test_student_detail(self) -> None:
        cr = _make_canonical(students=[_student()])
        fields = compute_form_8863_fields(cr)
        assert len(fields.students) == 1
        s = fields.students[0]
        assert s.credit_type == "AOTC"
        assert s.raw_credit == Decimal("2500.00")
        assert s.phased_credit == Decimal("2500.00")


# ---------------------------------------------------------------------------
# Test 2: AOTC with MAGI phase-out at $85k Single -> 50% reduction
# ---------------------------------------------------------------------------


class TestAOTCPhaseOut:
    """AOTC with MAGI $85k Single -> phase-out factor = ($90k - $85k) / ($90k - $80k) = 0.50.

    Raw AOTC = $2500
    Phased = $2500 * 0.50 = $1250
    Nonrefundable = 60% * $1250 = $750
    Refundable    = 40% * $1250 = $500
    """

    def test_phase_out_factor(self) -> None:
        cr = _make_canonical(agi=Decimal("85000"), students=[_student()])
        fields = compute_form_8863_fields(cr)
        assert fields.aotc_phase_out_factor == Decimal("0.50")

    def test_totals(self) -> None:
        cr = _make_canonical(agi=Decimal("85000"), students=[_student()])
        fields = compute_form_8863_fields(cr)
        assert fields.total_aotc_nonrefundable == Decimal("750.00")
        assert fields.total_aotc_refundable == Decimal("500.00")
        assert fields.total_nonrefundable == Decimal("750.00")
        assert fields.total_refundable == Decimal("500.00")


# ---------------------------------------------------------------------------
# Test 3: LLC only (grad student): $8k expenses, AGI $50k -> $1600 NR
# ---------------------------------------------------------------------------


class TestLLCOnly:
    """LLC only: 1 grad student (not AOTC eligible), $8k expenses, AGI $50k.

    LLC expenses = min($8000, $10000) = $8000
    LLC raw = 20% * $8000 = $1600
    Phase-out factor = 1.0 (AGI well below $80k)
    LLC phased = $1600
    All nonrefundable, zero refundable.
    """

    def test_totals(self) -> None:
        cr = _make_canonical(
            students=[_student(is_aotc_eligible=False, expenses=Decimal("8000"))]
        )
        fields = compute_form_8863_fields(cr)

        assert fields.llc_qualified_expenses == Decimal("8000")
        assert fields.llc_raw_credit == Decimal("1600.00")
        assert fields.llc_phased_credit == Decimal("1600.00")
        assert fields.total_nonrefundable == Decimal("1600.00")
        assert fields.total_refundable == Decimal("0")

    def test_llc_factor_is_one(self) -> None:
        cr = _make_canonical(
            students=[_student(is_aotc_eligible=False, expenses=Decimal("8000"))]
        )
        fields = compute_form_8863_fields(cr)
        assert fields.llc_phase_out_factor == Decimal("1")


# ---------------------------------------------------------------------------
# Test 4: LLC with MAGI phase-out
# ---------------------------------------------------------------------------


class TestLLCPhaseOut:
    """LLC with MAGI $85k Single -> factor = 0.50.

    LLC expenses = $8000
    LLC raw = $1600
    LLC phased = $1600 * 0.50 = $800
    """

    def test_totals(self) -> None:
        cr = _make_canonical(
            agi=Decimal("85000"),
            students=[_student(is_aotc_eligible=False, expenses=Decimal("8000"))],
        )
        fields = compute_form_8863_fields(cr)
        assert fields.llc_phased_credit == Decimal("800.00")
        assert fields.total_nonrefundable == Decimal("800.00")
        assert fields.total_refundable == Decimal("0")


# ---------------------------------------------------------------------------
# Test 5: Mixed -- 1 AOTC + 1 LLC student
# ---------------------------------------------------------------------------


class TestMixed:
    """Mixed: 1 AOTC student ($4k) + 1 LLC student ($6k), AGI $50k.

    AOTC student: raw $2500, phased $2500, NR $1500, R $1000
    LLC student:  expenses $6000, raw 20% * $6000 = $1200, phased $1200
    Total NR = $1500 + $1200 = $2700
    Total R  = $1000
    """

    def test_totals(self) -> None:
        students = [
            _student(name="Undergrad", ssn="222-33-4444"),
            _student(
                name="Grad",
                ssn="333-44-5555",
                is_aotc_eligible=False,
                expenses=Decimal("6000"),
            ),
        ]
        cr = _make_canonical(students=students)
        fields = compute_form_8863_fields(cr)

        assert fields.total_aotc_nonrefundable == Decimal("1500.00")
        assert fields.total_aotc_refundable == Decimal("1000.00")
        assert fields.llc_phased_credit == Decimal("1200.00")
        assert fields.total_nonrefundable == Decimal("2700.00")
        assert fields.total_refundable == Decimal("1000.00")

    def test_student_count(self) -> None:
        students = [
            _student(name="Undergrad", ssn="222-33-4444"),
            _student(
                name="Grad",
                ssn="333-44-5555",
                is_aotc_eligible=False,
                expenses=Decimal("6000"),
            ),
        ]
        cr = _make_canonical(students=students)
        fields = compute_form_8863_fields(cr)
        assert len(fields.students) == 2
        types = [s.credit_type for s in fields.students]
        assert "AOTC" in types
        assert "LLC" in types


# ---------------------------------------------------------------------------
# Test 6: AOTC disqualified -> falls to LLC
# ---------------------------------------------------------------------------


class TestAOTCDisqualified:
    """Student who completed 4 years or has felony drug conviction
    is not AOTC-eligible and falls to LLC.
    """

    def test_completed_4_years_falls_to_llc(self) -> None:
        cr = _make_canonical(
            students=[_student(completed_4_years=True, expenses=Decimal("4000"))]
        )
        fields = compute_form_8863_fields(cr)
        assert fields.total_aotc_nonrefundable == Decimal("0")
        assert fields.total_aotc_refundable == Decimal("0")
        # Falls to LLC: 20% * $4000 = $800
        assert fields.llc_phased_credit == Decimal("800.00")
        assert fields.total_nonrefundable == Decimal("800.00")
        assert fields.students[0].credit_type == "LLC"

    def test_felony_falls_to_llc(self) -> None:
        cr = _make_canonical(
            students=[
                _student(felony_drug_conviction=True, expenses=Decimal("4000"))
            ]
        )
        fields = compute_form_8863_fields(cr)
        assert fields.total_aotc_nonrefundable == Decimal("0")
        assert fields.total_aotc_refundable == Decimal("0")
        assert fields.llc_phased_credit == Decimal("800.00")
        assert fields.students[0].credit_type == "LLC"

    def test_not_half_time_falls_to_llc(self) -> None:
        cr = _make_canonical(
            students=[_student(half_time_student=False, expenses=Decimal("4000"))]
        )
        fields = compute_form_8863_fields(cr)
        assert fields.total_aotc_nonrefundable == Decimal("0")
        assert fields.students[0].credit_type == "LLC"


# ---------------------------------------------------------------------------
# Test 7: MFJ thresholds ($160k-$180k)
# ---------------------------------------------------------------------------


class TestMFJThresholds:
    """MFJ phase-out: $160k-$180k.

    At $170k MAGI -> factor = ($180k - $170k) / ($180k - $160k) = 0.50
    """

    def test_mfj_phase_out(self) -> None:
        spouse = _person(first="Spouse", ssn="999-88-7777")
        cr = _make_canonical(
            filing_status=FilingStatus.MFJ,
            agi=Decimal("170000"),
            students=[_student()],
            spouse=spouse,
        )
        fields = compute_form_8863_fields(cr)
        assert fields.aotc_phase_out_factor == Decimal("0.50")
        # Raw AOTC = $2500, phased = $1250
        assert fields.total_aotc_nonrefundable == Decimal("750.00")
        assert fields.total_aotc_refundable == Decimal("500.00")

    def test_mfj_below_threshold(self) -> None:
        spouse = _person(first="Spouse", ssn="999-88-7777")
        cr = _make_canonical(
            filing_status=FilingStatus.MFJ,
            agi=Decimal("150000"),
            students=[_student()],
            spouse=spouse,
        )
        fields = compute_form_8863_fields(cr)
        assert fields.aotc_phase_out_factor == Decimal("1")
        assert fields.total_aotc_nonrefundable == Decimal("1500.00")
        assert fields.total_aotc_refundable == Decimal("1000.00")

    def test_mfj_above_threshold(self) -> None:
        spouse = _person(first="Spouse", ssn="999-88-7777")
        cr = _make_canonical(
            filing_status=FilingStatus.MFJ,
            agi=Decimal("180000"),
            students=[_student()],
            spouse=spouse,
        )
        fields = compute_form_8863_fields(cr)
        assert fields.aotc_phase_out_factor == Decimal("0")
        assert fields.total_aotc_nonrefundable == Decimal("0")
        assert fields.total_aotc_refundable == Decimal("0")


# ---------------------------------------------------------------------------
# Test 8: Layer 2 scaffold renders
# ---------------------------------------------------------------------------


class TestRender:
    """Layer 2 scaffold renders a PDF that pypdf can open."""

    def test_render_produces_file(self, tmp_path: Path) -> None:
        cr = _make_canonical(students=[_student()])
        fields = compute_form_8863_fields(cr)
        out = tmp_path / "form_8863.pdf"
        result = render_form_8863_pdf(fields, out)
        assert result == out
        assert out.exists()
        assert out.stat().st_size > 0

    def test_render_opens_with_pypdf(self, tmp_path: Path) -> None:
        try:
            from pypdf import PdfReader
        except BaseException:
            pytest.skip("pypdf not importable")

        cr = _make_canonical(students=[_student()])
        fields = compute_form_8863_fields(cr)
        out = tmp_path / "form_8863.pdf"
        render_form_8863_pdf(fields, out)

        reader = PdfReader(str(out))
        assert len(reader.pages) >= 1
        text = reader.pages[0].extract_text()
        assert "8863" in text

    def test_render_empty_fields(self, tmp_path: Path) -> None:
        """Rendering with default (empty) fields should not crash."""
        fields = Form8863Fields()
        out = tmp_path / "form_8863_empty.pdf"
        result = render_form_8863_pdf(fields, out)
        assert result == out
        assert out.exists()
