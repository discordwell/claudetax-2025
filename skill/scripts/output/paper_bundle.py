"""Paper-file bundle generator — assembles a single mailable PDF.

This is the wave-5 fallback for taxpayers whose returns cannot be e-filed
through Free File Fillable Forms (FFFF). It glues together:

* a generated **cover sheet** (taxpayer ID, summary table, file status,
  FFFF compatibility status with blockers listed),
* the **input PDFs** rendered by ``skill.scripts.output.{form_1040, schedule_a,
  schedule_b, schedule_c, schedule_se, ...}``, ordered the way the IRS
  expects on a paper-mailed return,
* a generated **signature page** (sign-here markers, occupation, date),
* a generated **mailing instructions page** (IRS service-center address
  pulled from ``skill/reference/irs-mailing-addresses.json`` keyed on the
  taxpayer's state and whether a payment is enclosed).

Public surface
--------------
``build_paper_bundle(canonical_return, rendered_pdf_paths, out_path)`` is
the only function callers need. Everything else (cover sheet rendering,
ordering, mailing-address lookup) is a private helper exposed for tests.

Design notes
------------
* **No tax recomputation.** This module trusts ``canonical_return.computed``
  to be populated by ``skill.scripts.calc.engine.compute``. If a caller
  passes in an uncomputed return, the cover sheet will print zeros / a
  helpful "(not computed)" placeholder rather than crash.
* **Pure pypdf merge.** The cover/signature/mailing pages are written as
  scratch PDFs via reportlab, then merged into the final bundle with
  ``pypdf.PdfWriter.append_pages_from_reader``. The temp files are kept
  inside ``out_path.parent`` (or a system tempdir if the parent is
  read-only) and cleaned up on success.
* **Form ordering** follows the IRS paper-file convention listed in the
  Form 1040 instructions: 1040 first, then numbered schedules in
  attachment-sequence order, then alphabetical "other" forms.
* **State return PDFs** are NOT included in the federal bundle — they
  must be mailed separately to the state DOR. Wave-5 state PDFs are
  detected by filename and ignored (they belong in a parallel state
  bundle, which is a follow-up).
* **Schedule D / Form 8949 / Schedule E** are not produced by wave-5
  renderers; if a future wave drops them under
  ``schedules_d.pdf`` / ``schedule_e.pdf`` / ``form_8949.pdf``, the
  ordering table below already slots them in the correct position.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from skill.scripts.models import CanonicalReturn, FilingStatus

# ---------------------------------------------------------------------------
# Constants / lookup tables
# ---------------------------------------------------------------------------

_REFERENCE_DIR = Path(__file__).resolve().parent.parent.parent / "reference"
_MAILING_ADDRESSES_PATH = _REFERENCE_DIR / "irs-mailing-addresses.json"

# IRS attachment sequence order for paper-filed Form 1040 packages.
# Form 1040 itself is always first; the numbered schedules follow in
# the order the IRS prints them in the Form 1040 instructions, then
# any "other" form (alphabetical fallback).
#
# Each entry is the BASE filename (sans .pdf, sans any per-business
# suffix). Schedule C is special-cased because the per-business
# renderer ``render_schedule_c_pdfs_all`` writes
# ``schedule_c_<index>_<slug>.pdf`` per business.
_FORM_ORDER: tuple[str, ...] = (
    "form_1040",
    "schedule_1",
    "schedule_2",
    "schedule_3",
    "schedule_a",
    "schedule_b",
    "schedule_c",     # all schedule_c*.pdf files match this prefix
    "schedule_d",
    "form_8949",
    "schedule_e",
    "schedule_se",
    "schedule_eic",
    "schedule_8812",
    "form_8829",      # home-office deduction; attachment sequence 176
)

# Filenames produced by state plugin agents — these belong in a separate
# state-mail bundle, NOT in the federal mailing envelope. They are
# silently dropped from the federal bundle if a caller passes them in.
_STATE_FILENAME_PREFIX = "state_"

_ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Mailing-address lookup
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IRSMailingAddress:
    """A pair of (with-payment, without-payment) IRS service-center addresses."""

    state: str
    with_payment: str
    without_payment: str


def _load_mailing_addresses() -> dict[str, Any]:
    """Load the IRS mailing-address reference JSON.

    The reference file has shape::

        {
          "_source": {...},
          "addresses": {
            "AL": {"with_payment": "...", "without_payment": "..."},
            ...
          }
        }
    """
    if not _MAILING_ADDRESSES_PATH.exists():
        raise FileNotFoundError(
            f"IRS mailing-address reference not found: {_MAILING_ADDRESSES_PATH}"
        )
    return json.loads(_MAILING_ADDRESSES_PATH.read_text())


def lookup_mailing_address(state_code: str) -> IRSMailingAddress:
    """Return the IRS service-center address pair for a given state code.

    Falls back to the ``INTL`` entry (international / territories /
    APO/FPO/DPO) when the state code is unknown.
    """
    data = _load_mailing_addresses()
    addresses = data.get("addresses", {})
    key = (state_code or "").upper()
    if key not in addresses:
        key = "INTL"
    entry = addresses[key]
    return IRSMailingAddress(
        state=key,
        with_payment=entry["with_payment"],
        without_payment=entry["without_payment"],
    )


# ---------------------------------------------------------------------------
# Form ordering
# ---------------------------------------------------------------------------


def _form_order_key(path: Path) -> tuple[int, str]:
    """Sort key for ``order_forms``.

    Returns ``(priority, name)``:

    * ``priority`` is the index in ``_FORM_ORDER`` (lower = earlier).
      Files whose stem starts with a known prefix get that prefix's
      index. Anything unknown is placed after every known form.
    * ``name`` is the filename (lowercased), used as the alphabetical
      tiebreaker for "other" forms and for multiple Schedule C files.
    """
    stem = path.stem.lower()
    for idx, prefix in enumerate(_FORM_ORDER):
        if stem == prefix or stem.startswith(prefix + "_") or stem.startswith(prefix + "-"):
            return (idx, stem)
    return (len(_FORM_ORDER), stem)


def order_forms(rendered_paths: list[Path]) -> list[Path]:
    """Order rendered PDF paths by IRS attachment-sequence convention.

    Filters out state-return PDFs (``state_*.pdf``) — those go in a
    separate state envelope, not the federal mailing bundle.
    """
    federal_only = [
        p for p in rendered_paths if not p.name.lower().startswith(_STATE_FILENAME_PREFIX)
    ]
    return sorted(federal_only, key=_form_order_key)


# ---------------------------------------------------------------------------
# Cover-sheet / signature / mailing renderer helpers
# ---------------------------------------------------------------------------


def _format_money(value: Decimal | None) -> str:
    if value is None:
        return "(not computed)"
    return f"${value.quantize(Decimal('0.01')):,.2f}"


def _file_status_text(canonical_return: CanonicalReturn) -> str:
    """Compose a human-readable file-status string for the cover sheet."""
    state = canonical_return.address.state
    owed = canonical_return.computed.amount_owed or _ZERO
    enclosure = "with payment" if owed > _ZERO else "without payment"
    addr = lookup_mailing_address(state)
    target = addr.with_payment if owed > _ZERO else addr.without_payment
    return f"Paper file ({enclosure}) — mail to {target}"


def _ffff_summary(canonical_return: CanonicalReturn) -> tuple[bool, list[str]]:
    """Return ``(compatible, blocker_messages)`` for the cover sheet.

    Reads the validation_report dict that ``engine.compute`` populates,
    specifically the ``ffff`` sub-dict. If the validation_report is
    missing (return wasn't run through compute) or the ffff sub-dict is
    absent, returns ``(False, [])`` — the cover sheet will print "FFFF
    compatibility unknown".
    """
    report = canonical_return.computed.validation_report
    if not isinstance(report, dict):
        return (False, [])
    ffff = report.get("ffff")
    if not isinstance(ffff, dict):
        return (False, [])
    compatible = bool(ffff.get("compatible", False))
    blockers = ffff.get("blockers") or []
    messages = []
    for v in blockers:
        if isinstance(v, dict):
            msg = v.get("message")
            if msg:
                messages.append(str(msg))
    return (compatible, messages)


def _format_filing_status(status: FilingStatus) -> str:
    return {
        FilingStatus.SINGLE: "Single",
        FilingStatus.MFJ: "Married Filing Jointly",
        FilingStatus.MFS: "Married Filing Separately",
        FilingStatus.HOH: "Head of Household",
        FilingStatus.QSS: "Qualifying Surviving Spouse",
    }.get(status, status.value)


def _two_name_statuses() -> set[FilingStatus]:
    """Filing statuses that require both taxpayer and spouse signature lines."""
    return {FilingStatus.MFJ, FilingStatus.MFS, FilingStatus.QSS}


# ---------------------------------------------------------------------------
# reportlab page builders
# ---------------------------------------------------------------------------


def render_cover_sheet(canonical_return: CanonicalReturn, out_path: Path) -> Path:
    """Write a one-page cover sheet PDF and return its path."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=letter,
        title="Tax Return Cover Sheet",
    )
    styles = getSampleStyleSheet()
    story: list = []

    # ---- header --------------------------------------------------------
    story.append(
        Paragraph(
            f"Federal Tax Return — Tax Year {canonical_return.tax_year}",
            styles["Title"],
        )
    )
    story.append(
        Paragraph(
            "Paper-file bundle (cover sheet)",
            styles["Italic"],
        )
    )
    story.append(Spacer(1, 12))

    taxpayer_name = (
        f"{canonical_return.taxpayer.first_name} "
        f"{canonical_return.taxpayer.last_name}"
    )
    spouse_name = (
        f"{canonical_return.spouse.first_name} {canonical_return.spouse.last_name}"
        if canonical_return.spouse is not None
        else None
    )
    header_rows = [
        ["Taxpayer", taxpayer_name],
        ["SSN", canonical_return.taxpayer.ssn],
        ["Filing status", _format_filing_status(canonical_return.filing_status)],
        ["Tax year", str(canonical_return.tax_year)],
    ]
    if spouse_name:
        header_rows.insert(2, ["Spouse", spouse_name])
        header_rows.insert(3, ["Spouse SSN", canonical_return.spouse.ssn])

    header_table = Table(header_rows, colWidths=[120, 380])
    header_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story.append(header_table)
    story.append(Spacer(1, 16))

    # ---- summary table -------------------------------------------------
    c = canonical_return.computed
    refund = c.refund or _ZERO
    owed = c.amount_owed or _ZERO
    if owed > _ZERO:
        bottom_label = "Amount owed"
        bottom_value = _format_money(owed)
    else:
        bottom_label = "Refund"
        bottom_value = _format_money(refund)

    summary_rows: list[list[str]] = [
        ["Item", "Amount"],
        ["Total income", _format_money(c.total_income)],
        ["Adjusted gross income (AGI)", _format_money(c.adjusted_gross_income)],
        ["Deduction taken", _format_money(c.deduction_taken)],
        ["Taxable income", _format_money(c.taxable_income)],
        ["Total tax", _format_money(c.total_tax)],
        ["Total payments", _format_money(c.total_payments)],
        [bottom_label, bottom_value],
    ]
    summary_table = Table(summary_rows, colWidths=[300, 200])
    summary_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                ("BACKGROUND", (0, -1), (-1, -1), colors.whitesmoke),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ]
        )
    )
    story.append(summary_table)
    story.append(Spacer(1, 16))

    # ---- file status ---------------------------------------------------
    story.append(Paragraph("<b>File status</b>", styles["Normal"]))
    story.append(Paragraph(_file_status_text(canonical_return), styles["Normal"]))
    story.append(Spacer(1, 12))

    # ---- FFFF compatibility -------------------------------------------
    compatible, blockers = _ffff_summary(canonical_return)
    if canonical_return.computed.validation_report is None:
        story.append(
            Paragraph(
                "<b>FFFF compatibility:</b> not evaluated (validation report missing).",
                styles["Normal"],
            )
        )
    elif compatible:
        story.append(
            Paragraph(
                "<b>FFFF compatibility:</b> Eligible for Free File Fillable Forms "
                "(no blockers detected). Paper filing is the chosen channel.",
                styles["Normal"],
            )
        )
    else:
        story.append(
            Paragraph(
                "<b>FFFF compatibility:</b> NOT eligible for Free File Fillable "
                "Forms. This return must be mailed on paper because:",
                styles["Normal"],
            )
        )
        if blockers:
            for msg in blockers:
                story.append(Paragraph(f"&bull; {msg}", styles["Normal"]))
        else:
            story.append(
                Paragraph(
                    "&bull; FFFF blockers were detected but no per-blocker "
                    "message was supplied by the validation report.",
                    styles["Normal"],
                )
            )

    doc.build(story)
    return out_path


