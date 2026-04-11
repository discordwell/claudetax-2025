"""Tier 1 ingester specialization for Form W-2 fillable PDFs.

This wires the base :class:`PyPdfAcroFormIngester` with a W-2-specific
``field_map`` so that AcroForm widget values extracted from a fillable W-2
land at canonical-return paths like ``w2s[0].box1_wages`` instead of the
fallback ``_acroform_raw.<name>`` pseudo-paths.

Real-IRS widget compatibility (wave 6)
---------------------------------------
The IRS publishes a fillable Form W-2 template at
https://www.irs.gov/pub/irs-pdf/fw2.pdf. It is archived at
``skill/reference/irs_forms/fw2_ty2024.pdf`` (SHA-256
``a61501bb0d3e746cc826336a49a3e99e01835a30978687ad37d44c6b9c3ff293``).

The PDF has 407 AcroForm widgets across six physical copies
(CopyA / Copy1 / CopyB / CopyC / Copy2 / CopyD). Copy A uses ``f1_NN``
widget names; every other copy uses ``f2_NN`` with the same numbering.
Each copy has its own fully-qualified widget name — e.g.
``topmostSubform[0].CopyB[0].Col_Right[0].Box1_ReadOrder[0].f2_09[0]``.

``W2_FIELD_MAP`` below contains BOTH the synthetic placeholder keys used
by the wave-1 reportlab test fixture (``wages_box1`` etc.) AND the real
IRS widget names for every copy, so the same ingester instance can read
a real ``fw2.pdf`` (or a payroll-provider render derived from it) and
the synthetic test fixtures used by wave-1 through wave-5 tests.

Note: employees never receive the blank IRS ``fw2.pdf`` directly — they
get a rendered W-2 from their payroll provider (ADP, Gusto, Paychex,
etc.), usually as a flattened PDF or on paper. Major payroll providers
use their own internal widget names when they render fillable W-2s. The
real-IRS widget map here is useful for (a) reading the IRS template
itself (e.g. a small employer filing manually) and (b) reading W-2s
from providers that start from the IRS template. Future waves should
add per-provider field maps for the common payroll vendors.

Boxes NOT mapped (present on the real form but not modeled on
``skill.scripts.models.W2``):

- Box a Employee SSN (identity — not stored on W2 model)
- Employer / employee address free-text (not stored)
- Box 9 Verification code (deprecated by the IRS)
- Box 12 codes a-d (the model has ``box12_entries: list[W2Box12Entry]``
  but it is a structured list — a raw widget-to-path mapping would need
  to collect code+amount pairs, which is out of scope for the simple
  ``dict[str, str]`` field map.)
- Box 13 checkboxes (statutory / retirement / third-party sick pay) —
  need checkbox state interpretation.
- Box 14 Other (free-form list) — same structured-list shape issue as
  box 12 codes.
- Box 18 / 19 / 20 Locality wages/tax/name — not stored on W2StateRow.
"""
from __future__ import annotations

from skill.scripts.ingest._pipeline import DocumentKind
from skill.scripts.ingest._pypdf_acroform import PyPdfAcroFormIngester


# ---------------------------------------------------------------------------
# Real IRS W-2 widget path templates -> canonical-path
# ---------------------------------------------------------------------------
#
# The IRS ``fw2.pdf`` template has six physical copies. Copy A uses
# ``f1_NN`` widget leaves; every other copy uses ``f2_NN`` with identical
# numbering, so the real-widget map is templated and expanded across all
# six copies.
_W2_COPY_PREFIX: list[tuple[str, str]] = [
    ("CopyA", "f1"),
    ("Copy1", "f2"),
    ("CopyB", "f2"),
    ("CopyC", "f2"),
    ("Copy2", "f2"),
    ("CopyD", "f2"),
]

