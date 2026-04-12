"""Tests for ``skill.scripts.output.schedule_2`` — Schedule 2 (Additional Taxes).

Two layers under test:

* Layer 1 — ``compute_schedule_2_fields`` / ``schedule_2_required``:
  verify that the routing from ``OtherTaxes`` and ``ComputedTotals``
  onto Schedule 2 line names is correct, Part I and Part II totals sum
  correctly, and the filing gate fires only when at least one source
  field is nonzero.

* Layer 2 — ``render_schedule_2_pdf``: write a scaffold PDF, reopen
  with ``pypdf``, and assert the file exists and is non-trivially sized.

Sources referenced in assertions:
* IRS 2024 Schedule 2 (Form 1040) line-by-line layout.
* IRS 2024 Instructions for Schedule 2.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from skill.scripts.models import CanonicalReturn
from skill.scripts.output.schedule_2 import (
    Schedule2Fields,
    compute_schedule_2_fields,
    render_schedule_2_pdf,
    schedule_2_required,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ZERO = Decimal("0")


def _base_return_dict() -> dict[str, Any]:
    """Minimal single-filer canonical return dict with zero other_taxes."""
    return {
        "schema_version": "0.1.0",
        "tax_year": 2025,
        "filing_status": "single",
        "taxpayer": {
            "first_name": "Pat",
            "last_name": "Testerson",
            "ssn": "333-22-1111",
            "date_of_birth": "1985-06-15",
            "is_blind": False,
            "is_age_65_or_older": False,
        },
        "address": {
            "street1": "1 Test Lane",
            "city": "Austin",
            "state": "TX",
            "zip": "78701",
        },
        "w2s": [],
        "schedules_c": [],
        "itemize_deductions": False,
    }


def _make_return(**overrides: Any) -> CanonicalReturn:
    """Build a CanonicalReturn with optional other_taxes / computed overrides."""
    data = _base_return_dict()
    if "other_taxes" in overrides:
        data["other_taxes"] = overrides.pop("other_taxes")
    if "computed" in overrides:
        data["computed"] = overrides.pop("computed")
    data.update(overrides)
    return CanonicalReturn.model_validate(data)


# ---------------------------------------------------------------------------
# Layer 1: AMT only
# ---------------------------------------------------------------------------


def test_amt_only() -> None:
    """$5,000 AMT with no other taxes.

    Line 1 = $5,000, line 3 = $5,000, Part II lines all zero,
    line 21 = 0.
    """
    canonical = _make_return(
        computed={"alternative_minimum_tax": "5000"},
    )
    fields = compute_schedule_2_fields(canonical)

    assert fields.line_1_amt == Decimal("5000")
    assert fields.line_2_excess_aptc == _ZERO
    assert fields.line_3_part_i_total == Decimal("5000")

    # Part II all zeros
    assert fields.line_6_se_tax == _ZERO
    assert fields.line_10_additional_medicare == _ZERO
    assert fields.line_11_niit == _ZERO
    assert fields.line_8_early_distribution == _ZERO
    assert fields.line_21_part_ii_total == _ZERO


# ---------------------------------------------------------------------------
# Layer 1: SE tax only
# ---------------------------------------------------------------------------


def test_se_tax_only() -> None:
    """$9,194 SE tax with no AMT or other taxes.

    Line 6 = $9,194, line 21 = $9,194, Part I all zeros.
    """
    canonical = _make_return(
        other_taxes={"self_employment_tax": "9194"},
    )
    fields = compute_schedule_2_fields(canonical)

    # Part I all zeros
    assert fields.line_1_amt == _ZERO
    assert fields.line_3_part_i_total == _ZERO

    # Part II
    assert fields.line_6_se_tax == Decimal("9194")
    assert fields.line_21_part_ii_total == Decimal("9194")


# ---------------------------------------------------------------------------
# Layer 1: full Schedule 2 — AMT + SE + NIIT + Additional Medicare
# ---------------------------------------------------------------------------


def test_full_schedule_2() -> None:
    """AMT + SE + NIIT + Additional Medicare all nonzero.

    Verify both part totals sum correctly.
    """
    canonical = _make_return(
        computed={"alternative_minimum_tax": "3000"},
        other_taxes={
            "self_employment_tax": "7000",
            "additional_medicare_tax": "1500",
            "net_investment_income_tax": "2000",
            "early_distribution_penalty": "500",
        },
    )
    fields = compute_schedule_2_fields(canonical)

    # Part I
    assert fields.line_1_amt == Decimal("3000")
    assert fields.line_3_part_i_total == Decimal("3000")

    # Part II
    assert fields.line_6_se_tax == Decimal("7000")
    assert fields.line_10_additional_medicare == Decimal("1500")
    assert fields.line_11_niit == Decimal("2000")
    assert fields.line_8_early_distribution == Decimal("500")
    assert fields.line_21_part_ii_total == Decimal("11000")

    # Verify line 21 is exact sum of Part II lines
    expected_part_ii = (
        fields.line_6_se_tax
        + fields.line_7_unreported_tip_tax
        + fields.line_8_early_distribution
        + fields.line_10_additional_medicare
        + fields.line_11_niit
        + fields.line_17_recapture_taxes
        + fields.line_18_section_965
        + fields.line_19_other_taxes
    )
    assert fields.line_21_part_ii_total == expected_part_ii


# ---------------------------------------------------------------------------
# Layer 1: all zeros -> gate says not needed
# ---------------------------------------------------------------------------


def test_all_zeros_not_required() -> None:
    """When every source field is zero, Schedule 2 is NOT required."""
    canonical = _make_return()
    assert schedule_2_required(canonical) is False

    fields = compute_schedule_2_fields(canonical)
    assert fields.line_3_part_i_total == _ZERO
    assert fields.line_21_part_ii_total == _ZERO


# ---------------------------------------------------------------------------
# Layer 1: gate fires for each individual source
# ---------------------------------------------------------------------------


def test_gate_fires_for_amt() -> None:
    canonical = _make_return(
        computed={"alternative_minimum_tax": "100"},
    )
    assert schedule_2_required(canonical) is True


def test_gate_fires_for_se_tax() -> None:
    canonical = _make_return(
        other_taxes={"self_employment_tax": "100"},
    )
    assert schedule_2_required(canonical) is True


def test_gate_fires_for_additional_medicare() -> None:
    canonical = _make_return(
        other_taxes={"additional_medicare_tax": "100"},
    )
    assert schedule_2_required(canonical) is True


def test_gate_fires_for_niit() -> None:
    canonical = _make_return(
        other_taxes={"net_investment_income_tax": "100"},
    )
    assert schedule_2_required(canonical) is True


def test_gate_fires_for_early_distribution_penalty() -> None:
    canonical = _make_return(
        other_taxes={"early_distribution_penalty": "100"},
    )
    assert schedule_2_required(canonical) is True


# ---------------------------------------------------------------------------
# Layer 1: header populated from taxpayer
# ---------------------------------------------------------------------------


def test_header_populated_from_taxpayer() -> None:
    canonical = _make_return(
        other_taxes={"self_employment_tax": "100"},
    )
    fields = compute_schedule_2_fields(canonical)
    assert fields.taxpayer_name == "Pat Testerson"
    assert fields.taxpayer_ssn == "333-22-1111"


# ---------------------------------------------------------------------------
# Layer 1: frozen dataclass
# ---------------------------------------------------------------------------


def test_fields_is_frozen_dataclass() -> None:
    """Schedule2Fields should be immutable."""
    canonical = _make_return(
        other_taxes={"self_employment_tax": "100"},
    )
    fields = compute_schedule_2_fields(canonical)
    assert isinstance(fields, Schedule2Fields)
    with pytest.raises((AttributeError, Exception)):
        fields.line_6_se_tax = _ZERO  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Layer 2: scaffold PDF rendering
# ---------------------------------------------------------------------------


def test_render_produces_non_empty_pdf(tmp_path: Path) -> None:
    """The scaffold PDF must be non-trivially sized and on disk."""
    canonical = _make_return(
        computed={"alternative_minimum_tax": "3000"},
        other_taxes={
            "self_employment_tax": "7000",
            "additional_medicare_tax": "1500",
            "net_investment_income_tax": "2000",
        },
    )
    fields = compute_schedule_2_fields(canonical)
    out_path = tmp_path / "schedule_2.pdf"
    result_path = render_schedule_2_pdf(fields, out_path)

    assert result_path == out_path
    assert out_path.exists()
    assert out_path.stat().st_size > 500


def test_render_scaffold_contains_title_text(tmp_path: Path) -> None:
    """The scaffold PDF text should contain the form title."""
    canonical = _make_return(
        other_taxes={"self_employment_tax": "5000"},
    )
    fields = compute_schedule_2_fields(canonical)
    out_path = tmp_path / "schedule_2.pdf"
    render_schedule_2_pdf(fields, out_path)

    try:
        import pypdf
    except BaseException:
        pytest.skip("pypdf not importable in this environment")

    reader = pypdf.PdfReader(str(out_path))
    text = reader.pages[0].extract_text() or ""
    assert "Schedule 2" in text


def test_render_creates_parent_dirs(tmp_path: Path) -> None:
    """The renderer should mkdir the parent path if it doesn't exist."""
    canonical = _make_return(
        other_taxes={"self_employment_tax": "1000"},
    )
    fields = compute_schedule_2_fields(canonical)

    nested = tmp_path / "a" / "b" / "schedule_2.pdf"
    assert not nested.parent.exists()
    render_schedule_2_pdf(fields, nested)
    assert nested.exists()


def test_render_scaffold_shows_nonzero_values(tmp_path: Path) -> None:
    """Non-zero line values should appear in the rendered PDF text."""
    canonical = _make_return(
        computed={"alternative_minimum_tax": "12345"},
        other_taxes={"self_employment_tax": "6789"},
    )
    fields = compute_schedule_2_fields(canonical)
    out_path = tmp_path / "schedule_2.pdf"
    render_schedule_2_pdf(fields, out_path)

    try:
        import pypdf
    except BaseException:
        pytest.skip("pypdf not importable in this environment")

    reader = pypdf.PdfReader(str(out_path))
    text = reader.pages[0].extract_text() or ""
    assert "12345.00" in text
    assert "6789.00" in text
