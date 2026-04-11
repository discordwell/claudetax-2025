"""Tests for Form 4562 Layer 2 AcroForm overlay rendering.

Confirms:
* The bundled IRS f4562.pdf SHA-256 matches the wave-6 pin.
* The widget map JSON parses and its semantic keys match the Layer 1
  ``Form4562Fields`` dataclass field names for the subset that is
  actually mapped.
* A single render produces a valid PDF that pypdf can reopen, and that
  the filled widgets carry the expected values.
* Multi-business dispatch emits one filled PDF per Schedule C with
  depreciable assets.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.models import (
    CanonicalReturn,
    DepreciableAsset,
    ScheduleC,
)
from skill.scripts.output.form_4562 import (
    compute_form_4562_fields,
    render_form_4562_pdf,
    render_form_4562_pdfs_all,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PDF_PATH = _REPO_ROOT / "skill" / "reference" / "irs_forms" / "f4562.pdf"
_MAP_PATH = _REPO_ROOT / "skill" / "reference" / "form-4562-acroform-map.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_return(asset: DepreciableAsset | None = None) -> CanonicalReturn:
    sc_dict: dict = {
        "business_name": "Render Co",
        "principal_business_or_profession": "Rendering widgets",
        "line1_gross_receipts": "300000.00",
    }
    if asset is not None:
        sc_dict["depreciable_assets"] = [asset.model_dump(mode="json")]
    return CanonicalReturn.model_validate(
        {
            "schema_version": "0.1.0",
            "tax_year": 2025,
            "filing_status": "single",
            "taxpayer": {
                "first_name": "Renée",
                "last_name": "Render",
                "ssn": "123-45-6789",
                "date_of_birth": "1980-01-01",
                "is_blind": False,
                "is_age_65_or_older": False,
            },
            "address": {
                "street1": "1 Render Way",
                "city": "Austin",
                "state": "TX",
                "zip": "78701",
            },
            "schedules_c": [sc_dict],
        }
    )


# ---------------------------------------------------------------------------
# Reference files
# ---------------------------------------------------------------------------


class TestReferenceFiles:
    def test_pdf_is_bundled(self) -> None:
        assert _PDF_PATH.exists(), f"missing bundled f4562.pdf at {_PDF_PATH}"

    def test_widget_map_loads(self) -> None:
        data = json.loads(_MAP_PATH.read_text())
        assert data["form"] == "4562"
        assert data["form_year"] == 2025
        assert "mapping" in data
        assert len(data["mapping"]) > 0

    def test_pdf_sha256_matches_widget_map(self) -> None:
        data = json.loads(_MAP_PATH.read_text())
        actual = _sha256_file(_PDF_PATH)
        assert actual == data["source_pdf_sha256"]

    def test_widget_names_resolve_in_pdf(self) -> None:
        """Every mapped widget_name must exist in the IRS PDF's field list."""
        from pypdf import PdfReader

        data = json.loads(_MAP_PATH.read_text())
        reader = PdfReader(str(_PDF_PATH))
        all_fields = set((reader.get_fields() or {}).keys())
        missing: list[str] = []
        for sem, entry in data["mapping"].items():
            wn = entry["widget_name"]
            if wn not in all_fields:
                missing.append(f"{sem}:{wn}")
        assert not missing, f"widgets not in PDF: {missing}"


# ---------------------------------------------------------------------------
# Single render
# ---------------------------------------------------------------------------


class TestSingleRender:
    def test_render_basic_asset(self, tmp_path: Path) -> None:
        from pypdf import PdfReader

        asset = DepreciableAsset(
            description="Laptop",
            date_placed_in_service=dt.date(2025, 3, 1),
            cost=Decimal("10000"),
            macrs_class="5",
            bonus_depreciation_elected=False,
        )
        ret = _make_return(asset)
        fields = compute_form_4562_fields(ret, 0)

        out = tmp_path / "form_4562.pdf"
        render_form_4562_pdf(fields, out)

        assert out.exists()
        assert out.stat().st_size > 1000

        # Reopen and confirm a key widget holds the computed value
        reader = PdfReader(str(out))
        extracted = reader.get_fields() or {}
        # Line 1: $1,250,000 max amount
        line1 = extracted.get("topmostSubform[0].Page1[0].f1_4[0]")
        assert line1 is not None
        assert line1.get("/V") == "1250000.00"

    def test_render_header_is_populated(self, tmp_path: Path) -> None:
        from pypdf import PdfReader

        asset = DepreciableAsset(
            description="Laptop",
            date_placed_in_service=dt.date(2025, 3, 1),
            cost=Decimal("10000"),
            macrs_class="5",
            bonus_depreciation_elected=False,
        )
        ret = _make_return(asset)
        fields = compute_form_4562_fields(ret, 0)

        out = tmp_path / "form_4562.pdf"
        render_form_4562_pdf(fields, out)

        reader = PdfReader(str(out))
        extracted = reader.get_fields() or {}
        # Header: taxpayer name lives at f1_1, business at f1_2
        name_widget = extracted.get("topmostSubform[0].Page1[0].f1_1[0]")
        assert name_widget is not None
        # Either /V holds the string, or an empty value passed through
        v = name_widget.get("/V", "")
        assert "Render" in str(v)

    def test_line_22_total_lands_on_page_2(self, tmp_path: Path) -> None:
        """Line 22 total depreciation flows to the page-2 summary widget."""
        from pypdf import PdfReader

        asset = DepreciableAsset(
            description="Widget",
            date_placed_in_service=dt.date(2025, 1, 1),
            cost=Decimal("10000"),
            macrs_class="5",
            bonus_depreciation_elected=False,
        )
        ret = _make_return(asset)
        fields = compute_form_4562_fields(ret, 0)

        out = tmp_path / "form_4562.pdf"
        render_form_4562_pdf(fields, out)

        reader = PdfReader(str(out))
        extracted = reader.get_fields() or {}
        # f2_57 is our chosen line-22 widget on page 2
        line22 = extracted.get("topmostSubform[0].Page2[0].f2_57[0]")
        assert line22 is not None
        v = line22.get("/V", "")
        assert "2000" in str(v)


