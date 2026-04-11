"""End-to-end pipeline — the first real wet-test harness for the skill.

This is the glue that finally assembles the dark code produced across
waves 1-4 into a single user-facing flow:

    PDFs on disk → classifier → ingester cascade → PartialReturn(s)
                                                            ↓
                                         FieldExtraction path application
                                                            ↓
                                                   CanonicalReturn
                                                            ↓
                                                 engine.compute()
                                                            ↓
                                             rendered PDFs (1040 + Sch A/B/C/SE)
                                                            ↓
                                           result.json + validation_report

CP8-E lands the skeleton; wave 5 will extend it with state PDF
rendering, FFFF entry mapping, paper-bundle creation, and the SKILL.md
interview handoff.

Usage (programmatic)
--------------------

    from pathlib import Path
    from skill.scripts.pipeline import run_pipeline

    result = run_pipeline(
        input_dir=Path("./user_pdfs"),
        taxpayer_info_path=Path("./taxpayer.json"),
        output_dir=Path("./out"),
    )
    print(result.canonical_return.computed.adjusted_gross_income)
    print("PDFs written:", result.rendered_paths)

The ``taxpayer_info.json`` file carries the header fields that the PDF
ingesters cannot extract (taxpayer name, SSN, DOB, filing status,
address, spouse, dependents). Its shape is a partial CanonicalReturn
dict — the pipeline merges it with the ingested PDF fields before
validation.

Design notes
------------

* **Dict-based merge**. We do not construct intermediate Pydantic
  models until the final ``CanonicalReturn.model_validate`` call. This
  lets us incrementally build up nested structure (``w2s[0].box1_wages``)
  without tripping Pydantic's strict field validators mid-assembly.
* **Pydantic coerces strings**. PDF widgets expose text strings even
  for money fields; Pydantic's Decimal validator accepts numeric
  strings, so we pass the raw string values through.
* **One ingester per file**. The cascade's first-usable-wins policy
  applies per file. If an earlier ingester produces a usable result
  the later tiers don't run, which matches the existing
  ``IngestCascade`` v0.1 semantics.
* **No state PDFs yet**. Wave 5 will add state plugin dispatch +
  state return PDF rendering to this pipeline. For now we only
  render the federal forms.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from skill.scripts.calc.engine import compute
from skill.scripts.ingest._pipeline import (
    DocumentKind,
    IngestResult,
    IngestCascade,
    PartialReturn,
)
from skill.scripts.models import CanonicalReturn


# ---------------------------------------------------------------------------
# Path parsing and dict patching
# ---------------------------------------------------------------------------


_INDEX_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?$")


def _parse_path(path: str) -> list[tuple[str, int | None]]:
    """Parse a canonical path string into (key, index) segments.

    Examples:
        "w2s[0].box1_wages"  -> [("w2s", 0), ("box1_wages", None)]
        "taxpayer.ssn"       -> [("taxpayer", None), ("ssn", None)]
        "w2s[1].state_rows[0].state_code"
            -> [("w2s", 1), ("state_rows", 0), ("state_code", None)]

    Raises
    ------
    ValueError
        If a segment does not match the ``identifier[index]?`` form.
    """
    segments: list[tuple[str, int | None]] = []
    for raw in path.split("."):
        m = _INDEX_RE.match(raw)
        if m is None:
            raise ValueError(f"malformed canonical path segment: {raw!r}")
        key = m.group(1)
        idx_str = m.group(2)
        idx = int(idx_str) if idx_str is not None else None
        segments.append((key, idx))
    return segments


def _ensure_list_length(lst: list[Any], length: int) -> None:
    """Extend ``lst`` with empty dicts until it has at least ``length``."""
    while len(lst) < length:
        lst.append({})


def _set_path(root: dict[str, Any], path: str, value: Any) -> None:
    """Set ``value`` at ``path`` inside the nested dict ``root``.

    Creates any missing intermediate dicts or list entries. List entries
    are created as empty dicts so downstream segments can fill them in.

    Scalar paths like "taxpayer.first_name" route into nested dicts.
    Indexed paths like "w2s[0].box1_wages" create a list whose element
    at index 0 is a dict carrying the ``box1_wages`` key.

    This function is deliberately permissive — it does not enforce the
    canonical schema (that's Pydantic's job at validate time). It just
    produces a dict shape that Pydantic can consume.

    Pseudo-path segments like ``_acroform_raw.<field>`` from the pypdf
    base ingester are silently ignored by the caller (pipeline applies
    a filter before invoking this function).
    """
    segments = _parse_path(path)
    cursor: Any = root
    for i, (key, idx) in enumerate(segments):
        is_last = i == len(segments) - 1
        if idx is None:
            if is_last:
                cursor[key] = value
                return
            if key not in cursor or not isinstance(cursor[key], dict):
                cursor[key] = {}
            cursor = cursor[key]
        else:
            if key not in cursor or not isinstance(cursor[key], list):
                cursor[key] = []
            _ensure_list_length(cursor[key], idx + 1)
            if is_last:
                cursor[key][idx] = value
                return
            if not isinstance(cursor[key][idx], dict):
                cursor[key][idx] = {}
            cursor = cursor[key][idx]


def apply_partial_to_dict(
    partial: PartialReturn, base: dict[str, Any]
) -> dict[str, Any]:
    """Apply every ``FieldExtraction`` in ``partial`` onto ``base`` in place.

    Silently skips pseudo-paths that start with ``_acroform_raw.`` —
    those are the fallback capture from the pypdf base ingester and are
    not part of the canonical schema. They indicate a per-form field
    map is missing but do not block the pipeline.

    Returns the same ``base`` dict for chaining convenience.
    """
    for extraction in partial.fields:
        path = extraction.path
        if path.startswith("_acroform_raw."):
            continue
        _set_path(base, path, extraction.value)
    return base


# ---------------------------------------------------------------------------
# Cascade assembly
# ---------------------------------------------------------------------------


def build_default_cascade() -> IngestCascade:
    """Assemble every registered Tier-1 ingester into a single cascade.

    The cascade runs in tier order (Tier 1 AcroForm → Tier 2 text-layer
    → Tier 3 OCR). Only Tier 1 ingesters are registered out of the box;
    Tier 2 and Tier 3 are instantiated conditionally when their prereqs
    (pdfplumber text layer, Azure credentials) are available.

    Wave-5 state agents and the SKILL.md interview flow can call this
    helper instead of reaching into individual ingester modules.
    """
    from skill.scripts.ingest._1099_b_acroform import INGESTER as I_1099_B
    from skill.scripts.ingest._1099_div_acroform import INGESTER as I_1099_DIV
    from skill.scripts.ingest._1099_g_acroform import INGESTER as I_1099_G
    from skill.scripts.ingest._1099_int_acroform import INGESTER as I_1099_INT
    from skill.scripts.ingest._1099_nec_acroform import INGESTER as I_1099_NEC
    from skill.scripts.ingest._1099_r_acroform import INGESTER as I_1099_R
    from skill.scripts.ingest._ssa_1099_acroform import INGESTER as I_SSA_1099
    from skill.scripts.ingest._w2_acroform import INGESTER as I_W2

    tier_1: list[Any] = [
        I_W2,
        I_1099_INT,
        I_1099_DIV,
        I_1099_B,
        I_1099_NEC,
        I_1099_R,
        I_1099_G,
        I_SSA_1099,
    ]
    return IngestCascade(tier_1)


# ---------------------------------------------------------------------------
# Pipeline result dataclass
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """Everything the pipeline produced for one run.

    Callers can inspect ``canonical_return.computed`` for tax numbers,
    ``validation_report`` (mirrors ``canonical_return.computed.
    validation_report``) for FFFF / cross-check output, and
    ``rendered_paths`` for on-disk PDF locations.
    """

    canonical_return: CanonicalReturn
    ingest_results: list[IngestResult] = field(default_factory=list)
    rendered_paths: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def validation_report(self) -> dict[str, Any] | None:
        return self.canonical_return.computed.validation_report

    def write_result_json(self, path: Path) -> None:
        """Serialize ``canonical_return`` as a JSON file at ``path``."""
        data = self.canonical_return.model_dump(mode="json")
        path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_pipeline(
    input_dir: Path,
    taxpayer_info_path: Path,
    output_dir: Path,
    *,
    render_form_1040: bool = True,
    render_schedule_a: bool = True,
    render_schedule_b: bool = True,
    render_schedule_c: bool = True,
    render_schedule_se: bool = True,
) -> PipelineResult:
    """Run the full pipeline: ingest → compute → render → emit result.

    Parameters
    ----------
    input_dir
        Directory containing PDF tax documents (W-2s, 1099s, etc.).
        Subdirectories are NOT recursed; drop the relevant docs
        directly in the top level.
    taxpayer_info_path
        Path to a JSON file containing header fields that the PDF
        ingesters cannot extract (taxpayer name, SSN, DOB, filing
        status, address, spouse, dependents). Shape is a partial
        CanonicalReturn dict.
    output_dir
        Directory to write rendered PDFs and ``result.json``. Created
        if it does not exist.
    render_form_1040, render_schedule_a, ..., render_schedule_se
        Per-form render gates. Default ``True`` renders every applicable
        federal form. Schedule B is skipped if ``schedule_b_required``
        returns False; Schedule C is skipped if no schedules_c are
        present; Schedule SE is skipped if SE net earnings are under
        the $400 filing floor.

    Returns
    -------
    PipelineResult
        ``canonical_return`` is the patched-and-computed return;
        ``ingest_results`` is the per-file ingest output;
        ``rendered_paths`` is the list of PDF paths written; and
        ``warnings`` is a consolidated list of any warnings emitted
        during the run.

    Raises
    ------
    FileNotFoundError
        If ``input_dir`` or ``taxpayer_info_path`` does not exist.
    pydantic.ValidationError
        If the assembled dict fails to validate as a CanonicalReturn
        (usually missing required header fields in taxpayer_info.json).
    """
    if not input_dir.exists():
        raise FileNotFoundError(f"input_dir not found: {input_dir}")
    if not taxpayer_info_path.exists():
        raise FileNotFoundError(
            f"taxpayer_info_path not found: {taxpayer_info_path}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 1. Base dict from taxpayer_info.json
    # ------------------------------------------------------------------
    base: dict[str, Any] = json.loads(taxpayer_info_path.read_text())

    # ------------------------------------------------------------------
    # 2. Ingest every PDF in input_dir via the default cascade
    # ------------------------------------------------------------------
    cascade = build_default_cascade()
    pdf_paths = sorted(p for p in input_dir.iterdir() if p.suffix.lower() == ".pdf")
    ingest_results: list[IngestResult] = []
    warnings: list[str] = []

    for pdf_path in pdf_paths:
        result = cascade.ingest(pdf_path)
        ingest_results.append(result)
        if not result.is_usable:
            warnings.append(
                f"no ingester produced a usable result for {pdf_path.name} "
                f"(ingester={result.ingester_name}, error={result.error})"
            )
            continue
        apply_partial_to_dict(result.partial, base)

    # ------------------------------------------------------------------
    # 3. Validate assembled dict and run compute()
    # ------------------------------------------------------------------
    canonical = CanonicalReturn.model_validate(base)
    canonical = compute(canonical)

    # ------------------------------------------------------------------
    # 4. Render federal PDFs
    # ------------------------------------------------------------------
    rendered: list[Path] = []

    if render_form_1040:
        from skill.scripts.output.form_1040 import (
            compute_form_1040_fields,
            render_form_1040_pdf,
        )

        fields = compute_form_1040_fields(canonical)
        out_path = output_dir / "form_1040.pdf"
        render_form_1040_pdf(fields, out_path)
        rendered.append(out_path)

    if render_schedule_a and canonical.itemize_deductions and canonical.itemized is not None:
        from skill.scripts.output.schedule_a import (
            compute_schedule_a_fields,
            render_schedule_a_pdf,
        )

        fields_a = compute_schedule_a_fields(canonical)
        out_path_a = output_dir / "schedule_a.pdf"
        render_schedule_a_pdf(fields_a, out_path_a)
        rendered.append(out_path_a)

    if render_schedule_b:
        from skill.scripts.output.schedule_b import (
            compute_schedule_b_fields,
            render_schedule_b_pdf,
            schedule_b_required,
        )

        if schedule_b_required(canonical):
            fields_b = compute_schedule_b_fields(canonical)
            out_path_b = output_dir / "schedule_b.pdf"
            render_schedule_b_pdf(fields_b, out_path_b)
            rendered.append(out_path_b)

    if render_schedule_c and canonical.schedules_c:
        from skill.scripts.output.schedule_c import render_schedule_c_pdfs_all

        sch_c_paths = render_schedule_c_pdfs_all(canonical, output_dir)
        rendered.extend(sch_c_paths)

    if render_schedule_se:
        from skill.scripts.output.schedule_se import (
            compute_schedule_se_fields,
            render_schedule_se_pdf,
            schedule_se_required,
        )

        if schedule_se_required(canonical):
            fields_se = compute_schedule_se_fields(canonical)
            out_path_se = output_dir / "schedule_se.pdf"
            render_schedule_se_pdf(fields_se, out_path_se)
            rendered.append(out_path_se)

    # ------------------------------------------------------------------
    # 5. Emit result.json
    # ------------------------------------------------------------------
    result = PipelineResult(
        canonical_return=canonical,
        ingest_results=ingest_results,
        rendered_paths=rendered,
        warnings=warnings,
    )
    result.write_result_json(output_dir / "result.json")
    return result
