"""Tier 1 ingester for Form SSA-1099 (Social Security Benefit Statement) PDFs.

Wires a field-name map into the shared PyPdfAcroFormIngester base so that
AcroForm widget values from an SSA-1099 PDF land on the canonical
``forms_ssa_1099[0].*`` paths on CanonicalReturn.

Form SSA-1099 reports Social Security benefits paid by the SSA during the tax
year. The taxable portion flows through the Social Security benefits worksheet
onto 1040 lines 6a (total) and 6b (taxable). SSA-1099 is issued by the Social
Security Administration — NOT the IRS — and its box layout differs from the
1099-MISC/NEC/INT family: it has only boxes 1-8, no boxes 9, 10, or 11.

Box layout (per SSA Publication EN-05-10032, ``ssa-1099.pdf``):

- Box 1  — Name of beneficiary (text, not money)
- Box 2  — Beneficiary's Social Security number
- Box 3  — Benefits paid in <tax year> (gross benefits)
- Box 4  — Benefits repaid to SSA in <tax year>
- Box 5  — Net benefits for <tax year> (Box 3 minus Box 4)
- Box 6  — Voluntary federal income tax withheld
- Box 7  — Address (recipient's mailing address — informational, not money)
- Box 8  — Claim number (use this number if you have questions)

There is also a "Description of Amount in Box 3" narrative section, which
typically itemizes Medicare Part B and Part D premiums deducted from the
monthly benefit payment. The FormSSA1099 model tracks those separately because
they are deductible as self-employed health insurance (if Schedule C earnings
support it) or as an itemized Schedule A medical expense.

FormSSA1099 model fields mapped here:

- recipient_is_taxpayer    (bool, from Box 1 name — see note below)
- box3_total_benefits      (Box 3)
- box4_benefits_repaid     (Box 4)
- box5_net_benefits        (Box 5)
- box6_federal_income_tax_withheld (Box 6)
- medicare_part_b_premiums (from Description of Amount in Box 3)
- medicare_part_d_premiums (from Description of Amount in Box 3)

Note: the SSA-1099's Box 1 is a beneficiary *name* string; the ingester does
NOT auto-populate ``recipient_is_taxpayer`` (a bool) from it because that
requires a join against the filer's name on the taxpayer profile — a
responsibility that lives in the ingestion-to-canonical rewriter, not here.
A synthetic ``recipient_is_taxpayer`` field is exposed on the map anyway so
a hand-crafted fixture can exercise the path rewriting; downstream code may
override it once the taxpayer profile join is wired.

SYNTHETIC FIELD NAMES
---------------------
The keys in FORM_SSA_1099_FIELD_MAP below are SYNTHETIC placeholder names that
match the fixture produced by the test suite's ``_make_acroform_pdf`` helper.
The real SSA fillable SSA-1099 uses opaque internal field identifiers — those
need to be captured from an official SSA form PDF and swapped in. See the
TODO in the module footer. Until the real names are in place, this ingester
is useful for:

- verifying the plumbing (classifier -> base ingester -> path rewrite)
- providing a realistic fixture for downstream engine/integration tests
- documenting which SSA-1099 boxes the skill currently cares about

Not mapped (no FormSSA1099 model field — informational only):

- Box 1 — beneficiary name (used only for identity matching, not stored as
  a canonical field on FormSSA1099)
- Box 2 — beneficiary SSN (same rationale)
- Box 7 — address (informational)
- Box 8 — claim number (informational)

Sources:
- https://www.ssa.gov/pubs/EN-05-10032.pdf (official SSA-1099 description)
- https://www.ssa.gov/forms/ssa-1099.html
- skill.scripts.models.FormSSA1099
"""
from __future__ import annotations

from skill.scripts.ingest._pipeline import DocumentKind
from skill.scripts.ingest._pypdf_acroform import PyPdfAcroFormIngester

# ---------------------------------------------------------------------------
# Synthetic field-name -> canonical path map
# ---------------------------------------------------------------------------
#
# Keys: SYNTHETIC widget names used by the test fixture (and any hand-crafted
#       fillable PDFs the dev workflow generates). Replace with real SSA
#       AcroForm identifiers in a follow-up patch.
# Values: canonical CanonicalReturn paths under ``forms_ssa_1099[0]``.
#
# Covered fields track every non-identity field on
# skill.scripts.models.FormSSA1099.
FORM_SSA_1099_FIELD_MAP: dict[str, str] = {
    # Recipient flag (synthetic — see module docstring)
    "recipient_is_taxpayer": "forms_ssa_1099[0].recipient_is_taxpayer",
    # Box 3 — Benefits paid in the tax year (gross)
    "box3_total_benefits": "forms_ssa_1099[0].box3_total_benefits",
    # Box 4 — Benefits repaid to SSA in the tax year
    "box4_benefits_repaid": "forms_ssa_1099[0].box4_benefits_repaid",
    # Box 5 — Net benefits for the tax year (Box 3 - Box 4)
    "box5_net_benefits": "forms_ssa_1099[0].box5_net_benefits",
    # Box 6 — Voluntary federal income tax withheld
    "box6_federal_income_tax_withheld": (
        "forms_ssa_1099[0].box6_federal_income_tax_withheld"
    ),
    # Description of Amount in Box 3 — Medicare Part B premiums
    "medicare_part_b_premiums": "forms_ssa_1099[0].medicare_part_b_premiums",
    # Description of Amount in Box 3 — Medicare Part D premiums
    "medicare_part_d_premiums": "forms_ssa_1099[0].medicare_part_d_premiums",
}


# Module-level singleton. The cascade wiring imports this directly.
INGESTER: PyPdfAcroFormIngester = PyPdfAcroFormIngester(
    name="ssa_1099_acroform",
    field_map={DocumentKind.FORM_SSA_1099: FORM_SSA_1099_FIELD_MAP},
)


# TODO(taxes): Replace the SYNTHETIC keys in FORM_SSA_1099_FIELD_MAP with the
# real SSA AcroForm widget names from the official fillable SSA-1099 PDF.
# Procedure: obtain the SSA fillable SSA-1099 (note: the SSA does NOT publish a
# generic blank fillable like the IRS does — the form is generated per-
# beneficiary via the my Social Security portal, so this may require OCR-based
# field discovery from a sample statement). Open with pypdf, iterate
# ``reader.get_fields()``, match each printed box label to its widget name,
# and swap into the map above. Tests and downstream canonical paths do not
# need to change — only the left-hand-side keys.
#
# TODO(taxes): Boxes 1, 2, 7, 8 (beneficiary name, SSN, address, claim number)
# are identity/informational fields that are not currently tracked on
# FormSSA1099. If we add identity matching (to auto-set
# ``recipient_is_taxpayer``) or need claim numbers for correspondence, extend
# models.FormSSA1099 first, then add mappings here.
