"""Tier 1 ingester for Form 1099-INT (Interest Income) fillable PDFs.

Wires a field-name map into the shared PyPdfAcroFormIngester base so that
AcroForm widget values from a 1099-INT PDF land on the canonical
``forms_1099_int[0].*`` paths on CanonicalReturn.

Real-IRS widget compatibility (wave 6)
---------------------------------------
Fetched ``https://www.irs.gov/pub/irs-pdf/f1099int.pdf`` (archived at
``skill/reference/irs_forms/f1099int_ty2024.pdf``, SHA-256
``ee7697c9b29374596fd9645b157b7312d121ae3c2f76bebf68908c3fc2b739e6``).
The PDF is a real AcroForm with 200 widgets across 4 copies (Copy A,
Copy 1, Copy B, Copy 2). Per-copy column container names are
inconsistent across copies (``LeftColumn`` vs ``LftColumn``;
``RightColumn`` vs ``RghtColumn`` vs ``RghtCol``) so each of the 44 real
widget paths is enumerated explicitly below rather than
template-expanded. Copy A uses ``f1_N`` leaves; every other copy uses
``f2_N`` with the same per-box numbering.

``FORM_1099_INT_FIELD_MAP`` carries BOTH the synthetic fixture keys and
the real IRS widget names. Every monetary field on
``skill.scripts.models.Form1099INT`` is covered.
"""
from __future__ import annotations

from skill.scripts.ingest._pipeline import DocumentKind
from skill.scripts.ingest._pypdf_acroform import PyPdfAcroFormIngester


# ---------------------------------------------------------------------------
# Real IRS 1099-INT widget names -> canonical path
# ---------------------------------------------------------------------------
#
# Enumerated exhaustively from ``f1099int_ty2024.pdf``.
_1099_INT_REAL_WIDGETS: dict[str, str] = {
    # --- Copy A (filer paper / IRS) -------------------------------------
    "topmostSubform[0].CopyA[0].LeftColumn[0].f1_1[0]":
        "forms_1099_int[0].payer_name",
    "topmostSubform[0].CopyA[0].LeftColumn[0].f1_2[0]":
        "forms_1099_int[0].payer_tin",
    "topmostSubform[0].CopyA[0].RightColumn[0].Box1[0].f1_9[0]":
        "forms_1099_int[0].box1_interest_income",
    "topmostSubform[0].CopyA[0].RightColumn[0].Box2[0].f1_10[0]":
        "forms_1099_int[0].box2_early_withdrawal_penalty",
    "topmostSubform[0].CopyA[0].RightColumn[0].Box3[0].f1_11[0]":
        "forms_1099_int[0].box3_us_savings_bond_and_treasury_interest",
    "topmostSubform[0].CopyA[0].RightColumn[0].Box4[0].f1_12[0]":
        "forms_1099_int[0].box4_federal_income_tax_withheld",
    "topmostSubform[0].CopyA[0].RightColumn[0].Box5[0].f1_13[0]":
        "forms_1099_int[0].box5_investment_expenses",
    "topmostSubform[0].CopyA[0].RightColumn[0].Box6[0].f1_14[0]":
        "forms_1099_int[0].box6_foreign_tax_paid",
    "topmostSubform[0].CopyA[0].RightColumn[0].Box8[0].f1_16[0]":
        "forms_1099_int[0].box8_tax_exempt_interest",
    "topmostSubform[0].CopyA[0].RightColumn[0].Box9[0].f1_17[0]":
        "forms_1099_int[0].box9_specified_private_activity_bond_interest",
    "topmostSubform[0].CopyA[0].RightColumn[0].Box13[0].f1_21[0]":
        "forms_1099_int[0].box13_bond_premium_on_tax_exempt_bonds",
    # --- Copy 1 (state tax dept) ----------------------------------------
    "topmostSubform[0].Copy1[0].LftColumn[0].f2_1[0]":
        "forms_1099_int[0].payer_name",
    "topmostSubform[0].Copy1[0].LftColumn[0].f2_2[0]":
        "forms_1099_int[0].payer_tin",
    "topmostSubform[0].Copy1[0].RghtCol[0].Box1[0].f2_9[0]":
        "forms_1099_int[0].box1_interest_income",
    "topmostSubform[0].Copy1[0].RghtCol[0].Box2[0].f2_10[0]":
        "forms_1099_int[0].box2_early_withdrawal_penalty",
    "topmostSubform[0].Copy1[0].RghtCol[0].Box3[0].f2_11[0]":
        "forms_1099_int[0].box3_us_savings_bond_and_treasury_interest",
    "topmostSubform[0].Copy1[0].RghtCol[0].Box4[0].f2_12[0]":
        "forms_1099_int[0].box4_federal_income_tax_withheld",
    "topmostSubform[0].Copy1[0].RghtCol[0].Box5[0].f2_13[0]":
        "forms_1099_int[0].box5_investment_expenses",
    "topmostSubform[0].Copy1[0].RghtCol[0].Box6[0].f2_14[0]":
        "forms_1099_int[0].box6_foreign_tax_paid",
    "topmostSubform[0].Copy1[0].RghtCol[0].Box8[0].f2_16[0]":
        "forms_1099_int[0].box8_tax_exempt_interest",
    "topmostSubform[0].Copy1[0].RghtCol[0].Box9[0].f2_17[0]":
        "forms_1099_int[0].box9_specified_private_activity_bond_interest",
    "topmostSubform[0].Copy1[0].RghtCol[0].Box13[0].f2_21[0]":
        "forms_1099_int[0].box13_bond_premium_on_tax_exempt_bonds",
    # --- Copy B (recipient) ---------------------------------------------
    "topmostSubform[0].CopyB[0].LeftColumn[0].f2_1[0]":
        "forms_1099_int[0].payer_name",
    "topmostSubform[0].CopyB[0].LeftColumn[0].f2_2[0]":
        "forms_1099_int[0].payer_tin",
    "topmostSubform[0].CopyB[0].RghtColumn[0].Box1[0].f2_9[0]":
        "forms_1099_int[0].box1_interest_income",
    "topmostSubform[0].CopyB[0].RghtColumn[0].Box2[0].f2_10[0]":
        "forms_1099_int[0].box2_early_withdrawal_penalty",
    "topmostSubform[0].CopyB[0].RghtColumn[0].Box3[0].f2_11[0]":
        "forms_1099_int[0].box3_us_savings_bond_and_treasury_interest",
    "topmostSubform[0].CopyB[0].RghtColumn[0].Box4[0].f2_12[0]":
        "forms_1099_int[0].box4_federal_income_tax_withheld",
    "topmostSubform[0].CopyB[0].RghtColumn[0].Box5[0].f2_13[0]":
        "forms_1099_int[0].box5_investment_expenses",
    "topmostSubform[0].CopyB[0].RghtColumn[0].Box6[0].f2_14[0]":
        "forms_1099_int[0].box6_foreign_tax_paid",
    "topmostSubform[0].CopyB[0].RghtColumn[0].Box8[0].f2_16[0]":
        "forms_1099_int[0].box8_tax_exempt_interest",
    "topmostSubform[0].CopyB[0].RghtColumn[0].Box9[0].f2_17[0]":
        "forms_1099_int[0].box9_specified_private_activity_bond_interest",
    "topmostSubform[0].CopyB[0].RghtColumn[0].Box13[0].f2_21[0]":
        "forms_1099_int[0].box13_bond_premium_on_tax_exempt_bonds",
    # --- Copy 2 (recipient state) ---------------------------------------
    "topmostSubform[0].Copy2[0].LeftColumn[0].f2_1[0]":
        "forms_1099_int[0].payer_name",
    "topmostSubform[0].Copy2[0].LeftColumn[0].f2_2[0]":
        "forms_1099_int[0].payer_tin",
    "topmostSubform[0].Copy2[0].RghtColumn[0].Box1[0].f2_9[0]":
        "forms_1099_int[0].box1_interest_income",
    "topmostSubform[0].Copy2[0].RghtColumn[0].Box2[0].f2_10[0]":
        "forms_1099_int[0].box2_early_withdrawal_penalty",
    "topmostSubform[0].Copy2[0].RghtColumn[0].Box3[0].f2_11[0]":
        "forms_1099_int[0].box3_us_savings_bond_and_treasury_interest",
    "topmostSubform[0].Copy2[0].RghtColumn[0].Box4[0].f2_12[0]":
        "forms_1099_int[0].box4_federal_income_tax_withheld",
    "topmostSubform[0].Copy2[0].RghtColumn[0].Box5[0].f2_13[0]":
        "forms_1099_int[0].box5_investment_expenses",
    "topmostSubform[0].Copy2[0].RghtColumn[0].Box6[0].f2_14[0]":
        "forms_1099_int[0].box6_foreign_tax_paid",
    "topmostSubform[0].Copy2[0].RghtColumn[0].Box8[0].f2_16[0]":
        "forms_1099_int[0].box8_tax_exempt_interest",
    "topmostSubform[0].Copy2[0].RghtColumn[0].Box9[0].f2_17[0]":
        "forms_1099_int[0].box9_specified_private_activity_bond_interest",
    "topmostSubform[0].Copy2[0].RghtColumn[0].Box13[0].f2_21[0]":
        "forms_1099_int[0].box13_bond_premium_on_tax_exempt_bonds",
}


