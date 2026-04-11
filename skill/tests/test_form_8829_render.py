"""Tests for Form 8829 render — AcroForm overlay on f8829.pdf.

Layer 2 under test: ``render_form_8829_pdf`` writes a filled IRS
fillable PDF; ``render_form_8829_pdfs_all`` dispatches per Schedule C.

Coverage:
    * Render one full form end-to-end, reopen with pypdf, assert
      widget values match Layer 1 output.
    * Widget map SHA-256 matches the bundled f8829.pdf.
    * Per-business dispatch produces the right filenames and skips
      simplified-method home offices.
    * No home-office block → empty list from dispatch.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.models import (
    CanonicalReturn,
    HomeOffice,
    ScheduleC,
)
from skill.scripts.output.form_8829 import (
    compute_form_8829_fields,
    render_form_8829_pdf,
    render_form_8829_pdfs_all,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_FORM_8829_PDF = _REPO_ROOT / "skill" / "reference" / "irs_forms" / "f8829.pdf"
_FORM_8829_MAP = _REPO_ROOT / "skill" / "reference" / "form-8829-acroform-map.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_sch_c(**overrides) -> ScheduleC:
    base = {
        "business_name": "Test Co",
        "principal_business_or_profession": "Widgets",
    }
    base.update(overrides)
    return ScheduleC.model_validate(base)


def _canonical_with_schedules(scs: list[ScheduleC]) -> CanonicalReturn:
    return CanonicalReturn.model_validate(
        {
            "schema_version": "0.1.0",
            "tax_year": 2025,
            "filing_status": "single",
            "taxpayer": {
                "first_name": "Harriet",
                "last_name": "Hom",
                "ssn": "111-22-3333",
                "date_of_birth": "1985-01-01",
                "is_blind": False,
                "is_age_65_or_older": False,
            },
            "address": {
                "street1": "1 Main",
                "city": "Austin",
                "state": "TX",
                "zip": "78701",
            },
            "schedules_c": [sc.model_dump(mode="json") for sc in scs],
            "itemize_deductions": False,
        }
    )


def _read_widget_value(pdf_path: Path, substring: str) -> str | None:
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(pdf_path))
    fields = reader.get_fields() or {}
    for key, val in fields.items():
        if substring in key:
            return val.get("/V")
    return None


# ---------------------------------------------------------------------------
# Bundled asset + widget map sanity
# ---------------------------------------------------------------------------


def test_form_8829_pdf_is_bundled() -> None:
    assert _FORM_8829_PDF.exists(), (
        f"f8829.pdf must be checked in at {_FORM_8829_PDF}"
    )
    assert _FORM_8829_PDF.stat().st_size > 50_000  # ~82 KB canonical


def test_form_8829_widget_map_matches_bundled_pdf_sha() -> None:
    map_data = json.loads(_FORM_8829_MAP.read_text())
    actual = hashlib.sha256(_FORM_8829_PDF.read_bytes()).hexdigest()
    assert actual == map_data["source_pdf_sha256"]


def test_form_8829_widget_map_contains_all_widgets_present_in_pdf() -> None:
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(_FORM_8829_PDF))

    actual_names: set[str] = set()
    for page in reader.pages:
        annots = page.get("/Annots") or []
        for annot_ref in annots:
            annot = annot_ref.get_object()
            if annot.get("/Subtype") != "/Widget":
                continue
            parts: list[str] = []
            node = annot
            seen: set[int] = set()
            while node is not None and id(node) not in seen:
                seen.add(id(node))
                t = node.get("/T")
                if t is not None:
                    parts.append(str(t))
                parent = node.get("/Parent")
                node = parent.get_object() if parent is not None else None
            actual_names.add(".".join(reversed(parts)))

    map_data = json.loads(_FORM_8829_MAP.read_text())
    mapped_names = {entry["widget_name"] for entry in map_data["mapping"].values()}
    # Every widget in the PDF must be represented in the map (wave 6
    # mapped_count == total_widgets == 58).
    assert actual_names == mapped_names


# ---------------------------------------------------------------------------
# Layer 2 — render one filled PDF end-to-end
# ---------------------------------------------------------------------------


def test_render_form_8829_end_to_end_driving_full_form(tmp_path: Path) -> None:
    """Drive the full Form 8829 with realistic inputs and verify the
    PDF round-trips through pypdf with the expected widget values.
    """
    sc = _minimal_sch_c(line1_gross_receipts="150000.00")
    ho = HomeOffice(
        method="regular",
        business_sq_ft=Decimal("300"),
        total_home_sq_ft=Decimal("2000"),
        mortgage_interest_total=Decimal("18000"),
        real_estate_taxes_total=Decimal("6500"),
        utilities_total=Decimal("4800"),
        insurance_total=Decimal("1200"),
        repairs_total=Decimal("500"),
        home_purchase_price=Decimal("450000"),
        home_land_value=Decimal("60000"),
        # Steady-state (no purchase date → 2.564%)
    )
    fields = compute_form_8829_fields(ho, sc)
    out = tmp_path / "test_8829.pdf"
    rv = render_form_8829_pdf(fields, out)
    assert rv == out
    assert out.exists()
    assert out.stat().st_size > 50_000

    # The rendered widgets round-trip via pypdf.
    # f1_09 = line 7 business percentage (15.00)
    assert _read_widget_value(out, "f1_09") == "15.00"
    # f1_50 = line 36 final deductible amount
    line_36 = fields.line_36_allowable_expenses_to_sch_c_line_30
    assert _read_widget_value(out, "f1_50") == f"{line_36:.2f}"
    # f1_56 = line 42 depreciation
    line_42 = fields.line_42_depreciation_allowable
    assert _read_widget_value(out, "f1_56") == f"{line_42:.2f}"
    # f1_54 = line 40 business basis of building
    line_40 = fields.line_40_business_basis_of_building
    assert _read_widget_value(out, "f1_54") == f"{line_40:.2f}"


def test_render_form_8829_zero_cells_render_as_empty(tmp_path: Path) -> None:
    """Zero values must collapse to empty strings (matches wave-5
    convention — no $0.00 clutter on blank lines)."""
    sc = _minimal_sch_c(line1_gross_receipts="100000.00")
    ho = HomeOffice(
        method="regular",
        business_sq_ft=Decimal("200"),
        total_home_sq_ft=Decimal("2000"),
        utilities_total=Decimal("4000"),
    )
    fields = compute_form_8829_fields(ho, sc)
    out = tmp_path / "test_8829_zero.pdf"
    render_form_8829_pdf(fields, out)
    # No casualty (line 9a) → blank
    assert _read_widget_value(out, "f1_11") in (None, "")
    # No direct rent (line 19a) → blank
    assert _read_widget_value(out, "f1_28") in (None, "")
    # No depreciation (no purchase price) → line 42 blank
    assert _read_widget_value(out, "f1_56") in (None, "")


def test_render_form_8829_writes_area_as_integer(tmp_path: Path) -> None:
    """Square footage renders as an integer without a trailing .00."""
    sc = _minimal_sch_c(line1_gross_receipts="100000.00")
    ho = HomeOffice(
        method="regular",
        business_sq_ft=Decimal("250"),
        total_home_sq_ft=Decimal("2000"),
    )
    fields = compute_form_8829_fields(ho, sc)
    out = tmp_path / "test_8829_area.pdf"
    render_form_8829_pdf(fields, out)
    # line 1 = 250, line 2 = 2000 — no decimals in sq-ft cells
    assert _read_widget_value(out, "f1_03") == "250"
    assert _read_widget_value(out, "f1_04") == "2000"


# ---------------------------------------------------------------------------
# Layer 2 — multi-business dispatch
# ---------------------------------------------------------------------------


def test_dispatch_all_skips_simplified_and_emits_regular_only(
    tmp_path: Path,
) -> None:
    sc1 = _minimal_sch_c(
        business_name="First Biz",
        line1_gross_receipts="50000.00",
    )
    sc1.home_office = HomeOffice(
        method="simplified",
        business_sq_ft=Decimal("200"),
        total_home_sq_ft=Decimal("2000"),
    )
    sc2 = _minimal_sch_c(
        business_name="Second Biz",
        line1_gross_receipts="80000.00",
    )
    sc2.home_office = HomeOffice(
        method="regular",
        business_sq_ft=Decimal("300"),
        total_home_sq_ft=Decimal("2000"),
        utilities_total=Decimal("3000"),
    )
    return_ = _canonical_with_schedules([sc1, sc2])

    out_dir = tmp_path / "forms"
    written = render_form_8829_pdfs_all(return_, out_dir)

    # Only the regular-method home office emits a Form 8829.
    assert len(written) == 1
    assert written[0].name == "form_8829_01_second_biz.pdf"
    assert written[0].exists()


def test_dispatch_all_no_home_office_returns_empty(tmp_path: Path) -> None:
    sc = _minimal_sch_c(line1_gross_receipts="50000.00")
    # sc.home_office is None
    return_ = _canonical_with_schedules([sc])
    assert render_form_8829_pdfs_all(return_, tmp_path) == []


def test_dispatch_all_no_schedules_c_returns_empty(tmp_path: Path) -> None:
    return_ = CanonicalReturn.model_validate(
        {
            "schema_version": "0.1.0",
            "tax_year": 2025,
            "filing_status": "single",
            "taxpayer": {
                "first_name": "Harriet",
                "last_name": "Hom",
                "ssn": "111-22-3333",
                "date_of_birth": "1985-01-01",
                "is_blind": False,
                "is_age_65_or_older": False,
            },
            "address": {
                "street1": "1 Main",
                "city": "Austin",
                "state": "TX",
                "zip": "78701",
            },
            "itemize_deductions": False,
        }
    )
    assert render_form_8829_pdfs_all(return_, tmp_path) == []
