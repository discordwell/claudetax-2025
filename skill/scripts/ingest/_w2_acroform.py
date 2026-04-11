"""Tier 1 ingester specialization for Form W-2 fillable PDFs.

This wires the base :class:`PyPdfAcroFormIngester` with a W-2-specific
``field_map`` so that AcroForm widget values extracted from a fillable W-2
land at canonical-return paths like ``w2s[0].box1_wages`` instead of the
fallback ``_acroform_raw.<name>`` pseudo-paths.

========================================================================
LOUD TODO: RESEARCH REAL IRS W-2 ACROFORM FIELD NAMES
========================================================================
The IRS does not publish the internal AcroForm field names for Form W-2.
Unlike Form 1040, there is no canonical fillable W-2 from irs.gov that
ships with widgets we can read. Payroll providers (ADP, Gusto, Paychex,
QuickBooks, state employers) each issue their own fillable W-2 templates
with *their own* field names.

The mapping below is therefore SYNTHETIC: the keys are names we invented
for the unit test fixture (``wages_box1``, ``fed_withholding_box2``, ...)
and the values are the canonical paths we want them to land on. This is
*sufficient* for the Tier 1 contract and tests, but it does NOT recognize
a real-world W-2 PDF in the field.

A follow-up task MUST:
  1. Collect a representative sample of fillable W-2 PDFs from the major
     payroll providers (ADP, Gusto, Paychex, QuickBooks, Intuit TurboTax
     blank W-2, etc.).
  2. For each, dump the AcroForm field names via
     ``pypdf.PdfReader(path).get_fields()``.
  3. Populate additional entries in ``W2_FIELD_MAP`` keyed by the real
     field names. Consider splitting per-issuer maps if the names collide.
  4. Add fixtures / parametrized tests per issuer.
========================================================================
"""
from __future__ import annotations

from skill.scripts.ingest._pipeline import DocumentKind
from skill.scripts.ingest._pypdf_acroform import PyPdfAcroFormIngester


# ---------------------------------------------------------------------------
# Synthetic W-2 AcroForm field map
# ---------------------------------------------------------------------------
#
# NOTE: These key strings (``wages_box1`` etc.) are NOT real IRS AcroForm
# field names. They are synthetic placeholders used by the unit test
# fixture (which generates its own fillable PDF with these names). See
# the loud TODO at the top of this module.
W2_FIELD_MAP: dict[str, str] = {
    # Employer identity
    "employer_name": "w2s[0].employer_name",
    "employer_ein": "w2s[0].employer_ein",
    # Box 1 — wages, tips, other compensation
    "wages_box1": "w2s[0].box1_wages",
    # Box 2 — federal income tax withheld
    "fed_withholding_box2": "w2s[0].box2_federal_income_tax_withheld",
    # Box 3 — social security wages
    "ss_wages_box3": "w2s[0].box3_social_security_wages",
    # Box 4 — social security tax withheld
    "ss_tax_box4": "w2s[0].box4_social_security_tax_withheld",
    # Box 5 — Medicare wages and tips
    "medicare_wages_box5": "w2s[0].box5_medicare_wages",
    # Box 6 — Medicare tax withheld
    "medicare_tax_box6": "w2s[0].box6_medicare_tax_withheld",
    # Box 7 — social security tips
    "ss_tips_box7": "w2s[0].box7_social_security_tips",
    # Box 8 — allocated tips
    "allocated_tips_box8": "w2s[0].box8_allocated_tips",
    # Box 10 — dependent care benefits
    "dep_care_box10": "w2s[0].box10_dependent_care_benefits",
    # Box 11 — nonqualified plans
    "nonqualified_box11": "w2s[0].box11_nonqualified_plans",
    # Box 15/16/17 — first state row
    "state_box15": "w2s[0].state_rows[0].state",
    "state_wages_box16": "w2s[0].state_rows[0].state_wages",
    "state_tax_box17": "w2s[0].state_rows[0].state_tax_withheld",
}


# ---------------------------------------------------------------------------
# Ingester instance
# ---------------------------------------------------------------------------
#
# The base ``PyPdfAcroFormIngester`` is a dataclass that already accepts
# ``name`` and ``field_map``, so we just instantiate it directly rather
# than subclassing.
INGESTER: PyPdfAcroFormIngester = PyPdfAcroFormIngester(
    name="w2_acroform",
    field_map={DocumentKind.FORM_W2: W2_FIELD_MAP},
)
