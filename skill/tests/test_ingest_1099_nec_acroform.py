"""Tests for the 1099-NEC pypdf AcroForm ingester.

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

from skill.scripts.ingest._1099_nec_acroform import (
    FORM_1099_NEC_FIELD_MAP,
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
    "payer_name": "Acme Consulting LLC",
    "payer_tin": "98-7654321",
    "box1_nonemployee_compensation": "15000.00",
    "box4_federal_income_tax_withheld": "1500.00",
}


@pytest.fixture
def fake_1099_nec_pdf(tmp_path) -> Path:
    # Filename contains "1099-NEC" so the classifier resolves it to FORM_1099_NEC
    p = tmp_path / "1099-NEC_client.pdf"
    _make_acroform_pdf(p, _FULL_FIELDS)
    return p


@pytest.fixture
def sparse_1099_nec_pdf(tmp_path) -> Path:
    """A 1099-NEC with only box 1 filled (realistic for most freelancers)."""
    p = tmp_path / "1099-NEC_sparse.pdf"
    _make_acroform_pdf(
        p,
        {
            "payer_name": "Solo Client",
            "box1_nonemployee_compensation": "2500.00",
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
        assert INGESTER.name == "1099_nec_acroform"
        assert INGESTER.tier == 1

    def test_field_map_registered_for_1099_nec(self):
        assert DocumentKind.FORM_1099_NEC in INGESTER.field_map
        mapping = INGESTER.field_map[DocumentKind.FORM_1099_NEC]
        # Sanity check a few entries
        assert (
            mapping["box1_nonemployee_compensation"]
            == "forms_1099_nec[0].box1_nonemployee_compensation"
        )
        assert (
            mapping["box4_federal_income_tax_withheld"]
            == "forms_1099_nec[0].box4_federal_income_tax_withheld"
        )

    def test_field_map_covers_required_boxes(self):
        required = {
            "payer_name",
            "payer_tin",
            "box1_nonemployee_compensation",
            "box4_federal_income_tax_withheld",
        }
        assert required.issubset(set(FORM_1099_NEC_FIELD_MAP.keys()))

    def test_all_map_targets_under_forms_1099_nec(self):
        for canonical in FORM_1099_NEC_FIELD_MAP.values():
            assert canonical.startswith("forms_1099_nec[0].")


class TestCanHandle:
    def test_can_handle_fake_1099_nec(self, fake_1099_nec_pdf):
        assert INGESTER.can_handle(fake_1099_nec_pdf)

    def test_cannot_handle_non_pdf(self, tmp_path):
        p = tmp_path / "note.txt"
        p.write_bytes(b"hello")
        assert not INGESTER.can_handle(p)


class TestIngestFullForm:
    def test_ingest_succeeds(self, fake_1099_nec_pdf):
        result = INGESTER.ingest(fake_1099_nec_pdf)
        assert result.success
        assert result.error is None

    def test_document_kind_is_1099_nec(self, fake_1099_nec_pdf):
        result = INGESTER.ingest(fake_1099_nec_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1099_NEC

    def test_values_flow_to_canonical_paths(self, fake_1099_nec_pdf):
        result = INGESTER.ingest(fake_1099_nec_pdf)
        paths = {f.path: f.value for f in result.partial.fields}

        # Spot-check the load-bearing boxes
        assert (
            paths.get("forms_1099_nec[0].box1_nonemployee_compensation")
            == "15000.00"
        )
        assert (
            paths.get("forms_1099_nec[0].box4_federal_income_tax_withheld")
            == "1500.00"
        )
        assert paths.get("forms_1099_nec[0].payer_name") == "Acme Consulting LLC"
        assert paths.get("forms_1099_nec[0].payer_tin") == "98-7654321"

    def test_no_raw_fallback_paths_for_full_form(self, fake_1099_nec_pdf):
        """When every field is mapped, none should drop to _acroform_raw."""
        result = INGESTER.ingest(fake_1099_nec_pdf)
        for f in result.partial.fields:
            assert not f.path.startswith("_acroform_raw."), (
                f"unmapped field leaked to raw: {f.path}"
            )

    def test_fields_carry_full_confidence(self, fake_1099_nec_pdf):
        result = INGESTER.ingest(fake_1099_nec_pdf)
        assert result.partial.fields  # non-empty
        for f in result.partial.fields:
            assert f.confidence == 1.0


class TestIngestSparseForm:
    def test_sparse_ingest_succeeds(self, sparse_1099_nec_pdf):
        """A 1099-NEC with only box 1 + payer name should still be usable."""
        result = INGESTER.ingest(sparse_1099_nec_pdf)
        assert result.success
        assert result.is_usable

    def test_sparse_reports_what_is_filled(self, sparse_1099_nec_pdf):
        result = INGESTER.ingest(sparse_1099_nec_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        assert (
            paths.get("forms_1099_nec[0].box1_nonemployee_compensation")
            == "2500.00"
        )
        assert paths.get("forms_1099_nec[0].payer_name") == "Solo Client"
        # Unfilled boxes must NOT appear in the partial
        assert "forms_1099_nec[0].box4_federal_income_tax_withheld" not in paths
        assert "forms_1099_nec[0].payer_tin" not in paths

    def test_sparse_document_kind(self, sparse_1099_nec_pdf):
        result = INGESTER.ingest(sparse_1099_nec_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1099_NEC
