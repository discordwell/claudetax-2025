"""Tier 1 ingester for Form 1099-MISC (Miscellaneous Information) fillable PDFs.

Wires a field-name map into the shared PyPdfAcroFormIngester base so that
AcroForm widget values from a 1099-MISC PDF land on the canonical
``forms_1099_misc[0].*`` paths on CanonicalReturn.

Real-IRS widget compatibility (wave 8)
---------------------------------------
Fetched ``https://www.irs.gov/pub/irs-pdf/f1099msc.pdf`` (archived at
``skill/reference/irs_forms/f1099msc_ty2025.pdf``).
The PDF is a real AcroForm with 152 widgets across 4 copies (CopyA,
Copy1, CopyB, Copy2). Container naming is consistent: ``LeftColumn`` /
``RightColumn`` across all copies. Copy A uses ``f1_N`` / ``c1_N``
leaves; every other copy uses ``f2_N`` / ``c2_N``.

Box 16 (state tax withheld) and Box 17 (state/payer's state no.) use
``_ReadOrder`` subform containers. Box 18 (state income) fields live
directly under the copy, not inside a column container.

1099-MISC box layout:
- Box 1  — Rents
- Box 2  — Royalties
- Box 3  — Other income
- Box 4  — Federal income tax withheld
- Box 5  — Fishing boat proceeds
- Box 6  — Medical and health care payments
- Box 7  — Payer made direct sales (checkbox, $5,000+ threshold)
- Box 8  — Substitute payments in lieu of dividends or interest
- Box 9  — Crop insurance proceeds
- Box 10 — Gross proceeds paid to an attorney
- Box 11 — Fish purchased for resale
- Box 12 — Section 409A deferrals
- Box 13 — FATCA filing requirement (checkbox, not mapped)
- Box 14 — (blank on current form revision)
- Box 15 — Nonqualified deferred compensation
- Box 16 — State tax withheld (2 rows)
- Box 17 — State/Payer's state no. (2 rows)
- Box 18 — State income (2 rows)

Flows to: Schedule E (rents/royalties), Schedule 1 line 8z (other income),
Schedule C if applicable.
"""
from __future__ import annotations

from skill.scripts.ingest._pipeline import DocumentKind
from skill.scripts.ingest._pypdf_acroform import PyPdfAcroFormIngester


# ---------------------------------------------------------------------------
# Real IRS 1099-MISC widget path templates -> canonical path
# ---------------------------------------------------------------------------
_1099_MISC_COPY_PREFIX: list[tuple[str, str, str]] = [
    ("CopyA", "f1", "c1"),
    ("Copy1", "f2", "c2"),
    ("CopyB", "f2", "c2"),
    ("Copy2", "f2", "c2"),
]

# Widget-path template -> canonical-path. ``{c}`` is the physical copy name,
# ``{p}`` is ``f1`` for Copy A and ``f2`` for every other copy, ``{cp}`` is
# ``c1`` for Copy A and ``c2`` for every other copy.
_1099_MISC_REAL_TEMPLATES: dict[str, str] = {
    # LeftColumn — payer / recipient identity
    "topmostSubform[0].{c}[0].LeftColumn[0].{p}_2[0]":
        "forms_1099_misc[0].payer_name",
    "topmostSubform[0].{c}[0].LeftColumn[0].{p}_3[0]":
        "forms_1099_misc[0].payer_tin",
    "topmostSubform[0].{c}[0].LeftColumn[0].{p}_4[0]":
        "forms_1099_misc[0].recipient_tin",
    # RightColumn — monetary boxes
    "topmostSubform[0].{c}[0].RightColumn[0].{p}_9[0]":
        "forms_1099_misc[0].box1_rents",
    "topmostSubform[0].{c}[0].RightColumn[0].{p}_10[0]":
        "forms_1099_misc[0].box2_royalties",
    "topmostSubform[0].{c}[0].RightColumn[0].{p}_11[0]":
        "forms_1099_misc[0].box3_other_income",
    "topmostSubform[0].{c}[0].RightColumn[0].{p}_12[0]":
        "forms_1099_misc[0].box4_federal_tax_withheld",
    "topmostSubform[0].{c}[0].RightColumn[0].{p}_13[0]":
        "forms_1099_misc[0].box5_fishing_boat_proceeds",
    "topmostSubform[0].{c}[0].RightColumn[0].{p}_14[0]":
        "forms_1099_misc[0].box6_medical_healthcare_payments",
    # Box 7 — payer direct sales checkbox (c1_3 / c2_3 inside TagCorrectingSubform)
    # Note: checkbox state is "/Yes" or "/Off" — ingester emits raw string,
    # downstream conversion to bool lives in the canonical rewriter.
    "topmostSubform[0].{c}[0].RightColumn[0].TagCorrectingSubform[0].{cp}_3[0]":
        "forms_1099_misc[0].box7_payer_direct_sales",
    "topmostSubform[0].{c}[0].RightColumn[0].{p}_15[0]":
        "forms_1099_misc[0].box8_substitute_payments",
    "topmostSubform[0].{c}[0].RightColumn[0].{p}_16[0]":
        "forms_1099_misc[0].box9_crop_insurance",
    "topmostSubform[0].{c}[0].RightColumn[0].{p}_17[0]":
        "forms_1099_misc[0].box10_gross_proceeds_attorney",
    "topmostSubform[0].{c}[0].RightColumn[0].{p}_18[0]":
        "forms_1099_misc[0].box11_fish_purchased_for_resale",
    "topmostSubform[0].{c}[0].RightColumn[0].{p}_19[0]":
        "forms_1099_misc[0].box12_section_409a_deferrals",
    "topmostSubform[0].{c}[0].RightColumn[0].{p}_21[0]":
        "forms_1099_misc[0].box14_nonqualified_deferred_compensation",
    # Box 16 — State tax withheld (2 rows inside Box16_ReadOrder)
    "topmostSubform[0].{c}[0].Box16_ReadOrder[0].{p}_22[0]":
        "forms_1099_misc[0].box15_state_tax_withheld",
}


