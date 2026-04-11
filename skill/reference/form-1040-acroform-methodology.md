# Form 1040 AcroForm Widget Mapping — Methodology

This document describes how `skill/reference/form-1040-acroform-map.json`
was produced, so a future wave can repeat the process for Schedule A, B,
C, SE, or for next year's Form 1040 PDF, without re-inventing the
approach.

## Scope and status

* **What we mapped:** `skill/reference/form-1040-acroform-map.json` links
  every semantic Form 1040 line identifier used by
  `skill.scripts.output.form_1040.Form1040Fields` (Layer 1 dataclass) to
  its corresponding AcroForm widget annotation in the IRS-hosted
  fillable Form 1040 PDF.
* **What we did NOT map:** header identity fields (name, SSN, address),
  the full dependents-table (16 cells × 4 rows), refund banking (routing
  / account / type), page-2 signatures, third-party designee,
  preparer / firm block, and income sublines that Layer 1 does not yet
  track (1b–1i, 3c, 4c, 5c, 6c, 6d, 7b, 12a–12d, 13b, 27b, 27c, 30, 36,
  38). Each unmapped widget has a categorized `reason` in the JSON so a
  later pass can pick it up without re-opening the PDF.
* **Layer 2 consumer:** the widget names in this file are intended to
  be consumed by a future wave that replaces the current reportlab
  scaffold in `skill/scripts/output/form_1040.py` with a real AcroForm
  overlay (pypdf `update_page_form_field_values` or equivalent). This
  file is research only.

## Source PDF

* **URL:** <https://www.irs.gov/pub/irs-pdf/f1040.pdf> (public,
  IRS-hosted).
* **Fetched:** 2026-04-11.
* **SHA-256:** `3d31c226df0d189ced80e039d01cf0f8820c1019681a0f0ca6264de277b7e982`
* **Bytes:** 220,237
* **PDF `/Title` metadata:** `2025 Form 1040` (the IRS has already
  replaced the TY2024 asset at this URL with the TY2025 release, despite
  the TY2025 filing season not opening until January 2026). This has
  implications for the Layer 1 `Form1040Fields` dataclass, which was
  written against the TY2024 layout — see "Renumbering deltas" below.

Future agents should re-fetch the PDF and confirm the SHA-256 hash if
they want to use the same mapping. If the hash does not match, the IRS
has silently re-issued the PDF (this DOES happen mid-season for errata)
and the mapping must be revalidated.

## Tooling

* **pypdf** >= 5.1 (we used 5.9.0) — already pinned in
  `requirements.txt` for the project.
* **pdfplumber** >= 0.11 — already pinned. Used only to extract the
  visible label text near each widget so we could correlate widget
  names to IRS line numbers.
* No OCR. The IRS Form 1040 fillable PDF is a genuine AcroForm with a
  text layer and named widget annotations; OCR is never required.

## Step-by-step reproduction

### 1. Download

```python
import hashlib, urllib.request
url = "https://www.irs.gov/pub/irs-pdf/f1040.pdf"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
data = urllib.request.urlopen(req, timeout=60).read()
sha = hashlib.sha256(data).hexdigest()
open("/tmp/f1040.pdf", "wb").write(data)
```

Record `sha` in the JSON `source_pdf_sha256` field. If the new hash does
not match the previous run, diff the widget enumeration (step 2) before
trusting the old mapping.

### 2. Enumerate widgets by walking `/Annots`

`pypdf`'s `PdfReader.get_fields()` returns a dict keyed by partial
field name. That dict has two problems:

1. It includes non-terminal *group* containers (e.g. `topmostSubform[0]`,
   `Page1[0]`, `Table_Dependents[0]`) that have no widget annotation
   and therefore cannot be written to. On the TY2025 f1040.pdf,
   `get_fields()` returns 229 entries but only 199 of them are real
   terminal widgets.
2. It uses `/T` partial names, not the fully-qualified dotted path,
   so two sibling widgets with the same partial name collide.

