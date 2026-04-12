"""Tests for the 1098-T (Tuition Statement) pypdf AcroForm ingester.

Exercises the synthetic field-name map against a reportlab-generated fillable
PDF fixture to prove the path-rewriting wiring works end-to-end, then
validates the real IRS widget map against the archived ``f1098t_ty2024.pdf``.
"""
from __future__ import annotations

from pathlib import Path

import pypdf
import pytest
from reportlab.pdfgen import canvas

from skill.scripts.ingest._1098_t_acroform import (
    FORM_1098_T_FIELD_MAP,
    INGESTER,
)
from skill.scripts.ingest._pipeline import DocumentKind, Ingester


# ---------------------------------------------------------------------------
# Synthetic fillable PDF helper
# ---------------------------------------------------------------------------


def _make_acroform_pdf(path: Path, fields: dict[str, str]) -> None:
    """Write a minimal AcroForm PDF with the given text fields and values."""
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
# Fixtures
# ---------------------------------------------------------------------------

_FULL_FIELDS: dict[str, str] = {
    "institution_name": "State University",
    "institution_ein": "12-3456789",
    "student_ssn": "987-65-4321",
    "box1_payments_received": "15000.00",
    "box4_adjustments_prior_year": "500.00",
    "box5_scholarships": "3000.00",
    "box6_adjustments_to_scholarships": "200.00",
    "box10_insurance_contract_reimbursement": "0.00",
}


@pytest.fixture
def fake_1098_t_pdf(tmp_path) -> Path:
    p = tmp_path / "1098-T_tuition.pdf"
    _make_acroform_pdf(p, _FULL_FIELDS)
    return p


