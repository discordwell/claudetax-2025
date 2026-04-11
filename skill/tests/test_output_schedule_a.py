"""Tests for skill.scripts.output.schedule_a — Schedule A PDF scaffold.

Two layers under test:

* Layer 1 — ``compute_schedule_a_fields``: assert that values on the
  returned dataclass match the expected Schedule A line numbers
  for a variety of inputs, with special attention to the SALT cap.
  These tests do NOT duplicate the engine's golden diff — they only
  check that the canonical ItemizedDeductions block is routed onto
  Schedule A line names and that the cap is applied.

* Layer 2 — ``render_schedule_a_pdf``: write a scaffold PDF, reopen it
  with pypdf, and assert the extracted text contains the header and a
  numeric value.

The SALT cap tests are the center of gravity for this module:

* MFJ with $15k state + local income tax and $0 real estate / personal
  property tax: line 5d = 15000, line 5e = 10000.
* MFJ with $15k state income + $8k real estate: line 5d = 23000, line
  5e = 10000 (matches the shipped w2_investments_itemized fixture).
* MFS with the same $15k raw SALT: line 5d = 15000, line 5e = 5000.
* Single with $8k SALT (under cap): line 5e = 8000, no cap applied.
* Elect sales tax over income tax: line 5a reads sales tax.

Authority:
* IRS 2024 Instructions for Schedule A (Form 1040) — line layout.
* IRC §164(b)(6) — SALT cap; TCJA §11042, made permanent by OBBBA.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.calc.engine import compute, itemized_total_capped
from skill.scripts.models import (
    Address,
    CanonicalReturn,
    FilingStatus,
    ItemizedDeductions,
    Person,
)
from skill.scripts.output.schedule_a import (
    SALT_CAP_MFS,
    SALT_CAP_NORMAL,
    ScheduleAFields,
    compute_schedule_a_fields,
    render_schedule_a_pdf,
)


def _load_fixture(fixtures_dir: Path, name: str) -> CanonicalReturn:
    data = json.loads((fixtures_dir / name / "input.json").read_text())
    return CanonicalReturn.model_validate(data)


def _person(first: str = "Test", last: str = "Payer") -> Person:
    return Person(
        first_name=first,
        last_name=last,
        ssn="111-22-3333",
        date_of_birth="1990-01-01",
        is_blind=False,
        is_age_65_or_older=False,
    )


def _address() -> Address:
    return Address(street1="1 Test", city="Springfield", state="IL", zip="62701")


def _minimal_return(
    *,
    filing_status: FilingStatus = FilingStatus.SINGLE,
    itemized: ItemizedDeductions | None = None,
    taxpayer: Person | None = None,
    spouse: Person | None = None,
) -> CanonicalReturn:
    """Build a minimal CanonicalReturn for isolated Layer 1 tests.

    We don't run it through ``compute`` so AGI is 0; that's fine — Layer
    1 reads AGI only for the informational medical-floor worksheet.

    If filing_status is MFJ or MFS and no spouse is supplied, we
    auto-attach a default spouse to satisfy the CanonicalReturn
    validator.
    """
    needs_spouse = filing_status in (FilingStatus.MFJ, FilingStatus.MFS)
    if needs_spouse and spouse is None:
        spouse = _person("Spouse", "Two")
    return CanonicalReturn.model_validate(
        {
            "schema_version": "0.1.0",
            "tax_year": 2025,
            "filing_status": filing_status.value,
            "taxpayer": (taxpayer or _person()).model_dump(mode="json"),
            "spouse": spouse.model_dump(mode="json") if spouse else None,
            "address": _address().model_dump(mode="json"),
            "itemize_deductions": itemized is not None,
            "itemized": itemized.model_dump(mode="json") if itemized else None,
        }
    )


# ---------------------------------------------------------------------------
# Layer 1: SALT cap is the center of gravity
# ---------------------------------------------------------------------------


def test_salt_cap_mfj_15k_income_only_caps_to_10k() -> None:
    """MFJ with $15k state+local income tax, no real estate / PP tax.

    Line 5d = 15000 (raw SALT subtotal).
    Line 5e = 10000 (capped to $10k MFJ).
    Line 5e_cap_applied = $10,000.
    Line 7 = 10000 + 0 = 10000.
    """
    it = ItemizedDeductions(state_and_local_income_tax=Decimal("15000"))
    r = _minimal_return(filing_status=FilingStatus.MFJ, itemized=it, spouse=_person("Spouse", "Two"))
    fields = compute_schedule_a_fields(r)

    assert fields.line_5a_state_and_local_taxes == Decimal("15000")
    assert fields.line_5b_real_estate_taxes == Decimal("0")
    assert fields.line_5c_personal_property_taxes == Decimal("0")
    assert fields.line_5d_salt_subtotal == Decimal("15000")
    assert fields.line_5e_salt_capped == Decimal("10000")
    assert fields.line_5e_salt_cap_applied == SALT_CAP_NORMAL == Decimal("10000")
    assert fields.line_7_total_taxes == Decimal("10000")


def test_salt_cap_mfs_15k_caps_to_5k() -> None:
    """MFS with $15k raw SALT caps to $5,000 (half the normal cap).

    Authority: IRC §164(b)(6) — MFS half-cap is the statutory rule.
    """
    it = ItemizedDeductions(state_and_local_income_tax=Decimal("15000"))
    r = _minimal_return(filing_status=FilingStatus.MFS, itemized=it)
    fields = compute_schedule_a_fields(r)

    assert fields.line_5d_salt_subtotal == Decimal("15000")
    assert fields.line_5e_salt_capped == Decimal("5000")
    assert fields.line_5e_salt_cap_applied == SALT_CAP_MFS == Decimal("5000")
    # Full line 7 just carries the capped SALT + line 6 (0 here)
    assert fields.line_7_total_taxes == Decimal("5000")


def test_salt_cap_mfj_mixed_raw_23k_caps_to_10k() -> None:
    """MFJ with $15k state income + $8k real estate caps to $10k.

    This mirrors the shipped ``w2_investments_itemized`` fixture:
    raw SALT 23000 -> line 5e 10000.
    """
    it = ItemizedDeductions(
        state_and_local_income_tax=Decimal("15000"),
        real_estate_tax=Decimal("8000"),
    )
    r = _minimal_return(filing_status=FilingStatus.MFJ, itemized=it)
    fields = compute_schedule_a_fields(r)

    assert fields.line_5a_state_and_local_taxes == Decimal("15000")
    assert fields.line_5b_real_estate_taxes == Decimal("8000")
    assert fields.line_5d_salt_subtotal == Decimal("23000")
    assert fields.line_5e_salt_capped == Decimal("10000")


def test_salt_under_cap_is_not_reduced() -> None:
    """Single with $8k total SALT — under the $10k cap, keep raw."""
    it = ItemizedDeductions(
        state_and_local_income_tax=Decimal("5000"),
        real_estate_tax=Decimal("2000"),
        personal_property_tax=Decimal("1000"),
    )
    r = _minimal_return(filing_status=FilingStatus.SINGLE, itemized=it)
    fields = compute_schedule_a_fields(r)

    assert fields.line_5d_salt_subtotal == Decimal("8000")
    # Under the cap, line 5e equals line 5d exactly.
    assert fields.line_5e_salt_capped == Decimal("8000")
    assert fields.line_5e_salt_cap_applied == SALT_CAP_NORMAL


def test_salt_elect_sales_tax_reads_sales_line() -> None:
    """If the taxpayer elects sales tax, line 5a reads sales, not income."""
    it = ItemizedDeductions(
        state_and_local_income_tax=Decimal("5000"),
        state_and_local_sales_tax=Decimal("6500"),
        elect_sales_tax_over_income_tax=True,
        real_estate_tax=Decimal("1000"),
    )
    r = _minimal_return(filing_status=FilingStatus.SINGLE, itemized=it)
    fields = compute_schedule_a_fields(r)

    # Sales over income: 6500 + 1000 = 7500, under cap, passes through.
    assert fields.line_5a_state_and_local_taxes == Decimal("6500")
    assert fields.line_5a_elected_sales_tax is True
    assert fields.line_5d_salt_subtotal == Decimal("7500")
    assert fields.line_5e_salt_capped == Decimal("7500")


def test_line17_matches_engine_itemized_total_capped_zero_medical() -> None:
    """Cross-check: with medical=0, line 17 equals engine's capped total.

    With zero medical the 7.5% floor is a no-op on both sides, so line 17
    and ``itemized_total_capped`` agree. See
    ``test_line17_matches_engine_with_medical_floor`` for the non-zero-
    medical case where both sides apply the floor identically (CP8-A
    fix — they used to diverge until the engine was taught to apply the
    floor too).
    """
    it = ItemizedDeductions(
        state_and_local_income_tax=Decimal("15000"),
        real_estate_tax=Decimal("8000"),
        home_mortgage_interest=Decimal("20000"),
        gifts_to_charity_cash=Decimal("5000"),
    )
    r = _minimal_return(filing_status=FilingStatus.MFJ, itemized=it)
    fields = compute_schedule_a_fields(r)

    # Pass AGI=0 explicitly — no medical to floor, so any AGI gives the
    # same answer. Matches the renderer's no-AGI path on this fixture.
    engine_total = itemized_total_capped(it, FilingStatus.MFJ, Decimal("0"))
    # engine_total breakdown:
    #   medical 0 + SALT_capped 10000 + interest 20000 + charity 5000 = 35000
    assert engine_total == Decimal("35000")

    # With zero medical, our line 17 total agrees exactly.
    assert fields.line_17_total_itemized == engine_total


def test_line17_matches_engine_with_medical_floor() -> None:
    """CP8-A invariant: with medical > 0 AND AGI > 0, the engine and the
    renderer produce the SAME number when the engine is given the AGI
    used by the form.

    Pre-CP8-A, ``itemized_total_capped`` used RAW medical and tenforty
    treated the parameter as the final Sch A line 17 amount, over-
    deducting by exactly ``min(raw_medical, 0.075 * AGI)``. Post-fix,
    ``itemized_total_capped`` applies the floor itself, producing a
    number the filer's real Form 1040 line 12 (and Sch A line 17) will
    match bit-for-bit.
    """
    from skill.scripts.models import ComputedTotals

    it = ItemizedDeductions(
        medical_and_dental_total=Decimal("20000"),
        state_and_local_income_tax=Decimal("5000"),
        home_mortgage_interest=Decimal("10000"),
        gifts_to_charity_cash=Decimal("1000"),
    )
    r_pre = _minimal_return(filing_status=FilingStatus.SINGLE, itemized=it)
    agi = Decimal("65000")
    r = r_pre.model_copy(
        update={"computed": ComputedTotals(adjusted_gross_income=agi)}
    )
    fields = compute_schedule_a_fields(r)

    # 7.5% floor: quantize to cents as both sides do.
    floor = (agi * Decimal("0.075")).quantize(Decimal("0.01"))
    raw_medical = Decimal("20000")
    expected_medical_after_floor = max(Decimal("0"), raw_medical - floor)
    # Expected line 17: medical post-floor + SALT 5000 + interest 10000 + charity 1000
    expected_line_17 = (
        expected_medical_after_floor
        + Decimal("5000")
        + Decimal("10000")
        + Decimal("1000")
    )
    assert fields.line_17_total_itemized == expected_line_17

    # The engine must now produce the identical number when passed the
    # same AGI. Pre-CP8-A this was raw_medical + SALT + interest + charity
    # (i.e. $4,875 higher than the IRS form).
    engine_total = itemized_total_capped(it, FilingStatus.SINGLE, agi)
    assert engine_total == fields.line_17_total_itemized, (
        f"CP8-A invariant violated: engine={engine_total}, "
        f"renderer line_17={fields.line_17_total_itemized}. "
        f"Expected both to equal {expected_line_17}."
    )


# ---------------------------------------------------------------------------
# Layer 1: non-SALT line mapping
# ---------------------------------------------------------------------------


def test_no_itemized_block_returns_zeroed_fields() -> None:
    """Taxpayer taking standard deduction — itemized is None."""
    r = _minimal_return(filing_status=FilingStatus.SINGLE, itemized=None)
    fields = compute_schedule_a_fields(r)
    assert isinstance(fields, ScheduleAFields)
    assert fields.taxpayer_name == "Test Payer"
    assert fields.line_17_total_itemized == Decimal("0")
    assert fields.line_5e_salt_capped == Decimal("0")
    # Even with no itemized block, the cap CONSTANT is not populated.
    assert fields.line_5e_salt_cap_applied == Decimal("0")


def test_medical_lines_1_to_4_populated() -> None:
    """Medical line 1 shows raw total; line 4 shows post-7.5% floor."""
    it = ItemizedDeductions(medical_and_dental_total=Decimal("20000"))
    r = _minimal_return(filing_status=FilingStatus.SINGLE, itemized=it)
    # AGI is 0 on a return that hasn't been compute()'d — so the 7.5%
    # floor is 0 and line 4 equals line 1.
    fields = compute_schedule_a_fields(r)
    assert fields.line_1_medical_and_dental == Decimal("20000")
    assert fields.line_3_agi_floor == Decimal("0.00")
    assert fields.line_4_medical_deductible == Decimal("20000")


def test_interest_lines_8a_to_10_populated() -> None:
    """Home mortgage interest goes on 8a, points on 8c, inv. interest on 9."""
    it = ItemizedDeductions(
        home_mortgage_interest=Decimal("22000"),
        mortgage_points=Decimal("1500"),
        mortgage_insurance_premiums=Decimal("800"),  # line 8d: forced to 0
        investment_interest=Decimal("1200"),
    )
    r = _minimal_return(filing_status=FilingStatus.SINGLE, itemized=it)
    fields = compute_schedule_a_fields(r)

    assert fields.line_8a_home_mortgage_interest_on_1098 == Decimal("22000")
    assert fields.line_8b_home_mortgage_interest_not_on_1098 == Decimal("0")
    assert fields.line_8c_points_not_on_1098 == Decimal("1500")
    # Line 8d is always 0 — deduction expired for TY2022.
    assert fields.line_8d_mortgage_insurance_premiums == Decimal("0")
    assert fields.line_8e_total_home_mortgage_interest == Decimal("23500")
    assert fields.line_9_investment_interest == Decimal("1200")
    assert fields.line_10_total_interest == Decimal("24700")


def test_gifts_lines_11_to_14_populated() -> None:
    it = ItemizedDeductions(
        gifts_to_charity_cash=Decimal("3000"),
        gifts_to_charity_other_than_cash=Decimal("1500"),
        gifts_to_charity_carryover=Decimal("500"),
    )
    r = _minimal_return(filing_status=FilingStatus.SINGLE, itemized=it)
    fields = compute_schedule_a_fields(r)

    assert fields.line_11_gifts_cash == Decimal("3000")
    assert fields.line_12_gifts_noncash == Decimal("1500")
    assert fields.line_13_carryover == Decimal("500")
    assert fields.line_14_total_gifts == Decimal("5000")


def test_casualty_line_15_populated() -> None:
    it = ItemizedDeductions(
        casualty_and_theft_losses_federal_disaster=Decimal("12345")
    )
    r = _minimal_return(filing_status=FilingStatus.SINGLE, itemized=it)
    fields = compute_schedule_a_fields(r)
    assert fields.line_15_casualty_and_theft == Decimal("12345")


def test_other_itemized_split_taxes_vs_other() -> None:
    """other_itemized["other_taxes_paid"] -> line 6; everything else -> 16."""
    it = ItemizedDeductions(
        other_itemized={
            "other_taxes_paid": Decimal("250"),
            "gambling_losses": Decimal("1000"),
            "unrecovered_pension": Decimal("300"),
        }
    )
    r = _minimal_return(filing_status=FilingStatus.SINGLE, itemized=it)
    fields = compute_schedule_a_fields(r)

    assert fields.line_6_other_taxes == Decimal("250")
    assert fields.line_16_other_itemized == Decimal("1300")


# ---------------------------------------------------------------------------
# Layer 1: golden fixture round-trip
# ---------------------------------------------------------------------------


def test_w2_investments_itemized_fixture_field_mapping(fixtures_dir: Path) -> None:
    """Schedule A snapshot against the shipped MFJ itemized fixture.

    Per the fixture's ``expected.json`` hand-check:
      - raw SALT = 15000 income + 8000 real estate = 23000
      - post-cap SALT = min(23000, 10000) = 10000
      - total itemized = 10000 + 20000 (home mortgage interest) + 5000
        (cash charity) = 35000
    """
    return_ = _load_fixture(fixtures_dir, "w2_investments_itemized")
    computed = compute(return_)
    fields = compute_schedule_a_fields(computed)

    assert isinstance(fields, ScheduleAFields)
    assert fields.filing_status == "mfj"

    # Taxes You Paid
    assert fields.line_5a_state_and_local_taxes == Decimal("15000.00")
    assert fields.line_5b_real_estate_taxes == Decimal("8000.00")
    assert fields.line_5d_salt_subtotal == Decimal("23000.00")
    assert fields.line_5e_salt_capped == Decimal("10000.00")
    assert fields.line_5e_salt_cap_applied == Decimal("10000")
    assert fields.line_7_total_taxes == Decimal("10000.00")

    # Interest You Paid
    assert fields.line_8a_home_mortgage_interest_on_1098 == Decimal("20000.00")
    assert fields.line_8e_total_home_mortgage_interest == Decimal("20000.00")
    assert fields.line_10_total_interest == Decimal("20000.00")

    # Gifts to Charity
    assert fields.line_11_gifts_cash == Decimal("5000.00")
    assert fields.line_14_total_gifts == Decimal("5000.00")

    # Grand total matches the engine's deduction_taken AND the engine's
    # itemized_total_capped.
    assert fields.line_17_total_itemized == Decimal("35000.00")
    assert fields.line_17_total_itemized == computed.computed.deduction_taken


def test_simple_w2_standard_fixture_has_no_itemized(fixtures_dir: Path) -> None:
    """simple_w2_standard fixture takes the standard deduction."""
    return_ = _load_fixture(fixtures_dir, "simple_w2_standard")
    computed = compute(return_)
    fields = compute_schedule_a_fields(computed)

    # itemized is None in the fixture -> every numeric line is zero.
    assert fields.line_17_total_itemized == Decimal("0")
    assert fields.line_5e_salt_capped == Decimal("0")
    # Header still populated.
    assert fields.filing_status == "single"
    assert fields.taxpayer_name == "Alex Doe"


# ---------------------------------------------------------------------------
# Layer 2: AcroForm overlay PDF rendering (wave 5)
# ---------------------------------------------------------------------------


def _load_widget_value(out_path: Path, terminal_substring: str):
    """Look up a filled widget value from a freshly-written Schedule A PDF.

    Returns the string value (or None) of the first widget whose
    pypdf-reported field key contains ``terminal_substring`` (e.g.
    ``"f1_11["`` for line 5e).
    """
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(out_path))
    fields = reader.get_fields() or {}
    for k, v in fields.items():
        if terminal_substring in k:
            return v.get("/V")
    return None


def test_render_layer2_produces_non_empty_pdf(tmp_path: Path) -> None:
    """Render via the AcroForm overlay and assert the output is non-empty."""
    it = ItemizedDeductions(
        state_and_local_income_tax=Decimal("15000"),
        real_estate_tax=Decimal("8000"),
        home_mortgage_interest=Decimal("20000"),
        gifts_to_charity_cash=Decimal("5000"),
    )
    r = _minimal_return(
        filing_status=FilingStatus.MFJ, itemized=it, spouse=_person("Spouse", "Two")
    )
    fields = compute_schedule_a_fields(r)

    out_path = tmp_path / "schedule_a_overlay.pdf"
    result = render_schedule_a_pdf(fields, out_path)

    assert result == out_path
    assert out_path.exists()
    # The IRS source PDF is ~79 KB; a filled PDF is comparable in size.
    assert out_path.stat().st_size > 50_000


def test_render_layer2_round_trip_line_5e_salt_cap(tmp_path: Path) -> None:
    """SALT cap on line 5e must round-trip through the filled IRS PDF.

    Hand-crafted MFJ return with $15k state income + $8k real estate
    -> raw SALT 23000 -> capped 10000. The widget at line 5e
    (terminal f1_11) must read back as ``"10000.00"``.
    """
    it = ItemizedDeductions(
        state_and_local_income_tax=Decimal("15000"),
        real_estate_tax=Decimal("8000"),
    )
    r = _minimal_return(filing_status=FilingStatus.MFJ, itemized=it)
    fields = compute_schedule_a_fields(r)
    assert fields.line_5e_salt_capped == Decimal("10000")

    out_path = tmp_path / "round_trip_5e.pdf"
    render_schedule_a_pdf(fields, out_path)

    assert _load_widget_value(out_path, "f1_11[") == "10000.00"


def test_render_layer2_round_trip_w2_itemized_fixture(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """Golden fixture: round-trip multiple lines through the filled PDF.

    The shipped ``w2_investments_itemized`` MFJ fixture lands at:
        line 5a 15000, line 5b 8000, line 5d 23000, line 5e 10000,
        line 7 10000, line 8a 20000, line 17 35000.
    Each of those values should appear in the filled PDF.
    """
    return_ = _load_fixture(fixtures_dir, "w2_investments_itemized")
    computed = compute(return_)
    fields = compute_schedule_a_fields(computed)

    out_path = tmp_path / "fixture_round_trip.pdf"
    render_schedule_a_pdf(fields, out_path)

    # f1_7 = line 5a state/local income, f1_8 = 5b real estate,
    # f1_10 = 5d, f1_11 = 5e, f1_14 = line 7, f1_15 = 8a, f1_30 = line 17
    assert _load_widget_value(out_path, "f1_7[") == "15000.00"
    assert _load_widget_value(out_path, "f1_8[") == "8000.00"
    assert _load_widget_value(out_path, "f1_10[") == "23000.00"
    assert _load_widget_value(out_path, "f1_11[") == "10000.00"
    assert _load_widget_value(out_path, "f1_14[") == "10000.00"
    assert _load_widget_value(out_path, "f1_15[") == "20000.00"
    assert _load_widget_value(out_path, "f1_30[") == "35000.00"


def test_render_layer2_zero_lines_blank(tmp_path: Path) -> None:
    """Lines that Layer 1 leaves at 0 should produce blank widgets, not '0.00'.

    This keeps the printed form visually identical to a freshly-downloaded
    blank in cells the filer doesn't use (e.g. casualty and theft on most
    returns).
    """
    it = ItemizedDeductions(
        state_and_local_income_tax=Decimal("5000"),
    )
    r = _minimal_return(filing_status=FilingStatus.SINGLE, itemized=it)
    fields = compute_schedule_a_fields(r)
    assert fields.line_15_casualty_and_theft == Decimal("0")

    out_path = tmp_path / "blank_lines.pdf"
    render_schedule_a_pdf(fields, out_path)

    # Line 15 should be blank (None), not "0.00".
    assert _load_widget_value(out_path, "f1_27[") in (None, "")


def test_render_layer2_taxpayer_name_round_trip(tmp_path: Path) -> None:
    """The header name widget should carry the Layer 1 taxpayer_name string."""
    it = ItemizedDeductions(state_and_local_income_tax=Decimal("5000"))
    r = _minimal_return(filing_status=FilingStatus.SINGLE, itemized=it)
    fields = compute_schedule_a_fields(r)
    assert fields.taxpayer_name == "Test Payer"

    out_path = tmp_path / "name.pdf"
    render_schedule_a_pdf(fields, out_path)

    assert _load_widget_value(out_path, "f1_1[") == "Test Payer"


def test_render_layer2_raises_when_source_pdf_missing(
    monkeypatch, tmp_path: Path
) -> None:
    """Pre-flight check: if the IRS source PDF is missing, raise RuntimeError."""
    from skill.scripts.output import schedule_a as sa

    bogus = tmp_path / "missing.pdf"
    monkeypatch.setattr(sa, "_SCHEDULE_A_PDF_PATH", bogus)

    it = ItemizedDeductions(state_and_local_income_tax=Decimal("5000"))
    r = _minimal_return(filing_status=FilingStatus.SINGLE, itemized=it)
    fields = compute_schedule_a_fields(r)

    with pytest.raises(RuntimeError, match="missing"):
        sa.render_schedule_a_pdf(fields, tmp_path / "out.pdf")


def test_render_layer2_raises_on_sha_mismatch(monkeypatch, tmp_path: Path) -> None:
    """If the IRS PDF SHA-256 changes (silent re-issue), raise RuntimeError."""
    from skill.scripts.output import schedule_a as sa
    from skill.scripts.output._acroform_overlay import verify_pdf_sha256

    # A wrong SHA: should never match a real PDF.
    bad_sha = "deadbeef" * 8

    def _bad_verify(pdf, sha):
        return verify_pdf_sha256(pdf, bad_sha)

    monkeypatch.setattr(sa, "verify_pdf_sha256", _bad_verify, raising=False)
    # We'll instead force the map JSON's SHA to bad value.
    import json as _json

    real_map = _json.loads(sa._SCHEDULE_A_MAP_PATH.read_text())
    real_map["source_pdf_sha256"] = bad_sha
    fake_map_path = tmp_path / "fake_map.json"
    fake_map_path.write_text(_json.dumps(real_map))
    monkeypatch.setattr(sa, "_SCHEDULE_A_MAP_PATH", fake_map_path)

    it = ItemizedDeductions(state_and_local_income_tax=Decimal("5000"))
    r = _minimal_return(filing_status=FilingStatus.SINGLE, itemized=it)
    fields = compute_schedule_a_fields(r)

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        sa.render_schedule_a_pdf(fields, tmp_path / "out.pdf")


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------


def test_salt_cap_constants_match_statute() -> None:
    """IRC §164(b)(6): $10,000 normal, $5,000 MFS."""
    assert SALT_CAP_NORMAL == Decimal("10000")
    assert SALT_CAP_MFS == Decimal("5000")


def test_salt_cap_constants_match_engine() -> None:
    """Duplicated-but-checked: engine constants agree with ours."""
    from skill.scripts.calc.engine import (
        SALT_CAP_MFS as ENGINE_MFS,
        SALT_CAP_NORMAL as ENGINE_NORMAL,
    )

    assert ENGINE_NORMAL == SALT_CAP_NORMAL
    assert ENGINE_MFS == SALT_CAP_MFS
