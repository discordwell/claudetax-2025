"""Tests for the 1099-G pypdf AcroForm ingester.

The ingester uses SYNTHETIC field names (documented as a follow-up to replace
with real IRS AcroForm field names). These tests exercise the synthetic map
against a reportlab-generated fillable PDF fixture to prove the path-rewriting
wiring works end-to-end.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pypdf
import pytest
from reportlab.pdfgen import canvas

from skill.scripts.ingest._1099_g_acroform import (
    FORM_1099_G_FIELD_MAP,
    INGESTER,
)
from skill.scripts.ingest._classifier import classify_by_filename
from skill.scripts.ingest._pipeline import DocumentKind, Ingester


# ---------------------------------------------------------------------------
# Synthetic fillable PDF helper (inlined from test_ingest_1099_nec_acroform.py)
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
    "payer_name": "State of California EDD",
    "payer_tin": "94-1234567",
    "box1_unemployment_compensation": "8400.00",
    "box2_state_or_local_income_tax_refund": "325.50",
    "box2_tax_year": "2024",
    "box4_federal_income_tax_withheld": "840.00",
    "box5_rtaa_payments": "0.00",
    "box6_taxable_grants": "1500.00",
    "box7_agricultural_payments": "0.00",
}


@pytest.fixture
def fake_1099_g_pdf(tmp_path) -> Path:
    # Filename contains "1099-G" so the classifier resolves it to FORM_1099_G
    p = tmp_path / "1099-G_edd.pdf"
    _make_acroform_pdf(p, _FULL_FIELDS)
    return p


@pytest.fixture
def sparse_unemployment_pdf(tmp_path) -> Path:
    """A 1099-G for a plain unemployment recipient — box 1 and payer only."""
    p = tmp_path / "1099-G_unemployment.pdf"
    _make_acroform_pdf(
        p,
        {
            "payer_name": "NYS Department of Labor",
            "box1_unemployment_compensation": "12500.00",
        },
    )
    return p


@pytest.fixture
def state_refund_pdf(tmp_path) -> Path:
    """A 1099-G issued purely for a state tax refund (no unemployment)."""
    p = tmp_path / "1099-g_state_refund.pdf"
    _make_acroform_pdf(
        p,
        {
            "payer_name": "Franchise Tax Board",
            "payer_tin": "68-0204061",
            "box2_state_or_local_income_tax_refund": "742.00",
            "box2_tax_year": "2024",
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
        assert INGESTER.name == "1099_g_acroform"
        assert INGESTER.tier == 1

    def test_field_map_registered_for_1099_g(self):
        assert DocumentKind.FORM_1099_G in INGESTER.field_map
        mapping = INGESTER.field_map[DocumentKind.FORM_1099_G]
        assert (
            mapping["box1_unemployment_compensation"]
            == "forms_1099_g[0].box1_unemployment_compensation"
        )
        assert (
            mapping["box2_state_or_local_income_tax_refund"]
            == "forms_1099_g[0].box2_state_or_local_income_tax_refund"
        )

    def test_field_map_covers_required_boxes(self):
        required = {
            "payer_name",
            "payer_tin",
            "box1_unemployment_compensation",
            "box2_state_or_local_income_tax_refund",
            "box2_tax_year",
            "box4_federal_income_tax_withheld",
            "box5_rtaa_payments",
            "box6_taxable_grants",
            "box7_agricultural_payments",
        }
        # Wave 6 adds real IRS AcroForm widget names alongside the synthetic
        # fixture keys, so the map is a superset of ``required`` rather than
        # equal to it. Use ``issubset`` to match the other 1099 ingester tests.
        assert required.issubset(set(FORM_1099_G_FIELD_MAP.keys()))

    def test_all_map_targets_under_forms_1099_g(self):
        for canonical in FORM_1099_G_FIELD_MAP.values():
            assert canonical.startswith("forms_1099_g[0].")


class TestClassifierRouting:
    def test_filename_routes_to_1099_g_ingester(self, fake_1099_g_pdf):
        """A file named ``1099-G*.pdf`` must classify as FORM_1099_G so that
        the cascade picks up this ingester's field map."""
        kind = classify_by_filename(fake_1099_g_pdf)
        assert kind == DocumentKind.FORM_1099_G
        assert kind in INGESTER.field_map

    def test_lowercase_filename_routes(self, state_refund_pdf):
        assert classify_by_filename(state_refund_pdf) == DocumentKind.FORM_1099_G


class TestCanHandle:
    def test_can_handle_fake_1099_g(self, fake_1099_g_pdf):
        assert INGESTER.can_handle(fake_1099_g_pdf)

    def test_cannot_handle_non_pdf(self, tmp_path):
        p = tmp_path / "note.txt"
        p.write_bytes(b"hello")
        assert not INGESTER.can_handle(p)


