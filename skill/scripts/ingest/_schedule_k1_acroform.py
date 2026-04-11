"""Tier 1 ingester for Schedule K-1 (Forms 1065 and 1120-S) fillable PDFs.

Wires a field-name map into a thin subclass of PyPdfAcroFormIngester so that
AcroForm widget values from a Schedule K-1 PDF land on the canonical
``schedules_k1[0].*`` paths on CanonicalReturn.

Schedule K-1 comes in two flavors that share most line semantics:

- Form 1065 K-1 — Partner's Share of Income, Deductions, Credits, etc.
  (issued by partnerships and LLCs taxed as partnerships)
- Form 1120-S K-1 — Shareholder's Share of Income, Deductions, Credits, etc.
  (issued by S corporations)

Both forms use the same Part III box layout for the most common pass-through
items, but the form title and a handful of partner-only items (e.g. Box 4
Guaranteed payments on the 1065, which has no analog on the 1120-S) differ.
The model's ``source_type`` enum (``partnership`` | ``s_corp`` |
``estate_or_trust``) reflects this distinction. The ingester populates
``source_type`` from a content-layer heuristic (looking for "Form 1120-S" in
the first page text) and falls back to ``partnership`` when ambiguous.

Schedule K-1 (1065) Part III box layout (selected money lines mapped here):

- Box 1   — Ordinary business income (loss)
- Box 2   — Net rental real estate income (loss)
- Box 3   — Other net rental income (loss)
- Box 4   — Guaranteed payments (1065 only)
- Box 5   — Interest income
- Box 6a  — Ordinary dividends
- Box 6b  — Qualified dividends
- Box 7   — Royalties
- Box 8   — Net short-term capital gain (loss)
- Box 9a  — Net long-term capital gain (loss)
- Box 12  — Section 179 deduction
- Box 20  — Other information (codes Z=199A QBI item, AA=W-2 wages, AB=UBIA)

Schedule K-1 (1120-S) Part III layout differs in box numbering for the
section 179 / 199A items but uses the same labels. The model is layout-agnostic
because every line maps to a named ``ScheduleK1`` attribute, not a box number.

ScheduleK1 model fields mapped here (mirrors skill.scripts.models.ScheduleK1):

- source_name              (Part I item C entity name)
- source_ein               (Part I item A entity EIN)
- source_type              (1065 -> partnership; 1120-S -> s_corp)
- recipient_is_taxpayer    (synthetic — see SYNTHETIC FIELD NAMES below)
- ordinary_business_income (Box 1)
- net_rental_real_estate_income (Box 2)
- other_net_rental_income  (Box 3)
- guaranteed_payments      (Box 4 — partnership only)
- interest_income          (Box 5)
- ordinary_dividends       (Box 6a)
- qualified_dividends      (Box 6b)
- royalties                (Box 7)
- short_term_capital_gain_loss (Box 8)
- long_term_capital_gain_loss  (Box 9a)
- section_179_deduction    (Box 12 / 1120-S Box 11)
- qbi_qualified            (Box 20 code Z presence)
- section_199a_w2_wages    (Box 20 code AA / 1120-S Box 17 code V)
- section_199a_ubia        (Box 20 code AB / 1120-S Box 17 code V)
- other_items              (free-form code dictionary)

SYNTHETIC FIELD NAMES
---------------------
The keys in SCHEDULE_K1_FIELD_MAP below are SYNTHETIC placeholder names that
match the fixture produced by the test suite's ``_make_acroform_pdf`` helper.
The real IRS fillable Schedule K-1 forms use opaque internal field identifiers
like ``topmostSubform[0].Page1[0].PartIII[0].f1_15[0]`` — those need to be
captured from the official IRS fillable K-1 PDFs (one set per flavor) and
swapped in. See the TODO in the module footer.

Until the real names are in place, this ingester is useful for:

- verifying the plumbing (classifier -> base ingester -> path rewrite)
- providing a realistic fixture for downstream engine/integration tests
- documenting which K-1 boxes the skill currently cares about

CLASSIFIER LIMITATIONS
----------------------
The shared ``_classifier.py`` (off-limits to this ingester per CP8 fan-out
rules) currently has a single K-1 hint that maps both partnership and S-corp
filenames to ``DocumentKind.SCHEDULE_K1_1065``. This ingester therefore
overrides ``ingest()`` to run a content-layer "Form 1120-S" probe and patch
the document kind + ``source_type`` after the base ingest. See the v2 TODO
in the module footer.

Sources:
- https://www.irs.gov/forms-pubs/about-schedule-k-1-form-1065
- https://www.irs.gov/forms-pubs/about-schedule-k-1-form-1120-s
- skill.scripts.models.ScheduleK1
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pypdf

from skill.scripts.ingest._classifier import classify
from skill.scripts.ingest._pipeline import (
    DocumentKind,
    IngestResult,
    PartialReturn,
)
from skill.scripts.ingest._pypdf_acroform import PyPdfAcroFormIngester

# ---------------------------------------------------------------------------
# Synthetic field-name -> canonical path map
# ---------------------------------------------------------------------------
#
# Keys: SYNTHETIC widget names used by the test fixture (and any hand-crafted
#       fillable PDFs the dev workflow generates). Replace with real IRS
#       AcroForm identifiers in a follow-up patch (one set per K-1 flavor).
# Values: canonical CanonicalReturn paths under ``schedules_k1[0]``.
#
# Covered fields track every attribute on skill.scripts.models.ScheduleK1.
SCHEDULE_K1_FIELD_MAP: dict[str, str] = {
    # Part I — Information About the Partnership / S Corporation
    "source_name": "schedules_k1[0].source_name",
    "source_ein": "schedules_k1[0].source_ein",
    # source_type is normally populated by the content-layer probe in
    # ``ingest()`` below; the synthetic widget exists so a hand-crafted
    # fixture can override the probe (e.g. trust/estate K-1, where the
    # filename and content scan would otherwise default to partnership).
    "source_type": "schedules_k1[0].source_type",
    # Part II — Information About the Partner / Shareholder
    "recipient_is_taxpayer": "schedules_k1[0].recipient_is_taxpayer",
    # Part III Box 1 — Ordinary business income (loss)
    "ordinary_business_income": "schedules_k1[0].ordinary_business_income",
    # Part III Box 2 — Net rental real estate income (loss)
    "net_rental_real_estate_income": (
        "schedules_k1[0].net_rental_real_estate_income"
    ),
    # Part III Box 3 — Other net rental income (loss)
    "other_net_rental_income": "schedules_k1[0].other_net_rental_income",
    # Part III Box 4 — Guaranteed payments (1065 only; absent on 1120-S)
    "guaranteed_payments": "schedules_k1[0].guaranteed_payments",
    # Part III Box 5 — Interest income
    "interest_income": "schedules_k1[0].interest_income",
    # Part III Box 6a — Ordinary dividends
    "ordinary_dividends": "schedules_k1[0].ordinary_dividends",
    # Part III Box 6b — Qualified dividends
    "qualified_dividends": "schedules_k1[0].qualified_dividends",
    # Part III Box 7 — Royalties
    "royalties": "schedules_k1[0].royalties",
    # Part III Box 8 — Net short-term capital gain (loss)
    "short_term_capital_gain_loss": (
        "schedules_k1[0].short_term_capital_gain_loss"
    ),
    # Part III Box 9a — Net long-term capital gain (loss)
    "long_term_capital_gain_loss": (
        "schedules_k1[0].long_term_capital_gain_loss"
    ),
    # Part III Box 12 (1065) / Box 11 (1120-S) — Section 179 deduction
    "section_179_deduction": "schedules_k1[0].section_179_deduction",
    # Part III Box 20 code Z (1065) / Box 17 code V (1120-S) — QBI flag
    "qbi_qualified": "schedules_k1[0].qbi_qualified",
    # Part III Box 20 code AA (1065) / Box 17 code V (1120-S) — 199A W-2 wages
    "section_199a_w2_wages": "schedules_k1[0].section_199a_w2_wages",
    # Part III Box 20 code AB (1065) / Box 17 code V (1120-S) — 199A UBIA
    "section_199a_ubia": "schedules_k1[0].section_199a_ubia",
    # Catch-all for other coded items (Box 11/13/14/15/16/19/20 etc.)
    "other_items": "schedules_k1[0].other_items",
}


# Document kinds that this ingester handles. Both 1065 and 1120-S K-1s share
# the same canonical model, so they share the same field map.
_K1_KINDS: frozenset[DocumentKind] = frozenset(
    {DocumentKind.SCHEDULE_K1_1065, DocumentKind.SCHEDULE_K1_1120S}
)

# Content probe used to distinguish S-corp K-1s from partnership K-1s when
# the (off-limits) classifier returns the partnership default. Matches both
# "Form 1120-S" and "1120S" with optional whitespace/dash. Also matches the
# longer "shareholder's share" phrase from the form title.
_S_CORP_CONTENT_RE: re.Pattern[str] = re.compile(
    r"(form\s*1120-?s|shareholder['\u2019]?s\s+share)",
    re.IGNORECASE,
)


@dataclass
class SchedK1PyPdfAcroFormIngester(PyPdfAcroFormIngester):
    """Schedule K-1 specialization of the AcroForm ingester.

    Adds two behaviors on top of the base:

    1. ``can_handle`` returns True only for AcroForm PDFs that classify as one
       of the K-1 ``DocumentKind`` values. Other ingesters (1099-R, SSA-1099,
       etc.) handle their own kinds, so this strictness avoids spurious hits
       in the cascade.
    2. ``ingest`` runs a content-layer probe for "Form 1120-S" and, if found,
       patches ``document_kind`` to ``SCHEDULE_K1_1120S`` and injects
       ``source_type=s_corp`` into the partial. The probe runs even when an
       explicit ``source_type`` widget value is present, but the explicit
       widget value wins (last-write semantics on the partial).
    """

    name: str = "schedule_k1_acroform"

    def can_handle(self, path: Path) -> bool:
        if not super().can_handle(path):
            return False
        try:
            with path.open("rb") as fh:
                reader = pypdf.PdfReader(fh)
                first_text = ""
                if reader.pages:
                    try:
                        first_text = reader.pages[0].extract_text() or ""
                    except Exception:
                        first_text = ""
                kind = classify(path, first_text)
                return kind in _K1_KINDS
        except Exception:
            return False

    def ingest(self, path: Path) -> IngestResult:
        result = super().ingest(path)
        if not result.success:
            return result

        # Probe the first page text for an S-corp marker. The base ingester
        # has already classified by filename (1065 default), so we re-read
        # the text here to potentially upgrade the document kind.
        is_s_corp = False
        try:
            with path.open("rb") as fh:
                reader = pypdf.PdfReader(fh)
                if reader.pages:
                    try:
                        first_text = reader.pages[0].extract_text() or ""
                    except Exception:
                        first_text = ""
                    is_s_corp = bool(_S_CORP_CONTENT_RE.search(first_text))
        except Exception:
            # Probe is best-effort — if reading fails we keep the partnership
            # default that the base ingester already set.
            is_s_corp = False

        partial = result.partial

        # Bail out early if the kind isn't a K-1 at all (e.g. a stray non-K-1
        # PDF reached this ingester somehow). Defensive — the cascade should
        # never call us in that case because can_handle filters first.
        if partial.document_kind not in _K1_KINDS:
            return result

        # Detect whether the partial already carries an explicit source_type
        # widget value. If so, the explicit value wins; otherwise we inject
        # the content-probe-derived value.
        canonical_source_type_path = SCHEDULE_K1_FIELD_MAP["source_type"]
        explicit_source_type = any(
            f.path == canonical_source_type_path for f in partial.fields
        )

        if is_s_corp:
            partial.document_kind = DocumentKind.SCHEDULE_K1_1120S
            if not explicit_source_type:
                partial.add(
                    canonical_source_type_path,
                    "s_corp",
                    confidence=1.0,
                )
        else:
            partial.document_kind = DocumentKind.SCHEDULE_K1_1065
            if not explicit_source_type:
                partial.add(
                    canonical_source_type_path,
                    "partnership",
                    confidence=1.0,
                )

        return result


# Module-level singleton. The cascade wiring imports this directly. The same
# field map is registered for BOTH K-1 document kinds because the canonical
# ScheduleK1 model is layout-agnostic; the only difference between flavors is
# the ``source_type`` value, which the ``ingest()`` override patches in.
INGESTER: SchedK1PyPdfAcroFormIngester = SchedK1PyPdfAcroFormIngester(
    name="schedule_k1_acroform",
    field_map={
        DocumentKind.SCHEDULE_K1_1065: SCHEDULE_K1_FIELD_MAP,
        DocumentKind.SCHEDULE_K1_1120S: SCHEDULE_K1_FIELD_MAP,
    },
)


# TODO(taxes): Replace the SYNTHETIC keys in SCHEDULE_K1_FIELD_MAP with the
# real IRS AcroForm widget names from the official fillable K-1 PDFs.
# Procedure: download the IRS fillable Schedule K-1 (Form 1065) AND the
# fillable Schedule K-1 (Form 1120-S) for TY2025, open each with pypdf,
# iterate ``reader.get_fields()``, match each printed box label to its widget
# name, and swap into the map above. Note that the two flavors use DIFFERENT
# widget identifiers for the same logical box (1065 puts ordinary business
# income in box 1; 1120-S also puts it in box 1 but the underlying
# ``topmostSubform[0]...`` field path differs). The cleanest fix is to split
# SCHEDULE_K1_FIELD_MAP into ``_FIELD_MAP_1065`` and ``_FIELD_MAP_1120S`` and
# wire them through the per-kind ``field_map`` dict on INGESTER.
#
# TODO(taxes): the shared ``_classifier.py`` (off-limits in this fan-out)
# currently has a single K-1 filename pattern that resolves to
# ``SCHEDULE_K1_1065`` for both partnership and S-corp filenames, and a
# single content pattern (``Schedule K-1``) that does the same. The S-corp
# detection in this ingester is therefore implemented as a content-layer
# probe inside ``ingest()`` (looking for "Form 1120-S" or
# "shareholder['\u2019]?s share" in the first-page text). The cleaner fix is
# to add a dedicated 1120-S filename pattern (e.g. r"k-?1[-_ ]?(1120s|scorp)")
# and a content pattern (re.compile(r"Form\s*1120-?S", re.I)) to
# ``_classifier.py``, then drop the probe from this ingester.
#
# TODO(taxes): the ``other_items`` field is a free-form ``dict[str, Any]`` on
# ScheduleK1, but the synthetic widget here stores it as a single string. Once
# the real K-1 widget names are wired, ``other_items`` will need a more
# structured mapping (one widget per Box 11 / Box 13 / Box 14 / Box 20 code)
# OR a dedicated rewriting step in the ingestion-to-canonical layer that
# parses the raw widget string into the dict. Recommend the structured-widget
# approach because it preserves audit trails per-code.
