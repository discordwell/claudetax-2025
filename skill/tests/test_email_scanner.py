"""Tests for skill.scripts.email_scanner."""
from __future__ import annotations

import base64
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from skill.scripts.email_scanner import (
    DownloadedAttachment,
    ScanResult,
    _TAX_DOC_QUERIES,
    _TAX_FILENAME_PATTERNS,
    _extract_header,
    _is_tax_filename,
    scan_gmail,
)


# ---------------------------------------------------------------------------
# Unit tests: _extract_header
# ---------------------------------------------------------------------------


class TestExtractHeader:
    def test_finds_subject(self):
        headers = [
            {"name": "From", "value": "employer@co.com"},
            {"name": "Subject", "value": "Your W-2 is ready"},
        ]
        assert _extract_header(headers, "Subject") == "Your W-2 is ready"

    def test_case_insensitive(self):
        headers = [{"name": "SUBJECT", "value": "Tax doc"}]
        assert _extract_header(headers, "subject") == "Tax doc"

    def test_missing_header(self):
        headers = [{"name": "From", "value": "a@b.com"}]
        assert _extract_header(headers, "Subject") == ""

    def test_empty_headers(self):
        assert _extract_header([], "From") == ""


# ---------------------------------------------------------------------------
# Unit tests: _is_tax_filename
# ---------------------------------------------------------------------------


class TestIsTaxFilename:
    @pytest.mark.parametrize(
        "filename",
        [
            "W-2_2025.pdf",
            "w2_employer.pdf",
            "1099-INT_chase.pdf",
            "1099div.pdf",
            "1098_mortgage.pdf",
            "1095-A_marketplace.pdf",
            "SSA-1099_2025.pdf",
            "K-1_partnership.pdf",
            "tax_statement_2025.pdf",
            "wage_report.pdf",
            "Tax_Document_Final.pdf",
        ],
    )
    def test_recognizes_tax_filenames(self, filename: str):
        assert _is_tax_filename(filename), f"Should recognize {filename}"

    @pytest.mark.parametrize(
        "filename",
        [
            "invoice_2025.pdf",
            "report.pdf",
            "photo.jpg",
            "contract_final.pdf",
            "resume_2025.pdf",
        ],
    )
    def test_rejects_non_tax_filenames(self, filename: str):
        assert not _is_tax_filename(filename), f"Should reject {filename}"


# ---------------------------------------------------------------------------
# Unit tests: query coverage
# ---------------------------------------------------------------------------


class TestQueryCoverage:
    def test_all_form_families_covered(self):
        all_queries = " ".join(_TAX_DOC_QUERIES.values()).lower()
        assert "w-2" in all_queries
        assert "1099" in all_queries
        assert "1098" in all_queries
        assert "1095" in all_queries
        assert "ssa" in all_queries
        assert "k-1" in all_queries

    def test_queries_have_attachment_filter(self):
        for name, query in _TAX_DOC_QUERIES.items():
            assert "has:attachment" in query, (
                f"Query {name!r} must include has:attachment"
            )


# ---------------------------------------------------------------------------
# Unit tests: ScanResult / DownloadedAttachment
# ---------------------------------------------------------------------------


class TestDataClasses:
    def test_scan_result_defaults(self):
        r = ScanResult(query_used="test", messages_found=0)
        assert r.attachments_downloaded == []
        assert r.skipped_non_pdf == 0
        assert r.skipped_non_tax == 0
        assert r.errors == []

    def test_downloaded_attachment_fields(self):
        att = DownloadedAttachment(
            message_id="abc",
            subject="Your W-2",
            sender="hr@company.com",
            date="Mon, 1 Feb 2026",
            filename="W-2_2025.pdf",
            saved_path=Path("/tmp/W-2_2025.pdf"),
            size_bytes=45000,
        )
        assert att.filename == "W-2_2025.pdf"
        assert att.size_bytes == 45000


# ---------------------------------------------------------------------------
# Integration test: scan_gmail with mocked Gmail API
# ---------------------------------------------------------------------------


def _make_mock_service(messages: list[dict]) -> MagicMock:
    """Build a mock Gmail service with canned message list + get responses."""
    service = MagicMock()

    # messages().list() returns message IDs
    msg_ids = [{"id": m["id"]} for m in messages]
    service.users().messages().list().execute.return_value = {
        "messages": msg_ids
    }

    # messages().get() returns full message by ID
    def get_message(userId, id, format="full"):
        mock = MagicMock()
        for m in messages:
            if m["id"] == id:
                mock.execute.return_value = m
                return mock
        mock.execute.return_value = {"id": id, "payload": {"headers": [], "parts": []}}
        return mock

    service.users().messages().get = get_message

    # attachments().get() returns attachment data
    def get_attachment(userId, messageId, id):
        mock = MagicMock()
        mock.execute.return_value = {
            "data": base64.urlsafe_b64encode(b"%PDF-1.4 fake tax doc content").decode()
        }
        return mock

    service.users().messages().attachments().get = get_attachment

    return service


