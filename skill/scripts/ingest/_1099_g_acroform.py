"""Tier 1 ingester for Form 1099-G (Certain Government Payments) fillable PDFs.

Wires a field-name map into the shared PyPdfAcroFormIngester base so that
AcroForm widget values from a 1099-G PDF land on the canonical
``forms_1099_g[0].*`` paths on CanonicalReturn.

SYNTHETIC FIELD NAMES
---------------------
The keys in FORM_1099_G_FIELD_MAP below are SYNTHETIC placeholder names that
match the fixture produced by the test suite's ``_make_acroform_pdf`` helper.
The real IRS fillable 1099-G uses opaque internal field identifiers like
``topmostSubform[0].CopyB[0].f_1[0]`` — those need to be captured from an
actual IRS form PDF and swapped in. See the TODO in the module footer.

Until the real names are in place, this ingester is useful for:

- verifying the plumbing (classifier -> base ingester -> path rewrite)
- providing a realistic fixture for downstream engine/integration tests
- documenting which 1099-G boxes the skill currently cares about

Boxes covered (mirrors ``skill.scripts.models.Form1099G``):

- Payer name, payer TIN
- Box 1 — Unemployment compensation
- Box 2 — State or local income tax refunds, credits, or offsets
- Box 3 — Box 2 amount is for tax year (prior-year indicator)
- Box 4 — Federal income tax withheld
- Box 5 — RTAA payments
- Box 6 — Taxable grants
- Box 7 — Agriculture payments

Boxes NOT yet modeled on ``Form1099G`` (therefore not mapped here):

- Box 8 — Trade or business income checkbox
- Box 9 — Market gain (CCC loans)
- Box 10a / 10b / 11 — State information

Extending those requires a ``models.py`` change first.
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
# Values: canonical CanonicalReturn paths under ``forms_1099_g[0]``.
#
# Covered boxes track the fields on skill.scripts.models.Form1099G.
FORM_1099_G_FIELD_MAP: dict[str, str] = {
    # Payer identity
    "payer_name": "forms_1099_g[0].payer_name",
    "payer_tin": "forms_1099_g[0].payer_tin",
    # Box 1 — Unemployment compensation
    "box1_unemployment_compensation": (
        "forms_1099_g[0].box1_unemployment_compensation"
    ),
    # Box 2 — State or local income tax refunds, credits, or offsets
    "box2_state_or_local_income_tax_refund": (
        "forms_1099_g[0].box2_state_or_local_income_tax_refund"
    ),
    # Box 3 — Tax year the box 2 amount is for (prior-year indicator)
    "box2_tax_year": "forms_1099_g[0].box2_tax_year",
    # Box 4 — Federal income tax withheld
    "box4_federal_income_tax_withheld": (
        "forms_1099_g[0].box4_federal_income_tax_withheld"
    ),
    # Box 5 — RTAA payments (Reemployment Trade Adjustment Assistance)
    "box5_rtaa_payments": "forms_1099_g[0].box5_rtaa_payments",
    # Box 6 — Taxable grants
    "box6_taxable_grants": "forms_1099_g[0].box6_taxable_grants",
    # Box 7 — Agriculture payments
    "box7_agricultural_payments": "forms_1099_g[0].box7_agricultural_payments",
}


# Module-level singleton. The cascade wiring imports this directly.
INGESTER: PyPdfAcroFormIngester = PyPdfAcroFormIngester(
    name="1099_g_acroform",
    field_map={DocumentKind.FORM_1099_G: FORM_1099_G_FIELD_MAP},
)


# TODO(taxes): Replace the SYNTHETIC keys in FORM_1099_G_FIELD_MAP with the
# real IRS AcroForm widget names from the official fillable 1099-G PDF.
# Procedure: download the IRS fillable 1099-G for TY2025, open with pypdf,
# iterate ``reader.get_fields()``, match each printed box label to its widget
# name, and swap into the map above. Tests and downstream canonical paths do
# not need to change — only the left-hand-side keys. Boxes 8 (trade/business
# checkbox), 9 (market gain), and 10a/10b/11 (state info) are not yet modeled
# on Form1099G and will need model changes before they can be mapped.
