"""Tier 1 ingester for Form 1099-K (Payment Card / Third Party Network) fillable PDFs.

Wires a field-name map into the shared PyPdfAcroFormIngester base so that
AcroForm widget values from a 1099-K PDF land on the canonical
``forms_1099_k[0].*`` paths on CanonicalReturn.

Real-IRS widget compatibility (wave 8)
---------------------------------------
Fetched ``https://www.irs.gov/pub/irs-pdf/f1099k.pdf`` (archived at
``skill/reference/irs_forms/f1099k_ty2024.pdf``).
The PDF is a real AcroForm with 212 widgets across 4 copies (CopyA,
Copy1, CopyB, Copy2). Container naming is ``LeftCol`` across all copies
but the right column is inconsistent: CopyA/Copy1/Copy2 use ``RghtCol``
while CopyB uses ``RightCol``. Each copy's right-column widgets are
therefore enumerated explicitly for CopyB. Copy A uses ``f1_N`` / ``c1_N``
leaves; every other copy uses ``f2_N`` / ``c2_N``.

Many monetary boxes use ``_ReadOrder`` subform containers (Box1b, Box5a,
Box5c, Box5e, Box5g, Box5i, Box5k, Box6, Box7).

1099-K box layout (TY2025 -- $5,000 threshold):
- Box 1a — Gross amount of payment card/third party network transactions
- Box 1b — Card Not Present transactions
- Box 2  — Merchant category code
- Box 3  — Number of payment transactions
- Box 4  — Federal income tax withheld
- Box 5a-5l — Monthly amounts (January through December)
- Box 6  — State (2 rows)
- Box 7  — State identification no. (2 rows)
- Box 8  — State income tax withheld (2 rows)

Flows to: Schedule C (business income) or Schedule 1 (other income).
"""
from __future__ import annotations

from skill.scripts.ingest._pipeline import DocumentKind
from skill.scripts.ingest._pypdf_acroform import PyPdfAcroFormIngester


# ---------------------------------------------------------------------------
# Real IRS 1099-K widget paths -> canonical path
# ---------------------------------------------------------------------------
# The right-column container name differs between CopyB (``RightCol``) and
# every other copy (``RghtCol``). We use a template with ``{rc}`` for the
# right column name and expand across copies, passing the correct column
# name for each copy.
_1099_K_COPY_PREFIX: list[tuple[str, str, str]] = [
    # (copy_name, field_prefix, right_col_name)
    ("CopyA", "f1", "RghtCol"),
    ("Copy1", "f2", "RghtCol"),
    ("CopyB", "f2", "RightCol"),
    ("Copy2", "f2", "RghtCol"),
]

