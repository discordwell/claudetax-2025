"""Schema validation and cross-check rules for canonical returns.

Public entry point: `run_return_validation(return_) -> dict`.

Composes every validation pass the skill runs on a CanonicalReturn into a
single serializable report. Today the pipeline only has one pass (FFFF
compatibility), but the report shape is designed so future passes —
schema cross-checks, state-level checks, missing-document warnings — can
slot in without breaking callers or the stored ComputedTotals field.

Report shape (JSON-serializable, stable schema):

    {
      "ffff": {
        "compatible": bool,
        "blockers": [{"code", "message", "severity", "canonical_path"}, ...],
        "warnings": [...],
        "infos": [...],
        "details": {...},
      },
      # future: "cross_checks": {...}, "missing_docs": {...}, ...
    }

Callers (`engine.compute()` in particular) should treat the dict as
opaque for forward compatibility — new top-level keys are additive.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from skill.scripts.models import CanonicalReturn
from skill.scripts.validate.ffff_limits import (
    FFFFComplianceReport,
    check_ffff_compatibility,
)

__all__ = ["run_return_validation", "ffff_report_to_dict"]


def ffff_report_to_dict(report: FFFFComplianceReport) -> dict[str, Any]:
    """Serialize a FFFFComplianceReport (frozen dataclass) to a plain dict.

    `asdict` recursively converts the nested FFFFViolation dataclasses as
    well. The returned dict is JSON-safe (every field is str/bool/list/dict).
    """
    return asdict(report)


def run_return_validation(return_: CanonicalReturn) -> dict[str, Any]:
    """Run every validation pass against a canonical return.

    Returns a serializable dict suitable for storage on
    `ComputedTotals.validation_report`.
    """
    ffff: FFFFComplianceReport = check_ffff_compatibility(return_)
    return {"ffff": ffff_report_to_dict(ffff)}
