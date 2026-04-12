"""Ingestion pipeline interface — the contract every ingester implements.

An ingester takes a source (file path or raw bytes) and produces a PartialReturn:
a structured patch to apply to a CanonicalReturn. Multiple ingesters run in a
cascade (pypdf AcroForm → pdfplumber text-layer → Azure Document Intelligence OCR)
until one of them returns a usable result.

The 12 per-form ingester sub-agents in fan-out all code against this file.

Key types:

- DocumentKind: the tax document types we know how to classify
- IngestResult: what an ingester returns (partial data + metadata)
- PartialReturn: a path-addressable patch for CanonicalReturn (e.g.,
  `w2s[0].box1_wages = 65000`)
- Ingester: the Protocol
- IngestCascade: orchestrator that tries multiple ingesters in order
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Document classification
# ---------------------------------------------------------------------------


class DocumentKind(str, Enum):
    """Known tax document types. Classifier returns one of these, or UNKNOWN."""

    FORM_1040 = "form_1040"
    FORM_1040_SR = "form_1040_sr"
    FORM_1040_X = "form_1040_x"
    SCHEDULE_A = "schedule_a"
    SCHEDULE_B = "schedule_b"
    SCHEDULE_C = "schedule_c"
    SCHEDULE_D = "schedule_d"
    SCHEDULE_E = "schedule_e"
    SCHEDULE_SE = "schedule_se"
    SCHEDULE_1 = "schedule_1"
    SCHEDULE_2 = "schedule_2"
    SCHEDULE_3 = "schedule_3"
    FORM_W2 = "form_w2"
    FORM_1099_INT = "form_1099_int"
    FORM_1099_DIV = "form_1099_div"
    FORM_1099_B = "form_1099_b"
    FORM_1099_NEC = "form_1099_nec"
    FORM_1099_MISC = "form_1099_misc"
    FORM_1099_K = "form_1099_k"
    FORM_1099_R = "form_1099_r"
    FORM_1099_G = "form_1099_g"
    FORM_SSA_1099 = "form_ssa_1099"
    FORM_1095_A = "form_1095_a"
    FORM_1098 = "form_1098"
    FORM_1098_E = "form_1098_e"
    FORM_1098_T = "form_1098_t"
    SCHEDULE_K1_1065 = "schedule_k1_1065"
    SCHEDULE_K1_1120S = "schedule_k1_1120s"
    TXF = "txf"
    STATE_RETURN = "state_return"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# PartialReturn — a patch to apply to CanonicalReturn
# ---------------------------------------------------------------------------


@dataclass
class FieldExtraction:
    """A single field extracted by an ingester.

    path: a canonical-return-addressable path (e.g. "w2s[0].box1_wages").
    value: the extracted value (Decimal for money, str for text, etc.).
    confidence: [0.0, 1.0] confidence score. 1.0 = directly read from an
        AcroForm widget. <1.0 = inferred from layout, OCR, etc.
    source_bbox: optional bounding box on the source page for audit.
    raw_text: optional raw text/OCR snippet for audit.
    """

    path: str
    value: Any
    confidence: float = 1.0
    source_bbox: tuple[float, float, float, float] | None = None
    raw_text: str | None = None


@dataclass
class PartialReturn:
    """A structured patch an ingester wants to apply to a CanonicalReturn.

    v0.1 behavior: the cascade is first-usable-wins — it returns the first
    ingester whose result `is_usable`, without merging. A future version may
    introduce a merge step that combines fields from multiple ingesters (e.g.
    pypdf reads the headline W-2 boxes + pdfplumber picks up box 14 notes).
    When merging lands, the policy will be: higher confidence wins per path,
    earlier cascade tier breaks ties.
    """

    fields: list[FieldExtraction] = field(default_factory=list)
    document_kind: DocumentKind = DocumentKind.UNKNOWN
    warnings: list[str] = field(default_factory=list)

    def add(
        self,
        path: str,
        value: Any,
        confidence: float = 1.0,
        source_bbox: tuple[float, float, float, float] | None = None,
        raw_text: str | None = None,
    ) -> None:
        """Append a FieldExtraction to this partial.

        Only the documented kwargs are accepted — unknown kwargs were previously
        swallowed by **meta and silently ignored. If you need more metadata,
        extend FieldExtraction and update callers explicitly.
        """
        self.fields.append(
            FieldExtraction(
                path=path,
                value=value,
                confidence=confidence,
                source_bbox=source_bbox,
                raw_text=raw_text,
            )
        )

    def is_empty(self) -> bool:
        return len(self.fields) == 0


# ---------------------------------------------------------------------------
# Ingest result wrapper
# ---------------------------------------------------------------------------


@dataclass
class IngestResult:
    """What an ingester returns from a single document."""

    partial: PartialReturn
    source_path: Path | None
    ingester_name: str
    success: bool
    error: str | None = None

    @property
    def is_usable(self) -> bool:
        """True if the partial return has at least one field with reasonable confidence."""
        return (
            self.success
            and not self.partial.is_empty()
            and any(f.confidence >= 0.5 for f in self.partial.fields)
        )


# ---------------------------------------------------------------------------
# Ingester Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Ingester(Protocol):
    """Single-tier ingester. Takes a document, returns a partial return.

    Implementations live at skill/scripts/ingest/_<tier>_<form>.py
    (e.g. _pypdf_acroform.py, _pdfplumber_w2.py, _azure_1040.py).
    """

    name: str
    tier: int  # 1 = pypdf AcroForm, 2 = pdfplumber text, 3 = Azure OCR

    def can_handle(self, path: Path) -> bool:
        """Cheap filter: does this ingester even want to try this file?

        Used by the cascade to skip obviously-irrelevant ingesters (e.g. an
        AcroForm ingester should skip scanned JPGs).
        """
        ...

    def ingest(self, path: Path) -> IngestResult:
        """Parse the document and return a partial return."""
        ...


# ---------------------------------------------------------------------------
# Cascade orchestrator
# ---------------------------------------------------------------------------


class IngestCascade:
    """Runs a stack of ingesters in tier order and returns the first usable result.

    Tier 1 ingesters (pypdf AcroForm) are fast and high-confidence; always try
    them first. Tier 2 (pdfplumber text-layer) is the fallback. Tier 3 (OCR) is
    the last resort because it's slow and uses a paid API.

    v0.1 policy: first-usable wins, no merging across ingesters.
    """

    def __init__(self, ingesters: list[Ingester]) -> None:
        # Sort by tier so lower tiers try first
        self._ingesters = sorted(ingesters, key=lambda i: i.tier)

    def ingest(self, path: Path) -> IngestResult:
        """Run through the cascade until one ingester returns a usable result.

        Returns the first usable result. If no ingester succeeds, returns the
        last attempted result (which will have success=False or is_usable=False).
        """
        last_result: IngestResult | None = None
        for ingester in self._ingesters:
            if not ingester.can_handle(path):
                continue
            result = ingester.ingest(path)
            last_result = result
            if result.is_usable:
                return result

        if last_result is None:
            return IngestResult(
                partial=PartialReturn(),
                source_path=path,
                ingester_name="cascade",
                success=False,
                error=f"no ingester could handle {path}",
            )
        return last_result

    def ingest_many(self, paths: list[Path]) -> list[IngestResult]:
        """Run the cascade over multiple documents (e.g. a folder of W-2s)."""
        return [self.ingest(p) for p in paths]

    @property
    def ingester_names(self) -> list[str]:
        return [i.name for i in self._ingesters]
