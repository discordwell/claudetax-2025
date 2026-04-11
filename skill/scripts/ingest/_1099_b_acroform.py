"""Tier 1 ingester for Form 1099-B (Proceeds From Broker) fillable PDFs.

Wires a field-name map into the shared PyPdfAcroFormIngester base so that
AcroForm widget values from a 1099-B PDF land on the canonical
``forms_1099_b[0].*`` paths on CanonicalReturn.

Real-IRS widget compatibility (wave 6)
---------------------------------------
The IRS does NOT publish a fillable Form 1099-B. Probed URLs (all
returned HTTP 404 on the IRS PDF CDN as of 2026-04-11):

- ``https://www.irs.gov/pub/irs-pdf/f1099b.pdf`` -- 404
- ``https://www.irs.gov/pub/irs-pdf/f1099b-dft.pdf`` -- 404

The IRS form the payer/broker issues is a paper-scannable / flattened
PDF template, not an interactive AcroForm. Brokers who file 1099-B
electronically submit via the IRS FIRE / IRIS channels using XML,
not PDF AcroForms. Recipient copies brokers send to taxpayers are
rendered as consolidated broker statements (typically flattened PDF
with a multi-row 8949-style table) or on paper, not fillable AcroForms.

Upgrade path: the 1099-B Azure Document Intelligence ingester
(``_azure_doc_intelligence.py``) already handles real-world broker
summary statements via the Unified US Tax prebuilt model -- it reads
the tabular layout natively and produces populated
``Form1099BTransaction`` entries. Use it for real 1099-B statements;
this AcroForm ingester remains synthetic-only for unit testing the
cascade plumbing.

This ingester is therefore useful only for:

- verifying the plumbing (classifier -> base ingester -> path rewrite)
- providing a realistic single-sale fixture for downstream engine tests
- documenting which 1099-B boxes the skill currently cares about

SINGLE-TRANSACTION LIMITATION (important!)
------------------------------------------
Strictly speaking, an IRS Form 1099-B represents ONE transaction per form.
In practice, brokerage customers receive a *broker summary statement* that
bundles many transactions — these are almost always rendered as Form 8949
page supplements (tabular rows) and NOT as repeatable AcroForm widget
fields. pypdf / the AcroForm widget model has no good story for "N repeating
row groups whose count is only known at fill time," so this Tier 1 ingester
is DELIBERATELY limited to ONE transaction per 1099-B form. Every
per-transaction synthetic field maps onto ``forms_1099_b[0].transactions[0].*``.

If you need to import a real-world multi-row broker summary, use the escape
hatch: the 1099-B Azure Document Intelligence ingester. It reads the tabular
8949 layout natively and produces a populated list of
``Form1099BTransaction`` entries without needing AcroForm widgets at all.

Until the real field names are in place and/or a multi-transaction story
exists, this ingester is useful for:

- verifying the plumbing (classifier -> base ingester -> path rewrite)
- providing a realistic single-sale fixture for downstream engine tests
- documenting which 1099-B boxes the skill currently cares about
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
# Values: canonical CanonicalReturn paths under ``forms_1099_b[0]``.
#
# NOTE: every per-transaction field targets ``transactions[0].*`` because this
# Tier 1 ingester is SINGLE-TRANSACTION-ONLY by design. See the module
# docstring above and ``test_single_transaction_only_by_design``.
FORM_1099_B_FIELD_MAP: dict[str, str] = {
    # Broker / payer identity (form-level)
    "broker_name": "forms_1099_b[0].broker_name",
    # Transaction description (Form 8949 column a)
    "description": "forms_1099_b[0].transactions[0].description",
    # Date sold / disposed (Form 8949 column c)
    "date_sold": "forms_1099_b[0].transactions[0].date_sold",
    # Proceeds (Form 8949 column d, 1099-B box 1d)
    "proceeds": "forms_1099_b[0].transactions[0].proceeds",
    # Cost or other basis (Form 8949 column e, 1099-B box 1e)
    "cost_basis": "forms_1099_b[0].transactions[0].cost_basis",
    # Wash sale loss disallowed (1099-B box 1g)
    "wash_sale_loss_disallowed": (
        "forms_1099_b[0].transactions[0].wash_sale_loss_disallowed"
    ),
    # Federal income tax withheld (1099-B box 4, form-level)
    "box4_federal_withholding": "forms_1099_b[0].box4_federal_income_tax_withheld",
    # Short-term / long-term indicator. On the real form this is a checkbox
    # (box 2 — "Short-term"/"Long-term gain or loss"); the ingester will need
    # to interpret the checkbox state when real field names are swapped in.
    # For the synthetic fixture we accept a plain flag value.
    "is_long_term_flag": "forms_1099_b[0].transactions[0].is_long_term",
}


# Module-level singleton. The cascade wiring imports this directly.
INGESTER: PyPdfAcroFormIngester = PyPdfAcroFormIngester(
    name="1099_b_acroform",
    field_map={DocumentKind.FORM_1099_B: FORM_1099_B_FIELD_MAP},
)


# ---------------------------------------------------------------------------
# TODO(taxes): TWO outstanding limitations on this ingester — READ BEFORE USE
# ---------------------------------------------------------------------------
#
# (a) SYNTHETIC FIELD NAMES
#     The keys in FORM_1099_B_FIELD_MAP are SYNTHETIC and only match the
#     test fixture's reportlab-generated PDF. Replace with real IRS AcroForm
#     widget names from the official fillable 1099-B PDF. Procedure: download
#     the IRS fillable 1099-B for TY2025, open with pypdf, iterate
#     ``reader.get_fields()``, match each printed box label to its widget
#     name, and swap into the map above. Canonical paths on the right-hand
#     side don't need to change. The short-term/long-term indicator is a
#     checkbox on the real form (box 2) — the ingester will need to interpret
#     the checkbox state ("/Yes", "/Off", etc.) when the real names land.
#
# (b) SINGLE-TRANSACTION-ONLY LIMITATION
#     This ingester maps every per-transaction synthetic field to
#     ``forms_1099_b[0].transactions[0].*``. It CANNOT handle multi-row
#     broker summary statements (which are the common real-world shape of a
#     1099-B). The escape hatch for multi-row statements is the 1099-B Azure
#     Document Intelligence ingester, which reads the tabular 8949 layout
#     natively and produces a populated list of Form1099BTransaction rows.
#     Do NOT paper over this by adding ``transactions[1]``, ``transactions[2]``
#     keys here without first coordinating with the multi-row Azure path.
