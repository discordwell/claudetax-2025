"""Tests for skill.scripts.output.form_8949 — Form 8949 PDF renderer.

Two layers under test:

* Layer 1 — ``compute_form_8949_fields``: classifies 1099-B transactions
  into Part I (short-term) box A/B/C or Part II (long-term) box D/E/F,
  sums per-page totals, and caps at 11 rows.

* Layer 2 — ``render_form_8949_pdf``: writes one or more filled IRS
  Form 8949 PDFs and asserts the expected widgets are populated.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.models import CanonicalReturn
from skill.scripts.output.form_8949 import (
    MAX_ROWS_PER_PAGE,
    Form8949Fields,
    Form8949Page,
    Form8949Row,
    compute_form_8949_fields,
    render_form_8949_pdf,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_return_payload() -> dict:
    return {
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
        },
        "itemize_deductions": False,
    }


def _txn(
    *,
    description: str = "100 sh XYZ",
    date_acquired: str | None = "2024-01-01",
    date_sold: str = "2025-06-15",
    proceeds: str = "1000.00",
    cost_basis: str = "800.00",
    is_long_term: bool = False,
    basis_reported_to_irs: bool = True,
    wash_sale: str = "0",
    adjustment_amount: str = "0",
    adjustment_codes: list[str] | None = None,
    form_8949_box_code: str | None = None,
) -> dict:
    out: dict = {
        "description": description,
        "date_sold": date_sold,
        "proceeds": proceeds,
        "cost_basis": cost_basis,
        "is_long_term": is_long_term,
        "basis_reported_to_irs": basis_reported_to_irs,
        "wash_sale_loss_disallowed": wash_sale,
        "adjustment_amount": adjustment_amount,
        "adjustment_codes": adjustment_codes or [],
    }
    if date_acquired is not None:
        out["date_acquired"] = date_acquired
    if form_8949_box_code is not None:
        out["form_8949_box_code"] = form_8949_box_code
    return out


def _return_with_1099_b(transactions: list[dict]) -> CanonicalReturn:
    payload = _base_return_payload()
    payload["forms_1099_b"] = [
        {
            "broker_name": "Brokerage Inc",
            "recipient_is_taxpayer": True,
            "transactions": transactions,
        }
    ]
    return CanonicalReturn.model_validate(payload)


def _load_widget_value(out_path: Path, terminal_substring: str):
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(out_path))
    fields = reader.get_fields() or {}
    for k, v in fields.items():
        if terminal_substring in k:
            return v.get("/V")
    return None


# ---------------------------------------------------------------------------
# Layer 1 — classification and totals
# ---------------------------------------------------------------------------


def test_empty_return_produces_no_pages() -> None:
    r = CanonicalReturn.model_validate(_base_return_payload())
    fields = compute_form_8949_fields(r)
    assert isinstance(fields, Form8949Fields)
    assert fields.pages == ()
    assert fields.is_required is False
    assert fields.taxpayer_name == "Cap Gains"


def test_short_term_basis_reported_goes_to_box_A() -> None:
    r = _return_with_1099_b([
        _txn(proceeds="1500", cost_basis="1000", is_long_term=False,
             basis_reported_to_irs=True),
    ])
    fields = compute_form_8949_fields(r)
    assert len(fields.pages) == 1
    page = fields.pages[0]
    assert page.part == "I"
    assert page.box_code == "A"
    assert len(page.rows) == 1
    row = page.rows[0]
    assert row.proceeds == Decimal("1500")
    assert row.cost_basis == Decimal("1000")
    assert row.gain_loss == Decimal("500")
    assert page.total_proceeds == Decimal("1500")
    assert page.total_gain_loss == Decimal("500")


def test_short_term_basis_not_reported_goes_to_box_B() -> None:
    r = _return_with_1099_b([
        _txn(is_long_term=False, basis_reported_to_irs=False),
    ])
    fields = compute_form_8949_fields(r)
    assert fields.pages[0].box_code == "B"
    assert fields.pages[0].part == "I"


def test_long_term_basis_reported_goes_to_box_D() -> None:
    r = _return_with_1099_b([
        _txn(is_long_term=True, basis_reported_to_irs=True),
    ])
    fields = compute_form_8949_fields(r)
    assert fields.pages[0].box_code == "D"
    assert fields.pages[0].part == "II"


def test_long_term_basis_not_reported_goes_to_box_E() -> None:
    r = _return_with_1099_b([
        _txn(is_long_term=True, basis_reported_to_irs=False),
    ])
    fields = compute_form_8949_fields(r)
    assert fields.pages[0].box_code == "E"


def test_explicit_box_code_C_overrides_defaults() -> None:
    """Setting form_8949_box_code='C' forces Part I box C regardless of
    basis_reported_to_irs."""
    r = _return_with_1099_b([
        _txn(is_long_term=False, basis_reported_to_irs=True, form_8949_box_code="C"),
    ])
    fields = compute_form_8949_fields(r)
    assert fields.pages[0].box_code == "C"
    assert fields.pages[0].part == "I"


def test_mixed_st_lt_produces_two_pages() -> None:
    r = _return_with_1099_b([
        _txn(description="ST gain", is_long_term=False, basis_reported_to_irs=True,
             proceeds="1000", cost_basis="500"),
        _txn(description="LT gain", is_long_term=True, basis_reported_to_irs=True,
             proceeds="5000", cost_basis="2000"),
    ])
    fields = compute_form_8949_fields(r)
    assert len(fields.pages) == 2
    box_codes = {p.box_code for p in fields.pages}
    assert box_codes == {"A", "D"}
    # Short term page
    st_page = next(p for p in fields.pages if p.box_code == "A")
    assert st_page.total_gain_loss == Decimal("500")
    # Long term page
    lt_page = next(p for p in fields.pages if p.box_code == "D")
    assert lt_page.total_gain_loss == Decimal("3000")


def test_multiple_transactions_same_box_sum_in_totals() -> None:
    r = _return_with_1099_b([
        _txn(proceeds="100", cost_basis="50"),
        _txn(proceeds="200", cost_basis="75"),
        _txn(proceeds="300", cost_basis="400"),  # a loss
    ])
    fields = compute_form_8949_fields(r)
    page = fields.pages[0]
    assert len(page.rows) == 3
    assert page.total_proceeds == Decimal("600")
    assert page.total_cost_basis == Decimal("525")
    assert page.total_gain_loss == Decimal("75")


def test_wash_sale_becomes_W_code_and_positive_adjustment() -> None:
    r = _return_with_1099_b([
        _txn(
            proceeds="500",
            cost_basis="1000",
            wash_sale="200",
            is_long_term=False,
        ),
    ])
    fields = compute_form_8949_fields(r)
    row = fields.pages[0].rows[0]
    assert "W" in row.adjustment_code
    assert row.adjustment_amount == Decimal("200")
    # The actual loss reported = 500 - 1000 + 200 = -300 (not the full -500).
    assert row.gain_loss == Decimal("-300")


def test_11_row_page_cap_records_overflow_warning() -> None:
    txns = [
        _txn(description=f"ticker{i}", proceeds=str(100 * i), cost_basis="50")
        for i in range(1, MAX_ROWS_PER_PAGE + 3)  # 13 rows
    ]
    r = _return_with_1099_b(txns)
    fields = compute_form_8949_fields(r)
    page = fields.pages[0]
    assert len(page.rows) == MAX_ROWS_PER_PAGE
    assert page.overflow_row_count == 2
    assert any("continuation statement" in w for w in fields.warnings)
    # Totals still sum ALL rows even though visual rows are truncated.
    total_proceeds = sum(100 * i for i in range(1, MAX_ROWS_PER_PAGE + 3))
    assert page.total_proceeds == Decimal(total_proceeds)


def test_row_date_formatting() -> None:
    r = _return_with_1099_b([
        _txn(date_acquired="2020-03-15", date_sold="2025-12-20"),
    ])
    fields = compute_form_8949_fields(r)
    row = fields.pages[0].rows[0]
    assert row.date_acquired == "03/15/2020"
    assert row.date_sold == "12/20/2025"


def test_various_date_acquired_is_preserved() -> None:
    r = _return_with_1099_b([
        _txn(date_acquired="various", date_sold="2025-12-20",
             is_long_term=True),
    ])
    fields = compute_form_8949_fields(r)
    row = fields.pages[0].rows[0]
    assert row.date_acquired == "VARIOUS"


# ---------------------------------------------------------------------------
# Layer 2 — AcroForm overlay rendering
# ---------------------------------------------------------------------------


def test_render_single_page_writes_one_pdf(tmp_path: Path) -> None:
    r = _return_with_1099_b([
        _txn(description="APPLE", proceeds="1500", cost_basis="1000",
             is_long_term=False, basis_reported_to_irs=True),
    ])
    fields = compute_form_8949_fields(r)

    out_path = tmp_path / "form_8949.pdf"
    result = render_form_8949_pdf(fields, out_path)

    assert result == [out_path]
    assert out_path.exists()
    # IRS f8949.pdf is ~129 KB; filled output is comparable.
    assert out_path.stat().st_size > 50_000


def test_render_round_trips_description_and_proceeds(tmp_path: Path) -> None:
    r = _return_with_1099_b([
        _txn(description="100 sh TSLA", proceeds="2500.00", cost_basis="2000.00",
             is_long_term=False, basis_reported_to_irs=True),
    ])
    fields = compute_form_8949_fields(r)

    out_path = tmp_path / "f8949.pdf"
    render_form_8949_pdf(fields, out_path)

    # Row 0 description widget on Part I is f1_03.
    assert _load_widget_value(out_path, "f1_03[") == "100 sh TSLA"
    # Row 0 proceeds widget is f1_06.
    assert _load_widget_value(out_path, "f1_06[") == "2500.00"
    # Row 0 cost basis widget is f1_07.
    assert _load_widget_value(out_path, "f1_07[") == "2000.00"
    # Row 0 gain/loss widget is f1_10.
    assert _load_widget_value(out_path, "f1_10[") == "500.00"


def test_render_long_term_uses_part_2_page(tmp_path: Path) -> None:
    r = _return_with_1099_b([
        _txn(description="LT VTI", proceeds="20000", cost_basis="10000",
             is_long_term=True, basis_reported_to_irs=True),
    ])
    fields = compute_form_8949_fields(r)

    out_path = tmp_path / "lt_8949.pdf"
    render_form_8949_pdf(fields, out_path)

    # Part 2 row 0 description = f2_03; proceeds = f2_06.
    assert _load_widget_value(out_path, "f2_03[") == "LT VTI"
    assert _load_widget_value(out_path, "f2_06[") == "20000.00"
    # Part 2 line 2 proceeds total = f2_91.
    assert _load_widget_value(out_path, "f2_91[") == "20000.00"


def test_render_mixed_short_and_long_writes_two_pdfs(tmp_path: Path) -> None:
    r = _return_with_1099_b([
        _txn(description="ST", is_long_term=False, basis_reported_to_irs=True),
        _txn(description="LT", is_long_term=True, basis_reported_to_irs=True),
    ])
    fields = compute_form_8949_fields(r)

    out_path = tmp_path / "form_8949.pdf"
    written = render_form_8949_pdf(fields, out_path)

    assert len(written) == 2
    # Suffixed with box code A (ST) and D (LT)
    stems = sorted(p.name for p in written)
    assert stems == ["form_8949_A.pdf", "form_8949_D.pdf"]
    for p in written:
        assert p.exists()


def test_render_three_rows_fills_three_row_slots(tmp_path: Path) -> None:
    r = _return_with_1099_b([
        _txn(description="AAA", proceeds="100", cost_basis="50"),
        _txn(description="BBB", proceeds="200", cost_basis="75"),
        _txn(description="CCC", proceeds="300", cost_basis="150"),
    ])
    fields = compute_form_8949_fields(r)

    out_path = tmp_path / "rows.pdf"
    render_form_8949_pdf(fields, out_path)

    # Row 0 description = f1_03, row 1 = f1_11, row 2 = f1_19.
    assert _load_widget_value(out_path, "f1_03[") == "AAA"
    assert _load_widget_value(out_path, "f1_11[") == "BBB"
    assert _load_widget_value(out_path, "f1_19[") == "CCC"

    # Total (line 2) proceeds should be 100+200+300 = 600.
    assert _load_widget_value(out_path, "f1_91[") == "600.00"


def test_render_empty_pages_returns_empty_list(tmp_path: Path) -> None:
    r = CanonicalReturn.model_validate(_base_return_payload())
    fields = compute_form_8949_fields(r)
    out_path = tmp_path / "nope.pdf"
    result = render_form_8949_pdf(fields, out_path)
    assert result == []
    assert not out_path.exists()


def test_render_raises_on_sha_mismatch(monkeypatch, tmp_path: Path) -> None:
    from skill.scripts.output import form_8949 as f8949

    bad_sha = "deadbeef" * 8
    real_map = json.loads(f8949._FORM_8949_MAP_PATH.read_text())
    real_map["source_pdf_sha256"] = bad_sha
    fake_map_path = tmp_path / "fake_map.json"
    fake_map_path.write_text(json.dumps(real_map))
    monkeypatch.setattr(f8949, "_FORM_8949_MAP_PATH", fake_map_path)

    r = _return_with_1099_b([
        _txn(description="x", proceeds="100", cost_basis="50"),
    ])
    fields = compute_form_8949_fields(r)

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        f8949.render_form_8949_pdf(fields, tmp_path / "out.pdf")


# ---------------------------------------------------------------------------
# Dataclass immutability
# ---------------------------------------------------------------------------


def test_form_8949_row_is_frozen() -> None:
    row = Form8949Row(
        description="x", date_acquired="", date_sold="",
        proceeds=Decimal("1"), cost_basis=Decimal("1"),
        adjustment_code="", adjustment_amount=Decimal("0"),
        gain_loss=Decimal("0"),
    )
    with pytest.raises(Exception):
        row.proceeds = Decimal("2")  # type: ignore[misc]


def test_form_8949_page_is_frozen() -> None:
    p = Form8949Page(part="I", box_code="A", rows=(),
                     total_proceeds=Decimal("0"),
                     total_cost_basis=Decimal("0"),
                     total_adjustment_amount=Decimal("0"),
                     total_gain_loss=Decimal("0"))
    with pytest.raises(Exception):
        p.total_proceeds = Decimal("1")  # type: ignore[misc]
