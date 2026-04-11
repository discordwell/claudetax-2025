"""Tier 1 ingester for Form 1099-DIV (Dividends and Distributions) fillable PDFs.

Wires a field-name map into the shared PyPdfAcroFormIngester base so that
AcroForm widget values from a 1099-DIV PDF land on the canonical
``forms_1099_div[0].*`` paths on CanonicalReturn.

Real-IRS widget compatibility (wave 6)
---------------------------------------
Fetched ``https://www.irs.gov/pub/irs-pdf/f1099div.pdf`` (archived at
``skill/reference/irs_forms/f1099div_ty2024.pdf``, SHA-256
``4ea1de804ff1db921753bd6319beeccfe4647299e03d79816e04d852a9cd1435``).
That PDF is a real AcroForm with 205 widgets across 4 copies (CopyA,
Copy1, CopyB, Copy2). Column container naming is consistent
(``LeftCol`` / ``RghtCol``). Copy A uses ``f1_N`` leaves; every other
copy uses ``f2_N`` with the same per-box numbering.

The IRS 1099-DIV grew ``box 2e`` (Section 897 ordinary dividends) and
``box 2f`` (Section 897 capital gain) in Rev. 1-2022, shifting
``exempt-interest dividends`` from box 11 to box 12. The
``skill.scripts.models.Form1099DIV`` canonical model still uses
``box11_exempt_interest_dividends`` (pre-2022 numbering) for backwards
compatibility. This ingester maps the *post-2022 real box 12* widget
to the canonical *box11* path -- the off-by-one is intentional.

Boxes NOT mapped (no canonical-model field or ambiguous under the
2e/2f renumbering):

- Box 2e / 2f -- Section 897 ordinary / capital gain
- Box 8 -- Foreign country or US possession (text, not money)
- Box 9 -- Cash liquidation distributions
- Box 10 -- Noncash liquidation distributions
- Box 11 (real) -- FATCA filing requirement checkbox
- Box 13 -- Specified private activity bond interest dividends
- Box 14 / 15 / 16 -- State information
"""
from __future__ import annotations

from skill.scripts.ingest._pipeline import DocumentKind
from skill.scripts.ingest._pypdf_acroform import PyPdfAcroFormIngester


# ---------------------------------------------------------------------------
# Real IRS 1099-DIV widget path templates -> canonical path
# ---------------------------------------------------------------------------
_1099_DIV_COPY_PREFIX: list[tuple[str, str]] = [
    ("CopyA", "f1"),
    ("Copy1", "f2"),
    ("CopyB", "f2"),
    ("Copy2", "f2"),
]

_1099_DIV_REAL_TEMPLATES: dict[str, str] = {
    # LeftCol.fN_2 = payer name / address block
    "topmostSubform[0].{c}[0].LeftCol[0].{p}_2[0]":
        "forms_1099_div[0].payer_name",
    # LeftCol.fN_3 = payer TIN
    "topmostSubform[0].{c}[0].LeftCol[0].{p}_3[0]":
        "forms_1099_div[0].payer_tin",
    # RghtCol.fN_9 = box 1a total ordinary dividends
    "topmostSubform[0].{c}[0].RghtCol[0].{p}_9[0]":
        "forms_1099_div[0].box1a_ordinary_dividends",
    # RghtCol.fN_10 = box 1b qualified dividends
    "topmostSubform[0].{c}[0].RghtCol[0].{p}_10[0]":
        "forms_1099_div[0].box1b_qualified_dividends",
    # Box2a_ReadOrder.fN_11 = box 2a total capital gain distributions
    "topmostSubform[0].{c}[0].RghtCol[0].Box2a_ReadOrder[0].{p}_11[0]":
        "forms_1099_div[0].box2a_total_capital_gain_distributions",
    # RghtCol.fN_12 = box 2b unrecaptured sec 1250 gain
    "topmostSubform[0].{c}[0].RghtCol[0].{p}_12[0]":
        "forms_1099_div[0].box2b_unrecaptured_sec_1250_gain",
    # Box2c_ReadOrder.fN_13 = box 2c sec 1202 gain
    "topmostSubform[0].{c}[0].RghtCol[0].Box2c_ReadOrder[0].{p}_13[0]":
        "forms_1099_div[0].box2c_section_1202_gain",
    # RghtCol.fN_14 = box 2d collectibles 28% gain
    "topmostSubform[0].{c}[0].RghtCol[0].{p}_14[0]":
        "forms_1099_div[0].box2d_collectibles_28pct_gain",
    # Box3_ReadOrder.fN_17 = box 3 nondividend distributions
    "topmostSubform[0].{c}[0].RghtCol[0].Box3_ReadOrder[0].{p}_17[0]":
        "forms_1099_div[0].box3_nondividend_distributions",
    # RghtCol.fN_18 = box 4 federal income tax withheld
    "topmostSubform[0].{c}[0].RghtCol[0].{p}_18[0]":
        "forms_1099_div[0].box4_federal_income_tax_withheld",
    # Box5_ReadOrder.fN_19 = box 5 sec 199A dividends
    "topmostSubform[0].{c}[0].RghtCol[0].Box5_ReadOrder[0].{p}_19[0]":
        "forms_1099_div[0].box5_section_199a_dividends",
    # RghtCol.fN_20 = box 6 investment expenses
    "topmostSubform[0].{c}[0].RghtCol[0].{p}_20[0]":
        "forms_1099_div[0].box6_investment_expenses",
    # Box7_ReadOrder.fN_21 = box 7 foreign tax paid
    "topmostSubform[0].{c}[0].RghtCol[0].Box7_ReadOrder[0].{p}_21[0]":
        "forms_1099_div[0].box7_foreign_tax_paid",
    # Box12_ReadOrder.fN_25 = real box 12 exempt-interest dividends.
    # Mapped to canonical ``box11_exempt_interest_dividends`` because
    # the model predates the Rev. 1-2022 2e/2f renumbering (see
    # module docstring).
    "topmostSubform[0].{c}[0].RghtCol[0].Box12_ReadOrder[0].{p}_25[0]":
        "forms_1099_div[0].box11_exempt_interest_dividends",
}


