"""Tier 1 ingester for Form 1099-R (Retirement Distributions) fillable PDFs.

Wires a field-name map into the shared PyPdfAcroFormIngester base so that
AcroForm widget values from a 1099-R PDF land on the canonical
``forms_1099_r[0].*`` paths on CanonicalReturn.

Form 1099-R reports pensions, IRA distributions, and retirement plan payments.
Values flow to 1040 line 4a/4b (IRA distributions), 5a/5b (pensions/annuities),
or Schedule 1 depending on the distribution code in box 7.

Real-IRS widget compatibility (wave 6)
---------------------------------------
Fetched ``https://www.irs.gov/pub/irs-pdf/f1099r.pdf`` (archived at
``skill/reference/irs_forms/f1099r_ty2024.pdf``, SHA-256
``d6b7be486a4d7419968df8a7c8cc36284b672d5fe66cff12359255eb56d31f7a``).
The PDF is a real AcroForm with 254 widgets across 5 copies (CopyA,
Copy1, CopyB, CopyC, Copy2). CopyA uses ``f1_NN`` widget leaves and
uses ``Box10_ReadOrder`` as the box-10 container, while every other
copy uses ``f2_NN`` and names the box-10 container simply ``Box10``
(the other ReadOrder containers are consistent). The real-widget map
below is enumerated per copy because of that asymmetry.

Box semantics are decoded from the ``BoxN_ReadOrder`` containers in
the widget tree. Only money fields (plus box 7 distribution codes) are
mapped; the two box-2b checkboxes ``taxable_amount_not_determined``
and ``total_distribution`` on the canonical model are left to the
synthetic fixture because the real PDF exposes them as two separate
checkboxes ``c2_1`` / ``c2_2`` whose correspondence to the two model
flags needs wet-test confirmation.

Boxes covered (mirroring the fields on ``skill.scripts.models.Form1099R``):

- payer identity (payer_name, payer_tin)
- box 1  -- gross distribution
- box 2a -- taxable amount
- box 4  -- federal income tax withheld
- box 7  -- distribution code(s)
- box 9a -- your percentage of total distribution
- box 14 -- state tax withheld (row 1 -> ``box12_state_tax_withheld``)
- box 15 -- state/payer's state no (row 1 -> ``box13_state``)
- box 16 -- state distribution (row 1 -> ``box16_state_distribution``)

The state-row canonical paths mirror the single-row shape of the
Form1099R model (which does not expose multi-row state info). Wave 7+
may widen the model to expose the second row (f2_23 / f2_25 / f2_27).

Boxes NOT mapped (not in the canonical model):

- box 3 (capital gain, in box 2a)
- box 5 (employee contributions), box 6 (net unrealized appreciation)
- box 8 (other amount + %), box 9b (total employee contributions)
- box 10 (IRR), box 11 (1st year Roth), box 12 FATCA, box 13 date of payment
- box 2b checkboxes (see note above)
- box 7 IRA/SEP/SIMPLE checkbox (``c[12]_4`` -- needs wet test)
- box 17 local tax, box 18 locality name, box 19 local distribution
"""
from __future__ import annotations

from skill.scripts.ingest._pipeline import DocumentKind
from skill.scripts.ingest._pypdf_acroform import PyPdfAcroFormIngester


# ---------------------------------------------------------------------------
# Real IRS 1099-R widget names -> canonical path
# ---------------------------------------------------------------------------
#
# CopyA uses ``f1_NN`` and ``Box10_ReadOrder``; every other copy uses
# ``f2_NN`` and just ``Box10`` for the box-10 container. The rest of
# the ReadOrder container names are consistent across copies.
_1099_R_REAL_WIDGETS: dict[str, str] = {}


