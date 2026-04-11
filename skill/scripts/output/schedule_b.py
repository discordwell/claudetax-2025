"""Schedule B (Interest and Ordinary Dividends) output renderer — two-layer scaffold.

SCAFFOLD NOTICE
===============
This module is a SCAFFOLD for IRS Schedule B (TY2024 layout, assumed
stable for TY2025). It mirrors the two-layer design of
``skill.scripts.output.form_1040``:

* Layer 1 (``compute_schedule_b_fields``) is a pure mapping from the
  canonical return's ``forms_1099_int`` and ``forms_1099_div`` lists onto
  the Schedule B structure. It DOES NOT recompute tax or re-derive
  anything the engine already produced — it just routes values onto
  Schedule B line names.

* Layer 2 (``render_schedule_b_pdf``) writes a minimal reportlab
  text-tabular PDF for human eyeballing. It is NOT a filled IRS Schedule
  B. Real AcroForm overlay on the IRS fillable PDF is a follow-up task.

Schedule B structure (TY2024 instructions)
------------------------------------------
Part I — Interest
  Line 1 : Payer / amount rows. Per the IRS instructions, each 1099-INT
           box 1 (interest) and box 3 (US savings bond & Treasury
           interest) contribution from the same payer is listed with
           the payer's name. We merge duplicate payer names into a
           single row per payer.
  Line 2 : Add the amounts on line 1.
  Line 3 : Excludable interest on series EE/I US savings bonds issued
           after 1989 from Form 8815. *** DEFERRED — left at 0. ***
  Line 4 : Line 2 minus line 3. This amount flows to Form 1040 line 2b.

Part II — Ordinary Dividends
  Line 5 : Payer / amount rows from 1099-DIV box 1a (ordinary
           dividends). Duplicate payer names are merged.
  Line 6 : Add the amounts on line 5. Flows to Form 1040 line 3b.

Part III — Foreign Accounts and Trusts
  Line 7a: At any time during 2025, did you have a financial interest
           in or signature authority over a foreign financial account?
  Line 7a (FinCEN 114 required?)
  Line 7b: Country name (if 7a is yes and FinCEN 114 is required).
  Line 8 : During 2025, did you receive a distribution from, or were
           you the grantor of, or transferor to, a foreign trust?

Simplifications loudly deferred (TODO for later waves)
------------------------------------------------------
* Line 3 (Form 8815 excludable savings bond interest) is always 0. The
  8815 interview / worksheet is not yet in the canonical model. Callers
  who need this should patch Layer 1 once the model exposes it.
* Part III foreign account / trust flags: **wired in CP8-D** to the
  canonical return fields ``has_foreign_financial_account_over_10k``,
  ``has_foreign_trust_transaction``, and ``foreign_account_countries``.
  ``schedule_b_required`` now correctly flags foreign-based filing
  triggers independent of the $1,500 interest/dividend threshold.
  Line 7b country renders the first entry of ``foreign_account_countries``;
  multi-country enumeration is handled by FinCEN Form 114 (FBAR), not
  Schedule B itself.
* Line 1 "nominee" adjustment lines (the IRS instructions allow a
  subtotal followed by a negative "nominee distribution" line) are not
  modeled. Nominee handling is a follow-up.
* The required-filing threshold is $1,500 per the 2024 IRS instructions
  ("You must file Schedule B (Form 1040) if any of the following
   apply ... (a) You had over $1,500 of taxable interest or ordinary
   dividends."). The threshold is applied strictly ``> 1500`` — exactly
  $1,500 does not require Schedule B.

Sources
-------
* IRS 2024 Instructions for Schedule B (Form 1040), "Who Must File" and
  Part I / Part II / Part III line instructions.
* IRS 2024 Form 1099-INT instructions — box 1 (interest income) and
  box 3 (US savings bond and Treasury interest) both land on Schedule B
  line 1.
* IRS 2024 Form 1099-DIV instructions — box 1a (total ordinary
  dividends) lands on Schedule B line 5.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from skill.scripts.models import CanonicalReturn


_ZERO = Decimal("0")

# IRS 2024 Schedule B filing threshold. See module docstring.
SCHEDULE_B_THRESHOLD = Decimal("1500")


# ---------------------------------------------------------------------------
# Layer 1: field dataclass and computation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleBPayerRow:
    """A single payer / amount row on Schedule B line 1 or line 5."""

    payer_name: str
    amount: Decimal


@dataclass(frozen=True)
class ScheduleBFields:
    """Frozen snapshot of Schedule B line values, ready for rendering.

    Field names follow the TY2024 Schedule B (Form 1040) line numbers
    (assumed stable for TY2025). All amounts are ``Decimal``.
    """

    # Header
    taxpayer_name: str = ""
    taxpayer_ssn: str = ""

    # Part I — Interest
    part_i_line_1_rows: tuple[ScheduleBPayerRow, ...] = ()
    part_i_line_2_total_interest: Decimal = _ZERO
    part_i_line_3_excludable_savings_bond_interest: Decimal = _ZERO
    part_i_line_4_taxable_interest: Decimal = _ZERO

    # Part II — Ordinary Dividends
    part_ii_line_5_rows: tuple[ScheduleBPayerRow, ...] = ()
    part_ii_line_6_total_ordinary_dividends: Decimal = _ZERO

    # Part III — Foreign Accounts and Trusts
    # All default False and carry a loud TODO — see module docstring.
    part_iii_line_7a_foreign_account: bool = False
    part_iii_line_7a_fincen114_required: bool = False
    part_iii_line_7b_fincen114_country: str = ""
    part_iii_line_8_foreign_trust: bool = False

    # Whether Schedule B is REQUIRED for this return.
    is_required: bool = False


def _dec(x: Decimal | None) -> Decimal:
    """Coerce an Optional[Decimal] to a concrete Decimal (None -> 0)."""
    return x if x is not None else _ZERO


def _merge_rows(
    pairs: list[tuple[str, Decimal]],
) -> tuple[ScheduleBPayerRow, ...]:
    """Merge (payer_name, amount) pairs by payer_name, preserving first-seen order.

    Zero-amount entries are dropped, so a payer whose contributions net
    to exactly zero is omitted entirely from the line list.
    """
    order: list[str] = []
    totals: dict[str, Decimal] = {}
    for name, amount in pairs:
        if name not in totals:
            order.append(name)
            totals[name] = _ZERO
        totals[name] = totals[name] + amount
    return tuple(
        ScheduleBPayerRow(payer_name=name, amount=totals[name])
        for name in order
        if totals[name] != _ZERO
    )


def compute_schedule_b_fields(return_: CanonicalReturn) -> ScheduleBFields:
    """Map a CanonicalReturn onto a ScheduleBFields dataclass.

    This is a pure, side-effect-free routing of the return's
    ``forms_1099_int`` and ``forms_1099_div`` lists onto Schedule B line
    names. It does NOT require the return to have been passed through
    ``skill.scripts.calc.engine.compute`` — Schedule B line 4 and line 6
    are derived directly from the canonical 1099 forms, not from
    ``ComputedTotals``.
    """
    # -- header ---------------------------------------------------------
    taxpayer_name = f"{return_.taxpayer.first_name} {return_.taxpayer.last_name}"
    taxpayer_ssn = return_.taxpayer.ssn or ""

    # -- Part I line 1 rows --------------------------------------------
    # Per the IRS 2024 Schedule B instructions, line 1 lists each payer
    # of taxable interest. 1099-INT box 1 (interest income) and box 3
    # (US savings bond and Treasury interest) are BOTH taxable interest
    # that lands on Schedule B line 1. We therefore sum box1 + box3 per
    # payer before listing.
    int_pairs: list[tuple[str, Decimal]] = []
    for f in return_.forms_1099_int:
        amount = _dec(f.box1_interest_income) + _dec(
            f.box3_us_savings_bond_and_treasury_interest
        )
        int_pairs.append((f.payer_name, amount))
    line_1_rows = _merge_rows(int_pairs)

    # -- Part I line 2 -------------------------------------------------
    line_2 = sum((row.amount for row in line_1_rows), start=_ZERO)

    # -- Part I line 3 -------------------------------------------------
    # TODO: Form 8815 excludable savings bond interest is not yet in the
    # canonical model. Always 0 for now.
    line_3 = _ZERO

    # -- Part I line 4 -------------------------------------------------
    line_4 = line_2 - line_3

    # -- Part II line 5 rows -------------------------------------------
    div_pairs: list[tuple[str, Decimal]] = []
    for f in return_.forms_1099_div:
        div_pairs.append((f.payer_name, _dec(f.box1a_ordinary_dividends)))
    line_5_rows = _merge_rows(div_pairs)

    # -- Part II line 6 ------------------------------------------------
    line_6 = sum((row.amount for row in line_5_rows), start=_ZERO)

    # -- Part III ------------------------------------------------------
    # CP8-D: canonical model now carries the Schedule B Part III flags.
    # Line 7a: "At any time during YYYY, did you have a financial
    # interest in or signature authority over a financial account
    # located in a foreign country?" — maps to
    # has_foreign_financial_account_over_10k.
    # Line 7a FinCEN 114 requirement: same flag (true => FBAR required).
    # Line 7b country: first country from foreign_account_countries
    # (renderer shows a single country; multi-country reporting is
    # deferred — FinCEN 114 itself handles the enumeration).
    # Line 8: maps to has_foreign_trust_transaction.
    line_7a_foreign_account = return_.has_foreign_financial_account_over_10k
    line_7a_fincen114_required = return_.has_foreign_financial_account_over_10k
    line_7b_country = (
        return_.foreign_account_countries[0]
        if return_.foreign_account_countries
        else ""
    )
    line_8_foreign_trust = return_.has_foreign_trust_transaction

    # -- Required? -----------------------------------------------------
    required = _is_schedule_b_required(
        total_interest=line_4,
        total_dividends=line_6,
        foreign_account=line_7a_foreign_account,
        foreign_trust=line_8_foreign_trust,
    )

    return ScheduleBFields(
        taxpayer_name=taxpayer_name,
        taxpayer_ssn=taxpayer_ssn,
        part_i_line_1_rows=line_1_rows,
        part_i_line_2_total_interest=line_2,
        part_i_line_3_excludable_savings_bond_interest=line_3,
        part_i_line_4_taxable_interest=line_4,
        part_ii_line_5_rows=line_5_rows,
        part_ii_line_6_total_ordinary_dividends=line_6,
        part_iii_line_7a_foreign_account=line_7a_foreign_account,
        part_iii_line_7a_fincen114_required=line_7a_fincen114_required,
        part_iii_line_7b_fincen114_country=line_7b_country,
        part_iii_line_8_foreign_trust=line_8_foreign_trust,
        is_required=required,
    )


def _is_schedule_b_required(
    total_interest: Decimal,
    total_dividends: Decimal,
    foreign_account: bool,
    foreign_trust: bool,
) -> bool:
    """Pure-argument form of the Schedule B must-file rule.

    Schedule B is required if EITHER:
      * taxable interest (line 4) exceeds $1,500, OR
      * ordinary dividends (line 6) exceeds $1,500, OR
      * any Part III foreign account / trust flag is True.

    The dollar threshold is strict (``> 1500``), per the IRS 2024
    instructions language "You had over $1,500 of ...". Exactly $1,500
    does NOT require Schedule B.

    Note: this is not an exhaustive list of every Schedule B trigger
    from the IRS instructions — it covers the three triggers the
    canonical model can currently observe. Triggers we do NOT model
    (nominee interest, accrued bond interest, seller-financed mortgage
    interest, excludable savings bond interest claim, etc.) are
    follow-ups.
    """
    if total_interest > SCHEDULE_B_THRESHOLD:
        return True
    if total_dividends > SCHEDULE_B_THRESHOLD:
        return True
    if foreign_account or foreign_trust:
        return True
    return False


def schedule_b_required(return_: CanonicalReturn) -> bool:
    """Public helper: is Schedule B REQUIRED for this canonical return?

    Intended to be called by the engine / output pipeline to decide
    whether to render Schedule B at all. Uses the same threshold rule
    as ``compute_schedule_b_fields``.
    """
    fields = compute_schedule_b_fields(return_)
    return fields.is_required


# ---------------------------------------------------------------------------
# Layer 2: AcroForm overlay PDF rendering (wave 5)
# ---------------------------------------------------------------------------
#
# Layer 2 was previously a reportlab tabular scaffold; wave 5 replaced
# it with a real AcroForm overlay on the IRS fillable Schedule B PDF.
# The widget map at ``skill/reference/schedule-b-acroform-map.json``
# provides:
#
#   * ``mapping`` — single-cell line widgets (line 2/3/4 totals,
#     line 6 total, Part III foreign-account / FinCEN / trust checkboxes,
#     header taxpayer name).
#   * ``part_i_line_1_rows_widgets`` — 14 indexed payer/amount widget
#     pairs for Part I line 1.
#   * ``part_ii_line_5_rows_widgets`` — 15 indexed payer/amount widget
#     pairs for Part II line 5.
#
# Rows beyond the 14/15-row limit need a continuation statement and are
# tracked as a follow-up.

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEDULE_B_MAP_PATH = (
    _REPO_ROOT / "skill" / "reference" / "schedule-b-acroform-map.json"
)
_SCHEDULE_B_PDF_PATH = (
    _REPO_ROOT / "skill" / "reference" / "irs_forms" / "f1040sb.pdf"
)


def _format_decimal(value: Decimal) -> str:
    """Format a Decimal as a plain ``"1500.00"`` for AcroForm text fields.

    Zero is rendered as the empty string so the IRS PDF stays visually
    blank for cells the filer doesn't use.
    """
    q = value.quantize(Decimal("0.01"))
    if q == Decimal("0.00"):
        return ""
    return f"{q:.2f}"


def _build_widget_values(
    fields: ScheduleBFields,
    widget_map: dict,
) -> dict[str, str]:
    """Translate a ``ScheduleBFields`` snapshot to a widget_name->str dict."""
    out: dict[str, str] = {}
    mapping = widget_map["mapping"]

    # Single-cell numeric / string lines
    out[mapping["taxpayer_name"]["widget_name"]] = fields.taxpayer_name
    out[mapping["part_i_line_2_total_interest"]["widget_name"]] = _format_decimal(
        fields.part_i_line_2_total_interest
    )
    out[mapping["part_i_line_3_excludable_savings_bond_interest"]["widget_name"]] = (
        _format_decimal(fields.part_i_line_3_excludable_savings_bond_interest)
    )
    out[mapping["part_i_line_4_taxable_interest"]["widget_name"]] = _format_decimal(
        fields.part_i_line_4_taxable_interest
    )
    out[mapping["part_ii_line_6_total_ordinary_dividends"]["widget_name"]] = (
        _format_decimal(fields.part_ii_line_6_total_ordinary_dividends)
    )
    out[mapping["part_iii_line_7b_fincen114_country"]["widget_name"]] = (
        fields.part_iii_line_7b_fincen114_country or ""
    )

    # Part III checkboxes — Yes box only. Filling "" leaves it blank.
    out[mapping["part_iii_line_7a_foreign_account"]["widget_name"]] = (
        "Yes" if fields.part_iii_line_7a_foreign_account else ""
    )
    out[mapping["part_iii_line_7a_fincen114_required"]["widget_name"]] = (
        "Yes" if fields.part_iii_line_7a_fincen114_required else ""
    )
    out[mapping["part_iii_line_8_foreign_trust"]["widget_name"]] = (
        "Yes" if fields.part_iii_line_8_foreign_trust else ""
    )

    # Part I line 1 repeating rows
    part_i_row_widgets = widget_map.get("part_i_line_1_rows_widgets", [])
    for i, row in enumerate(fields.part_i_line_1_rows):
        if i >= len(part_i_row_widgets):
            # Continuation statement needed; bail out (do not raise — the
            # IRS PDF can still be rendered with the first 14 rows).
            break
        slot = part_i_row_widgets[i]
        out[slot["payer_widget"]["widget_name"]] = row.payer_name
        out[slot["amount_widget"]["widget_name"]] = _format_decimal(row.amount)

    # Part II line 5 repeating rows
    part_ii_row_widgets = widget_map.get("part_ii_line_5_rows_widgets", [])
    for i, row in enumerate(fields.part_ii_line_5_rows):
        if i >= len(part_ii_row_widgets):
            break
        slot = part_ii_row_widgets[i]
        out[slot["payer_widget"]["widget_name"]] = row.payer_name
        out[slot["amount_widget"]["widget_name"]] = _format_decimal(row.amount)

    return out


def render_schedule_b_pdf(fields: ScheduleBFields, out_path: Path) -> Path:
    """Render a Schedule B PDF by overlaying ``fields`` on the IRS fillable PDF.

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

    widget_map = load_widget_map_as_dict(_SCHEDULE_B_MAP_PATH)
    verify_pdf_sha256(_SCHEDULE_B_PDF_PATH, widget_map["source_pdf_sha256"])
    widget_values = _build_widget_values(fields, widget_map)
    return fill_acroform_pdf(_SCHEDULE_B_PDF_PATH, widget_values, Path(out_path))


__all__ = [
    "SCHEDULE_B_THRESHOLD",
    "ScheduleBFields",
    "ScheduleBPayerRow",
    "compute_schedule_b_fields",
    "render_schedule_b_pdf",
    "schedule_b_required",
]
