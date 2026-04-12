"""Pipeline integration tests for paper bundle + FFFF entry map emission.

Complements ``test_pipeline.py``'s existing end-to-end tests by
asserting the wave-6 additions:

* ``run_pipeline`` now calls ``build_paper_bundle`` by default and the
  bundle PDF appears in ``output_dir`` + ``rendered_paths``.
* ``run_pipeline`` now emits ``ffff_entries.json`` and
  ``ffff_entries.txt`` by default.
* The ``build_paper_bundle=False`` kwarg suppresses bundle creation.
* The ``emit_ffff_map=False`` kwarg suppresses FFFF file emission.

Reuses the synthetic-fillable-W-2 helper from ``test_pipeline.py``.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pypdf
import pytest
from reportlab.pdfgen import canvas

from skill.scripts.pipeline import run_pipeline


def _make_acroform_pdf(path: Path, fields: dict[str, str]) -> None:
    """Write a minimal AcroForm PDF (mirrors test_pipeline.py helper)."""
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


def _seed_w2_pipeline(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Seed a synthetic W-2 + taxpayer info scaffold. Returns (input_dir,
    taxpayer_info_path, output_dir)."""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    taxpayer_info_path = tmp_path / "taxpayer_info.json"
    _write_minimal_taxpayer_json(taxpayer_info_path)

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
    return input_dir, taxpayer_info_path, output_dir


# ---------------------------------------------------------------------------
# 1. Defaults — paper bundle and FFFF map are both emitted
# ---------------------------------------------------------------------------


class TestPipelineDefaultsEmitBundleAndMap:
    def test_default_produces_paper_bundle(self, tmp_path: Path):
        input_dir, taxpayer_info_path, output_dir = _seed_w2_pipeline(tmp_path)
        result = run_pipeline(
            input_dir=input_dir,
            taxpayer_info_path=taxpayer_info_path,
            output_dir=output_dir,
        )
        bundle = output_dir / "paper_bundle.pdf"
        assert bundle.exists()
        assert bundle.stat().st_size > 1000  # non-trivial merged PDF
        # Bundle path is in rendered_paths
        assert bundle in result.rendered_paths

    def test_default_produces_ffff_entry_files(self, tmp_path: Path):
        input_dir, taxpayer_info_path, output_dir = _seed_w2_pipeline(tmp_path)
        run_pipeline(
            input_dir=input_dir,
            taxpayer_info_path=taxpayer_info_path,
            output_dir=output_dir,
        )
        ffff_json = output_dir / "ffff_entries.json"
        ffff_txt = output_dir / "ffff_entries.txt"
        assert ffff_json.exists()
        assert ffff_txt.exists()
        # JSON is valid and contains the expected metadata
        parsed = json.loads(ffff_json.read_text())
        assert parsed["tax_year"] == 2025
        assert parsed["taxpayer_name"] == "Alex Doe"
        assert parsed["filing_status"] == "Single"
        assert any(
            e["form"] == "1040" and e["line"] == "1a"
            for e in parsed["entries"]
        )

    def test_ffff_text_is_pure_ascii(self, tmp_path: Path):
        input_dir, taxpayer_info_path, output_dir = _seed_w2_pipeline(tmp_path)
        run_pipeline(
            input_dir=input_dir,
            taxpayer_info_path=taxpayer_info_path,
            output_dir=output_dir,
        )
        text = (output_dir / "ffff_entries.txt").read_text()
        non_ascii = [c for c in text if ord(c) > 127]
        assert non_ascii == [], f"non-ASCII chars: {non_ascii[:5]}"


# ---------------------------------------------------------------------------
# 2. Opt-out kwargs
# ---------------------------------------------------------------------------


class TestPipelineBundleOptOut:
    def test_build_paper_bundle_false_skips_bundle(self, tmp_path: Path):
        input_dir, taxpayer_info_path, output_dir = _seed_w2_pipeline(tmp_path)
        result = run_pipeline(
            input_dir=input_dir,
            taxpayer_info_path=taxpayer_info_path,
            output_dir=output_dir,
            build_paper_bundle=False,
        )
        assert not (output_dir / "paper_bundle.pdf").exists()
        assert not any(
            p.name == "paper_bundle.pdf" for p in result.rendered_paths
        )
        # Loose federal forms are still rendered
        assert (output_dir / "form_1040.pdf").exists()

    def test_emit_ffff_map_false_skips_ffff_files(self, tmp_path: Path):
        input_dir, taxpayer_info_path, output_dir = _seed_w2_pipeline(tmp_path)
        run_pipeline(
            input_dir=input_dir,
            taxpayer_info_path=taxpayer_info_path,
            output_dir=output_dir,
            emit_ffff_map=False,
        )
        assert not (output_dir / "ffff_entries.json").exists()
        assert not (output_dir / "ffff_entries.txt").exists()

    def test_both_opt_out(self, tmp_path: Path):
        input_dir, taxpayer_info_path, output_dir = _seed_w2_pipeline(tmp_path)
        result = run_pipeline(
            input_dir=input_dir,
            taxpayer_info_path=taxpayer_info_path,
            output_dir=output_dir,
            build_paper_bundle=False,
            emit_ffff_map=False,
        )
        assert not (output_dir / "paper_bundle.pdf").exists()
        assert not (output_dir / "ffff_entries.json").exists()
        assert not (output_dir / "ffff_entries.txt").exists()
        # Loose forms and result.json still produced
        assert (output_dir / "form_1040.pdf").exists()
        assert (output_dir / "result.json").exists()
        # rendered_paths contains only the loose PDFs
        assert all(
            p.name != "paper_bundle.pdf" for p in result.rendered_paths
        )


# ---------------------------------------------------------------------------
# 3. Empty-input edge case — bundle is skipped when no forms rendered
# ---------------------------------------------------------------------------


class TestPipelineEmptyRendered:
    def test_no_rendered_forms_skips_bundle(self, tmp_path: Path):
        """If the return has nothing to render (e.g. gated off), the
        paper bundle step should be a no-op because there is nothing
        to merge. Rather than crashing, the pipeline just skips it.
        """
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        taxpayer_info_path = tmp_path / "taxpayer_info.json"
        _write_minimal_taxpayer_json(taxpayer_info_path)
        output_dir = tmp_path / "output"

        result = run_pipeline(
            input_dir=input_dir,
            taxpayer_info_path=taxpayer_info_path,
            output_dir=output_dir,
            render_form_1040=False,  # disable every renderer
            render_schedule_a=False,
            render_schedule_b=False,
            render_schedule_c=False,
            render_schedule_se=False,
            render_state_returns=False,
        )
        assert result.rendered_paths == []
        # Bundle path not created because rendered list is empty
        assert not (output_dir / "paper_bundle.pdf").exists()
        # FFFF map still emitted (independent of rendered PDFs)
        assert (output_dir / "ffff_entries.json").exists()
        assert (output_dir / "ffff_entries.txt").exists()