def _add_r(copy: str, pfx: str, box10_container: str, tin_path: str) -> None:
    p = pfx
    _1099_R_REAL_WIDGETS.update({
        # LeftCol_ReadOrder holds the payer/recipient identity block.
        f"topmostSubform[0].{copy}[0].LeftCol_ReadOrder[0].{p}_01[0]":
            "forms_1099_r[0].payer_name",
        # CopyA nests the payer TIN under ``PayersTIN[0]`` subform;
        # every other copy puts it as a direct child of ``LeftCol_ReadOrder``.
        f"topmostSubform[0].{copy}[0].LeftCol_ReadOrder[0].{tin_path}":
            "forms_1099_r[0].payer_tin",
        # f2_08 = box 1 gross distribution (at CopyB level, not inside a
        # ``Box1_ReadOrder`` subform like on the other 1099 forms).
        f"topmostSubform[0].{copy}[0].{p}_08[0]":
            "forms_1099_r[0].box1_gross_distribution",
        # f2_09 = box 2a taxable amount
        f"topmostSubform[0].{copy}[0].{p}_09[0]":
            "forms_1099_r[0].box2a_taxable_amount",
        # f2_11 = box 4 federal income tax withheld
        f"topmostSubform[0].{copy}[0].{p}_11[0]":
            "forms_1099_r[0].box4_federal_income_tax_withheld",
        # Box7_ReadOrder.f2_14 = box 7 distribution codes
        f"topmostSubform[0].{copy}[0].Box7_ReadOrder[0].{p}_14[0]":
            "forms_1099_r[0].box7_distribution_codes",
        # Box9a_ReadOrder.f2_17 = box 9a percentage of total distribution
        f"topmostSubform[0].{copy}[0].Box9a_ReadOrder[0].{p}_17[0]":
            "forms_1099_r[0].box9a_percent_total_distribution",
        # Box14_ReadOrder.f2_22 = box 14 state tax withheld row 1
        f"topmostSubform[0].{copy}[0].Box14_ReadOrder[0].{p}_22[0]":
            "forms_1099_r[0].box12_state_tax_withheld",
        # Box15_ReadOrder.f2_24 = box 15 state (two-letter) row 1
        f"topmostSubform[0].{copy}[0].Box15_ReadOrder[0].{p}_24[0]":
            "forms_1099_r[0].box13_state",
        # f2_26 = box 16 state distribution row 1 (no container)
        f"topmostSubform[0].{copy}[0].{p}_26[0]":
            "forms_1099_r[0].box16_state_distribution",
    })


for _copy, _pfx, _b10, _tin in [
    ("CopyA", "f1", "Box10_ReadOrder", "PayersTIN[0].f1_02[0]"),
    ("Copy1", "f2", "Box10", "f2_02[0]"),
    ("CopyB", "f2", "Box10", "f2_02[0]"),
    ("CopyC", "f2", "Box10", "f2_02[0]"),
    ("Copy2", "f2", "Box10", "f2_02[0]"),
]:
    _add_r(_copy, _pfx, _b10, _tin)


# ---------------------------------------------------------------------------
# Unified field-name -> canonical path map (synthetic + real IRS widgets)
# ---------------------------------------------------------------------------
FORM_1099_R_FIELD_MAP: dict[str, str] = {
    # --- Synthetic keys (test fixture) --------------------------------
    # Payer identity
    "payer_name": "forms_1099_r[0].payer_name",
    "payer_tin": "forms_1099_r[0].payer_tin",
    # Box 1 -- Gross distribution
    "box1_gross_distribution": "forms_1099_r[0].box1_gross_distribution",
    # Box 2a -- Taxable amount
    "box2a_taxable_amount": "forms_1099_r[0].box2a_taxable_amount",
    # Box 2b -- Taxable amount not determined (checkbox)
    "box2b_taxable_amount_not_determined": (
        "forms_1099_r[0].box2b_taxable_amount_not_determined"
    ),
    # Box 2b -- Total distribution (checkbox)
    "box2b_total_distribution": "forms_1099_r[0].box2b_total_distribution",
    # Box 4 -- Federal income tax withheld
    "box4_federal_income_tax_withheld": (
        "forms_1099_r[0].box4_federal_income_tax_withheld"
    ),
    # Box 7 -- Distribution code(s)
    "box7_distribution_codes": "forms_1099_r[0].box7_distribution_codes",
    # Box 7 -- IRA/SEP/SIMPLE checkbox
    "box7_ira_sep_simple": "forms_1099_r[0].box7_ira_sep_simple",
    # Box 9a -- Your percentage of total distribution
    "box9a_percent_total_distribution": (
        "forms_1099_r[0].box9a_percent_total_distribution"
    ),
    # Box 12 -- State tax withheld
    "box12_state_tax_withheld": "forms_1099_r[0].box12_state_tax_withheld",
    # Box 13 -- State (two-letter code)
    "box13_state": "forms_1099_r[0].box13_state",
    # Box 16 -- State distribution
    "box16_state_distribution": "forms_1099_r[0].box16_state_distribution",
    # --- Real IRS widget names (enumerated per copy) -----------------
    **_1099_R_REAL_WIDGETS,
}


# Module-level singleton. The cascade wiring imports this directly.
INGESTER: PyPdfAcroFormIngester = PyPdfAcroFormIngester(
    name="1099_r_acroform",
    field_map={DocumentKind.FORM_1099_R: FORM_1099_R_FIELD_MAP},
)
