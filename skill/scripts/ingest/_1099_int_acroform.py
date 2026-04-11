"""Tier 1 ingester for Form 1099-INT (Interest Income) fillable PDFs.

Wires a field-name map into the shared PyPdfAcroFormIngester base so that
AcroForm widget values from a 1099-INT PDF land on the canonical
``forms_1099_int[0].*`` paths on CanonicalReturn.

SYNTHETIC FIELD NAMES
---------------------
The keys in FORM_1099_INT_FIELD_MAP below are SYNTHETIC placeholder names that
match the fixture produced by the test suite's ``_make_acroform_pdf`` helper.
The real IRS fillable 1099-INT uses opaque internal field identifiers like
``topmostSubform[0].CopyB[0].f_1[0]`` — those need to be captured from an
actual IRS form PDF and swapped in. See the TODO in the module footer.

Until the real names are in place, this ingester is useful for:

- verifying the plumbing (classifier -> base ingester -> path rewrite)
- providing a realistic fixture for downstream engine/integration tests
- documenting which 1099-INT boxes the skill currently cares about
"""
from __future__ import annotations

from skill.scripts.ingest._pipeline import DocumentKind
from skill.scripts.ingest._pypdf_acroform import PyPdfAcroFormIngester

# ---------------------------------------------------------------------------
# Synthetic field-name -> canonical path map
# ---------------------------------------------------------------------------
#
# Keys: SYNTHETIC widget names used by the test fixture (and any hand-crafted
#       fillable PDFs the dev workflow generates). Replace with real IRS
#       AcroForm identifiers in a follow-up patch.
# Values: canonical CanonicalReturn paths under ``forms_1099_int[0]``.
#
# Covered boxes track the fields on skill.scripts.models.Form1099INT.
FORM_1099_INT_FIELD_MAP: dict[str, str] = {
    # Payer identity
    "payer_name": "forms_1099_int[0].payer_name",
    "payer_tin": "forms_1099_int[0].payer_tin",
    # Box 1 — Interest income
    "box1_interest_income": "forms_1099_int[0].box1_interest_income",
    # Box 2 — Early withdrawal penalty
    "box2_early_withdrawal_penalty": "forms_1099_int[0].box2_early_withdrawal_penalty",
    # Box 3 — Interest on US Savings Bonds and Treasury obligations
    "box3_us_savings_bond_and_treasury_interest": (
        "forms_1099_int[0].box3_us_savings_bond_and_treasury_interest"
    ),
    # Box 4 — Federal income tax withheld
    "box4_federal_income_tax_withheld": (
        "forms_1099_int[0].box4_federal_income_tax_withheld"
    ),
    # Box 5 — Investment expenses
    "box5_investment_expenses": "forms_1099_int[0].box5_investment_expenses",
    # Box 6 — Foreign tax paid
    "box6_foreign_tax_paid": "forms_1099_int[0].box6_foreign_tax_paid",
    # Box 8 — Tax-exempt interest
    "box8_tax_exempt_interest": "forms_1099_int[0].box8_tax_exempt_interest",
    # Box 9 — Specified private activity bond interest
    "box9_specified_private_activity_bond_interest": (
        "forms_1099_int[0].box9_specified_private_activity_bond_interest"
    ),
    # Box 13 — Bond premium on tax-exempt bonds
    "box13_bond_premium_on_tax_exempt_bonds": (
        "forms_1099_int[0].box13_bond_premium_on_tax_exempt_bonds"
    ),
}


# Module-level singleton. The cascade wiring imports this directly.
INGESTER: PyPdfAcroFormIngester = PyPdfAcroFormIngester(
    name="1099_int_acroform",
    field_map={DocumentKind.FORM_1099_INT: FORM_1099_INT_FIELD_MAP},
)


# TODO(taxes): Replace the SYNTHETIC keys in FORM_1099_INT_FIELD_MAP with the
# real IRS AcroForm widget names from the official fillable 1099-INT PDF.
# Procedure: download the IRS fillable 1099-INT for TY2025, open with pypdf,
# iterate ``reader.get_fields()``, match each printed box label to its widget
# name, and swap into the map above. Tests and downstream canonical paths do
# not need to change — only the left-hand-side keys.
