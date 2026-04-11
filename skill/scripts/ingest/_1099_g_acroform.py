"""Tier 1 ingester for Form 1099-G (Certain Government Payments) fillable PDFs.

Wires a field-name map into the shared PyPdfAcroFormIngester base so that
AcroForm widget values from a 1099-G PDF land on the canonical
``forms_1099_g[0].*`` paths on CanonicalReturn.

Real-IRS widget compatibility (wave 6)
---------------------------------------
Fetched ``https://www.irs.gov/pub/irs-pdf/f1099g.pdf`` (archived at
``skill/reference/irs_forms/f1099g_ty2024.pdf``, SHA-256
``fe46acb40d53442cca67fba55f79aa7683ab8d351e109096c3d6000f60e6226f``).
The PDF is a real AcroForm with 141 widgets across 4 copies (CopyA,
Copy1, CopyB, Copy2). Container naming is consistent:
``LeftColumn`` / ``RightColumn``. Copy A uses ``f1_N`` leaves; every
other copy uses ``f2_N``.

Boxes covered (mirrors ``skill.scripts.models.Form1099G``):

- Payer name, payer TIN
- Box 1 -- Unemployment compensation
- Box 2 -- State or local income tax refunds, credits, or offsets
- Box 3 -- Box 2 amount is for tax year (prior-year indicator)
- Box 4 -- Federal income tax withheld
- Box 5 -- RTAA payments
- Box 6 -- Taxable grants
- Box 7 -- Agriculture payments

Boxes NOT yet modeled on ``Form1099G`` (therefore not mapped here):

- Box 8 -- Trade or business income checkbox
- Box 9 -- Market gain (CCC loans)
- Box 10a / 10b / 11 -- State information

Extending those requires a ``models.py`` change first.
"""
from __future__ import annotations

from skill.scripts.ingest._pipeline import DocumentKind
from skill.scripts.ingest._pypdf_acroform import PyPdfAcroFormIngester


# ---------------------------------------------------------------------------
# Real IRS 1099-G widget path templates -> canonical path
# ---------------------------------------------------------------------------
_1099_G_COPY_PREFIX: list[tuple[str, str]] = [
    ("CopyA", "f1"),
    ("Copy1", "f2"),
    ("CopyB", "f2"),
    ("Copy2", "f2"),
]

_1099_G_REAL_TEMPLATES: dict[str, str] = {
    # LeftColumn.fN_2 = payer name/address block
    "topmostSubform[0].{c}[0].LeftColumn[0].{p}_2[0]":
        "forms_1099_g[0].payer_name",
    # LeftColumn.fN_3 = payer TIN
    "topmostSubform[0].{c}[0].LeftColumn[0].{p}_3[0]":
        "forms_1099_g[0].payer_tin",
    # RightColumn.fN_9 = box 1 unemployment compensation
    "topmostSubform[0].{c}[0].RightColumn[0].{p}_9[0]":
        "forms_1099_g[0].box1_unemployment_compensation",
    # RightColumn.fN_10 = box 2 state/local tax refund
    "topmostSubform[0].{c}[0].RightColumn[0].{p}_10[0]":
        "forms_1099_g[0].box2_state_or_local_income_tax_refund",
    # RightColumn.Box3_ReadOrder.fN_11 = box 3 tax year indicator
    "topmostSubform[0].{c}[0].RightColumn[0].Box3_ReadOrder[0].{p}_11[0]":
        "forms_1099_g[0].box2_tax_year",
    # RightColumn.fN_12 = box 4 federal income tax withheld
    "topmostSubform[0].{c}[0].RightColumn[0].{p}_12[0]":
        "forms_1099_g[0].box4_federal_income_tax_withheld",
    # RightColumn.Box5_ReadOrder.fN_13 = box 5 RTAA payments
    "topmostSubform[0].{c}[0].RightColumn[0].Box5_ReadOrder[0].{p}_13[0]":
        "forms_1099_g[0].box5_rtaa_payments",
    # RightColumn.fN_14 = box 6 taxable grants
    "topmostSubform[0].{c}[0].RightColumn[0].{p}_14[0]":
        "forms_1099_g[0].box6_taxable_grants",
    # RightColumn.Box7_ReadOrder.fN_15 = box 7 agriculture payments
    "topmostSubform[0].{c}[0].RightColumn[0].Box7_ReadOrder[0].{p}_15[0]":
        "forms_1099_g[0].box7_agricultural_payments",
}


def _expand_real_1099_g_widgets() -> dict[str, str]:
    out: dict[str, str] = {}
    for tmpl, canonical in _1099_G_REAL_TEMPLATES.items():
        for c, p in _1099_G_COPY_PREFIX:
            out[tmpl.format(c=c, p=p)] = canonical
    return out


# ---------------------------------------------------------------------------
# Unified field-name -> canonical path map (synthetic + real IRS widgets)
# ---------------------------------------------------------------------------
FORM_1099_G_FIELD_MAP: dict[str, str] = {
    # --- Synthetic keys (test fixture) --------------------------------
    # Payer identity
    "payer_name": "forms_1099_g[0].payer_name",
    "payer_tin": "forms_1099_g[0].payer_tin",
    # Box 1 -- Unemployment compensation
    "box1_unemployment_compensation": (
        "forms_1099_g[0].box1_unemployment_compensation"
    ),
    # Box 2 -- State or local income tax refunds, credits, or offsets
    "box2_state_or_local_income_tax_refund": (
        "forms_1099_g[0].box2_state_or_local_income_tax_refund"
    ),
    # Box 3 -- Tax year the box 2 amount is for (prior-year indicator)
    "box2_tax_year": "forms_1099_g[0].box2_tax_year",
    # Box 4 -- Federal income tax withheld
    "box4_federal_income_tax_withheld": (
        "forms_1099_g[0].box4_federal_income_tax_withheld"
    ),
    # Box 5 -- RTAA payments (Reemployment Trade Adjustment Assistance)
    "box5_rtaa_payments": "forms_1099_g[0].box5_rtaa_payments",
    # Box 6 -- Taxable grants
    "box6_taxable_grants": "forms_1099_g[0].box6_taxable_grants",
    # Box 7 -- Agriculture payments
    "box7_agricultural_payments": "forms_1099_g[0].box7_agricultural_payments",
    # --- Real IRS widget names (expanded across all copies) ----------
    **_expand_real_1099_g_widgets(),
}


# Module-level singleton. The cascade wiring imports this directly.
INGESTER: PyPdfAcroFormIngester = PyPdfAcroFormIngester(
    name="1099_g_acroform",
    field_map={DocumentKind.FORM_1099_G: FORM_1099_G_FIELD_MAP},
)
