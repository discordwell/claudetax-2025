"""Schema and consistency checks for the Schedule A/B/C/SE AcroForm maps.

This test file mirrors ``test_reference_form_1040_acroform_map.py`` but
covers the four wave-5 schedule maps:

* ``skill/reference/schedule-a-acroform-map.json``
* ``skill/reference/schedule-b-acroform-map.json``
* ``skill/reference/schedule-c-acroform-map.json``
* ``skill/reference/schedule-se-acroform-map.json``

Invariants asserted (per map):

* Top-level keys present: source_pdf_url, source_pdf_sha256, fetched_at,
  pypdf_version, total_widgets, mapped_count, unmapped_count, mapping,
  unmapped_widgets.
* mapped_count + unmapped_count == total_widgets.
* source_pdf_sha256 is a 64-char lowercase hex.
* source_pdf_url is the IRS-hosted .pdf.
* Every mapping entry has widget_name + type + page.
* Every mapping semantic name corresponds to an attribute on the
  matching Layer 1 dataclass (allowing repeating-row tuple fields whose
  representative entry uses a wildcard widget_name).
* No duplicate widget names across mapping + repeating-row arrays.
* unmapped_widgets entries have widget_name + type + page + reason.
* unmapped_widgets are disjoint from mapped widgets.
* The source PDF on disk matches the SHA-256 (so future regeneration
  cannot drift silently).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from pathlib import Path

import pytest

from skill.scripts.output.schedule_a import ScheduleAFields
from skill.scripts.output.schedule_b import ScheduleBFields
from skill.scripts.output.schedule_c import ScheduleCFields
from skill.scripts.output.schedule_se import ScheduleSEFields


_REPO_ROOT = Path(__file__).resolve().parents[1]
_REFERENCE_DIR = _REPO_ROOT / "reference"
_IRS_FORMS_DIR = _REFERENCE_DIR / "irs_forms"

_REQUIRED_TOP_KEYS = {
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


_SCHEDULE_SPECS = [
    {
        "code": "a",
        "json_path": _REFERENCE_DIR / "schedule-a-acroform-map.json",
        "pdf_path": _IRS_FORMS_DIR / "f1040sa.pdf",
        "dataclass": ScheduleAFields,
    },
    {
        "code": "b",
        "json_path": _REFERENCE_DIR / "schedule-b-acroform-map.json",
        "pdf_path": _IRS_FORMS_DIR / "f1040sb.pdf",
        "dataclass": ScheduleBFields,
    },
    {
        "code": "c",
        "json_path": _REFERENCE_DIR / "schedule-c-acroform-map.json",
        "pdf_path": _IRS_FORMS_DIR / "f1040sc.pdf",
        "dataclass": ScheduleCFields,
    },
    {
        "code": "se",
        "json_path": _REFERENCE_DIR / "schedule-se-acroform-map.json",
        "pdf_path": _IRS_FORMS_DIR / "f1040sse.pdf",
        "dataclass": ScheduleSEFields,
    },
]


@pytest.fixture(scope="module", params=_SCHEDULE_SPECS, ids=lambda s: s["code"])
def schedule_spec(request):
    spec = request.param
    assert spec["json_path"].exists(), (
        f"missing reference JSON: {spec['json_path']}"
    )
    return spec


@pytest.fixture(scope="module")
def schedule_map(schedule_spec):
    return json.loads(schedule_spec["json_path"].read_text())


# ---------------------------------------------------------------------------
# Top-level schema
# ---------------------------------------------------------------------------


def test_top_level_keys_present(schedule_map):
    missing = _REQUIRED_TOP_KEYS - set(schedule_map)
    assert not missing, f"missing top-level keys: {sorted(missing)}"


def test_source_url_is_irs(schedule_map):
    url = schedule_map["source_pdf_url"]
    assert url.startswith("https://www.irs.gov/"), (
        f"source_pdf_url must be an IRS-hosted URL; got {url!r}"
    )
    assert url.endswith(".pdf"), f"source_pdf_url must be a .pdf; got {url!r}"


def test_sha256_looks_valid(schedule_map):
    sha = schedule_map["source_pdf_sha256"]
    assert re.fullmatch(r"[0-9a-f]{64}", sha), (
        f"source_pdf_sha256 must be 64 lowercase hex chars; got {sha!r}"
    )


def test_total_widgets_is_positive_integer(schedule_map):
    total = schedule_map["total_widgets"]
    assert isinstance(total, int) and total > 0, (
        f"total_widgets must be a positive int; got {total!r}"
    )


def test_counts_sum_to_total(schedule_map):
    mapped = schedule_map["mapped_count"]
    unmapped = schedule_map["unmapped_count"]
    total = schedule_map["total_widgets"]
    assert mapped + unmapped == total, (
        f"mapped_count ({mapped}) + unmapped_count ({unmapped}) "
        f"must equal total_widgets ({total})"
    )


# ---------------------------------------------------------------------------
# Mapping entries
# ---------------------------------------------------------------------------


_REQUIRED_ENTRY_KEYS = {"widget_name", "type", "page"}


def test_every_mapping_entry_has_required_keys(schedule_map):
    for sem_name, entry in schedule_map["mapping"].items():
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


def test_every_mapping_semantic_name_matches_dataclass_attr(
    schedule_map, schedule_spec
):
    """Every key in `mapping` must be a real Layer 1 dataclass attribute."""
    valid_attrs = {f.name for f in dataclasses.fields(schedule_spec["dataclass"])}
    for sem_name in schedule_map["mapping"]:
        assert sem_name in valid_attrs, (
            f"mapping key {sem_name!r} is not a {schedule_spec['dataclass'].__name__} "
            f"attribute. Valid: {sorted(valid_attrs)}"
        )


# ---------------------------------------------------------------------------
# Unmapped widgets
# ---------------------------------------------------------------------------


def test_unmapped_widgets_have_required_keys(schedule_map):
    required = {"widget_name", "type", "page", "reason"}
    for i, entry in enumerate(schedule_map["unmapped_widgets"]):
        missing = required - set(entry)
        assert not missing, (
            f"unmapped_widgets[{i}] missing required keys: {sorted(missing)}; "
            f"entry={entry!r}"
        )
        assert entry["page"] in (1, 2), (
            f"unmapped_widgets[{i}].page must be 1 or 2; got {entry['page']!r}"
        )
        assert entry["reason"], (
            f"unmapped_widgets[{i}].reason must be non-empty"
        )


def _collect_concrete_widget_names(schedule_map) -> list[tuple[str, str]]:
    """Collect (source, widget_name) for all CONCRETE (non-wildcard) entries."""
    out: list[tuple[str, str]] = []
    for sem, entry in schedule_map["mapping"].items():
        wn = entry.get("widget_name", "")
        if wn and "*" not in wn and "f1_NN" not in wn and "f2_NN" not in wn and ".." not in wn:
            out.append((f"mapping.{sem}", wn))
    for key, rows in schedule_map.items():
        if not key.endswith("_widgets") or not isinstance(rows, list):
            continue
        for i, row in enumerate(rows):
            for slot_key, slot in row.items():
                if not isinstance(slot, dict):
                    continue
                wn = slot.get("widget_name")
                if wn and "*" not in wn:
                    out.append((f"{key}[{i}].{slot_key}", wn))
    return out


def test_no_duplicate_concrete_widget_names(schedule_map):
    entries = _collect_concrete_widget_names(schedule_map)
    seen: dict[str, list[str]] = {}
    for src, wn in entries:
        seen.setdefault(wn, []).append(src)
    dupes = {wn: srcs for wn, srcs in seen.items() if len(srcs) > 1}
    assert not dupes, (
        f"widget names claimed by more than one mapping slot: {dupes}"
    )


def test_unmapped_widgets_disjoint_from_mapped(schedule_map):
    mapped_set = {wn for _, wn in _collect_concrete_widget_names(schedule_map)}
    offenders = [
        e["widget_name"]
        for e in schedule_map["unmapped_widgets"]
        if e["widget_name"] in mapped_set
    ]
    assert not offenders, (
        f"unmapped_widgets contains names that are ALSO mapped: {offenders}"
    )


# ---------------------------------------------------------------------------
# Source PDF integrity
# ---------------------------------------------------------------------------


def test_source_pdf_exists_on_disk(schedule_spec):
    """The IRS source PDF must exist at the documented path."""
    assert schedule_spec["pdf_path"].exists(), (
        f"missing IRS source PDF: {schedule_spec['pdf_path']}. "
        "Re-run the widget-map regenerator to download it."
    )


def test_source_pdf_sha256_matches(schedule_spec, schedule_map):
    """The on-disk PDF must match the SHA-256 recorded in the JSON.

    A mismatch means either (a) the IRS silently re-issued the PDF — in
    which case the entire mapping must be revalidated — or (b) someone
    edited the PDF in tree, which would be wrong.
    """
    actual = hashlib.sha256(schedule_spec["pdf_path"].read_bytes()).hexdigest()
    expected = schedule_map["source_pdf_sha256"]
    assert actual == expected, (
        f"PDF SHA-256 mismatch for {schedule_spec['pdf_path']}: "
        f"expected {expected}, got {actual}"
    )


# ---------------------------------------------------------------------------
# Schedule-specific structural assertions
# ---------------------------------------------------------------------------


def test_schedule_b_part_i_has_14_row_widgets():
    m = json.loads((_REFERENCE_DIR / "schedule-b-acroform-map.json").read_text())
    rows = m["part_i_line_1_rows_widgets"]
    assert len(rows) == 14
    for r in rows:
        assert "payer_widget" in r and "amount_widget" in r
        assert r["payer_widget"]["widget_name"]
        assert r["amount_widget"]["widget_name"]


def test_schedule_b_part_ii_has_15_row_widgets():
    m = json.loads((_REFERENCE_DIR / "schedule-b-acroform-map.json").read_text())
    rows = m["part_ii_line_5_rows_widgets"]
    assert len(rows) == 15


def test_schedule_c_part_v_has_9_row_widgets():
    m = json.loads((_REFERENCE_DIR / "schedule-c-acroform-map.json").read_text())
    rows = m["part_v_other_expenses_widgets"]
    assert len(rows) == 9
    for r in rows:
        assert "description_widget" in r and "amount_widget" in r


def test_schedule_se_uses_ty2025_ss_wage_base_in_notes():
    """The Schedule SE map must mention the TY2025 SS wage base context.

    Pin the notes prose so it doesn't drift away from the constant
    SS_WAGE_BASE_TY2025 = 176100.
    """
    m = json.loads((_REFERENCE_DIR / "schedule-se-acroform-map.json").read_text())
    joined_notes = " ".join(m["notes"])
    assert "176,100" in joined_notes or "176100" in joined_notes


def test_all_four_maps_have_form_year_2025():
    """All four maps must declare form_year=2025 (the IRS has already
    published TY2025 PDFs for all four schedules at the canonical URLs)."""
    for spec in _SCHEDULE_SPECS:
        m = json.loads(spec["json_path"].read_text())
        assert m.get("form_year") == 2025, (
            f"{spec['code']} map form_year must be 2025; got {m.get('form_year')}"
        )


def test_schedule_a_maps_all_decimal_layer1_fields():
    """Every Decimal field on ScheduleAFields should be in the mapping
    (header strings and the elected_sales_tax bool are exceptions)."""
    m = json.loads((_REFERENCE_DIR / "schedule-a-acroform-map.json").read_text())
    mapped = set(m["mapping"])
    expected_unmapped = {
        "filing_status",  # carried from Form 1040
        "spouse_name",    # not on Schedule A header
    }
    for f in dataclasses.fields(ScheduleAFields):
        if f.name in expected_unmapped:
            continue
        # Booleans like line_5a_elected_sales_tax map to a checkbox.
        # Numeric Decimal fields all should be mapped.
        if f.name == "line_5e_salt_cap_applied":
            # This is a constant carried by Layer 1 (the cap value), NOT
            # a widget on the IRS form. Skip.
            continue
        assert f.name in mapped, (
            f"ScheduleAFields.{f.name} has no entry in schedule-a-acroform-map.json"
        )


def test_schedule_se_maps_all_decimal_layer1_fields():
    """Every Decimal field on ScheduleSEFields must be in the mapping."""
    m = json.loads((_REFERENCE_DIR / "schedule-se-acroform-map.json").read_text())
    mapped = set(m["mapping"])
    for f in dataclasses.fields(ScheduleSEFields):
        if f.name in ("taxpayer_name", "taxpayer_ssn"):
            # Header strings — both ARE mapped.
            assert f.name in mapped
            continue
        assert f.name in mapped, (
            f"ScheduleSEFields.{f.name} has no entry in schedule-se-acroform-map.json"
        )
