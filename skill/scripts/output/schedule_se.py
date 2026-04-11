"""Schedule SE (Self-Employment Tax) output renderer — two-layer scaffold.

SCAFFOLD NOTICE
===============
This module is a SCAFFOLD for Schedule SE PDF generation, modeled on the
``skill.scripts.output.form_1040`` pattern (wave 3). It is intentionally
minimal:

* Layer 1 (``compute_schedule_se_fields``) maps a computed
  ``CanonicalReturn`` onto a frozen dataclass whose field names mirror
  the TY2024 Schedule SE line numbers (the TY2024 layout is assumed
  stable for TY2025 — Schedule SE has not had substantive line changes
  since the 2020 restructure).

* Layer 2 (``render_schedule_se_pdf``) writes a simple tabular PDF using
  ``reportlab`` that lists every line name and value. It is NOT a filled
  IRS Schedule SE — real AcroForm overlay on the IRS fillable PDF is a
  follow-up task.

Why Layer 1 hand-computes SE tax (vs. trusting tenforty)
--------------------------------------------------------
``tenforty`` exposes the final SE tax as a single scalar via
``self_employment_tax`` (rolled into ``ComputedTotals.other_taxes_total``
inside ``skill.scripts.calc.engine.compute``). It does NOT expose the
line-level breakdown (92.35% multiplier, SS portion, Medicare portion,
wage-base interaction with W-2 SS wages) that Schedule SE prints. To
produce a VISUAL Schedule SE, Layer 1 hand-walks the line algebra from
the IRS 2024 Schedule SE instructions. A sanity cross-check against
``return_.computed.other_taxes_total`` is left as a follow-up (it would
also pick up AMT / NIIT / additional Medicare which tenforty lumps into
"other taxes").

Sources
-------
* IRS 2024 Schedule SE form + instructions —
  https://www.irs.gov/forms-pubs/about-schedule-se-form-1040
  Line structure (1a/1b/2/3/4a/4b/4c/5a/5b/6/7/8a/8b/8c/8d/9/10/11/12/13).
* SSA 2025 wage base ($176,100) — SSA press release, 2024-10-10,
  "Social Security Announces 2.5 Percent Benefit Increase for 2025":
  https://www.ssa.gov/news/press/releases/2024/#10-2024-2
  (Value is verified against ``skill/reference/ty2025-constants.json``
  key ``payroll_taxes.social_security_wage_base`` and
  ``schedule_se.note_ss_wage_base_applies``.)
* Internal Revenue Code §1401 (self-employment tax) and §164(f)
  (above-the-line deduction for ½ of SE tax).
* IRS Pub 334 — $400 net-earnings-from-self-employment filing floor.

Deferred parts (follow-up work)
-------------------------------
* Lines 1a / 1b (farm income from Schedule F / Schedule K-1 farm) are
  hard-coded to 0 — Schedule F is not yet modeled in this skill.
* Lines 5a / 5b (church employee income) are hard-coded to 0 — church
  employee income is not yet modeled.
* The optional-method worksheets (farm optional method, non-farm
  optional method) are not rendered. Most filers never elect them.
* No cross-check against ``other_taxes_total`` from the engine; see the
  note above.
* Real AcroForm overlay on the IRS fillable PDF is deferred.
"""
from __future__ import annotations

from dataclasses import dataclass, fields as dc_fields
from decimal import Decimal
from pathlib import Path

from skill.scripts.calc.engine import schedule_c_net_profit
from skill.scripts.models import CanonicalReturn

_ZERO = Decimal("0")

# ---------------------------------------------------------------------------
# TY2025 Schedule SE constants
# ---------------------------------------------------------------------------
# SSA 2025 wage base: $176,100 (SSA press release 2024-10-10). Cross-checked
# against skill/reference/ty2025-constants.json
#   -> payroll_taxes.social_security_wage_base == 176100
#   -> schedule_se.note_ss_wage_base_applies mentions 176100
# IRS Schedule SE instructions 2024 likewise tie the SS portion of SE tax to
# the current-year SSA wage base.
SS_WAGE_BASE_TY2025: Decimal = Decimal("176100")

# Net-earnings-from-self-employment multiplier = 100% - 7.65% employer share
# that self-employed can't deduct from gross. See Schedule SE line 4a.
SE_NET_EARNINGS_FRACTION: Decimal = Decimal("0.9235")

# Social Security OASDI rate (self-employed share = 2x employee rate 6.2%).
SS_RATE_SE: Decimal = Decimal("0.124")

# Medicare HI rate (self-employed share = 2x employee rate 1.45%). No cap.
MEDICARE_RATE_SE: Decimal = Decimal("0.029")

