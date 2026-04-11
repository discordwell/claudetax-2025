"""Tests for skill.scripts.output.form_1040 — Form 1040 PDF scaffold.

Two layers under test:

* Layer 1 — ``compute_form_1040_fields``: assert that values on the
  returned dataclass match the expected Form 1040 line numbers for each
  of the three golden fixtures we already ship. These tests do NOT
  duplicate the engine's golden diff — they only check that the engine's
  ComputedTotals are correctly routed onto Form 1040 line names.

* Layer 2 — ``render_form_1040_pdf``: write a scaffold PDF, reopen it
  with pypdf, and assert the extracted text contains the header and a
  numeric value.

Also: a small sanity test that refund / owed are mutually exclusive on
the rendered fields.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.calc.engine import compute
from skill.scripts.models import CanonicalReturn
from skill.scripts.output.form_1040 import (
    Form1040Fields,
    compute_form_1040_fields,
    render_form_1040_pdf,
)


def _load_fixture(fixtures_dir: Path, name: str) -> CanonicalReturn:
    data = json.loads((fixtures_dir / name / "input.json").read_text())
    return CanonicalReturn.model_validate(data)


# ---------------------------------------------------------------------------
# Layer 1: field mapping
# ---------------------------------------------------------------------------


def test_simple_w2_standard_field_mapping(fixtures_dir: Path) -> None:
    return_ = _load_fixture(fixtures_dir, "simple_w2_standard")
    computed = compute(return_)
    fields = compute_form_1040_fields(computed)

    assert isinstance(fields, Form1040Fields)
    assert fields.filing_status == "single"
    assert fields.taxpayer_name == "Alex Doe"
    assert fields.spouse_name is None

    # Wages
    assert fields.line_1a_total_w2_box1 == Decimal("65000.00")
    assert fields.line_1z_total_wages == Decimal("65000.00")

    # Totals from ComputedTotals
    assert fields.line_9_total_income == Decimal("65000.00")
    assert fields.line_11_adjusted_gross_income == Decimal("65000.00")
    assert fields.line_12_standard_or_itemized_deduction == Decimal("15750.00")
    assert fields.line_15_taxable_income == Decimal("49250.00")
    assert fields.line_16_tax == Decimal("5755.00")
    assert fields.line_24_total_tax == Decimal("5755.00")

    # Withholding
    assert isinstance(fields.line_25a_w2_withholding, Decimal)
    assert fields.line_25a_w2_withholding > Decimal("0")
    assert fields.line_25a_w2_withholding == Decimal("7500.00")

    # Exactly one of refund/owed is populated
    assert (fields.line_34_overpayment > 0) or (fields.line_37_amount_you_owe > 0)
    assert fields.line_34_overpayment == Decimal("1745.00")


def test_w2_investments_itemized_field_mapping(fixtures_dir: Path) -> None:
    return_ = _load_fixture(fixtures_dir, "w2_investments_itemized")
    computed = compute(return_)
    fields = compute_form_1040_fields(computed)

    # Wages: two W-2s (150k + 50k)
    assert fields.line_1z_total_wages == Decimal("200000.00")
    # Interest
    assert fields.line_2b_taxable_interest == Decimal("3000.00")
    # Ordinary dividends / qualified
    assert fields.line_3a_qualified_dividends == Decimal("3000.00")
    assert fields.line_3b_ordinary_dividends == Decimal("5000.00")
    # Long-term cap gain (20000 - 10000) + 500 cap gain distr = 10500
    assert fields.line_7_capital_gain_or_loss == Decimal("10500.00")
    # Itemized deduction: SALT-capped at 10k + mortgage 20k + charity 5k = 35000
    assert fields.line_12_standard_or_itemized_deduction == Decimal("35000.00")
    # W-2 withholding: 18000 + 5000 = 23000
    assert fields.line_25a_w2_withholding == Decimal("23000.00")
    # Spouse populated
    assert fields.spouse_name == "Pat Smith"
    assert fields.filing_status == "mfj"


def test_se_home_office_field_mapping(fixtures_dir: Path) -> None:
    return_ = _load_fixture(fixtures_dir, "se_home_office")
    computed = compute(return_)
    fields = compute_form_1040_fields(computed)

    # No W-2 wages
    assert fields.line_1a_total_w2_box1 == Decimal("0")
    assert fields.line_1z_total_wages == Decimal("0")
    # line_8 is Schedule 1 Part I additional income — no unemployment, so 0.
    # Schedule C net profit flows via engine.compute() into total_income.
    assert fields.line_8_additional_income_from_sch_1 == Decimal("0")
    # line_9 total_income comes from ComputedTotals; Sch C net should be
    # positive and non-zero (120k gross - 27k expenses - 3k home office = 90k).
    assert fields.line_9_total_income == Decimal("90000.00")
    # Engine populates line 11 (AGI) from tenforty — it subtracts 1/2 SE tax.
    assert fields.line_11_adjusted_gross_income < fields.line_9_total_income
    # Total tax is non-zero because of SE tax on the 90k schedule C net.
    assert fields.line_24_total_tax > Decimal("0")
    # Estimated payments fixture has $10k
    assert fields.line_26_estimated_and_prior_year_applied == Decimal("10000.00")


# ---------------------------------------------------------------------------
# Layer 2: PDF rendering (reportlab scaffold)
# ---------------------------------------------------------------------------


def test_render_scaffold_produces_readable_pdf(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    pypdf = pytest.importorskip("pypdf")

    return_ = _load_fixture(fixtures_dir, "simple_w2_standard")
    computed = compute(return_)
    fields = compute_form_1040_fields(computed)

    out_path = tmp_path / "test_1040.pdf"
    result_path = render_form_1040_pdf(fields, out_path)

    assert result_path == out_path
    assert out_path.exists()
    assert out_path.stat().st_size > 0

    reader = pypdf.PdfReader(str(out_path))
    assert len(reader.pages) >= 1
    text = "".join(page.extract_text() or "" for page in reader.pages)

    assert "Form 1040" in text
    # Should show at least one of the W-2 line values (formatted with comma)
    assert ("65,000" in text) or ("65000" in text)


# ---------------------------------------------------------------------------
# Refund vs owed: mutually exclusive
# ---------------------------------------------------------------------------


def _canonical_with_w2(wages: str, withheld: str) -> CanonicalReturn:
    """Small-return helper for the refund/owed sanity test."""
    return CanonicalReturn.model_validate(
        {
            "schema_version": "0.1.0",
            "tax_year": 2025,
            "filing_status": "single",
            "taxpayer": {
                "first_name": "Refund",
                "last_name": "Case",
                "ssn": "111-22-3333",
                "date_of_birth": "1990-01-01",
                "is_blind": False,
                "is_age_65_or_older": False,
            },
            "address": {
                "street1": "1 Test",
                "city": "Springfield",
                "state": "IL",
                "zip": "62701",
            },
            "w2s": [
                {
                    "employer_name": "Acme",
                    "box1_wages": wages,
                    "box2_federal_income_tax_withheld": withheld,
                }
            ],
            "itemize_deductions": False,
        }
    )


def test_refund_vs_owed_mutually_exclusive() -> None:
    # High withholding -> refund
    refund_return = compute(_canonical_with_w2("65000.00", "10000.00"))
    refund_fields = compute_form_1040_fields(refund_return)
    assert refund_fields.line_34_overpayment > Decimal("0")
    assert refund_fields.line_35a_refund_requested > Decimal("0")
    assert refund_fields.line_37_amount_you_owe == Decimal("0")

    # Zero withholding -> owed
    owed_return = compute(_canonical_with_w2("65000.00", "0"))
    owed_fields = compute_form_1040_fields(owed_return)
    assert owed_fields.line_37_amount_you_owe > Decimal("0")
    assert owed_fields.line_34_overpayment == Decimal("0")
    assert owed_fields.line_35a_refund_requested == Decimal("0")
