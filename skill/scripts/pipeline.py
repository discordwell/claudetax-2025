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

from decimal import Decimal

from skill.scripts.calc.engine import compute
from skill.scripts.ingest._pipeline import (
    DocumentKind,
    IngestResult,
    IngestCascade,
    PartialReturn,
)
from skill.scripts.models import (
    CanonicalReturn,
    ResidencyStatus,
    StateCode,
    StateReturn,
)


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


def _reindex_partial_paths(
    partial: PartialReturn, base: dict[str, Any]
) -> list[tuple[str, Any]]:
    """Rewrite list indices in ``partial`` to append after existing items.

    Every Tier-1 ingester hardcodes index 0 (e.g., ``w2s[0].box1_wages``).
    When the pipeline processes multiple PDFs of the same type, the second
    PDF's ``w2s[0]`` would clobber the first. This function detects list-
    rooted paths, computes an offset equal to the current list length in
    ``base``, and rewrites the index so the new document lands at the end.

    Returns a list of (rewritten_path, value) pairs.
    """
    offsets: dict[str, int] = {}
    result: list[tuple[str, Any]] = []
    for extraction in partial.fields:
        path = extraction.path
        if path.startswith("_acroform_raw."):
            continue
        segments = _parse_path(path)
        if segments and segments[0][1] is not None:
            root_key = segments[0][0]
            if root_key not in offsets:
                existing = base.get(root_key, [])
                offsets[root_key] = len(existing) if isinstance(existing, list) else 0
            offset = offsets[root_key]
            if offset > 0:
                old_idx = segments[0][1]
                new_idx = old_idx + offset
                old_prefix = f"{root_key}[{old_idx}]"
                new_prefix = f"{root_key}[{new_idx}]"
                path = new_prefix + path[len(old_prefix):]
        result.append((path, extraction.value))
    return result


