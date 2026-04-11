"""Tests for the 1099-INT pypdf AcroForm ingester.

The ingester uses SYNTHETIC field names (documented as a follow-up to replace
with real IRS AcroForm field names). These tests exercise the synthetic map
against a reportlab-generated fillable PDF fixture to prove the path-rewriting
wiring works end-to-end.
"""
from __future__ import annotations

from pathlib import Path

import pypdf
import pytest
from reportlab.pdfgen import canvas

from skill.scripts.ingest._1099_int_acroform import (
    FORM_1099_INT_FIELD_MAP,
    INGESTER,
)
from skill.scripts.ingest._pipeline import DocumentKind, Ingester


# ---------------------------------------------------------------------------
# Synthetic fillable PDF helper (inlined from test_ingest_pipeline.py)
# ---------------------------------------------------------------------------


def _make_acroform_pdf(path: Path, fields: dict[str, str]) -> None:
    """Write a minimal AcroForm PDF with the given text fields and values.

    Uses reportlab to draw the text fields (which registers them in the
    /AcroForm dict), then uses pypdf's clone_from to copy the whole document
    catalog (including /AcroForm) into a writer so we can set widget values.
    """
    # Step 1: reportlab creates a PDF with named acroform fields
    c = canvas.Canvas(str(path))
    form = c.acroForm
    y = 700
    for name, _value in fields.items():
        c.drawString(50, y + 20, name)
        form.textfield(
            name=name,
            x=200,
            y=y,
            width=200,
            height=18,
            borderStyle="solid",
        )
        y -= 40
    c.save()

    # Step 2: clone the full document (incl. /AcroForm) into a writer and set values
    reader = pypdf.PdfReader(str(path))
    writer = pypdf.PdfWriter(clone_from=reader)
    writer.update_page_form_field_values(
        writer.pages[0], fields, auto_regenerate=True
    )
    with path.open("wb") as fh:
        writer.write(fh)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_FULL_FIELDS: dict[str, str] = {
    "payer_name": "First National Bank",
    "payer_tin": "12-3456789",
    "box1_interest_income": "1234.56",
    "box2_early_withdrawal_penalty": "10.00",
    "box3_us_savings_bond_and_treasury_interest": "50.00",
    "box4_federal_income_tax_withheld": "200.00",
    "box5_investment_expenses": "5.00",
    "box6_foreign_tax_paid": "15.00",
    "box8_tax_exempt_interest": "75.00",
    "box9_specified_private_activity_bond_interest": "25.00",
    "box13_bond_premium_on_tax_exempt_bonds": "3.00",
}


@pytest.fixture
def fake_1099_int_pdf(tmp_path) -> Path:
    # Filename contains "1099-INT" so the classifier resolves it to FORM_1099_INT
    p = tmp_path / "1099-INT_bank.pdf"
    _make_acroform_pdf(p, _FULL_FIELDS)
    return p


@pytest.fixture
def sparse_1099_int_pdf(tmp_path) -> Path:
    """A 1099-INT with only box 1 filled (realistic for most savers)."""
    p = tmp_path / "1099-INT_sparse.pdf"
    _make_acroform_pdf(
        p,
        {
            "payer_name": "Credit Union",
            "box1_interest_income": "42.00",
        },
    )
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIngesterContract:
    def test_satisfies_ingester_protocol(self):
        assert isinstance(INGESTER, Ingester)

    def test_name_and_tier(self):
        assert INGESTER.name == "1099_int_acroform"
        assert INGESTER.tier == 1

    def test_field_map_registered_for_1099_int(self):
        assert DocumentKind.FORM_1099_INT in INGESTER.field_map
        mapping = INGESTER.field_map[DocumentKind.FORM_1099_INT]
        # Sanity check a few entries
        assert mapping["box1_interest_income"] == "forms_1099_int[0].box1_interest_income"
        assert (
            mapping["box4_federal_income_tax_withheld"]
            == "forms_1099_int[0].box4_federal_income_tax_withheld"
        )

    def test_field_map_covers_required_boxes(self):
        required = {
            "payer_name",
            "payer_tin",
            "box1_interest_income",
            "box2_early_withdrawal_penalty",
            "box3_us_savings_bond_and_treasury_interest",
            "box4_federal_income_tax_withheld",
            "box5_investment_expenses",
            "box6_foreign_tax_paid",
            "box8_tax_exempt_interest",
            "box9_specified_private_activity_bond_interest",
            "box13_bond_premium_on_tax_exempt_bonds",
        }
        assert required.issubset(set(FORM_1099_INT_FIELD_MAP.keys()))

    def test_all_map_targets_under_forms_1099_int(self):
        for canonical in FORM_1099_INT_FIELD_MAP.values():
            assert canonical.startswith("forms_1099_int[0].")