The fix is to walk each page's `/Annots` array, filter to entries whose
`/Subtype == "/Widget"`, and assemble the full dotted name by walking
up the `/Parent` chain collecting `/T` values:

```python
from pypdf import PdfReader

reader = PdfReader("/tmp/f1040.pdf")
all_widgets = []
for p_idx, page in enumerate(reader.pages):
    annots = page.get("/Annots") or []
    for annot_ref in annots:
        annot = annot_ref.get_object()
        if annot.get("/Subtype") != "/Widget":
            continue
        parts = []
        node = annot
        seen = set()
        while node is not None and id(node) not in seen:
            seen.add(id(node))
            t = node.get("/T")
            if t is not None:
                parts.append(str(t))
            parent = node.get("/Parent")
            node = parent.get_object() if parent is not None else None
        full_name = ".".join(reversed(parts))
        rect = annot.get("/Rect")
        # /FT may live on an ancestor — walk up if missing:
        ft = annot.get("/FT")
        node2 = annot.get("/Parent")
        while ft is None and node2 is not None:
            node2 = node2.get_object()
            ft = node2.get("/FT")
            node2 = node2.get("/Parent")
        all_widgets.append({
            "name": full_name,
            "page": p_idx + 1,
            "rect": [float(r) for r in rect] if rect else None,
            "ft": str(ft) if ft else None,
        })
```

This yields 199 widgets for TY2025 f1040: 126 text boxes (`/Tx`) and
73 checkboxes (`/Btn`).

### 3. Sort into visual reading order

PDF coordinates are bottom-left origin. Sort widgets by `(page, -rect_top,
rect_left)` to get the top-down left-right reading order that matches
the visual form:

```python
widgets.sort(key=lambda w: (w["page"], -w["rect"][3], w["rect"][0]))
```

### 4. Correlate widgets to line numbers

The challenge is that IRS widget names follow an internal Adobe Designer
convention — `fN_MM[K]` for text, `cN_MM[K]` for checkboxes, where `N` is
the page number and `MM` is a monotonically-increasing counter with
**no documented semantic meaning**. There is no IRS-published map from
`fN_MM` to line numbers.

To correlate, we extracted the visible label text using pdfplumber and
matched each widget's `rect` to the nearest line label:

```python
import pdfplumber

with pdfplumber.open("/tmp/f1040.pdf") as pdf:
    page = pdf.pages[0]
    words = page.extract_words()
    # pdfplumber uses a top-left origin; convert widget PDF y → pdfplumber top:
    # pdfplumber_top ≈ page_height - pdf_rect_y
```