def _expand_real_1099_div_widgets() -> dict[str, str]:
    out: dict[str, str] = {}
    for tmpl, canonical in _1099_DIV_REAL_TEMPLATES.items():
        for c, p in _1099_DIV_COPY_PREFIX:
            out[tmpl.format(c=c, p=p)] = canonical
    return out


# ---------------------------------------------------------------------------
# Unified field-name -> canonical path map (synthetic + real IRS widgets)
# ---------------------------------------------------------------------------
FORM_1099_DIV_FIELD_MAP: dict[str, str] = {
    # --- Synthetic keys (test fixture) --------------------------------
    # Payer identity
    "payer_name": "forms_1099_div[0].payer_name",
    "payer_tin": "forms_1099_div[0].payer_tin",
    # Box 1a -- Total ordinary dividends
    "box1a_ordinary_dividends": "forms_1099_div[0].box1a_ordinary_dividends",
    # Box 1b -- Qualified dividends
    "box1b_qualified_dividends": "forms_1099_div[0].box1b_qualified_dividends",
    # Box 2a -- Total capital gain distributions
    "box2a_total_capital_gain_distributions": (
        "forms_1099_div[0].box2a_total_capital_gain_distributions"
    ),
    # Box 2b -- Unrecaptured Section 1250 gain
    "box2b_unrecaptured_sec_1250_gain": (
        "forms_1099_div[0].box2b_unrecaptured_sec_1250_gain"
    ),
    # Box 2c -- Section 1202 gain
    "box2c_section_1202_gain": "forms_1099_div[0].box2c_section_1202_gain",
    # Box 2d -- Collectibles (28%) gain
    "box2d_collectibles_28pct_gain": (
        "forms_1099_div[0].box2d_collectibles_28pct_gain"
    ),
    # Box 3 -- Nondividend distributions
    "box3_nondividend_distributions": (
        "forms_1099_div[0].box3_nondividend_distributions"
    ),
    # Box 4 -- Federal income tax withheld
    "box4_federal_income_tax_withheld": (
        "forms_1099_div[0].box4_federal_income_tax_withheld"
    ),
    # Box 5 -- Section 199A dividends
    "box5_section_199a_dividends": "forms_1099_div[0].box5_section_199a_dividends",
    # Box 6 -- Investment expenses
    "box6_investment_expenses": "forms_1099_div[0].box6_investment_expenses",
    # Box 7 -- Foreign tax paid
    "box7_foreign_tax_paid": "forms_1099_div[0].box7_foreign_tax_paid",
    # Box 11 -- Exempt-interest dividends
    "box11_exempt_interest_dividends": (
        "forms_1099_div[0].box11_exempt_interest_dividends"
    ),
    # --- Real IRS widget names (expanded across all copies) ----------
    **_expand_real_1099_div_widgets(),
}


# Module-level singleton. The cascade wiring imports this directly.
INGESTER: PyPdfAcroFormIngester = PyPdfAcroFormIngester(
    name="1099_div_acroform",
    field_map={DocumentKind.FORM_1099_DIV: FORM_1099_DIV_FIELD_MAP},
)