def apply_partial_to_dict(
    partial: PartialReturn, base: dict[str, Any]
) -> dict[str, Any]:
    """Apply every ``FieldExtraction`` in ``partial`` onto ``base`` in place.

    List-rooted paths (e.g., ``w2s[0].*``) are reindexed via
    ``_reindex_partial_paths`` so multiple PDFs of the same type append
    to the list rather than clobbering index 0.

    Silently skips pseudo-paths that start with ``_acroform_raw.``.

    Returns the same ``base`` dict for chaining convenience.
    """
    for path, value in _reindex_partial_paths(partial, base):
        _set_path(base, path, value)
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
    from skill.scripts.ingest._1098_acroform import INGESTER as I_1098
    from skill.scripts.ingest._1098_e_acroform import INGESTER as I_1098_E
    from skill.scripts.ingest._1098_t_acroform import INGESTER as I_1098_T
    from skill.scripts.ingest._1099_b_acroform import INGESTER as I_1099_B
    from skill.scripts.ingest._1099_div_acroform import INGESTER as I_1099_DIV
    from skill.scripts.ingest._1099_g_acroform import INGESTER as I_1099_G
    from skill.scripts.ingest._1099_int_acroform import INGESTER as I_1099_INT
    from skill.scripts.ingest._1099_k_acroform import INGESTER as I_1099_K
    from skill.scripts.ingest._1099_misc_acroform import INGESTER as I_1099_MISC
    from skill.scripts.ingest._1099_nec_acroform import INGESTER as I_1099_NEC
    from skill.scripts.ingest._1099_r_acroform import INGESTER as I_1099_R
    from skill.scripts.ingest._schedule_k1_acroform import INGESTER as I_K1
    from skill.scripts.ingest._ssa_1099_acroform import INGESTER as I_SSA_1099
    from skill.scripts.ingest._w2_acroform import INGESTER as I_W2

    tier_1: list[Any] = [
        I_W2,
        I_1099_INT,
        I_1099_DIV,
        I_1099_B,
        I_1099_NEC,
        I_1099_MISC,
        I_1099_K,
        I_1099_R,
        I_1099_G,
        I_SSA_1099,
        I_K1,
        I_1098,
        I_1098_E,
        I_1098_T,
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
    ``rendered_paths`` for on-disk PDF locations. ``state_returns``
    holds the per-state plugin output when state dispatch ran.
    """

    canonical_return: CanonicalReturn
    ingest_results: list[IngestResult] = field(default_factory=list)
    rendered_paths: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    state_returns: list[StateReturn] = field(default_factory=list)

    @property
    def validation_report(self) -> dict[str, Any] | None:
        return self.canonical_return.computed.validation_report

    def write_result_json(self, path: Path) -> None:
        """Serialize ``canonical_return`` as a JSON file at ``path``."""
        data = self.canonical_return.model_dump(mode="json")
        path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# State plugin dispatch helpers
# ---------------------------------------------------------------------------


def _relevant_state_codes(canonical: CanonicalReturn) -> list[StateCode]:
    """Return the ordered set of state codes this return touches.

    Resident state appears first; every other state that shows up in a
    W-2 ``state_rows[]`` entry follows, in first-seen order, de-duped.
    """
    seen: list[StateCode] = []
    resident = canonical.address.state
    seen.append(resident)
    for w2 in canonical.w2s:
        for row in w2.state_rows:
            if row.state not in seen:
                seen.append(row.state)
    return seen


def _build_federal_totals(canonical: CanonicalReturn) -> "FederalTotals":
    """Build the ``FederalTotals`` struct a state plugin consumes.

    Imported locally so the plugin-api dependency stays out of module
    load order (keeps ``run_pipeline`` importable without the states
    package eagerly loading).
    """
    from skill.scripts.states._plugin_api import FederalTotals

    c = canonical.computed
    total_tax = c.total_tax or Decimal("0")
    tentative_tax = c.tentative_tax or Decimal("0")
    agi = c.adjusted_gross_income or Decimal("0")
    ti = c.taxable_income or Decimal("0")
    deduction_taken = c.deduction_taken or Decimal("0")
    # Standard deduction numbers live in ty2025-constants.json. For
    # FederalTotals we just report what actually reduced AGI; if the
    # filer itemized, federal_itemized_deductions_total comes from the
    # itemized block's SALT-capped sum (approximated here as
    # deduction_taken when itemize_deductions is True).
    if canonical.itemize_deductions:
        itemized_total = deduction_taken
        std_ded = Decimal("0")
    else:
        itemized_total = Decimal("0")
        std_ded = deduction_taken

    fed_wh = sum(
        (w2.box2_federal_income_tax_withheld for w2 in canonical.w2s),
        start=Decimal("0"),
    )
    return FederalTotals(
        filing_status=canonical.filing_status,
        num_dependents=len(canonical.dependents),
        adjusted_gross_income=agi,
        taxable_income=ti,
        total_federal_tax=total_tax,
        federal_income_tax=tentative_tax,
        federal_standard_deduction=std_ded,
        federal_itemized_deductions_total=itemized_total,
        deduction_taken=deduction_taken,
        federal_withholding_from_w2s=fed_wh,
        qbi_deduction=c.qbi_deduction or Decimal("0"),
        se_tax=canonical.other_taxes.self_employment_tax,
        additional_medicare_tax=canonical.other_taxes.additional_medicare_tax,
        niit=canonical.other_taxes.net_investment_income_tax,
    )


def _dispatch_state_plugins(
    canonical: CanonicalReturn,
    output_dir: Path,
) -> tuple[list[StateReturn], list[Path], list[str]]:
    """Run every relevant state plugin for ``canonical``.

    Returns a tuple of (state_returns, rendered_paths, warnings). This
    is the first place in the codebase that walks the state registry
    on behalf of a real return — CP8-E originally punted on this, and
    wave 6 owns wiring it up.

    Residency rule: the resident state is whichever state
    ``canonical.address.state`` points at; every other state found in
    ``W2.state_rows[]`` is treated as NONRESIDENT with ``days_in_state``
    of 0 (a future wave will add real part-year / day-count tracking).
    Plugins for states that are not registered get a warning but do not
    abort the pipeline.
    """
    from skill.scripts.states._registry import registry

    state_returns: list[StateReturn] = []
    rendered: list[Path] = []
    warnings: list[str] = []

    federal = _build_federal_totals(canonical)
    resident_state = canonical.address.state
    codes = _relevant_state_codes(canonical)

    for code in codes:
        if not registry.has(code):
            warnings.append(
                f"no state plugin registered for {code!r}; skipping"
            )
            continue
        plugin = registry.get(code)

        if code == resident_state:
            residency = ResidencyStatus.RESIDENT
            days_in_state = 365
        else:
            residency = ResidencyStatus.NONRESIDENT
            # Real day counts come from a future per-state
            # day-tracking field on canonical; for now treat every
            # non-resident as zero-days (the state-row path bypasses
            # day_prorate entirely when state rows are present).
            days_in_state = 0

        try:
            state_return = plugin.compute(
                canonical, federal, residency, days_in_state
            )
        except Exception as exc:  # pragma: no cover - defensive
            warnings.append(
                f"state plugin {code!r} compute raised {type(exc).__name__}: {exc}"
            )
            continue
        state_returns.append(state_return)

        try:
            paths = plugin.render_pdfs(state_return, output_dir)
        except Exception as exc:  # pragma: no cover - defensive
            warnings.append(
                f"state plugin {code!r} render_pdfs raised "
                f"{type(exc).__name__}: {exc}"
            )
            paths = []
        rendered.extend(paths)

    return state_returns, rendered, warnings


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
    render_schedule_d: bool = True,
    render_form_8949: bool = True,
    render_schedule_se: bool = True,
    render_form_6251: bool = True,
    render_form_4562: bool = True,
    render_form_8829: bool = True,
    render_form_2441: bool = True,
    render_form_8606: bool = True,
    render_form_8863: bool = True,
    render_form_8962: bool = True,
    render_form_4797: bool = True,
    render_schedule_1: bool = True,
    render_schedule_2: bool = True,
    render_schedule_3: bool = True,
    render_schedule_e: bool = True,
    render_state_returns: bool = True,
    build_paper_bundle: bool = True,
    emit_ffff_map: bool = True,
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
    render_form_1040, render_schedule_a, ..., render_schedule_se, render_form_4562
        Per-form render gates. Default ``True`` renders every applicable
        federal form. Schedule B is skipped if ``schedule_b_required``
        returns False; Schedule C is skipped if no schedules_c are
        present; Schedule D (and the companion Form 8949 pages) are
        skipped if there are no 1099-B transactions and no cap gain
        distributions; Schedule SE is skipped if SE net earnings are
        under the $400 filing floor; Form 4562 is rendered per
        Schedule C that has ``depreciable_assets`` populated.
    build_paper_bundle
        When ``True`` (default), assemble all rendered federal PDFs into
        a single ``paper_bundle.pdf`` inside ``output_dir`` (cover sheet
        + ordered forms + signature page + mailing instructions). Pass
        ``False`` to skip bundle assembly — useful when the caller only
        wants the loose form PDFs. The bundle path is appended to
        ``PipelineResult.rendered_paths`` when built.
    emit_ffff_map
        When ``True`` (default), write the FFFF entry transcript to
        ``output_dir / 'ffff_entries.json'`` and
        ``output_dir / 'ffff_entries.txt'``. These files are the
        field-by-field script a taxpayer follows to type their return
        into freefillableforms.com. Pass ``False`` to skip emission.

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
    # 3b. State plugin dispatch (wave 6)
    # ------------------------------------------------------------------
    state_returns: list[StateReturn] = []
    state_rendered: list[Path] = []
    if render_state_returns:
        state_returns, state_rendered, state_warnings = _dispatch_state_plugins(
            canonical, output_dir
        )
        warnings.extend(state_warnings)
        # Persist dispatched state returns onto the canonical return so
        # they survive the result.json serialization (which goes through
        # ``canonical.model_dump``). Without this the state_returns field
        # on the wire is always empty even when plugins computed real
        # numbers.
        canonical = canonical.model_copy(update={"state_returns": state_returns})

    # ------------------------------------------------------------------
    # 4. Render federal PDFs
    # ------------------------------------------------------------------
    rendered: list[Path] = list(state_rendered)

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

    # Form 4562 — one per Schedule C with depreciable_assets.
    if render_form_4562 and canonical.schedules_c:
        any_has_assets = any(sc.depreciable_assets for sc in canonical.schedules_c)
        if any_has_assets:
            from skill.scripts.output.form_4562 import render_form_4562_pdfs_all

            f4562_paths = render_form_4562_pdfs_all(canonical, output_dir)
            rendered.extend(f4562_paths)

    if render_form_8829 and canonical.schedules_c:
        # Emit one Form 8829 per regular-method home office. Simplified-
        # method home offices do not file a Form 8829 (the deduction is
        # reported directly on Schedule C line 30).
        from skill.scripts.output.form_8829 import render_form_8829_pdfs_all

        f8829_paths = render_form_8829_pdfs_all(canonical, output_dir)
        rendered.extend(f8829_paths)

    if render_schedule_3:
        from skill.scripts.output.schedule_3 import (
            compute_schedule_3_fields,
            render_schedule_3_pdf,
            schedule_3_required,
        )

        if schedule_3_required(canonical):
            fields_3 = compute_schedule_3_fields(canonical)
            out_path_3 = output_dir / "schedule_3.pdf"
            render_schedule_3_pdf(fields_3, out_path_3)
            rendered.append(out_path_3)

    if render_schedule_e and canonical.schedules_e:
        from skill.scripts.output.schedule_e import render_schedule_e_pdfs_all

        sch_e_paths = render_schedule_e_pdfs_all(canonical, output_dir)
        rendered.extend(sch_e_paths)

    # Form 8863 renders when the taxpayer has education credit data.
    if render_form_8863 and canonical.education is not None:
        from skill.scripts.output.form_8863 import (
            compute_form_8863_fields,
            render_form_8863_pdf,
        )

        fields_8863 = compute_form_8863_fields(canonical)
        canonical.credits.education_credits_nonrefundable = fields_8863.total_nonrefundable
        canonical.credits.education_credits_refundable = fields_8863.total_refundable
        out_path_8863 = output_dir / "form_8863.pdf"
        render_form_8863_pdf(fields_8863, out_path_8863)
        rendered.append(out_path_8863)

    # Form 4797 — Sales of Business Property
    if render_form_4797 and canonical.forms_4797:
        from skill.scripts.output.form_4797 import (
            compute_form_4797_fields,
            render_form_4797_pdf,
        )

        fields_4797 = compute_form_4797_fields(canonical)
        out_path_4797 = output_dir / "form_4797.pdf"
        render_form_4797_pdf(fields_4797, out_path_4797)
        rendered.append(out_path_4797)

    if render_schedule_d:
        from skill.scripts.output.schedule_d import (
            compute_schedule_d_fields,
            render_schedule_d_pdf,
            schedule_d_required,
        )
        from skill.scripts.output.form_8949 import render_form_8949_pdf

        if schedule_d_required(canonical):
            fields_d = compute_schedule_d_fields(canonical)
            out_path_d = output_dir / "schedule_d.pdf"
            render_schedule_d_pdf(fields_d, out_path_d)
            rendered.append(out_path_d)

            # Render the companion Form 8949 pages (one per box code
            # present). The renderer returns a list — possibly empty
            # if Schedule D only has cap gain distributions. Gated by
            # the render_form_8949 flag so callers can suppress 8949
            # independently of Schedule D.
            if render_form_8949:
                f8949_paths = render_form_8949_pdf(
                    fields_d.form_8949_fields, output_dir / "form_8949.pdf"
                )
                rendered.extend(f8949_paths)

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

    if render_schedule_1:
        from skill.scripts.output.schedule_1 import (
            compute_schedule_1_fields,
            render_schedule_1_pdf,
            schedule_1_required,
        )

        if schedule_1_required(canonical):
            fields_s1 = compute_schedule_1_fields(canonical)
            out_path_s1 = output_dir / "schedule_1.pdf"
            render_schedule_1_pdf(fields_s1, out_path_s1)
            rendered.append(out_path_s1)

    # Form 6251 renders ONLY when the engine actually computed a
    # nonzero AMT — most returns will not. The trigger already fired
    # inside engine.compute(), so we simply check the result.
    if render_form_6251:
        amt_val = canonical.computed.alternative_minimum_tax
        if amt_val is not None and amt_val > 0:
            from skill.scripts.output.form_6251 import (
                compute_form_6251_fields,
                render_form_6251_pdf,
            )

            fields_6251 = compute_form_6251_fields(canonical)
            out_path_6251 = output_dir / "form_6251.pdf"
            render_form_6251_pdf(fields_6251, out_path_6251)
            rendered.append(out_path_6251)

    # Form 2441 renders when the taxpayer has dependent_care data.
    # Compute the credit and store it, then render the PDF scaffold.
    if render_form_2441 and canonical.dependent_care is not None:
        from skill.scripts.output.form_2441 import (
            compute_form_2441_fields,
            render_form_2441_pdf,
        )

        fields_2441 = compute_form_2441_fields(canonical)
        canonical.credits.dependent_care_credit = fields_2441.line_10_credit
        out_path_2441 = output_dir / "form_2441.pdf"
        render_form_2441_pdf(fields_2441, out_path_2441)
        rendered.append(out_path_2441)

    # Form 8606 renders when the taxpayer has IRA basis info.
    if render_form_8606 and canonical.ira_info is not None:
        from skill.scripts.output.form_8606 import (
            compute_form_8606_fields,
            render_form_8606_pdf,
        )

        fields_8606 = compute_form_8606_fields(canonical)
        out_path_8606 = output_dir / "form_8606.pdf"
        render_form_8606_pdf(fields_8606, out_path_8606)
        rendered.append(out_path_8606)

    # Form 8962 renders when the filer has 1095-A marketplace data.
    if render_form_8962 and canonical.forms_1095_a:
        from skill.scripts.output.form_8962 import (
            compute_form_8962_fields,
            render_form_8962_pdf,
        )

        fields_8962 = compute_form_8962_fields(canonical)
        out_path_8962 = output_dir / "form_8962.pdf"
        render_form_8962_pdf(fields_8962, out_path_8962)
        rendered.append(out_path_8962)

    # Schedule 2 — Additional Taxes. Rendered when any of the source
    # taxes (AMT, SE, additional Medicare, NIIT, early distribution
    # penalty) are nonzero.
    if render_schedule_2:
        from skill.scripts.output.schedule_2 import (
            compute_schedule_2_fields,
            render_schedule_2_pdf,
            schedule_2_required,
        )

        if schedule_2_required(canonical):
            fields_2 = compute_schedule_2_fields(canonical)
            out_path_2 = output_dir / "schedule_2.pdf"
            render_schedule_2_pdf(fields_2, out_path_2)
            rendered.append(out_path_2)

    # ------------------------------------------------------------------
    # 5. Emit result.json
    # ------------------------------------------------------------------
    result = PipelineResult(
        canonical_return=canonical,
        ingest_results=ingest_results,
        rendered_paths=rendered,
        warnings=warnings,
        state_returns=state_returns,
    )
    result.write_result_json(output_dir / "result.json")

    # ------------------------------------------------------------------
    # 6. Build paper bundle (cover sheet + forms + sig + mailing).
    #
    # Runs AFTER all federal PDFs are rendered and AFTER result.json is
    # emitted so a bundle-assembly failure leaves loose PDFs on disk for
    # forensic inspection. State-return PDFs produced earlier in the
    # state-dispatch block are filtered out by ``order_forms`` via the
    # ``state_`` filename prefix so the federal envelope stays federal.
    # ------------------------------------------------------------------
    if build_paper_bundle and rendered:
        from skill.scripts.output.paper_bundle import (
            build_paper_bundle as _build_paper_bundle,
        )

        bundle_path = output_dir / "paper_bundle.pdf"
        _build_paper_bundle(canonical, rendered, bundle_path)
        rendered.append(bundle_path)

    # ------------------------------------------------------------------
    # 7. Emit FFFF entry map (JSON + human-readable transcript).
    #
    # The FFFF entry map is a field-by-field transcript a taxpayer
    # follows to type their return into freefillableforms.com. It is
    # produced from the same layer-1 field dataclasses the federal
    # renderers use, so numbers stay bit-for-bit consistent.
    # ------------------------------------------------------------------
    if emit_ffff_map:
        from skill.scripts.output.ffff_entry_map import build_ffff_entry_map

        entry_map = build_ffff_entry_map(canonical)
        (output_dir / "ffff_entries.json").write_text(entry_map.to_json())
        (output_dir / "ffff_entries.txt").write_text(entry_map.to_text())

    return result
