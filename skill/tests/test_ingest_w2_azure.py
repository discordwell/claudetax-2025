"""Tests for the W-2 Azure Document Intelligence ingester.

These tests follow the ``TestAzureIngesterGracefulMissingCreds`` pattern in
``test_ingest_pipeline.py``: every test that would actually call Azure must
skip if the ``AZURE_DOC_INTEL_ENDPOINT`` / ``AZURE_DOC_INTEL_KEY`` env vars
aren't set, so the suite stays green on machines without Azure credentials.

The ingester contract under test:
- ``INGESTER`` satisfies the ``Ingester`` Protocol at runtime.
- ``can_handle()`` is False whenever credentials are unset, regardless of path.
- ``can_handle()`` is True for PDFs/images when credentials are set.
- ``ingest()`` returns a failure ``IngestResult`` (does NOT raise) when
  credentials are missing, with an error message that cites the env vars.
- ``W2_AZURE_FIELD_MAP`` pins the canonical paths the rest of the skill relies
  on (for example ``WagesTipsAndOtherCompensation`` → ``w2s[0].box1_wages``).
- ``INGESTER.name == "w2_azure"`` and ``INGESTER.tier == 3``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from skill.scripts.ingest._azure_doc_intelligence import (
    ENV_ENDPOINT,
    ENV_KEY,
    azure_credentials_configured,
)
from skill.scripts.ingest._pipeline import DocumentKind, Ingester
from skill.scripts.ingest._w2_azure import (
    INGESTER,
    W2_AZURE_FIELD_MAP,
    W2AzureIngester,
)


# ---------------------------------------------------------------------------
# Protocol + identity
# ---------------------------------------------------------------------------


class TestW2AzureIngesterIdentity:
    def test_module_singleton_satisfies_ingester_protocol(self):
        assert isinstance(INGESTER, Ingester)

    def test_singleton_name(self):
        assert INGESTER.name == "w2_azure"

    def test_singleton_tier(self):
        # Azure OCR ingesters live at tier 3 (slow, paid API, last resort).
        assert INGESTER.tier == 3

    def test_is_subclass_of_base_azure_ingester(self):
        from skill.scripts.ingest._azure_doc_intelligence import (
            AzureDocIntelligenceIngester,
        )

        assert isinstance(INGESTER, AzureDocIntelligenceIngester)


# ---------------------------------------------------------------------------
# Field mapping
# ---------------------------------------------------------------------------


class TestW2AzureFieldMap:
    def test_contains_wages_mapping(self):
        assert (
            W2_AZURE_FIELD_MAP["WagesTipsAndOtherCompensation"]
            == "w2s[0].box1_wages"
        )

    def test_contains_federal_withholding(self):
        assert (
            W2_AZURE_FIELD_MAP["FederalIncomeTaxWithheld"]
            == "w2s[0].box2_federal_income_tax_withheld"
        )

    def test_contains_employer_name_and_ein(self):
        assert W2_AZURE_FIELD_MAP["Employer.Name"] == "w2s[0].employer_name"
        assert W2_AZURE_FIELD_MAP["Employer.IdNumber"] == "w2s[0].employer_ein"

    def test_contains_all_money_boxes(self):
        # Every canonical box should round-trip through the map. This pins the
        # contract so a future refactor can't silently drop a field.
        canonical_paths = set(W2_AZURE_FIELD_MAP.values())
        required = {
            "w2s[0].box1_wages",
            "w2s[0].box2_federal_income_tax_withheld",
            "w2s[0].box3_social_security_wages",
            "w2s[0].box4_social_security_tax_withheld",
            "w2s[0].box5_medicare_wages",
            "w2s[0].box6_medicare_tax_withheld",
            "w2s[0].box7_social_security_tips",
            "w2s[0].box8_allocated_tips",
            "w2s[0].box10_dependent_care_benefits",
            "w2s[0].box11_nonqualified_plans",
        }
        missing = required - canonical_paths
        assert not missing, f"missing canonical paths in W2_AZURE_FIELD_MAP: {missing}"

    def test_state_tax_infos_not_in_scalar_map(self):
        # StateTaxInfos is a list and must be handled in ingest(), not via
        # the scalar map. Pin this so nobody accidentally adds it.
        assert "StateTaxInfos" not in W2_AZURE_FIELD_MAP
        assert not any("state_rows" in p for p in W2_AZURE_FIELD_MAP.values())


# ---------------------------------------------------------------------------
# Graceful behavior when credentials are missing
# ---------------------------------------------------------------------------


class TestW2AzureIngesterGracefulMissingCreds:
    def test_can_handle_false_without_credentials(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ENV_ENDPOINT, raising=False)
        monkeypatch.delenv(ENV_KEY, raising=False)

        p = tmp_path / "scanned_w2.pdf"
        p.write_bytes(b"")

        assert not W2AzureIngester().can_handle(p)

    def test_can_handle_false_without_credentials_even_for_image(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv(ENV_ENDPOINT, raising=False)
        monkeypatch.delenv(ENV_KEY, raising=False)

        p = tmp_path / "photo_of_w2.jpg"
        p.write_bytes(b"")

        assert not W2AzureIngester().can_handle(p)

    def test_ingest_returns_failure_not_raise_without_credentials(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv(ENV_ENDPOINT, raising=False)
        monkeypatch.delenv(ENV_KEY, raising=False)

        p = tmp_path / "scanned_w2.pdf"
        p.write_bytes(b"")

        # Must NOT raise — a missing-credentials ingester degrades gracefully.
        result = W2AzureIngester().ingest(p)

        assert result.success is False
        assert result.ingester_name == "w2_azure"
        assert result.source_path == p

    def test_ingest_error_message_cites_credentials(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ENV_ENDPOINT, raising=False)
        monkeypatch.delenv(ENV_KEY, raising=False)

        p = tmp_path / "scanned_w2.pdf"
        p.write_bytes(b"")

        result = W2AzureIngester().ingest(p)
        error = (result.error or "").lower()

        # Must mention that credentials are missing and which env vars to set.
        assert "credentials" in error
        assert ENV_ENDPOINT.lower() in error
        assert ENV_KEY.lower() in error

    def test_ingest_failure_result_still_has_w2_document_kind(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv(ENV_ENDPOINT, raising=False)
        monkeypatch.delenv(ENV_KEY, raising=False)

        p = tmp_path / "scanned_w2.pdf"
        p.write_bytes(b"")

        result = W2AzureIngester().ingest(p)
        # Even on failure, we know this ingester only handles W-2s, so the
        # partial carries that kind for downstream audit/logging.
        assert result.partial.document_kind == DocumentKind.FORM_W2


# ---------------------------------------------------------------------------
# Behavior when credentials ARE set (still no real API call)
# ---------------------------------------------------------------------------


class TestW2AzureIngesterWithFakeCredentials:
    """When env vars are set but no real Azure backend exists.

    We exercise ``can_handle()`` — which must now return True for supported
    extensions — and verify the ingester doesn't over-accept obviously-wrong
    file types. ``ingest()`` itself talks to the network and is covered by
    the skip-marked tests below.
    """

    @pytest.fixture
    def fake_creds(self, monkeypatch):
        monkeypatch.setenv(ENV_ENDPOINT, "https://fake.cognitiveservices.azure.com/")
        monkeypatch.setenv(ENV_KEY, "fake-key-not-real")

    @pytest.mark.parametrize(
        "suffix",
        [".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".heif"],
    )
    def test_can_handle_supported_suffixes(self, tmp_path, fake_creds, suffix):
        p = tmp_path / f"w2{suffix}"
        p.write_bytes(b"")
        assert W2AzureIngester().can_handle(p)

    @pytest.mark.parametrize(
        "suffix",
        [".txt", ".csv", ".xml", ".docx", ".zip"],
    )
    def test_can_handle_rejects_unsupported_suffixes(
        self, tmp_path, fake_creds, suffix
    ):
        p = tmp_path / f"w2{suffix}"
        p.write_bytes(b"")
        assert not W2AzureIngester().can_handle(p)


# ---------------------------------------------------------------------------
# Real-call test (skipped without credentials)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not azure_credentials_configured(),
    reason="AZURE_DOC_INTEL_ENDPOINT / AZURE_DOC_INTEL_KEY not set; skipping live Azure call",
)
class TestW2AzureIngesterLive:
    """Live-API smoke test. Only runs when credentials are present.

    This exists so that in a dev environment with credentials set, we still
    exercise the happy path against a tiny fixture. It's intentionally lenient
    — a blank PDF will produce zero extracted fields, but the ingester should
    still return ``success=True`` (possibly with a warning) rather than raise.
    """

    def test_ingest_blank_pdf_does_not_raise(self, tmp_path):
        from reportlab.pdfgen import canvas

        p = tmp_path / "blank.pdf"
        c = canvas.Canvas(str(p))
        c.drawString(50, 750, "")
        c.save()

        result = W2AzureIngester().ingest(p)
        # We don't assert on success because Azure may reject a blank page,
        # but we DO assert the ingester returned an IngestResult rather than
        # raising.
        assert result.ingester_name == "w2_azure"
        assert result.source_path == p
