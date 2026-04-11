"""Schedule D (Capital Gains and Losses) output renderer.

Two-layer design mirroring ``skill.scripts.output.schedule_b``:

* Layer 1 — :func:`compute_schedule_d_fields` reads
  ``CanonicalReturn.forms_1099_b`` + the 1099-DIV capital-gain
  distributions and emits a frozen ``ScheduleDFields`` dataclass whose
  field names match the TY2025 IRS line numbers. Totals aggregate
  per-row output from :mod:`skill.scripts.output.form_8949` Layer 1.

* Layer 2 — :func:`render_schedule_d_pdf` loads the widget map, verifies
  the source PDF SHA-256, and overlays the values via
  ``fill_acroform_pdf``.

Key compute rules (TY2025 — see IRS Schedule D instructions)
------------------------------------------------------------
Part I — Short-Term (held <= 1 year)
  Line 1a:    Totals from 8949 box A transactions where the taxpayer is
              aggregating (no detail listed); for now we leave 1a blank
              and force every transaction through 1b/2/3 so the Schedule
              D <-> 8949 crosscheck is trivial.
  Line 1b:    Totals from 8949 Part I box A (basis reported).
  Line 2:     Totals from 8949 Part I box B (basis NOT reported).
  Line 3:     Totals from 8949 Part I box C (not on 1099-B).
  Line 4-6:   Short-term gains from Form 6252/K-1s/loss carryover. Deferred.
  Line 7:     Net short-term gain/loss = 1a + 1b + 2 + 3 + 4 + 5 + 6.

Part II — Long-Term (held > 1 year)
  Line 8a/8b/9/10: analogous to 1a/1b/2/3 for boxes D/E/F.
  Line 11-12:  LT gains from 4797/K-1s. Deferred.
  Line 13:     Capital gain distributions (1099-DIV box 2a sum).
  Line 14:     LT loss carryover. Deferred.
  Line 15:     Net long-term gain/loss = 8a + 8b + 9 + 10 + 11 + 12 + 13 + 14.

Part III — Summary
  Line 16:     Total gain/loss = line 7 + line 15. Flows to Form 1040 line 7.
  Line 18-19:  28% rate gain / unrecaptured 1250. Deferred.
  Line 21:     If line 16 is a loss, enter the smaller (by absolute value)
               of the loss or $3,000 ($1,500 if MFS). This caps the amount
               deductible this year; the remainder is a loss carryover to
               next year.

Note on "flowing to 1040 line 7": the engine's calc pipeline uses tenforty
directly against the 1099-B transactions (see
``skill.scripts.calc.engine.build_tenforty_input``) so this renderer
does NOT feed values back into the engine. It produces a PDF that the
filer can staple to their paper or FFFF-attached return.

Sources
-------
* IRS 2025 Schedule D (Form 1040): https://www.irs.gov/pub/irs-pdf/f1040sd.pdf
* IRS Schedule D instructions (carried forward from 2024 with no
  material layout change for TY2025).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from skill.scripts.models import CanonicalReturn
from skill.scripts.output.form_8949 import (
    Form8949Fields,
    Form8949Page,
    compute_form_8949_fields,
)


_ZERO = Decimal("0")

# Capital loss deduction caps per IRC sec. 1211(b). Pinned for TY2025.
LOSS_CAP_DEFAULT = Decimal("-3000")
LOSS_CAP_MFS = Decimal("-1500")


# ---------------------------------------------------------------------------
# Layer 1 — dataclass + compute
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleDRowTotals:
    """One 4-column row on Schedule D Part I or Part II (columns d/e/g/h)."""

    proceeds: Decimal = _ZERO
    cost_basis: Decimal = _ZERO
    adjustment_amount: Decimal = _ZERO
    gain_loss: Decimal = _ZERO

    @classmethod
    def from_8949_page(cls, page: Form8949Page | None) -> "ScheduleDRowTotals":
        if page is None:
            return cls()
        return cls(
            proceeds=page.total_proceeds,
            cost_basis=page.total_cost_basis,
            adjustment_amount=page.total_adjustment_amount,
            gain_loss=page.total_gain_loss,
        )


@dataclass(frozen=True)
class ScheduleDFields:
    """Frozen snapshot of every Schedule D line value, ready to render."""

    # Header
    taxpayer_name: str = ""
    taxpayer_ssn: str = ""

    # Part I — Short-Term
    line_1a_totals: ScheduleDRowTotals = ScheduleDRowTotals()  # aggregated A
    line_1b_totals: ScheduleDRowTotals = ScheduleDRowTotals()  # box A detail
    line_2_totals: ScheduleDRowTotals = ScheduleDRowTotals()  # box B
    line_3_totals: ScheduleDRowTotals = ScheduleDRowTotals()  # box C
    line_4_short_term_from_other_forms: Decimal = _ZERO  # deferred
    line_5_partnership_short_term: Decimal = _ZERO  # deferred
    line_6_short_term_loss_carryover: Decimal = _ZERO  # deferred (always <= 0)
    line_7_net_short_term_gain_loss: Decimal = _ZERO

    # Part II — Long-Term
    line_8a_totals: ScheduleDRowTotals = ScheduleDRowTotals()
    line_8b_totals: ScheduleDRowTotals = ScheduleDRowTotals()
    line_9_totals: ScheduleDRowTotals = ScheduleDRowTotals()
    line_10_totals: ScheduleDRowTotals = ScheduleDRowTotals()
    line_11_long_term_from_other_forms: Decimal = _ZERO  # deferred
    line_12_partnership_long_term: Decimal = _ZERO  # deferred
    line_13_capital_gain_distributions: Decimal = _ZERO
    line_14_long_term_loss_carryover: Decimal = _ZERO  # deferred (always <= 0)
    line_15_net_long_term_gain_loss: Decimal = _ZERO

    # Part III — Summary
    line_16_total_gain_loss: Decimal = _ZERO
    line_18_28pct_rate_gain: Decimal = _ZERO  # deferred
    line_19_unrecaptured_1250_gain: Decimal = _ZERO  # deferred
    line_21_allowable_loss_capped: Decimal = _ZERO
    """Capped loss (negative) going to Form 1040 line 7 when line 16 is
    a loss. Zero when line 16 is a gain (line 16 flows directly)."""

    # Back-reference so callers can render the companion 8949.
    form_8949_fields: Form8949Fields = Form8949Fields()

    @property
    def is_required(self) -> bool:
        """Schedule D is required if the return has any capital
        transactions or cap gain distributions."""
        return (
            bool(self.form_8949_fields.pages)
            or self.line_13_capital_gain_distributions != _ZERO
        )


def _find_page(f8949: Form8949Fields, box: str) -> Form8949Page | None:
    for p in f8949.pages:
        if p.box_code == box:
            return p
    return None


def compute_schedule_d_fields(return_: CanonicalReturn) -> ScheduleDFields:
    """Produce a ScheduleDFields snapshot from a CanonicalReturn.

    This function is pure: it does not call ``engine.compute()``. The
    numbers it produces are derived directly from the canonical
    ``forms_1099_b`` and ``forms_1099_div`` lists.
    """
    taxpayer_name = f"{return_.taxpayer.first_name} {return_.taxpayer.last_name}"
    taxpayer_ssn = return_.taxpayer.ssn or ""

    f8949 = compute_form_8949_fields(return_)

    line_1a = ScheduleDRowTotals()  # aggregated A — unused by Layer 1
    line_1b = ScheduleDRowTotals.from_8949_page(_find_page(f8949, "A"))
    line_2 = ScheduleDRowTotals.from_8949_page(_find_page(f8949, "B"))
    line_3 = ScheduleDRowTotals.from_8949_page(_find_page(f8949, "C"))

    line_8a = ScheduleDRowTotals()
    line_8b = ScheduleDRowTotals.from_8949_page(_find_page(f8949, "D"))
    line_9 = ScheduleDRowTotals.from_8949_page(_find_page(f8949, "E"))
    line_10 = ScheduleDRowTotals.from_8949_page(_find_page(f8949, "F"))

    # Deferred lines — always 0 until the K-1 / Form 6252 / carryover
    # model surfaces are wired up.
    line_4 = _ZERO
    line_5 = _ZERO
    line_6 = _ZERO
    line_11 = _ZERO
    line_12 = _ZERO
    line_14 = _ZERO

    # 1099-DIV box 2a (total capital gain distributions) -> line 13.
    line_13 = sum(
        (f.box2a_total_capital_gain_distributions for f in return_.forms_1099_div),
        start=_ZERO,
    )

    # Net short-term: sum of 1a + 1b + 2 + 3 + 4 + 5 + 6 (col h).
    line_7 = (
        line_1a.gain_loss
        + line_1b.gain_loss
        + line_2.gain_loss
        + line_3.gain_loss
        + line_4
        + line_5
        + line_6
    )

    # Net long-term: sum of 8a + 8b + 9 + 10 + 11 + 12 + 13 + 14.
    line_15 = (
        line_8a.gain_loss
        + line_8b.gain_loss
        + line_9.gain_loss
        + line_10.gain_loss
        + line_11
        + line_12
        + line_13
        + line_14
    )

    line_16 = line_7 + line_15

    line_21 = _ZERO
    if line_16 < _ZERO:
        # IRS filing status string is "mfs"; the canonical model uses the
        # 5-state enum {single, mfj, mfs, hoh, qss}.
        cap = LOSS_CAP_MFS if return_.filing_status == "mfs" else LOSS_CAP_DEFAULT
        # Allowed loss is max(line_16, cap) in the signed sense — i.e.
        # the less-negative of the two. max(-5000, -3000) = -3000.
        line_21 = max(line_16, cap)

    return ScheduleDFields(
        taxpayer_name=taxpayer_name,
        taxpayer_ssn=taxpayer_ssn,
        line_1a_totals=line_1a,
        line_1b_totals=line_1b,
        line_2_totals=line_2,
        line_3_totals=line_3,
        line_4_short_term_from_other_forms=line_4,
        line_5_partnership_short_term=line_5,
        line_6_short_term_loss_carryover=line_6,
        line_7_net_short_term_gain_loss=line_7,
        line_8a_totals=line_8a,
        line_8b_totals=line_8b,
        line_9_totals=line_9,
        line_10_totals=line_10,
        line_11_long_term_from_other_forms=line_11,
        line_12_partnership_long_term=line_12,
        line_13_capital_gain_distributions=line_13,
        line_14_long_term_loss_carryover=line_14,
        line_15_net_long_term_gain_loss=line_15,
        line_16_total_gain_loss=line_16,
        line_21_allowable_loss_capped=line_21,
        form_8949_fields=f8949,
    )


def schedule_d_required(return_: CanonicalReturn) -> bool:
    """Public helper: is Schedule D REQUIRED for this canonical return?"""
    return compute_schedule_d_fields(return_).is_required


# ---------------------------------------------------------------------------
# Layer 2 — AcroForm overlay PDF rendering
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEDULE_D_MAP_PATH = (
    _REPO_ROOT / "skill" / "reference" / "schedule-d-acroform-map.json"
)
_SCHEDULE_D_PDF_PATH = (
    _REPO_ROOT / "skill" / "reference" / "irs_forms" / "f1040sd.pdf"
)


def _format_decimal(value: Decimal) -> str:
    q = value.quantize(Decimal("0.01"))
    if q == Decimal("0.00"):
        return ""
    return f"{q:.2f}"


def _row_widget_values(
    prefix: str, totals: ScheduleDRowTotals, mapping: dict
) -> dict[str, str]:
    """Emit 4 widget values for a Schedule D row (d/e/g/h)."""
    out: dict[str, str] = {}
    out[mapping[f"{prefix}_proceeds"]["widget_name"]] = _format_decimal(
        totals.proceeds
    )
    out[mapping[f"{prefix}_cost_basis"]["widget_name"]] = _format_decimal(
        totals.cost_basis
    )
    out[mapping[f"{prefix}_adjustment"]["widget_name"]] = _format_decimal(
        totals.adjustment_amount
    )
    out[mapping[f"{prefix}_gain_loss"]["widget_name"]] = _format_decimal(
        totals.gain_loss
    )
    return out


def _build_widget_values(
    fields: ScheduleDFields, widget_map: dict
) -> dict[str, str]:
    mapping = widget_map["mapping"]
    out: dict[str, str] = {}

    out[mapping["taxpayer_name"]["widget_name"]] = fields.taxpayer_name
    out[mapping["taxpayer_ssn"]["widget_name"]] = fields.taxpayer_ssn

    # Part I rows (1a, 1b, 2, 3).
    out.update(_row_widget_values("line_1a", fields.line_1a_totals, mapping))
    out.update(_row_widget_values("line_1b", fields.line_1b_totals, mapping))
    out.update(_row_widget_values("line_2", fields.line_2_totals, mapping))
    out.update(_row_widget_values("line_3", fields.line_3_totals, mapping))

    out[mapping["line_4_short_term_gain_from_6252"]["widget_name"]] = _format_decimal(
        fields.line_4_short_term_from_other_forms
    )
    out[mapping["line_5_partnership_short_term"]["widget_name"]] = _format_decimal(
        fields.line_5_partnership_short_term
    )
    out[mapping["line_6_short_term_loss_carryover"]["widget_name"]] = _format_decimal(
        fields.line_6_short_term_loss_carryover
    )
    out[mapping["line_7_net_short_term_gain_loss"]["widget_name"]] = _format_decimal(
        fields.line_7_net_short_term_gain_loss
    )

    # Part II rows (8a, 8b, 9, 10).
    out.update(_row_widget_values("line_8a", fields.line_8a_totals, mapping))
    out.update(_row_widget_values("line_8b", fields.line_8b_totals, mapping))
    out.update(_row_widget_values("line_9", fields.line_9_totals, mapping))
    out.update(_row_widget_values("line_10", fields.line_10_totals, mapping))

    out[mapping["line_11_long_term_gain_from_other_forms"]["widget_name"]] = (
        _format_decimal(fields.line_11_long_term_from_other_forms)
    )
    out[mapping["line_12_partnership_long_term"]["widget_name"]] = _format_decimal(
        fields.line_12_partnership_long_term
    )
    out[mapping["line_13_capital_gain_distributions"]["widget_name"]] = _format_decimal(
        fields.line_13_capital_gain_distributions
    )
    out[mapping["line_14_long_term_loss_carryover"]["widget_name"]] = _format_decimal(
        fields.line_14_long_term_loss_carryover
    )
    out[mapping["line_15_net_long_term_gain_loss"]["widget_name"]] = _format_decimal(
        fields.line_15_net_long_term_gain_loss
    )

    # Part III — summary (page 2).
    out[mapping["line_16_total_gain_loss"]["widget_name"]] = _format_decimal(
        fields.line_16_total_gain_loss
    )
    out[mapping["line_18_28pct_rate_gain"]["widget_name"]] = _format_decimal(
        fields.line_18_28pct_rate_gain
    )
    out[mapping["line_19_unrecaptured_1250_gain"]["widget_name"]] = _format_decimal(
        fields.line_19_unrecaptured_1250_gain
    )
    out[mapping["line_21_allowable_loss_capped"]["widget_name"]] = _format_decimal(
        fields.line_21_allowable_loss_capped
    )

    return out


def render_schedule_d_pdf(fields: ScheduleDFields, out_path: Path) -> Path:
    """Render a Schedule D PDF by overlaying ``fields`` on the IRS PDF.

    Loads the wave-6 widget map, validates the on-disk source PDF
    SHA-256, fills the widgets via
    :func:`skill.scripts.output._acroform_overlay.fill_acroform_pdf`,
    and writes to ``out_path``. Returns ``out_path``.

    This does NOT render the companion Form 8949 — callers should call
    :func:`skill.scripts.output.form_8949.render_form_8949_pdf`
    separately. The pipeline wires both together.
    """
    from skill.scripts.output._acroform_overlay import (
        fill_acroform_pdf,
        load_widget_map_as_dict,
        verify_pdf_sha256,
    )

    widget_map = load_widget_map_as_dict(_SCHEDULE_D_MAP_PATH)
    verify_pdf_sha256(_SCHEDULE_D_PDF_PATH, widget_map["source_pdf_sha256"])
    widget_values = _build_widget_values(fields, widget_map)
    return fill_acroform_pdf(_SCHEDULE_D_PDF_PATH, widget_values, Path(out_path))


__all__ = [
    "LOSS_CAP_DEFAULT",
    "LOSS_CAP_MFS",
    "ScheduleDFields",
    "ScheduleDRowTotals",
    "compute_schedule_d_fields",
    "render_schedule_d_pdf",
    "schedule_d_required",
]