class TestScanGmailMocked:
    def test_downloads_w2_attachment(self, tmp_path: Path):
        fake_pdf_data = base64.urlsafe_b64encode(b"%PDF-1.4 fake W-2").decode()
        messages = [
            {
                "id": "msg001",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Your W-2 is ready"},
                        {"name": "From", "value": "hr@employer.com"},
                        {"name": "Date", "value": "Mon, 1 Feb 2026 10:00:00 -0800"},
                    ],
                    "parts": [
                        {
                            "filename": "W-2_2025.pdf",
                            "body": {"attachmentId": "att001"},
                        }
                    ],
                },
            }
        ]

        mock_service = _make_mock_service(messages)

        with patch(
            "skill.scripts.email_scanner._authenticate", return_value=mock_service
        ):
            result = scan_gmail(
                credentials_path=tmp_path / "fake_creds.json",
                output_dir=tmp_path / "downloads",
                tax_year=2025,
            )

        assert result.messages_found == 1
        assert len(result.attachments_downloaded) == 1
        att = result.attachments_downloaded[0]
        assert att.filename == "W-2_2025.pdf"
        assert att.subject == "Your W-2 is ready"
        assert att.sender == "hr@employer.com"
        assert att.saved_path.exists()
        assert att.size_bytes > 0

    def test_skips_non_pdf_attachments(self, tmp_path: Path):
        messages = [
            {
                "id": "msg002",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Tax info"},
                        {"name": "From", "value": "a@b.com"},
                        {"name": "Date", "value": "Tue, 2 Feb 2026"},
                    ],
                    "parts": [
                        {"filename": "photo.jpg", "body": {"attachmentId": "att"}},
                        {"filename": "1099-INT.pdf", "body": {"attachmentId": "att2"}},
                    ],
                },
            }
        ]

        mock_service = _make_mock_service(messages)
        with patch(
            "skill.scripts.email_scanner._authenticate", return_value=mock_service
        ):
            result = scan_gmail(
                credentials_path=tmp_path / "creds.json",
                output_dir=tmp_path / "dl",
                tax_year=2025,
            )

        assert len(result.attachments_downloaded) == 1
        assert result.skipped_non_pdf == 1
        assert result.attachments_downloaded[0].filename == "1099-INT.pdf"

    def test_filter_tax_only_skips_non_tax_pdfs(self, tmp_path: Path):
        messages = [
            {
                "id": "msg003",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Docs"},
                        {"name": "From", "value": "a@b.com"},
                        {"name": "Date", "value": "Wed, 3 Feb 2026"},
                    ],
                    "parts": [
                        {"filename": "invoice.pdf", "body": {"attachmentId": "att"}},
                        {"filename": "W-2_2025.pdf", "body": {"attachmentId": "att2"}},
                    ],
                },
            }
        ]

        mock_service = _make_mock_service(messages)
        with patch(
            "skill.scripts.email_scanner._authenticate", return_value=mock_service
        ):
            result = scan_gmail(
                credentials_path=tmp_path / "creds.json",
                output_dir=tmp_path / "dl",
                tax_year=2025,
                filter_tax_filenames=True,
            )

        assert len(result.attachments_downloaded) == 1
        assert result.skipped_non_tax == 1
        assert result.attachments_downloaded[0].filename == "W-2_2025.pdf"

    def test_deduplicates_filenames(self, tmp_path: Path):
        messages = [
            {
                "id": "msg004",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "W-2"},
                        {"name": "From", "value": "a@b.com"},
                        {"name": "Date", "value": "Thu, 4 Feb 2026"},
                    ],
                    "parts": [
                        {"filename": "W-2.pdf", "body": {"attachmentId": "att1"}},
                    ],
                },
            },
            {
                "id": "msg005",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "W-2 corrected"},
                        {"name": "From", "value": "a@b.com"},
                        {"name": "Date", "value": "Fri, 5 Feb 2026"},
                    ],
                    "parts": [
                        {"filename": "W-2.pdf", "body": {"attachmentId": "att2"}},
                    ],
                },
            },
        ]

        mock_service = _make_mock_service(messages)
        with patch(
            "skill.scripts.email_scanner._authenticate", return_value=mock_service
        ):
            result = scan_gmail(
                credentials_path=tmp_path / "creds.json",
                output_dir=tmp_path / "dl",
                tax_year=2025,
            )

        assert len(result.attachments_downloaded) == 2
        paths = {a.saved_path.name for a in result.attachments_downloaded}
        assert "W-2.pdf" in paths
        assert "W-2_1.pdf" in paths

    def test_no_messages_returns_empty(self, tmp_path: Path):
        service = MagicMock()
        service.users().messages().list().execute.return_value = {"messages": []}

        with patch(
            "skill.scripts.email_scanner._authenticate", return_value=service
        ):
            result = scan_gmail(
                credentials_path=tmp_path / "creds.json",
                output_dir=tmp_path / "dl",
                tax_year=2025,
            )

        assert result.messages_found == 0
        assert result.attachments_downloaded == []


# ---------------------------------------------------------------------------
# CLI integration test
# ---------------------------------------------------------------------------


class TestCLIScanEmail:
    def test_scan_email_subcommand_exists(self):
        from skill.scripts.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "scan-email",
            "--output", "/tmp/out",
        ])
        assert args.command == "scan-email"
        assert args.credentials == "~/.tax-prep/client_secret.json"
        assert args.tax_year == 2025
        assert args.filter_tax_only is False
        assert args.run_pipeline is False

    def test_scan_email_with_all_flags(self):
        from skill.scripts.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "scan-email",
            "--credentials", "creds.json",
            "--output", "/tmp/out",
            "--tax-year", "2024",
            "--filter-tax-only",
            "--run-pipeline",
            "--taxpayer-info", "tp.json",
            "--pipeline-output", "./results",
        ])
        assert args.tax_year == 2024
        assert args.filter_tax_only is True
        assert args.run_pipeline is True
        assert args.taxpayer_info == "tp.json"
        assert args.pipeline_output == "./results"

    def test_setup_gmail_subcommand_exists(self):
        from skill.scripts.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["setup-gmail"])
        assert args.command == "setup-gmail"