# Widget-path template -> canonical-path.
# ``{c}`` = copy name, ``{p}`` = field prefix (f1/f2), ``{rc}`` = right column name.
_1099_K_REAL_TEMPLATES: dict[str, str] = {
    # LeftCol — filer / payee identity
    "topmostSubform[0].{c}[0].LeftCol[0].{p}_2[0]":
        "forms_1099_k[0].payer_name",
    "topmostSubform[0].{c}[0].LeftCol[0].{p}_3[0]":
        "forms_1099_k[0].payer_tin",
    # RghtCol / RightCol — monetary boxes
    # f1_8 / f2_8 = PSE's name and telephone
    "topmostSubform[0].{c}[0].{rc}[0].{p}_8[0]":
        "forms_1099_k[0].settlement_entity_name",
    # Box 1a — Gross amount
    "topmostSubform[0].{c}[0].{rc}[0].{p}_10[0]":
        "forms_1099_k[0].box1a_gross_amount",
    # Box 1b — Card Not Present (inside Box1b_ReadOrder)
    "topmostSubform[0].{c}[0].{rc}[0].Box1b_ReadOrder[0].{p}_11[0]":
        "forms_1099_k[0].box1b_card_not_present",
    # Box 2 — Merchant category code
    "topmostSubform[0].{c}[0].{rc}[0].{p}_12[0]":
        "forms_1099_k[0].box2_merchant_category_code",
    # Box 3 — Number of payment transactions
    "topmostSubform[0].{c}[0].{rc}[0].{p}_13[0]":
        "forms_1099_k[0].box3_number_of_payment_transactions",
    # Box 4 — Federal income tax withheld
    "topmostSubform[0].{c}[0].{rc}[0].{p}_14[0]":
        "forms_1099_k[0].box4_federal_tax_withheld",
    # Box 5a — January (inside Box5a_ReadOrder)
    "topmostSubform[0].{c}[0].{rc}[0].Box5a_ReadOrder[0].{p}_15[0]":
        "forms_1099_k[0].box5a_january",
    # Box 5b — February
    "topmostSubform[0].{c}[0].{rc}[0].{p}_16[0]":
        "forms_1099_k[0].box5b_february",
    # Box 5c — March (inside Box5c_ReadOrder)
    "topmostSubform[0].{c}[0].{rc}[0].Box5c_ReadOrder[0].{p}_17[0]":
        "forms_1099_k[0].box5c_march",
    # Box 5d — April
    "topmostSubform[0].{c}[0].{rc}[0].{p}_18[0]":
        "forms_1099_k[0].box5d_april",
    # Box 5e — May (inside Box5e_ReadOrder)
    "topmostSubform[0].{c}[0].{rc}[0].Box5e_ReadOrder[0].{p}_19[0]":
        "forms_1099_k[0].box5e_may",
    # Box 5f — June
    "topmostSubform[0].{c}[0].{rc}[0].{p}_20[0]":
        "forms_1099_k[0].box5f_june",
    # Box 5g — July (inside Box5g_ReadOrder)
    "topmostSubform[0].{c}[0].{rc}[0].Box5g_ReadOrder[0].{p}_21[0]":
        "forms_1099_k[0].box5g_july",
    # Box 5h — August
    "topmostSubform[0].{c}[0].{rc}[0].{p}_22[0]":
        "forms_1099_k[0].box5h_august",
    # Box 5i — September (inside Box5i_ReadOrder)
    "topmostSubform[0].{c}[0].{rc}[0].Box5i_ReadOrder[0].{p}_23[0]":
        "forms_1099_k[0].box5i_september",
    # Box 5j — October
    "topmostSubform[0].{c}[0].{rc}[0].{p}_24[0]":
        "forms_1099_k[0].box5j_october",
    # Box 5k — November (inside Box5k_ReadOrder)
    "topmostSubform[0].{c}[0].{rc}[0].Box5k_ReadOrder[0].{p}_25[0]":
        "forms_1099_k[0].box5k_november",
    # Box 5l — December
    "topmostSubform[0].{c}[0].{rc}[0].{p}_26[0]":
        "forms_1099_k[0].box5l_december",
}


def _expand_real_1099_k_widgets() -> dict[str, str]:
    """Expand ``_1099_K_REAL_TEMPLATES`` across every physical copy."""
    out: dict[str, str] = {}
    for tmpl, canonical in _1099_K_REAL_TEMPLATES.items():
        for c, p, rc in _1099_K_COPY_PREFIX:
            out[tmpl.format(c=c, p=p, rc=rc)] = canonical
    return out


# ---------------------------------------------------------------------------
# Unified field-name -> canonical path map (synthetic + real IRS widgets)
# ---------------------------------------------------------------------------
FORM_1099_K_FIELD_MAP: dict[str, str] = {
    # --- Synthetic keys (test fixture) --------------------------------
    "payer_name": "forms_1099_k[0].payer_name",
    "payer_tin": "forms_1099_k[0].payer_tin",
    "settlement_entity_name": "forms_1099_k[0].settlement_entity_name",
    "box1a_gross_amount": "forms_1099_k[0].box1a_gross_amount",
    "box1b_card_not_present": "forms_1099_k[0].box1b_card_not_present",
    "box2_merchant_category_code": "forms_1099_k[0].box2_merchant_category_code",
    "box3_number_of_payment_transactions": (
        "forms_1099_k[0].box3_number_of_payment_transactions"
    ),
    "box4_federal_tax_withheld": "forms_1099_k[0].box4_federal_tax_withheld",
    "box5a_january": "forms_1099_k[0].box5a_january",
    "box5b_february": "forms_1099_k[0].box5b_february",
    "box5c_march": "forms_1099_k[0].box5c_march",
    "box5d_april": "forms_1099_k[0].box5d_april",
    "box5e_may": "forms_1099_k[0].box5e_may",
    "box5f_june": "forms_1099_k[0].box5f_june",
    "box5g_july": "forms_1099_k[0].box5g_july",
    "box5h_august": "forms_1099_k[0].box5h_august",
    "box5i_september": "forms_1099_k[0].box5i_september",
    "box5j_october": "forms_1099_k[0].box5j_october",
    "box5k_november": "forms_1099_k[0].box5k_november",
    "box5l_december": "forms_1099_k[0].box5l_december",
    # --- Real IRS widget names (expanded across all copies) ----------
    **_expand_real_1099_k_widgets(),
}


# Module-level singleton. The cascade wiring imports this directly.
INGESTER: PyPdfAcroFormIngester = PyPdfAcroFormIngester(
    name="1099_k_acroform",
    field_map={DocumentKind.FORM_1099_K: FORM_1099_K_FIELD_MAP},
)
