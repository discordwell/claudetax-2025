"""Tests for skill.scripts.output.schedule_b — Schedule B PDF scaffold.

Two layers under test:

* Layer 1 — ``compute_schedule_b_fields``: assert that values on the
  returned dataclass match the expected Schedule B line numbers for
  handcrafted and golden-fixture CanonicalReturns. These tests do NOT
  require the engine to have run; Schedule B is derived directly from
  the canonical 1099-INT / 1099-DIV lists.

* Layer 2 — ``render_schedule_b_pdf``: write a scaffold PDF, reopen it
  with pypdf, and assert the extracted text contains the header and a
  numeric value.

Threshold coverage: tests pin the strict $1,500 "must-file" boundary
from the IRS 2024 Schedule B instructions.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.models import CanonicalReturn
from skill.scripts.output.schedule_b import (
    SCHEDULE_B_THRESHOLD,
    ScheduleBFields,
    ScheduleBPayerRow,
    compute_schedule_b_fields,
    render_schedule_b_pdf,
    schedule_b_required,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_return_payload() -> dict:
    """A minimal single-filer canonical return payload with no 1099s."""
    return {
        "schema_version": "0.1.0",
        "tax_year": 2025,
        "filing_status": "single",
        "taxpayer": {
            "first_name": "Ivy",
            "last_name": "Investor",
            "ssn": "111-22-3333",
            "date_of_birth": "1985-06-01",
            "is_blind": False,
            "is_age_65_or_older": False,
        },
        "address": {
            "street1": "1 Dividend Way",
            "city": "Springfield",
            "state": "IL",
            "zip": "62701",
        },
        "itemize_deductions": False,
    }


def _return_with(
    *,
    forms_1099_int: list[dict] | None = None,
    forms_1099_div: list[dict] | None = None,
) -> CanonicalReturn:
    payload = _base_return_payload()
    if forms_1099_int is not None:
        payload["forms_1099_int"] = forms_1099_int
    if forms_1099_div is not None:
        payload["forms_1099_div"] = forms_1099_div
    return CanonicalReturn.model_validate(payload)


def _load_fixture(fixtures_dir: Path, name: str) -> CanonicalReturn:
    data = json.loads((fixtures_dir / name / "input.json").read_text())
    return CanonicalReturn.model_validate(data)


# ---------------------------------------------------------------------------
# Layer 1: field-level mapping
# ---------------------------------------------------------------------------


def test_empty_return_produces_zero_schedule_b_fields() -> None:
    r = _return_with()
    fields = compute_schedule_b_fields(r)

    assert isinstance(fields, ScheduleBFields)
    assert fields.taxpayer_name == "Ivy Investor"
    assert fields.taxpayer_ssn == "111-22-3333"
    assert fields.part_i_line_1_rows == ()
    assert fields.part_i_line_2_total_interest == Decimal("0")
    assert fields.part_i_line_3_excludable_savings_bond_interest == Decimal("0")
    assert fields.part_i_line_4_taxable_interest == Decimal("0")
    assert fields.part_ii_line_5_rows == ()
    assert fields.part_ii_line_6_total_ordinary_dividends == Decimal("0")
    assert fields.is_required is False


def test_single_1099_int_maps_to_part_i_line_1() -> None:
    r = _return_with(
        forms_1099_int=[
            {"payer_name": "Big Bank", "box1_interest_income": "250.00"},
        ],
    )
    fields = compute_schedule_b_fields(r)

    assert len(fields.part_i_line_1_rows) == 1
    row = fields.part_i_line_1_rows[0]
    assert isinstance(row, ScheduleBPayerRow)
    assert row.payer_name == "Big Bank"
    assert row.amount == Decimal("250.00")
    assert fields.part_i_line_2_total_interest == Decimal("250.00")
    # Line 3 (Form 8815 excludable savings bond interest) is deferred.
    assert fields.part_i_line_3_excludable_savings_bond_interest == Decimal("0")
    # Line 4 = line 2 - line 3 -> to Form 1040 line 2b.
    assert fields.part_i_line_4_taxable_interest == Decimal("250.00")


def test_single_1099_div_maps_to_part_ii_line_5() -> None:
    r = _return_with(
        forms_1099_div=[
            {
                "payer_name": "Index Fund",
                "box1a_ordinary_dividends": "400.00",
                "box1b_qualified_dividends": "300.00",
            },
        ],
    )
    fields = compute_schedule_b_fields(r)

    assert len(fields.part_ii_line_5_rows) == 1
    row = fields.part_ii_line_5_rows[0]
    assert row.payer_name == "Index Fund"
    # Box 1a (ordinary) — not 1b (qualified) — is what lands on line 5.
    assert row.amount == Decimal("400.00")
    assert fields.part_ii_line_6_total_ordinary_dividends == Decimal("400.00")


def test_1099_int_box3_us_savings_bond_merges_into_line_1_amount() -> None:
    """Box 1 interest income AND box 3 US savings bond / Treasury interest
    both land on Schedule B line 1 under the same payer name (per IRS 2024
    Schedule B line 1 instructions)."""
    r = _return_with(
        forms_1099_int=[
            {
                "payer_name": "Treasury Direct",
                "box1_interest_income": "100.00",
                "box3_us_savings_bond_and_treasury_interest": "150.00",
            },
        ],
    )
    fields = compute_schedule_b_fields(r)

    assert len(fields.part_i_line_1_rows) == 1
    row = fields.part_i_line_1_rows[0]
    assert row.payer_name == "Treasury Direct"
    assert row.amount == Decimal("250.00")
    assert fields.part_i_line_2_total_interest == Decimal("250.00")
    assert fields.part_i_line_4_taxable_interest == Decimal("250.00")


def test_multiple_1099_ints_same_payer_are_merged_into_single_row() -> None:
    """Required-merge test: two 1099-INT forms from the same payer must
    collapse into one Schedule B line 1 row."""
    r = _return_with(
        forms_1099_int=[
            {"payer_name": "Big Bank", "box1_interest_income": "300.00"},
            {"payer_name": "Big Bank", "box1_interest_income": "500.00"},
        ],
    )
    fields = compute_schedule_b_fields(r)

    assert len(fields.part_i_line_1_rows) == 1
    assert fields.part_i_line_1_rows[0].payer_name == "Big Bank"
    assert fields.part_i_line_1_rows[0].amount == Decimal("800.00")
    assert fields.part_i_line_2_total_interest == Decimal("800.00")


def test_multiple_1099_divs_same_payer_are_merged_into_single_row() -> None:
    r = _return_with(
        forms_1099_div=[
            {"payer_name": "Index Fund", "box1a_ordinary_dividends": "200.00"},
            {"payer_name": "Index Fund", "box1a_ordinary_dividends": "700.00"},
        ],
    )
    fields = compute_schedule_b_fields(r)

    assert len(fields.part_ii_line_5_rows) == 1
    assert fields.part_ii_line_5_rows[0].payer_name == "Index Fund"
    assert fields.part_ii_line_5_rows[0].amount == Decimal("900.00")
    assert fields.part_ii_line_6_total_ordinary_dividends == Decimal("900.00")


def test_distinct_payers_preserve_first_seen_order() -> None:
    r = _return_with(
        forms_1099_int=[
            {"payer_name": "Zeta Credit Union", "box1_interest_income": "10.00"},
            {"payer_name": "Alpha Bank", "box1_interest_income": "20.00"},
            {"payer_name": "Mid Bank", "box1_interest_income": "30.00"},
        ],
    )
    fields = compute_schedule_b_fields(r)

    assert [row.payer_name for row in fields.part_i_line_1_rows] == [
        "Zeta Credit Union",
        "Alpha Bank",
        "Mid Bank",
    ]
    assert fields.part_i_line_2_total_interest == Decimal("60.00")


def test_zero_amount_payer_is_dropped_from_line_1() -> None:
    r = _return_with(
        forms_1099_int=[
            {"payer_name": "Real Payer", "box1_interest_income": "100.00"},
            {
                "payer_name": "Zero Payer",
                "box1_interest_income": "0",
                "box3_us_savings_bond_and_treasury_interest": "0",
            },
        ],
    )
    fields = compute_schedule_b_fields(r)

    assert len(fields.part_i_line_1_rows) == 1
    assert fields.part_i_line_1_rows[0].payer_name == "Real Payer"


# ---------------------------------------------------------------------------
# Threshold / required-filing logic
# ---------------------------------------------------------------------------


def test_schedule_b_not_required_when_interest_exactly_at_threshold() -> None:
    """Exactly $1,500 does NOT require Schedule B; strict '> 1500' rule
    per IRS 2024 instructions."""
    r = _return_with(
        forms_1099_int=[
            {"payer_name": "Big Bank", "box1_interest_income": "1500.00"},
        ],
    )
    fields = compute_schedule_b_fields(r)
    assert fields.part_i_line_4_taxable_interest == Decimal("1500.00")
    assert fields.is_required is False
    assert schedule_b_required(r) is False


def test_schedule_b_required_when_interest_just_over_threshold() -> None:
    r = _return_with(
        forms_1099_int=[
            {"payer_name": "Big Bank", "box1_interest_income": "1500.01"},
        ],
    )
    fields = compute_schedule_b_fields(r)
    assert fields.is_required is True
    assert schedule_b_required(r) is True


def test_schedule_b_required_when_dividends_just_over_threshold() -> None:
    r = _return_with(
        forms_1099_div=[
            {"payer_name": "Index Fund", "box1a_ordinary_dividends": "1500.01"},
        ],
    )
    fields = compute_schedule_b_fields(r)
    assert fields.part_ii_line_6_total_ordinary_dividends == Decimal("1500.01")
    assert fields.is_required is True
    assert schedule_b_required(r) is True


def test_schedule_b_not_required_when_dividends_exactly_at_threshold() -> None:
    r = _return_with(
        forms_1099_div=[
            {"payer_name": "Index Fund", "box1a_ordinary_dividends": "1500.00"},
        ],
    )
    fields = compute_schedule_b_fields(r)
    assert fields.is_required is False
    assert schedule_b_required(r) is False


def test_schedule_b_threshold_constant_is_1500() -> None:
    """Pin the 2024 IRS threshold constant."""
    assert SCHEDULE_B_THRESHOLD == Decimal("1500")


def test_threshold_triggered_by_merged_same_payer_rows() -> None:
    """Two small-amount 1099-INTs from the same payer that individually
    fall below the threshold but sum above it must trigger Schedule B."""
    r = _return_with(
        forms_1099_int=[
            {"payer_name": "Big Bank", "box1_interest_income": "800.00"},
            {"payer_name": "Big Bank", "box1_interest_income": "800.00"},
        ],
    )
    fields = compute_schedule_b_fields(r)
    assert fields.part_i_line_2_total_interest == Decimal("1600.00")
    assert fields.is_required is True


# ---------------------------------------------------------------------------
# Part III (foreign accounts) — deferred defaults
# ---------------------------------------------------------------------------


def test_part_iii_foreign_accounts_default_false() -> None:
    """Canonical model does not yet carry foreign account flags; all
    Part III booleans must default False and the FinCEN country string
    must default empty. This pins the deferral so future work that adds
    a real foreign_accounts model field must update this test
    deliberately."""
    r = _return_with(
        forms_1099_int=[
            {"payer_name": "Big Bank", "box1_interest_income": "100.00"},
        ],
        forms_1099_div=[
            {"payer_name": "Index Fund", "box1a_ordinary_dividends": "200.00"},
        ],
    )
    fields = compute_schedule_b_fields(r)

    assert fields.part_iii_line_7a_foreign_account is False
    assert fields.part_iii_line_7a_fincen114_required is False
    assert fields.part_iii_line_7b_fincen114_country == ""
    assert fields.part_iii_line_8_foreign_trust is False


def test_low_balance_interest_and_dividends_not_required() -> None:
    """Even with both interest and dividends present, if each is below
    the $1,500 threshold and there are no foreign flags, Schedule B is
    NOT required."""
    r = _return_with(
        forms_1099_int=[
            {"payer_name": "Big Bank", "box1_interest_income": "100.00"},
        ],
        forms_1099_div=[
            {"payer_name": "Index Fund", "box1a_ordinary_dividends": "200.00"},
        ],
    )
    assert schedule_b_required(r) is False


# ---------------------------------------------------------------------------
# Layer 1 against golden fixtures
# ---------------------------------------------------------------------------


def test_w2_investments_itemized_fixture_field_mapping(fixtures_dir: Path) -> None:
    r = _load_fixture(fixtures_dir, "w2_investments_itemized")
    fields = compute_schedule_b_fields(r)

    # Single 1099-INT ($3,000) — required.
    assert len(fields.part_i_line_1_rows) == 1
    assert fields.part_i_line_1_rows[0].payer_name == "Big Bank"
    assert fields.part_i_line_1_rows[0].amount == Decimal("3000.00")
    assert fields.part_i_line_2_total_interest == Decimal("3000.00")
    assert fields.part_i_line_4_taxable_interest == Decimal("3000.00")

    # Single 1099-DIV ($5,000 ordinary).
    assert len(fields.part_ii_line_5_rows) == 1
    assert fields.part_ii_line_5_rows[0].payer_name == "Index Fund"
    assert fields.part_ii_line_5_rows[0].amount == Decimal("5000.00")
    assert fields.part_ii_line_6_total_ordinary_dividends == Decimal("5000.00")

    assert fields.is_required is True


def test_simple_w2_standard_fixture_has_no_schedule_b(fixtures_dir: Path) -> None:
    r = _load_fixture(fixtures_dir, "simple_w2_standard")
    fields = compute_schedule_b_fields(r)

    assert fields.part_i_line_1_rows == ()
    assert fields.part_ii_line_5_rows == ()
    assert fields.is_required is False
    assert schedule_b_required(r) is False


# ---------------------------------------------------------------------------
# Layer 2: AcroForm overlay PDF rendering (wave 5)
# ---------------------------------------------------------------------------


def _load_widget_value(out_path: Path, terminal_substring: str):
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(out_path))
    fields = reader.get_fields() or {}
    for k, v in fields.items():
        if terminal_substring in k:
            return v.get("/V")
    return None


def test_render_layer2_produces_non_empty_pdf(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """The filled IRS Schedule B PDF must be substantially-sized and on disk."""
    r = _load_fixture(fixtures_dir, "w2_investments_itemized")
    fields = compute_schedule_b_fields(r)

    out_path = tmp_path / "test_schedule_b.pdf"
    result_path = render_schedule_b_pdf(fields, out_path)

    assert result_path == out_path
    assert out_path.exists()
    # IRS f1040sb.pdf is ~76 KB; the filled output is comparable.
    assert out_path.stat().st_size > 50_000


def test_render_layer2_round_trip_line_4_taxable_interest(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """Line 4 (taxable interest) must round-trip through the filled PDF."""
    r = _load_fixture(fixtures_dir, "w2_investments_itemized")
    fields = compute_schedule_b_fields(r)
    assert fields.part_i_line_4_taxable_interest == Decimal("3000.00")

    out_path = tmp_path / "round_trip_line_4.pdf"
    render_schedule_b_pdf(fields, out_path)

    assert _load_widget_value(out_path, "f1_33[") == "3000.00"


def test_render_layer2_round_trip_line_6_total_dividends(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """Line 6 (total dividends) must round-trip through the filled PDF."""
    r = _load_fixture(fixtures_dir, "w2_investments_itemized")
    fields = compute_schedule_b_fields(r)
    assert fields.part_ii_line_6_total_ordinary_dividends == Decimal("5000.00")

    out_path = tmp_path / "round_trip_line_6.pdf"
    render_schedule_b_pdf(fields, out_path)

    assert _load_widget_value(out_path, "f1_64[") == "5000.00"


def test_render_layer2_part_i_row_widgets_filled(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """Each Part I row should land in the indexed payer/amount widget pair."""
    r = _load_fixture(fixtures_dir, "w2_investments_itemized")
    fields = compute_schedule_b_fields(r)
    assert len(fields.part_i_line_1_rows) >= 1
    assert fields.part_i_line_1_rows[0].payer_name == "Big Bank"
    assert fields.part_i_line_1_rows[0].amount == Decimal("3000.00")

    out_path = tmp_path / "row_widgets.pdf"
    render_schedule_b_pdf(fields, out_path)

    # Row 0 in the JSON map: payer = f1_03, amount = f1_04.
    assert _load_widget_value(out_path, "f1_03[") == "Big Bank"
    assert _load_widget_value(out_path, "f1_04[") == "3000.00"


def test_render_layer2_part_iii_foreign_account_yes(tmp_path: Path) -> None:
    """When Part III line 7a foreign account is True, the Yes checkbox is set."""
    r = CanonicalReturn.model_validate({
        **_base_return_payload(),
        "has_foreign_financial_account_over_10k": True,
        "foreign_account_countries": ["FR"],
    })
    fields = compute_schedule_b_fields(r)
    assert fields.part_iii_line_7a_foreign_account is True
    assert fields.part_iii_line_7b_fincen114_country == "FR"

    out_path = tmp_path / "foreign_account.pdf"
    render_schedule_b_pdf(fields, out_path)

    # f1_65 = country widget
    assert _load_widget_value(out_path, "f1_65[") == "FR"


def test_render_layer2_empty_schedule_b_renders_blank(tmp_path: Path) -> None:
    """An empty Schedule B should still produce a valid filled PDF (blank lines).

    Even when Schedule B is NOT required, callers may still want a blank
    PDF for QA purposes — Layer 2 should not crash on empty rows.
    """
    r = _return_with()
    fields = compute_schedule_b_fields(r)
    assert fields.is_required is False

    out_path = tmp_path / "empty_schedule_b.pdf"
    render_schedule_b_pdf(fields, out_path)
    assert out_path.exists()
    assert out_path.stat().st_size > 50_000

    # All numeric line widgets should remain blank.
    assert _load_widget_value(out_path, "f1_33[") in (None, "")
    assert _load_widget_value(out_path, "f1_64[") in (None, "")


def test_render_layer2_taxpayer_name_round_trip(tmp_path: Path) -> None:
    r = _return_with(
        forms_1099_int=[
            {"payer_name": "Big Bank", "box1_interest_income": "100.00"},
        ],
    )
    fields = compute_schedule_b_fields(r)
    out_path = tmp_path / "name.pdf"
    render_schedule_b_pdf(fields, out_path)
    assert _load_widget_value(out_path, "f1_01[") == "Ivy Investor"


def test_render_layer2_raises_on_sha_mismatch(monkeypatch, tmp_path: Path) -> None:
    """If the IRS PDF SHA-256 changes (silent re-issue), raise RuntimeError."""
    from skill.scripts.output import schedule_b as sb

    bad_sha = "deadbeef" * 8
    real_map = json.loads(sb._SCHEDULE_B_MAP_PATH.read_text())
    real_map["source_pdf_sha256"] = bad_sha
    fake_map_path = tmp_path / "fake_map.json"
    fake_map_path.write_text(json.dumps(real_map))
    monkeypatch.setattr(sb, "_SCHEDULE_B_MAP_PATH", fake_map_path)

    r = _return_with()
    fields = compute_schedule_b_fields(r)

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        sb.render_schedule_b_pdf(fields, tmp_path / "out.pdf")


# ---------------------------------------------------------------------------
# Immutability guarantees
# ---------------------------------------------------------------------------


def test_schedule_b_fields_is_frozen() -> None:
    """Layer 1 returns an immutable dataclass — defensive check that
    downstream render code cannot mutate shared state."""
    r = _return_with(
        forms_1099_int=[
            {"payer_name": "Big Bank", "box1_interest_income": "50.00"},
        ],
    )
    fields = compute_schedule_b_fields(r)
    with pytest.raises(Exception):
        fields.part_i_line_2_total_interest = Decimal("999")  # type: ignore[misc]


def test_schedule_b_payer_row_is_frozen() -> None:
    row = ScheduleBPayerRow(payer_name="X", amount=Decimal("1"))
    with pytest.raises(Exception):
        row.amount = Decimal("2")  # type: ignore[misc]
