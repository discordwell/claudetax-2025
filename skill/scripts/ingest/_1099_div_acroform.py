"""Tier 1 ingester for Form 1099-DIV (Dividends and Distributions) fillable PDFs.

Wires a field-name map into the shared PyPdfAcroFormIngester base so that
AcroForm widget values from a 1099-DIV PDF land on the canonical
``forms_1099_div[0].*`` paths on CanonicalReturn.

SYNTHETIC FIELD NAMES
---------------------
The keys in FORM_1099_DIV_FIELD_MAP below are SYNTHETIC placeholder names that
match the fixture produced by the test suite's ``_make_acroform_pdf`` helper.
The real IRS fillable 1099-DIV uses opaque internal field identifiers like
``topmostSubform[0].CopyB[0].f_1[0]`` — those need to be captured from an
actual IRS form PDF and swapped in. See the TODO in the module footer.

Until the real names are in place, this ingester is useful for:

- verifying the plumbing (classifier -> base ingester -> path rewrite)
- providing a realistic fixture for downstream engine/integration tests
- documenting which 1099-DIV boxes the skill currently cares about
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
# Values: canonical CanonicalReturn paths under ``forms_1099_div[0]``.
#
# Covered boxes track the fields on skill.scripts.models.Form1099DIV.
FORM_1099_DIV_FIELD_MAP: dict[str, str] = {
    # Payer identity
    "payer_name": "forms_1099_div[0].payer_name",
    "payer_tin": "forms_1099_div[0].payer_tin",
    # Box 1a — Total ordinary dividends
    "box1a_ordinary_dividends": "forms_1099_div[0].box1a_ordinary_dividends",
    # Box 1b — Qualified dividends
    "box1b_qualified_dividends": "forms_1099_div[0].box1b_qualified_dividends",
    # Box 2a — Total capital gain distributions
    "box2a_total_capital_gain_distributions": (
        "forms_1099_div[0].box2a_total_capital_gain_distributions"
    ),
    # Box 2b — Unrecaptured Section 1250 gain
    "box2b_unrecaptured_sec_1250_gain": (
        "forms_1099_div[0].box2b_unrecaptured_sec_1250_gain"
    ),
    # Box 2c — Section 1202 gain
    "box2c_section_1202_gain": "forms_1099_div[0].box2c_section_1202_gain",
    # Box 2d — Collectibles (28%) gain
    "box2d_collectibles_28pct_gain": (
        "forms_1099_div[0].box2d_collectibles_28pct_gain"
    ),
    # Box 3 — Nondividend distributions
    "box3_nondividend_distributions": (
        "forms_1099_div[0].box3_nondividend_distributions"
    ),
    # Box 4 — Federal income tax withheld
    "box4_federal_income_tax_withheld": (
        "forms_1099_div[0].box4_federal_income_tax_withheld"
    ),
    # Box 5 — Section 199A dividends
    "box5_section_199a_dividends": "forms_1099_div[0].box5_section_199a_dividends",
    # Box 6 — Investment expenses
    "box6_investment_expenses": "forms_1099_div[0].box6_investment_expenses",
    # Box 7 — Foreign tax paid
    "box7_foreign_tax_paid": "forms_1099_div[0].box7_foreign_tax_paid",
    # Box 11 — Exempt-interest dividends
    "box11_exempt_interest_dividends": (
        "forms_1099_div[0].box11_exempt_interest_dividends"
    ),
}


# Module-level singleton. The cascade wiring imports this directly.
INGESTER: PyPdfAcroFormIngester = PyPdfAcroFormIngester(
    name="1099_div_acroform",
    field_map={DocumentKind.FORM_1099_DIV: FORM_1099_DIV_FIELD_MAP},
)


# TODO(taxes): Replace the SYNTHETIC keys in FORM_1099_DIV_FIELD_MAP with the
# real IRS AcroForm widget names from the official fillable 1099-DIV PDF.
# Procedure: download the IRS fillable 1099-DIV for TY2025, open with pypdf,
# iterate ``reader.get_fields()``, match each printed box label to its widget
# name, and swap into the map above. Tests and downstream canonical paths do
# not need to change — only the left-hand-side keys.
