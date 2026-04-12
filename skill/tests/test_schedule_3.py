"""Tests for skill.scripts.output.schedule_3 — Schedule 3 (Additional Credits and Payments).

Two layers under test:

* Layer 1 — ``compute_schedule_3_fields``: assert that values on the
  returned dataclass match the expected Schedule 3 line numbers for a
  variety of credit/payment combinations. These tests verify pure
  routing from the canonical Credits and Payments blocks onto Schedule 3
  line names and the summation arithmetic for lines 7/8 and 15.

* Layer 2 — ``render_schedule_3_pdf``: write a scaffold PDF using
  reportlab and assert the output exists and is non-empty.

Tests:
  1. Foreign tax credit only: $500 -> line 1 = $500, line 7 = $500.
  2. Multiple nonrefundable credits: foreign + dependent care + education.
  3. Refundable credits: premium tax credit + AOTC refundable.
  4. Extension payment flows to line 10.
  5. Full Schedule 3: mix of nonrefundable + refundable -> verify both totals.
  6. All zeros -> schedule not required.
  7. Layer 2 scaffold renders a non-empty PDF.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.models import (
    Address,
    CanonicalReturn,
    Credits,
    FilingStatus,
    Payments,
    Person,
)
from skill.scripts.output.schedule_3 import (
    Schedule3Fields,
    compute_schedule_3_fields,
    render_schedule_3_pdf,
    schedule_3_required,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ZERO = Decimal("0")


def _person(first: str = "Test", last: str = "Payer") -> Person:
    return Person(
        first_name=first,
        last_name=last,
        ssn="111-22-3333",
        date_of_birth="1990-01-01",
        is_blind=False,
        is_age_65_or_older=False,
    )


def _address() -> Address:
    return Address(street1="1 Test", city="Springfield", state="IL", zip="62701")


def _minimal_return(
    *,
    filing_status: FilingStatus = FilingStatus.SINGLE,
    credits: Credits | None = None,
    payments: Payments | None = None,
) -> CanonicalReturn:
    """Build a minimal CanonicalReturn with optional credits/payments."""
    data: dict = {
        "schema_version": "0.1.0",
        "tax_year": 2025,
        "filing_status": filing_status.value,
        "taxpayer": _person().model_dump(mode="json"),
        "address": _address().model_dump(mode="json"),
    }
    if credits is not None:
        data["credits"] = credits.model_dump(mode="json")
    if payments is not None:
        data["payments"] = payments.model_dump(mode="json")
    return CanonicalReturn.model_validate(data)


# ---------------------------------------------------------------------------
# Test 1: Foreign tax credit only
# ---------------------------------------------------------------------------


def test_foreign_tax_credit_only() -> None:
    """$500 foreign tax credit -> line 1 = $500, line 7 = $500, line 8 = $500."""
    cr = Credits(foreign_tax_credit=Decimal("500"))
    r = _minimal_return(credits=cr)
    fields = compute_schedule_3_fields(r)

    assert fields.line_1_foreign_tax_credit == Decimal("500")
    assert fields.line_2_dependent_care_credit == _ZERO
    assert fields.line_3_education_credits == _ZERO
    assert fields.line_4_retirement_savings_credit == _ZERO
    assert fields.line_5a_residential_clean_energy_credit == _ZERO
    assert fields.line_7_total_other_credits == Decimal("500")
    assert fields.line_8_total_nonrefundable_credits == Decimal("500")
    # No refundable items -> line 15 = 0
    assert fields.line_15_total_other_payments_and_refundable == _ZERO


# ---------------------------------------------------------------------------
# Test 2: Multiple nonrefundable credits
# ---------------------------------------------------------------------------


def test_multiple_nonrefundable_credits() -> None:
    """Foreign + dependent care + education nonrefundable -> correct sum."""
    cr = Credits(
        foreign_tax_credit=Decimal("1200"),
        dependent_care_credit=Decimal("800"),
        education_credits_nonrefundable=Decimal("2000"),
    )
    r = _minimal_return(credits=cr)
    fields = compute_schedule_3_fields(r)

    assert fields.line_1_foreign_tax_credit == Decimal("1200")
    assert fields.line_2_dependent_care_credit == Decimal("800")
    assert fields.line_3_education_credits == Decimal("2000")
    expected_total = Decimal("1200") + Decimal("800") + Decimal("2000")
    assert fields.line_7_total_other_credits == expected_total
    assert fields.line_8_total_nonrefundable_credits == expected_total


# ---------------------------------------------------------------------------
# Test 3: Refundable credits
# ---------------------------------------------------------------------------


def test_refundable_credits() -> None:
    """Premium tax credit + AOTC refundable -> line 15 sums correctly."""
    cr = Credits(
        premium_tax_credit_net=Decimal("3500"),
        education_credits_refundable=Decimal("1000"),
    )
    r = _minimal_return(credits=cr)
    fields = compute_schedule_3_fields(r)

    assert fields.line_9_net_premium_tax_credit == Decimal("3500")
    assert fields.line_14_aotc_refundable == Decimal("1000")
    # Nonrefundable side stays zero
    assert fields.line_8_total_nonrefundable_credits == _ZERO
    # Refundable total: 3500 + 0 (line 10) + 0 (line 11) + 0 (line 12)
    #   + 0 (line 13) + 1000 (line 14) = 4500
    assert fields.line_15_total_other_payments_and_refundable == Decimal("4500")


# ---------------------------------------------------------------------------
# Test 4: Extension payment flows to line 10
# ---------------------------------------------------------------------------


def test_extension_payment_flows_to_line_10() -> None:
    """amount_paid_with_4868_extension -> line 10."""
    pay = Payments(amount_paid_with_4868_extension=Decimal("2000"))
    r = _minimal_return(payments=pay)
    fields = compute_schedule_3_fields(r)

    assert fields.line_10_amount_paid_with_extension == Decimal("2000")
    assert fields.line_15_total_other_payments_and_refundable == Decimal("2000")


# ---------------------------------------------------------------------------
# Test 5: Full Schedule 3 — mix of nonrefundable + refundable
# ---------------------------------------------------------------------------


def test_full_schedule_3_mixed() -> None:
    """All credit types + payments -> verify both part totals."""
    cr = Credits(
        foreign_tax_credit=Decimal("500"),
        dependent_care_credit=Decimal("1200"),
        education_credits_nonrefundable=Decimal("2000"),
        retirement_savings_credit=Decimal("1000"),
        residential_energy_credits=Decimal("3000"),
        premium_tax_credit_net=Decimal("750"),
        education_credits_refundable=Decimal("400"),
        other_credits={
            "elderly_or_disabled": Decimal("300"),
            "plug_in_vehicle": Decimal("7500"),
            "misc_credit": Decimal("200"),
        },
    )
    pay = Payments(
        amount_paid_with_4868_extension=Decimal("1000"),
        excess_social_security_tax_withheld=Decimal("250"),
    )
    r = _minimal_return(credits=cr, payments=pay)
    fields = compute_schedule_3_fields(r)

    # Part I verification
    assert fields.line_1_foreign_tax_credit == Decimal("500")
    assert fields.line_2_dependent_care_credit == Decimal("1200")
    assert fields.line_3_education_credits == Decimal("2000")
    assert fields.line_4_retirement_savings_credit == Decimal("1000")
    assert fields.line_5a_residential_clean_energy_credit == Decimal("3000")
    assert fields.line_5b_energy_efficient_home_improvement == _ZERO

    # Line 6 sub-lines
    assert fields.line_6d_elderly_or_disabled_credit == Decimal("300")
    assert fields.line_6g_plug_in_vehicle_credit == Decimal("7500")
    assert fields.line_6i_alternative_motor_vehicle_credit == _ZERO
    # "misc_credit" is not a recognized sub-line -> goes to 6z
    assert fields.line_6z_other_nonrefundable == Decimal("200")
    assert fields.line_6_total_other_nonrefundable == Decimal("8000")

    # Line 7 = sum of lines 1-6: 500+1200+2000+1000+3000+0+8000
    expected_line_7 = Decimal("15700")
    assert fields.line_7_total_other_credits == expected_line_7
    assert fields.line_8_total_nonrefundable_credits == expected_line_7

    # Part II verification
    assert fields.line_9_net_premium_tax_credit == Decimal("750")
    assert fields.line_10_amount_paid_with_extension == Decimal("1000")
    assert fields.line_11_excess_social_security_withheld == Decimal("250")
    assert fields.line_12_fuel_tax_credits == _ZERO
    assert fields.line_13_total_other_payments == _ZERO
    assert fields.line_14_aotc_refundable == Decimal("400")
    # Line 15 = 750 + 1000 + 250 + 0 + 0 + 400 = 2400
    assert fields.line_15_total_other_payments_and_refundable == Decimal("2400")


# ---------------------------------------------------------------------------
# Test 6: All zeros -> no schedule needed
# ---------------------------------------------------------------------------


def test_all_zeros_not_required() -> None:
    """With no credits or payments, Schedule 3 is not required."""
    r = _minimal_return()
    assert schedule_3_required(r) is False

    fields = compute_schedule_3_fields(r)
    assert fields.is_required is False
    assert fields.line_7_total_other_credits == _ZERO
    assert fields.line_8_total_nonrefundable_credits == _ZERO
    assert fields.line_15_total_other_payments_and_refundable == _ZERO


def test_schedule_3_required_with_foreign_tax_credit() -> None:
    """Any nonzero credit triggers Schedule 3."""
    cr = Credits(foreign_tax_credit=Decimal("100"))
    r = _minimal_return(credits=cr)
    assert schedule_3_required(r) is True


def test_schedule_3_required_with_estimated_payments() -> None:
    """Estimated tax payments alone trigger Schedule 3."""
    pay = Payments(estimated_tax_payments_2025=Decimal("5000"))
    r = _minimal_return(payments=pay)
    assert schedule_3_required(r) is True


def test_schedule_3_required_with_extension_payment() -> None:
    """Extension payment alone triggers Schedule 3."""
    pay = Payments(amount_paid_with_4868_extension=Decimal("1000"))
    r = _minimal_return(payments=pay)
    assert schedule_3_required(r) is True


def test_schedule_3_required_with_excess_ss() -> None:
    """Excess Social Security tax withheld alone triggers Schedule 3."""
    pay = Payments(excess_social_security_tax_withheld=Decimal("500"))
    r = _minimal_return(payments=pay)
    assert schedule_3_required(r) is True


def test_schedule_3_required_with_other_credits() -> None:
    """Nonzero other_credits dict triggers Schedule 3."""
    cr = Credits(other_credits={"elderly_or_disabled": Decimal("300")})
    r = _minimal_return(credits=cr)
    assert schedule_3_required(r) is True


# ---------------------------------------------------------------------------
# Test: header population
# ---------------------------------------------------------------------------


def test_header_fields_populated() -> None:
    """Taxpayer name and SSN flow to the header."""
    r = _minimal_return()
    fields = compute_schedule_3_fields(r)

    assert fields.taxpayer_name == "Test Payer"
    assert fields.taxpayer_ssn == "111-22-3333"


# ---------------------------------------------------------------------------
# Test: other_credits sub-line routing
# ---------------------------------------------------------------------------


def test_other_credits_subline_routing() -> None:
    """Well-known keys map to specific sub-lines; unknown keys go to 6z."""
    cr = Credits(
        other_credits={
            "elderly_or_disabled": Decimal("100"),
            "plug_in_vehicle": Decimal("200"),
            "alternative_motor_vehicle": Decimal("300"),
            "general_business": Decimal("400"),
            "adoption_credit": Decimal("500"),
        }
    )
    r = _minimal_return(credits=cr)
    fields = compute_schedule_3_fields(r)

    assert fields.line_6d_elderly_or_disabled_credit == Decimal("100")
    assert fields.line_6g_plug_in_vehicle_credit == Decimal("200")
    assert fields.line_6i_alternative_motor_vehicle_credit == Decimal("300")
    # general_business + adoption_credit = 900 -> 6z
    assert fields.line_6z_other_nonrefundable == Decimal("900")
    # Total line 6: 100 + 200 + 300 + 900 = 1500
    assert fields.line_6_total_other_nonrefundable == Decimal("1500")


# ---------------------------------------------------------------------------
# Test: retirement savings + residential energy in isolation
# ---------------------------------------------------------------------------


def test_retirement_savings_credit() -> None:
    """Retirement savings credit -> line 4."""
    cr = Credits(retirement_savings_credit=Decimal("1000"))
    r = _minimal_return(credits=cr)
    fields = compute_schedule_3_fields(r)

    assert fields.line_4_retirement_savings_credit == Decimal("1000")
    assert fields.line_7_total_other_credits == Decimal("1000")


def test_residential_energy_credit() -> None:
    """Residential energy credit -> line 5a (whole amount; 5b = 0)."""
    cr = Credits(residential_energy_credits=Decimal("2600"))
    r = _minimal_return(credits=cr)
    fields = compute_schedule_3_fields(r)

    assert fields.line_5a_residential_clean_energy_credit == Decimal("2600")
    assert fields.line_5b_energy_efficient_home_improvement == _ZERO
    assert fields.line_7_total_other_credits == Decimal("2600")


# ---------------------------------------------------------------------------
# Test: excess social security tax withheld
# ---------------------------------------------------------------------------


def test_excess_social_security_flows_to_line_11() -> None:
    """excess_social_security_tax_withheld -> line 11."""
    pay = Payments(excess_social_security_tax_withheld=Decimal("750"))
    r = _minimal_return(payments=pay)
    fields = compute_schedule_3_fields(r)

    assert fields.line_11_excess_social_security_withheld == Decimal("750")
    assert fields.line_15_total_other_payments_and_refundable == Decimal("750")


# ---------------------------------------------------------------------------
# Test 7: Layer 2 — scaffold PDF renders
# ---------------------------------------------------------------------------


def test_render_schedule_3_scaffold_pdf(tmp_path: Path) -> None:
    """Layer 2 scaffold: render a PDF with reportlab and verify output."""
    cr = Credits(
        foreign_tax_credit=Decimal("500"),
        dependent_care_credit=Decimal("1200"),
        education_credits_nonrefundable=Decimal("2000"),
        premium_tax_credit_net=Decimal("750"),
        education_credits_refundable=Decimal("400"),
    )
    pay = Payments(
        amount_paid_with_4868_extension=Decimal("1000"),
        excess_social_security_tax_withheld=Decimal("250"),
    )
    r = _minimal_return(credits=cr, payments=pay)
    fields = compute_schedule_3_fields(r)

    out_path = tmp_path / "schedule_3.pdf"
    result = render_schedule_3_pdf(fields, out_path)

    assert result == out_path
    assert out_path.exists()
    # Scaffold PDFs are relatively small but non-trivial
    assert out_path.stat().st_size > 500


def test_render_schedule_3_scaffold_all_zeros(tmp_path: Path) -> None:
    """Even with all zeros, the scaffold renders without error."""
    r = _minimal_return()
    fields = compute_schedule_3_fields(r)

    out_path = tmp_path / "schedule_3_zeros.pdf"
    result = render_schedule_3_pdf(fields, out_path)

    assert result == out_path
    assert out_path.exists()
    assert out_path.stat().st_size > 500


def test_render_schedule_3_contains_expected_text(tmp_path: Path) -> None:
    """Scaffold PDF text contains the schedule title and line values."""
    cr = Credits(foreign_tax_credit=Decimal("1234.56"))
    r = _minimal_return(credits=cr)
    fields = compute_schedule_3_fields(r)

    out_path = tmp_path / "schedule_3_text.pdf"
    render_schedule_3_pdf(fields, out_path)

    # Reopen with pypdfium2 to extract text and verify content.
    pypdfium2 = pytest.importorskip("pypdfium2")
    pdf = pypdfium2.PdfDocument(str(out_path))
    text = pdf[0].get_textpage().get_text_bounded()

    assert "Schedule 3" in text
    assert "1234.56" in text


# ---------------------------------------------------------------------------
# Test: frozen dataclass
# ---------------------------------------------------------------------------


def test_schedule_3_fields_is_frozen() -> None:
    """Schedule3Fields is a frozen dataclass — immutable after creation."""
    fields = Schedule3Fields(line_1_foreign_tax_credit=Decimal("500"))
    with pytest.raises(AttributeError):
        fields.line_1_foreign_tax_credit = Decimal("999")  # type: ignore[misc]
