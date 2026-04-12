"""Tier 1 ingester for Form 1098-E (Student Loan Interest Statement) fillable PDFs.

Wires a field-name map into the shared PyPdfAcroFormIngester base so that
AcroForm widget values from a 1098-E PDF land on the canonical
``forms_1098_e[0].*`` paths on CanonicalReturn.

Real-IRS widget compatibility
-----------------------------
Fetched ``https://www.irs.gov/pub/irs-pdf/f1098e.pdf`` (archived at
``skill/reference/irs_forms/f1098e_ty2024.pdf``).
The PDF is a real AcroForm with widgets across 2 copies (CopyA, CopyB).
Copy A uses ``f1_N`` leaves; Copy B uses ``f2_N``.

Form 1098-E is very simple: it has only one monetary box.

Layout:
  LeftCol: f_1=lender name, f_2=lender street, f_3=lender suite,
           f_4=lender city/state/zip, f_5=lender phone,
           f_6=lender TIN, f_7=borrower TIN,
           f_8=borrower name, f_9-f_18=borrower address + account number
  RightCol: f_19=Box 1 (student loan interest received)
            c_2=Box 2 checkbox (loan origination fees not included)

``FORM_1098_E_FIELD_MAP`` carries BOTH synthetic fixture keys and real IRS
widget names.
"""
from __future__ import annotations

from skill.scripts.ingest._pipeline import DocumentKind
from skill.scripts.ingest._pypdf_acroform import PyPdfAcroFormIngester


# ---------------------------------------------------------------------------
# Real IRS 1098-E widget names -> canonical path
# ---------------------------------------------------------------------------

_1098_E_REAL_WIDGETS: dict[str, str] = {
    # --- Copy A (IRS) ---------------------------------------------------
    "topmostSubform[0].CopyA[0].LeftCol[0].f1_1[0]":
        "forms_1098_e[0].lender_name",
    "topmostSubform[0].CopyA[0].LeftCol[0].f1_6[0]":
        "forms_1098_e[0].lender_tin",
    "topmostSubform[0].CopyA[0].RightCol[0].f1_19[0]":
        "forms_1098_e[0].box1_student_loan_interest",
    # --- Copy B (borrower) ----------------------------------------------
    "topmostSubform[0].CopyB[0].LeftCol[0].f2_1[0]":
        "forms_1098_e[0].lender_name",
    "topmostSubform[0].CopyB[0].LeftCol[0].f2_6[0]":
        "forms_1098_e[0].lender_tin",
    "topmostSubform[0].CopyB[0].RightCol[0].f2_19[0]":
        "forms_1098_e[0].box1_student_loan_interest",
}


# ---------------------------------------------------------------------------
# Unified field-name -> canonical path map (synthetic + real IRS widgets)
# ---------------------------------------------------------------------------
FORM_1098_E_FIELD_MAP: dict[str, str] = {
    # --- Synthetic keys (test fixture) --------------------------------
    "lender_name": "forms_1098_e[0].lender_name",
    "lender_tin": "forms_1098_e[0].lender_tin",
    "box1_student_loan_interest": (
        "forms_1098_e[0].box1_student_loan_interest"
    ),
    # --- Real IRS widget names (enumerated per copy) -----------------
    **_1098_E_REAL_WIDGETS,
}


# Module-level singleton. The cascade wiring imports this directly.
INGESTER: PyPdfAcroFormIngester = PyPdfAcroFormIngester(
    name="1098_e_acroform",
    field_map={DocumentKind.FORM_1098_E: FORM_1098_E_FIELD_MAP},
)
