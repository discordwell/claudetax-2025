"""Document classifier: identifies the tax document kind from a PDF.

Tier 1: filename heuristics (fast, no read).
Tier 2: first-page text scan with pypdf/pdfplumber (medium, requires text layer).
Tier 3: Azure AI Document Intelligence prebuilt tax model (slow, paid, for scans).

This module ships Tier 1 and Tier 2 out of the box. Tier 3 is wired through the
Azure wrapper in _azure_doc_intelligence.py when credentials are configured.
"""
from __future__ import annotations

import re
from pathlib import Path

from skill.scripts.ingest._pipeline import DocumentKind

# Filename patterns — case-insensitive, using custom alphanumeric boundaries
# (?<![a-z0-9]) / (?![a-z0-9]) so that underscores and hyphens count as separators.
# Python \b treats underscore as a word char, which breaks matching inside
# names like "w2_employer.pdf" or "Form_1040.pdf".
_B_OPEN = r"(?<![a-z0-9])"
_B_CLOSE = r"(?![a-z0-9])"
_FILENAME_HINTS: list[tuple[re.Pattern[str], DocumentKind]] = [
    (re.compile(rf"{_B_OPEN}f?1040-?x{_B_CLOSE}", re.I), DocumentKind.FORM_1040_X),
    (re.compile(rf"{_B_OPEN}f?1040-?sr{_B_CLOSE}", re.I), DocumentKind.FORM_1040_SR),
    (re.compile(rf"{_B_OPEN}f?1040{_B_CLOSE}(?!sc|se|sd)", re.I), DocumentKind.FORM_1040),
    (re.compile(rf"{_B_OPEN}schedule[-_ ]?a{_B_CLOSE}", re.I), DocumentKind.SCHEDULE_A),
    (re.compile(rf"{_B_OPEN}schedule[-_ ]?b{_B_CLOSE}", re.I), DocumentKind.SCHEDULE_B),
    (re.compile(rf"{_B_OPEN}schedule[-_ ]?c{_B_CLOSE}", re.I), DocumentKind.SCHEDULE_C),
    (re.compile(rf"{_B_OPEN}schedule[-_ ]?d{_B_CLOSE}", re.I), DocumentKind.SCHEDULE_D),
    (re.compile(rf"{_B_OPEN}schedule[-_ ]?se{_B_CLOSE}", re.I), DocumentKind.SCHEDULE_SE),
    (re.compile(rf"{_B_OPEN}schedule[-_ ]?e{_B_CLOSE}", re.I), DocumentKind.SCHEDULE_E),
    (re.compile(rf"{_B_OPEN}w-?2{_B_CLOSE}", re.I), DocumentKind.FORM_W2),
    (re.compile(rf"{_B_OPEN}ssa-?1099{_B_CLOSE}", re.I), DocumentKind.FORM_SSA_1099),
    (re.compile(rf"{_B_OPEN}1099-?nec{_B_CLOSE}", re.I), DocumentKind.FORM_1099_NEC),
    (re.compile(rf"{_B_OPEN}1099-?int{_B_CLOSE}", re.I), DocumentKind.FORM_1099_INT),
    (re.compile(rf"{_B_OPEN}1099-?div{_B_CLOSE}", re.I), DocumentKind.FORM_1099_DIV),
    (re.compile(rf"{_B_OPEN}1099-?b{_B_CLOSE}", re.I), DocumentKind.FORM_1099_B),
    (re.compile(rf"{_B_OPEN}1099-?misc{_B_CLOSE}", re.I), DocumentKind.FORM_1099_MISC),
    (re.compile(rf"{_B_OPEN}1099-?k{_B_CLOSE}", re.I), DocumentKind.FORM_1099_K),
    (re.compile(rf"{_B_OPEN}1099-?r{_B_CLOSE}", re.I), DocumentKind.FORM_1099_R),
    (re.compile(rf"{_B_OPEN}1099-?g{_B_CLOSE}", re.I), DocumentKind.FORM_1099_G),
    (re.compile(rf"{_B_OPEN}f?1095-?a{_B_CLOSE}", re.I), DocumentKind.FORM_1095_A),
    (re.compile(rf"{_B_OPEN}1098-?t{_B_CLOSE}", re.I), DocumentKind.FORM_1098_T),
    (re.compile(rf"{_B_OPEN}1098-?e{_B_CLOSE}", re.I), DocumentKind.FORM_1098_E),
    (re.compile(rf"{_B_OPEN}f?1098{_B_CLOSE}(?!-)", re.I), DocumentKind.FORM_1098),
    (re.compile(rf"{_B_OPEN}k-?1{_B_CLOSE}", re.I), DocumentKind.SCHEDULE_K1_1065),
    (re.compile(r"\.txf$", re.I), DocumentKind.TXF),
]