def render_signature_page(canonical_return: CanonicalReturn, out_path: Path) -> Path:
    """Write a one-page signature page PDF and return its path."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=letter,
        title="Tax Return Signature Page",
    )
    styles = getSampleStyleSheet()
    story: list = []

    story.append(Paragraph("Sign Here", styles["Title"]))
    story.append(
        Paragraph(
            "Sign and date below. Both spouses must sign on a joint return. "
            "Keep a copy of the signed return for your records.",
            styles["Italic"],
        )
    )
    story.append(Spacer(1, 16))

    taxpayer_name = (
        f"{canonical_return.taxpayer.first_name} "
        f"{canonical_return.taxpayer.last_name}"
    )
    taxpayer_occupation = canonical_return.taxpayer.occupation or ""
    show_spouse = canonical_return.filing_status in _two_name_statuses()

    sig_rows: list[list[str]] = [
        ["Field", "Taxpayer"],
        ["Printed name", taxpayer_name],
        ["Signature", "X _________________________________________"],
        ["Date", "____/____/________"],
        ["Occupation", taxpayer_occupation],
    ]
    sig_table = Table(sig_rows, colWidths=[120, 380])
    sig_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story.append(sig_table)
    story.append(Spacer(1, 12))

    if show_spouse and canonical_return.spouse is not None:
        spouse_name = (
            f"{canonical_return.spouse.first_name} "
            f"{canonical_return.spouse.last_name}"
        )
        spouse_occupation = canonical_return.spouse.occupation or ""
        spouse_rows: list[list[str]] = [
            ["Field", "Spouse"],
            ["Printed name", spouse_name],
            ["Signature", "X _________________________________________"],
            ["Date", "____/____/________"],
            ["Occupation", spouse_occupation],
        ]
        spouse_table = Table(spouse_rows, colWidths=[120, 380])
        spouse_table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                ]
            )
        )
        story.append(spouse_table)
        story.append(Spacer(1, 12))

    # Preparer area stub: always self-prepared in this skill.
    preparer_rows: list[list[str]] = [
        ["Paid preparer use only", ""],
        ["Preparer", "Self-prepared"],
        ["PTIN", "N/A"],
        ["Firm", "N/A"],
        ["Phone", "N/A"],
    ]
    preparer_table = Table(preparer_rows, colWidths=[180, 320])
    preparer_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(preparer_table)

    doc.build(story)
    return out_path


def render_mailing_instructions(
    canonical_return: CanonicalReturn, out_path: Path
) -> Path:
    """Write a one-page mailing-instructions PDF and return its path."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=letter,
        title="Mailing Instructions",
    )
    styles = getSampleStyleSheet()
    story: list = []

    story.append(Paragraph("Mailing Instructions", styles["Title"]))
    story.append(Spacer(1, 12))

    state_code = canonical_return.address.state
    addr = lookup_mailing_address(state_code)
    owed = canonical_return.computed.amount_owed or _ZERO
    enclosed = owed > _ZERO
    chosen = addr.with_payment if enclosed else addr.without_payment

    enclosure_label = (
        "WITH a payment enclosed" if enclosed else "WITHOUT a payment enclosed"
    )
    story.append(
        Paragraph(
            f"<b>You are mailing this return {enclosure_label}.</b>",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 8))

    story.append(Paragraph("<b>Mail to:</b>", styles["Normal"]))
    addr_lines = [line.strip() for line in chosen.split(",")]
    addr_table_rows = [[line] for line in addr_lines]
    addr_table = Table(addr_table_rows, colWidths=[500])
    addr_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 1.0, colors.black),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 11),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(addr_table)
    story.append(Spacer(1, 16))

    story.append(Paragraph("<b>Before mailing:</b>", styles["Normal"]))
    instructions = [
        "Sign and date the signature page (both spouses must sign on a joint return).",
        "Attach Copy B of every Form W-2 to the front of Form 1040.",
        "Attach any 1099 forms reporting federal income tax withholding to the "
        "front of Form 1040.",
        "Order the schedules in the sequence printed in this bundle (Form 1040 first, "
        "then schedules in attachment-sequence order).",
        "If a payment is enclosed, write a check or money order payable to "
        "&quot;United States Treasury&quot; for the &quot;Amount owed&quot; on the cover sheet, "
        "and write your SSN, tax year, and &quot;Form 1040&quot; on the memo line. Do NOT staple "
        "the payment to the return.",
        "Use a stamped envelope with sufficient postage. The IRS recommends "
        "Certified Mail with Return Receipt for proof of timely mailing.",
        "Mail the bundle to the address above.",
    ]
    for instruction in instructions:
        story.append(Paragraph(f"&bull; {instruction}", styles["Normal"]))
    story.append(Spacer(1, 16))

    # FFFF blocker warning box, if applicable.
    compatible, blockers = _ffff_summary(canonical_return)
    if (canonical_return.computed.validation_report is not None) and (not compatible):
        story.append(
            Paragraph(
                "<b>Why this return is being paper-filed:</b>",
                styles["Normal"],
            )
        )
        if blockers:
            for msg in blockers:
                story.append(
                    Paragraph(
                        f"&bull; This return could not be e-filed via FFFF because: {msg}",
                        styles["Normal"],
                    )
                )
        else:
            story.append(
                Paragraph(
                    "&bull; This return could not be e-filed via FFFF.",
                    styles["Normal"],
                )
            )

    doc.build(story)
    return out_path