# Pub 334: file Schedule SE if net SE earnings >= $400. This is a HARD
# statutory floor (§6017) — unchanged since 1990.
SE_FILING_FLOOR: Decimal = Decimal("400")

# §164(f) above-the-line deduction = 50% of SE tax.
HALF_SE_TAX_FRACTION: Decimal = Decimal("0.5")


# ---------------------------------------------------------------------------
# Layer 1: field dataclass and computation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleSEFields:
    """Frozen snapshot of Schedule SE line values, ready for rendering.

    Field names follow the TY2024 Schedule SE line numbers (assumed stable
    for TY2025). All numeric fields are ``Decimal``.

    Layer 1 hand-computes these via the formulas in the IRS 2024 Schedule
    SE instructions. The ``compute_schedule_se_fields`` function does NOT
    modify the engine's computed totals.
    """

    # Filing / header (non-Decimal)
    taxpayer_name: str = ""
    taxpayer_ssn: str = ""

    # Part I — Self-Employment Tax
    # Line 1a: Net farm profit from Schedule F (DEFERRED — 0).
    line_1a_net_farm_profit: Decimal = _ZERO
    # Line 1b: Social Security retirement/disability benefits received
    #         from a farm partnership (DEFERRED — 0).
    line_1b_ss_farm_optional: Decimal = _ZERO
    # Line 2: Net profit (or loss) from Schedule C (line 31) — summed over
    #         all Schedule Cs the taxpayer files. For MFJ returns this is
    #         the taxpayer-side Schedule Cs only; a spouse filing Schedule
    #         SE attaches a second form (handled via a follow-up).
    line_2_net_profit_schedule_c: Decimal = _ZERO
    # Line 3: Combine lines 1a, 1b, and 2.
    line_3_combine_1a_1b_2: Decimal = _ZERO
    # Line 4a: Line 3 x 92.35%. (If less than $400 and line 1a is non-zero,
    #          special farm rules apply — not handled; see deferred parts.)
    line_4a_net_earnings_times_9235: Decimal = _ZERO
    # Line 4b: Optional methods (farm/non-farm) (DEFERRED — 0).
    line_4b_optional_methods: Decimal = _ZERO
    # Line 4c: Combine 4a + 4b. If less than $400, no SE tax — stop here.
    line_4c_combine_4a_4b: Decimal = _ZERO
    # Line 5a: Church employee income (DEFERRED — 0).
    line_5a_church_employee_income: Decimal = _ZERO
    # Line 5b: Line 5a x 92.35%. (DEFERRED — 0.)
    line_5b_church_times_9235: Decimal = _ZERO
    # Line 6: Add 4c + 5b. Net earnings from self-employment.
    line_6_net_earnings_from_se: Decimal = _ZERO
    # Line 7: Max amount subject to Social Security tax — SS wage base
    #         for the year (TY2025: $176,100). Fixed constant per IRS.
    line_7_ss_wage_base: Decimal = _ZERO
    # Line 8a: Total W-2 social security wages + tips
    #         (W-2 box 3 + box 7). Primary taxpayer's W-2s only when the
    #         SE income is the taxpayer's; spouse is handled on their own
    #         Schedule SE.
    line_8a_w2_ss_wages_and_tips: Decimal = _ZERO
    # Line 8b: Unreported tips subject to SS tax (from Form 4137).
    #          Not yet modeled — 0.
    line_8b_unreported_tips: Decimal = _ZERO
    # Line 8c: Wages subject to SS tax from Form 8919 (uncollected SS/Med
    #         tax on wages). Not yet modeled — 0.
    line_8c_wages_8919: Decimal = _ZERO
    # Line 8d: Add 8a + 8b + 8c.
    line_8d_sum_8a_8b_8c: Decimal = _ZERO
    # Line 9: Subtract line 8d from line 7. This is the REMAINING room
    #         under the SS wage base that SE income is taxed on. If zero
    #         or less, line 10 is 0.
    line_9_subtract_8d_from_7: Decimal = _ZERO
    # Line 10: Multiply the smaller of line 6 or line 9 by 12.4%.
    line_10_ss_portion: Decimal = _ZERO
    # Line 11: Multiply line 6 by 2.9% (Medicare — no wage cap).
    line_11_medicare_portion: Decimal = _ZERO
    # Line 12: Self-employment tax. Add lines 10 and 11.
    line_12_se_tax: Decimal = _ZERO
    # Line 13: Deduction for ½ SE tax. Multiply line 12 by 50%. Flows to
    #          Schedule 1, line 15 (above-the-line adjustment).
    line_13_deductible_half_se_tax: Decimal = _ZERO


