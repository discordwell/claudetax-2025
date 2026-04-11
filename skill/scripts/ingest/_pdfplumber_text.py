"""Tier 2 ingester: extract tax data from the text layer of a PDF.

Used when Tier 1 (AcroForm) fails — typically because the PDF is a commercial-
software-printed 1040 (text layer present, but no fillable widgets) or an IRS
PDF where no values were filled in.

This is the stub base class. Per-form Tier 2 ingesters (e.g. a W-2 text parser,
a 1040 text parser with template-matched coordinates) land in fan-out.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from skill.scripts.ingest._classifier import classify
from skill.scripts.ingest._pipeline import (
    DocumentKind,
    IngestResult,
    PartialReturn,
)


@dataclass
class PdfPlumberTextIngester:
    """Base text-layer ingester.

    This base class runs classification and returns an empty PartialReturn with
    the DocumentKind set. Per-form subclasses override `_extract_fields()` to
    turn extracted text into FieldExtractions. Fan-out will produce those.
    """

    name: str = "pdfplumber_text"
    tier: int = 2

    def can_handle(self, path: Path) -> bool:
        if path.suffix.lower() != ".pdf":
            return False
        try:
            with pdfplumber.open(str(path)) as pdf:
                if not pdf.pages:
                    return False
                text = pdf.pages[0].extract_text() or ""
                return len(text.strip()) > 0
        except Exception:
            return False

    def ingest(self, path: Path) -> IngestResult:
        partial = PartialReturn()
        try:
            with pdfplumber.open(str(path)) as pdf:
                first_text = pdf.pages[0].extract_text() or "" if pdf.pages else ""
                kind = classify(path, first_text)
                partial.document_kind = kind
                self._extract_fields(pdf, kind, partial)
        except Exception as exc:
            return IngestResult(
                partial=partial,
                source_path=path,
                ingester_name=self.name,
                success=False,
                error=f"pdfplumber extraction failed: {exc}",
            )

        return IngestResult(
            partial=partial,
            source_path=path,
            ingester_name=self.name,
            success=True,
        )

    def _extract_fields(
        self, pdf: "pdfplumber.PDF", kind: DocumentKind, partial: PartialReturn
    ) -> None:
        """Per-form extraction. Override in fan-out sub-agents.

        Base implementation is a no-op — partial will have the document_kind set
        but no fields, so the cascade will fall through to Tier 3 OCR.
        """
        partial.warnings.append(
            f"pdfplumber base ingester: no per-form extraction for {kind.value}. "
            "Implement a per-form subclass in fan-out."
        )