# ---------------------------------------------------------------------------
# Main: build_paper_bundle
# ---------------------------------------------------------------------------


@dataclass
class _BundleAssets:
    """Internal accumulator: paths of generated cover/sig/mailing pages."""

    cover: Path
    signature: Path
    mailing: Path
    forms_in_order: list[Path] = field(default_factory=list)


def _validate_input_pdfs(rendered_pdf_paths: list[Path]) -> list[Path]:
    """Validate every input PDF path exists; raise FileNotFoundError if not."""
    paths: list[Path] = []
    for raw in rendered_pdf_paths:
        p = Path(raw)
        if not p.exists():
            raise FileNotFoundError(
                f"paper bundle input PDF not found: {p} "
                "(every entry in rendered_pdf_paths must point to an existing file)"
            )
        paths.append(p)
    return paths


def build_paper_bundle(
    canonical_return: CanonicalReturn,
    rendered_pdf_paths: list[Path],
    out_path: Path,
) -> Path:
    """Build a single mailable PDF bundle from rendered federal forms.

    Parameters
    ----------
    canonical_return
        A computed ``CanonicalReturn``. Should have been passed through
        ``skill.scripts.calc.engine.compute`` so that
        ``canonical_return.computed`` is populated; otherwise the cover
        sheet will print "(not computed)" placeholders.
    rendered_pdf_paths
        List of PDF paths produced by the form renderers (e.g. by
        ``skill.scripts.pipeline.run_pipeline``). Order does not matter
        — this function reorders them per IRS attachment-sequence
        convention. State-return PDFs (``state_*.pdf``) are silently
        dropped because they belong in a separate state envelope.
    out_path
        Where to write the merged bundle PDF. Parent directory will be
        created if necessary.

    Returns
    -------
    Path
        ``out_path`` for chaining convenience.

    Raises
    ------
    FileNotFoundError
        If any entry in ``rendered_pdf_paths`` does not exist on disk.
    """
    from pypdf import PdfReader, PdfWriter

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 1) Validate inputs early so we fail before producing scratch files.
    input_paths = _validate_input_pdfs(list(rendered_pdf_paths))

    # 2) Order federal forms; state PDFs are dropped here.
    forms_in_order = order_forms(input_paths)

    # 3) Render scratch cover/signature/mailing pages alongside out_path.
    scratch_dir = out_path.parent
    scratch_dir.mkdir(parents=True, exist_ok=True)
    cover_path = scratch_dir / f"{out_path.stem}._cover.pdf"
    sig_path = scratch_dir / f"{out_path.stem}._signature.pdf"
    mail_path = scratch_dir / f"{out_path.stem}._mailing.pdf"

    render_cover_sheet(canonical_return, cover_path)
    render_signature_page(canonical_return, sig_path)
    render_mailing_instructions(canonical_return, mail_path)

    # 4) Merge: cover -> ordered forms -> signature -> mailing.
    writer = PdfWriter()
    merge_order: list[Path] = [cover_path, *forms_in_order, sig_path, mail_path]
    for path in merge_order:
        reader = PdfReader(str(path))
        for page in reader.pages:
            writer.add_page(page)

    with out_path.open("wb") as fh:
        writer.write(fh)

    # 5) Best-effort cleanup of scratch pages.
    for scratch in (cover_path, sig_path, mail_path):
        try:
            scratch.unlink()
        except OSError:
            pass

    return out_path
