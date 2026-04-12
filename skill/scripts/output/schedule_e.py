"""Schedule E (Supplemental Income and Loss) output renderer -- two-layer scaffold.

SCAFFOLD NOTICE
===============
This module is a SCAFFOLD for Schedule E PDF generation, mirroring the
shape of ``skill.scripts.output.form_1040`` so that a later wave can
replace Layer 2 with an AcroForm widget-overlay renderer without
touching Layer 1 or its tests.

* Layer 1 (``compute_schedule_e_fields``) maps a ``CanonicalReturn``'s
  ``ScheduleE`` block onto a frozen dataclass whose field names mirror
  the TY2024 Schedule E line numbers (assumed stable for TY2025). It
  does NOT recompute any tax value -- it delegates per-property net
  income to ``skill.scripts.calc.engine.schedule_e_property_net``.

* Layer 2 (``render_schedule_e_pdf``) writes a minimal tabular PDF using
  ``reportlab``. The table lists every line_name/value pair so a human
  can eyeball the return; this is NOT a filled IRS Schedule E.

Schedule E Part I layout (TY2024, assumed stable for TY2025)
-------------------------------------------------------------
Per the IRS 2024 Instructions for Schedule E (Form 1040):

    Up to 3 properties per page (columns A, B, C):
        1a-1c  Physical address of each property
        1d     Property type (1-7 code)
        2      Yes/No — personal use checkbox (fair rental days / personal
               use days)
        3      Rents received (per property)
        4      Royalties received (per property)
    Expenses per property (lines 5-19):
        5  Advertising
        6  Auto and travel
        7  Cleaning and maintenance
        8  Commissions
        9  Insurance
        10 Legal and other professional fees
        11 Management fees
        12 Mortgage interest paid to banks
        13 Other interest
        14 Repairs
        15 Supplies
        16 Taxes
        17 Utilities
        18 Depreciation expense or depletion
        19 Other (list)
    20 Total expenses per property (sum of 5-19)
    21 Net income or loss per property (rents + royalties - expenses)

    Summary:
        23a Total rental/real estate income (sum of line 21 profits only)
        23b Total rental/real estate losses (sum of line 21 losses only)
        24  Income (sum of 23a, 23b, and any rental from Form 4835)
        25  Losses (after at-risk / passive limits -- deferred)
        26  Total rental real estate and royalty income or loss

    Part II: Income or Loss from Partnerships and S Corporations (stub)
    Part III: Income or Loss from Estates and Trusts (stub)

Design goals
------------
* No tax re-computation. Per-property net delegates to
  ``schedule_e_property_net`` from the engine.
* No modification of the engine, models, registry, or schema.
* Minimal surface area so downstream waves can replace Layer 2 with a
  real AcroForm renderer.

Sources
-------
* IRS 2024 Schedule E (Form 1040) -- Supplemental Income and Loss
  https://www.irs.gov/pub/irs-pdf/f1040se.pdf
* IRS 2024 Instructions for Schedule E
  https://www.irs.gov/pub/irs-pdf/i1040se.pdf
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from skill.scripts.calc.engine import schedule_e_property_net
from skill.scripts.models import CanonicalReturn, ScheduleE, ScheduleEProperty


_ZERO = Decimal("0")
_CENTS = Decimal("0.01")


# ---------------------------------------------------------------------------
# Layer 1: field dataclass and computation
# ---------------------------------------------------------------------------


def _q(x: Decimal) -> Decimal:
    """Quantize to cents using ROUND_HALF_UP."""
    return x.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _format_address(p: ScheduleEProperty) -> str:
    """Flatten a ScheduleEProperty's address into a single line."""
    addr = p.address
    parts = [addr.street1]
    if addr.street2:
        parts.append(addr.street2)
    city_state_zip = f"{addr.city}, {addr.state} {addr.zip}".strip()
    parts.append(city_state_zip)
    return " | ".join(part for part in parts if part)


