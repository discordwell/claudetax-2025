"""Tests for the Tier 1 W-2 AcroForm ingester.

Covers:
- Module-level ``INGESTER`` satisfies the ``Ingester`` Protocol at runtime
- ``can_handle`` is True for a fillable W-2 PDF and False for a non-PDF
- ``ingest`` returns success with ``document_kind == FORM_W2`` and
  extracts canonical-path values (including a state row)
- Base-class fallback: an AcroForm PDF with neither a W-2 filename hint
  nor W-2 content still produces ``_acroform_raw.*`` entries
- Metadata: name/tier
"""
from __future__ import annotations

from pathlib import Path

import pypdf
import pytest
from reportlab.pdfgen import canvas

from skill.scripts.ingest._pipeline import DocumentKind, Ingester
from skill.scripts.ingest._w2_acroform import INGESTER, W2_FIELD_MAP


# ---------------------------------------------------------------------------
# Helper: synthetic fillable PDF (inlined copy of the helper in
# test_ingest_pipeline.py — intentionally not imported across test modules)
# ---------------------------------------------------------------------------


def _make_acroform_pdf(path: Path, fields: dict[str, str]) -> None:
    """Write a minimal AcroForm PDF with the given text fields and values.

    Uses reportlab to draw the text fields (which registers them in the
    /AcroForm dict), then uses pypdf's ``clone_from`` to copy the full
    catalog (including /AcroForm) into a writer so we can set widget values.
    """
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

    reader = pypdf.PdfReader(str(path))
    writer = pypdf.PdfWriter(clone_from=reader)
    writer.update_page_form_field_values(
        writer.pages[0], fields, auto_regenerate=True
    )
    with path.open("wb") as fh:
        writer.write(fh)


# ---------------------------------------------------------------------------
# Fixture values
# ---------------------------------------------------------------------------


W2_WAGES = "65000.00"
W2_FED_WH = "9500.00"
W2_SS_WAGES = "65000.00"
W2_SS_TAX = "4030.00"
W2_MED_WAGES = "65000.00"
W2_MED_TAX = "942.50"
W2_STATE = "CA"
W2_STATE_WAGES = "65000.00"
W2_STATE_TAX = "3200.00"
W2_EMPLOYER_NAME = "Acme Corp"
W2_EMPLOYER_EIN = "12-3456789"


@pytest.fixture
def fake_w2(tmp_path: Path) -> Path:
    """A fillable AcroForm PDF that mimics a W-2 using synthetic field names.

    Filename contains ``w2_`` so the classifier picks it up as FORM_W2.
    """
    p = tmp_path / "w2_acme.pdf"
    _make_acroform_pdf(
        p,
        {
            "employer_name": W2_EMPLOYER_NAME,
            "employer_ein": W2_EMPLOYER_EIN,
            "wages_box1": W2_WAGES,
            "fed_withholding_box2": W2_FED_WH,
            "ss_wages_box3": W2_SS_WAGES,
            "ss_tax_box4": W2_SS_TAX,
            "medicare_wages_box5": W2_MED_WAGES,
            "medicare_tax_box6": W2_MED_TAX,
            "state_box15": W2_STATE,
            "state_wages_box16": W2_STATE_WAGES,
            "state_tax_box17": W2_STATE_TAX,
        },
    )
    return p


@pytest.fixture
def non_pdf(tmp_path: Path) -> Path:
    p = tmp_path / "non_pdf.txt"
    p.write_bytes(b"this is not a pdf")
    return p