# ---------------------------------------------------------------------------
# Unified field-name -> canonical path map (synthetic + real IRS widgets)
# ---------------------------------------------------------------------------
#
# Covered boxes track the fields on skill.scripts.models.Form1099INT.
FORM_1099_INT_FIELD_MAP: dict[str, str] = {
    # --- Synthetic keys (test fixture) --------------------------------
    "payer_name": "forms_1099_int[0].payer_name",
    "payer_tin": "forms_1099_int[0].payer_tin",
    "box1_interest_income": "forms_1099_int[0].box1_interest_income",
    "box2_early_withdrawal_penalty": "forms_1099_int[0].box2_early_withdrawal_penalty",
    "box3_us_savings_bond_and_treasury_interest": (
        "forms_1099_int[0].box3_us_savings_bond_and_treasury_interest"
    ),
    "box4_federal_income_tax_withheld": (
        "forms_1099_int[0].box4_federal_income_tax_withheld"
    ),
    "box5_investment_expenses": "forms_1099_int[0].box5_investment_expenses",
    "box6_foreign_tax_paid": "forms_1099_int[0].box6_foreign_tax_paid",
    "box8_tax_exempt_interest": "forms_1099_int[0].box8_tax_exempt_interest",
    "box9_specified_private_activity_bond_interest": (
        "forms_1099_int[0].box9_specified_private_activity_bond_interest"
    ),
    "box13_bond_premium_on_tax_exempt_bonds": (
        "forms_1099_int[0].box13_bond_premium_on_tax_exempt_bonds"
    ),
    # --- Real IRS widget names (enumerated per copy) -----------------
    **_1099_INT_REAL_WIDGETS,
}


# Module-level singleton. The cascade wiring imports this directly.
INGESTER: PyPdfAcroFormIngester = PyPdfAcroFormIngester(
    name="1099_int_acroform",
    field_map={DocumentKind.FORM_1099_INT: FORM_1099_INT_FIELD_MAP},
)