@dataclass(frozen=True)
class ScheduleEPropertyFields:
    """Per-property column on Schedule E Part I (lines 1-21)."""

    address: str = ""
    property_type: str = "single_family"
    fair_rental_days: int = 0
    personal_use_days: int = 0
    qbi_qualified: bool = False

    line_3_rents_received: Decimal = _ZERO
    line_4_royalties_received: Decimal = _ZERO

    # Expenses (lines 5-19)
    line_5_advertising: Decimal = _ZERO
    line_6_auto_and_travel: Decimal = _ZERO
    line_7_cleaning_and_maintenance: Decimal = _ZERO
    line_8_commissions: Decimal = _ZERO
    line_9_insurance: Decimal = _ZERO
    line_10_legal_and_professional: Decimal = _ZERO
    line_11_management_fees: Decimal = _ZERO
    line_12_mortgage_interest_to_banks: Decimal = _ZERO
    line_13_other_interest: Decimal = _ZERO
    line_14_repairs: Decimal = _ZERO
    line_15_supplies: Decimal = _ZERO
    line_16_taxes: Decimal = _ZERO
    line_17_utilities: Decimal = _ZERO
    line_18_depreciation: Decimal = _ZERO
    line_19_other_expenses: Decimal = _ZERO

    line_20_total_expenses: Decimal = _ZERO
    line_21_net_income_or_loss: Decimal = _ZERO


@dataclass(frozen=True)
class ScheduleEFields:
    """Frozen snapshot of Schedule E line values, ready for rendering.

    Field names follow the TY2024 Schedule E structure (assumed stable
    for TY2025). All numeric fields are ``Decimal``.
    """

    # Header
    taxpayer_name: str = ""
    taxpayer_ssn: str = ""

    # Part I -- per-property columns (up to 3 per page)
    properties: tuple[ScheduleEPropertyFields, ...] = ()

    # Part I summary (lines 23-26)
    line_23a_total_rental_income: Decimal = _ZERO
    line_23b_total_rental_losses: Decimal = _ZERO
    line_24_income: Decimal = _ZERO
    line_25_losses: Decimal = _ZERO
    line_26_total_rental_royalty_income_or_loss: Decimal = _ZERO

    # Part II stub -- partnership / S-corp passthrough
    part_ii_passthrough_total: Decimal = _ZERO

    # Part III stub -- estates / trusts
    part_iii_estates_trusts_total: Decimal = _ZERO


def _compute_property_fields(p: ScheduleEProperty) -> ScheduleEPropertyFields:
    """Map a single ScheduleEProperty to its field snapshot."""
    other_exp_total = _q(sum(p.other_expenses.values(), start=_ZERO))
    total_expenses = _q(
        p.advertising
        + p.auto_and_travel
        + p.cleaning_and_maintenance
        + p.commissions
        + p.insurance
        + p.legal_and_professional
        + p.management_fees
        + p.mortgage_interest_to_banks
        + p.other_interest
        + p.repairs
        + p.supplies
        + p.taxes
        + p.utilities
        + p.depreciation
        + other_exp_total
    )
    net = _q(schedule_e_property_net(p))

    return ScheduleEPropertyFields(
        address=_format_address(p),
        property_type=p.property_type,
        fair_rental_days=p.fair_rental_days,
        personal_use_days=p.personal_use_days,
        qbi_qualified=p.qbi_qualified,
        line_3_rents_received=_q(p.rents_received),
        line_4_royalties_received=_q(p.royalties_received),
        line_5_advertising=_q(p.advertising),
        line_6_auto_and_travel=_q(p.auto_and_travel),
        line_7_cleaning_and_maintenance=_q(p.cleaning_and_maintenance),
        line_8_commissions=_q(p.commissions),
        line_9_insurance=_q(p.insurance),
        line_10_legal_and_professional=_q(p.legal_and_professional),
        line_11_management_fees=_q(p.management_fees),
        line_12_mortgage_interest_to_banks=_q(p.mortgage_interest_to_banks),
        line_13_other_interest=_q(p.other_interest),
        line_14_repairs=_q(p.repairs),
        line_15_supplies=_q(p.supplies),
        line_16_taxes=_q(p.taxes),
        line_17_utilities=_q(p.utilities),
        line_18_depreciation=_q(p.depreciation),
        line_19_other_expenses=other_exp_total,
        line_20_total_expenses=total_expenses,
        line_21_net_income_or_loss=net,
    )


