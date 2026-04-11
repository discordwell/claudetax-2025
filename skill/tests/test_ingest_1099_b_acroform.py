"""Tests for the 1099-B pypdf AcroForm ingester.

The ingester uses SYNTHETIC field names (documented as a follow-up to replace
with real IRS AcroForm field names). These tests exercise the synthetic map
against a reportlab-generated fillable PDF fixture to prove the path-rewriting
wiring works end-to-end.

Note on 1099-B semantics
------------------------
Real 1099-B forms have ONE transaction per form; a broker summary typically
bundles many transactions as attachments (often Form 8949 page supplements,
not AcroForm fields). For v1, this ingester supports ONE transaction per
1099-B form — see ``test_single_transaction_only_by_design`` and the module
docstring on ``_1099_b_acroform.py``. The escape hatch for multi-row broker
summaries is the 1099-B Azure Document Intelligence ingester.
"""
from __future__ import annotations

from pathlib import Path

import pypdf
import pytest
from reportlab.pdfgen import canvas

from skill.scripts.ingest._1099_b_acroform import (
    FORM_1099_B_FIELD_MAP,
    INGESTER,
)
from skill.scripts.ingest._pipeline import DocumentKind, Ingester


# ---------------------------------------------------------------------------
# Synthetic fillable PDF helper (mirrors test_ingest_1099_int_acroform.py)
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
    "broker_name": "Fidelity Brokerage Services LLC",
    "description": "100 sh AAPL",
    "date_sold": "2025-06-15",
    "proceeds": "19500.00",
    "cost_basis": "12000.00",
    "wash_sale_loss_disallowed": "0.00",
    "box4_federal_withholding": "150.00",
    "is_long_term_flag": "true",
}


@pytest.fixture
def fake_1099_b_pdf(tmp_path) -> Path:
    # Filename contains "1099-B" so the classifier resolves it to FORM_1099_B
    p = tmp_path / "1099-B_brokerage.pdf"
    _make_acroform_pdf(p, _FULL_FIELDS)
    return p


