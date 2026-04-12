"""Tests for Form 8606 (Nondeductible IRAs) compute and render layers.

Covers:

1. Simple nondeductible contribution with no distributions — basis = $6,500
2. Distribution with basis — $20k basis, $100k total, $10k dist
3. Roth conversion with basis — $30k basis, $200k total, $50k conversion
4. Zero basis (all deductible) — 100% taxable
5. Basis exceeds distribution (no negative tax)
6. Layer 2 scaffold renders

Authority: IRS Form 8606 (TY2025) and IRS Instructions for Form 8606.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.models import (
    Address,
    CanonicalReturn,
    FilingStatus,
    IRAInfo,
    Person,
)
from skill.scripts.output.form_8606 import (
    Form8606Fields,
    compute_form_8606_fields,
    render_form_8606_pdf,
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
    )


def _address() -> Address:
    return Address(street1="1 Test St", city="Springfield", state="IL", zip="62701")


def _minimal_return(
    *,
    ira_info: IRAInfo | None = None,
    filing_status: FilingStatus = FilingStatus.SINGLE,
) -> CanonicalReturn:
    """Build a minimal CanonicalReturn with optional IRAInfo."""
    return CanonicalReturn.model_validate(
        {
            "schema_version": "0.1.0",
            "tax_year": 2025,
            "filing_status": filing_status.value,
            "taxpayer": _person().model_dump(mode="json"),
            "address": _address().model_dump(mode="json"),
            "ira_info": ira_info.model_dump(mode="json") if ira_info else None,
        }
    )


# ---------------------------------------------------------------------------
# 1. Simple nondeductible contribution, no distributions
# ---------------------------------------------------------------------------


def test_simple_nondeductible_contribution_basis() -> None:
    """$6,500 nondeductible contribution, no distributions.

    Line 1 = $6,500, line 2 = 0, line 3 = $6,500, line 4 = 0,
    line 5 = $6,500. No distributions or conversions, so line 9 = line 6
    (year-end value). With a $50k year-end value:
      line 10 = 6500 / 50000 = 0.130
      lines 11/12 = 0 (no distributions/conversions)
      line 13 = 0
      line 14 = $6,500 (full basis carries forward)
    """
    ira = IRAInfo(
        nondeductible_contributions_current_year=Decimal("6500"),
        total_ira_value_year_end=Decimal("50000"),
    )
    r = _minimal_return(ira_info=ira)
    fields = compute_form_8606_fields(r)

    assert fields.line_1_nondeductible_contributions == Decimal("6500.00")
    assert fields.line_2_prior_year_basis == Decimal("0.00")
    assert fields.line_3_add_1_and_2 == Decimal("6500.00")
    assert fields.line_5_subtract_4_from_3 == Decimal("6500.00")
    assert fields.line_7_distributions == Decimal("0.00")
    assert fields.line_8_roth_conversions == Decimal("0.00")
    assert fields.line_13_taxable_distributions == Decimal("0.00")
    assert fields.line_14_remaining_basis == Decimal("6500.00")


# ---------------------------------------------------------------------------
# 2. Distribution with basis
# ---------------------------------------------------------------------------


def test_distribution_with_basis() -> None:
    """$20k basis, $100k total IRA value, $10k distribution.

    line 5 = $20,000 (basis)
    line 6 = $100,000 (year-end value)
    line 7 = $10,000 (distributions)
    line 8 = $0 (no conversions)
    line 9 = $100,000 + $10,000 + $0 = $110,000
    line 10 = $20,000 / $110,000 = 0.182 (rounded to 3 decimals)
    line 11 = $10,000 x 0.182 = $1,820 (nontaxable)
    line 13 = $10,000 - $1,820 = $8,180 (taxable)
    line 14 = $20,000 - $1,820 = $18,180 (remaining basis)
    """
    ira = IRAInfo(
        prior_year_basis=Decimal("20000"),
        total_ira_value_year_end=Decimal("100000"),
        distributions_received=Decimal("10000"),
    )
    r = _minimal_return(ira_info=ira)
    fields = compute_form_8606_fields(r)

    assert fields.line_5_subtract_4_from_3 == Decimal("20000.00")
    assert fields.line_9_total_ira_value_base == Decimal("110000.00")
    assert fields.line_10_nontaxable_percentage == Decimal("0.182")
    assert fields.line_11_nontaxable_distributions == Decimal("1820.00")
    assert fields.line_13_taxable_distributions == Decimal("8180.00")
    assert fields.line_14_remaining_basis == Decimal("18180.00")

    # The task specification says nontaxable = $2k, taxable = $8k.
    # That uses a simplified 20k/100k = 0.200 ratio (ignoring line 9
    # which adds distributions to the denominator per the actual IRS
    # form). Verify the taxable amount is in the expected ballpark.
    assert fields.line_13_taxable_distributions > Decimal("8000")
    assert fields.line_13_taxable_distributions < Decimal("9000")


# ---------------------------------------------------------------------------
# 3. Roth conversion with basis
# ---------------------------------------------------------------------------


def test_roth_conversion_with_basis() -> None:
    """$30k basis, $200k total, $50k Roth conversion.

    line 5 = $30,000 (basis)
    line 6 = $200,000 (year-end value)
    line 7 = $0 (no distributions)
    line 8 = $50,000 (conversion)
    line 9 = $200,000 + $0 + $50,000 = $250,000
    line 10 = $30,000 / $250,000 = 0.120
    line 12 = $50,000 x 0.120 = $6,000 (nontaxable conversion)
    line 16 = $50,000 - $6,000 = $44,000 (taxable conversion)
    line 14 = $30,000 - $6,000 = $24,000 (remaining basis)
    """
    ira = IRAInfo(
        prior_year_basis=Decimal("30000"),
        total_ira_value_year_end=Decimal("200000"),
        roth_conversions=Decimal("50000"),
    )
    r = _minimal_return(ira_info=ira)
    fields = compute_form_8606_fields(r)

    assert fields.line_5_subtract_4_from_3 == Decimal("30000.00")
    assert fields.line_9_total_ira_value_base == Decimal("250000.00")
    assert fields.line_10_nontaxable_percentage == Decimal("0.120")
    assert fields.line_12_nontaxable_conversions == Decimal("6000.00")
    assert fields.line_16_taxable_conversion == Decimal("44000.00")
    assert fields.line_14_remaining_basis == Decimal("24000.00")

    # The task specification says nontaxable = $7.5k, taxable = $42.5k.
    # That uses 30k/200k = 0.150 (ignoring line 9 denominator adjustment).
    # Our correct IRS arithmetic uses the proper denominator (250k).
    assert fields.line_16_taxable_conversion > Decimal("42000")
    assert fields.line_16_taxable_conversion < Decimal("45000")


# ---------------------------------------------------------------------------
# 4. Zero basis (all deductible contributions) — 100% taxable
# ---------------------------------------------------------------------------


def test_zero_basis_fully_taxable() -> None:
    """No nondeductible contributions and no prior basis — 100% taxable.

    line 5 = 0 (no basis)
    line 10 = 0 (no nontaxable percentage)
    line 11 = 0
    line 13 = $10,000 (fully taxable distribution)
    """
    ira = IRAInfo(
        total_ira_value_year_end=Decimal("100000"),
        distributions_received=Decimal("10000"),
    )
    r = _minimal_return(ira_info=ira)
    fields = compute_form_8606_fields(r)

    assert fields.line_5_subtract_4_from_3 == Decimal("0.00")
    assert fields.line_10_nontaxable_percentage == Decimal("0.000")
    assert fields.line_11_nontaxable_distributions == Decimal("0.00")
    assert fields.line_13_taxable_distributions == Decimal("10000.00")
    assert fields.line_14_remaining_basis == Decimal("0.00")


# ---------------------------------------------------------------------------
# 5. Basis exceeds distribution (no negative tax)
# ---------------------------------------------------------------------------


def test_basis_exceeds_distribution_no_negative() -> None:
    """Large basis relative to distribution — taxable portion is small,
    never negative.

    $50k basis, $60k total IRA value, $5k distribution.
    line 9 = 60000 + 5000 = 65000
    line 10 = 50000 / 65000 = 0.769
    line 11 = 5000 x 0.769 = 3845
    line 13 = 5000 - 3845 = 1155
    line 14 = 50000 - 3845 = 46155
    """
    ira = IRAInfo(
        prior_year_basis=Decimal("50000"),
        total_ira_value_year_end=Decimal("60000"),
        distributions_received=Decimal("5000"),
    )
    r = _minimal_return(ira_info=ira)
    fields = compute_form_8606_fields(r)

    assert fields.line_13_taxable_distributions >= Decimal("0")
    assert fields.line_14_remaining_basis >= Decimal("0")
    # Nontaxable portion should be large relative to distribution
    assert fields.line_11_nontaxable_distributions > Decimal("3000")
    # Most of the basis carries forward
    assert fields.line_14_remaining_basis > Decimal("45000")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_raises_when_ira_info_is_none() -> None:
    """compute_form_8606_fields raises ValueError when ira_info is None."""
    r = _minimal_return(ira_info=None)
    with pytest.raises(ValueError, match="ira_info"):
        compute_form_8606_fields(r)


def test_contributions_withdrawn_reduces_basis() -> None:
    """Line 4 (contributions withdrawn by due date) reduces the basis on line 5."""
    ira = IRAInfo(
        nondeductible_contributions_current_year=Decimal("6500"),
        contributions_withdrawn_by_due_date=Decimal("2000"),
        total_ira_value_year_end=Decimal("50000"),
    )
    r = _minimal_return(ira_info=ira)
    fields = compute_form_8606_fields(r)

    assert fields.line_3_add_1_and_2 == Decimal("6500.00")
    assert fields.line_4_contributions_withdrawn == Decimal("2000.00")
    assert fields.line_5_subtract_4_from_3 == Decimal("4500.00")


def test_nontaxable_percentage_capped_at_one() -> None:
    """Line 10 cannot exceed 1.000, even with very high basis vs value.

    This can happen transiently when the year-end value drops below
    basis (e.g., market crash after contributions).
    """
    ira = IRAInfo(
        prior_year_basis=Decimal("50000"),
        total_ira_value_year_end=Decimal("30000"),
        distributions_received=Decimal("5000"),
    )
    r = _minimal_return(ira_info=ira)
    fields = compute_form_8606_fields(r)

    assert fields.line_10_nontaxable_percentage <= Decimal("1.000")


def test_zero_distributions_and_conversions() -> None:
    """When there are no distributions or conversions, basis fully carries forward."""
    ira = IRAInfo(
        nondeductible_contributions_current_year=Decimal("7000"),
        prior_year_basis=Decimal("13000"),
        total_ira_value_year_end=Decimal("100000"),
    )
    r = _minimal_return(ira_info=ira)
    fields = compute_form_8606_fields(r)

    assert fields.line_3_add_1_and_2 == Decimal("20000.00")
    assert fields.line_5_subtract_4_from_3 == Decimal("20000.00")
    assert fields.line_11_nontaxable_distributions == Decimal("0.00")
    assert fields.line_12_nontaxable_conversions == Decimal("0.00")
    assert fields.line_14_remaining_basis == Decimal("20000.00")


def test_header_fields_populated() -> None:
    """Layer 1 populates taxpayer name and SSN from canonical return."""
    ira = IRAInfo(
        nondeductible_contributions_current_year=Decimal("6500"),
        total_ira_value_year_end=Decimal("50000"),
    )
    r = _minimal_return(ira_info=ira)
    fields = compute_form_8606_fields(r)

    assert fields.taxpayer_name == "Test Payer"
    assert fields.taxpayer_ssn == "111-22-3333"


# ---------------------------------------------------------------------------
# 6. Layer 2 scaffold renders
# ---------------------------------------------------------------------------


def test_render_scaffold_produces_pdf(tmp_path: Path) -> None:
    """Layer 2 scaffold renders a non-empty PDF."""
    ira = IRAInfo(
        nondeductible_contributions_current_year=Decimal("6500"),
        prior_year_basis=Decimal("13500"),
        total_ira_value_year_end=Decimal("100000"),
        distributions_received=Decimal("10000"),
        roth_conversions=Decimal("5000"),
    )
    r = _minimal_return(ira_info=ira)
    fields = compute_form_8606_fields(r)

    out_path = tmp_path / "form_8606.pdf"
    result = render_form_8606_pdf(fields, out_path)

    assert result == out_path
    assert out_path.exists()
    assert out_path.stat().st_size > 1000  # Non-trivial PDF


def test_render_scaffold_creates_parent_dirs(tmp_path: Path) -> None:
    """Layer 2 creates parent directories if they don't exist."""
    ira = IRAInfo(
        nondeductible_contributions_current_year=Decimal("6500"),
        total_ira_value_year_end=Decimal("50000"),
    )
    r = _minimal_return(ira_info=ira)
    fields = compute_form_8606_fields(r)

    out_path = tmp_path / "subdir" / "nested" / "form_8606.pdf"
    result = render_form_8606_pdf(fields, out_path)

    assert result == out_path
    assert out_path.exists()


def test_render_scaffold_with_zero_fields(tmp_path: Path) -> None:
    """Scaffold renders even when all values are zero (default fields)."""
    fields = Form8606Fields(taxpayer_name="Zero Test", taxpayer_ssn="000-00-0000")
    out_path = tmp_path / "form_8606_zero.pdf"
    result = render_form_8606_pdf(fields, out_path)

    assert result == out_path
    assert out_path.exists()
    assert out_path.stat().st_size > 1000
