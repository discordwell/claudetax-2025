"""Tests for skill.scripts.output.form_1040 — Form 1040 PDF renderer.

Two layers under test:

* Layer 1 — ``compute_form_1040_fields``: assert that values on the
  returned dataclass match the expected Form 1040 line numbers for each
  of the three golden fixtures we already ship. These tests do NOT
  duplicate the engine's golden diff — they only check that the engine's
  ComputedTotals are correctly routed onto Form 1040 line names.

* Layer 2 — ``render_form_1040_pdf``: overlay the Layer-1 dataclass
  values onto the IRS fillable f1040.pdf via the wave-4 widget map and
  the shared :mod:`_acroform_overlay` helper. Tests reopen the filled
  PDF with ``pypdf.PdfReader`` and assert the canonical line widgets
  carry the expected values, the filing-status checkbox is toggled,
  and the original 199-widget AcroForm structure is preserved.

Also: a small sanity test that refund / owed are mutually exclusive on
the rendered fields.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.calc.engine import compute
from skill.scripts.models import CanonicalReturn
from skill.scripts.output.form_1040 import (
    Form1040Fields,
    _build_widget_values_for_form1040,
    compute_form_1040_fields,
    render_form_1040_pdf,
)
from skill.scripts.output._acroform_overlay import load_widget_map


# Canonical fully-qualified widget names from the wave-4 map. Tests
# assert against these directly so a regression in the renderer
# (filling the wrong widget) shows up immediately.
W_LINE_1Z = "topmostSubform[0].Page1[0].f1_57[0]"
W_LINE_11_PG1 = "topmostSubform[0].Page1[0].f1_75[0]"
W_LINE_11_PG2 = "topmostSubform[0].Page2[0].f2_01[0]"
W_LINE_16_TAX = "topmostSubform[0].Page2[0].f2_08[0]"
W_LINE_24_TOTAL_TAX = "topmostSubform[0].Page2[0].f2_16[0]"
W_LINE_25A_W2_WH = "topmostSubform[0].Page2[0].f2_17[0]"
W_LINE_34_OVERPAYMENT = "topmostSubform[0].Page2[0].f2_30[0]"
W_LINE_37_OWED = "topmostSubform[0].Page2[0].f2_35[0]"
W_TAXPAYER_NAME = "topmostSubform[0].Page1[0].f1_01[0]"
W_FS_SINGLE = "topmostSubform[0].Page1[0].Checkbox_ReadOrder[0].c1_8[0]"
W_FS_MFJ = "topmostSubform[0].Page1[0].Checkbox_ReadOrder[0].c1_8[1]"
W_FS_HOH = "topmostSubform[0].Page1[0].c1_8[0]"


def _load_fixture(fixtures_dir: Path, name: str) -> CanonicalReturn:
    data = json.loads((fixtures_dir / name / "input.json").read_text())
    return CanonicalReturn.model_validate(data)


# ---------------------------------------------------------------------------
# Layer 1: field mapping
# ---------------------------------------------------------------------------


def test_simple_w2_standard_field_mapping(fixtures_dir: Path) -> None:
    return_ = _load_fixture(fixtures_dir, "simple_w2_standard")
    computed = compute(return_)
    fields = compute_form_1040_fields(computed)

    assert isinstance(fields, Form1040Fields)
    assert fields.filing_status == "single"
    assert fields.taxpayer_name == "Alex Doe"
    assert fields.spouse_name is None

    # Wages
    assert fields.line_1a_total_w2_box1 == Decimal("65000.00")
    assert fields.line_1z_total_wages == Decimal("65000.00")

    # Totals from ComputedTotals
    assert fields.line_9_total_income == Decimal("65000.00")
    assert fields.line_11_adjusted_gross_income == Decimal("65000.00")
    assert fields.line_12_standard_or_itemized_deduction == Decimal("15750.00")
    assert fields.line_15_taxable_income == Decimal("49250.00")
    assert fields.line_16_tax == Decimal("5755.00")
    assert fields.line_24_total_tax == Decimal("5755.00")

    # Withholding
    assert isinstance(fields.line_25a_w2_withholding, Decimal)
    assert fields.line_25a_w2_withholding > Decimal("0")
    assert fields.line_25a_w2_withholding == Decimal("7500.00")

    # Exactly one of refund/owed is populated
    assert (fields.line_34_overpayment > 0) or (fields.line_37_amount_you_owe > 0)
    assert fields.line_34_overpayment == Decimal("1745.00")


def test_w2_investments_itemized_field_mapping(fixtures_dir: Path) -> None:
    return_ = _load_fixture(fixtures_dir, "w2_investments_itemized")
    computed = compute(return_)
    fields = compute_form_1040_fields(computed)

    # Wages: two W-2s (150k + 50k)
    assert fields.line_1z_total_wages == Decimal("200000.00")
    # Interest
    assert fields.line_2b_taxable_interest == Decimal("3000.00")
    # Ordinary dividends / qualified
    assert fields.line_3a_qualified_dividends == Decimal("3000.00")
    assert fields.line_3b_ordinary_dividends == Decimal("5000.00")
    # Long-term cap gain (20000 - 10000) + 500 cap gain distr = 10500
    assert fields.line_7_capital_gain_or_loss == Decimal("10500.00")
    # Itemized deduction: SALT-capped at 10k + mortgage 20k + charity 5k = 35000
    assert fields.line_12_standard_or_itemized_deduction == Decimal("35000.00")
    # W-2 withholding: 18000 + 5000 = 23000
    assert fields.line_25a_w2_withholding == Decimal("23000.00")
    # Spouse populated
    assert fields.spouse_name == "Pat Smith"
    assert fields.filing_status == "mfj"


def test_se_home_office_field_mapping(fixtures_dir: Path) -> None:
    return_ = _load_fixture(fixtures_dir, "se_home_office")
    computed = compute(return_)
    fields = compute_form_1040_fields(computed)

    # No W-2 wages
    assert fields.line_1a_total_w2_box1 == Decimal("0")
    assert fields.line_1z_total_wages == Decimal("0")
    # line_8 is Schedule 1 Part I additional income — no unemployment, so 0.
    # Schedule C net profit flows via engine.compute() into total_income.
    assert fields.line_8_additional_income_from_sch_1 == Decimal("0")
    # line_9 total_income comes from ComputedTotals; Sch C net should be
    # positive and non-zero (120k gross - 27k expenses - 3k home office = 90k).
    assert fields.line_9_total_income == Decimal("90000.00")
    # Engine populates line 11 (AGI) from tenforty — it subtracts 1/2 SE tax.
    assert fields.line_11_adjusted_gross_income < fields.line_9_total_income
    # Total tax is non-zero because of SE tax on the 90k schedule C net.
    assert fields.line_24_total_tax > Decimal("0")
    # Estimated payments fixture has $10k
    assert fields.line_26_estimated_and_prior_year_applied == Decimal("10000.00")


# ---------------------------------------------------------------------------
# Layer 2: pypdf AcroForm overlay onto the IRS fillable f1040.pdf
# ---------------------------------------------------------------------------


def test_render_produces_non_empty_pdf(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """The rendered file exists, is non-empty, and is a valid 2-page PDF."""
    pypdf = pytest.importorskip("pypdf")

    return_ = _load_fixture(fixtures_dir, "simple_w2_standard")
    computed = compute(return_)
    fields = compute_form_1040_fields(computed)

    out_path = tmp_path / "test_1040.pdf"
    result_path = render_form_1040_pdf(fields, out_path)

    assert result_path == out_path
    assert out_path.exists()
    # The IRS source PDF is ~220 KB; the filled copy is larger.
    assert out_path.stat().st_size > 100_000

    reader = pypdf.PdfReader(str(out_path))
    # The IRS Form 1040 has exactly two pages.
    assert len(reader.pages) == 2
    # The IRS form's static label text is preserved.
    text = "".join(page.extract_text() or "" for page in reader.pages)
    assert "Form" in text and "1040" in text


def test_render_round_trips_canonical_line_fields(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """At least 5 canonical line fields write to and read back from
    their pinned widget names. Guards against widget-map drift."""
    pypdf = pytest.importorskip("pypdf")

    return_ = _load_fixture(fixtures_dir, "simple_w2_standard")
    computed = compute(return_)
    fields = compute_form_1040_fields(computed)

    out_path = tmp_path / "test_1040.pdf"
    render_form_1040_pdf(fields, out_path)

    reader = pypdf.PdfReader(str(out_path))
    gf = reader.get_fields()

    # 1. Wages
    assert gf[W_LINE_1Z]["/V"] == "65000.00"
    # 2. AGI page 1
    assert gf[W_LINE_11_PG1]["/V"] == "65000.00"
    # 3. AGI page 2 (computed_copies mirror)
    assert gf[W_LINE_11_PG2]["/V"] == "65000.00"
    # 4. Tax (line 16)
    assert gf[W_LINE_16_TAX]["/V"] == "5755.00"
    # 5. Total tax (line 24)
    assert gf[W_LINE_24_TOTAL_TAX]["/V"] == "5755.00"
    # 6. W-2 withholding (line 25a)
    assert gf[W_LINE_25A_W2_WH]["/V"] == "7500.00"
    # 7. Overpayment (line 34) — refund case
    assert gf[W_LINE_34_OVERPAYMENT]["/V"] == "1745.00"
    # 8. Amount owed (line 37) — empty for refund case (zero collapse)
    assert gf[W_LINE_37_OWED].get("/V") in (None, "")


def test_render_preserves_full_widget_count(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """The filled copy still has 199 terminal widgets (the wave-4 map's
    pinned count)."""
    pypdf = pytest.importorskip("pypdf")

    return_ = _load_fixture(fixtures_dir, "simple_w2_standard")
    computed = compute(return_)
    fields = compute_form_1040_fields(computed)
    out_path = tmp_path / "test_1040.pdf"
    render_form_1040_pdf(fields, out_path)

    reader = pypdf.PdfReader(str(out_path))
    # Walk page annots to count terminal widgets (matches the wave-4
    # methodology, which is the source of truth for the widget map).
    n = 0
    for page in reader.pages:
        for annot_ref in (page.get("/Annots") or []):
            annot = annot_ref.get_object()
            if annot.get("/Subtype") == "/Widget":
                n += 1
    assert n == 199


def test_render_writes_taxpayer_name(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    pypdf = pytest.importorskip("pypdf")

    return_ = _load_fixture(fixtures_dir, "simple_w2_standard")
    computed = compute(return_)
    fields = compute_form_1040_fields(computed)
    out_path = tmp_path / "test_1040.pdf"
    render_form_1040_pdf(fields, out_path)

    reader = pypdf.PdfReader(str(out_path))
    gf = reader.get_fields()
    assert gf[W_TAXPAYER_NAME]["/V"] == "Alex Doe"


def test_render_toggles_single_filing_status_checkbox(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    pypdf = pytest.importorskip("pypdf")

    return_ = _load_fixture(fixtures_dir, "simple_w2_standard")
    computed = compute(return_)
    fields = compute_form_1040_fields(computed)
    assert fields.filing_status == "single"

    out_path = tmp_path / "test_1040.pdf"
    render_form_1040_pdf(fields, out_path)

    reader = pypdf.PdfReader(str(out_path))
    gf = reader.get_fields()
    # SINGLE is on, MFJ/HOH are off (their on-states use distinct
    # appearance names per the IRS f1040 — /1, /2, /3, /4, /5).
    assert str(gf[W_FS_SINGLE].get("/V")) != "/Off"
    assert str(gf[W_FS_MFJ].get("/V")) == "/Off"
    assert str(gf[W_FS_HOH].get("/V")) == "/Off"


def test_render_toggles_mfj_filing_status_checkbox(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """w2_investments_itemized fixture is filing_status=mfj — verify
    the MFJ checkbox is checked instead of SINGLE."""
    pypdf = pytest.importorskip("pypdf")

    return_ = _load_fixture(fixtures_dir, "w2_investments_itemized")
    computed = compute(return_)
    fields = compute_form_1040_fields(computed)
    assert fields.filing_status == "mfj"

    out_path = tmp_path / "test_1040.pdf"
    render_form_1040_pdf(fields, out_path)

    reader = pypdf.PdfReader(str(out_path))
    gf = reader.get_fields()
    assert str(gf[W_FS_SINGLE].get("/V")) == "/Off"
    assert str(gf[W_FS_MFJ].get("/V")) != "/Off"
    assert str(gf[W_FS_HOH].get("/V")) == "/Off"


def test_render_zero_values_collapse_to_empty(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """Lines with zero amounts must NOT be written as ``0.00``; they
    should leave the widget blank so the form does not get cluttered."""
    pypdf = pytest.importorskip("pypdf")

    return_ = _load_fixture(fixtures_dir, "simple_w2_standard")
    computed = compute(return_)
    fields = compute_form_1040_fields(computed)
    out_path = tmp_path / "test_1040.pdf"
    render_form_1040_pdf(fields, out_path)

    reader = pypdf.PdfReader(str(out_path))
    gf = reader.get_fields()
    # Line 2b interest is 0 in this fixture; widget must not carry "0.00".
    line_2b_widget = "topmostSubform[0].Page1[0].f1_59[0]"
    assert gf[line_2b_widget].get("/V") in (None, "")
    # Line 37 amount-you-owe is 0 in this fixture (refund case).
    assert gf[W_LINE_37_OWED].get("/V") in (None, "")


def test_build_widget_values_helper_includes_filing_status_booleans(
    fixtures_dir: Path,
) -> None:
    """White-box check on the per-form translator: filing-status
    booleans land in widget_values as True/False (the helper resolves
    them to /1.../5 at fill time)."""
    return_ = _load_fixture(fixtures_dir, "simple_w2_standard")
    computed = compute(return_)
    fields = compute_form_1040_fields(computed)

    reference_dir = Path(__file__).resolve().parents[1] / "reference"
    wm = load_widget_map(reference_dir / "form-1040-acroform-map.json")
    values = _build_widget_values_for_form1040(fields, wm)

    # Money fields are strings.
    assert values[W_LINE_1Z] == "65000.00"
    # Filing-status booleans are bools (resolved to /1.../5 by fill_acroform_pdf).
    assert values[W_FS_SINGLE] is True
    assert values[W_FS_MFJ] is False
    assert values[W_FS_HOH] is False
    # AGI computed-copy mirrors are populated.
    assert values[W_LINE_11_PG1] == "65000.00"
    assert values[W_LINE_11_PG2] == "65000.00"


def test_render_signature_unchanged(fixtures_dir: Path, tmp_path: Path) -> None:
    """``render_form_1040_pdf(fields, out_path) -> Path`` is the
    pipeline contract — keep the signature stable."""
    return_ = _load_fixture(fixtures_dir, "simple_w2_standard")
    computed = compute(return_)
    fields = compute_form_1040_fields(computed)

    out_path = tmp_path / "test_1040.pdf"
    result = render_form_1040_pdf(fields, out_path)
    assert isinstance(result, Path)
    assert result == out_path


# ---------------------------------------------------------------------------
# Refund vs owed: mutually exclusive
# ---------------------------------------------------------------------------


def _canonical_with_w2(wages: str, withheld: str) -> CanonicalReturn:
    """Small-return helper for the refund/owed sanity test."""
    return CanonicalReturn.model_validate(
        {
            "schema_version": "0.1.0",
            "tax_year": 2025,
            "filing_status": "single",
            "taxpayer": {
                "first_name": "Refund",
                "last_name": "Case",
                "ssn": "111-22-3333",
                "date_of_birth": "1990-01-01",
                "is_blind": False,
                "is_age_65_or_older": False,
            },
            "address": {
                "street1": "1 Test",
                "city": "Springfield",
                "state": "IL",
                "zip": "62701",
            },
            "w2s": [
                {
                    "employer_name": "Acme",
                    "box1_wages": wages,
                    "box2_federal_income_tax_withheld": withheld,
                }
            ],
            "itemize_deductions": False,
        }
    )


def test_refund_vs_owed_mutually_exclusive() -> None:
    # High withholding -> refund
    refund_return = compute(_canonical_with_w2("65000.00", "10000.00"))
    refund_fields = compute_form_1040_fields(refund_return)
    assert refund_fields.line_34_overpayment > Decimal("0")
    assert refund_fields.line_35a_refund_requested > Decimal("0")
    assert refund_fields.line_37_amount_you_owe == Decimal("0")

    # Zero withholding -> owed
    owed_return = compute(_canonical_with_w2("65000.00", "0"))
    owed_fields = compute_form_1040_fields(owed_return)
    assert owed_fields.line_37_amount_you_owe > Decimal("0")
    assert owed_fields.line_34_overpayment == Decimal("0")
    assert owed_fields.line_35a_refund_requested == Decimal("0")


def test_render_amount_owed_case() -> None:
    """An owed-case fixture writes line 37 (not line 34) and the refund
    widget is left blank."""
    pypdf = pytest.importorskip("pypdf")
    import tempfile

    owed_return = compute(_canonical_with_w2("65000.00", "0"))
    owed_fields = compute_form_1040_fields(owed_return)
    assert owed_fields.line_37_amount_you_owe > Decimal("0")

    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / "owed.pdf"
        render_form_1040_pdf(owed_fields, out_path)
        reader = pypdf.PdfReader(str(out_path))
        gf = reader.get_fields()
        # Line 34 overpayment widget is empty.
        assert gf[W_LINE_34_OVERPAYMENT].get("/V") in (None, "")
        # Line 37 amount-you-owe widget carries the computed amount.
        owed_str = gf[W_LINE_37_OWED]["/V"]
        assert owed_str
        assert Decimal(owed_str) == owed_fields.line_37_amount_you_owe.quantize(
            Decimal("0.01")
        )


# ---------------------------------------------------------------------------
# End-to-end: pipeline.run_pipeline → real renderer round-trip
# ---------------------------------------------------------------------------


def test_pipeline_end_to_end_uses_real_acroform_renderer(
    tmp_path: Path,
) -> None:
    """The pipeline emits a form_1040.pdf whose canonical line widgets
    round-trip the W-2 wages — proving the pipeline picks up the new
    renderer (not the old reportlab scaffold)."""
    pypdf = pytest.importorskip("pypdf")
    from reportlab.pdfgen import canvas

    from skill.scripts.pipeline import run_pipeline

    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    # Synthetic fillable W-2 PDF (matches the wave-1 W2_FIELD_MAP).
    w2_path = input_dir / "w2_acme.pdf"
    c = canvas.Canvas(str(w2_path))
    form = c.acroForm
    fields = {
        "employer_name": "Acme Corp",
        "employer_ein": "12-3456789",
        "wages_box1": "65000.00",
        "fed_withholding_box2": "7500.00",
        "ss_wages_box3": "65000.00",
        "ss_tax_box4": "4030.00",
        "medicare_wages_box5": "65000.00",
        "medicare_tax_box6": "942.50",
    }
    y = 700
    for name in fields:
        c.drawString(50, y + 20, name)
        form.textfield(
            name=name, x=200, y=y, width=200, height=18, borderStyle="solid"
        )
        y -= 40
    c.save()
    reader_w2 = pypdf.PdfReader(str(w2_path))
    writer_w2 = pypdf.PdfWriter(clone_from=reader_w2)
    writer_w2.update_page_form_field_values(
        writer_w2.pages[0], fields, auto_regenerate=True
    )
    with w2_path.open("wb") as fh:
        writer_w2.write(fh)

    # Header info
    taxpayer_info_path = tmp_path / "taxpayer_info.json"
    taxpayer_info_path.write_text(
        json.dumps(
            {
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
        )
    )

    result = run_pipeline(
        input_dir=input_dir,
        taxpayer_info_path=taxpayer_info_path,
        output_dir=output_dir,
    )
    form_1040_pdf = output_dir / "form_1040.pdf"
    assert form_1040_pdf.exists()
    # The IRS f1040 source PDF is ~220 KB; the filled copy is larger.
    assert form_1040_pdf.stat().st_size > 100_000

    # Open the emitted PDF and assert wages round-trip.
    reader_out = pypdf.PdfReader(str(form_1040_pdf))
    gf = reader_out.get_fields()
    assert gf[W_LINE_1Z]["/V"] == "65000.00"
    assert gf[W_LINE_11_PG1]["/V"] == "65000.00"
    assert gf[W_LINE_11_PG2]["/V"] == "65000.00"
    assert gf[W_LINE_25A_W2_WH]["/V"] == "7500.00"
    # 199-widget structure preserved (not a 1-page reportlab scaffold).
    n = 0
    for page in reader_out.pages:
        for annot_ref in (page.get("/Annots") or []):
            annot = annot_ref.get_object()
            if annot.get("/Subtype") == "/Widget":
                n += 1
    assert n == 199