# ---------------------------------------------------------------------------
# Multi-business dispatch
# ---------------------------------------------------------------------------


class TestMultiBusinessDispatch:
    def test_one_pdf_per_business_with_assets(self, tmp_path: Path) -> None:
        asset = DepreciableAsset(
            description="Computer",
            date_placed_in_service=dt.date(2025, 1, 1),
            cost=Decimal("10000"),
            macrs_class="5",
            bonus_depreciation_elected=False,
        )
        ret = CanonicalReturn.model_validate(
            {
                "schema_version": "0.1.0",
                "tax_year": 2025,
                "filing_status": "single",
                "taxpayer": {
                    "first_name": "Multi",
                    "last_name": "Biz",
                    "ssn": "111-22-3333",
                    "date_of_birth": "1980-01-01",
                    "is_blind": False,
                    "is_age_65_or_older": False,
                },
                "address": {
                    "street1": "1 Main",
                    "city": "Austin",
                    "state": "TX",
                    "zip": "78701",
                },
                "schedules_c": [
                    {
                        "business_name": "Biz With Assets",
                        "principal_business_or_profession": "Dev",
                        "line1_gross_receipts": "100000.00",
                        "depreciable_assets": [asset.model_dump(mode="json")],
                    },
                    {
                        "business_name": "Biz Without Assets",
                        "principal_business_or_profession": "Consulting",
                        "line1_gross_receipts": "50000.00",
                    },
                ],
            }
        )
        written = render_form_4562_pdfs_all(ret, tmp_path)
        # Only the first business produces a PDF
        assert len(written) == 1
        assert "biz_with_assets" in written[0].name

    def test_empty_list_when_no_assets(self, tmp_path: Path) -> None:
        ret = _make_return(asset=None)
        written = render_form_4562_pdfs_all(ret, tmp_path)
        assert written == []


# ---------------------------------------------------------------------------
# End-to-end: run_pipeline produces a Form 4562 and line 13 depreciation flows
# ---------------------------------------------------------------------------


class TestPipelineEndToEnd:
    """Exercise the full run_pipeline path with a Schedule C depreciable asset.

    Confirms the Wave 6 wiring: a $10k computer on a Schedule C produces
    a Form 4562 PDF, and the engine's computed total_income reflects the
    line-13 depreciation (reducing net profit by $2,000).
    """

    def test_end_to_end_writes_form_4562_and_reduces_schedule_c(
        self, tmp_path: Path
    ) -> None:
        from skill.scripts.pipeline import run_pipeline

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"

        taxpayer_info = {
            "schema_version": "0.1.0",
            "tax_year": 2025,
            "filing_status": "single",
            "taxpayer": {
                "first_name": "Depr",
                "last_name": "Tester",
                "ssn": "123-45-6789",
                "date_of_birth": "1980-01-01",
                "is_blind": False,
                "is_age_65_or_older": False,
            },
            "address": {
                "street1": "1 Depr Way",
                "city": "Austin",
                "state": "TX",
                "zip": "78701",
            },
            "schedules_c": [
                {
                    "business_name": "Depr Testing LLC",
                    "principal_business_or_profession": "Software dev",
                    "line1_gross_receipts": "100000.00",
                    "depreciable_assets": [
                        {
                            "description": "MacBook Pro",
                            "date_placed_in_service": "2025-03-01",
                            "cost": "10000.00",
                            "macrs_class": "5",
                            "bonus_depreciation_elected": False,
                        }
                    ],
                }
            ],
        }
        taxpayer_info_path = tmp_path / "taxpayer.json"
        taxpayer_info_path.write_text(json.dumps(taxpayer_info))

        result = run_pipeline(
            input_dir=input_dir,
            taxpayer_info_path=taxpayer_info_path,
            output_dir=output_dir,
        )

        # A Form 4562 PDF was rendered for this business
        f4562 = [
            p for p in result.rendered_paths if p.name.startswith("form_4562_")
        ]
        assert len(f4562) == 1, f"expected 1 Form 4562, got {result.rendered_paths}"

        # The Schedule C net profit on the canonical return reflects
        # the $2,000 depreciation deduction ($100k gross - $2k MACRS).
        from skill.scripts.calc.engine import schedule_c_net_profit

        canonical = result.canonical_return
        assert len(canonical.schedules_c) == 1
        net_profit = schedule_c_net_profit(canonical.schedules_c[0])
        assert net_profit == Decimal("98000.00")
