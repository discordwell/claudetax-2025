"""Tier 1 ingester for Form 1099-NEC (Nonemployee Compensation) fillable PDFs.

Wires a field-name map into the shared PyPdfAcroFormIngester base so that
AcroForm widget values from a 1099-NEC PDF land on the canonical
``forms_1099_nec[0].*`` paths on CanonicalReturn.

Real-IRS widget compatibility (wave 6)
---------------------------------------
Fetched ``https://www.irs.gov/pub/irs-pdf/f1099nec.pdf`` (archived at
``skill/reference/irs_forms/f1099nec_ty2024.pdf``, SHA-256
``7cf3272e27de046a92346375601f3211b711a9c800f9185034dd70badcab0abe``).
The PDF is a real AcroForm with 113 widgets across 4 copies (CopyA,
Copy1, CopyB, Copy2). Container naming is consistent: ``LeftCol`` /
``RightCol``. Copy A uses ``f1_N`` leaves; every other copy uses
``f2_N``.

1099-NEC only exposes ``box1_nonemployee_compensation`` and
``box4_federal_income_tax_withheld`` on the canonical model, so only
those two monetary boxes + payer identity are mapped. Box 2 (direct
sales checkbox), box 3 (excess golden parachute), and boxes 5/6/7
(state info) are not yet in the model and therefore not mapped here.
"""
from __future__ import annotations

from skill.scripts.ingest._pipeline import DocumentKind
from skill.scripts.ingest._pypdf_acroform import PyPdfAcroFormIngester


# ---------------------------------------------------------------------------
# Real IRS 1099-NEC widget path templates -> canonical path
# ---------------------------------------------------------------------------
_1099_NEC_COPY_PREFIX: list[tuple[str, str]] = [
    ("CopyA", "f1"),
    ("Copy1", "f2"),
    ("CopyB", "f2"),
    ("Copy2", "f2"),
]

_1099_NEC_REAL_TEMPLATES: dict[str, str] = {
    # LeftCol.fN_2 = payer name/address block
    "topmostSubform[0].{c}[0].LeftCol[0].{p}_2[0]":
        "forms_1099_nec[0].payer_name",
    # LeftCol.fN_3 = payer TIN
    "topmostSubform[0].{c}[0].LeftCol[0].{p}_3[0]":
        "forms_1099_nec[0].payer_tin",
    # RightCol.fN_9 = box 1 nonemployee compensation
    "topmostSubform[0].{c}[0].RightCol[0].{p}_9[0]":
        "forms_1099_nec[0].box1_nonemployee_compensation",
    # RightCol.fN_11 = box 4 federal income tax withheld
    # (fN_10 = box 3 excess golden parachute -- not modeled)
    "topmostSubform[0].{c}[0].RightCol[0].{p}_11[0]":
        "forms_1099_nec[0].box4_federal_income_tax_withheld",
}


def _expand_real_1099_nec_widgets() -> dict[str, str]:
    out: dict[str, str] = {}
    for tmpl, canonical in _1099_NEC_REAL_TEMPLATES.items():
        for c, p in _1099_NEC_COPY_PREFIX:
            out[tmpl.format(c=c, p=p)] = canonical
    return out


# ---------------------------------------------------------------------------
# Unified field-name -> canonical path map (synthetic + real IRS widgets)
# ---------------------------------------------------------------------------
FORM_1099_NEC_FIELD_MAP: dict[str, str] = {
    # --- Synthetic keys (test fixture) --------------------------------
    # Payer identity
    "payer_name": "forms_1099_nec[0].payer_name",
    "payer_tin": "forms_1099_nec[0].payer_tin",
    # Box 1 -- Nonemployee compensation
    "box1_nonemployee_compensation": (
        "forms_1099_nec[0].box1_nonemployee_compensation"
    ),
    # Box 4 -- Federal income tax withheld
    "box4_federal_income_tax_withheld": (
        "forms_1099_nec[0].box4_federal_income_tax_withheld"
    ),
    # --- Real IRS widget names (expanded across all copies) ----------
    **_expand_real_1099_nec_widgets(),
}


# Module-level singleton. The cascade wiring imports this directly.
INGESTER: PyPdfAcroFormIngester = PyPdfAcroFormIngester(
    name="1099_nec_acroform",
    field_map={DocumentKind.FORM_1099_NEC: FORM_1099_NEC_FIELD_MAP},
)
