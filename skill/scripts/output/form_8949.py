"""Form 8949 (Sales and Other Dispositions of Capital Assets) renderer.

Two-layer design mirroring ``skill.scripts.output.schedule_b``:

* Layer 1 — :func:`compute_form_8949_fields` walks every
  ``CanonicalReturn.forms_1099_b[*].transactions[*]`` entry, classifies
  each as short-term (Part I box A/B/C) or long-term (Part II box D/E/F)
  based on ``is_long_term`` + ``basis_reported_to_irs`` (or the explicit
  override ``form_8949_box_code``), and emits a frozen ``Form8949Fields``
  dataclass that carries per-row detail plus per-box totals.

* Layer 2 — :func:`render_form_8949_pdf` loads the widget map, verifies
  the source PDF SHA-256, and overlays the values via the shared
  ``fill_acroform_pdf`` helper.

Per IRS instructions, the form requires a SEPARATE 8949 page for each
box A/B/C that carries rows (short-term) and another for each D/E/F
(long-term). Since a canonical return can mix boxes, Layer 1 groups
rows by box and emits a list of ``Form8949Page`` snapshots — one per
(box_code -> nonempty row list) — and the renderer writes one filled
PDF per page. For the common single-box case this collapses to a
single output file.

Simplifications (deferred to later waves)
-----------------------------------------
* 11 rows per page max; beyond that a continuation statement is needed.
  Layer 2 truncates and records a warning in ``Form8949Fields.warnings``.
* Wash-sale adjustments are carried through as a 'W' code in column (f)
  plus a positive adjustment amount in column (g). Other codes from the
  1099-B (B, T, L, etc.) are passed through as-is if present in
  ``adjustment_codes``.
* The digital-asset boxes G/H/I/J/K/L are not yet populated — those
  require a Form 1099-DA model which doesn't exist yet.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal

from skill.scripts.models import CanonicalReturn, Form1099BTransaction


_ZERO = Decimal("0")
MAX_ROWS_PER_PAGE = 11

# Ordered list of box codes in the order they appear as checkboxes on the
# IRS PDF (top to bottom). Short-term = A..I, long-term = D..L. We only
# expose the non-digital-asset subset (A/B/C and D/E/F) until a Form
# 1099-DA model lands.
_SHORT_TERM_BOXES = ("A", "B", "C")
_LONG_TERM_BOXES = ("D", "E", "F")
_ALL_BOXES = _SHORT_TERM_BOXES + _LONG_TERM_BOXES


# ---------------------------------------------------------------------------
# Layer 1 dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Form8949Row:
    """One transaction row on Form 8949.

    Fields mirror the form columns (a) through (h). All money amounts
    are ``Decimal``; dates are rendered as ``MM/DD/YYYY`` strings.
    """

    description: str  # column (a)
    date_acquired: str  # column (b) — "" or "VARIOUS" allowed
    date_sold: str  # column (c)
    proceeds: Decimal  # column (d)
    cost_basis: Decimal  # column (e)
    adjustment_code: str  # column (f) — e.g. "W" for wash sale
    adjustment_amount: Decimal  # column (g)
    gain_loss: Decimal  # column (h) = (d) - (e) + (g)


@dataclass(frozen=True)
class Form8949Page:
    """One (part, box) page of Form 8949.

    A page corresponds to the IRS instruction "complete a separate Form
    8949, page 1, for each applicable box" — so each Form8949Page
    becomes one rendered PDF output.
    """

    part: Literal["I", "II"]  # Part I = short-term, Part II = long-term
    box_code: Literal["A", "B", "C", "D", "E", "F"]
    rows: tuple[Form8949Row, ...]
    total_proceeds: Decimal
    total_cost_basis: Decimal
    total_adjustment_amount: Decimal
    total_gain_loss: Decimal
    overflow_row_count: int = 0
    """Number of rows beyond the 11-row MAX_ROWS_PER_PAGE limit that
    were dropped. 0 in the normal case; > 0 means the renderer
    silently truncated (a continuation statement is required for
    those; flagged in Form8949Fields.warnings)."""


@dataclass(frozen=True)
class Form8949Fields:
    """Frozen snapshot of every Form 8949 page this return needs."""

    taxpayer_name: str = ""
    taxpayer_ssn: str = ""
    pages: tuple[Form8949Page, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def is_required(self) -> bool:
        """True iff at least one page has at least one row."""
        return any(p.rows for p in self.pages)

    @property
    def short_term_pages(self) -> tuple[Form8949Page, ...]:
        return tuple(p for p in self.pages if p.part == "I")

    @property
    def long_term_pages(self) -> tuple[Form8949Page, ...]:
        return tuple(p for p in self.pages if p.part == "II")


# ---------------------------------------------------------------------------
# Layer 1 computation
# ---------------------------------------------------------------------------


def _classify_box(txn: Form1099BTransaction) -> str:
    """Pick an 8949 box code for a single transaction.

    The explicit ``form_8949_box_code`` override wins; otherwise the
    decision is driven by ``is_long_term`` and ``basis_reported_to_irs``.
    """
    if txn.form_8949_box_code is not None:
        return txn.form_8949_box_code
    if txn.is_long_term:
        return "D" if txn.basis_reported_to_irs else "E"
    return "A" if txn.basis_reported_to_irs else "B"


def _format_date(d) -> str:
    """Format a date field for Form 8949 columns (b) and (c)."""
    if d is None:
        return ""
    if isinstance(d, str):
        return d.upper()  # "various" -> "VARIOUS"
    return d.strftime("%m/%d/%Y")


def _txn_to_row(txn: Form1099BTransaction) -> Form8949Row:
    """Convert a single 1099-B transaction to a Form8949Row.

    Wash-sale disallowed loss (1099-B box 1g) becomes a column (g)
    adjustment amount with code 'W'. Per IRS instructions, the
    adjustment amount is POSITIVE (it reduces an otherwise-reportable
    loss). That matches the sign convention used by the engine.
    """
    proceeds = txn.proceeds
    cost_basis = txn.cost_basis
    wash_sale = txn.wash_sale_loss_disallowed
    other_adj = txn.adjustment_amount

    adj_amount = wash_sale + other_adj

    codes: list[str] = list(txn.adjustment_codes)
    if wash_sale > _ZERO and "W" not in codes:
        codes.append("W")
    adj_code = ",".join(codes)

    gain_loss = proceeds - cost_basis + adj_amount

    return Form8949Row(
        description=txn.description,
        date_acquired=_format_date(txn.date_acquired),
        date_sold=_format_date(txn.date_sold),
        proceeds=proceeds,
        cost_basis=cost_basis,
        adjustment_code=adj_code,
        adjustment_amount=adj_amount,
        gain_loss=gain_loss,
    )


def compute_form_8949_fields(return_: CanonicalReturn) -> Form8949Fields:
    """Build a Form8949Fields snapshot from the canonical return.

    Groups every 1099-B transaction by (part, box_code), sums per-page
    totals, and enforces the 11-row-per-page cap (records overflow in
    ``warnings``).
    """
    taxpayer_name = f"{return_.taxpayer.first_name} {return_.taxpayer.last_name}"
    taxpayer_ssn = return_.taxpayer.ssn or ""

    # {box_code: [Form8949Row, ...]}
    by_box: dict[str, list[Form8949Row]] = {box: [] for box in _ALL_BOXES}

    for form in return_.forms_1099_b:
        for txn in form.transactions:
            box = _classify_box(txn)
            if box not in by_box:
                # Defensive: an unknown code from a later-wave 1099-DA
                # model should not crash this module.
                continue
            by_box[box].append(_txn_to_row(txn))

    pages: list[Form8949Page] = []
    warnings: list[str] = []

    for box in _ALL_BOXES:
        rows = by_box[box]
        if not rows:
            continue
        part: Literal["I", "II"] = "I" if box in _SHORT_TERM_BOXES else "II"

        overflow = max(0, len(rows) - MAX_ROWS_PER_PAGE)
        kept = tuple(rows[:MAX_ROWS_PER_PAGE])
        if overflow > 0:
            warnings.append(
                f"Form 8949 Part {part} box {box}: {overflow} rows "
                f"dropped beyond the {MAX_ROWS_PER_PAGE}-row page cap; "
                f"a continuation statement is required."
            )

        # Totals are computed across ALL rows including overflow so that
        # downstream Schedule D aggregation remains correct even when a
        # page is visually truncated.
        total_proceeds = sum((r.proceeds for r in rows), start=_ZERO)
        total_cost_basis = sum((r.cost_basis for r in rows), start=_ZERO)
        total_adjustment = sum((r.adjustment_amount for r in rows), start=_ZERO)
        total_gain_loss = sum((r.gain_loss for r in rows), start=_ZERO)

        pages.append(
            Form8949Page(
                part=part,
                box_code=box,  # type: ignore[arg-type]
                rows=kept,
                total_proceeds=total_proceeds,
                total_cost_basis=total_cost_basis,
                total_adjustment_amount=total_adjustment,
                total_gain_loss=total_gain_loss,
                overflow_row_count=overflow,
            )
        )

    return Form8949Fields(
        taxpayer_name=taxpayer_name,
        taxpayer_ssn=taxpayer_ssn,
        pages=tuple(pages),
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Layer 2 — AcroForm overlay PDF rendering
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parents[3]
_FORM_8949_MAP_PATH = (
    _REPO_ROOT / "skill" / "reference" / "form-8949-acroform-map.json"
)
_FORM_8949_PDF_PATH = (
    _REPO_ROOT / "skill" / "reference" / "irs_forms" / "f8949.pdf"
)


def _format_decimal(value: Decimal) -> str:
    """Format a Decimal as plain "1500.00"; zero collapses to empty."""
    q = value.quantize(Decimal("0.01"))
    if q == Decimal("0.00"):
        return ""
    return f"{q:.2f}"


# Box-code -> checkbox index within the 6-checkbox group on each page.
# Part I (page 1): A=0, B=1, C=2, G=3, H=4, I=5.
# Part II (page 2): D=0, E=1, F=2, J=3, K=4, L=5.
_BOX_CHECKBOX_INDEX = {
    "A": 0, "B": 1, "C": 2,
    "D": 0, "E": 1, "F": 2,
}


def _build_widget_values_for_page(
    fields: Form8949Fields,
    page: Form8949Page,
    widget_map: dict,
) -> dict[str, str | bool]:
    """Translate a single Form8949Page into a widget_name -> value dict."""
    mapping = widget_map["mapping"]
    out: dict[str, str | bool] = {}

    part_prefix = "part_1" if page.part == "I" else "part_2"
    rows_key = "part_1_row_widgets" if page.part == "I" else "part_2_row_widgets"
    checkbox_key = (
        "part_1_box_checkboxes" if page.part == "I" else "part_2_box_checkboxes"
    )

    # Header name/SSN on the appropriate page.
    out[mapping[f"{part_prefix}_taxpayer_name"]["widget_name"]] = fields.taxpayer_name
    out[mapping[f"{part_prefix}_taxpayer_ssn"]["widget_name"]] = fields.taxpayer_ssn

    # Box checkbox — exactly one per page.
    cboxes = widget_map[checkbox_key]
    idx = _BOX_CHECKBOX_INDEX[page.box_code]
    out[cboxes[idx]["widget_name"]] = True

    # Row widgets.
    row_widgets = widget_map[rows_key]
    for i, row in enumerate(page.rows):
        if i >= len(row_widgets):
            break
        slot = row_widgets[i]
        out[slot["description_widget"]["widget_name"]] = row.description
        out[slot["date_acquired_widget"]["widget_name"]] = row.date_acquired
        out[slot["date_sold_widget"]["widget_name"]] = row.date_sold
        out[slot["proceeds_widget"]["widget_name"]] = _format_decimal(row.proceeds)
        out[slot["cost_basis_widget"]["widget_name"]] = _format_decimal(row.cost_basis)
        out[slot["adjustment_code_widget"]["widget_name"]] = row.adjustment_code
        out[slot["adjustment_amount_widget"]["widget_name"]] = _format_decimal(
            row.adjustment_amount
        )
        out[slot["gain_loss_widget"]["widget_name"]] = _format_decimal(row.gain_loss)

    # Line 2 totals row (columns d/e/f/g/h).
    out[mapping[f"{part_prefix}_line_2_proceeds"]["widget_name"]] = _format_decimal(
        page.total_proceeds
    )
    out[mapping[f"{part_prefix}_line_2_cost_basis"]["widget_name"]] = _format_decimal(
        page.total_cost_basis
    )
    # Column (f) is the adjustment CODE cell — totals row shows nothing.
    out[mapping[f"{part_prefix}_line_2_adjustment_code"]["widget_name"]] = ""
    out[mapping[f"{part_prefix}_line_2_adjustment_amount"]["widget_name"]] = (
        _format_decimal(page.total_adjustment_amount)
    )
    out[mapping[f"{part_prefix}_line_2_gain_loss"]["widget_name"]] = _format_decimal(
        page.total_gain_loss
    )

    return out


def render_form_8949_pdf(
    fields: Form8949Fields, out_path: Path
) -> list[Path]:
    """Render one or more filled Form 8949 PDFs.

    Emits one PDF per non-empty ``Form8949Page`` in ``fields.pages``.
    For a return with a single box code (the common case) this is a
    single file at ``out_path``. For multi-box returns the file stem
    is extended with the box code:

        out_path = .../form_8949.pdf

        single box A  -> [.../form_8949.pdf]
        boxes A + D   -> [.../form_8949_A.pdf, .../form_8949_D.pdf]

    Returns the list of written paths.
    """
    from skill.scripts.output._acroform_overlay import (
        fill_acroform_pdf,
        load_widget_map_as_dict,
        verify_pdf_sha256,
    )

    widget_map = load_widget_map_as_dict(_FORM_8949_MAP_PATH)
    verify_pdf_sha256(_FORM_8949_PDF_PATH, widget_map["source_pdf_sha256"])

    pages = [p for p in fields.pages if p.rows]
    if not pages:
        return []

    out_path = Path(out_path)
    written: list[Path] = []

    if len(pages) == 1:
        only = pages[0]
        values = _build_widget_values_for_page(fields, only, widget_map)
        fill_acroform_pdf(_FORM_8949_PDF_PATH, values, out_path)
        written.append(out_path)
        return written

    # Multi-box: one file per box, suffixed with the box code.
    for page in pages:
        suffixed = out_path.with_name(
            f"{out_path.stem}_{page.box_code}{out_path.suffix}"
        )
        values = _build_widget_values_for_page(fields, page, widget_map)
        fill_acroform_pdf(_FORM_8949_PDF_PATH, values, suffixed)
        written.append(suffixed)
    return written


__all__ = [
    "MAX_ROWS_PER_PAGE",
    "Form8949Fields",
    "Form8949Page",
    "Form8949Row",
    "compute_form_8949_fields",
    "render_form_8949_pdf",
]