class TestIngestFullForm:
    def test_ingest_succeeds(self, fake_1099_g_pdf):
        result = INGESTER.ingest(fake_1099_g_pdf)
        assert result.success
        assert result.error is None

    def test_document_kind_is_1099_g(self, fake_1099_g_pdf):
        result = INGESTER.ingest(fake_1099_g_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1099_G

    def test_every_mapped_field_lands_on_canonical_path(self, fake_1099_g_pdf):
        result = INGESTER.ingest(fake_1099_g_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        expected_values = {
            "forms_1099_g[0].payer_name": "State of California EDD",
            "forms_1099_g[0].payer_tin": "94-1234567",
            "forms_1099_g[0].box1_unemployment_compensation": "8400.00",
            "forms_1099_g[0].box2_state_or_local_income_tax_refund": "325.50",
            "forms_1099_g[0].box2_tax_year": "2024",
            "forms_1099_g[0].box4_federal_income_tax_withheld": "840.00",
            "forms_1099_g[0].box5_rtaa_payments": "0.00",
            "forms_1099_g[0].box6_taxable_grants": "1500.00",
            "forms_1099_g[0].box7_agricultural_payments": "0.00",
        }
        for canonical, expected in expected_values.items():
            assert paths.get(canonical) == expected, (
                f"missing or wrong value for {canonical}: got {paths.get(canonical)!r}"
            )

    def test_payer_name_and_tin_extraction(self, fake_1099_g_pdf):
        """Payer identity fields are load-bearing for the downstream UX —
        call them out with a dedicated assertion."""
        result = INGESTER.ingest(fake_1099_g_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        assert paths["forms_1099_g[0].payer_name"] == "State of California EDD"
        assert paths["forms_1099_g[0].payer_tin"] == "94-1234567"

    def test_box1_unemployment_parses_as_decimal(self, fake_1099_g_pdf):
        """The raw widget value is a string, but it must round-trip cleanly
        through ``Decimal`` so downstream ``Money`` coercion on Form1099G
        succeeds without quantization surprises."""
        result = INGESTER.ingest(fake_1099_g_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        raw = paths["forms_1099_g[0].box1_unemployment_compensation"]
        assert isinstance(raw, str)
        as_decimal = Decimal(raw)
        assert as_decimal == Decimal("8400.00")
        # And the taxable-grants string likewise
        grants = Decimal(paths["forms_1099_g[0].box6_taxable_grants"])
        assert grants == Decimal("1500.00")

    def test_no_raw_fallback_paths_for_full_form(self, fake_1099_g_pdf):
        """When every field is mapped, none should drop to _acroform_raw."""
        result = INGESTER.ingest(fake_1099_g_pdf)
        for f in result.partial.fields:
            assert not f.path.startswith("_acroform_raw."), (
                f"unmapped field leaked to raw: {f.path}"
            )

    def test_fields_carry_full_confidence(self, fake_1099_g_pdf):
        result = INGESTER.ingest(fake_1099_g_pdf)
        assert result.partial.fields  # non-empty
        for f in result.partial.fields:
            assert f.confidence == 1.0


class TestIngestSparseUnemployment:
    def test_sparse_unemployment_ingest_succeeds(self, sparse_unemployment_pdf):
        """A 1099-G with only payer name + box 1 (common for pure
        unemployment recipients) should still be usable."""
        result = INGESTER.ingest(sparse_unemployment_pdf)
        assert result.success
        assert result.is_usable

    def test_sparse_unemployment_reports_what_is_filled(
        self, sparse_unemployment_pdf
    ):
        result = INGESTER.ingest(sparse_unemployment_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        assert (
            paths.get("forms_1099_g[0].box1_unemployment_compensation")
            == "12500.00"
        )
        assert paths.get("forms_1099_g[0].payer_name") == "NYS Department of Labor"
        # Unfilled boxes must NOT appear in the partial
        assert (
            "forms_1099_g[0].box2_state_or_local_income_tax_refund" not in paths
        )
        assert "forms_1099_g[0].box4_federal_income_tax_withheld" not in paths
        assert "forms_1099_g[0].payer_tin" not in paths

    def test_sparse_unemployment_document_kind(self, sparse_unemployment_pdf):
        result = INGESTER.ingest(sparse_unemployment_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1099_G


class TestIngestStateRefundOnly:
    def test_state_refund_ingest_succeeds(self, state_refund_pdf):
        """A 1099-G covering only a state tax refund (box 2 + box 3 tax year)
        should succeed and land on the refund canonical paths."""
        result = INGESTER.ingest(state_refund_pdf)
        assert result.success
        paths = {f.path: f.value for f in result.partial.fields}
        assert (
            paths.get("forms_1099_g[0].box2_state_or_local_income_tax_refund")
            == "742.00"
        )
        assert paths.get("forms_1099_g[0].box2_tax_year") == "2024"
        assert paths.get("forms_1099_g[0].payer_name") == "Franchise Tax Board"
        # Unemployment box is empty — must not appear
        assert "forms_1099_g[0].box1_unemployment_compensation" not in paths


# ---------------------------------------------------------------------------
# Real IRS f1099g.pdf template tests (wave 6)
# ---------------------------------------------------------------------------


_REAL_1099_G_PDF: Path = (
    Path(__file__).resolve().parents[1]
    / "reference"
    / "irs_forms"
    / "f1099g_ty2024.pdf"
)


@pytest.fixture
def real_1099_g(tmp_path: Path) -> Path:
    import shutil

    dst = tmp_path / "1099-g.pdf"
    shutil.copy(_REAL_1099_G_PDF, dst)
    return dst


class TestReal1099GAcroForm:
    def test_real_pdf_exists(self) -> None:
        assert _REAL_1099_G_PDF.exists()

    def test_real_pdf_is_acroform(self, real_1099_g: Path) -> None:
        assert INGESTER.can_handle(real_1099_g) is True

    def test_real_pdf_ingest_succeeds(self, real_1099_g: Path) -> None:
        result = INGESTER.ingest(real_1099_g)
        assert result.success, result.error
        assert result.partial.document_kind == DocumentKind.FORM_1099_G

    def test_field_map_has_real_widget_names(self) -> None:
        reader = pypdf.PdfReader(str(_REAL_1099_G_PDF))
        actual = set(reader.get_fields().keys())
        real_keys = [
            k for k in FORM_1099_G_FIELD_MAP if k.startswith("topmostSubform")
        ]
        assert real_keys, "FORM_1099_G_FIELD_MAP missing real IRS widget keys"
        missing = [k for k in real_keys if k not in actual]
        assert not missing, missing[:5]
