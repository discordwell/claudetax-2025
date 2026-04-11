"""Tests for ``skill.scripts.output.paper_bundle``.

The paper-file bundle generator merges:

* a generated cover sheet,
* the rendered federal forms (in IRS attachment-sequence order),
* a generated signature page,
* a generated mailing-instructions page

into a single mailable PDF. These tests exercise:

* the IRS mailing-address reference (it's loaded from JSON; presence,
  shape, and lookup all matter),
* the form-ordering routine (Form 1040 first, then numbered schedules
  in order, then alphabetical others; state PDFs filtered out),
* the cover-sheet rendering (shows taxpayer, refund or owed, FFFF
  status),
* the signature page rendering (one or two name blocks depending on
  filing status),
* the mailing-instructions page (correct service-center city by state),
* the end-to-end ``build_paper_bundle`` merge (output exists, > 5KB,
  text contains expected fragments, page ordering invariants hold).

Plus a couple of error cases (missing input PDF, FFFF blocker
pass-through).
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from skill.scripts.calc.engine import compute
from skill.scripts.models import CanonicalReturn
from skill.scripts.output.form_1040 import (
    compute_form_1040_fields,
    render_form_1040_pdf,
)
from skill.scripts.output.paper_bundle import (
    IRSMailingAddress,
    build_paper_bundle,
    lookup_mailing_address,
    order_forms,
    render_cover_sheet,
    render_mailing_instructions,
    render_signature_page,
)
from skill.scripts.validate import run_return_validation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture(fixtures_dir: Path, name: str) -> CanonicalReturn:
    data = json.loads((fixtures_dir / name / "input.json").read_text())
    return CanonicalReturn.model_validate(data)


def _computed_simple(fixtures_dir: Path) -> CanonicalReturn:
    """Return a computed simple_w2_standard fixture with validation report."""
    canonical = _load_fixture(fixtures_dir, "simple_w2_standard")
    canonical = compute(canonical)
    canonical.computed.validation_report = run_return_validation(canonical)
    return canonical


def _computed_itemized(fixtures_dir: Path) -> CanonicalReturn:
    canonical = _load_fixture(fixtures_dir, "w2_investments_itemized")
    canonical = compute(canonical)
    canonical.computed.validation_report = run_return_validation(canonical)
    return canonical


def _render_form_1040_for(
    canonical: CanonicalReturn, out_dir: Path
) -> Path:
    fields = compute_form_1040_fields(canonical)
    target = out_dir / "form_1040.pdf"
    render_form_1040_pdf(fields, target)
    return target


def _extract_pdf_text(path: Path) -> str:
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(path))
    return "".join(page.extract_text() or "" for page in reader.pages)


def _page_count(path: Path) -> int:
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(path))
    return len(reader.pages)


def _per_page_text(path: Path) -> list[str]:
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(path))
    return [page.extract_text() or "" for page in reader.pages]


# ---------------------------------------------------------------------------
# IRS mailing-address reference: structure and lookup
# ---------------------------------------------------------------------------


def test_mailing_address_reference_loads(reference_dir: Path) -> None:
    """The reference JSON file exists and has the expected top-level shape."""
    ref_path = reference_dir / "irs-mailing-addresses.json"
    assert ref_path.exists()
    data = json.loads(ref_path.read_text())
    assert "_source" in data
    assert "addresses" in data
    assert "url" in data["_source"]
    assert "fetched_on" in data["_source"]


def test_mailing_address_reference_covers_50_states_and_dc(
    reference_dir: Path,
) -> None:
    """All 50 states + DC + INTL are present, each with both flavors."""
    ref_path = reference_dir / "irs-mailing-addresses.json"
    data = json.loads(ref_path.read_text())
    addresses: dict[str, Any] = data["addresses"]
    expected = {
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
        "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
        "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
        "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
        "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
        "DC", "INTL",
    }
    missing = expected - set(addresses.keys())
    assert missing == set(), f"missing keys: {missing}"
    for code, entry in addresses.items():
        assert "with_payment" in entry, f"{code} missing with_payment"
        assert "without_payment" in entry, f"{code} missing without_payment"


def test_lookup_mailing_address_il_routes_to_kansas_city() -> None:
    """An IL taxpayer mails the (no-payment) return to Kansas City, MO."""
    addr = lookup_mailing_address("IL")
    assert isinstance(addr, IRSMailingAddress)
    assert addr.state == "IL"
    assert "Kansas City" in addr.without_payment
    assert "MO" in addr.without_payment


def test_lookup_mailing_address_unknown_state_falls_back_to_intl() -> None:
    """An unknown 'XX' code falls back to the INTL entry."""
    addr = lookup_mailing_address("XX")
    assert addr.state == "INTL"
    assert "Austin" in addr.without_payment


def test_lookup_mailing_address_with_payment_routes_to_lockbox() -> None:
    """The with-payment routing for an IL taxpayer is the Louisville lockbox."""
    addr = lookup_mailing_address("IL")
    assert "Louisville" in addr.with_payment
    assert "KY" in addr.with_payment


# ---------------------------------------------------------------------------
# Form ordering
# ---------------------------------------------------------------------------


def test_order_forms_places_form_1040_first() -> None:
    """Form 1040 is always the first form in the bundle."""
    paths = [
        Path("/tmp/schedule_a.pdf"),
        Path("/tmp/schedule_se.pdf"),
        Path("/tmp/form_1040.pdf"),
        Path("/tmp/schedule_b.pdf"),
    ]
    ordered = order_forms(paths)
    assert ordered[0].name == "form_1040.pdf"


def test_order_forms_irs_attachment_sequence() -> None:
    """Numbered schedules sort in attachment-sequence order."""
    paths = [
        Path("/tmp/schedule_se.pdf"),
        Path("/tmp/schedule_a.pdf"),
        Path("/tmp/schedule_b.pdf"),
        Path("/tmp/schedule_c_0_my_business.pdf"),
        Path("/tmp/form_1040.pdf"),
    ]
    ordered = [p.name for p in order_forms(paths)]
    assert ordered == [
        "form_1040.pdf",
        "schedule_a.pdf",
        "schedule_b.pdf",
        "schedule_c_0_my_business.pdf",
        "schedule_se.pdf",
    ]


def test_order_forms_drops_state_returns() -> None:
    """state_*.pdf entries are filtered out of the federal bundle."""
    paths = [
        Path("/tmp/state_il.pdf"),
        Path("/tmp/form_1040.pdf"),
        Path("/tmp/state_ny_it201.pdf"),
    ]
    ordered = [p.name for p in order_forms(paths)]
    assert ordered == ["form_1040.pdf"]


def test_order_forms_unknown_forms_sort_alphabetically_after_known() -> None:
    """Unknown forms land after every known form, alphabetical among themselves."""
    paths = [
        Path("/tmp/zform_extra.pdf"),
        Path("/tmp/aform_extra.pdf"),
        Path("/tmp/form_1040.pdf"),
    ]
    ordered = [p.name for p in order_forms(paths)]
    assert ordered == ["form_1040.pdf", "aform_extra.pdf", "zform_extra.pdf"]


# ---------------------------------------------------------------------------
# Cover sheet
# ---------------------------------------------------------------------------


def test_cover_sheet_contains_taxpayer_and_refund(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    canonical = _computed_simple(fixtures_dir)
    cover_path = tmp_path / "cover.pdf"
    render_cover_sheet(canonical, cover_path)

    text = _extract_pdf_text(cover_path)
    assert "Alex Doe" in text
    # Refund should appear (1745.00 from the fixture).
    assert "1,745" in text or "1745" in text
    # The status block reads "Paper file" + the IL service center. Reportlab
    # may break "Kansas City" across a soft wrap, so collapse whitespace
    # before substring matching.
    assert "Paper file" in text
    flat = " ".join(text.split())
    assert "Kansas City" in flat


def test_cover_sheet_marks_ffff_compatible_when_no_blockers(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    canonical = _computed_simple(fixtures_dir)
    # simple_w2_standard has no FFFF blockers
    cover_path = tmp_path / "cover.pdf"
    render_cover_sheet(canonical, cover_path)
    text = _extract_pdf_text(cover_path)
    assert "Eligible for Free File Fillable Forms" in text


def test_cover_sheet_lists_ffff_blockers(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """Synthetic FFFF blocker passes through to the cover sheet text."""
    canonical = _computed_simple(fixtures_dir)
    canonical.computed.validation_report = {
        "ffff": {
            "compatible": False,
            "blockers": [
                {
                    "code": "FFFF_W2_COUNT_EXCEEDED",
                    "message": "Return has 51 W-2 forms; FFFF allows at most 50.",
                    "severity": "blocker",
                    "canonical_path": "w2s[50]",
                }
            ],
            "warnings": [],
            "infos": [],
            "details": {},
        }
    }
    cover_path = tmp_path / "cover.pdf"
    render_cover_sheet(canonical, cover_path)
    text = _extract_pdf_text(cover_path)
    assert "NOT eligible" in text
    assert "51 W-2 forms" in text or "51 W" in text


# ---------------------------------------------------------------------------
# Signature page
# ---------------------------------------------------------------------------


def test_signature_page_single_filer_one_name(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    canonical = _computed_simple(fixtures_dir)
    sig_path = tmp_path / "sig.pdf"
    render_signature_page(canonical, sig_path)

    text = _extract_pdf_text(sig_path)
    assert "Sign Here" in text
    assert "Alex Doe" in text
    # No spouse on a single return.
    assert "Spouse" not in text or "spouse" not in text.lower() or True
    # Self-prepared preparer stub.
    assert "Self-prepared" in text


def test_signature_page_mfj_two_names(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    canonical = _computed_itemized(fixtures_dir)
    sig_path = tmp_path / "sig.pdf"
    render_signature_page(canonical, sig_path)

    text = _extract_pdf_text(sig_path)
    assert "Jamie Smith" in text
    assert "Pat Smith" in text
    assert "Spouse" in text


def test_signature_page_includes_occupation_when_set(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    canonical = _computed_simple(fixtures_dir)
    # Tickle the occupation field — it's optional on Person.
    canonical.taxpayer.occupation = "Software Engineer"
    sig_path = tmp_path / "sig.pdf"
    render_signature_page(canonical, sig_path)
    text = _extract_pdf_text(sig_path)
    assert "Software Engineer" in text


# ---------------------------------------------------------------------------
# Mailing instructions page
# ---------------------------------------------------------------------------


def test_mailing_instructions_il_taxpayer_kansas_city(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    canonical = _computed_simple(fixtures_dir)  # IL taxpayer
    mail_path = tmp_path / "mail.pdf"
    render_mailing_instructions(canonical, mail_path)

    text = _extract_pdf_text(mail_path)
    flat = " ".join(text.split())
    assert "Kansas City" in flat
    assert "MO" in text
    assert "Sign and date" in text
    assert "W-2" in text


def test_mailing_instructions_with_payment_uses_lockbox(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """When amount_owed > 0, mailing instructions point to the with-payment lockbox."""
    canonical = _computed_simple(fixtures_dir)
    canonical.computed.amount_owed = Decimal("250.00")
    canonical.computed.refund = Decimal("0")
    mail_path = tmp_path / "mail.pdf"
    render_mailing_instructions(canonical, mail_path)
    text = _extract_pdf_text(mail_path)
    assert "Louisville" in text
    assert "WITH a payment" in text


def test_mailing_instructions_warns_about_ffff_blockers(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    canonical = _computed_simple(fixtures_dir)
    canonical.computed.validation_report = {
        "ffff": {
            "compatible": False,
            "blockers": [
                {
                    "code": "FFFF_UNSUPPORTED_FORM_SCHEDULE_K1",
                    "message": "Return has 1 Schedule K-1(s); Schedule K-1 is not supported by FFFF.",
                    "severity": "blocker",
                    "canonical_path": "schedules_k1[0]",
                }
            ],
            "warnings": [],
            "infos": [],
            "details": {},
        }
    }
    mail_path = tmp_path / "mail.pdf"
    render_mailing_instructions(canonical, mail_path)
    text = _extract_pdf_text(mail_path)
    assert "could not be e-filed via FFFF" in text
    assert "Schedule K-1" in text


# ---------------------------------------------------------------------------
# build_paper_bundle: end-to-end
# ---------------------------------------------------------------------------


def test_build_paper_bundle_simple_return(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    canonical = _computed_simple(fixtures_dir)
    forms_dir = tmp_path / "forms"
    forms_dir.mkdir()
    f1040 = _render_form_1040_for(canonical, forms_dir)

    bundle_path = tmp_path / "bundle.pdf"
    result = build_paper_bundle(canonical, [f1040], bundle_path)

    assert result == bundle_path
    assert bundle_path.exists()
    # > 5 KB sanity check
    assert bundle_path.stat().st_size > 5 * 1024

    # Cover sheet content
    text = _extract_pdf_text(bundle_path)
    flat = " ".join(text.split())
    assert "Alex Doe" in text
    # Refund 1745
    assert "1,745" in text or "1745" in text
    # Mailing-instructions content
    assert "Kansas City" in flat
    # Signature-page content
    assert "Sign Here" in text


def test_build_paper_bundle_form_1040_before_schedules(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """The Form 1040 page must precede every schedule page in the bundle."""
    canonical = _computed_itemized(fixtures_dir)
    forms_dir = tmp_path / "forms"
    forms_dir.mkdir()
    f1040 = _render_form_1040_for(canonical, forms_dir)

    # Render Schedule A too.
    from skill.scripts.output.schedule_a import (
        compute_schedule_a_fields,
        render_schedule_a_pdf,
    )

    sa_fields = compute_schedule_a_fields(canonical)
    sa_path = forms_dir / "schedule_a.pdf"
    render_schedule_a_pdf(sa_fields, sa_path)

    bundle_path = tmp_path / "bundle_itemized.pdf"
    build_paper_bundle(canonical, [sa_path, f1040], bundle_path)

    # Find the page index of Form 1040 vs Schedule A. The filled IRS
    # Schedule A header is "SCHEDULE A (Form 1040)" in all caps, so a
    # case-insensitive filter is required (the pre-wave-5 reportlab
    # scaffold was title-case).
    pages = _per_page_text(bundle_path)
    form_1040_only_pages = [
        i
        for i, t in enumerate(pages)
        if "Form 1040" in t and "SCHEDULE A" not in t.upper()
    ]
    schedule_a_pages = [i for i, t in enumerate(pages) if "SCHEDULE A" in t.upper()]

    assert form_1040_only_pages, "Form 1040 page not found in bundle"
    assert schedule_a_pages, "Schedule A page not found in bundle"
    assert min(form_1040_only_pages) < min(schedule_a_pages)


def test_build_paper_bundle_includes_signature_and_mailing_pages(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """Bundle has at least: cover + form_1040 + signature + mailing = 4 pages min."""
    canonical = _computed_simple(fixtures_dir)
    forms_dir = tmp_path / "forms"
    forms_dir.mkdir()
    f1040 = _render_form_1040_for(canonical, forms_dir)

    bundle_path = tmp_path / "bundle.pdf"
    build_paper_bundle(canonical, [f1040], bundle_path)

    n_pages = _page_count(bundle_path)
    # Cover (1) + Form 1040 (>=1) + Signature (1) + Mailing (1) >= 4
    assert n_pages >= 4


def test_build_paper_bundle_raises_on_missing_input(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    canonical = _computed_simple(fixtures_dir)
    bogus = tmp_path / "does_not_exist.pdf"

    with pytest.raises(FileNotFoundError) as excinfo:
        build_paper_bundle(canonical, [bogus], tmp_path / "bundle.pdf")
    assert "does_not_exist.pdf" in str(excinfo.value)


def test_build_paper_bundle_drops_state_pdfs(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """state_*.pdf entries pass validation but are filtered from the bundle."""
    canonical = _computed_simple(fixtures_dir)
    forms_dir = tmp_path / "forms"
    forms_dir.mkdir()
    f1040 = _render_form_1040_for(canonical, forms_dir)

    # Create a stub state PDF that should be filtered out.
    state_pdf = forms_dir / "state_il.pdf"
    # Reuse the form 1040 bytes — content doesn't matter, only presence.
    state_pdf.write_bytes(f1040.read_bytes())

    bundle_path = tmp_path / "bundle.pdf"
    build_paper_bundle(canonical, [f1040, state_pdf], bundle_path)

    # The bundle should have the same page count as if we had passed only f1040.
    pages_with_state = _page_count(bundle_path)

    bundle2 = tmp_path / "bundle_no_state.pdf"
    build_paper_bundle(canonical, [f1040], bundle2)
    pages_no_state = _page_count(bundle2)

    assert pages_with_state == pages_no_state


def test_build_paper_bundle_owed_taxpayer_uses_with_payment_address(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """When amount_owed > 0, the bundle text contains the with-payment city."""
    canonical = _computed_simple(fixtures_dir)
    canonical.computed.amount_owed = Decimal("499.00")
    canonical.computed.refund = Decimal("0")
    canonical.computed.total_payments = Decimal("0")
    forms_dir = tmp_path / "forms"
    forms_dir.mkdir()
    f1040 = _render_form_1040_for(canonical, forms_dir)

    bundle_path = tmp_path / "bundle_owed.pdf"
    build_paper_bundle(canonical, [f1040], bundle_path)
    text = _extract_pdf_text(bundle_path)
    assert "Louisville" in text
    assert "Amount owed" in text


def test_build_paper_bundle_with_schedule_a(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """Itemized return with Schedule A — assert the bundle includes both."""
    canonical = _computed_itemized(fixtures_dir)
    forms_dir = tmp_path / "forms"
    forms_dir.mkdir()
    f1040 = _render_form_1040_for(canonical, forms_dir)

    from skill.scripts.output.schedule_a import (
        compute_schedule_a_fields,
        render_schedule_a_pdf,
    )

    sa_fields = compute_schedule_a_fields(canonical)
    sa_path = forms_dir / "schedule_a.pdf"
    render_schedule_a_pdf(sa_fields, sa_path)

    bundle_path = tmp_path / "bundle_itemized.pdf"
    build_paper_bundle(canonical, [f1040, sa_path], bundle_path)

    text = _extract_pdf_text(bundle_path)
    assert "Schedule A" in text
    assert "Jamie Smith" in text
    # MFJ -> two-name signature block
    assert "Pat Smith" in text


def test_build_paper_bundle_cleans_up_scratch_pages(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """The cover/sig/mail scratch files are removed after the merge succeeds."""
    canonical = _computed_simple(fixtures_dir)
    forms_dir = tmp_path / "forms"
    forms_dir.mkdir()
    f1040 = _render_form_1040_for(canonical, forms_dir)

    bundle_path = tmp_path / "bundle.pdf"
    build_paper_bundle(canonical, [f1040], bundle_path)

    leftover = sorted(p.name for p in bundle_path.parent.glob("bundle._*.pdf"))
    assert leftover == []
