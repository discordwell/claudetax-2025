"""Tests for the Schedule K-1 pypdf AcroForm ingester.

The ingester uses SYNTHETIC field names (documented as a follow-up to replace
with real IRS AcroForm field names — the IRS publishes separate fillable PDFs
for the partnership (1065) and S-corp (1120-S) flavors and the widget paths
differ even though the canonical model is shared). These tests exercise the
synthetic map against a reportlab-generated fillable PDF fixture to prove the
path-rewriting wiring works end-to-end for both flavors.

Schedule K-1 box layout (selected money lines covered by the model):

- Part I  — Information About the Partnership / S Corporation (entity name + EIN)
- Part II — Information About the Partner / Shareholder (recipient flag)
- Box 1   — Ordinary business income (loss)
- Box 2   — Net rental real estate income (loss)
- Box 3   — Other net rental income (loss)
- Box 4   — Guaranteed payments (1065 only)
- Box 5   — Interest income
- Box 6a  — Ordinary dividends
- Box 6b  — Qualified dividends
- Box 7   — Royalties
- Box 8   — Net short-term capital gain (loss)
- Box 9a  — Net long-term capital gain (loss)
- Box 12  — Section 179 deduction
- Box 20  — Other information (Z = QBI flag, AA = 199A W-2 wages, AB = UBIA)

The 1120-S K-1 uses the same Part III labels but different box numbers for
section 179 and 199A items; the canonical ScheduleK1 model is layout-agnostic
because every line maps to a named attribute, not a box number.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pypdf
import pytest
from reportlab.pdfgen import canvas

from skill.scripts.ingest._classifier import classify_by_filename
from skill.scripts.ingest._pipeline import DocumentKind, Ingester
from skill.scripts.ingest._schedule_k1_acroform import (
    INGESTER,
    SCHEDULE_K1_FIELD_MAP,
    SchedK1PyPdfAcroFormIngester,
)


# ---------------------------------------------------------------------------
# Synthetic fillable PDF helper (mirrors _make_acroform_pdf from sibling tests)
# ---------------------------------------------------------------------------


def _make_acroform_pdf(
    path: Path,
    fields: dict[str, str],
    *,
    extra_text: str | None = None,
) -> None:
    """Write a minimal AcroForm PDF with the given text fields and values.

    Uses reportlab to draw the text fields (which registers them in the
    /AcroForm dict), then uses pypdf's clone_from to copy the whole document
    catalog (including /AcroForm) into a writer so we can set widget values.

    ``extra_text`` is drawn at the top of the page so the content-layer probe
    in the K-1 ingester (which looks for "Form 1120-S") can find a marker.
    """
    c = canvas.Canvas(str(path))
    if extra_text:
        c.drawString(50, 780, extra_text)
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
    c.save()

    reader = pypdf.PdfReader(str(path))
    writer = pypdf.PdfWriter(clone_from=reader)
    writer.update_page_form_field_values(
        writer.pages[0], fields, auto_regenerate=True
    )
    with path.open("wb") as fh:
        writer.write(fh)


# ---------------------------------------------------------------------------
# Fixture data — full partnership K-1 (every mapped field populated)
# ---------------------------------------------------------------------------


_FULL_PARTNERSHIP_FIELDS: dict[str, str] = {
    # Part I
    "source_name": "Acme Holdings LP",
    "source_ein": "12-3456789",
    # Part II
    "recipient_is_taxpayer": "true",
    # Part III money boxes
    "ordinary_business_income": "42500.00",
    "net_rental_real_estate_income": "1800.00",
    "other_net_rental_income": "350.00",
    "guaranteed_payments": "12000.00",
    "interest_income": "275.50",
    "ordinary_dividends": "1100.00",
    "qualified_dividends": "950.00",
    "royalties": "0.00",
    "short_term_capital_gain_loss": "-200.00",
    "long_term_capital_gain_loss": "8500.00",
    "section_179_deduction": "5000.00",
    # Box 20 codes
    "qbi_qualified": "true",
    "section_199a_w2_wages": "85000.00",
    "section_199a_ubia": "1250000.00",
    # Free-form
    "other_items": "{'box_13_code_W': '500.00'}",
}


_FULL_S_CORP_FIELDS: dict[str, str] = {
    # Part I
    "source_name": "Beacon Manufacturing Inc",
    "source_ein": "98-7654321",
    # Part II
    "recipient_is_taxpayer": "true",
    # Part III money boxes (1120-S omits guaranteed_payments)
    "ordinary_business_income": "75000.00",
    "net_rental_real_estate_income": "0.00",
    "other_net_rental_income": "0.00",
    "guaranteed_payments": "0.00",
    "interest_income": "120.00",
    "ordinary_dividends": "450.00",
    "qualified_dividends": "450.00",
    "royalties": "0.00",
    "short_term_capital_gain_loss": "0.00",
    "long_term_capital_gain_loss": "3200.00",
    "section_179_deduction": "8000.00",
    # Box 17 codes (1120-S analog of 1065 Box 20)
    "qbi_qualified": "true",
    "section_199a_w2_wages": "150000.00",
    "section_199a_ubia": "500000.00",
    "other_items": "{'box_16_code_C': '0.00'}",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_k1_partnership_pdf(tmp_path) -> Path:
    """A fully-populated 1065 K-1 with no S-corp content marker."""
    p = tmp_path / "K-1_acme_partnership.pdf"
    _make_acroform_pdf(p, _FULL_PARTNERSHIP_FIELDS)
    return p


@pytest.fixture
def fake_k1_s_corp_pdf(tmp_path) -> Path:
    """A fully-populated 1120-S K-1.

    Includes the literal string "Form 1120-S" on the first page so the
    K-1 ingester's content-layer probe can upgrade ``document_kind`` and
    inject ``source_type=s_corp`` into the partial.
    """
    p = tmp_path / "K-1_beacon_scorp.pdf"
    _make_acroform_pdf(
        p,
        _FULL_S_CORP_FIELDS,
        extra_text="Schedule K-1 (Form 1120-S) Shareholder's Share",
    )
    return p


@pytest.fixture
def sparse_k1_pdf(tmp_path) -> Path:
    """A minimal partnership K-1 with only entity + Box 1 ordinary income.

    Common case for a single-investor LP whose only pass-through item is
    ordinary business income.
    """
    p = tmp_path / "K-1_sparse_partnership.pdf"
    _make_acroform_pdf(
        p,
        {
            "source_name": "Tiny LLC",
            "source_ein": "11-2233445",
            "ordinary_business_income": "1500.00",
        },
    )
    return p


# ---------------------------------------------------------------------------
# Ingester contract
# ---------------------------------------------------------------------------


class TestIngesterContract:
    def test_satisfies_ingester_protocol(self):
        assert isinstance(INGESTER, Ingester)

    def test_is_correct_subclass(self):
        assert isinstance(INGESTER, SchedK1PyPdfAcroFormIngester)

    def test_name_and_tier(self):
        assert INGESTER.name == "schedule_k1_acroform"
        assert INGESTER.tier == 1

    def test_field_map_registered_for_both_k1_kinds(self):
        assert DocumentKind.SCHEDULE_K1_1065 in INGESTER.field_map
        assert DocumentKind.SCHEDULE_K1_1120S in INGESTER.field_map

    def test_field_maps_for_both_kinds_share_same_canonical_paths(self):
        partnership_map = INGESTER.field_map[DocumentKind.SCHEDULE_K1_1065]
        s_corp_map = INGESTER.field_map[DocumentKind.SCHEDULE_K1_1120S]
        # Both flavors point at the same ``schedules_k1[0].*`` paths because
        # the canonical ScheduleK1 model is layout-agnostic.
        assert set(partnership_map.values()) == set(s_corp_map.values())

    def test_field_map_covers_required_fields(self):
        required = {
            "source_name",
            "source_ein",
            "source_type",
            "recipient_is_taxpayer",
            "ordinary_business_income",
            "net_rental_real_estate_income",
            "other_net_rental_income",
            "guaranteed_payments",
            "interest_income",
            "ordinary_dividends",
            "qualified_dividends",
            "royalties",
            "short_term_capital_gain_loss",
            "long_term_capital_gain_loss",
            "section_179_deduction",
            "qbi_qualified",
            "section_199a_w2_wages",
            "section_199a_ubia",
            "other_items",
        }
        assert required.issubset(set(SCHEDULE_K1_FIELD_MAP.keys()))

    def test_all_map_targets_under_schedules_k1(self):
        for canonical in SCHEDULE_K1_FIELD_MAP.values():
            assert canonical.startswith("schedules_k1[0].")

    def test_map_covers_every_schedule_k1_model_field(self):
        """Belt-and-suspenders: introspect ScheduleK1 and diff against the map.

        Any new ScheduleK1 field should force an update to the synthetic map.
        """
        from skill.scripts.models import ScheduleK1

        model_fields = set(ScheduleK1.model_fields.keys())
        mapped_leaves = {
            canonical.removeprefix("schedules_k1[0].")
            for canonical in SCHEDULE_K1_FIELD_MAP.values()
        }
        missing = model_fields - mapped_leaves
        assert not missing, f"ScheduleK1 fields not mapped: {missing}"


# ---------------------------------------------------------------------------
# Classifier routing — read-only checks against the shared classifier
# ---------------------------------------------------------------------------


class TestClassifierRouting:
    def test_classifier_routes_k1_filename(self, tmp_path):
        p = tmp_path / "K-1_acme.pdf"
        p.write_bytes(b"%PDF-1.4\n")
        assert classify_by_filename(p) == DocumentKind.SCHEDULE_K1_1065

    def test_classifier_routes_lowercase_k1_filename(self, tmp_path):
        p = tmp_path / "k1_partnership_2025.pdf"
        p.write_bytes(b"%PDF-1.4\n")
        assert classify_by_filename(p) == DocumentKind.SCHEDULE_K1_1065

    def test_classifier_does_not_confuse_k1_with_other_forms(self, tmp_path):
        """A 1099-R filename must not classify as K-1."""
        p = tmp_path / "1099-R_vanguard.pdf"
        p.write_bytes(b"%PDF-1.4\n")
        assert classify_by_filename(p) == DocumentKind.FORM_1099_R


# ---------------------------------------------------------------------------
# can_handle filtering
# ---------------------------------------------------------------------------


class TestCanHandle:
    def test_can_handle_partnership_k1(self, fake_k1_partnership_pdf):
        assert INGESTER.can_handle(fake_k1_partnership_pdf)

    def test_can_handle_s_corp_k1(self, fake_k1_s_corp_pdf):
        assert INGESTER.can_handle(fake_k1_s_corp_pdf)

    def test_cannot_handle_non_pdf(self, tmp_path):
        p = tmp_path / "note.txt"
        p.write_bytes(b"hello")
        assert not INGESTER.can_handle(p)

    def test_cannot_handle_missing_file(self, tmp_path):
        p = tmp_path / "does_not_exist.pdf"
        assert not INGESTER.can_handle(p)

    def test_cannot_handle_non_k1_acroform_pdf(self, tmp_path):
        """A 1099-R AcroForm PDF must NOT be claimed by the K-1 ingester.

        Stricter contract than the SSA-1099 / 1099-R ingesters which return
        True for any AcroForm PDF; the K-1 ingester filters by document kind
        because it has special source_type detection logic that would corrupt
        non-K-1 partials.
        """
        p = tmp_path / "1099-R_vanguard.pdf"
        _make_acroform_pdf(p, {"box1_gross_distribution": "1000.00"})
        # Sanity check: the helper produces a real AcroForm PDF the base
        # would otherwise accept.
        from skill.scripts.ingest._pypdf_acroform import PyPdfAcroFormIngester

        base = PyPdfAcroFormIngester()
        assert base.can_handle(p)
        # But the K-1 ingester filters by classified kind and rejects it.
        assert not INGESTER.can_handle(p)


# ---------------------------------------------------------------------------
# Round-trip ingestion — full partnership K-1
# ---------------------------------------------------------------------------


class TestIngestPartnershipK1:
    def test_ingest_succeeds(self, fake_k1_partnership_pdf):
        result = INGESTER.ingest(fake_k1_partnership_pdf)
        assert result.success
        assert result.error is None

    def test_document_kind_is_partnership(self, fake_k1_partnership_pdf):
        result = INGESTER.ingest(fake_k1_partnership_pdf)
        assert result.partial.document_kind == DocumentKind.SCHEDULE_K1_1065

    def test_round_trip_every_mapped_field_populated(
        self, fake_k1_partnership_pdf
    ):
        """Synthesize -> ingest -> assert every mapped field lands on its path."""
        result = INGESTER.ingest(fake_k1_partnership_pdf)
        assert result.success
        paths = {f.path: f.value for f in result.partial.fields}

        # Part I — entity
        assert paths.get("schedules_k1[0].source_name") == "Acme Holdings LP"
        assert paths.get("schedules_k1[0].source_ein") == "12-3456789"
        # Source type comes from the content probe (no S-corp marker present)
        assert paths.get("schedules_k1[0].source_type") == "partnership"
        # Part II — recipient
        assert paths.get("schedules_k1[0].recipient_is_taxpayer") == "true"
        # Part III — money boxes
        assert paths.get("schedules_k1[0].ordinary_business_income") == "42500.00"
        assert paths.get("schedules_k1[0].net_rental_real_estate_income") == "1800.00"
        assert paths.get("schedules_k1[0].other_net_rental_income") == "350.00"
        assert paths.get("schedules_k1[0].guaranteed_payments") == "12000.00"
        assert paths.get("schedules_k1[0].interest_income") == "275.50"
        assert paths.get("schedules_k1[0].ordinary_dividends") == "1100.00"
        assert paths.get("schedules_k1[0].qualified_dividends") == "950.00"
        assert paths.get("schedules_k1[0].royalties") == "0.00"
        assert paths.get("schedules_k1[0].short_term_capital_gain_loss") == "-200.00"
        assert paths.get("schedules_k1[0].long_term_capital_gain_loss") == "8500.00"
        assert paths.get("schedules_k1[0].section_179_deduction") == "5000.00"
        # Box 20 codes
        assert paths.get("schedules_k1[0].qbi_qualified") == "true"
        assert paths.get("schedules_k1[0].section_199a_w2_wages") == "85000.00"
        assert paths.get("schedules_k1[0].section_199a_ubia") == "1250000.00"
        # Free-form
        assert paths.get("schedules_k1[0].other_items") == "{'box_13_code_W': '500.00'}"

    def test_every_model_field_populated_in_round_trip(
        self, fake_k1_partnership_pdf
    ):
        """Field-count invariant: the round-trip partial carries ONE entry per
        mapped ScheduleK1 field — nothing lost, nothing duplicated."""
        result = INGESTER.ingest(fake_k1_partnership_pdf)
        canonical_paths = [
            f.path
            for f in result.partial.fields
            if f.path.startswith("schedules_k1[0].")
        ]
        expected_paths = set(SCHEDULE_K1_FIELD_MAP.values())
        assert set(canonical_paths) == expected_paths

    def test_money_boxes_parse_as_decimal(self, fake_k1_partnership_pdf):
        """Every money box must be Decimal-parseable on the partial."""
        result = INGESTER.ingest(fake_k1_partnership_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        money_paths = [
            "schedules_k1[0].ordinary_business_income",
            "schedules_k1[0].net_rental_real_estate_income",
            "schedules_k1[0].other_net_rental_income",
            "schedules_k1[0].guaranteed_payments",
            "schedules_k1[0].interest_income",
            "schedules_k1[0].ordinary_dividends",
            "schedules_k1[0].qualified_dividends",
            "schedules_k1[0].royalties",
            "schedules_k1[0].short_term_capital_gain_loss",
            "schedules_k1[0].long_term_capital_gain_loss",
            "schedules_k1[0].section_179_deduction",
            "schedules_k1[0].section_199a_w2_wages",
            "schedules_k1[0].section_199a_ubia",
        ]
        for path in money_paths:
            raw = paths.get(path)
            assert raw is not None, f"missing money path: {path}"
            # Must be Decimal-parseable; signed (capital losses can be negative).
            Decimal(raw)

    def test_short_term_loss_round_trips_signed(self, fake_k1_partnership_pdf):
        """Capital losses are signed; the partial must preserve the negative."""
        result = INGESTER.ingest(fake_k1_partnership_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        raw = paths.get("schedules_k1[0].short_term_capital_gain_loss")
        assert raw is not None
        assert Decimal(raw) == Decimal("-200.00")

    def test_no_raw_fallback_paths_for_full_form(self, fake_k1_partnership_pdf):
        """When every field is mapped, none should drop to _acroform_raw."""
        result = INGESTER.ingest(fake_k1_partnership_pdf)
        for f in result.partial.fields:
            assert not f.path.startswith("_acroform_raw."), (
                f"unmapped field leaked to raw: {f.path}"
            )

    def test_fields_carry_full_confidence(self, fake_k1_partnership_pdf):
        result = INGESTER.ingest(fake_k1_partnership_pdf)
        assert result.partial.fields  # non-empty
        for f in result.partial.fields:
            assert f.confidence == 1.0

    def test_result_is_usable(self, fake_k1_partnership_pdf):
        result = INGESTER.ingest(fake_k1_partnership_pdf)
        assert result.is_usable


# ---------------------------------------------------------------------------
# Round-trip ingestion — full S-corp K-1
# ---------------------------------------------------------------------------


class TestIngestSCorpK1:
    def test_ingest_succeeds(self, fake_k1_s_corp_pdf):
        result = INGESTER.ingest(fake_k1_s_corp_pdf)
        assert result.success
        assert result.error is None

    def test_document_kind_upgraded_to_1120s(self, fake_k1_s_corp_pdf):
        """The content probe must upgrade the kind from the 1065 default."""
        result = INGESTER.ingest(fake_k1_s_corp_pdf)
        assert result.partial.document_kind == DocumentKind.SCHEDULE_K1_1120S

    def test_source_type_is_s_corp(self, fake_k1_s_corp_pdf):
        """The content probe must inject source_type=s_corp into the partial."""
        result = INGESTER.ingest(fake_k1_s_corp_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        assert paths.get("schedules_k1[0].source_type") == "s_corp"

    def test_round_trip_every_mapped_field_populated(self, fake_k1_s_corp_pdf):
        result = INGESTER.ingest(fake_k1_s_corp_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        # Spot-check a few fields specific to the S-corp fixture
        assert paths.get("schedules_k1[0].source_name") == "Beacon Manufacturing Inc"
        assert paths.get("schedules_k1[0].source_ein") == "98-7654321"
        assert paths.get("schedules_k1[0].ordinary_business_income") == "75000.00"
        assert paths.get("schedules_k1[0].section_179_deduction") == "8000.00"
        assert paths.get("schedules_k1[0].section_199a_w2_wages") == "150000.00"
        # And the source_type came from the probe, not a widget
        assert paths.get("schedules_k1[0].source_type") == "s_corp"

    def test_every_mapped_field_lands_on_canonical_path(
        self, fake_k1_s_corp_pdf
    ):
        result = INGESTER.ingest(fake_k1_s_corp_pdf)
        canonical_paths = {
            f.path
            for f in result.partial.fields
            if f.path.startswith("schedules_k1[0].")
        }
        # Every value in SCHEDULE_K1_FIELD_MAP should appear because the
        # S-corp fixture also fills every synthetic widget.
        assert set(SCHEDULE_K1_FIELD_MAP.values()).issubset(canonical_paths)

    def test_fields_carry_full_confidence(self, fake_k1_s_corp_pdf):
        result = INGESTER.ingest(fake_k1_s_corp_pdf)
        assert result.partial.fields
        for f in result.partial.fields:
            assert f.confidence == 1.0

    def test_no_raw_fallback_paths_for_full_form(self, fake_k1_s_corp_pdf):
        result = INGESTER.ingest(fake_k1_s_corp_pdf)
        for f in result.partial.fields:
            assert not f.path.startswith("_acroform_raw."), (
                f"unmapped field leaked to raw: {f.path}"
            )


# ---------------------------------------------------------------------------
# Source type detection edge cases
# ---------------------------------------------------------------------------


class TestSourceTypeDetection:
    def test_partnership_default_when_no_marker(self, fake_k1_partnership_pdf):
        """No 'Form 1120-S' marker -> partnership."""
        result = INGESTER.ingest(fake_k1_partnership_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        assert paths.get("schedules_k1[0].source_type") == "partnership"
        assert result.partial.document_kind == DocumentKind.SCHEDULE_K1_1065

    def test_explicit_widget_overrides_content_probe(self, tmp_path):
        """An explicit ``source_type`` widget value wins over the content probe.

        This lets a fixture (e.g. an estate K-1, where neither the filename
        nor the form title contains 'Form 1120-S' but the source_type is
        ``estate_or_trust``) opt out of the heuristic.
        """
        p = tmp_path / "K-1_estate.pdf"
        _make_acroform_pdf(
            p,
            {
                "source_name": "Smith Family Trust",
                "source_ein": "55-6677889",
                "source_type": "estate_or_trust",
                "ordinary_business_income": "0.00",
                "interest_income": "1500.00",
            },
        )
        result = INGESTER.ingest(p)
        paths = {f.path: f.value for f in result.partial.fields}
        # The explicit widget value must survive — the content probe defaults
        # to partnership but does NOT overwrite an explicit widget.
        assert paths.get("schedules_k1[0].source_type") == "estate_or_trust"


# ---------------------------------------------------------------------------
# Sparse-form ingestion
# ---------------------------------------------------------------------------


class TestIngestSparseK1:
    def test_sparse_ingest_succeeds(self, sparse_k1_pdf):
        result = INGESTER.ingest(sparse_k1_pdf)
        assert result.success
        assert result.is_usable

    def test_sparse_reports_only_filled_fields(self, sparse_k1_pdf):
        result = INGESTER.ingest(sparse_k1_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        # Filled widgets land
        assert paths.get("schedules_k1[0].source_name") == "Tiny LLC"
        assert paths.get("schedules_k1[0].source_ein") == "11-2233445"
        assert paths.get("schedules_k1[0].ordinary_business_income") == "1500.00"
        # Unfilled widgets must NOT appear
        assert "schedules_k1[0].guaranteed_payments" not in paths
        assert "schedules_k1[0].section_179_deduction" not in paths
        assert "schedules_k1[0].qualified_dividends" not in paths
        # source_type was injected by the probe (no S-corp marker -> partnership)
        assert paths.get("schedules_k1[0].source_type") == "partnership"

    def test_sparse_document_kind_defaults_to_partnership(self, sparse_k1_pdf):
        result = INGESTER.ingest(sparse_k1_pdf)
        assert result.partial.document_kind == DocumentKind.SCHEDULE_K1_1065


# ---------------------------------------------------------------------------
# Real IRS K-1 PDF template tests (wave 6)
# ---------------------------------------------------------------------------


_REAL_K1_1065_PDF: Path = (
    Path(__file__).resolve().parents[1]
    / "reference"
    / "irs_forms"
    / "f1065sk1_ty2024.pdf"
)

_REAL_K1_1120S_PDF: Path = (
    Path(__file__).resolve().parents[1]
    / "reference"
    / "irs_forms"
    / "f1120ssk_ty2024.pdf"
)


@pytest.fixture
def real_k1_1065(tmp_path: Path) -> Path:
    import shutil

    dst = tmp_path / "k-1_1065.pdf"
    shutil.copy(_REAL_K1_1065_PDF, dst)
    return dst


@pytest.fixture
def real_k1_1120s(tmp_path: Path) -> Path:
    import shutil

    dst = tmp_path / "k-1_1120s.pdf"
    shutil.copy(_REAL_K1_1120S_PDF, dst)
    return dst


class TestRealK1AcroForm:
    """Wave 6: the K-1 ingester must read both real IRS fillable K-1
    templates (1065 partner and 1120-S shareholder) without crashing and
    must carry Part I identity widget names in its field map."""

    def test_real_k1_1065_pdf_exists(self) -> None:
        assert _REAL_K1_1065_PDF.exists()

    def test_real_k1_1120s_pdf_exists(self) -> None:
        assert _REAL_K1_1120S_PDF.exists()

    def test_real_k1_1065_ingest_succeeds(self, real_k1_1065: Path) -> None:
        assert INGESTER.can_handle(real_k1_1065) is True
        result = INGESTER.ingest(real_k1_1065)
        assert result.success, result.error
        # 1065 K-1 content probe -> partnership
        assert result.partial.document_kind == DocumentKind.SCHEDULE_K1_1065

    def test_real_k1_1120s_ingest_succeeds(self, real_k1_1120s: Path) -> None:
        assert INGESTER.can_handle(real_k1_1120s) is True
        result = INGESTER.ingest(real_k1_1120s)
        assert result.success, result.error
        # 1120-S K-1 content probe -> s_corp upgrade path
        assert result.partial.document_kind == DocumentKind.SCHEDULE_K1_1120S

    def test_field_map_has_real_identity_widget_names(self) -> None:
        """Part I entity-identity widgets for both K-1 flavors must be in
        the merged field map (and must resolve to real widgets on at
        least one of the two archived PDFs)."""
        reader_1065 = pypdf.PdfReader(str(_REAL_K1_1065_PDF))
        reader_1120s = pypdf.PdfReader(str(_REAL_K1_1120S_PDF))
        actual = set(reader_1065.get_fields().keys()) | set(
            reader_1120s.get_fields().keys()
        )
        real_keys = [
            k for k in SCHEDULE_K1_FIELD_MAP if k.startswith("topmostSubform")
        ]
        assert real_keys, "SCHEDULE_K1_FIELD_MAP missing real IRS widget keys"
        missing = [k for k in real_keys if k not in actual]
        assert not missing, missing[:5]