# Widget-path template -> canonical-path. ``{c}`` is the physical copy
# name and ``{p}`` is ``f1`` for Copy A and ``f2`` for every other copy.
# Numbering is decoded from the box-labeled ``ReadOrder`` subform paths
# in the real PDF.
_W2_REAL_TEMPLATES: dict[str, str] = {
    # Box b — Employer identification number (EIN)
    "topmostSubform[0].{c}[0].Col_Left[0].{p}_02[0]":
        "w2s[0].employer_ein",
    # Box c — Employer's name
    "topmostSubform[0].{c}[0].Col_Left[0].{p}_03[0]":
        "w2s[0].employer_name",
    # Box 1 — Wages, tips, other compensation
    "topmostSubform[0].{c}[0].Col_Right[0].Box1_ReadOrder[0].{p}_09[0]":
        "w2s[0].box1_wages",
    # Box 2 — Federal income tax withheld
    "topmostSubform[0].{c}[0].Col_Right[0].{p}_10[0]":
        "w2s[0].box2_federal_income_tax_withheld",
    # Box 3 — Social security wages
    "topmostSubform[0].{c}[0].Col_Right[0].Box3_ReadOrder[0].{p}_11[0]":
        "w2s[0].box3_social_security_wages",
    # Box 4 — Social security tax withheld
    "topmostSubform[0].{c}[0].Col_Right[0].{p}_12[0]":
        "w2s[0].box4_social_security_tax_withheld",
    # Box 5 — Medicare wages and tips
    "topmostSubform[0].{c}[0].Col_Right[0].Box5_ReadOrder[0].{p}_13[0]":
        "w2s[0].box5_medicare_wages",
    # Box 6 — Medicare tax withheld
    "topmostSubform[0].{c}[0].Col_Right[0].{p}_14[0]":
        "w2s[0].box6_medicare_tax_withheld",
    # Box 7 — Social security tips
    "topmostSubform[0].{c}[0].Col_Right[0].Box7_ReadOrder[0].{p}_15[0]":
        "w2s[0].box7_social_security_tips",
    # Box 8 — Allocated tips
    "topmostSubform[0].{c}[0].Col_Right[0].{p}_16[0]":
        "w2s[0].box8_allocated_tips",
    # Box 10 — Dependent care benefits
    "topmostSubform[0].{c}[0].Col_Right[0].Box10_ReadOrder[0].{p}_18[0]":
        "w2s[0].box10_dependent_care_benefits",
    # Box 11 — Nonqualified plans
    "topmostSubform[0].{c}[0].Col_Right[0].{p}_19[0]":
        "w2s[0].box11_nonqualified_plans",
    # Box 15 row 1 — State (two-letter code)
    "topmostSubform[0].{c}[0].Boxes15_ReadOrder[0].Box15_ReadOrder[0].{p}_31[0]":
        "w2s[0].state_rows[0].state",
    # Box 15 row 2 — State (second state row)
    "topmostSubform[0].{c}[0].Boxes15_ReadOrder[0].{p}_33[0]":
        "w2s[0].state_rows[1].state",
    # Box 16 row 1 — State wages, tips, etc.
    "topmostSubform[0].{c}[0].Box16_ReadOrder[0].{p}_35[0]":
        "w2s[0].state_rows[0].state_wages",
    # Box 16 row 2 — State wages (second row)
    "topmostSubform[0].{c}[0].Box16_ReadOrder[0].{p}_36[0]":
        "w2s[0].state_rows[1].state_wages",
    # Box 17 row 1 — State income tax withheld
    "topmostSubform[0].{c}[0].Box17_ReadOrder[0].{p}_37[0]":
        "w2s[0].state_rows[0].state_tax_withheld",
    # Box 17 row 2 — State income tax withheld (second row)
    "topmostSubform[0].{c}[0].Box17_ReadOrder[0].{p}_38[0]":
        "w2s[0].state_rows[1].state_tax_withheld",
}


def _expand_w2_real_widgets() -> dict[str, str]:
    """Expand ``_W2_REAL_TEMPLATES`` across every physical copy."""
    out: dict[str, str] = {}
    for tmpl, canonical in _W2_REAL_TEMPLATES.items():
        for c, p in _W2_COPY_PREFIX:
            out[tmpl.format(c=c, p=p)] = canonical
    return out


# ---------------------------------------------------------------------------
# Unified W-2 AcroForm field map (synthetic + real IRS widgets)
# ---------------------------------------------------------------------------
#
# Synthetic keys (``wages_box1`` etc.) match the wave-1 reportlab-generated
# test fixture. Real IRS widget names are the fully-qualified per-copy
# widget paths from the official ``fw2.pdf`` (see module docstring). Both
# sets map to the same canonical-return paths under ``w2s[0]``.
W2_FIELD_MAP: dict[str, str] = {
    # --- Synthetic keys (test fixture) --------------------------------
    # Employer identity
    "employer_name": "w2s[0].employer_name",
    "employer_ein": "w2s[0].employer_ein",
    # Box 1 — wages, tips, other compensation
    "wages_box1": "w2s[0].box1_wages",
    # Box 2 — federal income tax withheld
    "fed_withholding_box2": "w2s[0].box2_federal_income_tax_withheld",
    # Box 3 — social security wages
    "ss_wages_box3": "w2s[0].box3_social_security_wages",
    # Box 4 — social security tax withheld
    "ss_tax_box4": "w2s[0].box4_social_security_tax_withheld",
    # Box 5 — Medicare wages and tips
    "medicare_wages_box5": "w2s[0].box5_medicare_wages",
    # Box 6 — Medicare tax withheld
    "medicare_tax_box6": "w2s[0].box6_medicare_tax_withheld",
    # Box 7 — social security tips
    "ss_tips_box7": "w2s[0].box7_social_security_tips",
    # Box 8 — allocated tips
    "allocated_tips_box8": "w2s[0].box8_allocated_tips",
    # Box 10 — dependent care benefits
    "dep_care_box10": "w2s[0].box10_dependent_care_benefits",
    # Box 11 — nonqualified plans
    "nonqualified_box11": "w2s[0].box11_nonqualified_plans",
    # Box 15/16/17 — first state row
    "state_box15": "w2s[0].state_rows[0].state",
    "state_wages_box16": "w2s[0].state_rows[0].state_wages",
    "state_tax_box17": "w2s[0].state_rows[0].state_tax_withheld",
    # --- Real IRS widget names (expanded across all copies) ----------
    **_expand_w2_real_widgets(),
}


# ---------------------------------------------------------------------------
# Ingester instance
# ---------------------------------------------------------------------------
#
# The base ``PyPdfAcroFormIngester`` is a dataclass that already accepts
# ``name`` and ``field_map``, so we just instantiate it directly rather
# than subclassing.
INGESTER: PyPdfAcroFormIngester = PyPdfAcroFormIngester(
    name="w2_acroform",
    field_map={DocumentKind.FORM_W2: W2_FIELD_MAP},
)
