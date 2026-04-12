"""Form 4797 (Sales of Business Property) output renderer.

Two-layer design mirroring ``skill.scripts.output.schedule_d``:

* Layer 1 — :func:`compute_form_4797_fields` reads
  ``CanonicalReturn.forms_4797`` and classifies each sale by its
  ``section_type`` into Part I (§1231 gains/losses), Part II
  (§1245/§1250 ordinary gains), or Part III (§1250 recapture). The
  result is a frozen ``Form4797Fields`` dataclass.

* Layer 2 — :func:`render_form_4797_pdf` writes a simple tabular PDF
  via reportlab listing each Part and its totals.

Form 4797 structure (TY2025)
----------------------------
Part I — Sales or Exchanges of Property Used in a Trade or Business
and Involuntary Conversions From Other Than Casualty or Theft—
Property Held More Than 1 Year (§1231)

  Each §1231 sale produces a gain/loss = gross_sales_price -
  (cost_or_basis - depreciation_allowed). The Part I net is §1231
  gain (treated as LTCG on Schedule D line 11) or §1231 loss
  (ordinary, Schedule 1 line 4).

Part II — Ordinary Gains and Losses (§1245 recapture)

  §1245 property: all depreciation taken is recaptured as ordinary
  income. If gain exceeds depreciation, the excess is §1231 gain.
  §1250 property that does not go to Part III also lands here.

  Net Part II ordinary gain/loss flows to Schedule 1 line 4.

Part III — Gain From Disposition of Property Under §§1245, 1250,
1252, 1254, and 1255 (unrecaptured §1250 gain)

  For §1250 real property, the "additional depreciation" (accelerated
  minus straight-line) is recaptured as ordinary income in Part II.
  The remaining depreciation (straight-line portion) is "unrecaptured
  §1250 gain" taxed at a max 25% rate. Any gain above total
  depreciation is §1231 gain.

  For TY2025 (post-1986 real property), MACRS straight-line means
  additional depreciation is typically $0, so all depreciation is
  unrecaptured §1250 gain.

Flows to:
  - Schedule 1 line 4: net Part I §1231 loss + Part II ordinary gain/loss
  - Schedule D line 11: net Part I §1231 gain (when positive)
  - Schedule D line 19: unrecaptured §1250 gain (25% rate bucket)

Sources
-------
* IRS Form 4797 (TY2025): https://www.irs.gov/pub/irs-pdf/f4797.pdf
* IRS Instructions for Form 4797:
  https://www.irs.gov/pub/irs-pdf/i4797.pdf
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal

from skill.scripts.models import CanonicalReturn, Form4797Sale

_ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Layer 1: field dataclass and computation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Form4797SaleResult:
    """Computed result for a single Form 4797 sale.

    Produced by the classification step inside ``compute_form_4797_fields``.
    """

    sale: Form4797Sale
    """Original sale data from the canonical return."""
    total_gain_or_loss: Decimal = _ZERO
    """Gain (positive) or loss (negative) = gross_sales_price -
    adjusted_basis where adjusted_basis = cost_or_basis -
    depreciation_allowed."""
    ordinary_gain: Decimal = _ZERO
    """Portion of gain recaptured as ordinary income (§1245 full
    recapture, or §1250 additional depreciation recapture)."""
    section_1231_gain_or_loss: Decimal = _ZERO
    """Portion treated as §1231 gain/loss — flows to Part I."""
    unrecaptured_1250_gain: Decimal = _ZERO
    """Straight-line depreciation portion of §1250 gain — taxed at max
    25% rate. Only nonzero for §1250 property."""


@dataclass(frozen=True)
class Form4797Fields:
    """Frozen snapshot of Form 4797 line values, ready for rendering.

    Field names follow the TY2025 Form 4797 structure. All numeric
    fields are ``Decimal``; header fields are ``str``.
    """

    # Header
    taxpayer_name: str = ""
    taxpayer_ssn: str = ""

    # ------------------------------------------------------------------
    # Part I — §1231 gains and losses
    # ------------------------------------------------------------------
    part_i_sales: tuple[Form4797SaleResult, ...] = ()
    part_i_net_gain_or_loss: Decimal = _ZERO
    """Net of all Part I §1231 gains/losses. If positive, treated as
    long-term capital gain on Schedule D line 11. If negative, treated
    as ordinary loss on Schedule 1 line 4."""

    # ------------------------------------------------------------------
    # Part II — Ordinary gains and losses
    # ------------------------------------------------------------------
    part_ii_sales: tuple[Form4797SaleResult, ...] = ()
    part_ii_ordinary_gain_or_loss: Decimal = _ZERO
    """Net ordinary gain/loss from Part II. Flows to Schedule 1 line 4."""

    # ------------------------------------------------------------------
    # Part III — §1250 recapture (unrecaptured §1250 gain)
    # ------------------------------------------------------------------
    part_iii_sales: tuple[Form4797SaleResult, ...] = ()
    part_iii_total_unrecaptured_1250_gain: Decimal = _ZERO
    """Total unrecaptured §1250 gain — taxed at max 25% rate. Flows to
    Schedule D line 19."""

    # ------------------------------------------------------------------
    # Totals flowing out
    # ------------------------------------------------------------------
    schedule_1_line_4: Decimal = _ZERO
    """Amount that flows to Schedule 1 line 4 (other gains/losses).
    = Part II ordinary gain/loss + Part I §1231 loss (when Part I net
    is negative). When Part I net is positive it goes to Schedule D
    instead."""
    schedule_d_line_11: Decimal = _ZERO
    """Amount that flows to Schedule D line 11 (§1231 gain). Only
    nonzero when Part I net is a gain (positive)."""
    schedule_d_line_19: Decimal = _ZERO
    """Unrecaptured §1250 gain flowing to Schedule D line 19."""


def _classify_sale(sale: Form4797Sale) -> Form4797SaleResult:
    """Classify a single Form 4797 sale into gain components.

    Returns a ``Form4797SaleResult`` with the ordinary gain, §1231
    gain/loss, and unrecaptured §1250 gain broken out.
    """
    adjusted_basis = sale.cost_or_basis - sale.depreciation_allowed
    total_gain_or_loss = sale.gross_sales_price - adjusted_basis

    if sale.section_type == "1245":
        # §1245: ALL depreciation is recaptured as ordinary income.
        # If gain > depreciation, excess is §1231 gain.
        if total_gain_or_loss <= _ZERO:
            # Loss — entire amount is §1231 loss
            return Form4797SaleResult(
                sale=sale,
                total_gain_or_loss=total_gain_or_loss,
                ordinary_gain=_ZERO,
                section_1231_gain_or_loss=total_gain_or_loss,
                unrecaptured_1250_gain=_ZERO,
            )
        ordinary = min(total_gain_or_loss, sale.depreciation_allowed)
        excess = total_gain_or_loss - ordinary
        return Form4797SaleResult(
            sale=sale,
            total_gain_or_loss=total_gain_or_loss,
            ordinary_gain=ordinary,
            section_1231_gain_or_loss=excess,
            unrecaptured_1250_gain=_ZERO,
        )

    elif sale.section_type == "1250":
        # §1250: For post-1986 real property (MACRS straight-line),
        # "additional depreciation" (accelerated minus straight-line)
        # is typically $0 because MACRS uses straight-line for real
        # property. So all depreciation is "unrecaptured §1250 gain"
        # taxed at max 25%.
        #
        # Gain above total depreciation is §1231 gain.
        # Loss is §1231 loss.
        if total_gain_or_loss <= _ZERO:
            return Form4797SaleResult(
                sale=sale,
                total_gain_or_loss=total_gain_or_loss,
                ordinary_gain=_ZERO,
                section_1231_gain_or_loss=total_gain_or_loss,
                unrecaptured_1250_gain=_ZERO,
            )
        # Additional depreciation (accelerated - straight-line).
        # For post-1986 MACRS real property this is $0. We model it
        # as zero; a future wave can add an `additional_depreciation`
        # field to Form4797Sale for pre-1987 property.
        additional_depreciation = _ZERO
        ordinary = additional_depreciation
        # Unrecaptured = min(gain, total depreciation) - additional
        unrecaptured = min(total_gain_or_loss, sale.depreciation_allowed) - additional_depreciation
        unrecaptured = max(_ZERO, unrecaptured)
        excess = total_gain_or_loss - sale.depreciation_allowed
        section_1231 = max(_ZERO, excess)
        return Form4797SaleResult(
            sale=sale,
            total_gain_or_loss=total_gain_or_loss,
            ordinary_gain=ordinary,
            section_1231_gain_or_loss=section_1231,
            unrecaptured_1250_gain=unrecaptured,
        )

    else:  # "1231"
        # Pure §1231: no depreciation recapture computation — the
        # entire gain/loss is §1231. Caller classifies these into
        # Part I directly.
        return Form4797SaleResult(
            sale=sale,
            total_gain_or_loss=total_gain_or_loss,
            ordinary_gain=_ZERO,
            section_1231_gain_or_loss=total_gain_or_loss,
            unrecaptured_1250_gain=_ZERO,
        )


def compute_form_4797_fields(canonical: CanonicalReturn) -> Form4797Fields:
    """Map ``CanonicalReturn.forms_4797`` onto a ``Form4797Fields`` dataclass.

    Classification rules:

    * ``section_type="1231"`` sales go directly to Part I.
    * ``section_type="1245"`` sales: the depreciation-recapture ordinary
      gain goes to Part II, any excess §1231 gain goes to Part I.
    * ``section_type="1250"`` sales: additional depreciation recapture
      goes to Part II (ordinary), unrecaptured §1250 gain is tracked
      separately (25% rate bucket), and any excess §1231 gain goes to
      Part I.
    """
    if not canonical.forms_4797:
        return Form4797Fields()

    # Classify every sale
    results: list[Form4797SaleResult] = [
        _classify_sale(sale) for sale in canonical.forms_4797
    ]

    # Build Part I (§1231 gains/losses from all section types)
    part_i_results: list[Form4797SaleResult] = []
    part_ii_results: list[Form4797SaleResult] = []
    part_iii_results: list[Form4797SaleResult] = []

    part_i_net = _ZERO
    part_ii_ordinary = _ZERO
    part_iii_unrecaptured = _ZERO

    for r in results:
        # Part I: §1231 gain/loss portion (all section types contribute)
        if r.section_1231_gain_or_loss != _ZERO:
            part_i_results.append(r)
            part_i_net += r.section_1231_gain_or_loss

        # Part II: ordinary gain from §1245 or §1250 additional depreciation
        if r.ordinary_gain != _ZERO:
            part_ii_results.append(r)
            part_ii_ordinary += r.ordinary_gain

        # Part III: unrecaptured §1250 gain
        if r.unrecaptured_1250_gain != _ZERO:
            part_iii_results.append(r)
            part_iii_unrecaptured += r.unrecaptured_1250_gain

    # Schedule 1 line 4: ordinary gain + §1231 loss (when net is negative)
    schedule_1_line_4 = part_ii_ordinary
    if part_i_net < _ZERO:
        # §1231 net loss is ordinary
        schedule_1_line_4 += part_i_net

    # Schedule D line 11: §1231 net gain (when positive)
    schedule_d_line_11 = max(_ZERO, part_i_net)

    # Schedule D line 19: unrecaptured §1250 gain
    schedule_d_line_19 = part_iii_unrecaptured

    taxpayer_name = f"{canonical.taxpayer.first_name} {canonical.taxpayer.last_name}"
    taxpayer_ssn = canonical.taxpayer.ssn or ""

    return Form4797Fields(
        taxpayer_name=taxpayer_name,
        taxpayer_ssn=taxpayer_ssn,
        part_i_sales=tuple(part_i_results),
        part_i_net_gain_or_loss=part_i_net,
        part_ii_sales=tuple(part_ii_results),
        part_ii_ordinary_gain_or_loss=part_ii_ordinary,
        part_iii_sales=tuple(part_iii_results),
        part_iii_total_unrecaptured_1250_gain=part_iii_unrecaptured,
        schedule_1_line_4=schedule_1_line_4,
        schedule_d_line_11=schedule_d_line_11,
        schedule_d_line_19=schedule_d_line_19,
    )


def form_4797_required(canonical: CanonicalReturn) -> bool:
    """Return ``True`` if Form 4797 must be attached to the return."""
    return bool(canonical.forms_4797)


# ---------------------------------------------------------------------------
# Layer 2: reportlab scaffold PDF rendering
# ---------------------------------------------------------------------------


def _format_decimal(value: Decimal) -> str:
    """Format a Decimal as ``"1500.00"`` for display.

    Zero is rendered as the empty string so the scaffold PDF stays
    visually clean for cells the filer doesn't use.
    """
    q = value.quantize(Decimal("0.01"))
    if q == Decimal("0.00"):
        return ""
    return f"{q:.2f}"


def _format_date(d: dt.date | str | None) -> str:
    """Format a date or 'various' for display."""
    if d is None:
        return ""
    if isinstance(d, str):
        return d
    return d.strftime("%m/%d/%Y")


def render_form_4797_pdf(fields: Form4797Fields, out_path: Path) -> Path:
    """Render a Form 4797 scaffold PDF using reportlab.

    This is a minimal tabular layout listing each Part and its line
    values so a human can eyeball the return. It is NOT a filled IRS
    Form 4797 — real AcroForm overlay is a follow-up task.

    Returns ``out_path`` for convenience.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen.canvas import Canvas

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    c = Canvas(str(out_path), pagesize=letter)
    width, height = letter

    # Title
    c.setFont("Helvetica-Bold", 14)
    c.drawString(
        1 * inch, height - 1 * inch,
        "Form 4797 — Sales of Business Property",
    )
    c.setFont("Helvetica", 10)
    c.drawString(1 * inch, height - 1.3 * inch, f"Name: {fields.taxpayer_name}")
    c.drawString(4.5 * inch, height - 1.3 * inch, f"SSN: {fields.taxpayer_ssn}")

    y = height - 1.8 * inch

    def _line(label: str, value: str) -> None:
        nonlocal y
        c.setFont("Helvetica", 9)
        c.drawString(1 * inch, y, label)
        c.drawRightString(7.5 * inch, y, value)
        y -= 14
        if y < 1 * inch:
            c.showPage()
            y = height - 1 * inch

    def _section_header(text: str) -> None:
        nonlocal y
        y -= 6
        c.setFont("Helvetica-Bold", 11)
        c.drawString(1 * inch, y, text)
        y -= 18

    # -- Part I -----------------------------------------------------------
    _section_header("Part I -- Sales or Exchanges of Property Held > 1 Year (section 1231)")
    for r in fields.part_i_sales:
        _line(
            f"  {r.sale.description}  "
            f"({_format_date(r.sale.date_acquired)} - {_format_date(r.sale.date_sold)})",
            _format_decimal(r.section_1231_gain_or_loss),
        )
    _line("Net section 1231 gain or (loss)", _format_decimal(fields.part_i_net_gain_or_loss))

    # -- Part II ----------------------------------------------------------
    _section_header("Part II -- Ordinary Gains and Losses")
    for r in fields.part_ii_sales:
        _line(
            f"  {r.sale.description}  (ordinary recapture)",
            _format_decimal(r.ordinary_gain),
        )
    _line("Net ordinary gain or (loss)", _format_decimal(fields.part_ii_ordinary_gain_or_loss))

    # -- Part III ---------------------------------------------------------
    _section_header("Part III -- Unrecaptured Section 1250 Gain")
    for r in fields.part_iii_sales:
        _line(
            f"  {r.sale.description}  (unrecaptured section 1250)",
            _format_decimal(r.unrecaptured_1250_gain),
        )
    _line("Total unrecaptured section 1250 gain", _format_decimal(fields.part_iii_total_unrecaptured_1250_gain))

    # -- Flow-through summary ---------------------------------------------
    _section_header("Flow-Through Summary")
    _line("-> Schedule 1 line 4 (other gains/losses)", _format_decimal(fields.schedule_1_line_4))
    _line("-> Schedule D line 11 (section 1231 gain)", _format_decimal(fields.schedule_d_line_11))
    _line("-> Schedule D line 19 (unrecaptured section 1250 gain)", _format_decimal(fields.schedule_d_line_19))

    c.save()
    return out_path


__all__ = [
    "Form4797Fields",
    "Form4797SaleResult",
    "compute_form_4797_fields",
    "form_4797_required",
    "render_form_4797_pdf",
]
