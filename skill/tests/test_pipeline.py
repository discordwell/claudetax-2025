"""End-to-end pipeline integration tests (CP8-E).

These are the first tests in the project that exercise the FULL chain:

    synthetic PDF → classifier → ingester cascade → PartialReturn
        → apply_partial_to_dict → CanonicalReturn → compute()
            → render Form 1040 PDF → result.json

They intentionally use the same synthetic-fillable-PDF pattern as
``test_ingest_w2_acroform.py`` so the test fixture is self-contained
and does not depend on any real IRS PDF or any external service.

Unit-level tests for ``_parse_path``, ``_set_path``, and
``apply_partial_to_dict`` are in ``TestPathHelpers`` below and run
without touching the filesystem.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pypdf
import pytest
from reportlab.pdfgen import canvas

from skill.scripts.ingest._pipeline import FieldExtraction, PartialReturn
from skill.scripts.pipeline import (
    PipelineResult,
    _parse_path,
    _set_path,
    apply_partial_to_dict,
    build_default_cascade,
    run_pipeline,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_acroform_pdf(path: Path, fields: dict[str, str]) -> None:
    """Write a minimal AcroForm PDF (matches test_ingest_w2_acroform helper).

    Inlined rather than imported because test modules are independent.
    """
    c = canvas.Canvas(str(path))
    form = c.acroForm
    y = 700
    for name, _value in fields.items():
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

    reader = pypdf.PdfReader(str(path))
    writer = pypdf.PdfWriter(clone_from=reader)
    writer.update_page_form_field_values(
        writer.pages[0], fields, auto_regenerate=True
    )
    with path.open("wb") as fh:
        writer.write(fh)


def _write_minimal_taxpayer_json(path: Path) -> None:
    """Write a taxpayer_info.json that carries the header fields the
    PDF ingesters cannot extract — everything except w2s[]/forms_*[]."""
    data = {
        "schema_version": "0.1.0",
        "tax_year": 2025,
        "filing_status": "single",
        "taxpayer": {
            "first_name": "Alex",
            "last_name": "Doe",
            "ssn": "111-22-3333",
            "date_of_birth": "1985-01-01",
        },
        "address": {
            "street1": "1 Test Lane",
            "city": "Springfield",
            "state": "IL",
            "zip": "62701",
            "country": "US",
        },
    }
    path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Unit tests: path parser and dict patcher
# ---------------------------------------------------------------------------


class TestPathHelpers:
    def test_parse_scalar_path(self):
        assert _parse_path("taxpayer.first_name") == [
            ("taxpayer", None),
            ("first_name", None),
        ]

    def test_parse_indexed_path(self):
        assert _parse_path("w2s[0].box1_wages") == [
            ("w2s", 0),
            ("box1_wages", None),
        ]

    def test_parse_nested_indexed(self):
        assert _parse_path("w2s[1].state_rows[0].state_code") == [
            ("w2s", 1),
            ("state_rows", 0),
            ("state_code", None),
        ]

    def test_parse_single_segment(self):
        assert _parse_path("tax_year") == [("tax_year", None)]

    def test_parse_malformed_segment_raises(self):
        with pytest.raises(ValueError):
            _parse_path("w2s[].box1")

    def test_set_scalar_path_creates_nested_dict(self):
        root: dict = {}
        _set_path(root, "taxpayer.first_name", "Alex")
        assert root == {"taxpayer": {"first_name": "Alex"}}

    def test_set_indexed_path_creates_list(self):
        root: dict = {}
        _set_path(root, "w2s[0].box1_wages", "65000.00")
        assert root == {"w2s": [{"box1_wages": "65000.00"}]}

    def test_set_indexed_path_extends_list_to_length(self):
        root: dict = {}
        _set_path(root, "w2s[2].box1_wages", "1000")
        assert root == {
            "w2s": [{}, {}, {"box1_wages": "1000"}],
        }

    def test_set_preserves_existing_siblings(self):
        root = {"taxpayer": {"first_name": "Alex"}}
        _set_path(root, "taxpayer.last_name", "Doe")
        assert root == {"taxpayer": {"first_name": "Alex", "last_name": "Doe"}}

    def test_set_multiple_w2_fields(self):
        root: dict = {}
        _set_path(root, "w2s[0].box1_wages", "65000")
        _set_path(root, "w2s[0].box2_federal_income_tax_withheld", "9500")
        _set_path(root, "w2s[0].employer_name", "Acme")
        assert root == {
            "w2s": [
                {
                    "box1_wages": "65000",
                    "box2_federal_income_tax_withheld": "9500",
                    "employer_name": "Acme",
                }
            ]
        }

    def test_apply_partial_skips_acroform_raw_pseudo_paths(self):
        """``_acroform_raw.*`` pseudo-paths come from the pypdf base
        fallback when a per-form field map is missing. They are NOT
        part of the canonical schema and must be silently dropped
        (otherwise Pydantic validation explodes on the raw field
        names)."""
        partial = PartialReturn()
        partial.add("w2s[0].box1_wages", "65000")
        partial.add("_acroform_raw.mystery_field", "some raw text")
        base: dict = {}
        apply_partial_to_dict(partial, base)
        assert base == {"w2s": [{"box1_wages": "65000"}]}

    def test_apply_partial_returns_same_base(self):
        """Convenience return-self for chaining."""
        partial = PartialReturn(fields=[FieldExtraction("tax_year", 2025)])
        base: dict = {}
        result = apply_partial_to_dict(partial, base)
        assert result is base


# ---------------------------------------------------------------------------
# Cascade assembly
# ---------------------------------------------------------------------------


class TestBuildDefaultCascade:
    def test_cascade_registers_nine_tier_1_ingesters(self):
        cascade = build_default_cascade()
        names = cascade.ingester_names
        # W-2, 1099-INT, 1099-DIV, 1099-B, 1099-NEC, 1099-R, 1099-G, SSA-1099,
        # Schedule K-1 (K-1 added in wave 5 C1)
        assert len(names) == 9

    def test_every_ingester_is_tier_1(self):
        cascade = build_default_cascade()
        for ingester in cascade._ingesters:  # type: ignore[attr-defined]
            assert ingester.tier == 1


# ---------------------------------------------------------------------------
# Integration test: W-2 → compute → Form 1040 PDF
# ---------------------------------------------------------------------------


class TestPipelineEndToEndW2Only:
    """The golden happy-path test.

    Drops a synthetic fillable W-2 PDF into an input directory, runs
    ``run_pipeline``, and asserts the full chain produces:

      1. A CanonicalReturn with the W-2 income populated.
      2. ComputedTotals matching the pre-existing ``simple_w2_standard``
         golden ($65k wages, $15,750 standard deduction, $5,755 fed tax,
         $2,000 refund).
      3. A Form 1040 PDF file on disk.
      4. A ``result.json`` file on disk.
      5. A populated ``validation_report`` with FFFF compatible = True.
    """

    def test_w2_only_full_pipeline(self, tmp_path: Path):
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()

        # 1. Taxpayer info (header fields)
        taxpayer_info_path = tmp_path / "taxpayer_info.json"
        _write_minimal_taxpayer_json(taxpayer_info_path)

        # 2. Synthetic fillable W-2 PDF matching the wave-1 W2_FIELD_MAP
        w2_path = input_dir / "w2_acme.pdf"
        _make_acroform_pdf(
            w2_path,
            {
                "employer_name": "Acme Corp",
                "employer_ein": "12-3456789",
                "wages_box1": "65000.00",
                "fed_withholding_box2": "7500.00",
                "ss_wages_box3": "65000.00",
                "ss_tax_box4": "4030.00",
                "medicare_wages_box5": "65000.00",
                "medicare_tax_box6": "942.50",
            },
        )

        # 3. Run the pipeline
        result = run_pipeline(
            input_dir=input_dir,
            taxpayer_info_path=taxpayer_info_path,
            output_dir=output_dir,
        )

        # 4. Canonical return populated
        assert isinstance(result, PipelineResult)
        ret = result.canonical_return
        assert len(ret.w2s) == 1
        assert ret.w2s[0].box1_wages == Decimal("65000.00")
        assert ret.w2s[0].box2_federal_income_tax_withheld == Decimal("7500.00")
        assert ret.w2s[0].employer_name == "Acme Corp"

        # 5. Computed totals match the simple_w2_standard golden
        c = ret.computed
        assert c.adjusted_gross_income == Decimal("65000.00")
        assert c.deduction_taken == Decimal("15750.00")  # OBBBA single std ded
        assert c.taxable_income == Decimal("49250.00")
        assert c.tentative_tax == Decimal("5755.00")
        assert c.total_tax == Decimal("5755.00")
        assert c.refund == Decimal("1745.00")
        assert c.amount_owed is None

        # 6. Validation report populated
        assert c.validation_report is not None
        assert "ffff" in c.validation_report
        assert c.validation_report["ffff"]["compatible"] is True

        # 7. Rendered PDFs present
        form_1040_pdf = output_dir / "form_1040.pdf"
        assert form_1040_pdf.exists()
        assert form_1040_pdf.stat().st_size > 100  # non-empty

        # Standard deduction return — no Schedule A, no Schedule C, no
        # Schedule SE, no Schedule B (interest/dividends under $1,500).
        assert not (output_dir / "schedule_a.pdf").exists()
        assert not (output_dir / "schedule_b.pdf").exists()
        assert not (output_dir / "schedule_se.pdf").exists()

        # 8. result.json written and parseable
        result_json = output_dir / "result.json"
        assert result_json.exists()
        parsed = json.loads(result_json.read_text())
        assert parsed["tax_year"] == 2025
        assert parsed["computed"]["adjusted_gross_income"] == "65000.00"

    def test_pipeline_raises_on_missing_input_dir(self, tmp_path: Path):
        taxpayer_info_path = tmp_path / "taxpayer_info.json"
        _write_minimal_taxpayer_json(taxpayer_info_path)
        with pytest.raises(FileNotFoundError):
            run_pipeline(
                input_dir=tmp_path / "does_not_exist",
                taxpayer_info_path=taxpayer_info_path,
                output_dir=tmp_path / "output",
            )

    def test_pipeline_raises_on_missing_taxpayer_json(self, tmp_path: Path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            run_pipeline(
                input_dir=input_dir,
                taxpayer_info_path=tmp_path / "missing.json",
                output_dir=tmp_path / "output",
            )

    def test_pipeline_with_no_pdfs_succeeds_on_header_only(self, tmp_path: Path):
        """An empty input_dir still runs if taxpayer_info has enough
        for CanonicalReturn to validate — produces a zero-income return
        with standard deduction and $0 tax."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        taxpayer_info_path = tmp_path / "taxpayer_info.json"
        _write_minimal_taxpayer_json(taxpayer_info_path)
        output_dir = tmp_path / "output"

        result = run_pipeline(
            input_dir=input_dir,
            taxpayer_info_path=taxpayer_info_path,
            output_dir=output_dir,
        )
        assert len(result.canonical_return.w2s) == 0
        assert result.canonical_return.computed.adjusted_gross_income == Decimal("0.00")
        assert result.canonical_return.computed.total_tax == Decimal("0.00")