def _expand_real_1099_misc_widgets() -> dict[str, str]:
    """Expand ``_1099_MISC_REAL_TEMPLATES`` across every physical copy."""
    out: dict[str, str] = {}
    for tmpl, canonical in _1099_MISC_REAL_TEMPLATES.items():
        for c, p, cp in _1099_MISC_COPY_PREFIX:
            out[tmpl.format(c=c, p=p, cp=cp)] = canonical
    return out


# ---------------------------------------------------------------------------
# Unified field-name -> canonical path map (synthetic + real IRS widgets)
# ---------------------------------------------------------------------------
FORM_1099_MISC_FIELD_MAP: dict[str, str] = {
    # --- Synthetic keys (test fixture) --------------------------------
    "payer_name": "forms_1099_misc[0].payer_name",
    "payer_tin": "forms_1099_misc[0].payer_tin",
    "recipient_tin": "forms_1099_misc[0].recipient_tin",
    "box1_rents": "forms_1099_misc[0].box1_rents",
    "box2_royalties": "forms_1099_misc[0].box2_royalties",
    "box3_other_income": "forms_1099_misc[0].box3_other_income",
    "box4_federal_tax_withheld": "forms_1099_misc[0].box4_federal_tax_withheld",
    "box5_fishing_boat_proceeds": "forms_1099_misc[0].box5_fishing_boat_proceeds",
    "box6_medical_healthcare_payments": (
        "forms_1099_misc[0].box6_medical_healthcare_payments"
    ),
    "box7_payer_direct_sales": "forms_1099_misc[0].box7_payer_direct_sales",
    "box8_substitute_payments": "forms_1099_misc[0].box8_substitute_payments",
    "box9_crop_insurance": "forms_1099_misc[0].box9_crop_insurance",
    "box10_gross_proceeds_attorney": (
        "forms_1099_misc[0].box10_gross_proceeds_attorney"
    ),
    "box11_fish_purchased_for_resale": (
        "forms_1099_misc[0].box11_fish_purchased_for_resale"
    ),
    "box12_section_409a_deferrals": (
        "forms_1099_misc[0].box12_section_409a_deferrals"
    ),
    "box14_nonqualified_deferred_compensation": (
        "forms_1099_misc[0].box14_nonqualified_deferred_compensation"
    ),
    "box15_state_tax_withheld": "forms_1099_misc[0].box15_state_tax_withheld",
    # --- Real IRS widget names (expanded across all copies) ----------
    **_expand_real_1099_misc_widgets(),
}


# Module-level singleton. The cascade wiring imports this directly.
INGESTER: PyPdfAcroFormIngester = PyPdfAcroFormIngester(
    name="1099_misc_acroform",
    field_map={DocumentKind.FORM_1099_MISC: FORM_1099_MISC_FIELD_MAP},
)
