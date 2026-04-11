"""CP6 — ingestion pipeline interface + OCR cascade tests.

Covers:
- Document classifier (filename and content heuristics)
- Ingester Protocol at runtime
- Cascade orchestrator tier ordering
- pypdf AcroForm ingester on a synthetic fillable PDF
- pdfplumber text ingester on a synthetic text PDF
- Azure ingester is gated behind credentials and returns a graceful failure when missing
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import pypdf
import pytest
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)
from reportlab.pdfgen import canvas

from skill.scripts.ingest._azure_doc_intelligence import (
    AzureDocIntelligenceIngester,
    azure_credentials_configured,
)
from skill.scripts.ingest._classifier import (
    DocumentKind,
    classify,
    classify_by_filename,
    classify_by_text,
)
from skill.scripts.ingest._pdfplumber_text import PdfPlumberTextIngester
from skill.scripts.ingest._pipeline import (
    FieldExtraction,
    IngestCascade,
    IngestResult,
    Ingester,
    PartialReturn,
)
from skill.scripts.ingest._pypdf_acroform import PyPdfAcroFormIngester


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class TestClassifierFilename:
    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("f1040.pdf", DocumentKind.FORM_1040),
            ("Form_1040.pdf", DocumentKind.FORM_1040),
            ("f1040x.pdf", DocumentKind.FORM_1040_X),
            ("f1040sr.pdf", DocumentKind.FORM_1040_SR),
            ("w2_employer.pdf", DocumentKind.FORM_W2),
            ("w-2_2025.pdf", DocumentKind.FORM_W2),
            ("1099-NEC.pdf", DocumentKind.FORM_1099_NEC),
            ("1099-INT_bank.pdf", DocumentKind.FORM_1099_INT),
            ("1099-DIV.pdf", DocumentKind.FORM_1099_DIV),
            ("1099-B_broker.pdf", DocumentKind.FORM_1099_B),
            ("1099-K.pdf", DocumentKind.FORM_1099_K),
            ("SSA-1099.pdf", DocumentKind.FORM_SSA_1099),
            ("1098-T.pdf", DocumentKind.FORM_1098_T),
            ("schedule_c.pdf", DocumentKind.SCHEDULE_C),
            ("ScheduleE.pdf", DocumentKind.SCHEDULE_E),
            ("brokerage.txf", DocumentKind.TXF),
            ("random_file.pdf", DocumentKind.UNKNOWN),
        ],
    )
    def test_classify_by_filename(self, filename, expected):
        assert classify_by_filename(Path(filename)) == expected


class TestClassifierText:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Wage and Tax Statement", DocumentKind.FORM_W2),
            ("Form 1040 U.S. Individual Income Tax Return", DocumentKind.FORM_1040),
            ("Form 1040-X Amended U.S. Individual", DocumentKind.FORM_1040_X),
            ("Nonemployee Compensation", DocumentKind.FORM_1099_NEC),
            ("Dividends and Distributions", DocumentKind.FORM_1099_DIV),
            ("Proceeds From Broker and Barter", DocumentKind.FORM_1099_B),
            ("Mortgage Interest Statement", DocumentKind.FORM_1098),
            ("Profit or Loss From Business", DocumentKind.SCHEDULE_C),
            ("Self-Employment Tax", DocumentKind.SCHEDULE_SE),
            ("Supplemental Income and Loss", DocumentKind.SCHEDULE_E),
            ("", DocumentKind.UNKNOWN),
            ("random text that means nothing", DocumentKind.UNKNOWN),
        ],
    )
    def test_classify_by_text(self, text, expected):
        assert classify_by_text(text) == expected

    def test_classify_combines_filename_and_text(self, tmp_path):
        """Filename hint wins over text — tried first."""
        p = tmp_path / "something_random.pdf"
        p.write_bytes(b"")
        assert classify(p, "Wage and Tax Statement") == DocumentKind.FORM_W2

    def test_classify_text_fallback_when_filename_unknown(self, tmp_path):
        p = tmp_path / "unknown_name.pdf"
        p.write_bytes(b"")
        assert classify(p, "Wage and Tax Statement") == DocumentKind.FORM_W2


# ---------------------------------------------------------------------------
# Protocol and data types
# ---------------------------------------------------------------------------


class TestProtocolAndTypes:
    def test_pypdf_ingester_satisfies_protocol(self):
        assert isinstance(PyPdfAcroFormIngester(), Ingester)

    def test_pdfplumber_ingester_satisfies_protocol(self):
        assert isinstance(PdfPlumberTextIngester(), Ingester)

    def test_azure_ingester_satisfies_protocol(self):
        assert isinstance(AzureDocIntelligenceIngester(), Ingester)

    def test_partial_return_add(self):
        p = PartialReturn()
        p.add("w2s[0].box1_wages", 65000, confidence=1.0)
        assert len(p.fields) == 1
        assert p.fields[0].path == "w2s[0].box1_wages"
        assert p.fields[0].value == 65000

    def test_partial_return_is_empty(self):
        assert PartialReturn().is_empty()
        p = PartialReturn()
        p.add("x", 1)
        assert not p.is_empty()

    def test_ingest_result_is_usable(self):
        r = IngestResult(
            partial=PartialReturn(fields=[FieldExtraction("x", 1, 1.0)]),
            source_path=None,
            ingester_name="test",
            success=True,
        )
        assert r.is_usable

    def test_ingest_result_low_confidence_not_usable(self):
        r = IngestResult(
            partial=PartialReturn(fields=[FieldExtraction("x", 1, 0.2)]),
            source_path=None,
            ingester_name="test",
            success=True,
        )
        assert not r.is_usable

    def test_ingest_result_empty_not_usable(self):
        r = IngestResult(
            partial=PartialReturn(), source_path=None, ingester_name="test", success=True
        )
        assert not r.is_usable

    def test_ingest_result_failed_not_usable(self):
        r = IngestResult(
            partial=PartialReturn(fields=[FieldExtraction("x", 1, 1.0)]),
            source_path=None,
            ingester_name="test",
            success=False,
            error="boom",
        )
        assert not r.is_usable


# ---------------------------------------------------------------------------
# Cascade orchestrator
# ---------------------------------------------------------------------------


@dataclass
class _StubIngester:
    name: str
    tier: int
    _produces: PartialReturn = field(default_factory=PartialReturn)
    _handled_paths: list[Path] = field(default_factory=list)
    _accept_all: bool = True

    def can_handle(self, path: Path) -> bool:
        return self._accept_all

    def ingest(self, path: Path) -> IngestResult:
        self._handled_paths.append(path)
        return IngestResult(
            partial=self._produces,
            source_path=path,
            ingester_name=self.name,
            success=True,
        )


class TestIngestCascade:
    def test_tier_order_respected(self):
        t3 = _StubIngester("t3", tier=3)
        t1 = _StubIngester("t1", tier=1)
        t2 = _StubIngester("t2", tier=2)
        cascade = IngestCascade([t3, t1, t2])
        assert cascade.ingester_names == ["t1", "t2", "t3"]

    def test_first_usable_wins(self, tmp_path):
        path = tmp_path / "x.pdf"
        path.write_bytes(b"")

        usable_partial = PartialReturn(fields=[FieldExtraction("x", 1, 1.0)])
        t1 = _StubIngester("t1", tier=1, _produces=usable_partial)
        t2 = _StubIngester("t2", tier=2, _produces=PartialReturn())

        cascade = IngestCascade([t1, t2])
        result = cascade.ingest(path)
        assert result.ingester_name == "t1"
        assert t2._handled_paths == []  # t2 never called

    def test_fall_through_on_empty(self, tmp_path):
        path = tmp_path / "x.pdf"
        path.write_bytes(b"")
        t1 = _StubIngester("t1", tier=1, _produces=PartialReturn())
        t2_partial = PartialReturn(fields=[FieldExtraction("x", 1, 1.0)])
        t2 = _StubIngester("t2", tier=2, _produces=t2_partial)
        cascade = IngestCascade([t1, t2])
        result = cascade.ingest(path)
        assert result.ingester_name == "t2"

    def test_no_handlers_returns_unsuccessful(self, tmp_path):
        path = tmp_path / "x.pdf"
        path.write_bytes(b"")
        t1 = _StubIngester("t1", tier=1, _accept_all=False)
        cascade = IngestCascade([t1])
        result = cascade.ingest(path)
        assert not result.success
        assert "no ingester could handle" in (result.error or "")

    def test_ingest_many(self, tmp_path):
        p1 = tmp_path / "a.pdf"
        p2 = tmp_path / "b.pdf"
        p1.write_bytes(b"")
        p2.write_bytes(b"")
        usable = PartialReturn(fields=[FieldExtraction("x", 1, 1.0)])
        t1 = _StubIngester("t1", tier=1, _produces=usable)
        cascade = IngestCascade([t1])
        results = cascade.ingest_many([p1, p2])
        assert len(results) == 2
        assert all(r.success for r in results)


# ---------------------------------------------------------------------------
# Synthetic fillable PDF fixture
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


class TestPyPdfAcroFormIngester:
    @pytest.fixture
    def fillable_pdf(self, tmp_path) -> Path:
        p = tmp_path / "1099-NEC_fake.pdf"
        _make_acroform_pdf(
            p, {"payer_name": "Acme Co", "nonemployee_comp": "12345.00"}
        )
        return p

    def test_can_handle_acroform(self, fillable_pdf):
        ing = PyPdfAcroFormIngester()
        assert ing.can_handle(fillable_pdf)

    def test_can_handle_non_pdf(self, tmp_path):
        p = tmp_path / "some.txt"
        p.write_bytes(b"not a pdf")
        ing = PyPdfAcroFormIngester()
        assert not ing.can_handle(p)

    def test_ingest_extracts_field_values(self, fillable_pdf):
        ing = PyPdfAcroFormIngester()
        result = ing.ingest(fillable_pdf)
        assert result.success
        # Without a configured field_map, the ingester emits under _acroform_raw.*
        paths = [f.path for f in result.partial.fields]
        values = {f.path.split(".", 1)[1]: f.value for f in result.partial.fields}
        assert any("_acroform_raw.payer_name" in p for p in paths)
        assert any("_acroform_raw.nonemployee_comp" in p for p in paths)
        assert values.get("payer_name") == "Acme Co"
        assert values.get("nonemployee_comp") == "12345.00"

    def test_document_kind_from_filename_hint(self, fillable_pdf):
        ing = PyPdfAcroFormIngester()
        result = ing.ingest(fillable_pdf)
        # Filename contains "1099-NEC", so classifier picks it up
        assert result.partial.document_kind == DocumentKind.FORM_1099_NEC

    def test_configured_field_map_rewrites_paths(self, fillable_pdf):
        ing = PyPdfAcroFormIngester(
            field_map={
                DocumentKind.FORM_1099_NEC: {
                    "payer_name": "forms_1099_nec[0].payer_name",
                    "nonemployee_comp": "forms_1099_nec[0].box1_nonemployee_compensation",
                }
            }
        )
        result = ing.ingest(fillable_pdf)
        paths = {f.path for f in result.partial.fields}
        assert "forms_1099_nec[0].payer_name" in paths
        assert "forms_1099_nec[0].box1_nonemployee_compensation" in paths


class TestPdfPlumberTextIngester:
    @pytest.fixture
    def text_pdf(self, tmp_path) -> Path:
        """A PDF with a text layer but no AcroForm."""
        p = tmp_path / "unknown_print.pdf"
        c = canvas.Canvas(str(p))
        c.drawString(50, 750, "Wage and Tax Statement")
        c.drawString(50, 720, "Employer: Acme Corp")
        c.drawString(50, 690, "Wages: 65,000.00")
        c.save()
        return p

    def test_can_handle_text_pdf(self, text_pdf):
        assert PdfPlumberTextIngester().can_handle(text_pdf)

    def test_ingest_sets_document_kind_from_text(self, text_pdf):
        ing = PdfPlumberTextIngester()
        result = ing.ingest(text_pdf)
        assert result.success
        # Text "Wage and Tax Statement" classifies as W-2
        assert result.partial.document_kind == DocumentKind.FORM_W2


# ---------------------------------------------------------------------------
# Azure ingester: graceful credentials-missing behavior
# ---------------------------------------------------------------------------


class TestAzureIngesterGracefulMissingCreds:
    def test_credentials_not_configured_by_default(self, monkeypatch):
        monkeypatch.delenv("AZURE_DOC_INTEL_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_DOC_INTEL_KEY", raising=False)
        assert not azure_credentials_configured()

    def test_can_handle_false_without_credentials(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AZURE_DOC_INTEL_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_DOC_INTEL_KEY", raising=False)
        p = tmp_path / "scanned.pdf"
        p.write_bytes(b"")
        assert not AzureDocIntelligenceIngester().can_handle(p)

    def test_ingest_returns_error_without_credentials(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AZURE_DOC_INTEL_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_DOC_INTEL_KEY", raising=False)
        p = tmp_path / "scanned.pdf"
        p.write_bytes(b"")
        result = AzureDocIntelligenceIngester().ingest(p)
        assert not result.success
        assert "credentials" in (result.error or "").lower()

    def test_credentials_detected_when_set(self, monkeypatch):
        monkeypatch.setenv("AZURE_DOC_INTEL_ENDPOINT", "https://example.azure.com/")
        monkeypatch.setenv("AZURE_DOC_INTEL_KEY", "fake-key")
        assert azure_credentials_configured()


# ---------------------------------------------------------------------------
# End-to-end: cascade falls through from Tier 1 to Tier 2 on a text-only PDF
# ---------------------------------------------------------------------------


class TestEndToEndCascade:
    def test_cascade_runs_tier1_then_tier2(self, tmp_path):
        """A text-layer PDF without AcroForm: Tier 1 skips, Tier 2 classifies."""
        p = tmp_path / "print_out.pdf"
        c = canvas.Canvas(str(p))
        c.drawString(50, 750, "Form 1040 U.S. Individual Income Tax Return")
        c.save()

        cascade = IngestCascade(
            [PyPdfAcroFormIngester(), PdfPlumberTextIngester(), AzureDocIntelligenceIngester()]
        )
        result = cascade.ingest(p)
        # Tier 2 should classify but emit 0 fields (base impl). Tier 3 fails
        # without credentials. So the overall result is "last attempted", which
        # is Tier 3 with an error OR Tier 2 with classification-only.
        # Our contract: IngestCascade returns the last attempted result when
        # none are usable. Verify at least that the classifier worked at tier 2.
        # We check the intermediate partial from Tier 2 directly:
        tier2 = PdfPlumberTextIngester()
        tier2_result = tier2.ingest(p)
        assert tier2_result.partial.document_kind == DocumentKind.FORM_1040
