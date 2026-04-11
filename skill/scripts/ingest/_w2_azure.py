"""Tier 3 ingester for Form W-2 via Azure AI Document Intelligence.

This specialises the generic :class:`AzureDocIntelligenceIngester` to call the
W-2 prebuilt model (``prebuilt-tax.us.w2``) and map its extracted fields to
canonical-return paths under ``w2s[0].*``.

The Azure prebuilt W-2 model returns a top-level document whose ``fields`` dict
uses Microsoft's field naming (for example ``WagesTipsAndOtherCompensation``).
We translate those into the canonical paths the rest of the skill consumes.

Sources:
- https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/tax-document
- W-2 schema: https://github.com/Azure-Samples/document-intelligence-code-samples/blob/main/schema/2024-11-30-ga/us-tax/w2.md

Like the base class, this ingester MUST NOT raise when Azure credentials are
unset; instead it returns a failure :class:`IngestResult` so the cascade can
fall through or the caller can render a helpful error.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skill.scripts.ingest._azure_doc_intelligence import (
    AZURE_TAX_MODELS,
    ENV_ENDPOINT,
    ENV_KEY,
    AzureDocIntelligenceIngester,
    azure_credentials_configured,
)
from skill.scripts.ingest._pipeline import (
    DocumentKind,
    IngestResult,
    PartialReturn,
)

# ---------------------------------------------------------------------------
# Azure W-2 field name -> canonical path mapping
# ---------------------------------------------------------------------------
#
# Source: https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/tax-document
# W-2 schema field names (2024-11-30 GA):
# https://github.com/Azure-Samples/document-intelligence-code-samples/blob/main/schema/2024-11-30-ga/us-tax/w2.md
#
# Nested subfields (for example ``Employer.Name``) are navigated dotwise against
# the Azure ``fields`` dict. StateTaxInfos is an array and is handled separately
# in :meth:`W2AzureIngester.ingest` because it expands to multiple canonical
# paths (``w2s[0].state_rows[i].*``).
W2_AZURE_FIELD_MAP: dict[str, str] = {
    "Employer.Name": "w2s[0].employer_name",
    "Employer.IdNumber": "w2s[0].employer_ein",
    "WagesTipsAndOtherCompensation": "w2s[0].box1_wages",
    "FederalIncomeTaxWithheld": "w2s[0].box2_federal_income_tax_withheld",
    "SocialSecurityWages": "w2s[0].box3_social_security_wages",
    "SocialSecurityTaxWithheld": "w2s[0].box4_social_security_tax_withheld",
    "MedicareWagesAndTips": "w2s[0].box5_medicare_wages",
    "MedicareTaxWithheld": "w2s[0].box6_medicare_tax_withheld",
    "SocialSecurityTips": "w2s[0].box7_social_security_tips",
    "AllocatedTips": "w2s[0].box8_allocated_tips",
    "DependentCareBenefits": "w2s[0].box10_dependent_care_benefits",
    "NonQualifiedPlans": "w2s[0].box11_nonqualified_plans",
    # StateTaxInfos is a list — handled separately in ingest()
}

# Azure W-2 subfield names inside each StateTaxInfos list entry.
# Source: https://github.com/Azure-Samples/document-intelligence-code-samples/blob/main/schema/2024-11-30-ga/us-tax/w2.md
_STATE_TAX_INFO_STATE = "State"
_STATE_TAX_INFO_WAGES = "StateWagesTipsEtc"
_STATE_TAX_INFO_TAX = "StateIncomeTax"

# Confidence assigned when an Azure field is present but the SDK omits a
# confidence score for it. The base class uses the same 0.8 fallback.
_DEFAULT_CONFIDENCE = 0.8


def _field_value(field_obj: Any) -> Any:
    """Pull the most useful scalar value out of an Azure ``DocumentField``.

    Azure's SDK exposes a handful of ``value_*`` attributes depending on the
    field's type — ``value_string``, ``value_number``, ``value_currency``,
    ``value_date``, and so on. We prefer ``value_currency.amount`` / ``value_number``
    for money boxes and fall back to ``value_string`` / ``content`` otherwise.
    """
    if field_obj is None:
        return None

    # Currency fields (e.g. WagesTipsAndOtherCompensation) are wrapped in a
    # CurrencyValue dataclass with an ``amount`` attribute.
    value_currency = getattr(field_obj, "value_currency", None)
    if value_currency is not None:
        amount = getattr(value_currency, "amount", None)
        if amount is not None:
            return amount

    value_number = getattr(field_obj, "value_number", None)
    if value_number is not None:
        return value_number

    value_string = getattr(field_obj, "value_string", None)
    if value_string:
        return value_string

    return getattr(field_obj, "content", None)


def _lookup_nested(fields: dict[str, Any], dotted: str) -> Any:
    """Walk ``fields`` using a dotted Azure path like ``Employer.Name``.

    Azure nests composite fields under ``value_object``; we descend into that
    dict for each path segment after the first.
    """
    if not fields:
        return None

    head, _, tail = dotted.partition(".")
    current = fields.get(head)
    if current is None:
        return None

    if not tail:
        return current

    for part in tail.split("."):
        value_object = getattr(current, "value_object", None)
        if not value_object:
            return None
        current = value_object.get(part)
        if current is None:
            return None
    return current


@dataclass
class W2AzureIngester(AzureDocIntelligenceIngester):
    """Azure Document Intelligence ingester specialised for Form W-2.

    Calls the ``prebuilt-tax.us.w2`` prebuilt model and maps its extracted
    fields to canonical ``w2s[0].*`` paths. Inherits graceful
    credentials-missing behavior from the base class — never raises on missing
    env vars, always returns a failure :class:`IngestResult`.
    """

    name: str = "w2_azure"

    def can_handle(self, path: Path) -> bool:
        # Base class gates on credentials and then on file extension. Mirror
        # the same logic here so the cascade skips us cleanly when Azure is
        # not configured.
        if not azure_credentials_configured():
            return False
        return path.suffix.lower() in {
            ".pdf",
            ".jpg",
            ".jpeg",
            ".png",
            ".bmp",
            ".tiff",
            ".tif",
            ".heif",
        }

    def ingest(self, path: Path) -> IngestResult:
        partial = PartialReturn(document_kind=DocumentKind.FORM_W2)

        if not azure_credentials_configured():
            return IngestResult(
                partial=partial,
                source_path=path,
                ingester_name=self.name,
                success=False,
                error=(
                    f"Azure credentials not set. Export {ENV_ENDPOINT} and {ENV_KEY} "
                    "to enable W-2 OCR ingestion."
                ),
            )

        try:
            # Lazy import so test environments without Azure deps still load
            # this module (matches the base class pattern).
            from azure.ai.documentintelligence import DocumentIntelligenceClient
            from azure.core.credentials import AzureKeyCredential

            endpoint = os.environ[ENV_ENDPOINT]
            key = os.environ[ENV_KEY]
            client = DocumentIntelligenceClient(
                endpoint=endpoint, credential=AzureKeyCredential(key)
            )

            # The W-2 specific prebuilt model. We reuse the base class's
            # authoritative model-ID table so there is one source of truth.
            model_id = AZURE_TAX_MODELS[DocumentKind.FORM_W2]
            with path.open("rb") as fh:
                poller = client.begin_analyze_document(model_id, analyze_request=fh)
                result = poller.result()

            if not result.documents:
                partial.warnings.append(
                    "Azure W-2 model returned no documents for this file."
                )
                return IngestResult(
                    partial=partial,
                    source_path=path,
                    ingester_name=self.name,
                    success=True,
                )

            doc = result.documents[0]
            fields = doc.fields or {}

            # ------------------------------------------------------------------
            # Scalar + nested-scalar fields from W2_AZURE_FIELD_MAP
            # ------------------------------------------------------------------
            for azure_name, canonical_path in W2_AZURE_FIELD_MAP.items():
                field_obj = _lookup_nested(fields, azure_name)
                if field_obj is None:
                    continue
                value = _field_value(field_obj)
                if value is None:
                    continue
                confidence = getattr(field_obj, "confidence", None) or _DEFAULT_CONFIDENCE
                partial.add(canonical_path, value, confidence=confidence)

            # ------------------------------------------------------------------
            # StateTaxInfos — a list of per-state rows. Expand into
            # w2s[0].state_rows[i].{state,state_wages,state_tax_withheld}.
            # Source: https://github.com/Azure-Samples/document-intelligence-code-samples/blob/main/schema/2024-11-30-ga/us-tax/w2.md
            # ------------------------------------------------------------------
            state_tax_infos = fields.get("StateTaxInfos")
            if state_tax_infos is not None:
                rows = getattr(state_tax_infos, "value_array", None) or []
                for idx, row in enumerate(rows):
                    sub = getattr(row, "value_object", None) or {}

                    state_field = sub.get(_STATE_TAX_INFO_STATE)
                    wages_field = sub.get(_STATE_TAX_INFO_WAGES)
                    tax_field = sub.get(_STATE_TAX_INFO_TAX)

                    state_val = _field_value(state_field) if state_field else None
                    wages_val = _field_value(wages_field) if wages_field else None
                    tax_val = _field_value(tax_field) if tax_field else None

                    if state_val is not None:
                        partial.add(
                            f"w2s[0].state_rows[{idx}].state",
                            state_val,
                            confidence=(
                                getattr(state_field, "confidence", None)
                                or _DEFAULT_CONFIDENCE
                            ),
                        )
                    if wages_val is not None:
                        partial.add(
                            f"w2s[0].state_rows[{idx}].state_wages",
                            wages_val,
                            confidence=(
                                getattr(wages_field, "confidence", None)
                                or _DEFAULT_CONFIDENCE
                            ),
                        )
                    if tax_val is not None:
                        partial.add(
                            f"w2s[0].state_rows[{idx}].state_tax_withheld",
                            tax_val,
                            confidence=(
                                getattr(tax_field, "confidence", None)
                                or _DEFAULT_CONFIDENCE
                            ),
                        )

        except Exception as exc:
            return IngestResult(
                partial=partial,
                source_path=path,
                ingester_name=self.name,
                success=False,
                error=f"Azure W-2 Document Intelligence failed: {exc}",
            )

        return IngestResult(
            partial=partial,
            source_path=path,
            ingester_name=self.name,
            success=True,
        )


# Module-level singleton the cascade can import directly.
INGESTER = W2AzureIngester()
