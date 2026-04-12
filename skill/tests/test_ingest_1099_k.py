"""Tests for the 1099-K pypdf AcroForm ingester.

The ingester supports BOTH synthetic field names (for test fixtures) and real
IRS AcroForm widget names from the official ``f1099k.pdf``. These tests
exercise the synthetic map against a reportlab-generated fillable PDF fixture
to prove the path-rewriting wiring works end-to-end, and validate the real
widget names against the archived IRS PDF.

1099-K TY2025 note: the reporting threshold is $5,000 for third-party
network transactions (payment card transactions have no threshold).
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pypdf
import pytest
from reportlab.pdfgen import canvas

from skill.scripts.ingest._1099_k_acroform import (
    FORM_1099_K_FIELD_MAP,
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
    "payer_name": "Stripe Payments Company",
    "payer_tin": "46-1234567",
    "settlement_entity_name": "Stripe Inc",
    "box1a_gross_amount": "52500.00",
    "box1b_card_not_present": "48000.00",
    "box2_merchant_category_code": "5734",
    "box3_number_of_payment_transactions": "1247",
    "box4_federal_tax_withheld": "0.00",
    "box5a_january": "3200.00",
    "box5b_february": "3800.00",
    "box5c_march": "4100.00",
    "box5d_april": "4500.00",
    "box5e_may": "4800.00",
    "box5f_june": "5200.00",
    "box5g_july": "5500.00",
    "box5h_august": "4900.00",
    "box5i_september": "4300.00",
    "box5j_october": "4700.00",
    "box5k_november": "5100.00",
    "box5l_december": "2400.00",
}


@pytest.fixture
def fake_1099_k_pdf(tmp_path) -> Path:
    p = tmp_path / "1099-K_stripe.pdf"
    _make_acroform_pdf(p, _FULL_FIELDS)
    return p


@pytest.fixture
def sparse_1099_k_pdf(tmp_path) -> Path:
    """A 1099-K with only box 1a and payer info (common for gig workers)."""
    p = tmp_path / "1099-K_sparse.pdf"
    _make_acroform_pdf(
        p,
        {
            "payer_name": "PayPal Holdings",
            "box1a_gross_amount": "8500.00",
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
        assert INGESTER.name == "1099_k_acroform"
        assert INGESTER.tier == 1

    def test_field_map_registered_for_1099_k(self):
        assert DocumentKind.FORM_1099_K in INGESTER.field_map
        mapping = INGESTER.field_map[DocumentKind.FORM_1099_K]
        assert mapping["box1a_gross_amount"] == "forms_1099_k[0].box1a_gross_amount"
        assert (
            mapping["box4_federal_tax_withheld"]
            == "forms_1099_k[0].box4_federal_tax_withheld"
        )

    def test_field_map_covers_required_boxes(self):
        required = {
            "payer_name",
            "payer_tin",
            "box1a_gross_amount",
            "box1b_card_not_present",
            "box2_merchant_category_code",
            "box4_federal_tax_withheld",
            "box5a_january",
            "box5b_february",
            "box5c_march",
            "box5d_april",
            "box5e_may",
            "box5f_june",
            "box5g_july",
            "box5h_august",
            "box5i_september",
            "box5j_october",
            "box5k_november",
            "box5l_december",
        }
        assert required.issubset(set(FORM_1099_K_FIELD_MAP.keys()))

    def test_all_map_targets_under_forms_1099_k(self):
        for canonical in FORM_1099_K_FIELD_MAP.values():
            assert canonical.startswith("forms_1099_k[0].")

    def test_map_covers_key_model_fields(self):
        """Key Form1099K fields must have a mapping entry."""
        must_map = {
            "payer_name",
            "payer_tin",
            "settlement_entity_name",
            "box1a_gross_amount",
            "box1b_card_not_present",
            "box2_merchant_category_code",
            "box3_number_of_payment_transactions",
            "box4_federal_tax_withheld",
            "box5a_january",
            "box5b_february",
            "box5c_march",
            "box5d_april",
            "box5e_may",
            "box5f_june",
            "box5g_july",
            "box5h_august",
            "box5i_september",
            "box5j_october",
            "box5k_november",
            "box5l_december",
        }
        mapped_leaves = {
            canonical.removeprefix("forms_1099_k[0].")
            for canonical in FORM_1099_K_FIELD_MAP.values()
        }
        missing = must_map - mapped_leaves
        assert not missing, f"Form1099K fields not mapped: {missing}"


class TestClassifierRouting:
    def test_classifier_routes_1099_k_filename(self, tmp_path):
        p = tmp_path / "1099-K.pdf"
        p.write_bytes(b"%PDF-1.4\n")
        assert classify_by_filename(p) == DocumentKind.FORM_1099_K

    def test_classifier_routes_lowercase(self, tmp_path):
        p = tmp_path / "1099k_stripe.pdf"
        p.write_bytes(b"%PDF-1.4\n")
        assert classify_by_filename(p) == DocumentKind.FORM_1099_K


class TestCanHandle:
    def test_can_handle_fake_1099_k(self, fake_1099_k_pdf):
        assert INGESTER.can_handle(fake_1099_k_pdf)

    def test_cannot_handle_non_pdf(self, tmp_path):
        p = tmp_path / "note.txt"
        p.write_bytes(b"hello")
        assert not INGESTER.can_handle(p)

    def test_cannot_handle_missing_file(self, tmp_path):
        p = tmp_path / "does_not_exist.pdf"
        assert not INGESTER.can_handle(p)


class TestIngestFullForm:
    def test_ingest_succeeds(self, fake_1099_k_pdf):
        result = INGESTER.ingest(fake_1099_k_pdf)
        assert result.success
        assert result.error is None

    def test_document_kind_is_1099_k(self, fake_1099_k_pdf):
        result = INGESTER.ingest(fake_1099_k_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1099_K

    def test_round_trip_every_box_populated(self, fake_1099_k_pdf):
        """Synthesize -> ingest -> assert every mapped box lands on its path."""
        result = INGESTER.ingest(fake_1099_k_pdf)
        assert result.success
        paths = {f.path: f.value for f in result.partial.fields}

        assert paths.get("forms_1099_k[0].payer_name") == "Stripe Payments Company"
        assert paths.get("forms_1099_k[0].payer_tin") == "46-1234567"
        assert paths.get("forms_1099_k[0].settlement_entity_name") == "Stripe Inc"
        assert paths.get("forms_1099_k[0].box1a_gross_amount") == "52500.00"
        assert paths.get("forms_1099_k[0].box1b_card_not_present") == "48000.00"
        assert paths.get("forms_1099_k[0].box2_merchant_category_code") == "5734"
        assert (
            paths.get("forms_1099_k[0].box3_number_of_payment_transactions")
            == "1247"
        )
        assert paths.get("forms_1099_k[0].box4_federal_tax_withheld") == "0.00"
        assert paths.get("forms_1099_k[0].box5a_january") == "3200.00"
        assert paths.get("forms_1099_k[0].box5b_february") == "3800.00"
        assert paths.get("forms_1099_k[0].box5c_march") == "4100.00"
        assert paths.get("forms_1099_k[0].box5d_april") == "4500.00"
        assert paths.get("forms_1099_k[0].box5e_may") == "4800.00"
        assert paths.get("forms_1099_k[0].box5f_june") == "5200.00"
        assert paths.get("forms_1099_k[0].box5g_july") == "5500.00"
        assert paths.get("forms_1099_k[0].box5h_august") == "4900.00"
        assert paths.get("forms_1099_k[0].box5i_september") == "4300.00"
        assert paths.get("forms_1099_k[0].box5j_october") == "4700.00"
        assert paths.get("forms_1099_k[0].box5k_november") == "5100.00"
        assert paths.get("forms_1099_k[0].box5l_december") == "2400.00"

    def test_monthly_amounts_sum_to_gross(self, fake_1099_k_pdf):
        """Arithmetic invariant: sum of monthly boxes should equal box 1a."""
        result = INGESTER.ingest(fake_1099_k_pdf)
        paths = {f.path: f.value for f in result.partial.fields}

        monthly_total = sum(
            Decimal(paths[f"forms_1099_k[0].box5{m}_{'january february march april may june july august september october november december'.split()[i]}"])
            for i, m in enumerate("abcdefghijkl")
        )
        gross = Decimal(paths["forms_1099_k[0].box1a_gross_amount"])
        assert monthly_total == gross

    def test_no_raw_fallback_paths_for_full_form(self, fake_1099_k_pdf):
        """When every field is mapped, none should drop to _acroform_raw."""
        result = INGESTER.ingest(fake_1099_k_pdf)
        for f in result.partial.fields:
            assert not f.path.startswith("_acroform_raw."), (
                f"unmapped field leaked to raw: {f.path}"
            )

    def test_fields_carry_full_confidence(self, fake_1099_k_pdf):
        result = INGESTER.ingest(fake_1099_k_pdf)
        assert result.partial.fields
        for f in result.partial.fields:
            assert f.confidence == 1.0

    def test_result_is_usable(self, fake_1099_k_pdf):
        result = INGESTER.ingest(fake_1099_k_pdf)
        assert result.is_usable

    def test_all_money_boxes_parse_as_decimal(self, fake_1099_k_pdf):
        """Every money box must be Decimal-parseable."""
        result = INGESTER.ingest(fake_1099_k_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        money_paths = [
            "forms_1099_k[0].box1a_gross_amount",
            "forms_1099_k[0].box1b_card_not_present",
            "forms_1099_k[0].box4_federal_tax_withheld",
            "forms_1099_k[0].box5a_january",
            "forms_1099_k[0].box5f_june",
            "forms_1099_k[0].box5l_december",
        ]
        for path in money_paths:
            raw = paths.get(path)
            assert raw is not None, f"missing money path: {path}"
            parsed = Decimal(raw)
            assert parsed >= 0, f"{path} should be non-negative, got {parsed}"


class TestIngestSparseForm:
    def test_sparse_ingest_succeeds(self, sparse_1099_k_pdf):
        """A 1099-K with only box 1a + payer name should still be usable."""
        result = INGESTER.ingest(sparse_1099_k_pdf)
        assert result.success
        assert result.is_usable

    def test_sparse_reports_only_filled_boxes(self, sparse_1099_k_pdf):
        result = INGESTER.ingest(sparse_1099_k_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        assert paths.get("forms_1099_k[0].box1a_gross_amount") == "8500.00"
        assert paths.get("forms_1099_k[0].payer_name") == "PayPal Holdings"
        # Unfilled boxes must NOT appear in the partial
        assert "forms_1099_k[0].box1b_card_not_present" not in paths
        assert "forms_1099_k[0].box4_federal_tax_withheld" not in paths
        assert "forms_1099_k[0].box5a_january" not in paths

    def test_sparse_document_kind(self, sparse_1099_k_pdf):
        result = INGESTER.ingest(sparse_1099_k_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1099_K


# ---------------------------------------------------------------------------
# Real IRS f1099k.pdf template tests (wave 8)
# ---------------------------------------------------------------------------


_REAL_1099_K_PDF: Path = (
    Path(__file__).resolve().parents[1]
    / "reference"
    / "irs_forms"
    / "f1099k_ty2024.pdf"
)


@pytest.fixture
def real_1099_k(tmp_path: Path) -> Path:
    import shutil

    dst = tmp_path / "1099-k.pdf"
    shutil.copy(_REAL_1099_K_PDF, dst)
    return dst


class TestReal1099KAcroForm:
    """Wave 8: the ingester must read the real IRS f1099k.pdf template
    without crashing and must have real IRS widget names in its field map."""

    def test_real_pdf_exists(self) -> None:
        assert _REAL_1099_K_PDF.exists()

    def test_real_pdf_is_acroform(self, real_1099_k: Path) -> None:
        assert INGESTER.can_handle(real_1099_k) is True

    def test_real_pdf_ingest_succeeds(self, real_1099_k: Path) -> None:
        result = INGESTER.ingest(real_1099_k)
        assert result.success, result.error
        assert result.partial.document_kind == DocumentKind.FORM_1099_K

    def test_field_map_has_real_widget_names(self) -> None:
        reader = pypdf.PdfReader(str(_REAL_1099_K_PDF))
        actual = set(reader.get_fields().keys())
        real_keys = [
            k for k in FORM_1099_K_FIELD_MAP if k.startswith("topmostSubform")
        ]
        assert real_keys, "FORM_1099_K_FIELD_MAP missing real IRS widget keys"
        missing = [k for k in real_keys if k not in actual]
        assert not missing, (
            f"real 1099-K widget keys not on f1099k.pdf: {missing[:5]}"
        )


class TestCanonicalReturnIntegration:
    """Verify the Form1099K model is wired on CanonicalReturn."""

    def test_forms_1099_k_field_exists(self):
        from skill.scripts.models import CanonicalReturn

        assert "forms_1099_k" in CanonicalReturn.model_fields

    def test_model_accepts_form_1099_k(self):
        from skill.scripts.models import Form1099K

        f = Form1099K(
            payer_name="Test PSE",
            box1a_gross_amount=Decimal("10000"),
            box4_federal_tax_withheld=Decimal("0"),
        )
        assert f.box1a_gross_amount == Decimal("10000")

    def test_model_monthly_fields_exist(self):
        from skill.scripts.models import Form1099K

        months = [
            "box5a_january", "box5b_february", "box5c_march",
            "box5d_april", "box5e_may", "box5f_june",
            "box5g_july", "box5h_august", "box5i_september",
            "box5j_october", "box5k_november", "box5l_december",
        ]
        for month in months:
            assert month in Form1099K.model_fields, f"missing field: {month}"