def _dec(x: Decimal | None) -> Decimal:
    """Coerce an Optional[Decimal] to a concrete Decimal (None -> 0)."""
    return x if x is not None else _ZERO


def _sum(iterable) -> Decimal:
    return sum(iterable, start=_ZERO)


def _taxpayer_se_net_profit(return_: CanonicalReturn) -> Decimal:
    """Sum Schedule C net profit across Schedule Cs attributable to the
    primary taxpayer.

    For v1, Schedule C's ``proprietor_is_taxpayer`` flag selects taxpayer
    vs. spouse Schedule Cs. Spouse Schedule Cs would attach a second
    Schedule SE (not yet rendered). If the flag is missing / defaulted,
    we treat the Schedule C as the taxpayer's.
    """
    total = _ZERO
    for sc in return_.schedules_c:
        is_tp = getattr(sc, "proprietor_is_taxpayer", True)
        if is_tp:
            total += schedule_c_net_profit(sc)
    return total


def _taxpayer_w2_ss_wages_and_tips(return_: CanonicalReturn) -> Decimal:
    """Sum W-2 box 3 (SS wages) + box 7 (SS tips) across taxpayer W-2s.

    The spouse's W-2 SS wages do NOT affect the taxpayer's Schedule SE
    line 8a — SS wage base is per-person.
    """
    total = _ZERO
    for w2 in return_.w2s:
        if not getattr(w2, "employee_is_taxpayer", True):
            continue
        total += _dec(w2.box3_social_security_wages)
        total += _dec(w2.box7_social_security_tips)
    return total


def compute_schedule_se_fields(return_: CanonicalReturn) -> ScheduleSEFields:
    """Map a CanonicalReturn onto a ``ScheduleSEFields`` dataclass.

    Hand-computes every Schedule SE line from the canonical return using
    the formulas in the IRS 2024 Schedule SE instructions. Does NOT rely
    on ``tenforty`` or the engine's ``ComputedTotals.other_taxes_total``
    (which only exposes a total, not the line-level breakdown).

    This function is SAFE to call on any ``CanonicalReturn`` — even one
    that has not been passed through ``engine.compute``. The only engine
    helper used is ``schedule_c_net_profit``, which is a pure function of
    ``ScheduleC``.
    """
    # -- header ---------------------------------------------------------
    taxpayer_name = f"{return_.taxpayer.first_name} {return_.taxpayer.last_name}"
    taxpayer_ssn = return_.taxpayer.ssn or ""

    # -- Part I line-by-line --------------------------------------------
    line_1a = _ZERO  # Schedule F not modeled
    line_1b = _ZERO  # farm partnership SS benefits not modeled
    line_2 = _taxpayer_se_net_profit(return_)
    line_3 = line_1a + line_1b + line_2

    line_4a = line_3 * SE_NET_EARNINGS_FRACTION
    line_4b = _ZERO  # optional methods not modeled
    line_4c = line_4a + line_4b

    line_5a = _ZERO  # church employee income not modeled
    line_5b = line_5a * SE_NET_EARNINGS_FRACTION

    line_6 = line_4c + line_5b
    line_7 = SS_WAGE_BASE_TY2025

    # Line 8: only count W-2 SS wages/tips belonging to the taxpayer
    # whose Schedule SE this is. (Spouse has their own.)
    line_8a = _taxpayer_w2_ss_wages_and_tips(return_)
    line_8b = _ZERO  # Form 4137 not modeled
    line_8c = _ZERO  # Form 8919 not modeled
    line_8d = line_8a + line_8b + line_8c

    # Line 9: room under the SS wage base.
    line_9 = max(_ZERO, line_7 - line_8d)

    # Line 10: SS portion = min(line_6, line_9) * 12.4%
    ss_base = min(line_6, line_9)
    if ss_base < _ZERO:
        ss_base = _ZERO
    line_10 = ss_base * SS_RATE_SE

    # Line 11: Medicare portion = line_6 * 2.9%
    line_11 = line_6 * MEDICARE_RATE_SE

    # Line 12: total SE tax
    line_12 = line_10 + line_11

    # Line 13: ½ SE tax above-the-line deduction
    line_13 = line_12 * HALF_SE_TAX_FRACTION

    return ScheduleSEFields(
        taxpayer_name=taxpayer_name,
        taxpayer_ssn=taxpayer_ssn,
        line_1a_net_farm_profit=line_1a,
        line_1b_ss_farm_optional=line_1b,
        line_2_net_profit_schedule_c=line_2,
        line_3_combine_1a_1b_2=line_3,
        line_4a_net_earnings_times_9235=line_4a,
        line_4b_optional_methods=line_4b,
        line_4c_combine_4a_4b=line_4c,
        line_5a_church_employee_income=line_5a,
        line_5b_church_times_9235=line_5b,
        line_6_net_earnings_from_se=line_6,
        line_7_ss_wage_base=line_7,
        line_8a_w2_ss_wages_and_tips=line_8a,
        line_8b_unreported_tips=line_8b,
        line_8c_wages_8919=line_8c,
        line_8d_sum_8a_8b_8c=line_8d,
        line_9_subtract_8d_from_7=line_9,
        line_10_ss_portion=line_10,
        line_11_medicare_portion=line_11,
        line_12_se_tax=line_12,
        line_13_deductible_half_se_tax=line_13,
    )


