"""Shared AcroForm overlay helper for IRS fillable PDFs.

This module is the wave-5 replacement for the reportlab "scaffold" Layer 2
renderers. It exposes two primitives that the per-form renderers
(``form_1040.py`` first, then ``schedule_a/b/c/se.py`` in the next wave)
can compose:

* :func:`fill_acroform_pdf` — open a fillable source PDF, write a dict
  of ``widget_name -> value`` into it via pypdf's
  ``PdfWriter.update_page_form_field_values``, and emit the filled copy.
  Text widgets and checkbox widgets are handled uniformly: text values
  are passed through as strings, while ``True`` / ``False`` for a
  ``/Btn`` widget is resolved to the correct on-state appearance name
  (the IRS Form 1040 uses ``/1`` ... ``/5`` rather than ``/Yes``).

* :func:`load_widget_map` — parse one of the wave-4 reference JSONs
  (``form-1040-acroform-map.json`` and friends) into a typed
  :class:`WidgetMap` object that maps Layer-1 dataclass attribute names
  to fully-qualified widget names, optional computed-copies, and
  filing-status checkbox sub-dicts.

* :func:`fetch_and_verify_source_pdf` — download an IRS-hosted fillable
  PDF from a known URL, verify its SHA-256 against a pinned digest, and
  cache it at a target path. Designed to be called once per machine
  (the cached file is checked in via the renderer's bundled-asset
  contract).

Design notes
------------

* The widget-name in the JSON is the *fully qualified dotted path* (the
  same form ``PdfReader.get_fields()`` produces, e.g.
  ``topmostSubform[0].Page1[0].f1_57[0]``). pypdf's
  ``update_page_form_field_values`` matches both the qualified name and
  the partial ``/T`` name, so passing the qualified name is safe.

* All money values are formatted as ``"{:.2f}"`` (e.g. ``"65000.00"``)
  with no thousands separator. The IRS fillable PDFs accept either
  format, but plain-decimal is the safest because some downstream tools
  parse the rendered text back to a Decimal.

* Zero amounts collapse to the empty string so the rendered form does
  not get cluttered with leading zeros on every blank line.

* This helper does NOT silently fall back to a reportlab scaffold. If
  the source PDF is missing or its SHA-256 does not match the pinned
  digest, the caller gets a loud :class:`RuntimeError`.
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping


# ---------------------------------------------------------------------------
# Value formatting helpers
# ---------------------------------------------------------------------------


_ZERO = Decimal("0")


def format_money(value: Decimal | int | float | None) -> str:
    """Format a money amount for an AcroForm text widget.

    * ``None`` and zero collapse to the empty string (the IRS form
      should not show ``0.00`` on every blank line).
    * Anything else is quantized to two decimal places and formatted
      without thousands separators (e.g. ``"65000.00"``).
    """
    if value is None:
        return ""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    if value == _ZERO:
        return ""
    return f"{value.quantize(Decimal('0.01')):.2f}"


# ---------------------------------------------------------------------------
# Widget map (JSON schema produced by wave 4 research scripts)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WidgetMap:
    """Parsed wave-4 widget map for one IRS form.

    Attributes
    ----------
    source_pdf_url
        IRS-hosted URL the source PDF was downloaded from.
    source_pdf_sha256
        Hex-encoded SHA-256 digest of the source PDF, pinned at
        research time. Filling code MUST verify this against the actual
        bytes before opening the PDF.
    semantic_to_widget
        ``{layer_1_field_name: fully_qualified_widget_name}`` for every
        text-widget mapping. Excludes filing-status checkbox groups
        (those live in ``filing_status_checkboxes``).
    computed_copies
        ``{layer_1_field_name: [extra_widget_name, ...]}`` for fields
        whose value must be written to more than one widget (e.g. line
        11 AGI on f1040 lives on page 1 *and* the top of page 2).
    filing_status_checkboxes
        ``{filing_status_label: widget_name}`` for the mutually
        exclusive five-checkbox filing-status group on Form 1040.
    """

    source_pdf_url: str
    source_pdf_sha256: str
    semantic_to_widget: dict[str, str] = field(default_factory=dict)
    computed_copies: dict[str, list[str]] = field(default_factory=dict)
    filing_status_checkboxes: dict[str, str] = field(default_factory=dict)

    def widget_names_for(self, semantic_name: str) -> list[str]:
        """Return every widget that should receive ``semantic_name``'s
        value (the primary widget plus any computed-copy mirrors)."""
        out: list[str] = []
        primary = self.semantic_to_widget.get(semantic_name)
        if primary is not None:
            out.append(primary)
        for w in self.computed_copies.get(semantic_name, []):
            if w not in out:
                out.append(w)
        return out


def load_widget_map(map_json_path: Path) -> WidgetMap:
    """Load a wave-4 widget-map JSON into a :class:`WidgetMap`.

    Raises
    ------
    FileNotFoundError
        If the JSON file does not exist.
    KeyError
        If the JSON is missing one of the required top-level keys.
    """
    map_json_path = Path(map_json_path)
    if not map_json_path.exists():
        raise FileNotFoundError(
            f"widget map JSON not found: {map_json_path}. "
            "See skill/reference/form-1040-acroform-methodology.md "
            "for the regeneration steps."
        )
    data = json.loads(map_json_path.read_text())

    try:
        url = data["source_pdf_url"]
        sha = data["source_pdf_sha256"]
        mapping = data["mapping"]
    except KeyError as exc:  # pragma: no cover - load_widget_map guarded
        raise KeyError(
            f"widget map {map_json_path} missing required key: {exc.args[0]!r}"
        ) from exc

    semantic_to_widget: dict[str, str] = {}
    for sem_name, entry in mapping.items():
        widget_name = entry.get("widget_name")
        widget_type = entry.get("type")
        # Skip pseudo wildcard entries (filing_status checkbox group):
        # those have widget_name ending in [*] and are handled via
        # the dedicated filing_status_checkboxes block.
        if not widget_name or "*" in widget_name:
            continue
        # Skip checkbox-typed entries from the main mapping; they live
        # in filing_status_checkboxes for Form 1040 and would otherwise
        # confuse text-fill code.
        if widget_type == "checkbox":
            continue
        semantic_to_widget[sem_name] = widget_name

    computed_copies: dict[str, list[str]] = {}
    for sem_name, copies in (data.get("computed_copies") or {}).items():
        names: list[str] = []
        for entry in copies:
            wn = entry.get("widget_name")
            if wn:
                names.append(wn)
        if names:
            computed_copies[sem_name] = names

    filing_status_checkboxes: dict[str, str] = {}
    for label, entry in (data.get("filing_status_checkboxes") or {}).items():
        wn = entry.get("widget_name")
        if wn:
            filing_status_checkboxes[label] = wn

    return WidgetMap(
        source_pdf_url=url,
        source_pdf_sha256=sha,
        semantic_to_widget=semantic_to_widget,
        computed_copies=computed_copies,
        filing_status_checkboxes=filing_status_checkboxes,
    )


# ---------------------------------------------------------------------------
# Source PDF cache: fetch + SHA-256 verification
# ---------------------------------------------------------------------------


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_and_verify_source_pdf(
    target_path: Path,
    source_url: str,
    expected_sha256: str,
    *,
    timeout_seconds: float = 60.0,
) -> Path:
    """Ensure ``target_path`` holds the canonical source PDF.

    If the file already exists and its SHA-256 matches
    ``expected_sha256``, this is a no-op. Otherwise the IRS-hosted URL
    is fetched, the digest is verified, and the bytes are written to
    ``target_path``.

    Raises
    ------
    RuntimeError
        If the fetched bytes do not match the expected SHA-256, or if
        the URL fetch fails. The error message tells the user where to
        manually drop the file.
    """
    target_path = Path(target_path)

    if target_path.exists():
        actual = _sha256_of_file(target_path)
        if actual == expected_sha256:
            return target_path
        # File exists but is the wrong version — try to redownload.

    target_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        req = urllib.request.Request(
            source_url, headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            data = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(
            f"failed to download source PDF from {source_url}: {exc}. "
            f"Place the file manually at {target_path} (expected sha256 "
            f"{expected_sha256})."
        ) from exc

    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise RuntimeError(
            f"downloaded source PDF SHA-256 mismatch for {source_url}: "
            f"got {actual}, expected {expected_sha256}. The IRS may have "
            f"re-issued the form; regenerate the widget map (see "
            f"skill/reference/form-1040-acroform-methodology.md) before "
            f"trusting the renderer. To override, place a verified copy "
            f"at {target_path}."
        )

    target_path.write_bytes(data)
    return target_path


def ensure_source_pdf(
    target_path: Path,
    expected_sha256: str,
    source_url: str,
) -> Path:
    """Verify (and refetch if necessary) the cached source PDF.

    Convenience wrapper around :func:`fetch_and_verify_source_pdf` that
    accepts a target path the caller is responsible for choosing.
    """
    return fetch_and_verify_source_pdf(
        target_path,
        source_url,
        expected_sha256,
    )


# ---------------------------------------------------------------------------
# Core fill routine
# ---------------------------------------------------------------------------


def _resolve_widget_to_page_index(reader, widget_name: str) -> int | None:
    """Locate which page (0-indexed) carries the widget annotation.

    Walks each page's ``/Annots`` and matches the fully-qualified name
    by following the ``/Parent`` chain. Returns ``None`` if no page
    contains the widget (the caller should treat that as an error).
    """
    for page_idx, page in enumerate(reader.pages):
        annots = page.get("/Annots")
        if not annots:
            continue
        for annot_ref in annots:
            annot = annot_ref.get_object()
            if annot.get("/Subtype") != "/Widget":
                continue
            parts: list[str] = []
            node: Any = annot
            seen: set[int] = set()
            while node is not None and id(node) not in seen:
                seen.add(id(node))
                t = node.get("/T")
                if t is not None:
                    parts.append(str(t))
                parent = node.get("/Parent")
                node = parent.get_object() if parent is not None else None
            full_name = ".".join(reversed(parts))
            if full_name == widget_name:
                return page_idx
    return None


def _checkbox_on_state(
    reader, widget_name: str, page_idx: int
) -> str | None:
    """Return the on-state appearance name (e.g. ``/1`` or ``/Yes``)
    for a checkbox widget by inspecting its ``/AP/N`` keys.

    The IRS Form 1040 filing-status checkboxes use ``/1`` .. ``/5`` as
    on-state names rather than the conventional ``/Yes``. Returns
    ``None`` if the widget cannot be located or has no AP/N entry —
    the caller should treat ``None`` as an error.
    """
    page = reader.pages[page_idx]
    annots = page.get("/Annots") or []
    for annot_ref in annots:
        annot = annot_ref.get_object()
        if annot.get("/Subtype") != "/Widget":
            continue
        parts: list[str] = []
        node: Any = annot
        seen: set[int] = set()
        while node is not None and id(node) not in seen:
            seen.add(id(node))
            t = node.get("/T")
            if t is not None:
                parts.append(str(t))
            parent = node.get("/Parent")
            node = parent.get_object() if parent is not None else None
        full_name = ".".join(reversed(parts))
        if full_name != widget_name:
            continue
        ap = annot.get("/AP")
        if ap is None:
            parent_obj = annot.get("/Parent")
            if parent_obj is not None:
                ap = parent_obj.get_object().get("/AP")
        if ap is None:
            return None
        normal = ap.get("/N")
        if normal is None:
            return None
        for key in normal.keys():
            key_str = str(key)
            if key_str != "/Off":
                return key_str
        return None
    return None


def fill_acroform_pdf(
    source_pdf_path: Path,
    widget_values: Mapping[str, str | bool],
    out_path: Path,
) -> Path:
    """Fill a fillable PDF with widget values and write a copy.

    Parameters
    ----------
    source_pdf_path
        Path to the source AcroForm PDF (e.g. the IRS-hosted f1040.pdf
        the caller has already verified via
        :func:`fetch_and_verify_source_pdf`).
    widget_values
        Mapping from fully-qualified widget name to the value to write.
        Strings are written verbatim into ``/V`` of the matching text
        widget. Booleans target checkbox widgets — ``True`` resolves to
        the widget's actual on-state appearance name (e.g. ``/1`` for
        the IRS f1040 filing-status checkboxes), ``False`` writes
        ``/Off``.
    out_path
        Where to write the filled copy. Parent directories are created
        if they do not exist.

    Returns
    -------
    Path
        ``out_path`` for convenience.

    Raises
    ------
    FileNotFoundError
        If ``source_pdf_path`` does not exist.
    RuntimeError
        If a checkbox widget's on-state cannot be resolved.
    """
    from pypdf import PdfReader, PdfWriter

    source_pdf_path = Path(source_pdf_path)
    out_path = Path(out_path)
    if not source_pdf_path.exists():
        raise FileNotFoundError(
            f"source PDF not found: {source_pdf_path}"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(str(source_pdf_path))
    writer = PdfWriter(clone_from=reader)

    # Group values by page so we can submit them with one
    # update_page_form_field_values call per page (the call accepts a
    # single page or a list, but per-page is the most explicit).
    by_page: dict[int, dict[str, str]] = {}
    unresolved: list[str] = []

    for widget_name, value in widget_values.items():
        page_idx = _resolve_widget_to_page_index(reader, widget_name)
        if page_idx is None:
            unresolved.append(widget_name)
            continue

        if isinstance(value, bool):
            # Checkbox: resolve the on-state appearance name from the
            # source widget itself (the IRS f1040 uses /1../5, not /Yes).
            if value:
                on_state = _checkbox_on_state(reader, widget_name, page_idx)
                if on_state is None:
                    raise RuntimeError(
                        f"cannot resolve checkbox on-state for widget "
                        f"{widget_name!r} in {source_pdf_path}"
                    )
                resolved_value = on_state
            else:
                resolved_value = "/Off"
        else:
            resolved_value = str(value)

        by_page.setdefault(page_idx, {})[widget_name] = resolved_value

    if unresolved:
        raise RuntimeError(
            f"the following widgets could not be located in "
            f"{source_pdf_path}: {sorted(unresolved)}"
        )

    for page_idx, fields in by_page.items():
        writer.update_page_form_field_values(
            writer.pages[page_idx],
            fields,
            auto_regenerate=True,
        )

    with out_path.open("wb") as fh:
        writer.write(fh)
    return out_path


# ---------------------------------------------------------------------------
# High-level convenience: build the per-widget dict from a Layer-1 dataclass
# ---------------------------------------------------------------------------


def build_widget_values(
    widget_map: WidgetMap,
    semantic_values: Mapping[str, Decimal | int | float | str | None],
    *,
    money_field_names: Iterable[str] | None = None,
) -> dict[str, str]:
    """Translate ``{semantic_field_name: value}`` into a widget-name dict.

    Parameters
    ----------
    widget_map
        The :class:`WidgetMap` for the form being rendered.
    semantic_values
        ``{layer_1_field_name: value}``. Decimal/int/float values are
        formatted via :func:`format_money`; strings are passed through.
    money_field_names
        Optional iterable of names that are *unconditionally* treated
        as money (formatted via :func:`format_money` even if the value
        was passed as a plain string). Useful when the dataclass mixes
        Decimal money fields with free-text header fields.
    """
    money_set = set(money_field_names or ())
    out: dict[str, str] = {}
    for sem_name, value in semantic_values.items():
        widget_names = widget_map.widget_names_for(sem_name)
        if not widget_names:
            continue
        if isinstance(value, (Decimal, int, float)):
            text = format_money(Decimal(str(value)) if not isinstance(value, Decimal) else value)
        elif isinstance(value, str):
            text = format_money(value) if sem_name in money_set else value
        elif value is None:
            text = ""
        else:
            text = str(value)
        for wn in widget_names:
            out[wn] = text
    return out