def compute_schedule_e_fields(
    canonical: CanonicalReturn,
    schedule_idx: int = 0,
) -> ScheduleEFields:
    """Map a CanonicalReturn's ScheduleE onto a ScheduleEFields dataclass.

    Parameters
    ----------
    canonical
        The canonical return (must have ``schedules_e`` populated).
    schedule_idx
        Index into ``canonical.schedules_e`` (default 0). Each ScheduleE
        may contain up to 3 properties on one page.

    Returns
    -------
    ScheduleEFields
        A frozen dataclass with every line populated.
    """
    taxpayer_name = f"{canonical.taxpayer.first_name} {canonical.taxpayer.last_name}"
    taxpayer_ssn = canonical.taxpayer.ssn

    if not canonical.schedules_e or schedule_idx >= len(canonical.schedules_e):
        return ScheduleEFields(
            taxpayer_name=taxpayer_name,
            taxpayer_ssn=taxpayer_ssn,
        )

    sched_e = canonical.schedules_e[schedule_idx]

    # Per-property fields
    prop_fields = tuple(
        _compute_property_fields(p) for p in sched_e.properties
    )

    # Summary lines
    total_income = _ZERO
    total_losses = _ZERO
    for pf in prop_fields:
        if pf.line_21_net_income_or_loss >= _ZERO:
            total_income += pf.line_21_net_income_or_loss
        else:
            total_losses += pf.line_21_net_income_or_loss

    line_23a = _q(total_income)
    line_23b = _q(total_losses)
    line_24 = _q(line_23a + line_23b)
    # Line 25: passive activity losses (deferred -- use line 23b as passthrough)
    line_25 = line_23b
    line_26 = _q(line_23a + line_25)

    # Part II stub: sum passthrough income from partnership / S-corp dicts
    part_ii_total = _ZERO
    for entry in sched_e.part_ii_partnership_s_corp:
        val = entry.get("net_income", _ZERO)
        if isinstance(val, (int, float, str)):
            val = Decimal(str(val))
        part_ii_total += val

    # Part III stub: sum estate/trust passthrough
    part_iii_total = _ZERO
    for entry in sched_e.part_iii_estates_trusts:
        val = entry.get("net_income", _ZERO)
        if isinstance(val, (int, float, str)):
            val = Decimal(str(val))
        part_iii_total += val

    return ScheduleEFields(
        taxpayer_name=taxpayer_name,
        taxpayer_ssn=taxpayer_ssn,
        properties=prop_fields,
        line_23a_total_rental_income=line_23a,
        line_23b_total_rental_losses=line_23b,
        line_24_income=line_24,
        line_25_losses=line_25,
        line_26_total_rental_royalty_income_or_loss=line_26,
        part_ii_passthrough_total=_q(part_ii_total),
        part_iii_estates_trusts_total=_q(part_iii_total),
    )


# ---------------------------------------------------------------------------
# Layer 2: reportlab scaffold PDF rendering
# ---------------------------------------------------------------------------


# Human-readable labels for per-property expense lines.
_PROPERTY_LINE_LABELS: list[tuple[str, str]] = [
    ("line_3_rents_received", "Line 3 -- Rents received"),
    ("line_4_royalties_received", "Line 4 -- Royalties received"),
    ("line_5_advertising", "Line 5 -- Advertising"),
    ("line_6_auto_and_travel", "Line 6 -- Auto and travel"),
    ("line_7_cleaning_and_maintenance", "Line 7 -- Cleaning and maintenance"),
    ("line_8_commissions", "Line 8 -- Commissions"),
    ("line_9_insurance", "Line 9 -- Insurance"),
    ("line_10_legal_and_professional", "Line 10 -- Legal and professional fees"),
    ("line_11_management_fees", "Line 11 -- Management fees"),
    ("line_12_mortgage_interest_to_banks", "Line 12 -- Mortgage interest paid to banks"),
    ("line_13_other_interest", "Line 13 -- Other interest"),
    ("line_14_repairs", "Line 14 -- Repairs"),
    ("line_15_supplies", "Line 15 -- Supplies"),
    ("line_16_taxes", "Line 16 -- Taxes"),
    ("line_17_utilities", "Line 17 -- Utilities"),
    ("line_18_depreciation", "Line 18 -- Depreciation expense or depletion"),
    ("line_19_other_expenses", "Line 19 -- Other expenses"),
    ("line_20_total_expenses", "Line 20 -- Total expenses"),
    ("line_21_net_income_or_loss", "Line 21 -- Net income or (loss)"),
]