def schedule_se_required(return_: CanonicalReturn) -> bool:
    """Return ``True`` iff Schedule SE must be filed.

    Per IRS Pub 334 and §6017, Schedule SE is required when NET EARNINGS
    FROM SELF-EMPLOYMENT (Schedule SE line 4c + line 5b = line 6) reach
    or exceed $400 for the year. Church employee income has its own,
    lower ($108.28) threshold, but church income is not yet modeled here
    so the $400 floor is the only gate.
    """
    fields = compute_schedule_se_fields(return_)
    return fields.line_6_net_earnings_from_se >= SE_FILING_FLOOR


# ---------------------------------------------------------------------------
# Layer 2: AcroForm overlay PDF rendering (wave 5)
# ---------------------------------------------------------------------------
#
# Layer 2 was previously a reportlab tabular scaffold; wave 5 replaced
# it with a real AcroForm overlay on the IRS fillable Schedule SE PDF.
# The widget map at ``skill/reference/schedule-se-acroform-map.json``
# ties every numeric Layer 1 line to its widget on page 1 (Part I).
# Page 2 (Part II — optional methods) is left blank because Layer 1
# does not model the optional methods.

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEDULE_SE_MAP_PATH = (
    _REPO_ROOT / "skill" / "reference" / "schedule-se-acroform-map.json"
)
_SCHEDULE_SE_PDF_PATH = (
    _REPO_ROOT / "skill" / "reference" / "irs_forms" / "f1040sse.pdf"
)


def _format_decimal(value: Decimal) -> str:
    """Format a Decimal as a plain ``"12716.60"`` for AcroForm text fields.

    Zero is rendered as the empty string so the IRS PDF stays visually
    blank for cells the filer doesn't use. Cents are rounded with the
    Decimal default (ROUND_HALF_EVEN); intermediate Schedule SE
    arithmetic produces values with up to 12 fractional digits, which
    we collapse to two for the printed form.
    """
    q = value.quantize(Decimal("0.01"))
    if q == Decimal("0.00"):
        return ""
    return f"{q:.2f}"


def _build_widget_values(
    fields: ScheduleSEFields,
    widget_map: dict,
) -> dict[str, str]:
    """Translate a ``ScheduleSEFields`` snapshot to a widget_name->str dict."""
    out: dict[str, str] = {}
    mapping = widget_map["mapping"]

    for f in dc_fields(fields):
        sem = f.name
        if sem not in mapping:
            continue
        wn = mapping[sem]["widget_name"]
        if "*" in wn:
            continue
        value = getattr(fields, sem)
        if isinstance(value, Decimal):
            out[wn] = _format_decimal(value)
        elif value is None:
            out[wn] = ""
        else:
            out[wn] = str(value)
    return out


def render_schedule_se_pdf(fields: ScheduleSEFields, out_path: Path) -> Path:
    """Render a Schedule SE PDF by overlaying ``fields`` on the IRS fillable PDF.

    Loads the wave-5 widget map, validates the on-disk source PDF
    SHA-256, fills the widgets via
    ``skill.scripts.output._acroform_overlay.fill_acroform_pdf``, and
    writes to ``out_path``. Raises ``RuntimeError`` if the source PDF is
    missing or has been re-issued (SHA mismatch).

    Returns ``out_path`` for convenience.
    """
    from skill.scripts.output._acroform_overlay import (
        fill_acroform_pdf,
        load_widget_map_as_dict,
        verify_pdf_sha256,
    )

    widget_map = load_widget_map_as_dict(_SCHEDULE_SE_MAP_PATH)
    verify_pdf_sha256(_SCHEDULE_SE_PDF_PATH, widget_map["source_pdf_sha256"])
    widget_values = _build_widget_values(fields, widget_map)
    return fill_acroform_pdf(_SCHEDULE_SE_PDF_PATH, widget_values, Path(out_path))
