"""Tests for the 1099-DIV pypdf AcroForm ingester.

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

from skill.scripts.ingest._1099_div_acroform import (
    FORM_1099_DIV_FIELD_MAP,
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
    "payer_name": "Vanguard Total Stock Market Fund",
    "payer_tin": "98-7654321",
    "box1a_ordinary_dividends": "4321.00",
    "box1b_qualified_dividends": "3800.00",
    "box2a_total_capital_gain_distributions": "1500.00",
    "box2b_unrecaptured_sec_1250_gain": "100.00",
    "box2c_section_1202_gain": "50.00",
    "box2d_collectibles_28pct_gain": "25.00",
    "box3_nondividend_distributions": "12.00",
    "box4_federal_income_tax_withheld": "600.00",
    "box5_section_199a_dividends": "80.00",
    "box6_investment_expenses": "7.00",
    "box7_foreign_tax_paid": "35.00",
    "box11_exempt_interest_dividends": "9.00",
}


@pytest.fixture
def fake_1099_div_pdf(tmp_path) -> Path:
    # Filename contains "1099-DIV" so the classifier resolves it to FORM_1099_DIV
    p = tmp_path / "1099-DIV_fund.pdf"
    _make_acroform_pdf(p, _FULL_FIELDS)
    return p


@pytest.fixture
def sparse_1099_div_pdf(tmp_path) -> Path:
    """A 1099-DIV with only box 1a filled (realistic for small holdings)."""
    p = tmp_path / "1099-DIV_sparse.pdf"
    _make_acroform_pdf(
        p,
        {
            "payer_name": "Tiny Brokerage",
            "box1a_ordinary_dividends": "17.00",
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
        assert INGESTER.name == "1099_div_acroform"
        assert INGESTER.tier == 1

    def test_field_map_registered_for_1099_div(self):
        assert DocumentKind.FORM_1099_DIV in INGESTER.field_map
        mapping = INGESTER.field_map[DocumentKind.FORM_1099_DIV]
        # Sanity check a few entries
        assert (
            mapping["box1a_ordinary_dividends"]
            == "forms_1099_div[0].box1a_ordinary_dividends"
        )
        assert (
            mapping["box4_federal_income_tax_withheld"]
            == "forms_1099_div[0].box4_federal_income_tax_withheld"
        )

    def test_field_map_covers_required_boxes(self):
        required = {
            "payer_name",
            "payer_tin",
            "box1a_ordinary_dividends",
            "box1b_qualified_dividends",
            "box2a_total_capital_gain_distributions",
            "box2b_unrecaptured_sec_1250_gain",
            "box2c_section_1202_gain",
            "box2d_collectibles_28pct_gain",
            "box3_nondividend_distributions",
            "box4_federal_income_tax_withheld",
            "box5_section_199a_dividends",
            "box6_investment_expenses",
            "box7_foreign_tax_paid",
            "box11_exempt_interest_dividends",
        }
        assert required.issubset(set(FORM_1099_DIV_FIELD_MAP.keys()))

    def test_all_map_targets_under_forms_1099_div(self):
        for canonical in FORM_1099_DIV_FIELD_MAP.values():
            assert canonical.startswith("forms_1099_div[0].")


class TestCanHandle:
    def test_can_handle_fake_1099_div(self, fake_1099_div_pdf):
        assert INGESTER.can_handle(fake_1099_div_pdf)

    def test_cannot_handle_non_pdf(self, tmp_path):
        p = tmp_path / "note.txt"
        p.write_bytes(b"hello")
        assert not INGESTER.can_handle(p)


class TestIngestFullForm:
    def test_ingest_succeeds(self, fake_1099_div_pdf):
        result = INGESTER.ingest(fake_1099_div_pdf)
        assert result.success
        assert result.error is None

    def test_document_kind_is_1099_div(self, fake_1099_div_pdf):
        result = INGESTER.ingest(fake_1099_div_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1099_DIV

    def test_values_flow_to_canonical_paths(self, fake_1099_div_pdf):
        result = INGESTER.ingest(fake_1099_div_pdf)
        paths = {f.path: f.value for f in result.partial.fields}

        # Spot-check the load-bearing boxes called out in the task spec
        assert paths.get("forms_1099_div[0].box1a_ordinary_dividends") == "4321.00"
        assert paths.get("forms_1099_div[0].box1b_qualified_dividends") == "3800.00"
        assert (
            paths.get("forms_1099_div[0].box2a_total_capital_gain_distributions")
            == "1500.00"
        )
        assert (
            paths.get("forms_1099_div[0].box4_federal_income_tax_withheld")
            == "600.00"
        )
        assert paths.get("forms_1099_div[0].box5_section_199a_dividends") == "80.00"
        assert (
            paths.get("forms_1099_div[0].payer_name")
            == "Vanguard Total Stock Market Fund"
        )
        assert paths.get("forms_1099_div[0].payer_tin") == "98-7654321"
        assert (
            paths.get("forms_1099_div[0].box11_exempt_interest_dividends") == "9.00"
        )

    def test_no_raw_fallback_paths_for_full_form(self, fake_1099_div_pdf):
        """When every field is mapped, none should drop to _acroform_raw."""
        result = INGESTER.ingest(fake_1099_div_pdf)
        for f in result.partial.fields:
            assert not f.path.startswith("_acroform_raw."), (
                f"unmapped field leaked to raw: {f.path}"
            )

    def test_fields_carry_full_confidence(self, fake_1099_div_pdf):
        result = INGESTER.ingest(fake_1099_div_pdf)
        assert result.partial.fields  # non-empty
        for f in result.partial.fields:
            assert f.confidence == 1.0


class TestIngestSparseForm:
    def test_sparse_ingest_succeeds(self, sparse_1099_div_pdf):
        """A 1099-DIV with only box 1a + payer name should still be usable."""
        result = INGESTER.ingest(sparse_1099_div_pdf)
        assert result.success
        assert result.is_usable

    def test_sparse_reports_what_is_filled(self, sparse_1099_div_pdf):
        result = INGESTER.ingest(sparse_1099_div_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        assert paths.get("forms_1099_div[0].box1a_ordinary_dividends") == "17.00"
        assert paths.get("forms_1099_div[0].payer_name") == "Tiny Brokerage"
        # Unfilled boxes must NOT appear in the partial
        assert "forms_1099_div[0].box4_federal_income_tax_withheld" not in paths
        assert "forms_1099_div[0].box1b_qualified_dividends" not in paths

    def test_sparse_document_kind(self, sparse_1099_div_pdf):
        result = INGESTER.ingest(sparse_1099_div_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1099_DIV