# Content patterns — searched against the first page's text layer when available.
_CONTENT_HINTS: list[tuple[re.Pattern[str], DocumentKind]] = [
    (re.compile(r"Form\s*1040-?X", re.I), DocumentKind.FORM_1040_X),
    (re.compile(r"Form\s*1040-?SR", re.I), DocumentKind.FORM_1040_SR),
    (re.compile(r"Form\s*1040\b(?!\w)", re.I), DocumentKind.FORM_1040),
    (re.compile(r"Wage\s+and\s+Tax\s+Statement", re.I), DocumentKind.FORM_W2),
    (re.compile(r"Nonemployee\s+Compensation", re.I), DocumentKind.FORM_1099_NEC),
    (re.compile(r"Interest\s+Income.*1099-?INT", re.I), DocumentKind.FORM_1099_INT),
    (re.compile(r"Dividends\s+and\s+Distributions", re.I), DocumentKind.FORM_1099_DIV),
    (re.compile(r"Proceeds\s+From\s+Broker", re.I), DocumentKind.FORM_1099_B),
    (re.compile(r"Miscellaneous\s+Information", re.I), DocumentKind.FORM_1099_MISC),
    (re.compile(r"Payment\s+Card\s+and\s+Third\s+Party", re.I), DocumentKind.FORM_1099_K),
    (re.compile(r"Distributions\s+From\s+Pensions", re.I), DocumentKind.FORM_1099_R),
    (re.compile(r"Certain\s+Government\s+Payments", re.I), DocumentKind.FORM_1099_G),
    (re.compile(r"Social\s+Security\s+Benefit\s+Statement", re.I), DocumentKind.FORM_SSA_1099),
    (re.compile(r"Health\s+Insurance\s+Marketplace\s+Statement", re.I), DocumentKind.FORM_1095_A),
    (re.compile(r"Form\s*1095-?A\b", re.I), DocumentKind.FORM_1095_A),
    (re.compile(r"Mortgage\s+Interest\s+Statement", re.I), DocumentKind.FORM_1098),
    (re.compile(r"Tuition\s+Statement", re.I), DocumentKind.FORM_1098_T),
    (re.compile(r"Student\s+Loan\s+Interest", re.I), DocumentKind.FORM_1098_E),
    (re.compile(r"Profit\s+or\s+Loss\s+From\s+Business", re.I), DocumentKind.SCHEDULE_C),
    (
        re.compile(r"Supplemental\s+Income\s+and\s+Loss", re.I),
        DocumentKind.SCHEDULE_E,
    ),
    (re.compile(r"Self-?Employment\s+Tax", re.I), DocumentKind.SCHEDULE_SE),
    (re.compile(r"Schedule\s+K-?1", re.I), DocumentKind.SCHEDULE_K1_1065),
]


def classify_by_filename(path: Path) -> DocumentKind:
    """Tier 1: identify a tax document from its filename."""
    stem = path.name
    for pattern, kind in _FILENAME_HINTS:
        if pattern.search(stem):
            return kind
    return DocumentKind.UNKNOWN


def classify_by_text(text: str) -> DocumentKind:
    """Tier 2: identify a tax document from its first-page text.

    Runs the content patterns in order and returns the first match. Caller is
    responsible for extracting the text (e.g. via pdfplumber.open(path).pages[0]).
    """
    if not text:
        return DocumentKind.UNKNOWN
    for pattern, kind in _CONTENT_HINTS:
        if pattern.search(text):
            return kind
    return DocumentKind.UNKNOWN


def classify(path: Path, first_page_text: str | None = None) -> DocumentKind:
    """Convenience: try filename first, then text if provided."""
    kind = classify_by_filename(path)
    if kind != DocumentKind.UNKNOWN:
        return kind
    if first_page_text is not None:
        return classify_by_text(first_page_text)
    return DocumentKind.UNKNOWN