@pytest.fixture
def sparse_1099_b_pdf(tmp_path) -> Path:
    """A 1099-B with only broker + proceeds + basis filled."""
    p = tmp_path / "1099-B_sparse.pdf"
    _make_acroform_pdf(
        p,
        {
            "broker_name": "Vanguard Brokerage",
            "description": "50 sh VTI",
            "date_sold": "2025-11-02",
            "proceeds": "12000.00",
            "cost_basis": "10000.00",
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
        assert INGESTER.name == "1099_b_acroform"
        assert INGESTER.tier == 1

    def test_field_map_registered_for_1099_b(self):
        assert DocumentKind.FORM_1099_B in INGESTER.field_map
        mapping = INGESTER.field_map[DocumentKind.FORM_1099_B]
        assert mapping["broker_name"] == "forms_1099_b[0].broker_name"
        assert mapping["proceeds"] == "forms_1099_b[0].transactions[0].proceeds"
        assert mapping["cost_basis"] == "forms_1099_b[0].transactions[0].cost_basis"
        assert (
            mapping["box4_federal_withholding"]
            == "forms_1099_b[0].box4_federal_income_tax_withheld"
        )

    def test_field_map_covers_required_fields(self):
        required = {
            "broker_name",
            "description",
            "date_sold",
            "proceeds",
            "cost_basis",
            "wash_sale_loss_disallowed",
            "box4_federal_withholding",
            "is_long_term_flag",
        }
        assert required.issubset(set(FORM_1099_B_FIELD_MAP.keys()))

    def test_all_map_targets_under_forms_1099_b(self):
        for canonical in FORM_1099_B_FIELD_MAP.values():
            assert canonical.startswith("forms_1099_b[0]")

    def test_transaction_paths_target_index_zero(self):
        """All per-transaction fields must land on transactions[0].* by design."""
        transaction_keys = {
            "description",
            "date_sold",
            "proceeds",
            "cost_basis",
            "wash_sale_loss_disallowed",
            "is_long_term_flag",
        }
        for key in transaction_keys:
            assert FORM_1099_B_FIELD_MAP[key].startswith(
                "forms_1099_b[0].transactions[0]."
            ), f"{key} must target transactions[0].*"


class TestCanHandle:
    def test_can_handle_fake_1099_b(self, fake_1099_b_pdf):
        assert INGESTER.can_handle(fake_1099_b_pdf)

    def test_cannot_handle_non_pdf(self, tmp_path):
        p = tmp_path / "note.txt"
        p.write_bytes(b"hello")
        assert not INGESTER.can_handle(p)


class TestIngestFullForm:
    def test_ingest_succeeds(self, fake_1099_b_pdf):
        result = INGESTER.ingest(fake_1099_b_pdf)
        assert result.success
        assert result.error is None

    def test_document_kind_is_1099_b(self, fake_1099_b_pdf):
        result = INGESTER.ingest(fake_1099_b_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1099_B

    def test_values_flow_to_canonical_paths(self, fake_1099_b_pdf):
        result = INGESTER.ingest(fake_1099_b_pdf)
        paths = {f.path: f.value for f in result.partial.fields}

        assert paths.get("forms_1099_b[0].broker_name") == (
            "Fidelity Brokerage Services LLC"
        )
        assert (
            paths.get("forms_1099_b[0].transactions[0].description")
            == "100 sh AAPL"
        )
        assert (
            paths.get("forms_1099_b[0].transactions[0].date_sold")
            == "2025-06-15"
        )
        assert (
            paths.get("forms_1099_b[0].transactions[0].proceeds")
            == "19500.00"
        )
        assert (
            paths.get("forms_1099_b[0].transactions[0].cost_basis")
            == "12000.00"
        )
        assert (
            paths.get("forms_1099_b[0].transactions[0].wash_sale_loss_disallowed")
            == "0.00"
        )
        assert (
            paths.get("forms_1099_b[0].box4_federal_income_tax_withheld")
            == "150.00"
        )
        assert (
            paths.get("forms_1099_b[0].transactions[0].is_long_term")
            == "true"
        )

    def test_no_raw_fallback_paths_for_full_form(self, fake_1099_b_pdf):
        """When every field is mapped, none should drop to _acroform_raw."""
        result = INGESTER.ingest(fake_1099_b_pdf)
        for f in result.partial.fields:
            assert not f.path.startswith("_acroform_raw."), (
                f"unmapped field leaked to raw: {f.path}"
            )

    def test_fields_carry_full_confidence(self, fake_1099_b_pdf):
        result = INGESTER.ingest(fake_1099_b_pdf)
        assert result.partial.fields  # non-empty
        for f in result.partial.fields:
            assert f.confidence == 1.0


class TestIngestSparseForm:
    def test_sparse_ingest_succeeds(self, sparse_1099_b_pdf):
        result = INGESTER.ingest(sparse_1099_b_pdf)
        assert result.success
        assert result.is_usable

    def test_sparse_reports_what_is_filled(self, sparse_1099_b_pdf):
        result = INGESTER.ingest(sparse_1099_b_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        assert paths.get("forms_1099_b[0].broker_name") == "Vanguard Brokerage"
        assert (
            paths.get("forms_1099_b[0].transactions[0].proceeds") == "12000.00"
        )
        assert (
            paths.get("forms_1099_b[0].transactions[0].cost_basis") == "10000.00"
        )
        # Unfilled fields must NOT appear in the partial
        assert (
            "forms_1099_b[0].box4_federal_income_tax_withheld" not in paths
        )
        assert (
            "forms_1099_b[0].transactions[0].wash_sale_loss_disallowed" not in paths
        )

    def test_sparse_document_kind(self, sparse_1099_b_pdf):
        result = INGESTER.ingest(sparse_1099_b_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1099_B


class TestSingleTransactionLimitation:
    def test_single_transaction_only_by_design(self):
        """The AcroForm ingester is INTENTIONALLY limited to ONE transaction.

        Real 1099-B forms technically carry one transaction per form, but
        broker summary statements bundle many rows as Form 8949 page
        supplements which are NOT AcroForm widget fields. This Tier 1 ingester
        maps every per-transaction synthetic field into ``transactions[0].*``.

        Multi-row broker summaries are the escape hatch handled by the 1099-B
        Azure Document Intelligence ingester (tabular layout extraction),
        which natively understands multi-row 8949-style tables.

        This test exists so the limitation is enforced in code and any future
        attempt to add ``transactions[1]`` keys to the synthetic field map
        has to explicitly choose to loosen this contract.
        """
        transaction_targets = [
            path
            for path in FORM_1099_B_FIELD_MAP.values()
            if ".transactions[" in path
        ]
        assert transaction_targets, (
            "expected at least one transaction-scoped synthetic field"
        )
        for path in transaction_targets:
            assert "transactions[0]." in path, (
                f"multi-transaction synthetic field detected: {path} — "
                "the AcroForm Tier 1 ingester is single-transaction only; "
                "use the 1099-B Azure Document Intelligence ingester for "
                "multi-row broker summaries."
            )
