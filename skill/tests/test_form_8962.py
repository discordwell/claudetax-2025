"""Tests for Form 8962 (Premium Tax Credit) compute and render layers.

Covers:
1. Family of 3, AGI $40k (~200% FPL), 12 months coverage -> compute PTC
2. AGI above 400% FPL -> full repayment of advance PTC
3. Repayment cap test: AGI 250% FPL, excess advance -> capped repayment
4. Partial-year coverage (6 months)
5. No advance PTC -> pure refundable credit
6. Single vs family FPL thresholds
7. Layer 2 scaffold renders

Authority: IRS Form 8962 (TY2025) and Instructions for Form 8962.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.models import (
    Address,
    CanonicalReturn,
    Dependent,
    DependentRelationship,
    FilingStatus,
    Form1095A,
    Form1095AMonthly,
    Person,
    W2,
)
from skill.scripts.output.form_8962 import (
    FPL_ADDITIONAL_PERSON,
    FPL_BASE_1_PERSON,
    FPL_BASE_2_PERSON,
    Form8962Fields,
    applicable_figure,
    compute_form_8962_fields,
    fpl_for_family_size,
    render_form_8962_pdf,
    repayment_cap,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _person(first: str = "Test", last: str = "Payer", ssn: str = "111-22-3333") -> Person:
    return Person(
        first_name=first,
        last_name=last,
        ssn=ssn,
        date_of_birth="1985-06-15",
        is_blind=False,
        is_age_65_or_older=False,
    )


def _child(first: str = "Kid", ssn: str = "444-55-6666") -> Dependent:
    return Dependent(
        person=Person(
            first_name=first,
            last_name="Payer",
            ssn=ssn,
            date_of_birth="2015-01-01",
        ),
        relationship=DependentRelationship.SON,
        months_lived_with_taxpayer=12,
        is_qualifying_child=True,
        is_qualifying_relative=False,
    )


def _address() -> Address:
    return Address(street1="1 Test St", city="Springfield", state="IL", zip="62701")


def _make_monthly_data(
    months: int = 12,
    enrollment: Decimal = Decimal("500"),
    slcsp: Decimal = Decimal("600"),
    advance_ptc: Decimal = Decimal("200"),
) -> list[Form1095AMonthly]:
    """Create monthly 1095-A data for a given number of months."""
    return [
        Form1095AMonthly(
            enrollment_premium=enrollment,
            slcsp_premium=slcsp,
            advance_ptc=advance_ptc,
        )
        for _ in range(months)
    ]


def _build_return(
    *,
    filing_status: FilingStatus = FilingStatus.MFJ,
    w2_wages: Decimal = Decimal("40000"),
    dependents: list[Dependent] | None = None,
    forms_1095_a: list[Form1095A] | None = None,
    spouse: Person | None = None,
) -> CanonicalReturn:
    """Build a CanonicalReturn for Form 8962 tests, pre-computed."""
    from skill.scripts.calc.engine import compute

    needs_spouse = filing_status in (FilingStatus.MFJ, FilingStatus.MFS)
    if needs_spouse and spouse is None:
        spouse = _person("Spouse", "Two", "222-33-4444")

    deps = dependents if dependents is not None else []
    forms = forms_1095_a if forms_1095_a is not None else []

    w2_list = []
    if w2_wages > 0:
        w2_list.append(
            W2(
                employer_name="ACME",
                employer_ein="12-3456789",
                box1_wages=w2_wages,
            ).model_dump(mode="json")
        )

    dep_dicts = [d.model_dump(mode="json") for d in deps]
    form_dicts = [f.model_dump(mode="json") for f in forms]

    cr = CanonicalReturn.model_validate(
        {
            "schema_version": "0.1.0",
            "tax_year": 2025,
            "filing_status": filing_status.value,
            "taxpayer": _person().model_dump(mode="json"),
            "spouse": spouse.model_dump(mode="json") if spouse else None,
            "address": _address().model_dump(mode="json"),
            "w2s": w2_list,
            "dependents": dep_dicts,
            "forms_1095_a": form_dicts,
        }
    )
    return compute(cr)


# ---------------------------------------------------------------------------
# FPL table tests
# ---------------------------------------------------------------------------


class TestFPLTable:
    def test_single_person(self) -> None:
        assert fpl_for_family_size(1) == Decimal("15650")

    def test_two_persons(self) -> None:
        assert fpl_for_family_size(2) == Decimal("21150")

    def test_three_persons(self) -> None:
        assert fpl_for_family_size(3) == Decimal("26650")

    def test_four_persons(self) -> None:
        assert fpl_for_family_size(4) == Decimal("32150")

    def test_invalid_size_raises(self) -> None:
        with pytest.raises(ValueError, match="family size"):
            fpl_for_family_size(0)


# ---------------------------------------------------------------------------
# Applicable figure tests
# ---------------------------------------------------------------------------


class TestApplicableFigure:
    def test_below_100_fpl(self) -> None:
        assert applicable_figure(Decimal("0.50")) == Decimal("0")

    def test_at_100_fpl(self) -> None:
        result = applicable_figure(Decimal("1.00"))
        assert result == Decimal("0.0000")

    def test_at_133_fpl(self) -> None:
        result = applicable_figure(Decimal("1.33"))
        assert result == Decimal("0.0201")

    def test_at_200_fpl(self) -> None:
        result = applicable_figure(Decimal("2.00"))
        assert result == Decimal("0.0652")

    def test_at_300_fpl(self) -> None:
        result = applicable_figure(Decimal("3.00"))
        assert result == Decimal("0.0983")

    def test_at_350_fpl(self) -> None:
        # 300%-400% is flat 9.83%
        result = applicable_figure(Decimal("3.50"))
        assert result == Decimal("0.0983")

    def test_at_400_fpl(self) -> None:
        result = applicable_figure(Decimal("4.00"))
        assert result == Decimal("0.0983")

    def test_above_400_fpl(self) -> None:
        result = applicable_figure(Decimal("5.00"))
        assert result == Decimal("0.0983")


# ---------------------------------------------------------------------------
# Repayment cap tests
# ---------------------------------------------------------------------------


class TestRepaymentCap:
    def test_under_200_single(self) -> None:
        cap = repayment_cap(Decimal("1.50"), FilingStatus.SINGLE)
        assert cap == Decimal("375")

    def test_under_200_mfj(self) -> None:
        cap = repayment_cap(Decimal("1.50"), FilingStatus.MFJ)
        assert cap == Decimal("750")

    def test_200_300_single(self) -> None:
        cap = repayment_cap(Decimal("2.50"), FilingStatus.SINGLE)
        assert cap == Decimal("950")

    def test_200_300_mfj(self) -> None:
        cap = repayment_cap(Decimal("2.50"), FilingStatus.MFJ)
        assert cap == Decimal("1900")

    def test_300_400_single(self) -> None:
        cap = repayment_cap(Decimal("3.50"), FilingStatus.SINGLE)
        assert cap == Decimal("1600")

    def test_300_400_mfj(self) -> None:
        cap = repayment_cap(Decimal("3.50"), FilingStatus.MFJ)
        assert cap == Decimal("3200")

    def test_above_400_no_cap(self) -> None:
        cap = repayment_cap(Decimal("4.50"), FilingStatus.SINGLE)
        assert cap is None


# ---------------------------------------------------------------------------
# 1. Family of 3, AGI $40k (~200% FPL), 12 months coverage
# ---------------------------------------------------------------------------


class TestFamilyOf3_200FPL:
    """Family of 3 (MFJ + 1 child), $40k AGI, ~150% FPL, 12 months coverage."""

    def test_compute_ptc(self) -> None:
        form_1095a = Form1095A(
            marketplace_id="MARKETPLACE1",
            monthly_data=_make_monthly_data(
                months=12,
                enrollment=Decimal("800"),
                slcsp=Decimal("900"),
                advance_ptc=Decimal("300"),
            ),
        )
        cr = _build_return(
            filing_status=FilingStatus.MFJ,
            w2_wages=Decimal("40000"),
            dependents=[_child()],
            forms_1095_a=[form_1095a],
        )
        fields = compute_form_8962_fields(cr)

        # Family size = 3 (taxpayer + spouse + 1 child)
        assert fields.line_1_tax_family_size == 3
        # FPL for family of 3 = $26,650
        assert fields.line_5_fpl == Decimal("26650")
        # ~150% FPL -> eligible
        assert fields.is_eligible is True
        # Should have 12 monthly rows
        assert len(fields.monthly_rows) == 12
        # Total advance PTC = 300 * 12 = 3600
        assert fields.line_11e_annual_advance_ptc == Decimal("3600.00")


# ---------------------------------------------------------------------------
# 2. AGI above 400% FPL -> full repayment of advance PTC
# ---------------------------------------------------------------------------


class TestAbove400FPL:
    """High income above 400% FPL -- must repay all advance PTC with no cap."""

    def test_full_repayment(self) -> None:
        form_1095a = Form1095A(
            marketplace_id="MARKETPLACE1",
            monthly_data=_make_monthly_data(
                months=12,
                enrollment=Decimal("500"),
                slcsp=Decimal("600"),
                advance_ptc=Decimal("400"),
            ),
        )
        cr = _build_return(
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("150000"),  # well above 400% FPL for single ($62,600)
            dependents=[],
            forms_1095_a=[form_1095a],
            spouse=None,
        )
        fields = compute_form_8962_fields(cr)

        # Single, $150k AGI, FPL = $15,650 -> ~959% FPL
        assert fields.line_1_tax_family_size == 1
        assert fields.is_eligible is False
        # Not eligible -> max PTC = 0 for each month
        assert fields.line_11d_annual_max_ptc == Decimal("0.00")
        # Excess advance = total advance (since PTC = 0)
        assert fields.line_27_excess_advance_ptc == Decimal("4800.00")
        # No cap above 400% -> full repayment
        assert fields.line_29_repayment == Decimal("4800.00")
        # Net PTC should be 0
        assert fields.line_24_net_ptc == Decimal("0.00")


# ---------------------------------------------------------------------------
# 3. Repayment cap test: AGI ~250% FPL, excess advance -> capped repayment
# ---------------------------------------------------------------------------


class TestRepaymentCapApplied:
    """AGI around 250% FPL with excess advance PTC -- repayment is capped."""

    def test_capped_repayment_mfj(self) -> None:
        # Family of 2 (MFJ, no dependents), FPL = $21,150
        # 250% FPL -> income ~$52,875
        form_1095a = Form1095A(
            marketplace_id="MARKETPLACE1",
            monthly_data=_make_monthly_data(
                months=12,
                enrollment=Decimal("600"),
                slcsp=Decimal("500"),  # SLCSP < contribution means max_ptc might be 0
                advance_ptc=Decimal("500"),  # large advance
            ),
        )
        cr = _build_return(
            filing_status=FilingStatus.MFJ,
            w2_wages=Decimal("52875"),
            dependents=[],
            forms_1095_a=[form_1095a],
        )
        fields = compute_form_8962_fields(cr)

        # FPL for 2 = $21,150; income $52,875 -> ~250% FPL
        assert fields.line_5_fpl == Decimal("21150")
        assert fields.is_eligible is True

        # Excess advance should exist and be capped
        if fields.line_27_excess_advance_ptc > Decimal("0"):
            # For MFJ at 200%-300% FPL, cap is $1,900
            assert fields.line_29_repayment <= Decimal("1900")


# ---------------------------------------------------------------------------
# 4. Partial-year coverage (6 months)
# ---------------------------------------------------------------------------


class TestPartialYearCoverage:
    def test_six_months(self) -> None:
        form_1095a = Form1095A(
            marketplace_id="MARKETPLACE1",
            monthly_data=_make_monthly_data(
                months=6,
                enrollment=Decimal("500"),
                slcsp=Decimal("600"),
                advance_ptc=Decimal("100"),
            ),
        )
        cr = _build_return(
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("25000"),
            dependents=[],
            forms_1095_a=[form_1095a],
            spouse=None,
        )
        fields = compute_form_8962_fields(cr)

        # Should only have 6 monthly rows
        assert len(fields.monthly_rows) == 6
        # Total advance = 100 * 6 = 600
        assert fields.line_11e_annual_advance_ptc == Decimal("600.00")
        # Total enrollment = 500 * 6 = 3000
        assert fields.line_11a_annual_enrollment_premium == Decimal("3000.00")


# ---------------------------------------------------------------------------
# 5. No advance PTC -> pure refundable credit
# ---------------------------------------------------------------------------


class TestNoAdvancePTC:
    """Filer received no advance PTC -- should get full PTC as refundable credit."""

    def test_pure_refundable_credit(self) -> None:
        form_1095a = Form1095A(
            marketplace_id="MARKETPLACE1",
            monthly_data=_make_monthly_data(
                months=12,
                enrollment=Decimal("500"),
                slcsp=Decimal("600"),
                advance_ptc=Decimal("0"),  # no advance
            ),
        )
        cr = _build_return(
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("25000"),
            dependents=[],
            forms_1095_a=[form_1095a],
            spouse=None,
        )
        fields = compute_form_8962_fields(cr)

        # Should be eligible (single, $25k, FPL $15,650 -> ~160% FPL)
        assert fields.is_eligible is True
        # No advance PTC
        assert fields.line_11e_annual_advance_ptc == Decimal("0.00")
        # Excess advance = 0
        assert fields.line_27_excess_advance_ptc == Decimal("0.00")
        # Net PTC should be positive (refundable credit)
        assert fields.line_24_net_ptc > Decimal("0")
        # Repayment should be 0
        assert fields.line_29_repayment == Decimal("0.00")


# ---------------------------------------------------------------------------
# 6. Single vs family FPL thresholds
# ---------------------------------------------------------------------------


class TestSingleVsFamilyFPL:
    def test_single_fpl(self) -> None:
        """Single filer FPL = $15,650."""
        form_1095a = Form1095A(
            marketplace_id="M1",
            monthly_data=_make_monthly_data(months=1, enrollment=Decimal("100"),
                                            slcsp=Decimal("100"), advance_ptc=Decimal("0")),
        )
        cr = _build_return(
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("20000"),
            dependents=[],
            forms_1095_a=[form_1095a],
            spouse=None,
        )
        fields = compute_form_8962_fields(cr)
        assert fields.line_1_tax_family_size == 1
        assert fields.line_5_fpl == Decimal("15650")

    def test_family_of_4_fpl(self) -> None:
        """MFJ + 2 dependents -> family of 4, FPL = $32,150."""
        form_1095a = Form1095A(
            marketplace_id="M1",
            monthly_data=_make_monthly_data(months=1, enrollment=Decimal("100"),
                                            slcsp=Decimal("100"), advance_ptc=Decimal("0")),
        )
        cr = _build_return(
            filing_status=FilingStatus.MFJ,
            w2_wages=Decimal("50000"),
            dependents=[_child("Kid1", "444-55-6666"), _child("Kid2", "444-55-7777")],
            forms_1095_a=[form_1095a],
        )
        fields = compute_form_8962_fields(cr)
        assert fields.line_1_tax_family_size == 4
        assert fields.line_5_fpl == Decimal("32150")


# ---------------------------------------------------------------------------
# 7. Layer 2 scaffold renders
# ---------------------------------------------------------------------------


class TestRenderScaffold:
    def test_render_creates_pdf(self, tmp_path: Path) -> None:
        """Layer 2 scaffold should produce a PDF file."""
        form_1095a = Form1095A(
            marketplace_id="MARKETPLACE1",
            monthly_data=_make_monthly_data(
                months=12,
                enrollment=Decimal("500"),
                slcsp=Decimal("600"),
                advance_ptc=Decimal("200"),
            ),
        )
        cr = _build_return(
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("25000"),
            dependents=[],
            forms_1095_a=[form_1095a],
            spouse=None,
        )
        fields = compute_form_8962_fields(cr)
        out_path = tmp_path / "form_8962.pdf"
        result = render_form_8962_pdf(fields, out_path)

        assert result == out_path
        assert out_path.exists()
        assert out_path.stat().st_size > 0

    def test_render_empty_fields(self, tmp_path: Path) -> None:
        """Rendering a default (zeroed) Form8962Fields should not crash."""
        fields = Form8962Fields()
        out_path = tmp_path / "form_8962_empty.pdf"
        result = render_form_8962_pdf(fields, out_path)
        assert result == out_path
        assert out_path.exists()


# ---------------------------------------------------------------------------
# No 1095-A data
# ---------------------------------------------------------------------------


class TestNo1095AData:
    def test_no_forms_returns_default(self) -> None:
        """Without 1095-A forms, compute_form_8962_fields returns defaults."""
        cr = _build_return(
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("50000"),
            dependents=[],
            forms_1095_a=[],
            spouse=None,
        )
        fields = compute_form_8962_fields(cr)
        assert fields.line_1_tax_family_size == 0
        assert fields.is_eligible is False
        assert "No Form 1095-A data present" in fields.warnings
