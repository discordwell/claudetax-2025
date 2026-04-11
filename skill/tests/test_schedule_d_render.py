"""Tests for skill.scripts.output.schedule_d — Schedule D PDF renderer.

Two layers under test:

* Layer 1 — ``compute_schedule_d_fields``: classifies + sums 1099-B
  transactions into Schedule D lines 1a-15, applies the capital loss
  cap on line 21, and carries 1099-DIV capital gain distributions onto
  line 13. Tests cover short-only, long-only, mixed, loss capped at
  the default $3,000 and at the MFS $1,500, and wash-sale adjustment.

* Layer 2 — ``render_schedule_d_pdf``: overlays the fields onto the
  IRS fillable PDF and round-trips the line values back via pypdf.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.models import CanonicalReturn
from skill.scripts.output.schedule_d import (
    LOSS_CAP_DEFAULT,
    LOSS_CAP_MFS,
    ScheduleDFields,
    ScheduleDRowTotals,
    compute_schedule_d_fields,
    render_schedule_d_pdf,
    schedule_d_required,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_return_payload(*, filing_status: str = "single") -> dict:
    return {
        "schema_version": "0.1.0",
        "tax_year": 2025,
        "filing_status": filing_status,
        "taxpayer": {
            "first_name": "Cap",
            "last_name": "Gains",
            "ssn": "111-22-3333",
            "date_of_birth": "1985-06-01",
        },
        "address": {
            "street1": "1 Broker Way",
            "city": "Boston",
            "state": "MA",
            "zip": "02108",
        },
        "itemize_deductions": False,
    }


def _txn(
    *,
    description: str = "100 sh XYZ",
    date_acquired: str = "2024-01-01",
    date_sold: str = "2025-06-15",
    proceeds: str = "1000.00",
    cost_basis: str = "800.00",
    is_long_term: bool = False,
    basis_reported_to_irs: bool = True,
    wash_sale: str = "0",
) -> dict:
    return {
        "description": description,
        "date_acquired": date_acquired,
        "date_sold": date_sold,
        "proceeds": proceeds,
        "cost_basis": cost_basis,
        "is_long_term": is_long_term,
        "basis_reported_to_irs": basis_reported_to_irs,
        "wash_sale_loss_disallowed": wash_sale,
    }


def _return_with(
    *,
    transactions: list[dict] | None = None,
    cap_gain_distributions: str | None = None,
    filing_status: str = "single",
) -> CanonicalReturn:
    payload = _base_return_payload(filing_status=filing_status)
    if filing_status in ("mfj", "mfs", "qss"):
        payload["spouse"] = {
            "first_name": "Pat",
            "last_name": "Gains",
            "ssn": "222-33-4444",
            "date_of_birth": "1987-07-07",
        }
    if transactions is not None:
        payload["forms_1099_b"] = [
            {
                "broker_name": "Brokerage Inc",
                "recipient_is_taxpayer": True,
                "transactions": transactions,
            }
        ]
    if cap_gain_distributions is not None:
        payload["forms_1099_div"] = [
            {
                "payer_name": "Index Fund",
                "recipient_is_taxpayer": True,
                "box1a_ordinary_dividends": "0",
                "box2a_total_capital_gain_distributions": cap_gain_distributions,
            }
        ]
    return CanonicalReturn.model_validate(payload)


def _load_fixture(fixtures_dir: Path, name: str) -> CanonicalReturn:
    data = json.loads((fixtures_dir / name / "input.json").read_text())
    return CanonicalReturn.model_validate(data)


def _load_widget_value(out_path: Path, terminal_substring: str):
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(out_path))
    fields = reader.get_fields() or {}
    for k, v in fields.items():
        if terminal_substring in k:
            return v.get("/V")
    return None


# ---------------------------------------------------------------------------
# Layer 1 — field-level mapping
# ---------------------------------------------------------------------------


def test_empty_return_has_zero_schedule_d() -> None:
    r = _return_with()
    fields = compute_schedule_d_fields(r)
    assert isinstance(fields, ScheduleDFields)
    assert fields.taxpayer_name == "Cap Gains"
    assert fields.line_7_net_short_term_gain_loss == Decimal("0")
    assert fields.line_15_net_long_term_gain_loss == Decimal("0")
    assert fields.line_16_total_gain_loss == Decimal("0")
    assert fields.line_21_allowable_loss_capped == Decimal("0")
    assert fields.is_required is False
    assert schedule_d_required(r) is False


def test_short_term_only_gain_populates_line_1b_and_7() -> None:
    r = _return_with(transactions=[
        _txn(proceeds="1500", cost_basis="1000", is_long_term=False),
    ])
    fields = compute_schedule_d_fields(r)
    assert fields.line_1b_totals.proceeds == Decimal("1500")
    assert fields.line_1b_totals.cost_basis == Decimal("1000")
    assert fields.line_1b_totals.gain_loss == Decimal("500")
    assert fields.line_2_totals.gain_loss == Decimal("0")
    assert fields.line_3_totals.gain_loss == Decimal("0")
    assert fields.line_7_net_short_term_gain_loss == Decimal("500")
    assert fields.line_15_net_long_term_gain_loss == Decimal("0")
    assert fields.line_16_total_gain_loss == Decimal("500")
    assert fields.line_21_allowable_loss_capped == Decimal("0")
    assert fields.is_required is True


def test_long_term_only_gain_populates_line_8b_and_15() -> None:
    r = _return_with(transactions=[
        _txn(proceeds="20000", cost_basis="10000", is_long_term=True),
    ])
    fields = compute_schedule_d_fields(r)
    assert fields.line_8b_totals.gain_loss == Decimal("10000")
    assert fields.line_15_net_long_term_gain_loss == Decimal("10000")
    assert fields.line_7_net_short_term_gain_loss == Decimal("0")
    assert fields.line_16_total_gain_loss == Decimal("10000")


def test_mixed_short_long_produces_both_nets() -> None:
    r = _return_with(transactions=[
        _txn(description="ST", proceeds="1000", cost_basis="500",
             is_long_term=False),
        _txn(description="LT", proceeds="5000", cost_basis="2000",
             is_long_term=True),
    ])
    fields = compute_schedule_d_fields(r)
    assert fields.line_7_net_short_term_gain_loss == Decimal("500")
    assert fields.line_15_net_long_term_gain_loss == Decimal("3000")
    assert fields.line_16_total_gain_loss == Decimal("3500")


def test_capital_gain_distributions_land_on_line_13() -> None:
    r = _return_with(cap_gain_distributions="500.00")
    fields = compute_schedule_d_fields(r)
    assert fields.line_13_capital_gain_distributions == Decimal("500.00")
    assert fields.line_15_net_long_term_gain_loss == Decimal("500.00")
    assert fields.line_16_total_gain_loss == Decimal("500.00")
    assert fields.is_required is True


def test_basis_not_reported_short_term_goes_to_line_2() -> None:
    r = _return_with(transactions=[
        _txn(proceeds="1500", cost_basis="1000", is_long_term=False,
             basis_reported_to_irs=False),
    ])
    fields = compute_schedule_d_fields(r)
    assert fields.line_1b_totals.gain_loss == Decimal("0")
    assert fields.line_2_totals.gain_loss == Decimal("500")
    assert fields.line_7_net_short_term_gain_loss == Decimal("500")


def test_basis_not_reported_long_term_goes_to_line_9() -> None:
    r = _return_with(transactions=[
        _txn(proceeds="3000", cost_basis="1000", is_long_term=True,
             basis_reported_to_irs=False),
    ])
    fields = compute_schedule_d_fields(r)
    assert fields.line_8b_totals.gain_loss == Decimal("0")
    assert fields.line_9_totals.gain_loss == Decimal("2000")
    assert fields.line_15_net_long_term_gain_loss == Decimal("2000")


# ---------------------------------------------------------------------------
# Layer 1 — loss cap at line 21
# ---------------------------------------------------------------------------


def test_small_loss_under_3k_cap_is_not_capped() -> None:
    """Net loss of -$1,000 is less (in magnitude) than the $3,000 cap;
    line 21 allows the full loss."""
    r = _return_with(transactions=[
        _txn(proceeds="500", cost_basis="1500", is_long_term=False),
    ])
    fields = compute_schedule_d_fields(r)
    assert fields.line_16_total_gain_loss == Decimal("-1000")
    # Line 21 = max(-1000, -3000) = -1000 (full allowed).
    assert fields.line_21_allowable_loss_capped == Decimal("-1000")


def test_large_loss_capped_at_3k_default() -> None:
    r = _return_with(transactions=[
        _txn(proceeds="1000", cost_basis="8000", is_long_term=False),
    ])
    fields = compute_schedule_d_fields(r)
    assert fields.line_16_total_gain_loss == Decimal("-7000")
    assert fields.line_21_allowable_loss_capped == LOSS_CAP_DEFAULT
    assert fields.line_21_allowable_loss_capped == Decimal("-3000")


def test_large_loss_capped_at_1500_for_mfs() -> None:
    r = _return_with(
        transactions=[
            _txn(proceeds="1000", cost_basis="8000", is_long_term=False),
        ],
        filing_status="mfs",
    )
    fields = compute_schedule_d_fields(r)
    assert fields.line_16_total_gain_loss == Decimal("-7000")
    assert fields.line_21_allowable_loss_capped == LOSS_CAP_MFS
    assert fields.line_21_allowable_loss_capped == Decimal("-1500")


def test_net_gain_does_not_trigger_loss_cap() -> None:
    r = _return_with(transactions=[
        _txn(proceeds="5000", cost_basis="1000", is_long_term=True),
    ])
    fields = compute_schedule_d_fields(r)
    assert fields.line_16_total_gain_loss == Decimal("4000")
    assert fields.line_21_allowable_loss_capped == Decimal("0")


# ---------------------------------------------------------------------------
# Layer 1 — wash sale adjustment
# ---------------------------------------------------------------------------


def test_wash_sale_reduces_loss_on_schedule_d() -> None:
    """Wash-sale disallowed loss flows through 8949 Layer 1 as a
    positive column-g adjustment and the resulting gain/loss matches on
    Schedule D line 1b."""
    r = _return_with(transactions=[
        _txn(proceeds="500", cost_basis="1000", wash_sale="200",
             is_long_term=False),
    ])
    fields = compute_schedule_d_fields(r)
    # Raw loss = -500, wash sale disallowed = 200, adjusted loss = -300.
    assert fields.line_1b_totals.adjustment_amount == Decimal("200")
    assert fields.line_1b_totals.gain_loss == Decimal("-300")
    assert fields.line_7_net_short_term_gain_loss == Decimal("-300")
    assert fields.line_16_total_gain_loss == Decimal("-300")


# ---------------------------------------------------------------------------
# Layer 1 — golden fixture
# ---------------------------------------------------------------------------


def test_w2_investments_itemized_fixture_field_mapping(fixtures_dir: Path) -> None:
    r = _load_fixture(fixtures_dir, "w2_investments_itemized")
    fields = compute_schedule_d_fields(r)
    # One 1099-B LT sale: 20000 - 10000 = 10000 gain (box D).
    assert fields.line_8b_totals.proceeds == Decimal("20000.00")
    assert fields.line_8b_totals.cost_basis == Decimal("10000.00")
    assert fields.line_8b_totals.gain_loss == Decimal("10000.00")
    # Cap gain distributions of $500 from 1099-DIV box 2a.
    assert fields.line_13_capital_gain_distributions == Decimal("500.00")
    # Line 15 = 10000 + 500 = 10500.
    assert fields.line_15_net_long_term_gain_loss == Decimal("10500.00")
    assert fields.line_7_net_short_term_gain_loss == Decimal("0")
    assert fields.line_16_total_gain_loss == Decimal("10500.00")
    assert fields.is_required is True


# ---------------------------------------------------------------------------
# Layer 2 — AcroForm overlay PDF rendering
# ---------------------------------------------------------------------------


def test_render_produces_non_empty_pdf(tmp_path: Path) -> None:
    r = _return_with(transactions=[
        _txn(proceeds="20000", cost_basis="10000", is_long_term=True),
    ])
    fields = compute_schedule_d_fields(r)

    out_path = tmp_path / "schedule_d.pdf"
    result = render_schedule_d_pdf(fields, out_path)
    assert result == out_path
    assert out_path.exists()
    assert out_path.stat().st_size > 50_000


def test_render_round_trips_line_16(tmp_path: Path) -> None:
    r = _return_with(transactions=[
        _txn(proceeds="5000", cost_basis="2000", is_long_term=True),
    ])
    fields = compute_schedule_d_fields(r)
    assert fields.line_16_total_gain_loss == Decimal("3000")

    out_path = tmp_path / "round_trip.pdf"
    render_schedule_d_pdf(fields, out_path)

    # Line 16 is on page 2 widget f2_1.
    assert _load_widget_value(out_path, "f2_1[") == "3000.00"


def test_render_round_trips_line_8b_totals(tmp_path: Path) -> None:
    r = _return_with(transactions=[
        _txn(proceeds="20000", cost_basis="10000", is_long_term=True,
             basis_reported_to_irs=True),
    ])
    fields = compute_schedule_d_fields(r)

    out_path = tmp_path / "line_8b.pdf"
    render_schedule_d_pdf(fields, out_path)

    # Line 8b widgets: f1_27 (proceeds), f1_28 (cost), f1_30 (gain/loss).
    assert _load_widget_value(out_path, "f1_27[") == "20000.00"
    assert _load_widget_value(out_path, "f1_28[") == "10000.00"
    assert _load_widget_value(out_path, "f1_30[") == "10000.00"


def test_render_round_trips_line_1b_totals_short_term(tmp_path: Path) -> None:
    r = _return_with(transactions=[
        _txn(proceeds="2500", cost_basis="1500", is_long_term=False,
             basis_reported_to_irs=True),
    ])
    fields = compute_schedule_d_fields(r)

    out_path = tmp_path / "line_1b.pdf"
    render_schedule_d_pdf(fields, out_path)

    # Line 1b widgets: f1_7/f1_8/f1_9/f1_10 = proceeds/cost/adj/gl.
    assert _load_widget_value(out_path, "f1_7[") == "2500.00"
    assert _load_widget_value(out_path, "f1_8[") == "1500.00"
    assert _load_widget_value(out_path, "f1_10[") == "1000.00"


def test_render_round_trips_line_13_cap_gain_distributions(tmp_path: Path) -> None:
    r = _return_with(cap_gain_distributions="750.00")
    fields = compute_schedule_d_fields(r)

    out_path = tmp_path / "line_13.pdf"
    render_schedule_d_pdf(fields, out_path)

    # Line 13 widget f1_41.
    assert _load_widget_value(out_path, "f1_41[") == "750.00"


def test_render_round_trips_line_21_capped_loss(tmp_path: Path) -> None:
    r = _return_with(transactions=[
        _txn(proceeds="1000", cost_basis="8000", is_long_term=False),
    ])
    fields = compute_schedule_d_fields(r)
    assert fields.line_21_allowable_loss_capped == Decimal("-3000")

    out_path = tmp_path / "line_21.pdf"
    render_schedule_d_pdf(fields, out_path)

    # Line 21 widget f2_4.
    assert _load_widget_value(out_path, "f2_4[") == "-3000.00"


def test_render_taxpayer_name_on_header(tmp_path: Path) -> None:
    r = _return_with(transactions=[
        _txn(proceeds="100", cost_basis="50", is_long_term=False),
    ])
    fields = compute_schedule_d_fields(r)
    out_path = tmp_path / "hdr.pdf"
    render_schedule_d_pdf(fields, out_path)
    assert _load_widget_value(out_path, "f1_1[") == "Cap Gains"


def test_render_raises_on_sha_mismatch(monkeypatch, tmp_path: Path) -> None:
    from skill.scripts.output import schedule_d as sd

    bad_sha = "deadbeef" * 8
    real_map = json.loads(sd._SCHEDULE_D_MAP_PATH.read_text())
    real_map["source_pdf_sha256"] = bad_sha
    fake_map_path = tmp_path / "fake_map.json"
    fake_map_path.write_text(json.dumps(real_map))
    monkeypatch.setattr(sd, "_SCHEDULE_D_MAP_PATH", fake_map_path)

    r = _return_with(transactions=[
        _txn(proceeds="100", cost_basis="50"),
    ])
    fields = compute_schedule_d_fields(r)

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        sd.render_schedule_d_pdf(fields, tmp_path / "out.pdf")


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_schedule_d_fields_is_frozen() -> None:
    r = _return_with(transactions=[_txn(proceeds="100", cost_basis="50")])
    fields = compute_schedule_d_fields(r)
    with pytest.raises(Exception):
        fields.line_16_total_gain_loss = Decimal("999")  # type: ignore[misc]


def test_schedule_d_row_totals_is_frozen() -> None:
    row = ScheduleDRowTotals()
    with pytest.raises(Exception):
        row.proceeds = Decimal("1")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------


def test_pipeline_renders_schedule_d_and_8949_when_1099_b_present(
    tmp_path: Path,
) -> None:
    """Dropping a 1099-B fixture into input_dir should produce both
    schedule_d.pdf and form_8949.pdf in output_dir."""
    from skill.scripts.pipeline import run_pipeline

    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    # Taxpayer info carries the header + the 1099-B directly, so we do
    # not need to mint a real AcroForm PDF for this test. (The pipeline
    # merges taxpayer_info.json into the canonical return without
    # requiring the 1099-B to come from a PDF.)
    taxpayer_info_path = tmp_path / "taxpayer_info.json"
    taxpayer_info_path.write_text(json.dumps({
        "schema_version": "0.1.0",
        "tax_year": 2025,
        "filing_status": "single",
        "taxpayer": {
            "first_name": "Cap",
            "last_name": "Gains",
            "ssn": "111-22-3333",
            "date_of_birth": "1985-06-01",
        },
        "address": {
            "street1": "1 Broker Way",
            "city": "Boston",
            "state": "MA",
            "zip": "02108",
            "country": "US",
        },
        "forms_1099_b": [
            {
                "broker_name": "Brokerage Inc",
                "recipient_is_taxpayer": True,
                "transactions": [
                    {
                        "description": "100 sh VTI",
                        "date_acquired": "2020-03-15",
                        "date_sold": "2025-11-15",
                        "proceeds": "20000.00",
                        "cost_basis": "10000.00",
                        "is_long_term": True,
                        "basis_reported_to_irs": True,
                    }
                ],
            }
        ],
    }))

    result = run_pipeline(
        input_dir=input_dir,
        taxpayer_info_path=taxpayer_info_path,
        output_dir=output_dir,
    )
    paths = {p.name for p in result.rendered_paths}
    assert "schedule_d.pdf" in paths
    assert "form_8949.pdf" in paths

    # Files exist on disk.
    assert (output_dir / "schedule_d.pdf").exists()
    assert (output_dir / "form_8949.pdf").exists()

    # Line 8b round-trips through the real PDF.
    assert _load_widget_value(output_dir / "schedule_d.pdf", "f1_27[") == "20000.00"


def test_pipeline_skips_schedule_d_when_no_capital_transactions(
    tmp_path: Path,
) -> None:
    from skill.scripts.pipeline import run_pipeline

    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    taxpayer_info_path = tmp_path / "taxpayer_info.json"
    taxpayer_info_path.write_text(json.dumps({
        "schema_version": "0.1.0",
        "tax_year": 2025,
        "filing_status": "single",
        "taxpayer": {
            "first_name": "Simple",
            "last_name": "Filer",
            "ssn": "111-22-3333",
            "date_of_birth": "1985-06-01",
        },
        "address": {
            "street1": "1 Main St",
            "city": "Boston",
            "state": "MA",
            "zip": "02108",
            "country": "US",
        },
    }))

    result = run_pipeline(
        input_dir=input_dir,
        taxpayer_info_path=taxpayer_info_path,
        output_dir=output_dir,
    )
    paths = {p.name for p in result.rendered_paths}
    assert "schedule_d.pdf" not in paths
    assert "form_8949.pdf" not in paths
    assert not (output_dir / "schedule_d.pdf").exists()
    assert not (output_dir / "form_8949.pdf").exists()
