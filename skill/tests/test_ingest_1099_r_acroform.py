"""Tests for the 1099-R pypdf AcroForm ingester.

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

from skill.scripts.ingest._1099_r_acroform import (
    FORM_1099_R_FIELD_MAP,
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
    "payer_name": "Vanguard Fiduciary Trust Company",
    "payer_tin": "23-1945552",
    "box1_gross_distribution": "42000.00",
    "box2a_taxable_amount": "38500.00",
    "box2b_taxable_amount_not_determined": "true",
    "box2b_total_distribution": "false",
    "box4_federal_income_tax_withheld": "4200.50",
    "box7_distribution_codes": "7",
    "box7_ira_sep_simple": "true",
    "box9a_percent_total_distribution": "100",
    "box12_state_tax_withheld": "1050.00",
    "box13_state": "CA",
    "box16_state_distribution": "38500.00",
}


@pytest.fixture
def fake_1099_r_pdf(tmp_path) -> Path:
    # Filename contains "1099-R" so the classifier resolves it to FORM_1099_R
    p = tmp_path / "1099-R_vanguard.pdf"
    _make_acroform_pdf(p, _FULL_FIELDS)
    return p


@pytest.fixture
def sparse_1099_r_pdf(tmp_path) -> Path:
    """A 1099-R with only gross distribution + payer name (minimal IRA withdrawal)."""
    p = tmp_path / "1099-R_sparse.pdf"
    _make_acroform_pdf(
        p,
        {
            "payer_name": "Small IRA Custodian",
            "box1_gross_distribution": "2000.00",
            "box2a_taxable_amount": "2000.00",
            "box7_distribution_codes": "1",
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
        assert INGESTER.name == "1099_r_acroform"
        assert INGESTER.tier == 1

    def test_field_map_registered_for_1099_r(self):
        assert DocumentKind.FORM_1099_R in INGESTER.field_map
        mapping = INGESTER.field_map[DocumentKind.FORM_1099_R]
        # Sanity check a few entries
        assert (
            mapping["box1_gross_distribution"]
            == "forms_1099_r[0].box1_gross_distribution"
        )
        assert (
            mapping["box4_federal_income_tax_withheld"]
            == "forms_1099_r[0].box4_federal_income_tax_withheld"
        )

    def test_field_map_covers_required_boxes(self):
        required = {
            "payer_name",
            "payer_tin",
            "box1_gross_distribution",
            "box2a_taxable_amount",
            "box2b_taxable_amount_not_determined",
            "box2b_total_distribution",
            "box4_federal_income_tax_withheld",
            "box7_distribution_codes",
            "box7_ira_sep_simple",
            "box9a_percent_total_distribution",
            "box12_state_tax_withheld",
            "box13_state",
            "box16_state_distribution",
        }
        assert required.issubset(set(FORM_1099_R_FIELD_MAP.keys()))

    def test_all_map_targets_under_forms_1099_r(self):
        for canonical in FORM_1099_R_FIELD_MAP.values():
            assert canonical.startswith("forms_1099_r[0].")

    def test_map_covers_every_form_1099_r_model_field(self):
        """Every non-skipped field on Form1099R should have a mapping entry.

        If this test fails after a model change, either add the new field to
        the synthetic map (and the TODO footer) or explicitly skip it here.
        """
        expected_model_fields = {
            "payer_name",
            "payer_tin",
            "box1_gross_distribution",
            "box2a_taxable_amount",
            "box2b_taxable_amount_not_determined",
            "box2b_total_distribution",
            "box4_federal_income_tax_withheld",
            "box7_distribution_codes",
            "box7_ira_sep_simple",
            "box9a_percent_total_distribution",
            "box12_state_tax_withheld",
            "box13_state",
            "box16_state_distribution",
        }
        mapped_leaves = {
            canonical.removeprefix("forms_1099_r[0].")
            for canonical in FORM_1099_R_FIELD_MAP.values()
        }
        assert expected_model_fields.issubset(mapped_leaves)


class TestClassifierRouting:
    def test_classifier_routes_1099_r_filename(self, tmp_path):
        """A bare 1099-R filename should classify as FORM_1099_R."""
        p = tmp_path / "1099-R.pdf"
        p.write_bytes(b"%PDF-1.4\n")
        assert classify_by_filename(p) == DocumentKind.FORM_1099_R

    def test_classifier_routes_lowercase_1099r_filename(self, tmp_path):
        """Case-insensitive match: lowercase '1099-r' should also route."""
        p = tmp_path / "1099-r_client.pdf"
        p.write_bytes(b"%PDF-1.4\n")
        assert classify_by_filename(p) == DocumentKind.FORM_1099_R

    def test_ingester_ingest_sets_document_kind_from_filename(
        self, fake_1099_r_pdf
    ):
        """End-to-end: ingesting a '1099-R_*.pdf' file tags the partial
        with FORM_1099_R so the field_map is used."""
        result = INGESTER.ingest(fake_1099_r_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1099_R


class TestCanHandle:
    def test_can_handle_fake_1099_r(self, fake_1099_r_pdf):
        assert INGESTER.can_handle(fake_1099_r_pdf)

    def test_cannot_handle_non_pdf(self, tmp_path):
        p = tmp_path / "note.txt"
        p.write_bytes(b"hello")
        assert not INGESTER.can_handle(p)


class TestIngestFullForm:
    def test_ingest_succeeds(self, fake_1099_r_pdf):
        result = INGESTER.ingest(fake_1099_r_pdf)
        assert result.success
        assert result.error is None

    def test_document_kind_is_1099_r(self, fake_1099_r_pdf):
        result = INGESTER.ingest(fake_1099_r_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1099_R

    def test_values_flow_to_canonical_paths(self, fake_1099_r_pdf):
        result = INGESTER.ingest(fake_1099_r_pdf)
        paths = {f.path: f.value for f in result.partial.fields}

        # Spot-check the load-bearing boxes
        assert paths.get("forms_1099_r[0].box1_gross_distribution") == "42000.00"
        assert paths.get("forms_1099_r[0].box2a_taxable_amount") == "38500.00"
        assert (
            paths.get("forms_1099_r[0].box4_federal_income_tax_withheld")
            == "4200.50"
        )
        assert paths.get("forms_1099_r[0].box7_distribution_codes") == "7"
        assert paths.get("forms_1099_r[0].box9a_percent_total_distribution") == "100"
        assert paths.get("forms_1099_r[0].box12_state_tax_withheld") == "1050.00"
        assert paths.get("forms_1099_r[0].box13_state") == "CA"
        assert paths.get("forms_1099_r[0].box16_state_distribution") == "38500.00"

    def test_payer_identity_extracted(self, fake_1099_r_pdf):
        """Payer name and TIN text fields should land on their canonical paths."""
        result = INGESTER.ingest(fake_1099_r_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        assert (
            paths.get("forms_1099_r[0].payer_name")
            == "Vanguard Fiduciary Trust Company"
        )
        assert paths.get("forms_1099_r[0].payer_tin") == "23-1945552"

    def test_federal_withholding_parses_as_decimal(self, fake_1099_r_pdf):
        """Box 4 federal withholding string must round-trip to Decimal cleanly."""
        result = INGESTER.ingest(fake_1099_r_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        raw = paths.get("forms_1099_r[0].box4_federal_income_tax_withheld")
        assert raw is not None
        # The ingester emits the raw widget string; downstream rewriting
        # converts to Decimal. Prove the string is parseable without loss.
        assert Decimal(raw) == Decimal("4200.50")

    def test_checkbox_fields_emit_values(self, fake_1099_r_pdf):
        """Box 2b / box 7 checkboxes should make it through as string values."""
        result = INGESTER.ingest(fake_1099_r_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        assert (
            paths.get("forms_1099_r[0].box2b_taxable_amount_not_determined")
            == "true"
        )
        assert paths.get("forms_1099_r[0].box2b_total_distribution") == "false"
        assert paths.get("forms_1099_r[0].box7_ira_sep_simple") == "true"

    def test_no_raw_fallback_paths_for_full_form(self, fake_1099_r_pdf):
        """When every field is mapped, none should drop to _acroform_raw."""
        result = INGESTER.ingest(fake_1099_r_pdf)
        for f in result.partial.fields:
            assert not f.path.startswith("_acroform_raw."), (
                f"unmapped field leaked to raw: {f.path}"
            )

    def test_fields_carry_full_confidence(self, fake_1099_r_pdf):
        result = INGESTER.ingest(fake_1099_r_pdf)
        assert result.partial.fields  # non-empty
        for f in result.partial.fields:
            assert f.confidence == 1.0


class TestIngestSparseForm:
    def test_sparse_ingest_succeeds(self, sparse_1099_r_pdf):
        """A 1099-R with only payer + gross + taxable + code should still be usable."""
        result = INGESTER.ingest(sparse_1099_r_pdf)
        assert result.success
        assert result.is_usable

    def test_sparse_reports_what_is_filled(self, sparse_1099_r_pdf):
        result = INGESTER.ingest(sparse_1099_r_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        assert paths.get("forms_1099_r[0].box1_gross_distribution") == "2000.00"
        assert paths.get("forms_1099_r[0].box2a_taxable_amount") == "2000.00"
        assert paths.get("forms_1099_r[0].box7_distribution_codes") == "1"
        assert paths.get("forms_1099_r[0].payer_name") == "Small IRA Custodian"
        # Unfilled boxes must NOT appear in the partial
        assert "forms_1099_r[0].box4_federal_income_tax_withheld" not in paths
        assert "forms_1099_r[0].payer_tin" not in paths
        assert "forms_1099_r[0].box12_state_tax_withheld" not in paths

    def test_sparse_document_kind(self, sparse_1099_r_pdf):
        result = INGESTER.ingest(sparse_1099_r_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1099_R
