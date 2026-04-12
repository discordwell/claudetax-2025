"""Tests for the Form 1095-A pypdf AcroForm ingester.

Form 1095-A (Health Insurance Marketplace Statement) reports monthly
enrollment premium, SLCSP premium, and advance PTC amounts for individuals
enrolled in qualified health plans through the Marketplace.  These values
feed Form 8962 (Premium Tax Credit) reconciliation.

The ingester supports BOTH:
- Synthetic field names (for unit-test fixtures)
- Real IRS AcroForm widget names (from https://www.irs.gov/pub/irs-pdf/f1095a.pdf)

These tests exercise the synthetic map against a reportlab-generated fillable
PDF fixture to prove the path-rewriting wiring works end-to-end.

Part III layout (12 monthly rows + 1 annual row):
  Row 21 (Jan) through Row 32 (Dec), each with 3 columns:
    Column A: Monthly enrollment premium
    Column B: Monthly SLCSP premium
    Column C: Monthly advance payment of PTC (APTC)

Form1095A model fields mapped:
- marketplace_id          (Part I, line 2)
- policy_start_date       (Part I, line 11)
- policy_end_date         (Part I, line 12)
- monthly_data[0..11]     (Part III, rows 21-32):
    - enrollment_premium  (column A)
    - slcsp_premium       (column B)
    - advance_ptc         (column C)
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pypdf
import pytest
from reportlab.pdfgen import canvas

from skill.scripts.ingest._classifier import classify_by_filename, classify_by_text
from skill.scripts.ingest._pipeline import DocumentKind, Ingester
from skill.scripts.ingest._1095_a_acroform import (
    FORM_1095_A_FIELD_MAP,
    INGESTER,
)


# ---------------------------------------------------------------------------
# Synthetic fillable PDF helper
# ---------------------------------------------------------------------------


def _make_acroform_pdf(path: Path, fields: dict[str, str]) -> None:
    """Write a minimal AcroForm PDF with the given text fields and values.

    Uses reportlab to draw the text fields (which registers them in the
    /AcroForm dict), then uses pypdf's clone_from to copy the whole document
    catalog (including /AcroForm) into a writer so we can set widget values.

    Fields may spill across multiple pages; we call
    update_page_form_field_values on each page so that every widget gets
    its value regardless of which page it landed on.
    """
    c = canvas.Canvas(str(path))
    form = c.acroForm
    y = 720
    for name, _value in fields.items():
        c.drawString(50, y + 20, name)
        form.textfield(
            name=name,
            x=230,
            y=y,
            width=220,
            height=14,
            borderStyle="solid",
        )
        y -= 28
        if y < 50:
            c.showPage()
            y = 720
    c.save()

    reader = pypdf.PdfReader(str(path))
    writer = pypdf.PdfWriter(clone_from=reader)
    for page in writer.pages:
        writer.update_page_form_field_values(
            page, fields, auto_regenerate=True
        )
    with path.open("wb") as fh:
        writer.write(fh)


# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

# Full-year coverage: all 12 months populated
_FULL_FIELDS: dict[str, str] = {
    "marketplace_id": "12345678901234",
    "policy_start_date": "01/01/2025",
    "policy_end_date": "12/31/2025",
}

# Add monthly data for all 12 months
_MONTHLY_PREMIUMS = [
    ("450.00", "500.00", "200.00"),  # Jan
    ("450.00", "500.00", "200.00"),  # Feb
    ("450.00", "500.00", "200.00"),  # Mar
    ("475.00", "510.00", "210.00"),  # Apr
    ("475.00", "510.00", "210.00"),  # May
    ("475.00", "510.00", "210.00"),  # Jun
    ("490.00", "520.00", "215.00"),  # Jul
    ("490.00", "520.00", "215.00"),  # Aug
    ("490.00", "520.00", "215.00"),  # Sep
    ("500.00", "530.00", "220.00"),  # Oct
    ("500.00", "530.00", "220.00"),  # Nov
    ("500.00", "530.00", "220.00"),  # Dec
]

for _i, (_enroll, _slcsp, _aptc) in enumerate(_MONTHLY_PREMIUMS):
    _FULL_FIELDS[f"monthly_data_{_i}_enrollment_premium"] = _enroll
    _FULL_FIELDS[f"monthly_data_{_i}_slcsp_premium"] = _slcsp
    _FULL_FIELDS[f"monthly_data_{_i}_advance_ptc"] = _aptc


# Partial-year coverage: only Jan-Jun populated
_PARTIAL_FIELDS: dict[str, str] = {
    "marketplace_id": "98765432109876",
    "policy_start_date": "01/01/2025",
    "policy_end_date": "06/30/2025",
}

for _i in range(6):
    _PARTIAL_FIELDS[f"monthly_data_{_i}_enrollment_premium"] = "400.00"
    _PARTIAL_FIELDS[f"monthly_data_{_i}_slcsp_premium"] = "480.00"
    _PARTIAL_FIELDS[f"monthly_data_{_i}_advance_ptc"] = "180.00"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_1095_a_pdf(tmp_path) -> Path:
    """Full-year 1095-A fixture with all 12 months populated."""
    p = tmp_path / "1095-A_marketplace.pdf"
    _make_acroform_pdf(p, _FULL_FIELDS)
    return p


@pytest.fixture
def partial_year_1095_a_pdf(tmp_path) -> Path:
    """Partial-year 1095-A: only Jan-Jun covered."""
    p = tmp_path / "f1095a_partial.pdf"
    _make_acroform_pdf(p, _PARTIAL_FIELDS)
    return p


@pytest.fixture
def minimal_1095_a_pdf(tmp_path) -> Path:
    """Minimal 1095-A with just marketplace_id and one month."""
    p = tmp_path / "1095-A_minimal.pdf"
    _make_acroform_pdf(
        p,
        {
            "marketplace_id": "11111111111111",
            "monthly_data_0_enrollment_premium": "350.00",
            "monthly_data_0_slcsp_premium": "400.00",
            "monthly_data_0_advance_ptc": "150.00",
        },
    )
    return p


# ---------------------------------------------------------------------------
# Tests: Ingester contract
# ---------------------------------------------------------------------------


class TestIngesterContract:
    def test_satisfies_ingester_protocol(self):
        assert isinstance(INGESTER, Ingester)

    def test_name_and_tier(self):
        assert INGESTER.name == "1095_a_acroform"
        assert INGESTER.tier == 1

    def test_field_map_registered_for_1095_a(self):
        assert DocumentKind.FORM_1095_A in INGESTER.field_map
        mapping = INGESTER.field_map[DocumentKind.FORM_1095_A]
        assert mapping["marketplace_id"] == "forms_1095_a[0].marketplace_id"

    def test_field_map_covers_header_fields(self):
        required = {"marketplace_id", "policy_start_date", "policy_end_date"}
        assert required.issubset(set(FORM_1095_A_FIELD_MAP.keys()))

    def test_field_map_covers_all_12_months(self):
        """Every month (0-11) must have enrollment, slcsp, and aptc entries."""
        for month_idx in range(12):
            for col in ("enrollment_premium", "slcsp_premium", "advance_ptc"):
                key = f"monthly_data_{month_idx}_{col}"
                assert key in FORM_1095_A_FIELD_MAP, (
                    f"missing synthetic key: {key}"
                )

    def test_all_map_targets_under_forms_1095_a(self):
        for canonical in FORM_1095_A_FIELD_MAP.values():
            assert canonical.startswith("forms_1095_a[0]."), (
                f"unexpected target prefix: {canonical}"
            )

    def test_map_covers_model_fields_via_introspection(self):
        """Introspect Form1095A and Form1095AMonthly to verify coverage."""
        from skill.scripts.models import Form1095A, Form1095AMonthly

        # Header fields on Form1095A
        header_fields = {"marketplace_id", "policy_start_date", "policy_end_date"}
        mapped_headers = {
            canonical.removeprefix("forms_1095_a[0].")
            for canonical in FORM_1095_A_FIELD_MAP.values()
            if "monthly_data" not in canonical
        }
        missing_headers = header_fields - mapped_headers
        assert not missing_headers, f"unmapped header fields: {missing_headers}"

        # Monthly fields on Form1095AMonthly
        monthly_model_fields = set(Form1095AMonthly.model_fields.keys())
        # Check month 0 as representative
        mapped_monthly_fields = set()
        for canonical in FORM_1095_A_FIELD_MAP.values():
            if "monthly_data[0]." in canonical:
                leaf = canonical.split(".")[-1]
                mapped_monthly_fields.add(leaf)
        missing_monthly = monthly_model_fields - mapped_monthly_fields
        assert not missing_monthly, (
            f"unmapped monthly fields: {missing_monthly}"
        )

    def test_real_irs_widget_names_present(self):
        """The real IRS widget names from f1095a.pdf must be in the map."""
        # Spot-check a few real widget names
        assert (
            "topmostSubform[0].Page1[0].f1_2[0]" in FORM_1095_A_FIELD_MAP
        )
        assert (
            "topmostSubform[0].Page1[0].Table_PartIII[0].Row21[0].f1_41[0]"
            in FORM_1095_A_FIELD_MAP
        )
        assert (
            "topmostSubform[0].Page1[0].Table_PartIII[0].Row32[0].f1_74[0]"
            in FORM_1095_A_FIELD_MAP
        )

    def test_synthetic_and_real_map_to_same_canonical_paths(self):
        """Synthetic key and real IRS widget for the same field must map
        to the same canonical path."""
        m = FORM_1095_A_FIELD_MAP
        # marketplace_id
        assert (
            m["marketplace_id"]
            == m["topmostSubform[0].Page1[0].f1_2[0]"]
        )
        # Jan enrollment premium
        assert (
            m["monthly_data_0_enrollment_premium"]
            == m[
                "topmostSubform[0].Page1[0].Table_PartIII[0].Row21[0].f1_41[0]"
            ]
        )
        # Dec advance_ptc
        assert (
            m["monthly_data_11_advance_ptc"]
            == m[
                "topmostSubform[0].Page1[0].Table_PartIII[0].Row32[0].f1_76[0]"
            ]
        )


# ---------------------------------------------------------------------------
# Tests: Classifier routing
# ---------------------------------------------------------------------------


class TestClassifierRouting:
    def test_classifier_routes_1095_a_filename(self, tmp_path):
        p = tmp_path / "1095-A_marketplace.pdf"
        p.write_bytes(b"%PDF-1.4\n")
        assert classify_by_filename(p) == DocumentKind.FORM_1095_A

    def test_classifier_routes_f1095a_filename(self, tmp_path):
        p = tmp_path / "f1095a.pdf"
        p.write_bytes(b"%PDF-1.4\n")
        assert classify_by_filename(p) == DocumentKind.FORM_1095_A

    def test_classifier_routes_1095a_no_dash_filename(self, tmp_path):
        p = tmp_path / "1095a_client_2025.pdf"
        p.write_bytes(b"%PDF-1.4\n")
        assert classify_by_filename(p) == DocumentKind.FORM_1095_A

    def test_classifier_by_text_marketplace_statement(self):
        text = "Health Insurance Marketplace Statement"
        assert classify_by_text(text) == DocumentKind.FORM_1095_A

    def test_classifier_by_text_form_1095a(self):
        text = "Form 1095-A for Tax Year 2025"
        assert classify_by_text(text) == DocumentKind.FORM_1095_A

    def test_ingester_ingest_sets_document_kind(self, fake_1095_a_pdf):
        result = INGESTER.ingest(fake_1095_a_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1095_A


# ---------------------------------------------------------------------------
# Tests: can_handle
# ---------------------------------------------------------------------------


class TestCanHandle:
    def test_can_handle_fake_1095_a(self, fake_1095_a_pdf):
        assert INGESTER.can_handle(fake_1095_a_pdf)

    def test_cannot_handle_non_pdf(self, tmp_path):
        p = tmp_path / "note.txt"
        p.write_bytes(b"hello")
        assert not INGESTER.can_handle(p)

    def test_cannot_handle_missing_file(self, tmp_path):
        p = tmp_path / "does_not_exist.pdf"
        assert not INGESTER.can_handle(p)


# ---------------------------------------------------------------------------
# Tests: Full-year ingest
# ---------------------------------------------------------------------------


class TestIngestFullYear:
    def test_ingest_succeeds(self, fake_1095_a_pdf):
        result = INGESTER.ingest(fake_1095_a_pdf)
        assert result.success
        assert result.error is None

    def test_document_kind_is_1095_a(self, fake_1095_a_pdf):
        result = INGESTER.ingest(fake_1095_a_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1095_A

    def test_marketplace_id_extracted(self, fake_1095_a_pdf):
        result = INGESTER.ingest(fake_1095_a_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        assert paths.get("forms_1095_a[0].marketplace_id") == "12345678901234"

    def test_policy_dates_extracted(self, fake_1095_a_pdf):
        result = INGESTER.ingest(fake_1095_a_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        assert paths.get("forms_1095_a[0].policy_start_date") == "01/01/2025"
        assert paths.get("forms_1095_a[0].policy_end_date") == "12/31/2025"

    def test_all_12_months_enrollment_premium(self, fake_1095_a_pdf):
        """Every month's enrollment premium must be present and Decimal-parseable."""
        result = INGESTER.ingest(fake_1095_a_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        for month_idx in range(12):
            path = f"forms_1095_a[0].monthly_data[{month_idx}].enrollment_premium"
            raw = paths.get(path)
            assert raw is not None, f"missing enrollment_premium for month {month_idx}"
            parsed = Decimal(raw)
            assert parsed > 0, f"month {month_idx} enrollment_premium should be positive"

    def test_all_12_months_slcsp_premium(self, fake_1095_a_pdf):
        result = INGESTER.ingest(fake_1095_a_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        for month_idx in range(12):
            path = f"forms_1095_a[0].monthly_data[{month_idx}].slcsp_premium"
            raw = paths.get(path)
            assert raw is not None, f"missing slcsp_premium for month {month_idx}"
            parsed = Decimal(raw)
            assert parsed > 0

    def test_all_12_months_advance_ptc(self, fake_1095_a_pdf):
        result = INGESTER.ingest(fake_1095_a_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        for month_idx in range(12):
            path = f"forms_1095_a[0].monthly_data[{month_idx}].advance_ptc"
            raw = paths.get(path)
            assert raw is not None, f"missing advance_ptc for month {month_idx}"
            parsed = Decimal(raw)
            assert parsed >= 0

    def test_round_trip_january_values(self, fake_1095_a_pdf):
        """Verify January (month 0) values match the fixture exactly."""
        result = INGESTER.ingest(fake_1095_a_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        assert paths["forms_1095_a[0].monthly_data[0].enrollment_premium"] == "450.00"
        assert paths["forms_1095_a[0].monthly_data[0].slcsp_premium"] == "500.00"
        assert paths["forms_1095_a[0].monthly_data[0].advance_ptc"] == "200.00"

    def test_round_trip_december_values(self, fake_1095_a_pdf):
        """Verify December (month 11) values match the fixture exactly."""
        result = INGESTER.ingest(fake_1095_a_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        assert paths["forms_1095_a[0].monthly_data[11].enrollment_premium"] == "500.00"
        assert paths["forms_1095_a[0].monthly_data[11].slcsp_premium"] == "530.00"
        assert paths["forms_1095_a[0].monthly_data[11].advance_ptc"] == "220.00"

    def test_no_raw_fallback_paths(self, fake_1095_a_pdf):
        """When every field is mapped, none should drop to _acroform_raw."""
        result = INGESTER.ingest(fake_1095_a_pdf)
        for f in result.partial.fields:
            assert not f.path.startswith("_acroform_raw."), (
                f"unmapped field leaked to raw: {f.path}"
            )

    def test_fields_carry_full_confidence(self, fake_1095_a_pdf):
        result = INGESTER.ingest(fake_1095_a_pdf)
        assert result.partial.fields
        for f in result.partial.fields:
            assert f.confidence == 1.0

    def test_result_is_usable(self, fake_1095_a_pdf):
        result = INGESTER.ingest(fake_1095_a_pdf)
        assert result.is_usable

    def test_field_count_full_year(self, fake_1095_a_pdf):
        """Full-year: 3 header + 12*3 monthly = 39 canonical fields."""
        result = INGESTER.ingest(fake_1095_a_pdf)
        canonical_paths = [
            f.path
            for f in result.partial.fields
            if f.path.startswith("forms_1095_a[0].")
        ]
        # 3 header fields + 36 monthly fields = 39
        assert len(canonical_paths) == 39


# ---------------------------------------------------------------------------
# Tests: Partial-year ingest
# ---------------------------------------------------------------------------


class TestIngestPartialYear:
    def test_partial_year_ingest_succeeds(self, partial_year_1095_a_pdf):
        result = INGESTER.ingest(partial_year_1095_a_pdf)
        assert result.success
        assert result.is_usable

    def test_partial_year_has_6_months(self, partial_year_1095_a_pdf):
        """Only Jan-Jun should be populated; Jul-Dec should be absent."""
        result = INGESTER.ingest(partial_year_1095_a_pdf)
        paths = {f.path: f.value for f in result.partial.fields}

        # Months 0-5 should be present
        for month_idx in range(6):
            path = f"forms_1095_a[0].monthly_data[{month_idx}].enrollment_premium"
            assert path in paths, f"month {month_idx} should be present"

        # Months 6-11 should be absent
        for month_idx in range(6, 12):
            path = f"forms_1095_a[0].monthly_data[{month_idx}].enrollment_premium"
            assert path not in paths, f"month {month_idx} should be absent"

    def test_partial_year_field_count(self, partial_year_1095_a_pdf):
        """Partial year: 3 header + 6*3 monthly = 21 canonical fields."""
        result = INGESTER.ingest(partial_year_1095_a_pdf)
        canonical_paths = [
            f.path
            for f in result.partial.fields
            if f.path.startswith("forms_1095_a[0].")
        ]
        assert len(canonical_paths) == 21

    def test_partial_year_document_kind(self, partial_year_1095_a_pdf):
        result = INGESTER.ingest(partial_year_1095_a_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_1095_A

    def test_partial_year_values_correct(self, partial_year_1095_a_pdf):
        result = INGESTER.ingest(partial_year_1095_a_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        assert paths["forms_1095_a[0].monthly_data[0].enrollment_premium"] == "400.00"
        assert paths["forms_1095_a[0].monthly_data[5].slcsp_premium"] == "480.00"
        assert paths["forms_1095_a[0].monthly_data[3].advance_ptc"] == "180.00"


# ---------------------------------------------------------------------------
# Tests: Minimal ingest
# ---------------------------------------------------------------------------


class TestIngestMinimal:
    def test_minimal_ingest_succeeds(self, minimal_1095_a_pdf):
        result = INGESTER.ingest(minimal_1095_a_pdf)
        assert result.success
        assert result.is_usable

    def test_minimal_has_only_one_month(self, minimal_1095_a_pdf):
        result = INGESTER.ingest(minimal_1095_a_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        # Month 0 present
        assert (
            "forms_1095_a[0].monthly_data[0].enrollment_premium" in paths
        )
        # Month 1 absent
        assert (
            "forms_1095_a[0].monthly_data[1].enrollment_premium" not in paths
        )

    def test_minimal_field_count(self, minimal_1095_a_pdf):
        """Minimal: 1 header (marketplace_id) + 3 monthly = 4 canonical fields."""
        result = INGESTER.ingest(minimal_1095_a_pdf)
        canonical_paths = [
            f.path
            for f in result.partial.fields
            if f.path.startswith("forms_1095_a[0].")
        ]
        assert len(canonical_paths) == 4


# ---------------------------------------------------------------------------
# Tests: Money field precision
# ---------------------------------------------------------------------------


class TestMoneyFieldPrecision:
    def test_all_money_fields_parse_as_decimal(self, fake_1095_a_pdf):
        """Every monthly premium/SLCSP/APTC must parse as a non-negative Decimal."""
        result = INGESTER.ingest(fake_1095_a_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        for month_idx in range(12):
            for col in ("enrollment_premium", "slcsp_premium", "advance_ptc"):
                path = f"forms_1095_a[0].monthly_data[{month_idx}].{col}"
                raw = paths.get(path)
                assert raw is not None, f"missing {path}"
                parsed = Decimal(raw)
                assert parsed >= 0, f"{path} should be non-negative, got {parsed}"

    def test_premium_exceeds_aptc_each_month(self, fake_1095_a_pdf):
        """Sanity: enrollment premium should be >= advance PTC for every month."""
        result = INGESTER.ingest(fake_1095_a_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        for month_idx in range(12):
            premium = Decimal(
                paths[
                    f"forms_1095_a[0].monthly_data[{month_idx}].enrollment_premium"
                ]
            )
            aptc = Decimal(
                paths[
                    f"forms_1095_a[0].monthly_data[{month_idx}].advance_ptc"
                ]
            )
            assert premium >= aptc, (
                f"month {month_idx}: enrollment premium {premium} < APTC {aptc}"
            )