@pytest.fixture
def sparse_1098_t_pdf(tmp_path) -> Path:
    """A 1098-T with only box 1 + institution name filled."""
    p = tmp_path / "1098-T_sparse.pdf"
    _make_acroform_pdf(
        p,
        {
            "institution_name": "Community College",
            "box1_payments_received": "4500.00",
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
        assert INGESTER.name == "1098_t_acroform"
        assert INGESTER.tier == 1

    def test_field_map_registered_for_1098_t(self):
        assert DocumentKind.FORM_1098_T in INGESTER.field_map
        mapping = INGESTER.field_map[DocumentKind.FORM_1098_T]
        assert (
            mapping["box1_payments_received"]
            == "forms_1098_t[0].box1_payments_received"
        )
        assert (
            mapping["box5_scholarships"]
            == "forms_1098_t[0].box5_scholarships"
        )

    def test_field_map_covers_required_boxes(self):
        required = {
            "institution_name",
            "institution_ein",
            "student_ssn",
            "box1_payments_received",
            "box4_adjustments_prior_year",
            "box5_scholarships",
            "box6_adjustments_to_scholarships",
            "box10_insurance_contract_reimbursement",
        }
        assert required.issubset(set(FORM_1098_T_FIELD_MAP.keys()))

    def test_all_map_targets_under_forms_1098_t(self):
        for canonical in FORM_1098_T_FIELD_MAP.values():
            assert canonical.startswith("forms_1098_t[0].")


class TestCanHandle:
    def test_can_handle_fake_1098_t(self, fake_1098_t_pdf):
        assert INGESTER.can_handle(fake_1098_t_pdf)

    def test_cannot_handle_non_pdf(self, tmp_path):
        p = tmp_path / "note.txt"
        p.write_bytes(b"hello")
        assert not INGESTER.can_handle(p)


class TestIngestFullForm:
    def test_ingest_succeeds(self, fake_1098_t_pdf):
        result = INGESTER.ingest(fake_1098_t_pdf)
        assert result.success
        assert result.error is None

    def test_document_kind_is_1098_t(self, fake_1098_t_pdf):
        result = INGESTER.ingest(fake_1098_t_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1098_T

    def test_values_flow_to_canonical_paths(self, fake_1098_t_pdf):
        result = INGESTER.ingest(fake_1098_t_pdf)
        paths = {f.path: f.value for f in result.partial.fields}

        assert (
            paths.get("forms_1098_t[0].box1_payments_received")
            == "15000.00"
        )
        assert (
            paths.get("forms_1098_t[0].box5_scholarships")
            == "3000.00"
        )
        assert (
            paths.get("forms_1098_t[0].institution_name")
            == "State University"
        )
        assert (
            paths.get("forms_1098_t[0].institution_ein")
            == "12-3456789"
        )
        assert (
            paths.get("forms_1098_t[0].student_ssn")
            == "987-65-4321"
        )
        assert (
            paths.get("forms_1098_t[0].box4_adjustments_prior_year")
            == "500.00"
        )
        assert (
            paths.get("forms_1098_t[0].box6_adjustments_to_scholarships")
            == "200.00"
        )

    def test_no_raw_fallback_paths_for_full_form(self, fake_1098_t_pdf):
        result = INGESTER.ingest(fake_1098_t_pdf)
        for f in result.partial.fields:
            assert not f.path.startswith("_acroform_raw."), (
                f"unmapped field leaked to raw: {f.path}"
            )

    def test_fields_carry_full_confidence(self, fake_1098_t_pdf):
        result = INGESTER.ingest(fake_1098_t_pdf)
        assert result.partial.fields
        for f in result.partial.fields:
            assert f.confidence == 1.0


class TestIngestSparseForm:
    def test_sparse_ingest_succeeds(self, sparse_1098_t_pdf):
        result = INGESTER.ingest(sparse_1098_t_pdf)
        assert result.success
        assert result.is_usable

    def test_sparse_reports_what_is_filled(self, sparse_1098_t_pdf):
        result = INGESTER.ingest(sparse_1098_t_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        assert (
            paths.get("forms_1098_t[0].box1_payments_received")
            == "4500.00"
        )
        assert (
            paths.get("forms_1098_t[0].institution_name")
            == "Community College"
        )
        # Unfilled boxes must NOT appear in the partial
        assert "forms_1098_t[0].box5_scholarships" not in paths
        assert "forms_1098_t[0].institution_ein" not in paths

    def test_sparse_document_kind(self, sparse_1098_t_pdf):
        result = INGESTER.ingest(sparse_1098_t_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1098_T


# ---------------------------------------------------------------------------
# Real IRS f1098t.pdf template tests
# ---------------------------------------------------------------------------

_REAL_1098_T_PDF: Path = (
    Path(__file__).resolve().parents[1]
    / "reference"
    / "irs_forms"
    / "f1098t_ty2024.pdf"
)


@pytest.fixture
def real_1098_t(tmp_path: Path) -> Path:
    import shutil

    dst = tmp_path / "1098-t.pdf"
    shutil.copy(_REAL_1098_T_PDF, dst)
    return dst


class TestReal1098TAcroForm:
    def test_real_pdf_exists(self) -> None:
        assert _REAL_1098_T_PDF.exists()

    def test_real_pdf_is_acroform(self, real_1098_t: Path) -> None:
        assert INGESTER.can_handle(real_1098_t) is True

    def test_real_pdf_ingest_succeeds(self, real_1098_t: Path) -> None:
        result = INGESTER.ingest(real_1098_t)
        assert result.success, result.error
        assert result.partial.document_kind == DocumentKind.FORM_1098_T

    def test_field_map_has_real_widget_names(self) -> None:
        reader = pypdf.PdfReader(str(_REAL_1098_T_PDF))
        actual = set(reader.get_fields().keys())
        real_keys = [
            k for k in FORM_1098_T_FIELD_MAP if k.startswith("topmostSubform")
        ]
        assert real_keys, "FORM_1098_T_FIELD_MAP missing real IRS widget keys"
        missing = [k for k in real_keys if k not in actual]
        assert not missing, missing[:5]
