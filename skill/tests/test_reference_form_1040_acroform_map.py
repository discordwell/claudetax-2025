"""Schema and consistency checks for the Form 1040 AcroForm widget map.

This test file exists solely to keep
``skill/reference/form-1040-acroform-map.json`` honest. It does not
execute tax math and does not open the IRS PDF — it only loads the JSON
we already produced and asserts structural invariants.

The invariants asserted here are load-bearing for the FUTURE wave that
replaces the current reportlab scaffold in
``skill.scripts.output.form_1040`` with a real AcroForm overlay:

* every Layer 1 ``Form1040Fields`` dataclass attribute (other than the
  trio of free-text header strings and computed ``_ZERO`` helpers) has
  a mapping entry;
* every mapping entry has the required sub-fields;
* no widget name is claimed twice across ``mapping`` /
  ``computed_copies`` / ``filing_status_checkboxes``;
* ``mapped_count + unmapped_count == total_widgets``;
* the source PDF SHA-256 looks like a real hex-encoded SHA-256.

If the IRS re-issues the f1040.pdf and the widget mapping has to be
regenerated, these invariants should STILL hold — any failure here
means the regeneration script produced an inconsistent artifact.
"""
from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import pytest

from skill.scripts.output.form_1040 import Form1040Fields


REFERENCE_JSON = (
    Path(__file__).resolve().parents[1]
    / "reference"
    / "form-1040-acroform-map.json"
)

# Layer 1 header fields that are STRINGS (not Decimal), intentionally
# NOT part of the numeric line mapping — some are mapped in the JSON
# (taxpayer_name, spouse_name) and some are handled specially
# (filing_status is a checkbox group).
_HEADER_STRING_FIELDS = {"filing_status", "taxpayer_name", "spouse_name"}


@pytest.fixture(scope="module")
def acroform_map() -> dict:
    assert REFERENCE_JSON.exists(), (
        f"missing reference JSON: {REFERENCE_JSON}. "
        "Regenerate it with the steps in form-1040-acroform-methodology.md."
    )
    return json.loads(REFERENCE_JSON.read_text())


# ---------------------------------------------------------------------------
# Top-level schema
# ---------------------------------------------------------------------------


def test_top_level_keys_present(acroform_map):
    required = {
        "source_pdf_url",
        "source_pdf_sha256",
        "fetched_at",
        "pypdf_version",
        "total_widgets",
        "mapped_count",
        "unmapped_count",
        "mapping",
        "unmapped_widgets",
    }
    missing = required - set(acroform_map)
    assert not missing, f"missing top-level keys: {sorted(missing)}"


def test_source_url_is_irs(acroform_map):
    url = acroform_map["source_pdf_url"]
    assert url.startswith("https://www.irs.gov/"), (
        f"source_pdf_url must be an IRS-hosted URL; got {url!r}"
    )
    assert url.endswith(".pdf"), f"source_pdf_url must be a .pdf; got {url!r}"


def test_sha256_looks_valid(acroform_map):
    sha = acroform_map["source_pdf_sha256"]
    assert re.fullmatch(r"[0-9a-f]{64}", sha), (
        f"source_pdf_sha256 must be 64 lowercase hex chars; got {sha!r}"
    )


def test_total_widgets_is_positive_integer(acroform_map):
    total = acroform_map["total_widgets"]
    assert isinstance(total, int) and total > 0, (
        f"total_widgets must be a positive int; got {total!r}"
    )


def test_counts_sum_to_total(acroform_map):
    mapped = acroform_map["mapped_count"]
    unmapped = acroform_map["unmapped_count"]
    total = acroform_map["total_widgets"]
    assert mapped + unmapped == total, (
        f"mapped_count ({mapped}) + unmapped_count ({unmapped}) "
        f"must equal total_widgets ({total})"
    )


# ---------------------------------------------------------------------------
# Mapping entries
# ---------------------------------------------------------------------------


_REQUIRED_ENTRY_KEYS = {"widget_name", "type", "page"}


def _entry_has_required_keys(entry: dict) -> bool:
    return _REQUIRED_ENTRY_KEYS <= set(entry)


def test_every_mapping_entry_has_required_keys(acroform_map):
    for sem_name, entry in acroform_map["mapping"].items():
        assert isinstance(entry, dict), f"mapping[{sem_name}] must be a dict"
        missing = _REQUIRED_ENTRY_KEYS - set(entry)
        assert not missing, (
            f"mapping[{sem_name}] missing required keys: {sorted(missing)}"
        )
        assert entry["page"] in (1, 2), (
            f"mapping[{sem_name}].page must be 1 or 2; got {entry['page']!r}"
        )
        assert entry["type"] in ("text", "checkbox", "checkbox_group"), (
            f"mapping[{sem_name}].type must be text/checkbox/checkbox_group; "
            f"got {entry['type']!r}"
        )


