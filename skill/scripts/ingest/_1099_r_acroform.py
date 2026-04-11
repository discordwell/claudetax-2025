"""Tier 1 ingester for Form 1099-R (Retirement Distributions) fillable PDFs.

Wires a field-name map into the shared PyPdfAcroFormIngester base so that
AcroForm widget values from a 1099-R PDF land on the canonical
``forms_1099_r[0].*`` paths on CanonicalReturn.

Form 1099-R reports pensions, IRA distributions, and retirement plan payments.
Values flow to 1040 line 4a/4b (IRA distributions), 5a/5b (pensions/annuities),
or Schedule 1 depending on the distribution code in box 7.

SYNTHETIC FIELD NAMES
---------------------
The keys in FORM_1099_R_FIELD_MAP below are SYNTHETIC placeholder names that
match the fixture produced by the test suite's ``_make_acroform_pdf`` helper.
The real IRS fillable 1099-R uses opaque internal field identifiers like
``topmostSubform[0].CopyB[0].f_1[0]`` — those need to be captured from an
actual IRS form PDF and swapped in. See the TODO in the module footer.

Until the real names are in place, this ingester is useful for:

- verifying the plumbing (classifier -> base ingester -> path rewrite)
- providing a realistic fixture for downstream engine/integration tests
- documenting which 1099-R boxes the skill currently cares about

Boxes covered (tracking fields on skill.scripts.models.Form1099R):

- payer identity (payer_name, payer_tin)
- box 1  — gross distribution
- box 2a — taxable amount
- box 2b — taxable amount not determined (checkbox)
- box 2b — total distribution (checkbox)
- box 4  — federal income tax withheld
- box 7  — distribution code(s)
- box 7  — IRA/SEP/SIMPLE checkbox
- box 9a — your percentage of total distribution
- box 12 — state tax withheld
- box 13 — state (two-letter code)
- box 16 — state distribution

Boxes NOT yet modeled on Form1099R (and therefore not mapped here):

- box 3  — capital gain (included in box 2a)
- box 5  — employee contributions / Roth contributions
- box 6  — net unrealized appreciation in employer's securities
- box 8  — other
- box 9b — total employee contributions
- box 10 — amount allocable to IRR within 5 years
- box 11 — 1st year of designated Roth contributions
- box 14 — state/payer's state no. (text ID, not a monetary box)
- box 15 — local tax withheld
- box 17 — name of locality
- box 18 — local distribution

Extending those requires model changes first; see TODO note below.
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
# Values: canonical CanonicalReturn paths under ``forms_1099_r[0]``.
#
# Covered boxes track the fields on skill.scripts.models.Form1099R.
FORM_1099_R_FIELD_MAP: dict[str, str] = {
    # Payer identity
    "payer_name": "forms_1099_r[0].payer_name",
    "payer_tin": "forms_1099_r[0].payer_tin",
    # Box 1 — Gross distribution
    "box1_gross_distribution": "forms_1099_r[0].box1_gross_distribution",
    # Box 2a — Taxable amount
    "box2a_taxable_amount": "forms_1099_r[0].box2a_taxable_amount",
    # Box 2b — Taxable amount not determined (checkbox)
    "box2b_taxable_amount_not_determined": (
        "forms_1099_r[0].box2b_taxable_amount_not_determined"
    ),
    # Box 2b — Total distribution (checkbox)
    "box2b_total_distribution": "forms_1099_r[0].box2b_total_distribution",
    # Box 4 — Federal income tax withheld
    "box4_federal_income_tax_withheld": (
        "forms_1099_r[0].box4_federal_income_tax_withheld"
    ),
    # Box 7 — Distribution code(s)
    "box7_distribution_codes": "forms_1099_r[0].box7_distribution_codes",
    # Box 7 — IRA/SEP/SIMPLE checkbox
    "box7_ira_sep_simple": "forms_1099_r[0].box7_ira_sep_simple",
    # Box 9a — Your percentage of total distribution
    "box9a_percent_total_distribution": (
        "forms_1099_r[0].box9a_percent_total_distribution"
    ),
    # Box 12 — State tax withheld
    "box12_state_tax_withheld": "forms_1099_r[0].box12_state_tax_withheld",
    # Box 13 — State (two-letter code)
    "box13_state": "forms_1099_r[0].box13_state",
    # Box 16 — State distribution
    "box16_state_distribution": "forms_1099_r[0].box16_state_distribution",
}


# Module-level singleton. The cascade wiring imports this directly.
INGESTER: PyPdfAcroFormIngester = PyPdfAcroFormIngester(
    name="1099_r_acroform",
    field_map={DocumentKind.FORM_1099_R: FORM_1099_R_FIELD_MAP},
)


# TODO(taxes): Replace the SYNTHETIC keys in FORM_1099_R_FIELD_MAP with the
# real IRS AcroForm widget names from the official fillable 1099-R PDF.
# Procedure: download the IRS fillable 1099-R for TY2025, open with pypdf,
# iterate ``reader.get_fields()``, match each printed box label to its widget
# name, and swap into the map above. Tests and downstream canonical paths do
# not need to change — only the left-hand-side keys. Boxes 3, 5, 6, 8, 9b, 10,
# 11, 14, 15, 17, 18 are not yet modeled on Form1099R and will need model
# changes before they can be mapped.