For each widget, find the label word whose `top` is nearest to
`792 - rect[3]` (the widget's top edge in pdfplumber coordinates) and
whose `x0` is on the line-label margin (x < 400 — labels live in the
left half of the form).

In practice, pdfplumber word `top` sits ~3 points *below* the widget's
rect top because the label is the baseline of the glyph while the
widget rect is the ascender. Use a tolerance of ±6 points.

### 5. Map to Layer 1 dataclass field names

The file `skill/scripts/output/form_1040.py` defines
`Form1040Fields` — a frozen dataclass whose field names follow the
TY2024 Form 1040 line numbers. For each `line_NN_description` field in
that dataclass, find the correlated widget name and record it as
`mapping[<field_name>] = {widget_name, type, page, rect, note?}`.

Fields with no widget (e.g. `taxpayer_name` is a Layer 1 string but the
PDF splits it into first + last name widgets) are mapped to the
primary widget and annotated with a `note` explaining the caller
responsibility.

### 6. Classify unmapped widgets

Every widget not claimed by `mapping`, `computed_copies`, or
`filing_status_checkboxes` is listed in `unmapped_widgets` with a
categorized `reason`. The categories (picked so a Schedule A/B/C/SE
agent can scan for the ones they care about) are:

* `header_row1_2` — taxpayer / spouse name and SSN.
* `header_address` — street / city / state / zip / foreign address.
* `header_checkbox_or_minor_label` — presidential election, digital
  asset, etc.
* `dependents_block` / `dependents_table` — the 4-row dependents table.
* `income_subline` — line 1b–1i, 2a sub-amount, 3c, 4c, 5c, 6c, 6d, 7b
  sublines that Layer 1 does not carry.
* `income_checkbox` — 3c / 4c / 5c / 6c / 7b modifier checkboxes.
* `page2_checkboxes_12d_13a_block_or_standard_deduction` — 12a..12d
  dependent/dual-status/age/blind checkboxes.
* `refund_banking_block` — 35b routing, 35c type, 35d account, 36
  applied-to-2026 ES.
* `signature_designee_preparer_block` — third-party designee,
  signatures, IP PIN, phone/email, preparer, firm.

## TY2025 renumbering deltas

The IRS has renumbered several lines on the TY2025 Form 1040 relative
to TY2024. The Layer 1 dataclass uses TY2024 names but is semantically
correct for TY2025; the widget mapping records the renumbering in
per-field `note` entries. Key deltas:

| Layer 1 name (TY2024) | TY2025 widget line | Notes |
|--|--|--|
| `line_11_adjusted_gross_income` | line 11a (page 1) + line 11b (page 2) | Split into two widgets because AGI is carried forward across the page break. See `computed_copies`. |
| `line_12_standard_or_itemized_deduction` | line 12e | TY2025 splits line 12 into 12a/b/c (dependent checkboxes), 12d (age/blind), and 12e (numeric). |
| `line_13_qbi_deduction` | line 13a | TY2025 splits line 13 into 13a (QBI) and 13b (Schedule 1-A additional deductions, new under OBBBA). Layer 1 does not yet populate 13b — see `skill/scripts/output/form_1040.py` module docstring. |
| `line_14_sum_12_13` | line 14 = 12e + 13a + 13b | Callers must ensure 13b is accounted for if non-zero. |
| `line_27_earned_income_credit` | line 27a | TY2025 adds 27b (clergy SE checkbox) and 27c (opt-out checkbox). Layer 1 carries only the amount. |

New for TY2025 and NOT in Layer 1 (tracked in `unmapped_widgets`):

* Line 1c (tip income reported separately — new per OBBBA).
* Line 1d (Medicaid waiver payments not reported on W-2).
* Line 13b (Schedule 1-A additional deductions — OBBBA).
* Line 27b / 27c (clergy / EIC opt-out).
* Line 30 (Refundable adoption credit from Form 8839).
* Line 36 (Amount of line 34 applied to 2026 estimated tax).
* Line 38 (Estimated tax penalty).

## Known limitations

1. **Mirrored checkbox annotations.** Several checkboxes appear twice
   in the widget list, once under a `Checkbox_ReadOrder[0]` / similar
   accessibility container and once directly under `Page1[0]`. For
   example `c1_8[0]` is the "Single" filing-status checkbox and exists
   as both `topmostSubform[0].Page1[0].Checkbox_ReadOrder[0].c1_8[0]`
   and `topmostSubform[0].Page1[0].c1_8[0]`. These are two annotation
   entries for the same logical field — toggling one toggles the other
   in a PDF viewer. Downstream AcroForm writers must set the
   appearance stream on BOTH annotations or the PDF will look
   inconsistent (one shown checked, one unchecked). pypdf's
   `update_page_form_field_values` handles this correctly if you pass
   the unqualified terminal name; if you write appearance streams by
   hand you must iterate every matching annotation.

2. **Multi-instance widgets in the dependents table.** The dependents
   table has 4 columns × 4 rows × 4 fields per dependent = 64 cells.
   Plus CTC/ODC checkboxes (2 per dependent = 8). They are organized as
   `Table_Dependents[0].RowN[0].DependentM[0].cK_NN[P]`. The pair
   `cK_NN[0]` / `cK_NN[1]` indexes the two checkbox choices (CTC vs.
   ODC) for the same dependent. Any future wave adding a dependents
   payload to Layer 1 must iterate these with matching `(row, col)`
   indices.

3. **Conditional fields.** Many fields are semantically conditional —
   e.g. the former-spouse SSN box (`f2_22` / `SSN_ReadOrder[0].f2_22[0]`)
   only needs to be filled if the 2025 estimated tax payments were
   made jointly with a former spouse. The AcroForm has no way to
   express this conditionality; callers must decide whether to leave
   the widget blank or fill it. This research file does not mark
   conditional fields explicitly — use the IRS instructions PDF if
   you need to know when to fill a given widget.

4. **No widget for "filing status" as a single field.** The filing
   status is a radio / checkbox *group* with one checked box. Layer 1
   stores the string status; the mapping uses a special
   `filing_status_checkboxes` section keyed by
   SINGLE / MFJ / MFS / HOH / QSS so the caller can toggle the right
   one. Exactly one should be checked; runtime validation is the
   caller's responsibility.

5. **PDF coordinate vs. pdfplumber coordinate conversion.** PDF rect
   coordinates are in points, bottom-left origin. pdfplumber word
   positions are in points, top-left origin. Convert with
   `pdfplumber_top = page_height - pdf_rect_top` where `page_height`
   is 792 for US Letter.

6. **`get_fields()` is lossy.** See step 2 — use `/Annots` walking
   instead.

## Repeating for Schedule A / B / C / SE

The same approach applies unchanged. Update:

1. **URL.** Use the canonical IRS-hosted URL:
   * Schedule A: <https://www.irs.gov/pub/irs-pdf/f1040sa.pdf>
   * Schedule B: <https://www.irs.gov/pub/irs-pdf/f1040sb.pdf>
   * Schedule C: <https://www.irs.gov/pub/irs-pdf/f1040sc.pdf>
   * Schedule SE: <https://www.irs.gov/pub/irs-pdf/f1040sse.pdf>
2. **Output file names.** Mirror the naming convention:
   * `skill/reference/schedule-a-acroform-map.json` + `schedule-a-acroform-methodology.md`
   * `skill/reference/schedule-b-acroform-map.json` + `schedule-b-acroform-methodology.md`
   * `skill/reference/schedule-c-acroform-map.json` + `schedule-c-acroform-methodology.md`
   * `skill/reference/schedule-se-acroform-map.json` + `schedule-se-acroform-methodology.md`
3. **Layer 1 dataclass.** Each schedule has (or will have) its own
   Layer 1 dataclass in `skill/scripts/output/` — map those field names
   to the widget names.
4. **Test file.** Copy `skill/tests/test_reference_form_1040_acroform_map.py`
   and change the JSON path, dataclass import, and class name.
5. **Store the SHA-256.** Always record the source PDF hash so future
   mapping runs can detect silent IRS re-issues.

## Handling TY2026 and future years

When the TY2026 Form 1040 PDF is published (typically November–December):

1. Re-fetch the PDF. Note the new SHA-256 and update the JSON.
2. Re-run the widget enumeration. Widget names are generally stable
   year-over-year in IRS-published fillable PDFs (the Adobe Designer
   counter increments in predictable chunks), but **do not assume
   identity**. A single renumbered line (like TY2024 → TY2025's line 1
   additions for OBBBA tip/Medicaid rows) will shift every widget
   number below it.
3. Diff the two years' widget enumerations by visual position (not by
   name), then port the line-to-widget mapping.
4. If the IRS changes the AcroForm structure (e.g. replaces
   `Table_Dependents` with a different container), update the
   categorization heuristics in `unmapped_widgets` and the mirrored-
   checkbox logic.

## Citations

* IRS Form 1040: <https://www.irs.gov/pub/irs-pdf/f1040.pdf>
* IRS Form 1040 instructions (for line semantics):
  <https://www.irs.gov/pub/irs-pdf/i1040gi.pdf>
* pypdf documentation: `PdfReader.get_fields()` and `/Annots` walking
  — <https://pypdf.readthedocs.io/en/stable/user/forms.html>
* pdfplumber documentation: <https://github.com/jsvine/pdfplumber>
* Adobe Designer AcroForm widget-naming convention (no official IRS
  reference exists; inferred from repeated inspection of IRS PDFs
  from TY2018 onward).
