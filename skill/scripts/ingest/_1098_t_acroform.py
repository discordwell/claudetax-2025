"""Tier 1 ingester for Form 1098-T (Tuition Statement) fillable PDFs.

Wires a field-name map into the shared PyPdfAcroFormIngester base so that
AcroForm widget values from a 1098-T PDF land on the canonical
``forms_1098_t[0].*`` paths on CanonicalReturn.

Real-IRS widget compatibility
-----------------------------
Fetched ``https://www.irs.gov/pub/irs-pdf/f1098t.pdf`` (archived at
``skill/reference/irs_forms/f1098t_ty2024.pdf``).
The PDF is a real AcroForm with widgets across 2 copies (CopyA, CopyB).
Copy A uses ``f1_N`` leaves; Copy B uses ``f2_N``.

Layout:
  LeftCol: f_1=institution name, f_2=institution street, f_3=suite,
           f_4=institution city/state/zip, f_5=phone,
           f_6=institution EIN, f_7=student TIN,
           f_8=student name, f_9-f_18=student address + account number
  RightCol: f_19=Box 1 (payments received for qualified tuition),
            f_20=Box 4 (adjustments for prior year, in Box4_ReadOrder),
            f_21=Box 5 (scholarships or grants),
            f_22=Box 6 (adjustments to scholarships, in Box6_ReadOrder),
            f_23=Box 10 (insurance contract reimbursement/refund)
  Checkboxes: c_3=Box 7 (next year amounts), c_4=Box 8 (half-time),
              c_5=Box 9 (graduate student)

``FORM_1098_T_FIELD_MAP`` carries BOTH synthetic fixture keys and real IRS
widget names.
"""
from __future__ import annotations

from skill.scripts.ingest._pipeline import DocumentKind
from skill.scripts.ingest._pypdf_acroform import PyPdfAcroFormIngester


# ---------------------------------------------------------------------------
# Real IRS 1098-T widget names -> canonical path
# ---------------------------------------------------------------------------

_1098_T_REAL_WIDGETS: dict[str, str] = {
    # --- Copy A (IRS) ---------------------------------------------------
    "topmostSubform[0].CopyA[0].LeftCol[0].f1_1[0]":
        "forms_1098_t[0].institution_name",
    "topmostSubform[0].CopyA[0].LeftCol[0].f1_6[0]":
        "forms_1098_t[0].institution_ein",
    "topmostSubform[0].CopyA[0].LeftCol[0].f1_7[0]":
        "forms_1098_t[0].student_ssn",
    "topmostSubform[0].CopyA[0].RightCol[0].f1_19[0]":
        "forms_1098_t[0].box1_payments_received",
    "topmostSubform[0].CopyA[0].RightCol[0].Box4_ReadOrder[0].f1_20[0]":
        "forms_1098_t[0].box4_adjustments_prior_year",
    "topmostSubform[0].CopyA[0].RightCol[0].f1_21[0]":
        "forms_1098_t[0].box5_scholarships",
    "topmostSubform[0].CopyA[0].RightCol[0].Box6_ReadOrder[0].f1_22[0]":
        "forms_1098_t[0].box6_adjustments_to_scholarships",
    "topmostSubform[0].CopyA[0].RightCol[0].f1_23[0]":
        "forms_1098_t[0].box10_insurance_contract_reimbursement",
    # --- Copy B (student) -----------------------------------------------
    "topmostSubform[0].CopyB[0].LeftCol[0].f2_1[0]":
        "forms_1098_t[0].institution_name",
    "topmostSubform[0].CopyB[0].LeftCol[0].f2_6[0]":
        "forms_1098_t[0].institution_ein",
    "topmostSubform[0].CopyB[0].LeftCol[0].f2_7[0]":
        "forms_1098_t[0].student_ssn",
    "topmostSubform[0].CopyB[0].RightCol[0].f2_19[0]":
        "forms_1098_t[0].box1_payments_received",
    "topmostSubform[0].CopyB[0].RightCol[0].Box4_ReadOrder[0].f2_20[0]":
        "forms_1098_t[0].box4_adjustments_prior_year",
    "topmostSubform[0].CopyB[0].RightCol[0].f2_21[0]":
        "forms_1098_t[0].box5_scholarships",
    "topmostSubform[0].CopyB[0].RightCol[0].Box6_ReadOrder[0].f2_22[0]":
        "forms_1098_t[0].box6_adjustments_to_scholarships",
    "topmostSubform[0].CopyB[0].RightCol[0].f2_23[0]":
        "forms_1098_t[0].box10_insurance_contract_reimbursement",
}


# ---------------------------------------------------------------------------
# Unified field-name -> canonical path map (synthetic + real IRS widgets)
# ---------------------------------------------------------------------------
FORM_1098_T_FIELD_MAP: dict[str, str] = {
    # --- Synthetic keys (test fixture) --------------------------------
    "institution_name": "forms_1098_t[0].institution_name",
    "institution_ein": "forms_1098_t[0].institution_ein",
    "student_ssn": "forms_1098_t[0].student_ssn",
    "box1_payments_received": "forms_1098_t[0].box1_payments_received",
    "box4_adjustments_prior_year": (
        "forms_1098_t[0].box4_adjustments_prior_year"
    ),
    "box5_scholarships": "forms_1098_t[0].box5_scholarships",
    "box6_adjustments_to_scholarships": (
        "forms_1098_t[0].box6_adjustments_to_scholarships"
    ),
    "box10_insurance_contract_reimbursement": (
        "forms_1098_t[0].box10_insurance_contract_reimbursement"
    ),
    # --- Real IRS widget names (enumerated per copy) -----------------
    **_1098_T_REAL_WIDGETS,
}


# Module-level singleton. The cascade wiring imports this directly.
INGESTER: PyPdfAcroFormIngester = PyPdfAcroFormIngester(
    name="1098_t_acroform",
    field_map={DocumentKind.FORM_1098_T: FORM_1098_T_FIELD_MAP},
)