def test_every_mapping_semantic_name_matches_a_form1040fields_attr(acroform_map):
    """Every key in `mapping` must be a real Form1040Fields attribute."""
    valid_attrs = {f.name for f in dataclasses.fields(Form1040Fields)}
    for sem_name in acroform_map["mapping"]:
        assert sem_name in valid_attrs, (
            f"mapping key {sem_name!r} is not a Form1040Fields attribute. "
            f"Valid attributes: {sorted(valid_attrs)}"
        )


def test_every_numeric_form1040fields_field_is_mapped(acroform_map):
    """Every numeric Layer 1 field must have a mapping entry.

    Layer 1 has three free-text header fields (filing_status,
    taxpayer_name, spouse_name). The numeric (Decimal) fields all
    correspond to Form 1040 line amounts and MUST be mapped so the
    future AcroForm overlay pass can write them.
    """
    numeric = [
        f.name
        for f in dataclasses.fields(Form1040Fields)
        if f.name not in _HEADER_STRING_FIELDS
    ]
    mapped_keys = set(acroform_map["mapping"])
    unmapped = [name for name in numeric if name not in mapped_keys]
    assert not unmapped, (
        f"these numeric Form1040Fields attributes have no widget mapping: "
        f"{unmapped}"
    )


# ---------------------------------------------------------------------------
# Uniqueness across mapping / computed_copies / filing_status_checkboxes
# ---------------------------------------------------------------------------


def _collect_mapped_widget_names(acroform_map) -> list[tuple[str, str]]:
    """Return a list of (source, widget_name) tuples for uniqueness checks."""
    out: list[tuple[str, str]] = []
    for sem, entry in acroform_map["mapping"].items():
        wn = entry.get("widget_name")
        # A pseudo wildcard like `c1_8[*]` is used for checkbox *groups*
        # (filing_status) where the real widgets live under
        # filing_status_checkboxes. Skip those from uniqueness.
        if wn and "*" not in wn:
            out.append((f"mapping.{sem}", wn))
    for sem, copies in acroform_map.get("computed_copies", {}).items():
        for entry in copies:
            wn = entry.get("widget_name")
            if wn:
                out.append((f"computed_copies.{sem}", wn))
    for status, entry in acroform_map.get("filing_status_checkboxes", {}).items():
        wn = entry.get("widget_name")
        if wn:
            out.append((f"filing_status_checkboxes.{status}", wn))
    return out


def test_no_duplicate_widget_names_across_mapping(acroform_map):
    entries = _collect_mapped_widget_names(acroform_map)
    seen: dict[str, list[str]] = {}
    for src, wn in entries:
        seen.setdefault(wn, []).append(src)
    dupes = {wn: srcs for wn, srcs in seen.items() if len(srcs) > 1}
    assert not dupes, (
        f"widget names claimed by more than one mapping slot: {dupes}"
    )


def test_mapped_count_matches_unique_widget_names(acroform_map):
    unique = len({wn for _, wn in _collect_mapped_widget_names(acroform_map)})
    assert unique == acroform_map["mapped_count"], (
        f"mapped_count={acroform_map['mapped_count']} but unique widget "
        f"names across mapping+copies+filing_status_checkboxes={unique}"
    )


# ---------------------------------------------------------------------------
# Unmapped widgets
# ---------------------------------------------------------------------------


def test_unmapped_widgets_have_required_keys(acroform_map):
    required = {"widget_name", "type", "page", "reason"}
    for i, entry in enumerate(acroform_map["unmapped_widgets"]):
        missing = required - set(entry)
        assert not missing, (
            f"unmapped_widgets[{i}] missing required keys: {sorted(missing)}; "
            f"entry={entry!r}"
        )
        assert entry["page"] in (1, 2), (
            f"unmapped_widgets[{i}].page must be 1 or 2; got {entry['page']!r}"
        )
        assert entry["reason"], (
            f"unmapped_widgets[{i}].reason must be non-empty; got empty string"
        )


def test_unmapped_widgets_disjoint_from_mapped(acroform_map):
    mapped_set = {wn for _, wn in _collect_mapped_widget_names(acroform_map)}
    offenders = [
        e["widget_name"]
        for e in acroform_map["unmapped_widgets"]
        if e["widget_name"] in mapped_set
    ]
    assert not offenders, (
        f"unmapped_widgets contains names that are ALSO mapped: {offenders}"
    )


def test_filing_status_checkboxes_cover_all_five(acroform_map):
    """If filing_status_checkboxes is present, it must cover the five
    IRS filing statuses. (This guards against silent partial edits.)"""
    fs = acroform_map.get("filing_status_checkboxes")
    if fs is None:
        pytest.skip("filing_status_checkboxes not present")
    expected = {"SINGLE", "MFJ", "MFS", "HOH", "QSS"}
    actual = set(fs)
    assert actual == expected, (
        f"filing_status_checkboxes must have exactly the five IRS filing "
        f"statuses; got {sorted(actual)}"
    )
    for status, entry in fs.items():
        assert entry.get("widget_name"), (
            f"filing_status_checkboxes[{status}] has no widget_name"
        )
        assert entry.get("type") == "checkbox", (
            f"filing_status_checkboxes[{status}].type must be 'checkbox'; "
            f"got {entry.get('type')!r}"
        )
