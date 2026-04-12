"""Tier 1 ingester for Form 1095-A (Health Insurance Marketplace Statement).

Wires a field-name map into the shared PyPdfAcroFormIngester base so that
AcroForm widget values from a 1095-A PDF land on the canonical
``forms_1095_a[0].*`` paths on CanonicalReturn.

Form 1095-A reports monthly marketplace health insurance data used to
reconcile the Premium Tax Credit on Form 8962.  It is issued by the Health
Insurance Marketplace (not the IRS) and delivered to enrollees annually.

IRS AcroForm layout (fetched ``https://www.irs.gov/pub/irs-pdf/f1095a.pdf``)
---------------------------------------------------------------------------
The PDF is a real AcroForm with 104 widgets across 3 pages.  Only Page 1
carries tax-relevant data; pages 2-3 are instructions/copies.

Part I  (header, ``f1_1`` through ``f1_15``):
  f1_1  = Marketplace name (line 1)
  f1_2  = Marketplace ID number (line 2)
  f1_3  = Policy number (line 3)
  f1_4  = Recipient SSN (line 4)
  f1_5  = Recipient DOB (line 5)
  f1_6  = Recipient first name (line 6)
  f1_7  = Recipient middle name (line 6)
  f1_8  = Recipient last name (line 6)
  f1_9  = Recipient address line 1 (line 7)
  f1_10 = Recipient city (line 8)
  f1_11 = Recipient state (line 9)
  f1_12 = Recipient ZIP (line 10)
  f1_13 = Policy start date (line 11)
  f1_14 = Policy end date (line 12)
  f1_15 = Corrected form checkbox? / line 15

Part II (covered individuals, ``Table_PartII``, rows 16-20):
  5 rows x 5 fields each: name, SSN, DOB, coverage start, coverage end.
  Not mapped to Form1095A model fields (identity data, not used by 8962).

Part III (monthly data, ``Table_PartIII``, rows 21-33):
  Row 21 (Jan):  f1_41 = enrollment premium, f1_42 = SLCSP, f1_43 = APTC
  Row 22 (Feb):  f1_44, f1_45, f1_46
  Row 23 (Mar):  f1_47, f1_48, f1_49
  Row 24 (Apr):  f1_50, f1_51, f1_52
  Row 25 (May):  f1_53, f1_54, f1_55
  Row 26 (Jun):  f1_56, f1_57, f1_58
  Row 27 (Jul):  f1_59, f1_60, f1_61
  Row 28 (Aug):  f1_62, f1_63, f1_64
  Row 29 (Sep):  f1_65, f1_66, f1_67
  Row 30 (Oct):  f1_68, f1_69, f1_70
  Row 31 (Nov):  f1_71, f1_72, f1_73
  Row 32 (Dec):  f1_74, f1_75, f1_76
  Row 33 (Annual): f1_77, f1_78, f1_79  (not mapped — annual totals)

Form1095A model fields mapped here:

- marketplace_id       (Part I, line 2)
- policy_start_date    (Part I, line 11)
- policy_end_date      (Part I, line 12)
- monthly_data[0..11]  (Part III, rows 21-32):
    - enrollment_premium  (column A)
    - slcsp_premium       (column B)
    - advance_ptc         (column C)

Sources:
- https://www.irs.gov/pub/irs-pdf/f1095a.pdf (official IRS 1095-A form)
- https://www.irs.gov/pub/irs-pdf/i1095a.pdf (instructions)
- skill.scripts.models.Form1095A, Form1095AMonthly
"""
from __future__ import annotations

from skill.scripts.ingest._pipeline import DocumentKind
from skill.scripts.ingest._pypdf_acroform import PyPdfAcroFormIngester

# Month names for documentation clarity
_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# ---------------------------------------------------------------------------
# Real IRS 1095-A widget names -> canonical path
# ---------------------------------------------------------------------------

_PREFIX = "topmostSubform[0].Page1[0]"
_PT3 = f"{_PREFIX}.Table_PartIII[0]"

_1095_A_REAL_WIDGETS: dict[str, str] = {
    # --- Part I: header info ---
    f"{_PREFIX}.f1_2[0]": "forms_1095_a[0].marketplace_id",
    f"{_PREFIX}.f1_13[0]": "forms_1095_a[0].policy_start_date",
    f"{_PREFIX}.f1_14[0]": "forms_1095_a[0].policy_end_date",
}

# --- Part III: monthly data (rows 21-32, Jan-Dec) ---
# Each row has 3 fields: enrollment_premium, slcsp_premium, advance_ptc
# Field numbering: Row21 starts at f1_41, each row adds 3
for _month_idx in range(12):
    _row_num = 21 + _month_idx
    _field_base = 41 + _month_idx * 3
    _row_key = f"{_PT3}.Row{_row_num}[0]"
    _1095_A_REAL_WIDGETS[f"{_row_key}.f1_{_field_base}[0]"] = (
        f"forms_1095_a[0].monthly_data[{_month_idx}].enrollment_premium"
    )
    _1095_A_REAL_WIDGETS[f"{_row_key}.f1_{_field_base + 1}[0]"] = (
        f"forms_1095_a[0].monthly_data[{_month_idx}].slcsp_premium"
    )
    _1095_A_REAL_WIDGETS[f"{_row_key}.f1_{_field_base + 2}[0]"] = (
        f"forms_1095_a[0].monthly_data[{_month_idx}].advance_ptc"
    )


# ---------------------------------------------------------------------------
# Unified field-name -> canonical path map (synthetic + real IRS widgets)
# ---------------------------------------------------------------------------
FORM_1095_A_FIELD_MAP: dict[str, str] = {
    # --- Synthetic keys (test fixture) --------------------------------
    "marketplace_id": "forms_1095_a[0].marketplace_id",
    "policy_start_date": "forms_1095_a[0].policy_start_date",
    "policy_end_date": "forms_1095_a[0].policy_end_date",
}

# Synthetic monthly keys: monthly_data_N_enrollment_premium, etc.
for _month_idx in range(12):
    for _col, _field in [
        ("enrollment_premium", "enrollment_premium"),
        ("slcsp_premium", "slcsp_premium"),
        ("advance_ptc", "advance_ptc"),
    ]:
        FORM_1095_A_FIELD_MAP[
            f"monthly_data_{_month_idx}_{_col}"
        ] = f"forms_1095_a[0].monthly_data[{_month_idx}].{_field}"

# Merge real IRS widget names
FORM_1095_A_FIELD_MAP.update(_1095_A_REAL_WIDGETS)


# Module-level singleton. The cascade wiring imports this directly.
INGESTER: PyPdfAcroFormIngester = PyPdfAcroFormIngester(
    name="1095_a_acroform",
    field_map={DocumentKind.FORM_1095_A: FORM_1095_A_FIELD_MAP},
)
