"""Form 8995 — Qualified Business Income Deduction Simplified Computation.

Two-layer renderer for Form 8995:

* **Layer 1** (:func:`compute_form_8995_fields`) maps a computed
  :class:`CanonicalReturn` onto a :class:`Form8995Fields` frozen
  dataclass whose attribute names mirror the Form 8995 (TY2025) line
  numbers. It does NOT recompute QBI — every value comes from the
  engine's QBI patch output stored on ``ComputedTotals``.

* **Layer 2** (:func:`render_form_8995_pdf`) writes a minimal tabular
  PDF using ``reportlab``. This is a human-readable scaffold, NOT a
  filled IRS Form 8995 — a real AcroForm overlay is a future task.

Authority
---------
* IRS Form 8995 (TY2025): https://www.irs.gov/pub/irs-pdf/f8995.pdf
* Instructions: https://www.irs.gov/instructions/i8995

The simplified Form 8995 is used when taxable income before QBI is at
or below $197,300 (Single/HoH/MFS/QSS) or $394,600 (MFJ).

Key lines:
  Line 1-5: Individual qualified business entries (name, TIN, QBI amount)
  Line 6:   Total QBI
  Line 10:  QBI component of deduction (line 6 × 20%)
  Line 11:  Taxable income before QBI deduction
  Line 12:  Net capital gain (0 for simplified v1)
  Line 13:  Line 11 − line 12
  Line 14:  Income limitation (line 13 × 20%)
  Line 15:  QBI deduction = smaller of line 10 and line 14
  Line 16:  Total QBI deduction (= line 15 for simplified)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from skill.scripts.calc.engine import schedule_c_net_profit, schedule_e_property_net
from skill.scripts.models import CanonicalReturn

_ZERO = Decimal("0")
_CENTS = Decimal("0.01")
_QBI_RATE = Decimal("0.20")


# ---------------------------------------------------------------------------
# Layer 1: field dataclass and computation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QBIBusinessEntry:
    """One qualified business on Form 8995 lines 1-5."""

    business_name: str
    tin: str | None = None
    qbi_amount: Decimal = _ZERO


@dataclass(frozen=True)
class Form8995Fields:
    """Frozen snapshot of Form 8995 line values, ready for rendering.

    Field names mirror the TY2025 Form 8995 lines.
    """

    taxpayer_name: str = ""
    taxpayer_ssn: str = ""

    # Lines 1-5: up to 5 qualified business entries
    business_entries: tuple[QBIBusinessEntry, ...] = ()

    # Line 6: Total QBI (sum of all business entries)
    line_6_total_qbi: Decimal = _ZERO

    # Line 10: QBI component of deduction (line 6 × 20%)
    line_10_qbi_component: Decimal = _ZERO

    # Line 11: Taxable income before QBI deduction
    line_11_taxable_income_before_qbi: Decimal = _ZERO

    # Line 12: Net capital gain (0 for simplified v1)
    line_12_net_capital_gain: Decimal = _ZERO

    # Line 13: Line 11 minus line 12
    line_13_adjusted_ti: Decimal = _ZERO

    # Line 14: Income limitation (line 13 × 20%)
    line_14_income_limitation: Decimal = _ZERO

    # Line 15: QBI deduction (smaller of line 10 and line 14)
    line_15_qbi_deduction: Decimal = _ZERO

    # Line 16: Total QBI deduction (= line 15 for simplified)
    line_16_total_deduction: Decimal = _ZERO


def _cents(v: Decimal) -> Decimal:
    return v.quantize(_CENTS, rounding=ROUND_HALF_UP)


def compute_form_8995_fields(return_: CanonicalReturn) -> Form8995Fields:
    """Map a computed CanonicalReturn onto Form8995Fields.

    The input ``return_`` must have been passed through
    ``skill.scripts.calc.engine.compute`` so that
    ``ComputedTotals.qbi_deduction`` is populated.
    """
    c = return_.computed
    taxpayer_name = f"{return_.taxpayer.first_name} {return_.taxpayer.last_name}"
    taxpayer_ssn = return_.taxpayer.ssn

    # Build business entries from QBI sources
    entries: list[QBIBusinessEntry] = []

    # Schedule C businesses
    for sc in return_.schedules_c:
        net = schedule_c_net_profit(sc)
        entries.append(
            QBIBusinessEntry(
                business_name=sc.business_name,
                tin=sc.ein,
                qbi_amount=_cents(net),
            )
        )

    # Schedule E QBI-qualified properties
    for sched in return_.schedules_e:
        for prop in sched.properties:
            if prop.qbi_qualified:
                net = schedule_e_property_net(prop)
                addr = prop.address
                name = f"Rental: {addr.street1}, {addr.city} {addr.state}"
                entries.append(
                    QBIBusinessEntry(
                        business_name=name,
                        tin=None,
                        qbi_amount=_cents(net),
                    )
                )

    # K-1 QBI-qualified entities
    for k1 in return_.schedules_k1:
        if k1.qbi_qualified:
            entries.append(
                QBIBusinessEntry(
                    business_name=k1.source_name,
                    tin=k1.source_ein,
                    qbi_amount=_cents(k1.ordinary_business_income),
                )
            )

    total_qbi = sum((e.qbi_amount for e in entries), start=_ZERO)
    qbi_deduction = c.qbi_deduction if c.qbi_deduction is not None else _ZERO

    # Reconstruct the Form 8995 intermediate lines from the QBI result
    twenty_pct_qbi = _cents(max(_ZERO, total_qbi) * _QBI_RATE) if total_qbi > _ZERO else _ZERO

    # TI before QBI = AGI - (deduction_taken - qbi_deduction)
    # Because deduction_taken already includes QBI in the engine output,
    # we need to back it out for the "before QBI" figure.
    agi = c.adjusted_gross_income if c.adjusted_gross_income is not None else _ZERO
    deduction_taken = c.deduction_taken if c.deduction_taken is not None else _ZERO
    # deduction_taken = std/itemized + qbi, so std/itemized = deduction_taken - qbi
    std_itemized = deduction_taken - qbi_deduction
    ti_before_qbi = max(_ZERO, agi - std_itemized)

    net_cap_gain = _ZERO  # v1: simplified, no net cap gain adjustment
    adjusted_ti = max(_ZERO, ti_before_qbi - net_cap_gain)
    twenty_pct_ti = _cents(adjusted_ti * _QBI_RATE)

    return Form8995Fields(
        taxpayer_name=taxpayer_name,
        taxpayer_ssn=taxpayer_ssn,
        business_entries=tuple(entries),
        line_6_total_qbi=_cents(total_qbi),
        line_10_qbi_component=twenty_pct_qbi,
        line_11_taxable_income_before_qbi=_cents(ti_before_qbi),
        line_12_net_capital_gain=net_cap_gain,
        line_13_adjusted_ti=_cents(adjusted_ti),
        line_14_income_limitation=twenty_pct_ti,
        line_15_qbi_deduction=_cents(qbi_deduction),
        line_16_total_deduction=_cents(qbi_deduction),
    )


# ---------------------------------------------------------------------------
# Layer 2: reportlab scaffold
# ---------------------------------------------------------------------------


def render_form_8995_pdf(fields: Form8995Fields, out_path: Path) -> Path:
    """Render a Form 8995 PDF scaffold using reportlab.

    Produces a simple tabular PDF listing every field and its value.
    This is a human-readable scaffold, not a filled IRS form.

    Returns ``out_path`` for convenience.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    c = canvas.Canvas(str(out_path), pagesize=letter)
    width, height = letter

    # Title
    c.setFont("Helvetica-Bold", 14)
    c.drawString(
        1 * inch, height - 1 * inch,
        "Form 8995 -- Qualified Business Income Deduction (TY2025)",
    )

    c.setFont("Helvetica", 10)
    y = height - 1.5 * inch

    # Header
    c.drawString(1 * inch, y, f"Taxpayer: {fields.taxpayer_name}")
    y -= 0.25 * inch
    c.drawString(1 * inch, y, f"SSN: {fields.taxpayer_ssn}")
    y -= 0.4 * inch

    # Business entries (lines 1-5)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(1 * inch, y, "Qualified Businesses (Lines 1-5)")
    y -= 0.3 * inch
    c.setFont("Helvetica", 9)

    if fields.business_entries:
        for i, entry in enumerate(fields.business_entries, start=1):
            tin_str = f" (TIN: {entry.tin})" if entry.tin else ""
            c.drawString(
                1.2 * inch, y,
                f"  {i}. {entry.business_name}{tin_str}: ${entry.qbi_amount:,.2f}",
            )
            y -= 0.2 * inch
    else:
        c.drawString(1.2 * inch, y, "  (none)")
        y -= 0.2 * inch

    y -= 0.25 * inch

    # Summary lines
    c.setFont("Helvetica-Bold", 11)
    c.drawString(1 * inch, y, "Deduction Computation")
    y -= 0.3 * inch
    c.setFont("Helvetica", 10)

    summary_lines = [
        ("Line 6", f"Total QBI: ${fields.line_6_total_qbi:,.2f}"),
        ("Line 10", f"QBI component (20% of QBI): ${fields.line_10_qbi_component:,.2f}"),
        ("Line 11", f"Taxable income before QBI: ${fields.line_11_taxable_income_before_qbi:,.2f}"),
        ("Line 12", f"Net capital gain: ${fields.line_12_net_capital_gain:,.2f}"),
        ("Line 13", f"Line 11 - Line 12: ${fields.line_13_adjusted_ti:,.2f}"),
        ("Line 14", f"Income limitation (20% of Line 13): ${fields.line_14_income_limitation:,.2f}"),
        ("Line 15", f"QBI deduction (smaller of 10 and 14): ${fields.line_15_qbi_deduction:,.2f}"),
        ("Line 16", f"Total QBI deduction: ${fields.line_16_total_deduction:,.2f}"),
    ]
    for label, value in summary_lines:
        c.drawString(1.2 * inch, y, f"{label}  {value}")
        y -= 0.25 * inch

    # Footer
    y -= 0.5 * inch
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(
        1 * inch, y,
        "SCAFFOLD -- This is a human-readable summary, not a filled IRS Form 8995.",
    )

    c.save()
    return out_path


# ---------------------------------------------------------------------------
# Gate helper (for pipeline)
# ---------------------------------------------------------------------------


def form_8995_required(return_: CanonicalReturn) -> bool:
    """Return True when Form 8995 should be emitted.

    The form is required when the engine computed a nonzero QBI deduction.
    """
    qbi = return_.computed.qbi_deduction
    return qbi is not None and qbi > _ZERO
