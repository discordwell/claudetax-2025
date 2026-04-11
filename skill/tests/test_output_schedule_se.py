"""Tests for ``skill.scripts.output.schedule_se`` — Schedule SE scaffold.

Two layers under test:

* Layer 1 — ``compute_schedule_se_fields`` / ``schedule_se_required``:
  assert that the hand-computed SE tax line-by-line breakdown matches
  the IRS 2024 Schedule SE instructions and that the $400 filing floor
  behaves correctly at / across the boundary. Also cross-verify Layer 1
  against ``tenforty``'s total SE tax (via
  ``engine.compute().other_taxes_total``) on a SE-only fixture, inside
  a small $1 tolerance for intermediate rounding.

* Layer 2 — ``render_schedule_se_pdf``: write a scaffold PDF, reopen
  with ``pypdf``, and assert the extracted text contains header markers
  and at least one numeric line value.

Sources referenced in assertions:
* IRS 2024 Schedule SE instructions — line 4a = line 3 × 92.35%,
  line 10 = min(line 6, line 9) × 12.4%, line 11 = line 6 × 2.9%,
  line 13 = line 12 × 50%.
* SSA 2025 wage base $176,100 (SSA 2024-10-10 press release).
* IRC §1401 / §164(f) — ½ SE tax above-the-line deduction.
* IRS Pub 334 — $400 net-SE-earnings filing floor.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from skill.scripts.calc.engine import compute
from skill.scripts.models import CanonicalReturn
from skill.scripts.output.schedule_se import (
    HALF_SE_TAX_FRACTION,
    MEDICARE_RATE_SE,
    SE_FILING_FLOOR,
    SE_NET_EARNINGS_FRACTION,
    SS_RATE_SE,
    SS_WAGE_BASE_TY2025,
    ScheduleSEFields,
    compute_schedule_se_fields,
    render_schedule_se_pdf,
    schedule_se_required,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_return_dict() -> dict[str, Any]:
    """Minimal single-filer canonical return dict, no Schedule Cs or W-2s."""
    return {
        "schema_version": "0.1.0",
        "tax_year": 2025,
        "filing_status": "single",
        "taxpayer": {
            "first_name": "Pat",
            "last_name": "Testerson",
            "ssn": "333-22-1111",
            "date_of_birth": "1985-06-15",
            "is_blind": False,
            "is_age_65_or_older": False,
        },
        "address": {
            "street1": "1 Test Lane",
            "city": "Austin",
            "state": "TX",
            "zip": "78701",
        },
        "w2s": [],
        "schedules_c": [],
        "itemize_deductions": False,
    }


def _schedule_c(gross: str, expenses_simple: str = "0") -> dict[str, Any]:
    """Build a minimal Schedule C dict with a single advertising expense
    line used to reach a specific net profit."""
    return {
        "proprietor_is_taxpayer": True,
        "business_name": "Test Biz",
        "principal_business_or_profession": "Consulting",
        "accounting_method": "cash",
        "material_participation": True,
        "line1_gross_receipts": gross,
        "line2_returns_and_allowances": "0",
        "line4_cost_of_goods_sold": "0",
        "line6_other_income": "0",
        "expenses": {
            "line8_advertising": expenses_simple,
        },
        "line30_home_office_expense": "0",
        "line32_at_risk_box": "all_at_risk",
    }


def _w2(box1: str, box3: str, box7: str = "0") -> dict[str, Any]:
    """Build a minimal taxpayer W-2 with the given SS wages (box 3)."""
    return {
        "employer_name": "MegaCorp",
        "employee_is_taxpayer": True,
        "box1_wages": box1,
        "box2_federal_income_tax_withheld": "0",
        "box3_social_security_wages": box3,
        "box7_social_security_tips": box7,
    }


def _return_with_net_profit(net_profit: str) -> CanonicalReturn:
    """Build a single-filer CanonicalReturn with exactly the given Schedule
    C net profit (line 1 gross receipts = net_profit, no expenses)."""
    data = _base_return_dict()
    data["schedules_c"] = [_schedule_c(net_profit)]
    return CanonicalReturn.model_validate(data)


def _return_with_net_profit_and_w2(
    net_profit: str, w2_box3: str, w2_box1: str | None = None
) -> CanonicalReturn:
    data = _base_return_dict()
    data["schedules_c"] = [_schedule_c(net_profit)]
    data["w2s"] = [_w2(w2_box1 or w2_box3, w2_box3)]
    return CanonicalReturn.model_validate(data)


# ---------------------------------------------------------------------------
# Layer 1: constants and basic sanity
# ---------------------------------------------------------------------------


def test_ty2025_ss_wage_base_constant() -> None:
    """The TY2025 SS wage base must match ty2025-constants.json and SSA."""
    # SSA 2024-10-10 press release fixed this at $176,100 for 2025.
    assert SS_WAGE_BASE_TY2025 == Decimal("176100")


def test_ty2025_ss_wage_base_matches_reference_json(reference_dir: Path) -> None:
    """Cross-check the hard-coded constant against the reference JSON."""
    data = json.loads((reference_dir / "ty2025-constants.json").read_text())
    assert data["payroll_taxes"]["social_security_wage_base"] == 176100
    # Schedule SE's note also mentions this.
    assert "176,100" in data["schedule_se"]["note_ss_wage_base_applies"]


def test_se_rate_constants() -> None:
    """12.4% SS + 2.9% Medicare == 15.3% combined SE rate."""
    assert SS_RATE_SE == Decimal("0.124")
    assert MEDICARE_RATE_SE == Decimal("0.029")
    assert SS_RATE_SE + MEDICARE_RATE_SE == Decimal("0.153")
    assert SE_NET_EARNINGS_FRACTION == Decimal("0.9235")
    assert HALF_SE_TAX_FRACTION == Decimal("0.5")
    assert SE_FILING_FLOOR == Decimal("400")


# ---------------------------------------------------------------------------
# Layer 1: $400 filing floor (threshold boundary)
# ---------------------------------------------------------------------------


def test_threshold_below_400_not_required() -> None:
    """Net profit of $400 → net earnings = 400 * 0.9235 = $369.40, below
    the $400 filing floor, so Schedule SE is NOT required."""
    return_ = _return_with_net_profit("400.00")
    fields = compute_schedule_se_fields(return_)
    assert fields.line_2_net_profit_schedule_c == Decimal("400.00")
    assert fields.line_4a_net_earnings_times_9235 == Decimal("369.4000")
    assert fields.line_6_net_earnings_from_se < SE_FILING_FLOOR
    assert schedule_se_required(return_) is False


def test_threshold_at_filing_floor_exact() -> None:
    """Net profit just above the threshold where line 6 crosses $400.

    $433.19 × 0.9235 = $400.0010... ≥ $400.
    """
    return_ = _return_with_net_profit("433.20")
    fields = compute_schedule_se_fields(return_)
    assert fields.line_6_net_earnings_from_se >= SE_FILING_FLOOR
    assert schedule_se_required(return_) is True


def test_threshold_well_above() -> None:
    """Clearly SE-required: net profit $10,000."""
    return_ = _return_with_net_profit("10000.00")
    fields = compute_schedule_se_fields(return_)
    assert fields.line_6_net_earnings_from_se == Decimal("9235.0000")
    assert schedule_se_required(return_) is True


def test_zero_net_profit_not_required() -> None:
    """Zero Schedule C net profit → Schedule SE not required."""
    return_ = _return_with_net_profit("0.00")
    fields = compute_schedule_se_fields(return_)
    assert fields.line_2_net_profit_schedule_c == Decimal("0")
    assert fields.line_6_net_earnings_from_se == Decimal("0")
    assert fields.line_12_se_tax == Decimal("0")
    assert schedule_se_required(return_) is False


def test_no_schedule_c_not_required() -> None:
    """A return with no Schedule Cs has no SE tax."""
    return_ = CanonicalReturn.model_validate(_base_return_dict())
    fields = compute_schedule_se_fields(return_)
    assert fields.line_2_net_profit_schedule_c == Decimal("0")
    assert fields.line_12_se_tax == Decimal("0")
    assert schedule_se_required(return_) is False


# ---------------------------------------------------------------------------
# Layer 1: standard formula cross-check
# ---------------------------------------------------------------------------


def test_basic_90k_net_profit_matches_hand_calc() -> None:
    """Hand-check: $90,000 Schedule C → SE tax = $12,716.595.

    The se_home_office golden fixture locks this: tenforty produces
    $12,716.60 and our hand-computed Layer 1 should land on the same
    value (within sub-cent Decimal rounding).
    """
    return_ = _return_with_net_profit("90000.00")
    fields = compute_schedule_se_fields(return_)

    # Line 2 / 3
    assert fields.line_2_net_profit_schedule_c == Decimal("90000.00")
    assert fields.line_3_combine_1a_1b_2 == Decimal("90000.00")
    # Line 4a = 90000 * 0.9235 = 83115
    assert fields.line_4a_net_earnings_times_9235 == Decimal("83115.000000")
    assert fields.line_4c_combine_4a_4b == Decimal("83115.000000")
    # Line 6 (no church)
    assert fields.line_6_net_earnings_from_se == Decimal("83115.000000")
    # Line 7 SS wage base
    assert fields.line_7_ss_wage_base == Decimal("176100")
    # Line 8: no W-2s, so 0
    assert fields.line_8a_w2_ss_wages_and_tips == Decimal("0")
    assert fields.line_8d_sum_8a_8b_8c == Decimal("0")
    # Line 9 = 176100 - 0 = 176100
    assert fields.line_9_subtract_8d_from_7 == Decimal("176100")
    # Line 10 = min(83115, 176100) * 0.124 = 10306.26
    assert fields.line_10_ss_portion == Decimal("10306.26000000")
    # Line 11 = 83115 * 0.029 = 2410.335
    assert fields.line_11_medicare_portion == Decimal("2410.33500000")
    # Line 12 = 12716.595
    assert fields.line_12_se_tax == Decimal("12716.59500000")
    # Line 13 = ½ of line 12 = 6358.2975
    assert fields.line_13_deductible_half_se_tax == Decimal("6358.297500000")


def test_header_populated_from_taxpayer() -> None:
    return_ = _return_with_net_profit("5000.00")
    fields = compute_schedule_se_fields(return_)
    assert fields.taxpayer_name == "Pat Testerson"
    assert fields.taxpayer_ssn == "333-22-1111"


# ---------------------------------------------------------------------------
# Layer 1: SS wage base cap (self-employed with $200k net)
# ---------------------------------------------------------------------------


def test_ss_wage_base_cap_self_employed_200k() -> None:
    """$200,000 Schedule C net profit with NO W-2 wages.

    Line 4a = 200000 * 0.9235 = $184,700 (ABOVE the $176,100 SS wage base).
    Line 9  = 176100 - 0 = 176100.
    Line 10 = min(184700, 176100) * 0.124 = 176100 * 0.124 = $21,836.40
             (capped — this is the regression lock.)
    Line 11 = 184700 * 0.029 = $5,356.30 (no cap on Medicare)
    Line 12 = $27,192.70
    Line 13 = $13,596.35
    """
    return_ = _return_with_net_profit("200000.00")
    fields = compute_schedule_se_fields(return_)

    assert fields.line_4a_net_earnings_times_9235 == Decimal("184700.000000")
    # Cap kicks in: SS base = 176100, not 184700
    assert fields.line_9_subtract_8d_from_7 == Decimal("176100")
    assert fields.line_10_ss_portion == Decimal("21836.40000000")
    assert fields.line_11_medicare_portion == Decimal("5356.30000000")
    assert fields.line_12_se_tax == Decimal("27192.70000000")
    assert fields.line_13_deductible_half_se_tax == Decimal("13596.350000000")


def test_ss_cap_exact_wage_base_boundary() -> None:
    """Net profit where line 4a is just above $176,100, confirming cap."""
    # 176100 / 0.9235 ≈ 190687.06, use 200000 / 0.9235 clearly above.
    return_ = _return_with_net_profit("200000.00")
    fields = compute_schedule_se_fields(return_)
    # SS portion should be exactly 176100 * 0.124 when line 4a > 176100.
    assert fields.line_10_ss_portion == (SS_WAGE_BASE_TY2025 * SS_RATE_SE)


# ---------------------------------------------------------------------------
# Layer 1: ½ SE tax deduction
# ---------------------------------------------------------------------------


def test_half_se_tax_deduction_matches_50_percent_of_line_12() -> None:
    """Line 13 = Line 12 × 50% regardless of income level."""
    for net_profit in ("5000.00", "50000.00", "500000.00"):
        return_ = _return_with_net_profit(net_profit)
        fields = compute_schedule_se_fields(return_)
        assert fields.line_13_deductible_half_se_tax == (
            fields.line_12_se_tax * HALF_SE_TAX_FRACTION
        )


def test_half_se_tax_nonzero_for_real_filer() -> None:
    return_ = _return_with_net_profit("50000.00")
    fields = compute_schedule_se_fields(return_)
    assert fields.line_13_deductible_half_se_tax > Decimal("0")
    assert fields.line_13_deductible_half_se_tax < fields.line_12_se_tax


# ---------------------------------------------------------------------------
# Layer 1: W-2 wage integration (both W-2 and SE income)
# ---------------------------------------------------------------------------


def test_w2_wages_reduce_ss_room_line_9() -> None:
    """W-2 SS wages eat into the SS wage base before SE income.

    Box 3 = $100,000 (fully under cap), net profit $50,000.
    Line 4a = 50000 * 0.9235 = $46,175
    Line 6  = $46,175
    Line 8d = $100,000
    Line 9  = 176100 - 100000 = $76,100
    Since line 6 ($46,175) < line 9 ($76,100), SS portion uses line 6:
        Line 10 = 46175 * 0.124 = $5,725.70
    Line 11 = 46175 * 0.029 = $1,339.075
    """
    return_ = _return_with_net_profit_and_w2("50000.00", "100000.00")
    fields = compute_schedule_se_fields(return_)
    assert fields.line_8a_w2_ss_wages_and_tips == Decimal("100000.00")
    assert fields.line_8d_sum_8a_8b_8c == Decimal("100000.00")
    assert fields.line_9_subtract_8d_from_7 == Decimal("76100.00")
    assert fields.line_4a_net_earnings_times_9235 == Decimal("46175.000000")
    # min(46175, 76100) = 46175
    assert fields.line_10_ss_portion == Decimal("5725.70000000")


def test_w2_wages_above_wage_base_zero_ss_room() -> None:
    """W-2 box 3 already maxed at the SS wage base → SE SS portion = 0.

    Box 3 = $200,000 (above $176,100 cap — employer will have stopped at
    the wage base, but if overreported we still cap line 9 at 0).
    Net profit $60,000.
    Line 9 = max(0, 176100 - 200000) = 0
    Line 10 = min(line 6, 0) * 12.4% = 0
    Line 11 = 60000 * 0.9235 * 0.029 = $1,606.89
    Line 12 = $1,606.89 (Medicare only)
    """
    return_ = _return_with_net_profit_and_w2("60000.00", "200000.00")
    fields = compute_schedule_se_fields(return_)
    assert fields.line_9_subtract_8d_from_7 == Decimal("0")
    assert fields.line_10_ss_portion == Decimal("0")
    # Medicare portion is nonzero
    assert fields.line_11_medicare_portion > Decimal("0")
    assert fields.line_12_se_tax == fields.line_11_medicare_portion


def test_w2_box7_tips_count_toward_line_8a() -> None:
    """Box 3 SS wages AND box 7 SS tips BOTH count on Schedule SE line 8a."""
    data = _base_return_dict()
    data["schedules_c"] = [_schedule_c("50000.00")]
    data["w2s"] = [
        {
            "employer_name": "MegaCorp",
            "employee_is_taxpayer": True,
            "box1_wages": "80000.00",
            "box2_federal_income_tax_withheld": "0",
            "box3_social_security_wages": "70000.00",
            "box7_social_security_tips": "10000.00",
        }
    ]
    return_ = CanonicalReturn.model_validate(data)
    fields = compute_schedule_se_fields(return_)
    assert fields.line_8a_w2_ss_wages_and_tips == Decimal("80000.00")


def test_spouse_w2_excluded_from_line_8a() -> None:
    """Spouse's W-2 box 3 must NOT reduce the taxpayer's Schedule SE room.

    Build an MFJ return where taxpayer has a $50k Sch C and spouse has a
    $180k box-3 W-2. Spouse's W-2 should NOT eat the taxpayer's SS room.
    """
    data = _base_return_dict()
    data["filing_status"] = "mfj"
    data["spouse"] = {
        "first_name": "Jamie",
        "last_name": "Testerson",
        "ssn": "444-55-6666",
        "date_of_birth": "1986-07-20",
        "is_blind": False,
        "is_age_65_or_older": False,
    }
    data["schedules_c"] = [_schedule_c("50000.00")]
    data["w2s"] = [
        {
            "employer_name": "SpouseInc",
            "employee_is_taxpayer": False,
            "box1_wages": "180000.00",
            "box2_federal_income_tax_withheld": "0",
            "box3_social_security_wages": "180000.00",
        }
    ]
    return_ = CanonicalReturn.model_validate(data)
    fields = compute_schedule_se_fields(return_)
    # Spouse's wages are ignored on the taxpayer's Schedule SE
    assert fields.line_8a_w2_ss_wages_and_tips == Decimal("0")
    assert fields.line_9_subtract_8d_from_7 == Decimal("176100")


def test_spouse_schedule_c_excluded_from_line_2() -> None:
    """A Schedule C owned by the spouse must NOT appear on the taxpayer's
    Schedule SE line 2 (spouse would file their own Schedule SE)."""
    data = _base_return_dict()
    data["filing_status"] = "mfj"
    data["spouse"] = {
        "first_name": "Jamie",
        "last_name": "Testerson",
        "ssn": "444-55-6666",
        "date_of_birth": "1986-07-20",
        "is_blind": False,
        "is_age_65_or_older": False,
    }
    spouse_sc = _schedule_c("30000.00")
    spouse_sc["proprietor_is_taxpayer"] = False
    data["schedules_c"] = [spouse_sc]
    return_ = CanonicalReturn.model_validate(data)
    fields = compute_schedule_se_fields(return_)
    assert fields.line_2_net_profit_schedule_c == Decimal("0")
    assert schedule_se_required(return_) is False


def test_multiple_taxpayer_schedule_cs_aggregated() -> None:
    data = _base_return_dict()
    data["schedules_c"] = [
        _schedule_c("30000.00"),
        _schedule_c("20000.00"),
    ]
    return_ = CanonicalReturn.model_validate(data)
    fields = compute_schedule_se_fields(return_)
    assert fields.line_2_net_profit_schedule_c == Decimal("50000.00")


def test_deferred_lines_are_zero() -> None:
    """Farm lines 1a/1b and church lines 5a/5b are deferred and always 0."""
    return_ = _return_with_net_profit("75000.00")
    fields = compute_schedule_se_fields(return_)
    assert fields.line_1a_net_farm_profit == Decimal("0")
    assert fields.line_1b_ss_farm_optional == Decimal("0")
    assert fields.line_5a_church_employee_income == Decimal("0")
    assert fields.line_5b_church_times_9235 == Decimal("0")
    assert fields.line_4b_optional_methods == Decimal("0")
    assert fields.line_8b_unreported_tips == Decimal("0")
    assert fields.line_8c_wages_8919 == Decimal("0")


# ---------------------------------------------------------------------------
# Golden-fixture integration: se_home_office
# ---------------------------------------------------------------------------


def test_se_home_office_golden_required_and_deductible_half(
    fixtures_dir: Path,
) -> None:
    """Golden fixture: se_home_office has $90k Schedule C net profit.

    Schedule SE should be REQUIRED and line 13 (½ SE tax) must match
    the engine's view of the deductible amount.

    Expected (from expected.json hand_check):
        SE tax ≈ 12716.60  (tenforty produces exactly that)
        ½ SE tax = 6358.30

    Our hand-computed line 12 is $12,716.595 — we compare with a cent
    tolerance against the golden deductible_half_se_tax = $6,358.30.
    """
    data = json.loads((fixtures_dir / "se_home_office" / "input.json").read_text())
    return_ = CanonicalReturn.model_validate(data)

    # Schedule SE is required
    assert schedule_se_required(return_) is True

    fields = compute_schedule_se_fields(return_)
    # Schedule C net profit = 90000 (120k - 27k part-II - 3k home office)
    assert fields.line_2_net_profit_schedule_c == Decimal("90000.00")
    # Line 12 should land at 12716.595 exactly from hand calc.
    assert fields.line_12_se_tax == Decimal("12716.59500000")
    # Golden expected.json: other_taxes_total = 12716.60, half = 6358.30.
    # Our hand-computed line 13 = 6358.2975, within $0.01 of the golden.
    expected_half = Decimal("6358.30")
    diff = abs(fields.line_13_deductible_half_se_tax - expected_half)
    assert diff < Decimal("0.01"), (
        f"line 13 hand calc {fields.line_13_deductible_half_se_tax} "
        f"differs from golden {expected_half} by {diff}"
    )


def test_se_home_office_cross_verify_against_engine_other_taxes(
    fixtures_dir: Path,
) -> None:
    """Cross-check: after running engine.compute(), other_taxes_total
    (which is SE tax in this SE-only fixture) should match our
    hand-computed line 12 within a $1 rounding tolerance.
    """
    data = json.loads((fixtures_dir / "se_home_office" / "input.json").read_text())
    return_ = CanonicalReturn.model_validate(data)

    fields = compute_schedule_se_fields(return_)

    computed = compute(return_)
    engine_other_taxes = computed.computed.other_taxes_total
    assert engine_other_taxes is not None

    # SE-only fixture: other_taxes is SE tax. Tolerance $1 for intermediate
    # cent rounding between tenforty and our Decimal-pure path.
    diff = abs(fields.line_12_se_tax - engine_other_taxes)
    assert diff < Decimal("1.00"), (
        f"Layer 1 SE tax {fields.line_12_se_tax} differs from engine "
        f"other_taxes_total {engine_other_taxes} by {diff}"
    )


# ---------------------------------------------------------------------------
# Layer 2: AcroForm overlay PDF rendering (wave 5)
# ---------------------------------------------------------------------------


def _load_widget_value(out_path: Path, terminal_substring: str):
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(out_path))
    fields = reader.get_fields() or {}
    for k, v in fields.items():
        if terminal_substring in k:
            return v.get("/V")
    return None


def test_render_layer2_produces_non_empty_pdf(tmp_path: Path) -> None:
    """The filled IRS Schedule SE PDF must be substantially-sized and on disk."""
    return_ = _return_with_net_profit("90000.00")
    fields = compute_schedule_se_fields(return_)

    out_path = tmp_path / "test_sch_se.pdf"
    result_path = render_schedule_se_pdf(fields, out_path)

    assert result_path == out_path
    assert out_path.exists()
    # IRS f1040sse.pdf is ~80 KB; the filled output is comparable.
    assert out_path.stat().st_size > 50_000


def test_render_layer2_round_trip_line_2_net_profit(tmp_path: Path) -> None:
    """Line 2 (Schedule C net profit) must round-trip through the filled PDF."""
    return_ = _return_with_net_profit("90000.00")
    fields = compute_schedule_se_fields(return_)
    assert fields.line_2_net_profit_schedule_c == Decimal("90000.00")

    out_path = tmp_path / "round_trip_line_2.pdf"
    render_schedule_se_pdf(fields, out_path)

    # f1_5 = line 2
    assert _load_widget_value(out_path, "f1_5[") == "90000.00"


def test_render_layer2_round_trip_line_12_se_tax(tmp_path: Path) -> None:
    """Line 12 (total SE tax) must round-trip through the filled PDF."""
    return_ = _return_with_net_profit("90000.00")
    fields = compute_schedule_se_fields(return_)
    # Hand-computed: 12,716.595 → quantized to 12716.60
    assert fields.line_12_se_tax == Decimal("12716.59500000")

    out_path = tmp_path / "round_trip_line_12.pdf"
    render_schedule_se_pdf(fields, out_path)

    # f1_21 = line 12 SE tax
    assert _load_widget_value(out_path, "f1_21[") == "12716.60"


def test_render_layer2_round_trip_line_13_half_se_tax(tmp_path: Path) -> None:
    """Line 13 (½ SE tax above-the-line deduction) must round-trip."""
    return_ = _return_with_net_profit("90000.00")
    fields = compute_schedule_se_fields(return_)

    out_path = tmp_path / "round_trip_line_13.pdf"
    render_schedule_se_pdf(fields, out_path)

    # f1_22 = line 13 deductible half SE tax
    assert _load_widget_value(out_path, "f1_22[") == "6358.30"


def test_render_layer2_round_trip_line_7_ss_wage_base(tmp_path: Path) -> None:
    """Line 7 (SS wage base) is the TY2025 constant $176,100; should round-trip."""
    return_ = _return_with_net_profit("90000.00")
    fields = compute_schedule_se_fields(return_)
    assert fields.line_7_ss_wage_base == Decimal("176100")

    out_path = tmp_path / "round_trip_line_7.pdf"
    render_schedule_se_pdf(fields, out_path)

    # f1_13 = line 7
    assert _load_widget_value(out_path, "f1_13[") == "176100.00"


def test_render_layer2_round_trip_taxpayer_name_and_ssn(tmp_path: Path) -> None:
    return_ = _return_with_net_profit("5000.00")
    fields = compute_schedule_se_fields(return_)

    out_path = tmp_path / "name_ssn.pdf"
    render_schedule_se_pdf(fields, out_path)

    assert _load_widget_value(out_path, "f1_1[") == "Pat Testerson"
    assert _load_widget_value(out_path, "f1_2[") == "333-22-1111"


def test_render_layer2_zero_se_tax_blank_widgets(tmp_path: Path) -> None:
    """When net profit is 0, line 12 SE tax should be blank (not '0.00')."""
    return_ = _return_with_net_profit("0.00")
    fields = compute_schedule_se_fields(return_)
    assert fields.line_12_se_tax == Decimal("0")

    out_path = tmp_path / "zero_se.pdf"
    render_schedule_se_pdf(fields, out_path)

    # f1_21 = line 12; should be blank (None) for zero values
    assert _load_widget_value(out_path, "f1_21[") in (None, "")


def test_render_creates_parent_dirs(tmp_path: Path) -> None:
    """The renderer should mkdir the parent path if it doesn't exist."""
    return_ = _return_with_net_profit("10000.00")
    fields = compute_schedule_se_fields(return_)

    nested = tmp_path / "a" / "b" / "c" / "sch_se.pdf"
    assert not nested.parent.exists()
    render_schedule_se_pdf(fields, nested)
    assert nested.exists()


def test_render_layer2_raises_on_sha_mismatch(monkeypatch, tmp_path: Path) -> None:
    """If the IRS PDF SHA-256 changes (silent re-issue), raise RuntimeError."""
    from skill.scripts.output import schedule_se as sse

    bad_sha = "deadbeef" * 8
    real_map = json.loads(sse._SCHEDULE_SE_MAP_PATH.read_text())
    real_map["source_pdf_sha256"] = bad_sha
    fake_map_path = tmp_path / "fake_map.json"
    fake_map_path.write_text(json.dumps(real_map))
    monkeypatch.setattr(sse, "_SCHEDULE_SE_MAP_PATH", fake_map_path)

    return_ = _return_with_net_profit("10000.00")
    fields = compute_schedule_se_fields(return_)

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        sse.render_schedule_se_pdf(fields, tmp_path / "out.pdf")


def test_fields_is_frozen_dataclass() -> None:
    """ScheduleSEFields should be immutable."""
    return_ = _return_with_net_profit("5000.00")
    fields = compute_schedule_se_fields(return_)
    assert isinstance(fields, ScheduleSEFields)
    with pytest.raises((AttributeError, Exception)):
        fields.line_12_se_tax = Decimal("0")  # type: ignore[misc]