_SUMMARY_LINE_LABELS: list[tuple[str, str]] = [
    ("line_23a_total_rental_income", "Line 23a -- Total rental/real estate income"),
    ("line_23b_total_rental_losses", "Line 23b -- Total rental/real estate losses"),
    ("line_24_income", "Line 24 -- Income"),
    ("line_25_losses", "Line 25 -- Losses"),
    ("line_26_total_rental_royalty_income_or_loss", "Line 26 -- Total rental real estate and royalty income or (loss)"),
    ("part_ii_passthrough_total", "Part II -- Partnership/S-Corp passthrough total"),
    ("part_iii_estates_trusts_total", "Part III -- Estates/Trusts passthrough total"),
]


def _format_decimal(value: Decimal) -> str:
    """Format a Decimal for display in the scaffold table."""
    q = value.quantize(_CENTS, rounding=ROUND_HALF_UP)
    return f"{q:.2f}"


def render_schedule_e_pdf(fields: ScheduleEFields, out_path: Path) -> Path:
    """Render a Schedule E scaffold PDF via reportlab.

    Writes a single-page table listing every Schedule E line label and
    its computed value. This is NOT a filled IRS form -- it is a
    human-readable diagnostic scaffold suitable for review.

    Parameters
    ----------
    fields
        The Layer 1 output from :func:`compute_schedule_e_fields`.
    out_path
        Destination path for the PDF.

    Returns
    -------
    Path
        ``out_path`` for convenience.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=letter,
        title="Schedule E -- Supplemental Income and Loss",
    )
    styles = getSampleStyleSheet()
    story: list = []

    # Header
    story.append(
        Paragraph(
            "Schedule E -- Supplemental Income and Loss (TY2025)",
            styles["Title"],
        )
    )
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            f"Taxpayer: {fields.taxpayer_name} &nbsp;&nbsp; SSN: {fields.taxpayer_ssn}",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 12))

    # Per-property sections
    for idx, prop in enumerate(fields.properties):
        story.append(
            Paragraph(
                f"Property {chr(65 + idx)}: {prop.address} ({prop.property_type})",
                styles["Heading3"],
            )
        )
        story.append(
            Paragraph(
                f"Fair rental days: {prop.fair_rental_days} | "
                f"Personal use days: {prop.personal_use_days} | "
                f"QBI qualified: {'Yes' if prop.qbi_qualified else 'No'}",
                styles["Normal"],
            )
        )
        story.append(Spacer(1, 4))

        table_data = [["Line", "Amount"]]
        for attr_name, label in _PROPERTY_LINE_LABELS:
            value = getattr(prop, attr_name)
            table_data.append([label, _format_decimal(value)])

        table = Table(table_data, colWidths=[380, 100])
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 12))

    # Summary section
    story.append(
        Paragraph("Part I Summary", styles["Heading3"])
    )
    summary_data = [["Line", "Amount"]]
    for attr_name, label in _SUMMARY_LINE_LABELS:
        value = getattr(fields, attr_name)
        summary_data.append([label, _format_decimal(value)])

    summary_table = Table(summary_data, colWidths=[380, 100])
    summary_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(summary_table)

    doc.build(story)
    return out_path


# ---------------------------------------------------------------------------
# Multi-schedule helper
# ---------------------------------------------------------------------------


def render_schedule_e_pdfs_all(
    canonical: CanonicalReturn,
    output_dir: Path,
) -> list[Path]:
    """Render one scaffold PDF per ScheduleE in the return.

    Files are named ``schedule_e_{idx:02d}.pdf``. Returns the list of
    written paths in input order. Empty list when the return has no
    ``schedules_e``.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for idx in range(len(canonical.schedules_e)):
        fields = compute_schedule_e_fields(canonical, schedule_idx=idx)
        out_path = output_dir / f"schedule_e_{idx:02d}.pdf"
        render_schedule_e_pdf(fields, out_path)
        written.append(out_path)
    return written


__all__ = [
    "ScheduleEFields",
    "ScheduleEPropertyFields",
    "compute_schedule_e_fields",
    "render_schedule_e_pdf",
    "render_schedule_e_pdfs_all",
]
