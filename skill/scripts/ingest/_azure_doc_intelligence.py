"""Tier 3 ingester: Azure AI Document Intelligence "Unified US Tax" model.

This is the OCR / scanned-document path. Used when Tiers 1 and 2 fail because
the document has no text layer — typically a scanned or photographed return.

Azure's prebuilt "prebuilt-tax.us" model classifies and extracts W-2, 1098,
1099-*, and 1040 fields. See:
https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/tax-document

CREDENTIALS: this ingester requires two environment variables:
    AZURE_DOC_INTEL_ENDPOINT   — https://<your-resource>.cognitiveservices.azure.com/
    AZURE_DOC_INTEL_KEY        — your API key

Without them, `can_handle()` returns False and the cascade simply skips this
tier. Tests marked @pytest.mark.azure are skipped when credentials are absent.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from skill.scripts.ingest._pipeline import (
    DocumentKind,
    IngestResult,
    PartialReturn,
)

ENV_ENDPOINT = "AZURE_DOC_INTEL_ENDPOINT"
ENV_KEY = "AZURE_DOC_INTEL_KEY"

# Azure prebuilt tax model IDs (verified against azure-ai-documentintelligence 1.0.x).
AZURE_TAX_MODELS = {
    DocumentKind.FORM_W2: "prebuilt-tax.us.W2",
    DocumentKind.FORM_1098: "prebuilt-tax.us.1098",
    DocumentKind.FORM_1098_E: "prebuilt-tax.us.1098E",
    DocumentKind.FORM_1098_T: "prebuilt-tax.us.1098T",
    DocumentKind.FORM_1099_NEC: "prebuilt-tax.us.1099",
    DocumentKind.FORM_1099_INT: "prebuilt-tax.us.1099",
    DocumentKind.FORM_1099_DIV: "prebuilt-tax.us.1099",
    DocumentKind.FORM_1099_B: "prebuilt-tax.us.1099",
    DocumentKind.FORM_1099_MISC: "prebuilt-tax.us.1099",
    DocumentKind.FORM_1099_K: "prebuilt-tax.us.1099",
    DocumentKind.FORM_1099_R: "prebuilt-tax.us.1099",
    DocumentKind.FORM_1099_G: "prebuilt-tax.us.1099",
    DocumentKind.FORM_1040: "prebuilt-tax.us.1040",
}


def azure_credentials_configured() -> bool:
    return bool(os.environ.get(ENV_ENDPOINT)) and bool(os.environ.get(ENV_KEY))


@dataclass
class AzureDocIntelligenceIngester:
    """OCR / scanned-document ingester using Azure AI Document Intelligence.

    Fan-out sub-agents will implement per-kind field mappings from Azure's
    extracted doc.fields dict to canonical-return paths. This stub wires the
    client and handles the "credentials not configured" case gracefully.
    """

    name: str = "azure_doc_intelligence"
    tier: int = 3

    def can_handle(self, path: Path) -> bool:
        if not azure_credentials_configured():
            return False
        # Azure supports pdf, jpg, png, bmp, tiff, heif
        return path.suffix.lower() in {".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".heif"}

    def ingest(self, path: Path) -> IngestResult:
        partial = PartialReturn()
        if not azure_credentials_configured():
            return IngestResult(
                partial=partial,
                source_path=path,
                ingester_name=self.name,
                success=False,
                error=(
                    f"Azure credentials not set. Export {ENV_ENDPOINT} and {ENV_KEY} "
                    "to enable OCR ingestion."
                ),
            )

        try:
            # Lazy import so test environments without Azure deps still load this module
            from azure.ai.documentintelligence import DocumentIntelligenceClient
            from azure.core.credentials import AzureKeyCredential

            endpoint = os.environ[ENV_ENDPOINT]
            key = os.environ[ENV_KEY]
            client = DocumentIntelligenceClient(
                endpoint=endpoint, credential=AzureKeyCredential(key)
            )

            # Fan-out agents will classify first and pick the right prebuilt model.
            # Base stub: call the generic tax.us model (falls back to layout + classifier).
            # NOTE: "prebuilt-tax.us" is a composed model covering all US tax docs.
            model_id = "prebuilt-tax.us"
            with path.open("rb") as fh:
                poller = client.begin_analyze_document(model_id, analyze_request=fh)
                result = poller.result()

            # Extract top-level document classification
            if result.documents:
                doc = result.documents[0]
                partial.warnings.append(
                    f"Azure returned doc_type={doc.doc_type!r} with "
                    f"confidence={doc.confidence:.2f}. "
                    "Per-kind field mapping is pending fan-out."
                )
                # Emit raw fields for audit; fan-out will map them
                for field_name, field_val in (doc.fields or {}).items():
                    val = getattr(field_val, "value_string", None) or getattr(
                        field_val, "content", None
                    )
                    if val is not None:
                        partial.add(
                            f"_azure_raw.{field_name}",
                            val,
                            confidence=getattr(field_val, "confidence", 0.8) or 0.8,
                        )
        except Exception as exc:
            return IngestResult(
                partial=partial,
                source_path=path,
                ingester_name=self.name,
                success=False,
                error=f"Azure Document Intelligence failed: {exc}",
            )

        return IngestResult(
            partial=partial,
            source_path=path,
            ingester_name=self.name,
            success=True,
        )