@pytest.fixture
def unrelated_acroform_pdf(tmp_path: Path) -> Path:
    """A fillable AcroForm PDF that is NOT a W-2 by filename or content.

    Used to verify the base-class fallback: when the classifier cannot
    identify the document as a W-2, fields should still be emitted under
    ``_acroform_raw.*`` pseudo-paths.
    """
    p = tmp_path / "mystery_form.pdf"
    _make_acroform_pdf(
        p,
        {
            "mystery_field_a": "alpha",
            "mystery_field_b": "beta",
        },
    )
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestW2AcroFormIngesterMetadata:
    def test_satisfies_ingester_protocol(self) -> None:
        assert isinstance(INGESTER, Ingester)

    def test_name_is_w2_acroform(self) -> None:
        assert INGESTER.name == "w2_acroform"

    def test_tier_is_one(self) -> None:
        assert INGESTER.tier == 1

    def test_field_map_registered_under_form_w2(self) -> None:
        assert DocumentKind.FORM_W2 in INGESTER.field_map
        # Sanity: every value in W2_FIELD_MAP is used by the ingester's map
        assert INGESTER.field_map[DocumentKind.FORM_W2] is W2_FIELD_MAP

    def test_field_map_covers_required_canonical_paths(self) -> None:
        """The task requires specific canonical paths to be present."""
        canonical_paths = set(W2_FIELD_MAP.values())
        required = {
            "w2s[0].employer_name",
            "w2s[0].employer_ein",
            "w2s[0].box1_wages",
            "w2s[0].box2_federal_income_tax_withheld",
            "w2s[0].box3_social_security_wages",
            "w2s[0].box4_social_security_tax_withheld",
            "w2s[0].box5_medicare_wages",
            "w2s[0].box6_medicare_tax_withheld",
            "w2s[0].box7_social_security_tips",
            "w2s[0].box8_allocated_tips",
            "w2s[0].box10_dependent_care_benefits",
            "w2s[0].box11_nonqualified_plans",
            "w2s[0].state_rows[0].state",
            "w2s[0].state_rows[0].state_wages",
            "w2s[0].state_rows[0].state_tax_withheld",
        }
        missing = required - canonical_paths
        assert not missing, f"W2_FIELD_MAP missing canonical paths: {missing}"


class TestW2AcroFormIngesterCanHandle:
    def test_can_handle_true_for_fillable_w2(self, fake_w2: Path) -> None:
        assert INGESTER.can_handle(fake_w2) is True

    def test_can_handle_false_for_non_pdf(self, non_pdf: Path) -> None:
        assert INGESTER.can_handle(non_pdf) is False


class TestW2AcroFormIngesterIngest:
    def test_ingest_success_for_fake_w2(self, fake_w2: Path) -> None:
        result = INGESTER.ingest(fake_w2)
        assert result.success
        assert result.error is None

    def test_document_kind_is_form_w2(self, fake_w2: Path) -> None:
        result = INGESTER.ingest(fake_w2)
        assert result.partial.document_kind == DocumentKind.FORM_W2

    def test_box1_wages_mapped_to_canonical_path(self, fake_w2: Path) -> None:
        result = INGESTER.ingest(fake_w2)
        by_path = {f.path: f.value for f in result.partial.fields}
        assert "w2s[0].box1_wages" in by_path
        assert by_path["w2s[0].box1_wages"] == W2_WAGES

    def test_state_row_mapped_to_canonical_path(self, fake_w2: Path) -> None:
        result = INGESTER.ingest(fake_w2)
        by_path = {f.path: f.value for f in result.partial.fields}
        assert "w2s[0].state_rows[0].state" in by_path
        assert by_path["w2s[0].state_rows[0].state"] == W2_STATE
        assert by_path["w2s[0].state_rows[0].state_wages"] == W2_STATE_WAGES
        assert by_path["w2s[0].state_rows[0].state_tax_withheld"] == W2_STATE_TAX

    def test_employer_fields_mapped(self, fake_w2: Path) -> None:
        result = INGESTER.ingest(fake_w2)
        by_path = {f.path: f.value for f in result.partial.fields}
        assert by_path.get("w2s[0].employer_name") == W2_EMPLOYER_NAME
        assert by_path.get("w2s[0].employer_ein") == W2_EMPLOYER_EIN

    def test_all_extracted_fields_have_full_confidence(self, fake_w2: Path) -> None:
        result = INGESTER.ingest(fake_w2)
        assert result.partial.fields, "expected at least one extracted field"
        for f in result.partial.fields:
            assert f.confidence == 1.0

    def test_result_is_usable(self, fake_w2: Path) -> None:
        result = INGESTER.ingest(fake_w2)
        assert result.is_usable


class TestW2AcroFormIngesterFallback:
    """When the doc isn't classified as W-2, the base class should still
    emit raw fields under ``_acroform_raw.*`` pseudo-paths."""

    def test_unrelated_acroform_falls_back_to_raw_paths(
        self, unrelated_acroform_pdf: Path
    ) -> None:
        result = INGESTER.ingest(unrelated_acroform_pdf)
        assert result.success
        # Classifier should NOT have tagged it as W-2
        assert result.partial.document_kind != DocumentKind.FORM_W2
        paths = {f.path for f in result.partial.fields}
        assert "_acroform_raw.mystery_field_a" in paths
        assert "_acroform_raw.mystery_field_b" in paths
        by_path = {f.path: f.value for f in result.partial.fields}
        assert by_path["_acroform_raw.mystery_field_a"] == "alpha"
        assert by_path["_acroform_raw.mystery_field_b"] == "beta"
