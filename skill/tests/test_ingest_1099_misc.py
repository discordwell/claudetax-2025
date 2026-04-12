"""Tests for the 1099-MISC pypdf AcroForm ingester.

The ingester supports BOTH synthetic field names (for test fixtures) and real
IRS AcroForm widget names from the official ``f1099msc.pdf``. These tests
exercise the synthetic map against a reportlab-generated fillable PDF fixture
to prove the path-rewriting wiring works end-to-end, and validate the real
widget names against the archived IRS PDF.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pypdf
import pytest
from reportlab.pdfgen import canvas

from skill.scripts.ingest._1099_misc_acroform import (
    FORM_1099_MISC_FIELD_MAP,
    INGESTER,
)
from skill.scripts.ingest._classifier import classify_by_filename
from skill.scripts.ingest._pipeline import DocumentKind, Ingester


# ---------------------------------------------------------------------------
# Synthetic fillable PDF helper
# ---------------------------------------------------------------------------


def _make_acroform_pdf(path: Path, fields: dict[str, str]) -> None:
    """Write a minimal AcroForm PDF with the given text fields and values."""
    c = canvas.Canvas(str(path))
    form = c.acroForm
    y = 720
    for name, _value in fields.items():
        c.drawString(50, y + 20, name)
        form.textfield(
            name=name,
            x=230,
            y=y,
            width=220,
            height=14,
            borderStyle="solid",
        )
        y -= 28
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
    "payer_name": "ACME Property Management LLC",
    "payer_tin": "12-3456789",
    "recipient_tin": "987-65-4321",
    "box1_rents": "24000.00",
    "box2_royalties": "5000.00",
    "box3_other_income": "1500.00",
    "box4_federal_tax_withheld": "3000.00",
    "box5_fishing_boat_proceeds": "0.00",
    "box6_medical_healthcare_payments": "8500.00",
    "box7_payer_direct_sales": "false",
    "box8_substitute_payments": "200.00",
    "box9_crop_insurance": "0.00",
    "box10_gross_proceeds_attorney": "15000.00",
    "box11_fish_purchased_for_resale": "0.00",
    "box12_section_409a_deferrals": "0.00",
    "box14_nonqualified_deferred_compensation": "0.00",
    "box15_state_tax_withheld": "1200.00",
}


@pytest.fixture
def fake_1099_misc_pdf(tmp_path) -> Path:
    p = tmp_path / "1099-MISC_property.pdf"
    _make_acroform_pdf(p, _FULL_FIELDS)
    return p


@pytest.fixture
def sparse_1099_misc_pdf(tmp_path) -> Path:
    """A 1099-MISC with only rents and payer info (common landlord scenario)."""
    p = tmp_path / "1099-MISC_sparse.pdf"
    _make_acroform_pdf(
        p,
        {
            "payer_name": "Rental Agency Inc",
            "box1_rents": "18000.00",
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
        assert INGESTER.name == "1099_misc_acroform"
        assert INGESTER.tier == 1

    def test_field_map_registered_for_1099_misc(self):
        assert DocumentKind.FORM_1099_MISC in INGESTER.field_map
        mapping = INGESTER.field_map[DocumentKind.FORM_1099_MISC]
        assert mapping["box1_rents"] == "forms_1099_misc[0].box1_rents"
        assert (
            mapping["box4_federal_tax_withheld"]
            == "forms_1099_misc[0].box4_federal_tax_withheld"
        )

    def test_field_map_covers_required_boxes(self):
        required = {
            "payer_name",
            "payer_tin",
            "recipient_tin",
            "box1_rents",
            "box2_royalties",
            "box3_other_income",
            "box4_federal_tax_withheld",
            "box6_medical_healthcare_payments",
            "box7_payer_direct_sales",
            "box9_crop_insurance",
            "box10_gross_proceeds_attorney",
        }
        assert required.issubset(set(FORM_1099_MISC_FIELD_MAP.keys()))

    def test_all_map_targets_under_forms_1099_misc(self):
        for canonical in FORM_1099_MISC_FIELD_MAP.values():
            assert canonical.startswith("forms_1099_misc[0].")

    def test_map_covers_key_model_fields(self):
        """Key Form1099MISC fields must have a mapping entry."""
        from skill.scripts.models import Form1099MISC

        # Fields that MUST be mapped (identity + all monetary boxes)
        must_map = {
            "payer_name",
            "payer_tin",
            "recipient_tin",
            "box1_rents",
            "box2_royalties",
            "box3_other_income",
            "box4_federal_tax_withheld",
            "box5_fishing_boat_proceeds",
            "box6_medical_healthcare_payments",
            "box7_payer_direct_sales",
            "box8_substitute_payments",
            "box9_crop_insurance",
            "box10_gross_proceeds_attorney",
            "box11_fish_purchased_for_resale",
            "box12_section_409a_deferrals",
            "box14_nonqualified_deferred_compensation",
            "box15_state_tax_withheld",
        }
        mapped_leaves = {
            canonical.removeprefix("forms_1099_misc[0].")
            for canonical in FORM_1099_MISC_FIELD_MAP.values()
        }
        missing = must_map - mapped_leaves
        assert not missing, f"Form1099MISC fields not mapped: {missing}"


class TestClassifierRouting:
    def test_classifier_routes_1099_misc_filename(self, tmp_path):
        p = tmp_path / "1099-MISC.pdf"
        p.write_bytes(b"%PDF-1.4\n")
        assert classify_by_filename(p) == DocumentKind.FORM_1099_MISC

    def test_classifier_routes_lowercase(self, tmp_path):
        p = tmp_path / "1099misc_landlord.pdf"
        p.write_bytes(b"%PDF-1.4\n")
        assert classify_by_filename(p) == DocumentKind.FORM_1099_MISC


class TestCanHandle:
    def test_can_handle_fake_1099_misc(self, fake_1099_misc_pdf):
        assert INGESTER.can_handle(fake_1099_misc_pdf)

    def test_cannot_handle_non_pdf(self, tmp_path):
        p = tmp_path / "note.txt"
        p.write_bytes(b"hello")
        assert not INGESTER.can_handle(p)

    def test_cannot_handle_missing_file(self, tmp_path):
        p = tmp_path / "does_not_exist.pdf"
        assert not INGESTER.can_handle(p)


class TestIngestFullForm:
    def test_ingest_succeeds(self, fake_1099_misc_pdf):
        result = INGESTER.ingest(fake_1099_misc_pdf)
        assert result.success
        assert result.error is None

    def test_document_kind_is_1099_misc(self, fake_1099_misc_pdf):
        result = INGESTER.ingest(fake_1099_misc_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1099_MISC

    def test_round_trip_every_box_populated(self, fake_1099_misc_pdf):
        """Synthesize -> ingest -> assert every mapped box lands on its path."""
        result = INGESTER.ingest(fake_1099_misc_pdf)
        assert result.success
        paths = {f.path: f.value for f in result.partial.fields}

        assert paths.get("forms_1099_misc[0].payer_name") == "ACME Property Management LLC"
        assert paths.get("forms_1099_misc[0].payer_tin") == "12-3456789"
        assert paths.get("forms_1099_misc[0].recipient_tin") == "987-65-4321"
        assert paths.get("forms_1099_misc[0].box1_rents") == "24000.00"
        assert paths.get("forms_1099_misc[0].box2_royalties") == "5000.00"
        assert paths.get("forms_1099_misc[0].box3_other_income") == "1500.00"
        assert paths.get("forms_1099_misc[0].box4_federal_tax_withheld") == "3000.00"
        assert (
            paths.get("forms_1099_misc[0].box6_medical_healthcare_payments")
            == "8500.00"
        )
        assert (
            paths.get("forms_1099_misc[0].box10_gross_proceeds_attorney")
            == "15000.00"
        )
        assert paths.get("forms_1099_misc[0].box15_state_tax_withheld") == "1200.00"

    def test_no_raw_fallback_paths_for_full_form(self, fake_1099_misc_pdf):
        """When every field is mapped, none should drop to _acroform_raw."""
        result = INGESTER.ingest(fake_1099_misc_pdf)
        for f in result.partial.fields:
            assert not f.path.startswith("_acroform_raw."), (
                f"unmapped field leaked to raw: {f.path}"
            )

    def test_fields_carry_full_confidence(self, fake_1099_misc_pdf):
        result = INGESTER.ingest(fake_1099_misc_pdf)
        assert result.partial.fields
        for f in result.partial.fields:
            assert f.confidence == 1.0

    def test_result_is_usable(self, fake_1099_misc_pdf):
        result = INGESTER.ingest(fake_1099_misc_pdf)
        assert result.is_usable

    def test_all_money_boxes_parse_as_decimal(self, fake_1099_misc_pdf):
        """Every money box must be Decimal-parseable."""
        result = INGESTER.ingest(fake_1099_misc_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        money_paths = [
            "forms_1099_misc[0].box1_rents",
            "forms_1099_misc[0].box2_royalties",
            "forms_1099_misc[0].box3_other_income",
            "forms_1099_misc[0].box4_federal_tax_withheld",
            "forms_1099_misc[0].box6_medical_healthcare_payments",
            "forms_1099_misc[0].box10_gross_proceeds_attorney",
            "forms_1099_misc[0].box15_state_tax_withheld",
        ]
        for path in money_paths:
            raw = paths.get(path)
            assert raw is not None, f"missing money path: {path}"
            parsed = Decimal(raw)
            assert parsed >= 0, f"{path} should be non-negative, got {parsed}"


class TestIngestSparseForm:
    def test_sparse_ingest_succeeds(self, sparse_1099_misc_pdf):
        """A 1099-MISC with only box 1 + payer name should still be usable."""
        result = INGESTER.ingest(sparse_1099_misc_pdf)
        assert result.success
        assert result.is_usable

    def test_sparse_reports_only_filled_boxes(self, sparse_1099_misc_pdf):
        result = INGESTER.ingest(sparse_1099_misc_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        assert paths.get("forms_1099_misc[0].box1_rents") == "18000.00"
        assert paths.get("forms_1099_misc[0].payer_name") == "Rental Agency Inc"
        # Unfilled boxes must NOT appear in the partial
        assert "forms_1099_misc[0].box2_royalties" not in paths
        assert "forms_1099_misc[0].box4_federal_tax_withheld" not in paths

    def test_sparse_document_kind(self, sparse_1099_misc_pdf):
        result = INGESTER.ingest(sparse_1099_misc_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1099_MISC


# ---------------------------------------------------------------------------
# Real IRS f1099msc.pdf template tests (wave 8)
# ---------------------------------------------------------------------------


_REAL_1099_MISC_PDF: Path = (
    Path(__file__).resolve().parents[1]
    / "reference"
    / "irs_forms"
    / "f1099msc_ty2025.pdf"
)


@pytest.fixture
def real_1099_misc(tmp_path: Path) -> Path:
    import shutil

    dst = tmp_path / "1099-misc.pdf"
    shutil.copy(_REAL_1099_MISC_PDF, dst)
    return dst


class TestReal1099MISCAcroForm:
    """Wave 8: the ingester must read the real IRS f1099msc.pdf template
    without crashing and must have real IRS widget names in its field map."""

    def test_real_pdf_exists(self) -> None:
        assert _REAL_1099_MISC_PDF.exists()

    def test_real_pdf_is_acroform(self, real_1099_misc: Path) -> None:
        assert INGESTER.can_handle(real_1099_misc) is True

    def test_real_pdf_ingest_succeeds(self, real_1099_misc: Path) -> None:
        result = INGESTER.ingest(real_1099_misc)
        assert result.success, result.error
        assert result.partial.document_kind == DocumentKind.FORM_1099_MISC

    def test_field_map_has_real_widget_names(self) -> None:
        reader = pypdf.PdfReader(str(_REAL_1099_MISC_PDF))
        actual = set(reader.get_fields().keys())
        real_keys = [
            k for k in FORM_1099_MISC_FIELD_MAP if k.startswith("topmostSubform")
        ]
        assert real_keys, "FORM_1099_MISC_FIELD_MAP missing real IRS widget keys"
        missing = [k for k in real_keys if k not in actual]
        assert not missing, (
            f"real 1099-MISC widget keys not on f1099msc.pdf: {missing[:5]}"
        )


class TestCanonicalReturnIntegration:
    """Verify the Form1099MISC model is wired on CanonicalReturn."""

    def test_forms_1099_misc_field_exists(self):
        from skill.scripts.models import CanonicalReturn

        assert "forms_1099_misc" in CanonicalReturn.model_fields

    def test_model_accepts_form_1099_misc(self):
        from skill.scripts.models import Form1099MISC

        f = Form1099MISC(
            payer_name="Test",
            box1_rents=Decimal("10000"),
            box4_federal_tax_withheld=Decimal("1000"),
        )
        assert f.box1_rents == Decimal("10000")
