"""Tier 1 ingester for Form 1098 (Mortgage Interest Statement) fillable PDFs.

Wires a field-name map into the shared PyPdfAcroFormIngester base so that
AcroForm widget values from a 1098 PDF land on the canonical
``forms_1098[0].*`` paths on CanonicalReturn.

Real-IRS widget compatibility
-----------------------------
Fetched ``https://www.irs.gov/pub/irs-pdf/f1098.pdf`` (archived at
``skill/reference/irs_forms/f1098_ty2024.pdf``).
The PDF is a real AcroForm with widgets across 2 copies (CopyA, CopyB).
Copy A uses ``f1_N`` leaves; Copy B uses ``f2_N`` with the same numbering.

Layout:
  LeftCol: f_2=lender name, f_3=lender TIN, f_4=borrower TIN,
           f_5=borrower name, f_6=borrower street, f_7=borrower city,
           f_10=account number
  RightCol: f_8=Box 1 (mortgage interest, in TagCorrectingSubform),
            f_9=Box 2 (outstanding principal, in TagCorrectingSubform),
            f_11=Box 3 (origination date), f_12=Box 4 (refund overpaid),
            f_13=Box 5 (mortgage insurance premiums), f_14=Box 6 (points),
            f_15=Box 8 (property address), f_16=Box 9 (num properties),
            f_17=Box 10 (other), f_18=Box 11 (acquisition date)
  c1_3/c2_3=Box 7 checkbox (property same as borrower address)

``FORM_1098_FIELD_MAP`` carries BOTH synthetic fixture keys and real IRS
widget names. All modeled fields on ``skill.scripts.models.Form1098`` are
covered.
"""
from __future__ import annotations

from skill.scripts.ingest._pipeline import DocumentKind
from skill.scripts.ingest._pypdf_acroform import PyPdfAcroFormIngester


# ---------------------------------------------------------------------------
# Real IRS 1098 widget names -> canonical path
# ---------------------------------------------------------------------------

_1098_REAL_WIDGETS: dict[str, str] = {
    # --- Copy A (IRS) ---------------------------------------------------
    "topmostSubform[0].CopyA[0].LeftCol[0].f1_2[0]":
        "forms_1098[0].lender_name",
    "topmostSubform[0].CopyA[0].LeftCol[0].f1_3[0]":
        "forms_1098[0].lender_tin",
    "topmostSubform[0].CopyA[0].RightCol[0].TagCorrectingSubform[0].f1_8[0]":
        "forms_1098[0].box1_mortgage_interest",
    "topmostSubform[0].CopyA[0].RightCol[0].TagCorrectingSubform[0].f1_9[0]":
        "forms_1098[0].box2_outstanding_principal",
    "topmostSubform[0].CopyA[0].RightCol[0].f1_11[0]":
        "forms_1098[0].box3_mortgage_origination_date",
    "topmostSubform[0].CopyA[0].RightCol[0].f1_12[0]":
        "forms_1098[0].box4_refund_of_overpaid_interest",
    "topmostSubform[0].CopyA[0].RightCol[0].f1_13[0]":
        "forms_1098[0].box5_mortgage_insurance_premiums",
    "topmostSubform[0].CopyA[0].RightCol[0].f1_14[0]":
        "forms_1098[0].box6_points_paid_on_purchase",
    "topmostSubform[0].CopyA[0].RightCol[0].f1_16[0]":
        "forms_1098[0].box9_number_of_properties",
    "topmostSubform[0].CopyA[0].RightCol[0].f1_17[0]":
        "forms_1098[0].box10_other",
    "topmostSubform[0].CopyA[0].RightCol[0].f1_18[0]":
        "forms_1098[0].box11_mortgage_acquisition_date",
    # --- Copy B (borrower) ----------------------------------------------
    "topmostSubform[0].CopyB[0].LeftCol[0].f2_2[0]":
        "forms_1098[0].lender_name",
    "topmostSubform[0].CopyB[0].LeftCol[0].f2_3[0]":
        "forms_1098[0].lender_tin",
    "topmostSubform[0].CopyB[0].RightCol[0].TagCorrectingSubform[0].f2_8[0]":
        "forms_1098[0].box1_mortgage_interest",
    "topmostSubform[0].CopyB[0].RightCol[0].TagCorrectingSubform[0].f2_9[0]":
        "forms_1098[0].box2_outstanding_principal",
    "topmostSubform[0].CopyB[0].RightCol[0].f2_11[0]":
        "forms_1098[0].box3_mortgage_origination_date",
    "topmostSubform[0].CopyB[0].RightCol[0].f2_12[0]":
        "forms_1098[0].box4_refund_of_overpaid_interest",
    "topmostSubform[0].CopyB[0].RightCol[0].f2_13[0]":
        "forms_1098[0].box5_mortgage_insurance_premiums",
    "topmostSubform[0].CopyB[0].RightCol[0].f2_14[0]":
        "forms_1098[0].box6_points_paid_on_purchase",
    "topmostSubform[0].CopyB[0].RightCol[0].f2_16[0]":
        "forms_1098[0].box9_number_of_properties",
    "topmostSubform[0].CopyB[0].RightCol[0].f2_17[0]":
        "forms_1098[0].box10_other",
    "topmostSubform[0].CopyB[0].RightCol[0].f2_18[0]":
        "forms_1098[0].box11_mortgage_acquisition_date",
}


# ---------------------------------------------------------------------------
# Unified field-name -> canonical path map (synthetic + real IRS widgets)
# ---------------------------------------------------------------------------
FORM_1098_FIELD_MAP: dict[str, str] = {
    # --- Synthetic keys (test fixture) --------------------------------
    "lender_name": "forms_1098[0].lender_name",
    "lender_tin": "forms_1098[0].lender_tin",
    "box1_mortgage_interest": "forms_1098[0].box1_mortgage_interest",
    "box2_outstanding_principal": "forms_1098[0].box2_outstanding_principal",
    "box3_mortgage_origination_date": (
        "forms_1098[0].box3_mortgage_origination_date"
    ),
    "box4_refund_of_overpaid_interest": (
        "forms_1098[0].box4_refund_of_overpaid_interest"
    ),
    "box5_mortgage_insurance_premiums": (
        "forms_1098[0].box5_mortgage_insurance_premiums"
    ),
    "box6_points_paid_on_purchase": (
        "forms_1098[0].box6_points_paid_on_purchase"
    ),
    "box9_number_of_properties": "forms_1098[0].box9_number_of_properties",
    "box10_other": "forms_1098[0].box10_other",
    "box11_mortgage_acquisition_date": (
        "forms_1098[0].box11_mortgage_acquisition_date"
    ),
    # --- Real IRS widget names (enumerated per copy) -----------------
    **_1098_REAL_WIDGETS,
}


# Module-level singleton. The cascade wiring imports this directly.
INGESTER: PyPdfAcroFormIngester = PyPdfAcroFormIngester(
    name="1098_acroform",
    field_map={DocumentKind.FORM_1098: FORM_1098_FIELD_MAP},
)
