"""Tier 1 ingester for Form 1099-NEC (Nonemployee Compensation) fillable PDFs.

Wires a field-name map into the shared PyPdfAcroFormIngester base so that
AcroForm widget values from a 1099-NEC PDF land on the canonical
``forms_1099_nec[0].*`` paths on CanonicalReturn.

SYNTHETIC FIELD NAMES
---------------------
The keys in FORM_1099_NEC_FIELD_MAP below are SYNTHETIC placeholder names that
match the fixture produced by the test suite's ``_make_acroform_pdf`` helper.
The real IRS fillable 1099-NEC uses opaque internal field identifiers like
``topmostSubform[0].CopyB[0].f_1[0]`` — those need to be captured from an
actual IRS form PDF and swapped in. See the TODO in the module footer.

Until the real names are in place, this ingester is useful for:

- verifying the plumbing (classifier -> base ingester -> path rewrite)
- providing a realistic fixture for downstream engine/integration tests
- documenting which 1099-NEC boxes the skill currently cares about

1099-NEC is much simpler than 1099-INT: it only tracks box 1 (nonemployee
compensation) and box 4 (federal tax withheld) in the canonical model. Boxes
2 (direct sales, checkbox), 5/6/7 (state info) are not yet modeled.
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
# Values: canonical CanonicalReturn paths under ``forms_1099_nec[0]``.
#
# Covered boxes track the fields on skill.scripts.models.Form1099NEC.
FORM_1099_NEC_FIELD_MAP: dict[str, str] = {
    # Payer identity
    "payer_name": "forms_1099_nec[0].payer_name",
    "payer_tin": "forms_1099_nec[0].payer_tin",
    # Box 1 — Nonemployee compensation
    "box1_nonemployee_compensation": (
        "forms_1099_nec[0].box1_nonemployee_compensation"
    ),
    # Box 4 — Federal income tax withheld
    "box4_federal_income_tax_withheld": (
        "forms_1099_nec[0].box4_federal_income_tax_withheld"
    ),
}


# Module-level singleton. The cascade wiring imports this directly.
INGESTER: PyPdfAcroFormIngester = PyPdfAcroFormIngester(
    name="1099_nec_acroform",
    field_map={DocumentKind.FORM_1099_NEC: FORM_1099_NEC_FIELD_MAP},
)


# TODO(taxes): Replace the SYNTHETIC keys in FORM_1099_NEC_FIELD_MAP with the
# real IRS AcroForm widget names from the official fillable 1099-NEC PDF.
# Procedure: download the IRS fillable 1099-NEC for TY2025, open with pypdf,
# iterate ``reader.get_fields()``, match each printed box label to its widget
# name, and swap into the map above. Tests and downstream canonical paths do
# not need to change — only the left-hand-side keys. Boxes 5/6/7 (state info)
# and box 2 (direct sales checkbox) are not yet modeled on Form1099NEC and
# will need model changes before they can be mapped.
