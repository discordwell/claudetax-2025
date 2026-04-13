"""Gmail tax document scanner.

Searches a Gmail account for emails containing tax document PDF
attachments (W-2, 1099, 1098, 1095-A, SSA-1099, K-1), downloads
them to a staging directory, and optionally runs the full pipeline.

Authentication uses OAuth2 via Google's API client. On first run,
opens a browser-based consent flow and caches the token at
``~/.tax-prep/gmail_token.json``. Subsequent runs reuse the cached
token silently.

Usage (programmatic)::

    from skill.scripts.email_scanner import scan_gmail

    downloaded = scan_gmail(
        credentials_path=Path("client_secret.json"),
        output_dir=Path("./user_pdfs_2025"),
        tax_year=2025,
    )

Usage (CLI)::

    tax-prep scan-email \\
        --credentials client_secret.json \\
        --output ./user_pdfs_2025 \\
        --tax-year 2025
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TOKEN_DIR = Path.home() / ".tax-prep"
_TOKEN_FILE = _TOKEN_DIR / "gmail_token.json"
_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Tax document search queries. Each targets a specific form family.
# Gmail search supports OR but not regex — keep patterns simple.
_TAX_DOC_QUERIES: dict[str, str] = {
    "w2": 'has:attachment (W-2 OR W2 OR "wage and tax" OR "wage & tax")',
    "1099": 'has:attachment (1099-INT OR 1099-DIV OR 1099-B OR 1099-NEC OR 1099-MISC OR 1099-K OR 1099-R OR 1099-G OR "1099-MISC" OR "1099-K")',
    "1098": 'has:attachment (1098 OR "mortgage interest" OR "student loan interest" OR "tuition statement" OR 1098-E OR 1098-T)',
    "1095": 'has:attachment (1095-A OR "health insurance marketplace" OR "marketplace statement")',
    "ssa": 'has:attachment (SSA-1099 OR "social security benefit")',
    "k1": 'has:attachment (K-1 OR "schedule K" OR "partner share")',
    "broad": 'has:attachment ("tax document" OR "tax statement" OR "tax form" OR "your tax" OR "annual tax")',
}

# Filename patterns that indicate a tax document PDF.
_TAX_FILENAME_PATTERNS = [
    re.compile(r"w-?2", re.I),
    re.compile(r"1099", re.I),
    re.compile(r"1098", re.I),
    re.compile(r"1095", re.I),
    re.compile(r"ssa.?1099", re.I),
    re.compile(r"k-?1", re.I),
    re.compile(r"tax.?(form|doc|statement)", re.I),
    re.compile(r"wage", re.I),
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DownloadedAttachment:
    """Record of a single downloaded PDF attachment."""

    message_id: str
    subject: str
    sender: str
    date: str
    filename: str
    saved_path: Path
    size_bytes: int


@dataclass
class ScanResult:
    """Result of a Gmail tax document scan."""

    query_used: str
    messages_found: int
    attachments_downloaded: list[DownloadedAttachment] = field(default_factory=list)
    skipped_non_pdf: int = 0
    skipped_non_tax: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Gmail API helpers
# ---------------------------------------------------------------------------


def _authenticate(credentials_path: Path) -> Any:
    """Authenticate with Gmail API using OAuth2.

    On first run, opens a browser consent flow. Caches the token
    at ~/.tax-prep/gmail_token.json for subsequent silent reuse.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    _TOKEN_DIR.mkdir(parents=True, exist_ok=True)

    if _TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), _SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_path), _SCOPES
            )
            creds = flow.run_local_server(port=0)
        _TOKEN_FILE.write_text(creds.to_json())

    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=creds)


def _search_messages(service: Any, query: str, max_results: int = 100) -> list[str]:
    """Search Gmail and return message IDs."""
    results = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )
    messages = results.get("messages", [])
    return [m["id"] for m in messages]


def _get_message(service: Any, msg_id: str) -> dict:
    """Fetch a full message by ID."""
    return (
        service.users()
        .messages()
        .get(userId="me", id=msg_id, format="full")
        .execute()
    )


def _extract_header(headers: list[dict], name: str) -> str:
    """Extract a header value from a Gmail message payload."""
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _is_tax_filename(filename: str) -> bool:
    """Check if a filename looks like a tax document."""
    for pat in _TAX_FILENAME_PATTERNS:
        if pat.search(filename):
            return True
    return False


