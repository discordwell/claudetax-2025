"""Unit tests for ``skill.scripts.output._acroform_overlay``.

These tests cover the shared widget-map-driven AcroForm fill helper
that the per-form renderers (Form 1040 first, Schedule A/B/C/SE next
wave) compose. They use synthetic fillable PDFs generated via reportlab
so they have no dependency on the IRS-hosted f1040.pdf.

Coverage:

* :func:`format_money` formatting rules (zero -> empty, decimal
  quantization, no thousands separator).
* :func:`load_widget_map` parses a wave-4 reference JSON, including the
  ``computed_copies`` and ``filing_status_checkboxes`` blocks, and
  filters out wildcard / checkbox-typed entries from the main mapping.
* :func:`fetch_and_verify_source_pdf` is a no-op when the cached file
  matches the pinned digest, raises ``RuntimeError`` on a mismatch
  with no network reachable, and verifies the live IRS PDF SHA-256
  end-to-end.
* :func:`fill_acroform_pdf` round-trips text values into a synthetic
  fillable PDF and emits the filled bytes at the requested path.
* The Form 1040 widget map is loadable as a smoke test (it must
  conform to the helper's expected schema).
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.output._acroform_overlay import (
    WidgetMap,
    build_widget_values,
    fetch_and_verify_source_pdf,
    fill_acroform_pdf,
    format_money,
    load_widget_map,
)


# ---------------------------------------------------------------------------
# Synthetic PDF helper (mirrors test_pipeline._make_acroform_pdf)
# ---------------------------------------------------------------------------


def _make_synthetic_acroform_pdf(path: Path, field_names: list[str]) -> None:
    """Generate a minimal fillable PDF with the given text widgets."""
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path))
    form = c.acroForm
    y = 700
    for name in field_names:
        c.drawString(50, y + 20, name)
        form.textfield(
            name=name,
            x=200,
            y=y,
            width=200,
            height=18,
            borderStyle="solid",
        )
        y -= 40
    c.save()


# ---------------------------------------------------------------------------
# format_money
# ---------------------------------------------------------------------------


class TestFormatMoney:
    def test_none_collapses_to_empty(self):
        assert format_money(None) == ""

    def test_zero_collapses_to_empty(self):
        assert format_money(Decimal("0")) == ""
        assert format_money(0) == ""
        assert format_money(0.0) == ""

    def test_integer_amount(self):
        assert format_money(Decimal("65000")) == "65000.00"

    def test_no_thousands_separator(self):
        # IRS PDFs accept either; plain-decimal is the safer choice.
        assert format_money(Decimal("123456.78")) == "123456.78"
        assert "," not in format_money(Decimal("1234567.89"))

    def test_quantizes_to_two_places(self):
        assert format_money(Decimal("100.5")) == "100.50"
        # Decimal default rounding is ROUND_HALF_EVEN ("banker's round").
        # 100.005 rounds to 100.00 because 0 is even.
        assert format_money(Decimal("100.005")) == "100.00"
        # 100.015 rounds away from 0 because the next digit (1) is odd.
        assert format_money(Decimal("100.015")) == "100.02"

    def test_negative_amount(self):
        # Capital losses, etc.
        assert format_money(Decimal("-1234.56")) == "-1234.56"

    def test_int_and_float_inputs_supported(self):
        assert format_money(7500) == "7500.00"
        assert format_money(7500.25) == "7500.25"


# ---------------------------------------------------------------------------
# load_widget_map
# ---------------------------------------------------------------------------


class TestLoadWidgetMap:
    def test_form_1040_widget_map_loads(self, reference_dir: Path):
        wm = load_widget_map(reference_dir / "form-1040-acroform-map.json")
        assert isinstance(wm, WidgetMap)
        assert wm.source_pdf_url.startswith("https://www.irs.gov/")
        assert len(wm.source_pdf_sha256) == 64
        assert "line_1z_total_wages" in wm.semantic_to_widget
        assert "line_11_adjusted_gross_income" in wm.semantic_to_widget

    def test_form_1040_filing_status_checkboxes_present(
        self, reference_dir: Path
    ):
        wm = load_widget_map(reference_dir / "form-1040-acroform-map.json")
        # All five filing statuses are mapped.
        assert set(wm.filing_status_checkboxes) == {
            "SINGLE",
            "MFJ",
            "MFS",
            "HOH",
            "QSS",
        }

    def test_form_1040_computed_copies_includes_line_11(
        self, reference_dir: Path
    ):
        wm = load_widget_map(reference_dir / "form-1040-acroform-map.json")
        copies = wm.computed_copies.get("line_11_adjusted_gross_income", [])
        assert len(copies) >= 1
        # The page-2 mirror is f2_01.
        assert any("f2_01" in w for w in copies)

    def test_widget_names_for_includes_primary_and_copies(
        self, reference_dir: Path
    ):
        wm = load_widget_map(reference_dir / "form-1040-acroform-map.json")
        widgets = wm.widget_names_for("line_11_adjusted_gross_income")
        # Primary (page 1) + at least one mirror (page 2).
        assert len(widgets) >= 2
        assert any("Page1" in w for w in widgets)
        assert any("Page2" in w for w in widgets)

    def test_widget_names_for_unmapped_returns_empty(
        self, reference_dir: Path
    ):
        wm = load_widget_map(reference_dir / "form-1040-acroform-map.json")
        assert wm.widget_names_for("nonexistent_field") == []

    def test_load_widget_map_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_widget_map(tmp_path / "does_not_exist.json")

    def test_filing_status_wildcard_entry_filtered(
        self, reference_dir: Path
    ):
        wm = load_widget_map(reference_dir / "form-1040-acroform-map.json")
        # The mapping JSON has a wildcard widget_name "...c1_8[*]" for
        # filing_status. It should NOT appear in semantic_to_widget;
        # only the per-status checkboxes should be in
        # filing_status_checkboxes.
        assert "filing_status" not in wm.semantic_to_widget


# ---------------------------------------------------------------------------
# fetch_and_verify_source_pdf
# ---------------------------------------------------------------------------


class TestFetchAndVerifySourcePdf:
    def test_existing_file_with_correct_sha_is_noop(self, tmp_path: Path):
        target = tmp_path / "f.pdf"
        payload = b"hello world"
        target.write_bytes(payload)
        sha = hashlib.sha256(payload).hexdigest()

        result = fetch_and_verify_source_pdf(
            target, "http://invalid.example/never-fetched", sha
        )
        assert result == target
        assert target.read_bytes() == payload

    def test_missing_file_unreachable_url_raises(self, tmp_path: Path):
        target = tmp_path / "missing.pdf"
        with pytest.raises(RuntimeError, match="failed to download"):
            fetch_and_verify_source_pdf(
                target,
                "http://127.0.0.1:1/never-listening.pdf",
                "0" * 64,
                timeout_seconds=2.0,
            )

    def test_existing_wrong_sha_unreachable_url_raises(self, tmp_path: Path):
        target = tmp_path / "wrong.pdf"
        target.write_bytes(b"not the real PDF")
        # Expected sha is for the empty string -> never matches.
        empty_sha = hashlib.sha256(b"").hexdigest()
        with pytest.raises(RuntimeError):
            fetch_and_verify_source_pdf(
                target,
                "http://127.0.0.1:1/never-listening.pdf",
                empty_sha,
                timeout_seconds=2.0,
            )

    def test_bundled_irs_form_1040_sha_verifies(self, reference_dir: Path):
        """The wave-5 worktree must already have the verified IRS PDF
        cached at skill/reference/irs_forms/f1040.pdf — this test would
        fail loudly if a future regeneration desynced the bundled copy
        from the SHA-256 pinned in the widget map JSON."""
        map_json = reference_dir / "form-1040-acroform-map.json"
        data = json.loads(map_json.read_text())
        target = reference_dir / "irs_forms" / "f1040.pdf"
        if not target.exists():
            pytest.skip(
                "f1040.pdf not bundled; will be fetched on first render"
            )
        # Should be a no-op (file exists, SHA matches).
        result = fetch_and_verify_source_pdf(
            target,
            data["source_pdf_url"],
            data["source_pdf_sha256"],
        )
        assert result == target


# ---------------------------------------------------------------------------
# fill_acroform_pdf round-trip
# ---------------------------------------------------------------------------


class TestFillAcroformPdf:
    def test_text_fields_round_trip(self, tmp_path: Path):
        import pypdf

        source = tmp_path / "source.pdf"
        _make_synthetic_acroform_pdf(
            source, ["alpha", "beta", "gamma"]
        )

        out = tmp_path / "filled.pdf"
        result = fill_acroform_pdf(
            source,
            {
                "alpha": "111.00",
                "beta": "222.50",
                "gamma": "33333.33",
            },
            out,
        )
        assert result == out
        assert out.exists()
        assert out.stat().st_size > 0

        reader = pypdf.PdfReader(str(out))
        gf = reader.get_fields()
        assert gf["alpha"]["/V"] == "111.00"
        assert gf["beta"]["/V"] == "222.50"
        assert gf["gamma"]["/V"] == "33333.33"

    def test_partial_fill_leaves_others_blank(self, tmp_path: Path):
        import pypdf

        source = tmp_path / "source.pdf"
        _make_synthetic_acroform_pdf(source, ["a", "b", "c"])
        out = tmp_path / "filled.pdf"
        fill_acroform_pdf(source, {"b": "only b"}, out)

        reader = pypdf.PdfReader(str(out))
        gf = reader.get_fields()
        assert gf["b"]["/V"] == "only b"
        # Untouched widgets are blank (reportlab seeds an empty /V default
        # on synthetic widgets, so accept either None or empty string).
        assert gf["a"].get("/V") in (None, "")
        assert gf["c"].get("/V") in (None, "")

    def test_missing_source_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            fill_acroform_pdf(
                tmp_path / "nope.pdf",
                {"a": "x"},
                tmp_path / "out.pdf",
            )

    def test_unknown_widget_name_raises(self, tmp_path: Path):
        source = tmp_path / "source.pdf"
        _make_synthetic_acroform_pdf(source, ["only_field"])
        with pytest.raises(RuntimeError, match="could not be located"):
            fill_acroform_pdf(
                source,
                {"missing_field": "x"},
                tmp_path / "out.pdf",
            )

    def test_creates_parent_directory(self, tmp_path: Path):
        source = tmp_path / "source.pdf"
        _make_synthetic_acroform_pdf(source, ["a"])
        out = tmp_path / "deep" / "nested" / "out.pdf"
        fill_acroform_pdf(source, {"a": "v"}, out)
        assert out.exists()


# ---------------------------------------------------------------------------
# build_widget_values
# ---------------------------------------------------------------------------


class TestBuildWidgetValues:
    def test_decimal_values_formatted_as_money(self):
        wm = WidgetMap(
            source_pdf_url="http://x",
            source_pdf_sha256="0" * 64,
            semantic_to_widget={
                "line_1": "topmostSubform[0].Page1[0].f1_01[0]",
                "line_2": "topmostSubform[0].Page1[0].f1_02[0]",
            },
        )
        values = build_widget_values(
            wm,
            {
                "line_1": Decimal("100.00"),
                "line_2": Decimal("0"),
            },
        )
        assert values["topmostSubform[0].Page1[0].f1_01[0]"] == "100.00"
        # Zero collapses to empty string.
        assert values["topmostSubform[0].Page1[0].f1_02[0]"] == ""

    def test_unmapped_field_silently_skipped(self):
        wm = WidgetMap(
            source_pdf_url="http://x",
            source_pdf_sha256="0" * 64,
            semantic_to_widget={"line_1": "w1"},
        )
        values = build_widget_values(
            wm, {"line_1": Decimal("1.00"), "missing": Decimal("9.00")}
        )
        assert values == {"w1": "1.00"}

    def test_computed_copies_propagate_value(self):
        wm = WidgetMap(
            source_pdf_url="http://x",
            source_pdf_sha256="0" * 64,
            semantic_to_widget={"line_11": "w_pri"},
            computed_copies={"line_11": ["w_copy_a", "w_copy_b"]},
        )
        values = build_widget_values(wm, {"line_11": Decimal("65000.00")})
        assert values == {
            "w_pri": "65000.00",
            "w_copy_a": "65000.00",
            "w_copy_b": "65000.00",
        }

    def test_string_values_passed_through(self):
        wm = WidgetMap(
            source_pdf_url="http://x",
            source_pdf_sha256="0" * 64,
            semantic_to_widget={"taxpayer_name": "name_widget"},
        )
        values = build_widget_values(wm, {"taxpayer_name": "Alex Doe"})
        assert values == {"name_widget": "Alex Doe"}
