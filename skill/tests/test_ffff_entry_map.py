"""Tests for ``skill.scripts.output.ffff_entry_map``.

The FFFF entry map translates a computed ``CanonicalReturn`` into a
field-by-field transcript of every entry the taxpayer must type into
freefillableforms.com. These tests exercise:

* The entry count and required-line coverage for a simple W-2 return
  (must include 1a, 1z, 9, 10, 11, 12, 15, 16, 24, 25a, 33, 34).
* Itemized return produces Schedule A entries (``1040-SA`` form).
* Schedule C + SE return produces per-business ``1040-SC-1`` +
  ``1040-SSE-1`` entries.
* ``to_text()`` is pure ASCII (no binary, no weird escape sequences).
* ``to_json()`` round-trips and holds a metadata header.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.calc.engine import compute
from skill.scripts.models import CanonicalReturn
from skill.scripts.output.ffff_entry_map import (
    FFFFEntry,
    FFFFEntryMap,
    build_ffff_entry_map,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _computed(fixture_name: str) -> CanonicalReturn:
    data = json.loads((FIXTURES / fixture_name / "input.json").read_text())
    canonical = CanonicalReturn.model_validate(data)
    return compute(canonical)


# ---------------------------------------------------------------------------
# 1. Simple W-2 Single return
# ---------------------------------------------------------------------------


REQUIRED_1040_LINES: tuple[str, ...] = (
    "1a",
    "1z",
    "9",
    "10",
    "11",
    "12",
    "15",
    "16",
    "24",
    "25a",
    "33",
    "34",
)


class TestSimpleW2:
    """$65k W-2 Single return — the baseline scenario."""

    def test_produces_entry_map(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        assert isinstance(entry_map, FFFFEntryMap)
        assert entry_map.tax_year == 2025
        assert entry_map.filing_status == "Single"
        assert entry_map.taxpayer_name == "Alex Doe"

    def test_has_at_least_ten_form_1040_lines(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        form_1040_entries = [e for e in entry_map.entries if e.form == "1040"]
        assert len(form_1040_entries) >= 10

    def test_includes_all_required_form_1040_lines(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        present_lines = {e.line for e in entry_map.entries if e.form == "1040"}
        missing = [ln for ln in REQUIRED_1040_LINES if ln not in present_lines]
        assert missing == [], f"missing required 1040 lines: {missing}"

    def test_line_1a_value_is_65000(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        line_1a = next(
            e for e in entry_map.entries if e.form == "1040" and e.line == "1a"
        )
        assert line_1a.value == "65,000.00"

    def test_no_schedule_a_for_standard_deduction(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        assert not any(e.form == "1040-SA" for e in entry_map.entries)

    def test_no_schedule_b_below_threshold(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        assert not any(e.form == "1040-SB" for e in entry_map.entries)

    def test_no_schedule_c_for_pure_w2_return(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        assert not any(e.form.startswith("1040-SC") for e in entry_map.entries)

    def test_no_schedule_se_for_pure_w2_return(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        assert not any(e.form.startswith("1040-SSE") for e in entry_map.entries)


# ---------------------------------------------------------------------------
# 2. Itemized MFJ return with investments
# ---------------------------------------------------------------------------


class TestItemizedWithInvestments:
    """MFJ with W-2s, 1099-INT/DIV/B, itemized deductions."""

    def test_produces_schedule_a_entries(self):
        canonical = _computed("w2_investments_itemized")
        entry_map = build_ffff_entry_map(canonical)
        sa_entries = [e for e in entry_map.entries if e.form == "1040-SA"]
        assert len(sa_entries) > 0

    def test_schedule_a_includes_salt_subtotal_and_cap(self):
        canonical = _computed("w2_investments_itemized")
        entry_map = build_ffff_entry_map(canonical)
        sa_lines = {e.line for e in entry_map.entries if e.form == "1040-SA"}
        assert "5a" in sa_lines
        assert "5d" in sa_lines
        assert "5e" in sa_lines
        assert "17" in sa_lines

    def test_schedule_b_triggered_by_investment_interest(self):
        """$3000 in taxable interest > $1,500 threshold -> Schedule B."""
        canonical = _computed("w2_investments_itemized")
        entry_map = build_ffff_entry_map(canonical)
        sb_entries = [e for e in entry_map.entries if e.form == "1040-SB"]
        assert len(sb_entries) > 0
        # Line 2 (total interest) and line 6 (total dividends) should be emitted
        sb_lines = {e.line for e in entry_map.entries if e.form == "1040-SB"}
        assert "2" in sb_lines
        assert "6" in sb_lines

    def test_schedule_b_payer_rows_emitted(self):
        canonical = _computed("w2_investments_itemized")
        entry_map = build_ffff_entry_map(canonical)
        # At least one payer row (line 1.1.payer + line 1.1.amount) for interest.
        sb_lines = {e.line for e in entry_map.entries if e.form == "1040-SB"}
        assert "1.1.payer" in sb_lines
        assert "1.1.amount" in sb_lines

    def test_spouse_entries_present_for_mfj(self):
        canonical = _computed("w2_investments_itemized")
        entry_map = build_ffff_entry_map(canonical)
        spouse_entries = [
            e for e in entry_map.entries
            if e.form == "1040" and e.line.startswith("header.spouse")
        ]
        assert len(spouse_entries) == 2  # name + ssn


# ---------------------------------------------------------------------------
# 3. Schedule C + SE return
# ---------------------------------------------------------------------------


class TestScheduleCAndSE:
    """Sole proprietor with SE income above $400 floor."""

    def test_produces_schedule_c_entries(self):
        canonical = _computed("se_home_office")
        entry_map = build_ffff_entry_map(canonical)
        sc_entries = [e for e in entry_map.entries if e.form == "1040-SC-1"]
        assert len(sc_entries) > 0

    def test_schedule_c_form_name_uses_index_suffix(self):
        canonical = _computed("se_home_office")
        entry_map = build_ffff_entry_map(canonical)
        sc_forms = {e.form for e in entry_map.entries if e.form.startswith("1040-SC-")}
        # Exactly one business -> exactly one SC form
        assert sc_forms == {"1040-SC-1"}

    def test_schedule_c_includes_line_31_net_profit(self):
        canonical = _computed("se_home_office")
        entry_map = build_ffff_entry_map(canonical)
        line_31 = next(
            e for e in entry_map.entries
            if e.form == "1040-SC-1" and e.line == "31"
        )
        # Net profit should be positive for this profitable fixture.
        assert line_31.value != "0.00"

    def test_produces_schedule_se_entries(self):
        canonical = _computed("se_home_office")
        entry_map = build_ffff_entry_map(canonical)
        sse_entries = [e for e in entry_map.entries if e.form == "1040-SSE-1"]
        assert len(sse_entries) > 0

    def test_schedule_se_includes_se_tax_line_12(self):
        canonical = _computed("se_home_office")
        entry_map = build_ffff_entry_map(canonical)
        line_12 = next(
            e for e in entry_map.entries
            if e.form == "1040-SSE-1" and e.line == "12"
        )
        assert line_12.value != "0.00"


# ---------------------------------------------------------------------------
# 4. Serialization: to_text() + to_json()
# ---------------------------------------------------------------------------


class TestToText:
    def test_text_is_pure_ascii(self):
        """``to_text`` must not emit any non-ASCII chars (no em-dashes,
        no Unicode smart quotes, no escape sequences)."""
        canonical = _computed("w2_investments_itemized")
        text = build_ffff_entry_map(canonical).to_text()
        non_ascii = [c for c in text if ord(c) > 127]
        assert non_ascii == [], f"non-ASCII chars found: {non_ascii[:5]}"

    def test_text_contains_heading_and_taxpayer(self):
        canonical = _computed("simple_w2_standard")
        text = build_ffff_entry_map(canonical).to_text()
        assert "FFFF Entry Transcript" in text
        assert "Alex Doe" in text
        assert "Tax Year 2025" in text
        assert "Form 1040" in text

    def test_text_groups_by_form(self):
        """Each form appears once as a heading, in emit order."""
        canonical = _computed("se_home_office")
        text = build_ffff_entry_map(canonical).to_text()
        assert "Form 1040" in text
        assert "Schedule C #1" in text
        assert "Schedule SE #1" in text
        # Form 1040 heading comes before Schedule C heading
        assert text.index("Form 1040") < text.index("Schedule C #1")

    def test_text_includes_line_values(self):
        canonical = _computed("simple_w2_standard")
        text = build_ffff_entry_map(canonical).to_text()
        # Form 1040 line 1a should show $65,000.00
        assert "65,000.00" in text


class TestToJson:
    def test_json_round_trips(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        parsed = json.loads(entry_map.to_json())
        assert parsed["tax_year"] == 2025
        assert parsed["taxpayer_name"] == "Alex Doe"
        assert parsed["filing_status"] == "Single"
        assert isinstance(parsed["entries"], list)
        assert len(parsed["entries"]) == len(entry_map.entries)

    def test_json_entry_shape(self):
        canonical = _computed("simple_w2_standard")
        parsed = json.loads(build_ffff_entry_map(canonical).to_json())
        first = parsed["entries"][0]
        assert set(first.keys()) == {
            "form", "line", "value", "description", "note"
        }

    def test_json_preserves_emit_order(self):
        canonical = _computed("se_home_office")
        entry_map = build_ffff_entry_map(canonical)
        parsed = json.loads(entry_map.to_json())
        original_forms = [e.form for e in entry_map.entries]
        parsed_forms = [e["form"] for e in parsed["entries"]]
        assert original_forms == parsed_forms


# ---------------------------------------------------------------------------
# 5. FFFFEntry dataclass sanity
# ---------------------------------------------------------------------------


class TestFFFFEntryDataclass:
    def test_entry_is_frozen(self):
        entry = FFFFEntry(
            form="1040",
            line="1a",
            value="65,000.00",
            description="Total W-2 box 1 wages",
        )
        with pytest.raises((AttributeError, Exception)):
            entry.line = "1b"  # type: ignore[misc]

    def test_note_defaults_to_none(self):
        entry = FFFFEntry(
            form="1040",
            line="1a",
            value="1.00",
            description="d",
        )
        assert entry.note is None

    def test_entry_map_is_frozen(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        with pytest.raises((AttributeError, Exception)):
            entry_map.tax_year = 2099  # type: ignore[misc]
