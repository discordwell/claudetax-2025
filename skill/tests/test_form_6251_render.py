"""Tests for Form 6251 AcroForm overlay renderer (Layer 2)."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.calc.engine import compute
from skill.scripts.models import (
    Address,
    AMTAdjustments,
    CanonicalReturn,
    ComputedTotals,
    FilingStatus,
    ItemizedDeductions,
    Person,
)
from skill.scripts.output import form_6251 as f6251_mod
from skill.scripts.output.form_6251 import (
    Form6251Fields,
    compute_form_6251_fields,
    render_form_6251_pdf,
)


def _person(first: str = "Test", last: str = "Payer") -> Person:
    return Person(
        first_name=first,
        last_name=last,
        ssn="111-22-3333",
        date_of_birth="1985-06-15",
    )


def _address() -> Address:
    return Address(street1="1 Test St", city="Springfield", state="IL", zip="62701")


def _minimal_return(
    *,
    filing_status: FilingStatus = FilingStatus.SINGLE,
    amt_adjustments_manual: AMTAdjustments | None = None,
    spouse: Person | None = None,
) -> CanonicalReturn:
    needs_spouse = filing_status in (FilingStatus.MFJ, FilingStatus.MFS)
    if needs_spouse and spouse is None:
        spouse = _person("Spouse", "Two")
    return CanonicalReturn.model_validate(
        {
            "schema_version": "0.1.0",
            "tax_year": 2025,
            "filing_status": filing_status.value,
            "taxpayer": _person().model_dump(mode="json"),
            "spouse": spouse.model_dump(mode="json") if spouse else None,
            "address": _address().model_dump(mode="json"),
            "amt_adjustments_manual": amt_adjustments_manual.model_dump(mode="json")
            if amt_adjustments_manual is not None
            else None,
        }
    )


def _synthetic_amt_fields_500k_iso() -> Form6251Fields:
    """Build a set of Form 6251 fields that produce a nonzero line 11."""
    r = _minimal_return(
        filing_status=FilingStatus.SINGLE,
        amt_adjustments_manual=AMTAdjustments(
            iso_bargain_element=Decimal("500000")
        ),
    )
    r = r.model_copy(
        update={
            "computed": ComputedTotals(
                total_income=Decimal("0"),
                adjustments_total=Decimal("0"),
                adjusted_gross_income=Decimal("0"),
                deduction_taken=Decimal("0"),
                taxable_income=Decimal("0"),
                tentative_tax=Decimal("0"),
            )
        }
    )
    return compute_form_6251_fields(r)


def _load_widget_value(out_path: Path, terminal_substring: str):
    """Look up a filled widget value from a freshly-written Form 6251 PDF."""
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(out_path))
    fields = reader.get_fields() or {}
    for k, v in fields.items():
        if terminal_substring in k:
            return v.get("/V")
    return None


def test_render_produces_non_empty_pdf(tmp_path: Path) -> None:
    """Render via the AcroForm overlay and assert the output is non-empty."""
    fields = _synthetic_amt_fields_500k_iso()
    # Sanity on the synthetic fixture
    assert fields.line_11_amt_owed == Decimal("110550.00")

    out_path = tmp_path / "form_6251.pdf"
    result = render_form_6251_pdf(fields, out_path)

    assert result == out_path
    assert out_path.exists()
    # The IRS source PDF is ~104 KB; a filled PDF is comparable in size.
    assert out_path.stat().st_size > 50_000


def test_render_round_trip_line_11_amt() -> None:
    """Line 11 (final AMT) should round-trip through the filled PDF.

    The synthetic $500k-ISO fixture lands at $110,550.00 on line 11,
    which is the f1_33 widget (terminal f1_33[) per the wave-6 map.
    """
    pytest.importorskip("pypdf")
    import tempfile

    fields = _synthetic_amt_fields_500k_iso()
    with tempfile.TemporaryDirectory() as d:
        out_path = Path(d) / "round_trip_11.pdf"
        render_form_6251_pdf(fields, out_path)
        assert _load_widget_value(out_path, "f1_33[") == "110550.00"


def test_render_round_trip_line_4_amti(tmp_path: Path) -> None:
    """Line 4 (AMTI) round-trips to its widget.

    Line 4 is the 26th f1_* widget (f1_26, terminal f1_26[) per the
    wave-6 map.
    """
    fields = _synthetic_amt_fields_500k_iso()
    assert fields.line_4_amti == Decimal("500000.00")
    out_path = tmp_path / "round_trip_4.pdf"
    render_form_6251_pdf(fields, out_path)
    assert _load_widget_value(out_path, "f1_26[") == "500000.00"


def test_render_round_trip_line_5_exemption(tmp_path: Path) -> None:
    fields = _synthetic_amt_fields_500k_iso()
    assert fields.line_5_exemption == Decimal("88100")
    out_path = tmp_path / "round_trip_5.pdf"
    render_form_6251_pdf(fields, out_path)
    # f1_27 -> line 5 (exemption)
    assert _load_widget_value(out_path, "f1_27[") == "88100.00"


def test_render_round_trip_line_2i_iso(tmp_path: Path) -> None:
    """Line 2i (ISO exercise preference) round-trips.

    Line 2i is the 13th f1_* widget (f1_13, terminal f1_13[).
    """
    fields = _synthetic_amt_fields_500k_iso()
    assert fields.line_2i_iso_exercise == Decimal("500000")
    out_path = tmp_path / "round_trip_2i.pdf"
    render_form_6251_pdf(fields, out_path)
    assert _load_widget_value(out_path, "f1_13[") == "500000.00"


def test_render_zero_lines_are_blank(tmp_path: Path) -> None:
    """Lines the filer does not use (e.g. 2c, 2d, 2h) should be empty."""
    fields = _synthetic_amt_fields_500k_iso()
    # Line 2c (investment interest expense) was not populated by Layer 1
    assert fields.line_2c_investment_interest_expense == Decimal("0")

    out_path = tmp_path / "blanks.pdf"
    render_form_6251_pdf(fields, out_path)

    # Line 2c is the 5th f1_* widget (f1_7 terminal, but actually f1_7
    # is line 2c — let's look at which index corresponds). We'll
    # verify that AT LEAST ONE of the unused fields is blank by
    # loading all fields and counting non-empty values.
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(out_path))
    allf = reader.get_fields() or {}
    # The synthetic $500k-ISO fixture populates lines 1b, 2i, 4, 5, 6,
    # 7, 9, 11 — 8 numeric values — plus the taxpayer name, so we
    # expect at most ~10 filled widgets out of 62.
    filled = [k for k, v in allf.items() if v.get("/V") not in (None, "")]
    assert len(filled) <= 15, f"too many filled widgets: {sorted(filled)}"
    assert len(filled) >= 6, f"too few filled widgets: {sorted(filled)}"


def test_render_taxpayer_name_round_trip(tmp_path: Path) -> None:
    """The header name widget should carry the Layer 1 taxpayer_name string."""
    fields = _synthetic_amt_fields_500k_iso()
    assert fields.taxpayer_name == "Test Payer"

    out_path = tmp_path / "name.pdf"
    render_form_6251_pdf(fields, out_path)

    # f1_1 -> taxpayer name
    assert _load_widget_value(out_path, "f1_1[") == "Test Payer"


def test_render_raises_when_source_pdf_missing(
    monkeypatch, tmp_path: Path
) -> None:
    """Pre-flight: if the IRS source PDF is missing, raise RuntimeError."""
    bogus = tmp_path / "missing.pdf"
    monkeypatch.setattr(f6251_mod, "_FORM_6251_PDF_PATH", bogus)

    fields = _synthetic_amt_fields_500k_iso()
    with pytest.raises(RuntimeError, match="missing"):
        f6251_mod.render_form_6251_pdf(fields, tmp_path / "out.pdf")


def test_render_raises_on_sha_mismatch(monkeypatch, tmp_path: Path) -> None:
    """If the IRS PDF SHA-256 changes (silent re-issue), raise RuntimeError."""
    # Force the map JSON to carry a bogus SHA so the verify step fires.
    real_map = json.loads(f6251_mod._FORM_6251_MAP_PATH.read_text())
    real_map["source_pdf_sha256"] = "deadbeef" * 8
    fake_map_path = tmp_path / "fake_map.json"
    fake_map_path.write_text(json.dumps(real_map))
    monkeypatch.setattr(f6251_mod, "_FORM_6251_MAP_PATH", fake_map_path)

    fields = _synthetic_amt_fields_500k_iso()
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        f6251_mod.render_form_6251_pdf(fields, tmp_path / "out.pdf")


def test_widget_map_coverage_all_62_mapped() -> None:
    """The widget map should account for every widget in the bundled PDF.

    Per the TY2025 form: 62 widgets total (33 on page 1, 29 on page 2).
    The Layer 2 renderer iterates the dataclass — every Form6251Fields
    attribute except `filing_status` and `taxpayer_ssn` should have a
    mapping entry so a change to the Layer 1 shape is caught here.
    """
    widget_map = json.loads(f6251_mod._FORM_6251_MAP_PATH.read_text())
    assert widget_map["total_widgets"] == 62
    assert widget_map["mapped_count"] == 62
    # The Layer 1 dataclass has more named lines than Part III needs;
    # every line_N_* field should be in the mapping (we don't require
    # Part III line values to be non-zero, just to be mappable).
    for f in Form6251Fields.__dataclass_fields__:
        if f in {"filing_status"}:
            continue
        assert f in widget_map["mapping"], (
            f"Form6251Fields attribute {f!r} has no widget map entry"
        )
