"""Tier 1 ingester: extract AcroForm fields from a fillable IRS PDF.

This is the highest-confidence path: if the PDF is a real IRS fillable form
(1040, schedules, etc.) and the widgets have values, pypdf reads them directly.

Tier 1 = no OCR, no layout guessing. Either the fields are there or can_handle()
returns False so the cascade falls through to Tier 2 (pdfplumber text-layer).

This file is deliberately minimal — it establishes the Tier 1 contract. Per-form
field-name mappings (e.g. "what IRS field name holds 1040 line 1z wages") are
added in fan-out, one sub-agent per form.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pypdf

from skill.scripts.ingest._classifier import classify
from skill.scripts.ingest._pipeline import (
    DocumentKind,
    IngestResult,
    PartialReturn,
)


@dataclass
class PyPdfAcroFormIngester:
    """Reads AcroForm widget values out of a fillable PDF.

    The class-level mapping from IRS AcroForm field names to canonical-return
    paths is populated per-form in fan-out. This base implementation returns
    the raw field dict wrapped in a PartialReturn so other code can inspect it.
    """

    name: str = "pypdf_acroform"
    tier: int = 1
    field_map: dict[DocumentKind, dict[str, str]] = field(default_factory=dict)
    """{ DocumentKind: { pdf_field_name: canonical_path, ... }, ... }
    Populated per-form in fan-out."""

    def can_handle(self, path: Path) -> bool:
        if path.suffix.lower() != ".pdf":
            return False
        try:
            with path.open("rb") as fh:
                reader = pypdf.PdfReader(fh)
                # An AcroForm-enabled PDF has an /AcroForm dict at the catalog
                root = reader.trailer["/Root"]
                if "/AcroForm" not in root:
                    return False
                fields = reader.get_fields()
                return fields is not None and len(fields) > 0
        except Exception:
            return False

    def ingest(self, path: Path) -> IngestResult:
        partial = PartialReturn()
        try:
            with path.open("rb") as fh:
                reader = pypdf.PdfReader(fh)
                fields = reader.get_fields() or {}

                # Classify to know which field_map to use
                first_text = ""
                if reader.pages:
                    try:
                        first_text = reader.pages[0].extract_text() or ""
                    except Exception:
                        first_text = ""
                kind = classify(path, first_text)
                partial.document_kind = kind

                mapping = self.field_map.get(kind, {})

                # Emit raw field values. Per-form mappings in fan-out will
                # translate these into canonical paths; until then the raw dict
                # is preserved under an "_acroform_raw" pseudo-path for audit.
                for pdf_field_name, field_obj in fields.items():
                    value = getattr(field_obj, "value", None)
                    if value is None:
                        continue
                    canonical_path = mapping.get(pdf_field_name)
                    if canonical_path:
                        partial.add(canonical_path, value, confidence=1.0)
                    else:
                        partial.add(
                            f"_acroform_raw.{pdf_field_name}",
                            value,
                            confidence=1.0,
                            raw_text=str(value),
                        )

                if not partial.fields:
                    partial.warnings.append("AcroForm present but no field values set")

        except Exception as exc:
            return IngestResult(
                partial=partial,
                source_path=path,
                ingester_name=self.name,
                success=False,
                error=f"pypdf extraction failed: {exc}",
            )

        return IngestResult(
            partial=partial,
            source_path=path,
            ingester_name=self.name,
            success=True,
        )