def _download_attachments(
    service: Any,
    msg_id: str,
    payload: dict,
    output_dir: Path,
    filter_tax_only: bool = False,
) -> tuple[list[tuple[str, Path, int]], int, int]:
    """Download PDF attachments from a message.

    Returns (downloaded_list, skipped_non_pdf, skipped_non_tax).
    Each item in downloaded_list is (filename, saved_path, size_bytes).
    """
    downloaded: list[tuple[str, Path, int]] = []
    skipped_non_pdf = 0
    skipped_non_tax = 0

    parts = payload.get("parts", [])
    if not parts and payload.get("filename"):
        parts = [payload]

    for part in parts:
        # Recurse into multipart
        if part.get("parts"):
            sub_dl, sub_np, sub_nt = _download_attachments(
                service, msg_id, part, output_dir, filter_tax_only
            )
            downloaded.extend(sub_dl)
            skipped_non_pdf += sub_np
            skipped_non_tax += sub_nt
            continue

        filename = part.get("filename", "")
        if not filename:
            continue

        # Only PDFs
        if not filename.lower().endswith(".pdf"):
            skipped_non_pdf += 1
            continue

        # Optional tax-filename filter
        if filter_tax_only and not _is_tax_filename(filename):
            skipped_non_tax += 1
            continue

        # Download the attachment data
        body = part.get("body", {})
        att_id = body.get("attachmentId")
        if att_id:
            att = (
                service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=msg_id, id=att_id)
                .execute()
            )
            data = base64.urlsafe_b64decode(att["data"])
        elif body.get("data"):
            data = base64.urlsafe_b64decode(body["data"])
        else:
            continue

        # Deduplicate filenames
        save_path = output_dir / filename
        counter = 1
        while save_path.exists():
            stem = Path(filename).stem
            suffix = Path(filename).suffix
            save_path = output_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        save_path.write_bytes(data)
        downloaded.append((filename, save_path, len(data)))

    return downloaded, skipped_non_pdf, skipped_non_tax


# ---------------------------------------------------------------------------
# Main scan function
# ---------------------------------------------------------------------------


def scan_gmail(
    credentials_path: Path,
    output_dir: Path,
    tax_year: int = 2025,
    *,
    queries: dict[str, str] | None = None,
    filter_tax_filenames: bool = False,
    max_results_per_query: int = 50,
) -> ScanResult:
    """Scan Gmail for tax document PDFs and download them.

    Parameters
    ----------
    credentials_path
        Path to the Google OAuth2 client_secret JSON file.
    output_dir
        Directory to save downloaded PDFs.
    tax_year
        Tax year to search for. Documents for TY2025 typically
        arrive between Dec 2025 and Mar 2026.
    queries
        Custom search queries. Defaults to the built-in tax
        document queries.
    filter_tax_filenames
        When True, only downloads PDFs whose filenames match
        tax document patterns. When False (default), downloads
        all PDF attachments from matching emails.
    max_results_per_query
        Maximum messages to fetch per query.

    Returns
    -------
    ScanResult
        Summary of the scan including downloaded files and any errors.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if queries is None:
        queries = _TAX_DOC_QUERIES

    # Add date filter: tax docs for TY2025 arrive Dec 2025 - Apr 2026
    date_filter = f" after:{tax_year}/12/01"

    service = _authenticate(credentials_path)

    # Deduplicate message IDs across queries
    seen_ids: set[str] = set()
    all_msg_ids: list[str] = []
    combined_query_parts: list[str] = []

    for name, query in queries.items():
        full_query = query + date_filter
        combined_query_parts.append(f"({query})")
        try:
            ids = _search_messages(service, full_query, max_results_per_query)
            for mid in ids:
                if mid not in seen_ids:
                    seen_ids.add(mid)
                    all_msg_ids.append(mid)
        except Exception as exc:
            pass  # Individual query failures are non-fatal

    result = ScanResult(
        query_used=" OR ".join(combined_query_parts) + date_filter,
        messages_found=len(all_msg_ids),
    )

    for msg_id in all_msg_ids:
        try:
            msg = _get_message(service, msg_id)
            payload = msg.get("payload", {})
            headers = payload.get("headers", [])

            subject = _extract_header(headers, "Subject")
            sender = _extract_header(headers, "From")
            date = _extract_header(headers, "Date")

            downloaded, skip_np, skip_nt = _download_attachments(
                service, msg_id, payload, output_dir, filter_tax_filenames
            )
            result.skipped_non_pdf += skip_np
            result.skipped_non_tax += skip_nt

            for filename, saved_path, size_bytes in downloaded:
                result.attachments_downloaded.append(
                    DownloadedAttachment(
                        message_id=msg_id,
                        subject=subject,
                        sender=sender,
                        date=date,
                        filename=filename,
                        saved_path=saved_path,
                        size_bytes=size_bytes,
                    )
                )
        except Exception as exc:
            result.errors.append(f"Error processing message {msg_id}: {exc}")

    return result
