"""Tests for the SSA-1099 pypdf AcroForm ingester.

The ingester uses SYNTHETIC field names (documented as a follow-up to replace
with real SSA AcroForm field names — the SSA does not publish a generic
fillable SSA-1099 PDF). These tests exercise the synthetic map against a
reportlab-generated fillable PDF fixture to prove the path-rewriting wiring
works end-to-end.

SSA-1099 box layout (per https://www.ssa.gov/pubs/EN-05-10032.pdf):

- Box 1 — Beneficiary name (text)
- Box 2 — Beneficiary SSN (text)
- Box 3 — Benefits paid in tax year (money)
- Box 4 — Benefits repaid to SSA (money)
- Box 5 — Net benefits (Box 3 - Box 4, money)
- Box 6 — Voluntary federal income tax withheld (money)
- Box 7 — Address (text, informational)
- Box 8 — Claim number (text, informational)

Only boxes 3-6 plus the Medicare premium narrative are modeled on
skill.scripts.models.FormSSA1099; boxes 1/2/7/8 are identity/information and
not currently tracked on the canonical return.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pypdf
import pytest
from reportlab.pdfgen import canvas

from skill.scripts.ingest._classifier import classify_by_filename
from skill.scripts.ingest._pipeline import DocumentKind, Ingester
from skill.scripts.ingest._ssa_1099_acroform import (
    FORM_SSA_1099_FIELD_MAP,
    INGESTER,
)


# ---------------------------------------------------------------------------
# Synthetic fillable PDF helper (mirrors _make_acroform_pdf from 1099-R tests)
# ---------------------------------------------------------------------------


def _make_acroform_pdf(path: Path, fields: dict[str, str]) -> None:
    """Write a minimal AcroForm PDF with the given text fields and values.

    Uses reportlab to draw the text fields (which registers them in the
    /AcroForm dict), then uses pypdf's clone_from to copy the whole document
    catalog (including /AcroForm) into a writer so we can set widget values.
    """
    # Step 1: reportlab creates a PDF with named acroform fields
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
    c.save()

    # Step 2: clone the full document (incl. /AcroForm) into a writer and set values
    reader = pypdf.PdfReader(str(path))
    writer = pypdf.PdfWriter(clone_from=reader)
    writer.update_page_form_field_values(
        writer.pages[0], fields, auto_regenerate=True
    )
    with path.open("wb") as fh:
        writer.write(fh)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_FULL_FIELDS: dict[str, str] = {
    "recipient_is_taxpayer": "true",
    "box3_total_benefits": "24600.00",
    "box4_benefits_repaid": "1200.00",
    "box5_net_benefits": "23400.00",
    "box6_federal_income_tax_withheld": "2340.00",
    "medicare_part_b_premiums": "2096.40",
    "medicare_part_d_premiums": "396.00",
}


@pytest.fixture
def fake_ssa_1099_pdf(tmp_path) -> Path:
    # Filename contains "SSA-1099" so the classifier resolves to FORM_SSA_1099
    p = tmp_path / "SSA-1099_retiree.pdf"
    _make_acroform_pdf(p, _FULL_FIELDS)
    return p


@pytest.fixture
def sparse_ssa_1099_pdf(tmp_path) -> Path:
    """An SSA-1099 with only the gross / net benefit amounts filled.

    Common case for a retiree who has no repayments and declines voluntary
    federal withholding — box 4 and box 6 are blank, and there are no Medicare
    premium deductions.
    """
    p = tmp_path / "SSA-1099_sparse.pdf"
    _make_acroform_pdf(
        p,
        {
            "box3_total_benefits": "18000.00",
            "box5_net_benefits": "18000.00",
        },
    )
    return p


@pytest.fixture
def withheld_ssa_1099_pdf(tmp_path) -> Path:
    """A taxable-benefit flow: voluntary withholding MUST be present.

    Exercises the field_count invariant that any SSA-1099 reporting federal
    withholding also carries box5_net_benefits so that a CanonicalReturn
    consumer can compute the effective withholding rate.
    """
    p = tmp_path / "ssa-1099_withheld.pdf"
    _make_acroform_pdf(
        p,
        {
            "box3_total_benefits": "30000.00",
            "box5_net_benefits": "30000.00",
            "box6_federal_income_tax_withheld": "3600.00",
        },
    )
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIngesterContract:
    def test_satisfies_ingester_protocol(self):
        assert isinstance(INGESTER, Ingester)

    def test_name_and_tier(self):
        assert INGESTER.name == "ssa_1099_acroform"
        assert INGESTER.tier == 1

    def test_field_map_registered_for_ssa_1099(self):
        assert DocumentKind.FORM_SSA_1099 in INGESTER.field_map
        mapping = INGESTER.field_map[DocumentKind.FORM_SSA_1099]
        assert (
            mapping["box3_total_benefits"]
            == "forms_ssa_1099[0].box3_total_benefits"
        )
        assert (
            mapping["box6_federal_income_tax_withheld"]
            == "forms_ssa_1099[0].box6_federal_income_tax_withheld"
        )

    def test_field_map_covers_required_boxes(self):
        required = {
            "recipient_is_taxpayer",
            "box3_total_benefits",
            "box4_benefits_repaid",
            "box5_net_benefits",
            "box6_federal_income_tax_withheld",
            "medicare_part_b_premiums",
            "medicare_part_d_premiums",
        }
        assert required.issubset(set(FORM_SSA_1099_FIELD_MAP.keys()))

    def test_all_map_targets_under_forms_ssa_1099(self):
        for canonical in FORM_SSA_1099_FIELD_MAP.values():
            assert canonical.startswith("forms_ssa_1099[0].")

    def test_map_covers_every_form_ssa_1099_model_field(self):
        """Every non-identity field on FormSSA1099 must have a mapping entry.

        If this fails after a FormSSA1099 change, either add the new field to
        the synthetic map (and document in the module docstring) or explicitly
        skip it here.
        """
        expected_model_fields = {
            "recipient_is_taxpayer",
            "box3_total_benefits",
            "box4_benefits_repaid",
            "box5_net_benefits",
            "box6_federal_income_tax_withheld",
            "medicare_part_b_premiums",
            "medicare_part_d_premiums",
        }
        mapped_leaves = {
            canonical.removeprefix("forms_ssa_1099[0].")
            for canonical in FORM_SSA_1099_FIELD_MAP.values()
        }
        assert expected_model_fields.issubset(mapped_leaves)

    def test_map_covers_every_model_field_exactly_via_introspection(self):
        """Belt-and-suspenders: introspect FormSSA1099 and diff against the map.

        Any new FormSSA1099 field (other than the deliberately skipped identity
        boxes 1/2/7/8 which aren't on the model anyway) should force an update.
        """
        from skill.scripts.models import FormSSA1099

        model_fields = set(FormSSA1099.model_fields.keys())
        mapped_leaves = {
            canonical.removeprefix("forms_ssa_1099[0].")
            for canonical in FORM_SSA_1099_FIELD_MAP.values()
        }
        missing = model_fields - mapped_leaves
        assert not missing, f"FormSSA1099 fields not mapped: {missing}"


class TestClassifierRouting:
    def test_classifier_routes_ssa_1099_filename(self, tmp_path):
        p = tmp_path / "SSA-1099.pdf"
        p.write_bytes(b"%PDF-1.4\n")
        assert classify_by_filename(p) == DocumentKind.FORM_SSA_1099

    def test_classifier_routes_lowercase_ssa1099_filename(self, tmp_path):
        """Case-insensitive and no-dash: 'ssa1099_*.pdf' should still route."""
        p = tmp_path / "ssa1099_client_2025.pdf"
        p.write_bytes(b"%PDF-1.4\n")
        assert classify_by_filename(p) == DocumentKind.FORM_SSA_1099

    def test_classifier_does_not_confuse_with_1099_r(self, tmp_path):
        """A plain '1099-R_*.pdf' must NOT classify as SSA-1099."""
        p = tmp_path / "1099-R_vanguard.pdf"
        p.write_bytes(b"%PDF-1.4\n")
        assert classify_by_filename(p) == DocumentKind.FORM_1099_R

    def test_ingester_ingest_sets_document_kind_from_filename(
        self, fake_ssa_1099_pdf
    ):
        result = INGESTER.ingest(fake_ssa_1099_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_SSA_1099


class TestCanHandle:
    def test_can_handle_fake_ssa_1099(self, fake_ssa_1099_pdf):
        assert INGESTER.can_handle(fake_ssa_1099_pdf)

    def test_cannot_handle_non_pdf(self, tmp_path):
        p = tmp_path / "note.txt"
        p.write_bytes(b"hello")
        assert not INGESTER.can_handle(p)

    def test_cannot_handle_missing_file(self, tmp_path):
        p = tmp_path / "does_not_exist.pdf"
        assert not INGESTER.can_handle(p)


class TestIngestFullForm:
    def test_ingest_succeeds(self, fake_ssa_1099_pdf):
        result = INGESTER.ingest(fake_ssa_1099_pdf)
        assert result.success
        assert result.error is None

    def test_document_kind_is_ssa_1099(self, fake_ssa_1099_pdf):
        result = INGESTER.ingest(fake_ssa_1099_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_SSA_1099

    def test_round_trip_every_box_populated(self, fake_ssa_1099_pdf):
        """Synthesize -> ingest -> assert every mapped box lands on its path.

        This is the required "round-trip" test: builds a fillable PDF with the
        synthetic widget names, runs it through the ingester, and asserts the
        resulting PartialReturn has every mapped field populated with the
        expected value.
        """
        result = INGESTER.ingest(fake_ssa_1099_pdf)
        assert result.success
        paths = {f.path: f.value for f in result.partial.fields}

        # Every mapped field must have landed on its canonical path with the
        # expected widget value.
        assert paths.get("forms_ssa_1099[0].recipient_is_taxpayer") == "true"
        assert paths.get("forms_ssa_1099[0].box3_total_benefits") == "24600.00"
        assert paths.get("forms_ssa_1099[0].box4_benefits_repaid") == "1200.00"
        assert paths.get("forms_ssa_1099[0].box5_net_benefits") == "23400.00"
        assert (
            paths.get("forms_ssa_1099[0].box6_federal_income_tax_withheld")
            == "2340.00"
        )
        assert paths.get("forms_ssa_1099[0].medicare_part_b_premiums") == "2096.40"
        assert paths.get("forms_ssa_1099[0].medicare_part_d_premiums") == "396.00"

    def test_every_model_field_is_populated_in_round_trip(
        self, fake_ssa_1099_pdf
    ):
        """Field-count invariant: the round-trip partial carries ONE entry per
        mapped FormSSA1099 field — nothing lost, nothing duplicated."""
        result = INGESTER.ingest(fake_ssa_1099_pdf)
        canonical_paths = [
            f.path
            for f in result.partial.fields
            if f.path.startswith("forms_ssa_1099[0].")
        ]
        expected_paths = set(FORM_SSA_1099_FIELD_MAP.values())
        assert set(canonical_paths) == expected_paths
        assert len(canonical_paths) == len(expected_paths)

    def test_box5_net_benefits_parses_as_decimal(self, fake_ssa_1099_pdf):
        """Field-count invariant: box5 net benefits must be parseable as Decimal.

        The ingester emits the raw widget string; downstream rewriting converts
        to Decimal. This test proves the string round-trips without loss of
        precision — critical because Social Security benefits feed the
        taxable-benefit worksheet and any rounding would change the 1040 line 6b.
        """
        result = INGESTER.ingest(fake_ssa_1099_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        raw = paths.get("forms_ssa_1099[0].box5_net_benefits")
        assert raw is not None
        assert Decimal(raw) == Decimal("23400.00")

    def test_all_money_boxes_parse_as_decimal(self, fake_ssa_1099_pdf):
        """Every money box (3, 4, 5, 6, Medicare B/D) must parse as Decimal."""
        result = INGESTER.ingest(fake_ssa_1099_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        money_paths = [
            "forms_ssa_1099[0].box3_total_benefits",
            "forms_ssa_1099[0].box4_benefits_repaid",
            "forms_ssa_1099[0].box5_net_benefits",
            "forms_ssa_1099[0].box6_federal_income_tax_withheld",
            "forms_ssa_1099[0].medicare_part_b_premiums",
            "forms_ssa_1099[0].medicare_part_d_premiums",
        ]
        for path in money_paths:
            raw = paths.get(path)
            assert raw is not None, f"missing money path: {path}"
            # Must be Decimal-parseable; must be non-negative.
            parsed = Decimal(raw)
            assert parsed >= 0, f"{path} should be non-negative, got {parsed}"

    def test_box3_minus_box4_equals_box5(self, fake_ssa_1099_pdf):
        """Arithmetic invariant: net = gross - repaid on the widget values."""
        result = INGESTER.ingest(fake_ssa_1099_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        gross = Decimal(paths["forms_ssa_1099[0].box3_total_benefits"])
        repaid = Decimal(paths["forms_ssa_1099[0].box4_benefits_repaid"])
        net = Decimal(paths["forms_ssa_1099[0].box5_net_benefits"])
        assert gross - repaid == net

    def test_no_raw_fallback_paths_for_full_form(self, fake_ssa_1099_pdf):
        """When every field is mapped, none should drop to _acroform_raw."""
        result = INGESTER.ingest(fake_ssa_1099_pdf)
        for f in result.partial.fields:
            assert not f.path.startswith("_acroform_raw."), (
                f"unmapped field leaked to raw: {f.path}"
            )

    def test_fields_carry_full_confidence(self, fake_ssa_1099_pdf):
        result = INGESTER.ingest(fake_ssa_1099_pdf)
        assert result.partial.fields  # non-empty
        for f in result.partial.fields:
            assert f.confidence == 1.0

    def test_result_is_usable(self, fake_ssa_1099_pdf):
        result = INGESTER.ingest(fake_ssa_1099_pdf)
        assert result.is_usable


class TestIngestSparseForm:
    def test_sparse_ingest_succeeds(self, sparse_ssa_1099_pdf):
        """A minimal SSA-1099 with just box3 + box5 must still be usable."""
        result = INGESTER.ingest(sparse_ssa_1099_pdf)
        assert result.success
        assert result.is_usable

    def test_sparse_reports_only_filled_boxes(self, sparse_ssa_1099_pdf):
        result = INGESTER.ingest(sparse_ssa_1099_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        assert paths.get("forms_ssa_1099[0].box3_total_benefits") == "18000.00"
        assert paths.get("forms_ssa_1099[0].box5_net_benefits") == "18000.00"
        # Unfilled boxes must NOT appear in the partial
        assert "forms_ssa_1099[0].box4_benefits_repaid" not in paths
        assert (
            "forms_ssa_1099[0].box6_federal_income_tax_withheld" not in paths
        )
        assert "forms_ssa_1099[0].medicare_part_b_premiums" not in paths
        assert "forms_ssa_1099[0].medicare_part_d_premiums" not in paths

    def test_sparse_document_kind(self, sparse_ssa_1099_pdf):
        result = INGESTER.ingest(sparse_ssa_1099_pdf)
        assert result.partial.document_kind == DocumentKind.FORM_SSA_1099


class TestTaxableBenefitWithholdingInvariant:
    """Invariant: any taxable-benefit flow with voluntary withholding must
    also carry box5 net benefits, so downstream code can compute the effective
    withholding rate.
    """

    def test_withheld_fixture_has_box5(self, withheld_ssa_1099_pdf):
        result = INGESTER.ingest(withheld_ssa_1099_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        # If box 6 is present, box 5 MUST also be present.
        has_withholding = (
            "forms_ssa_1099[0].box6_federal_income_tax_withheld" in paths
        )
        has_net_benefits = "forms_ssa_1099[0].box5_net_benefits" in paths
        if has_withholding:
            assert has_net_benefits, (
                "box6 voluntary withholding present without box5 net "
                "benefits — invalid taxable-benefit flow"
            )

    def test_withheld_box6_is_decimal(self, withheld_ssa_1099_pdf):
        """Box 6 voluntary federal withholding must be Decimal-parseable when
        any taxable-benefit flow is reported."""
        result = INGESTER.ingest(withheld_ssa_1099_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        raw = paths.get("forms_ssa_1099[0].box6_federal_income_tax_withheld")
        assert raw is not None
        parsed = Decimal(raw)
        assert parsed == Decimal("3600.00")
        assert parsed >= Decimal("0")

    def test_withheld_net_benefits_is_decimal(self, withheld_ssa_1099_pdf):
        result = INGESTER.ingest(withheld_ssa_1099_pdf)
        paths = {f.path: f.value for f in result.partial.fields}
        raw = paths.get("forms_ssa_1099[0].box5_net_benefits")
        assert raw is not None
        assert Decimal(raw) == Decimal("30000.00")