class TestCanHandle:
    def test_can_handle_fake_1099_int(self, fake_1099_int_pdf):
        assert INGESTER.can_handle(fake_1099_int_pdf)

    def test_cannot_handle_non_pdf(self, tmp_path):
        p = tmp_path / "note.txt"
        p.write_bytes(b"hello")
        assert not INGESTER.can_handle(p)


class TestIngestFullForm:
    def test_ingest_succeeds(self, fake_1099_int_pdf):
        result = INGESTER.ingest(fake_1099_int_pdf)
        assert result.success
        assert result.error is None

    def test_document_kind_is_1099_int(self, fake_1099_int_pdf):
        result = INGESTER.ingest(fake_1099_int_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1099_INT

    def test_values_flow_to_canonical_paths(self, fake_1099_int_pdf):
        result = INGESTER.ingest(fake_1099_int_pdf)
        paths = {f.path: f.value for f in result.partial.fields}

        # Spot-check the load-bearing boxes
        assert paths.get("forms_1099_int[0].box1_interest_income") == "1234.56"
        assert paths.get("forms_1099_int[0].box4_federal_income_tax_withheld") == "200.00"
        assert paths.get("forms_1099_int[0].payer_name") == "First National Bank"
        assert paths.get("forms_1099_int[0].payer_tin") == "12-3456789"
        assert (
            paths.get("forms_1099_int[0].box3_us_savings_bond_and_treasury_interest")
            == "50.00"
        )
        assert paths.get("forms_1099_int[0].box8_tax_exempt_interest") == "75.00"
        assert (
            paths.get("forms_1099_int[0].box13_bond_premium_on_tax_exempt_bonds")
            == "3.00"
        )

    def test_no_raw_fallback_paths_for_full_form(self, fake_1099_int_pdf):
        """When every field is mapped, none should drop to _acroform_raw."""
        result = INGESTER.ingest(fake_1099_int_pdf)
        for f in result.partial.fields:
            assert not f.path.startswith("_acroform_raw."), (
                f"unmapped field leaked to raw: {f.path}"
            )

    def test_fields_carry_full_confidence(self, fake_1099_int_pdf):
        result = INGESTER.ingest(fake_1099_int_pdf)
        assert result.partial.fields  # non-empty
        for f in result.partial.fields:
            assert f.confidence == 1.0


class TestIngestSparseForm:
    def test_sparse_ingest_succeeds(self, sparse_1099_int_pdf):
        """A 1099-INT with only box 1 + payer name should still be usable."""
        result = INGESTER.ingest(sparse_1099_int_pdf)
        assert result.success
        assert result.is_usable

    def test_sparse_reports_what_is_filled(self, sparse_1099_int_pdf):
        result = INGESTER.ingest(sparse_1099_int_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        assert paths.get("forms_1099_int[0].box1_interest_income") == "42.00"
        assert paths.get("forms_1099_int[0].payer_name") == "Credit Union"
        # Unfilled boxes must NOT appear in the partial
        assert "forms_1099_int[0].box4_federal_income_tax_withheld" not in paths
        assert "forms_1099_int[0].box8_tax_exempt_interest" not in paths

    def test_sparse_document_kind(self, sparse_1099_int_pdf):
        result = INGESTER.ingest(sparse_1099_int_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1099_INT
