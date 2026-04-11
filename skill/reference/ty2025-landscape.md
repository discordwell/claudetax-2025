# US Individual Tax Filing Reference (TY2025, Filing Season 2026)

Research compiled 2026-04-10. This is the canonical reference doc for the tax-prep skill. Every numeric figure and policy claim has an inline source URL. Update the "Verified" line below whenever you re-check any section.

**Verified:** 2026-04-10

---

## 1. Filing channels for individuals in TY2025

### 1a. IRS Direct File — DEAD for TY2025

Direct File is **gone**. On November 3, 2025, IRS product manager Cindy Noe notified state revenue departments via email that "IRS Direct File will not be available in Filing Season 2026. No launch date has been set for the future." Treasury cited low participation (~296,531 users in FS2025, <0.5% of all returns) and a cost of ~$138 per return. ([Federal News Network](https://federalnewsnetwork.com/it-modernization/2025/11/irs-direct-file-will-not-be-available-in-2026-agency-tells-states/), [Tax Notes](https://www.taxnotes.com/featured-news/irs-shutters-direct-file-citing-cost-and-low-uptake/2025/11/05/7t7q0), [Money](https://money.com/irs-direct-file-end-2026/), [The Tax Adviser](https://www.thetaxadviser.com/news/2025/nov/irs-ends-direct-file-shifts-focus-to-free-file-upgrades-and-private-sector/))

Even when it was operating in FS2025, Direct File never supported Schedule C, Schedule E, or multi-state filers; it was limited to simple W-2/1099-INT returns in a single participating state. Not a viable channel for the target profile regardless.

### 1b. IRS Free File (Free File Alliance) — Operating, AGI ≤ $89,000

Free File operates for TY2025 through **eight** private-sector partners. AGI limit is **$89,000** (up $5,000 from the prior year). IRS states "more than 70% of taxpayers qualify." Each partner sets additional eligibility rules. ([IRS press release](https://www.irs.gov/newsroom/use-irs-free-file-to-conveniently-file-your-return-at-no-cost), [IRS 2026 opens](https://www.irs.gov/newsroom/2026-tax-filing-season-opens-with-several-free-filing-options-available), [Money](https://money.com/irs-free-file-income-limit-2026/))

Relevance: the typical Schedule C + Schedule E + multi-state filer is likely above $89,000 AGI and likely excluded by partner-specific rules. Not the channel to target.

### 1c. Free File Fillable Forms (FFFF) — ALIVE for TY2025

FFFF **is available** for the 2026 filing season. Opened January 26, 2026, **no income limit**, IRS-backed electronic forms. ([IRS 2026 opens](https://www.irs.gov/newsroom/2026-tax-filing-season-opens-with-several-free-filing-options-available), [Kiplinger](https://www.kiplinger.com/taxes/a-free-tax-filing-option-just-disappeared))

Hard limitations relevant to our profile ([IRS FFFF program limitations](https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms)):
- **Schedule C**: supported; multiple copies allowed. Form 4562 (depreciation) must be added from within Schedule C, not the main menu; additional 4562s require another Schedule C.
- **Schedule E**: supported, but page 2 + 10 additional pages — i.e. up to **11 properties** on a single return.
- **W-2**: up to 50 forms.
- **No state returns — none, ever.** "Fillable Forms only handle federal entries and do not produce any state return."
- **No document attachments** beyond forms available in the program. No PDF statements, no election statements, no explanatory statements.
- Form 1040-SR not supported (use 1040 instead).
- Forms 1098-C, 8915-C, 8915-D not supported.
- Form 8889 (HSA) forces paper filing in specific edge cases.

### 1d. Realistic path for Schedule C + Schedule E + multi-state without commercial software

**There is no clean path.** Options:
1. **FFFF for the federal 1040** (if no paper-only attachments are triggered), then file each state return **separately** via that state's own free e-file system, commercial software, or paper.
2. **Paper-file everything.** Works, but slow.
3. **State DOR free portals** where offered (CA CalFile, NY Free File, etc.) — income caps and profile restrictions apply.
4. **Commercial software.** The only realistic "everything works" path.

For a new tool, the most honest workflow is: compute everything internally → render federal as FFFF-compatible entries (or fill IRS PDFs for paper backup) → render state returns separately per state DOR format.

### 1e. Can a non-ERO individual submit MeF XML directly to IRS?

**No.** MeF submission requires an active IRS e-file provider account (EFIN) and at least one of the ERO / Transmitter / Software Developer roles. EFIN application requires a suitability check. EROs must file **5+ returns per season** to remain eligible. Becoming a Transmitter requires ATS (Assurance Testing System) testing. A taxpayer filing only their own return cannot obtain these credentials. ([IRS EFIN FAQs](https://www.irs.gov/e-file-providers/faqs-about-electronic-filing-identification-numbers-efin), [IRS Become an Authorized e-file Provider](https://www.irs.gov/e-file-providers/become-an-authorized-e-file-provider), [IRS e-file Application Pub 3112](https://www.irs.gov/pub/irs-pdf/p3112.pdf), [IRS ATS TY2025](https://www.irs.gov/e-file-providers/tax-year-2025-form-1040-series-and-extensions-modernized-e-file-mef-assurance-testing-system-ats-information))

---

## 2. What can and cannot be e-filed (TY2025)

### 2a. Authoritative lists
- **FFFF limitations**: https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms
- **MeF accepted forms & attachments spreadsheet** (xlsx, updated 2/19/2026): https://www.irs.gov/pub/irs-efile/tax-year-2025-recommended-pdf-names-attached-mef-1040-series-extensions-submissions.xlsx
- **MeF 1040 schema & business rules**: https://www.irs.gov/tax-professionals/tax-year-2025-modernized-e-file-schema-and-business-rules-for-individual-tax-returns-and-extensions
- **Form 8453 (paper transmittal for required attachments)**: https://www.irs.gov/forms-pubs/about-form-8453

Exclusions that matter for our target profile:
- **Form 1040-X** for tax years older than current + 2 prior years must be paper-filed.
- Prior-year amended returns where the original was filed on paper must be paper-filed.
- **Form 1098-C** (vehicle donation over $500) cannot be e-filed in FFFF; must use Form 8453.
- HSA Form 8889 edge cases force paper.
- FFFF-specific: >50 W-2s, >11 Schedule E properties.

### 2b. Elections and statements that force paper

Anything requiring a free-form signed statement is problematic in FFFF (no attachments). Via commercial software / ERO the rule is narrower: MeF schema supports "general dependency" PDF attachments for most elections. Examples commonly paper-only in FFFF:
- §1.263(a) capitalization election statements.
- §754 partnership step-up elections (passes through).
- §6013(g)/(h) nonresident-spouse election statements.
- **Form 4547** (new Trump Account Election under OBBBA) — per IRS 1040-X instructions, file with the 2025 return, do not attach to 1040-X. ([IRS 1040-X instructions 12/2025](https://www.irs.gov/instructions/i1040x))

Rule of thumb: if a real transmitter can attach it as a general dependency PDF, it e-files. FFFF has a substantially smaller allowed surface.

### 2c. Form 1040-X e-file (TY2025)

Yes — 1040-X is e-fileable for the current year and two prior years (FS2026: TY2023/2024/2025). Conditions: original return must have been e-filed; some transmitters require the original to have been e-filed with that same transmitter. Form 8879 authorization required. Form 1040-X rev. December 2025 is current. ([IRS amended return FAQ](https://www.irs.gov/filing/file-an-amended-return), [IRS about 1040-X](https://www.irs.gov/forms-pubs/about-form-1040x))

---

## 3. IRS MeF Schemas for TY2025

### 3a. URLs
- Main: https://www.irs.gov/e-file-providers/modernized-e-file-mef-schemas-and-business-rules
- Individual 1040 landing: https://www.irs.gov/e-file-providers/modernized-e-file-schema-and-business-rules-for-individual-tax-returns-and-extensions
- TY2025 1040 schema page: https://www.irs.gov/tax-professionals/tax-year-2025-modernized-e-file-schema-and-business-rules-for-individual-tax-returns-and-extensions
- v5.2 release memo: https://www.irs.gov/e-file-providers/release-memo-for-tax-year-2025-modernized-e-file-schema-and-business-rules-for-individual-tax-returns-version-5-point-2

Current versions: **1040 series 2025v5.2** (released 2/19/2026), **4868 2025v4.0**, **2350 2025v4.0** (both 11/6/2025).

### 3b. Are schemas free to download? Registration?

**Yes registration is required.** Schemas are distributed via the IRS **Secure Object Repository (SOR)**, accessed through an active e-Services account. You cannot anonymously download XSDs from irs.gov. Business rules are published as PDF and CSV via the same portal. ([IRS MeF schemas landing](https://www.irs.gov/e-file-providers/modernized-e-file-mef-schemas-and-business-rules))

### 3c. Envelope structure

MeF submissions wrap each return as a ZIP-packaged **SubmissionArchive**. XML payload is a **Return** element with two children: **ReturnHeader** (taxpayer identity, signature, preparer info, tax period, software ID, binary attachment list) and **ReturnData** (forms and schedules: IRS1040, IRS1040ScheduleA, IRS1040ScheduleC, etc., plus any binary attachments). Attached PDFs are packaged in the archive and referenced via `AttachmentInformation`. Outer SOAP transmission wraps one or more SubmissionArchives. Authoritative structural reference: **Publication 4164** ([MeF user guides](https://www.irs.gov/e-file-providers/modernized-e-file-mef-user-guides-and-publications)).

---

## 4. TXF (Tax Exchange Format)

### 4a. Current spec

TXF is ASCII, circa 1991, originally Intuit. Authoritative public spec is **v042**, mirrored at https://taxdataexchange.org/docs/txf/v042/txf-spec.html. Intuit's own URL (turbotax.intuit.com/txf) is no longer reachable. No active maintainer. GitHub gist copy: https://gist.github.com/gnagel/c152a63dd6be57ac1a8e6ad5001edd18

### 4b. Does TXF carry a full return?

**No.** Line-item import format — primarily carries brokerage Form 8949 / 1099-B transactions, some 1099-DIV boxes, W-2 wages, Schedule C/E line items. Uses "reference numbers" (e.g., N321 for short-term covered sales). Does NOT represent calculated values, AGI, taxable income, or the full 1040. **Cannot rehydrate a prior-year return; only reloads underlying inputs for specific line items.**

### 4c. Current exporters

- **TurboTax (desktop)**: imports/exports TXF.
- **H&R Block (desktop, formerly TaxCut)**: historically imports TXF.
- **FreeTaxUSA**: does not support TXF (CSV/1099 import only).
- **Brokerages**: Interactive Brokers, TD/Schwab, Fidelity (via GainsKeeper) historically offered TXF 1099-B downloads; usage declining in favor of OFX and proprietary CSVs.

Treat TXF as an input format for legacy brokerage 1099-B downloads; do not rely on it to serialize a full return.

---

## 5. PDF forms and programmatic fill

### 5a. Canonical IRS PDFs

Source: https://www.irs.gov/forms-instructions-and-publications. Direct per-form URLs: `https://www.irs.gov/pub/irs-pdf/f1040.pdf`, `f1040sc.pdf` (Schedule C), `f1040se.pdf` (Schedule E), `f8949.pdf`, etc. TY2025 1040 instructions (Catalog 24811V) dated Feb 25, 2026: https://www.irs.gov/pub/irs-pdf/i1040gi.pdf

### 5b. Are they real AcroForms?

**Yes, most main IRS forms are AcroForms**, not flattened. Can be opened in Reader, typed into, saved. But:
- Field names are IRS-internal and **not publicly documented** — must introspect each PDF.
- Some IRS PDFs use **XFA (dynamic) layers** — pdfrw and older pypdf can't handle those cleanly. Most 1040-series are static AcroForms and work.
- **Calculated fields** use embedded JavaScript; programmatically filled values leave computed fields blank unless you set `/NeedAppearances true` and pre-compute, or open in Acrobat to let scripts run.

### 5c. Python libraries that work on IRS PDFs

- **pypdf** (successor to PyPDF2) — current, actively maintained. `update_page_form_field_values()` works on most IRS AcroForms. **Primary choice.**
- **pdfrw** — most commonly used historically for IRS forms. Pattern: enumerate annotations, set widgets, set `/NeedAppearances true`. Works on static AcroForm 1040-series. ([pdf-form-filler reference](https://github.com/WestHealth/pdf-form-filler))
- **PyPDFForm** — higher-level, inspects fields, fills via dict. Good DX. https://github.com/chinapandaman/PyPDFForm
- **pdftk** (CLI) — shell out, FDF-based. Requires native binary.
- **reportlab + pdfrw merge** — overlay text on background for flattened/widgetless forms.
- **opentaxforms** (PyPI) — IRS-specific but dated.

**Recommendation**: pypdf primary, pdfrw + reportlab overlay for edge cases, always `flatten()` before producing the final PDF.

---

## 6. Existing open-source tax calculation engines

### 6a. OpenTaxSolver (OTS)

Maintained. Maintainer: Aston Roberts. License: **GPL**. TY2025 release posted (homepage updated Feb 20, 2026). https://opentaxsolver.sourceforge.net/

Forms supported for TY2025: **Federal 1040** with Schedules A, B, C, D (via 8949), SE, plus **Forms 2210, 8606, 8812, 8829, 8889 (HSA), 8959, 8960, 8995 (QBI simplified)**. **States**: AZ, CA, MA, MI, NC, NJ, NY, OH, PA, VA (MI contributed new for 2025). **Does not produce e-file XML; fills PDFs + worksheet.**

### 6b. Other open-source calculators

- **tenforty** (Python, MIT): wraps OTS in a Cython layer with pandas-friendly API. Supports TY2018–TY2025 federal + AZ/CA/MA/MI/NC/NJ/NY/OH/OR/PA/VA. Best for scenario modeling and tests. https://github.com/mmacpherson/tenforty
- **HabuTax** (Python, open source): federal 1040 + supporting forms, form-graph/solver architecture, actively developed. https://github.com/habutax/habutax
- **PSL Tax-Calculator** (Python, microsimulation): federal individual + payroll microsim. Policy research, not individual filing, but has well-tested parameter files. https://github.com/PSLmodels/Tax-Calculator
- **py1040** (educational): https://github.com/b-k/py1040

### 6c. Machine-readable parameter data

Best sources for TY2025 inflation-adjusted parameters in structured form:
- **PSL Tax-Calculator `policy_current_law.json`** — comprehensive JSON keyed by year. Best source to copy from.
- **IRS Rev. Proc. 2024-40** (official TY2025 adjustments): https://www.irs.gov/pub/irs-drop/rp-24-40.pdf
- **IRS "Inflation-adjusted tax items by tax year"**: https://www.irs.gov/newsroom/inflation-adjusted-tax-items-by-tax-year
- **tenforty** embeds OTS's numeric constants.

---

## 7. TY2025 key numbers

**Critical caveats:**
1. **Rev. Proc. 2024-40** (October 2024) set initial TY2025 inflation adjustments.
2. **One Big Beautiful Bill Act (OBBBA, P.L. 119-21)**, signed July 4, 2025, **retroactively bumped several TY2025 numbers**. Standard deduction raised above Rev. Proc. figures by $750/$1,125/$1,500 for S/HoH/MFJ. **Use OBBBA-adjusted TY2025 figures.** ([IRS OBBBA provisions](https://www.irs.gov/newsroom/one-big-beautiful-bill-provisions), [Tax Foundation OBBBA FAQ](https://taxfoundation.org/research/all/federal/one-big-beautiful-bill-act-tax-changes/))

### Standard deduction (TY2025, OBBBA-adjusted)
- Single / MFS: **$15,750**
- MFJ / QSS: **$31,500**
- HoH: **$23,625**
- Additional age-65/blind: $1,600 (MFJ/QSS/MFS), $2,000 (Single/HoH)
- **NEW senior deduction**: +$6,000 per filer age 65+ (2025–2028), phase-out begins $75k single / $150k MFJ

Sources: [IRS OBBBA deductions](https://www.irs.gov/newsroom/one-big-beautiful-bill-act-tax-deductions-for-working-americans-and-seniors), [H&R Block OBBBA analysis](https://www.hrblock.com/tax-center/irs/tax-law-and-policy/one-big-beautiful-bill-taxes/)

### Federal ordinary-income brackets (TY2025)
Sources: [IRS Rev. Proc. 2024-40](https://www.irs.gov/pub/irs-drop/rp-24-40.pdf); [IRS inflation adjustments](https://www.irs.gov/newsroom/irs-releases-tax-inflation-adjustments-for-tax-year-2025)

**Single**: 10% ≤ $11,925; 12% ≤ $48,475; 22% ≤ $103,350; 24% ≤ $197,300; 32% ≤ $250,525; 35% ≤ $626,350; 37% >

**MFJ / QSS**: 10% ≤ $23,850; 12% ≤ $96,950; 22% ≤ $206,700; 24% ≤ $394,600; 32% ≤ $501,050; 35% ≤ $751,600; 37% >

**MFS**: 10% ≤ $11,925; 12% ≤ $48,475; 22% ≤ $103,350; 24% ≤ $197,300; 32% ≤ $250,525; 35% ≤ $375,800; 37% >

**HoH**: 10% ≤ $17,000; 12% ≤ $64,850; 22% ≤ $103,350; 24% ≤ $197,300; 32% ≤ $250,500; 35% ≤ $626,350; 37% >

### LTCG / qualified dividend brackets (TY2025)

| Rate | Single | MFJ | HoH | MFS |
|------|--------|-----|-----|-----|
| 0% | ≤ $48,350 | ≤ $96,700 | ≤ $64,750 | ≤ $48,350 |
| 15% | ≤ $533,400 | ≤ $600,050 | ≤ $566,700 | ≤ $300,000 |
| 20% | > $533,400 | > $600,050 | > $566,700 | > $300,000 |

### Social Security / Medicare
- **SS wage base 2025: $176,100** ([SSA COLA](https://www.ssa.gov/oact/cola/cbb.html))
- SS rate: 6.2% EE + 6.2% ER (12.4% SE)
- Medicare rate: 1.45% + 1.45% (2.9% SE), no cap
- **Additional Medicare Tax**: 0.9% on wages/SE > $200k single / $250k MFJ / $125k MFS (not indexed)
- **NIIT**: 3.8% on lesser of NII or (MAGI − threshold). Thresholds: $200k S/HoH, $250k MFJ, $125k MFS (not indexed)

### Schedule SE
- Rate: 15.3% (12.4% SS on SE earnings up to $176,100 + 2.9% Medicare on all SE earnings)
- Filing floor: **$400** net SE earnings
- Deduction for ½ SE tax: above-the-line

### QBI deduction (Section 199A, TY2025)
- Rate: 20% of QBI
- Threshold (full deduction below, phase-in above): **$197,300 single / $394,600 MFJ**
- Full phase-in complete at: **$247,300 single / $494,600 MFJ**
- Form 8995 (simplified) if taxable income ≤ threshold; else Form 8995-A
- **OBBBA made QBI permanent** past its scheduled 12/31/2025 sunset

### Child Tax Credit (TY2025, OBBBA-adjusted)
- **$2,200 per qualifying child** (raised by OBBBA, now indexed)
- Refundable (ACTC) portion: **up to $1,700**
- Phase-out: $200k single/HoH/MFS, $400k MFJ (unchanged, not indexed)

### EITC (TY2025)

| Qualifying Children | Max Credit | AGI (S/HoH) | AGI (MFJ) |
|---|---|---|---|
| 0 | $649 | $19,104 | $26,214 |
| 1 | $4,328 | $50,434 | $57,554 |
| 2 | $7,152 | $57,310 | $64,430 |
| 3+ | $8,046 | $61,555 | $68,675 |

Investment income disqualifier: **$11,950**

### Retirement contribution limits (TY2025)
- **401(k)/403(b)/457/TSP elective deferral**: $23,500
- **401(k) catch-up age 50+**: $7,500
- **NEW "super catch-up" ages 60–63** (SECURE 2.0 §109): $11,250 (replaces regular catch-up for that age band, effective 2025)
- **IRA (Trad + Roth combined)**: $7,000; age 50+ catch-up: $1,000
- **SEP-IRA**: lesser of 25% compensation or $70,000
- **Solo 401(k) total additions**: $70,000 (+ catch-ups)
- **SIMPLE IRA elective deferral**: $16,500; age 50+ catch-up: $3,500; ages 60–63 super catch-up: $5,250
- **HSA self-only**: $4,300; family: $8,550; age 55+ catch-up: $1,000
- **DB §415 limit**: $280,000
- **HDHP min deductible**: $1,650 self / $3,300 family; max OOP: $8,300 self / $16,600 family

Sources: [IRS COLA page](https://www.irs.gov/retirement-plans/cola-increases-for-dollar-limitations-on-benefits-and-contributions), [Rev. Proc. 2024-25 (HSA)](https://www.irs.gov/pub/irs-drop/rp-24-25.pdf)

---

## 8. Multi-state filing

### 8a. State MeF: Fed/State program

IRS MeF supports a **Fed/State program**: transmitters submit fed + state through a single gateway; IRS routes state returns to state DOR. **States have their own schemas**, not subsets of federal. Each state publishes its own form XSDs and business rules. Submission options:
- **Linked (piggyback)**: state attaches to federal; fed reject = state reject.
- **Unlinked**: state standalone via MeF.
- **State-only**: states requiring a dedicated path.

Sources: [IRS MeF Program Overview](https://www.irs.gov/e-file-providers/modernized-e-file-mef-program-overview), [Intuit ProConnect on piggyback vs standalone](https://accountants.intuit.com/support/en-us/help-article/electronic-filing/state-e-file-methods-standalone-vs-piggyback/L11dxp3xr_US_en_US), [IRS Pub 5830](https://www.irs.gov/e-file-providers/modernized-e-file-mef-user-guides-and-publications)

State schemas distributed via [statemef.com](https://statemef.com) and state DOR developer portals. Most require developer registration.

Do not confuse with **Combined Federal/State Filing (CF/SF)**, which is for 1099 info returns, not 1040s.

### 8b. State "free fillable forms"-style options

No federated FFFF equivalent for states. Coverage varies:
- **CalFile** (CA): free online filing, eligibility limits. https://www.ftb.ca.gov/
- **NY Free File**: if federal AGI ≤ $89k, through partner software. https://www.tax.ny.gov/pit/efile/
- Most states: link to commercial free-file partners and/or a DOR web portal.

No individual income tax (irrelevant): AK, FL, NV, NH (wages; I/D tax phased out after TY2024), SD, TN, TX, WA (WA has capital gains tax), WY.

### 8c. State reciprocity agreements

Source: [Tax Foundation State Reciprocity](https://taxfoundation.org/research/all/state/state-reciprocity-agreements/)

16 states + DC with at least one agreement (30 total). Resident of A working in B under reciprocity pays only home state and files an exemption form with employer.

**Key pairs:**
- **DC**: reciprocity with every state
- **Illinois**: IA, KY, MI, WI
- **Indiana**: KY, MI, OH, PA, WI (unilateral)
- **Iowa**: IL
- **Kentucky**: IL, IN, MI, OH, VA, WI, WV
- **Maryland**: DC, PA, VA, WV
- **Michigan**: IL, IN, KY, MN, OH, WI
- **Minnesota**: MI, ND
- **Montana**: ND
- **New Jersey**: PA
- **North Dakota**: MN, MT
- **Ohio**: IN, KY, MI, PA, WV
- **Pennsylvania**: IN, MD, NJ, OH, VA, WV
- **Virginia**: DC, KY, MD, PA, WV
- **West Virginia**: KY, MD, OH, PA, VA
- **Wisconsin**: IL, IN, KY, MI

**No reciprocity**: NY, CA, MA. Crossing those borders = nonresident return in work state + resident credit in home state.

---

## 9. PDF parsing of prior returns

### 9a. Most reliable libraries/tools for 1040 PDFs

IRS-generated fillable 1040 PDFs retain text layers — no OCR needed. Returns printed from commercial software usually retain text too. Scanned/photographed returns require OCR.

**Non-OCR (text extraction, form-aware)**:
- **pdfplumber** — best for coordinate-aware extraction. Good for tabular schedules (D, E, 8949).
- **pypdf** — if AcroForm with filled values, `reader.get_fields()` returns names + values directly. Most reliable when it works (someone filled the real IRS form).
- **PyMuPDF (fitz)** — fast, good spatial extraction, decent table detection.
- **pdfminer.six** — underpins pdfplumber.

**OCR**:
- **Azure AI Document Intelligence "Unified US Tax" prebuilt model** (v4.0 GA Nov 2024) — specifically classifies W-2, 1098, 1099, **and 1040** fields. https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/tax-document
- **Amazon Textract** + `AnalyzeExpense` / tax classifier.
- **Google Document AI** — tax-specific processors.
- **olmOCR** (AllenAI, open source): https://olmocr.allenai.org/
- **Tesseract + pdfplumber layout** — free fallback.

### 9b. Existing "1040 PDF → structured data" tools

- **Azure Document Intelligence Unified US Tax model** — only mainstream turnkey product trained on IRS 1040. Returns structured JSON with labeled fields. Closest to "just works."
- **Amazon Textract** (general forms) — needs post-processing to map to 1040 lines.
- **opentaxforms** (PyPI) — hobby project; extracts field structure, not production-ready for prior-return ingestion.
- **Camelot / Tabula** — tables only; useful for Schedule D / Form 8949 rows.

**No mature open-source "1040 PDF → JSON of filed return" tool exists as of April 2026.** Plan to build one: Azure Document Intelligence for OCR cases, pdfplumber + pypdf for text-layer cases, template library keyed on form revision.

---

## 10. Surprises / gotchas for TY2025

### 10a. OBBBA (P.L. 119-21, signed July 4, 2025)

Biggest thing to know:
- Made TCJA individual brackets, standard deduction, personal-exemption elimination, and CTC structure **permanent** past the original 2025 sunset.
- Retroactively raised TY2025 standard deduction to **$15,750 / $31,500 / $23,625**.
- Raised CTC to **$2,200**, indexed for inflation; refundable $1,700.
- New **senior deduction**: +$6,000 per filer age 65+, TY2025–2028, phase-out $75k single / $150k MFJ.
- New temporary deductions for **qualified tips** and **qualified overtime** (TY2025–2028).
- New **Trump Account** deduction and **Form 4547** election.
- Reversed 1099-K threshold (see 10b).
- Made **QBI deduction permanent**.
- SALT cap adjustments (details vary by provision).

Sources: [IRS OBBBA provisions](https://www.irs.gov/newsroom/one-big-beautiful-bill-provisions), [IRS OBBBA deductions](https://www.irs.gov/newsroom/one-big-beautiful-bill-act-tax-deductions-for-working-americans-and-seniors), [Wolters Kluwer OBBBA summary](https://www.wolterskluwer.com/en/expert-insights/2025-tax-law-changes-key-obbba-updates-for-preparers), [Tax Foundation OBBBA FAQ](https://taxfoundation.org/research/all/federal/one-big-beautiful-bill-act-tax-changes/)

### 10b. 1099-K threshold reverted

OBBBA **retroactively reinstated** the pre-ARPA threshold: **$20,000 AND 200 transactions**, applicable to TY2025 and forward. Reverses the IRS's previously-announced $2,500 transitional threshold for 2025.

Tool implications:
- Taxpayers may receive 1099-Ks issued under old $2,500 guidance that are no longer "required." Income still taxable but forms may not match IRS records.
- Several **states still have lower thresholds**: MA/MD/VA use $600, NJ uses $1,000, IL/VT vary. Multi-state tool must track state-specific 1099-K thresholds.

Source: [IRS 1099-K OBBBA FAQs](https://www.irs.gov/newsroom/irs-issues-faqs-on-form-1099-k-threshold-under-the-one-big-beautiful-bill-dollar-limit-reverts-to-20000), [IRS FS-2025-08](https://www.irs.gov/pub/taxpros/fs-2025-08.pdf)

### 10c. SECURE 2.0 provisions effective 2025

- **Ages 60–63 "super catch-up"** (§109): $11,250 for 401(k)/403(b)/governmental 457; $5,250 for SIMPLE. **Effective 1/1/2025.**
- **Mandatory Roth catch-up for high earners** (§603): Participants whose prior-year FICA wages > $145,000 must make catch-ups as Roth only. Final regs Sept 16, 2025. **Effective 1/1/2026** (good-faith compliance from that date; full regs 2027).
- **Auto-enrollment mandate** (§101) for 401(k)/403(b) plans established after 12/29/2022 begins **2025**.
- **Starter 401(k)s** available 2025.
- **529 → Roth IRA rollovers** (§126): available 2024+, lifetime cap $35,000.

Sources: [IRS 401k 2026 announcement](https://www.irs.gov/newsroom/401k-limit-increases-to-24500-for-2026-ira-limit-increases-to-7500), [IRS final regs press release](https://www.irs.gov/newsroom/treasury-irs-issue-final-regulations-on-new-roth-catch-up-rule-other-secure-2point0-act-provisions)

### 10d. Direct File killed

Covered above; any doc still recommending Direct File is stale. The IRS 2026 filing-season page conspicuously doesn't mention it.

### 10e. New forms

- **Form 4547** — Trump Account Election (OBBBA, new TY2025). Filed with return, not 1040-X.
- **Revised Form 1040-X** — rev. December 2025, accommodates OBBBA.
- **Schedule 1-A** (tips/overtime deductions) — new schedule for OBBBA temporary deductions; check [IRS draft forms](https://www.irs.gov/draft-tax-forms) for final version.

### 10f. QBI saved by OBBBA

Under prior law, §199A QBI was scheduled to expire 12/31/2025. OBBBA made it permanent. "Last year for QBI" assumptions are wrong.

### 10g. Schema version churn

1040 MeF schema already on **2025v5.2** as of 2/19/2026 — mid-season update. Expect another minor version before 4/15. Any tool emitting MeF must pin schema version and track release memos.

### 10h. PTIN / preparer mandate

If the skill is used to prepare returns for compensation for other people, preparer must have a **PTIN**. Since 1/1/2024, specified tax return preparers filing 10+ returns per calendar year must e-file (the "e-file mandate"). [IRS e-file requirements FAQ](https://www.irs.gov/e-file-providers/frequently-asked-questions-e-file-requirements-for-specified-tax-return-preparers-sometimes-referred-to-as-the-e-file-mandate). A tool that "prepares" a return for someone to self-file does not trigger PTIN; tools that submit on users' behalf would.

### 10i. Key deadlines

- **Federal 1040**: April 15, 2026
- **Extension (Form 4868)**: April 15, 2026 (6-month extension, payment still due 4/15)
- **Q1 2026 estimated tax**: April 15, 2026
- **1040-X refund window**: 3 years from original filing or 2 years from tax paid, whichever later
- **FBAR (FinCEN 114)**: April 15, 2026 (automatic 6-month extension to October 15)
- **Form 8938**: attaches to 1040 (no separate deadline)

---

## Appendix: Primary-source bookmarks

- IRS Forms & Pubs: https://www.irs.gov/forms-instructions-and-publications
- Rev. Proc. 2024-40: https://www.irs.gov/pub/irs-drop/rp-24-40.pdf
- OBBBA provisions: https://www.irs.gov/newsroom/one-big-beautiful-bill-provisions
- MeF 1040 TY2025 schema: https://www.irs.gov/tax-professionals/tax-year-2025-modernized-e-file-schema-and-business-rules-for-individual-tax-returns-and-extensions
- MeF schemas main: https://www.irs.gov/e-file-providers/modernized-e-file-mef-schemas-and-business-rules
- MeF user guides (Pub 4164): https://www.irs.gov/e-file-providers/modernized-e-file-mef-user-guides-and-publications
- FFFF limitations: https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms
- Form 8453: https://www.irs.gov/forms-pubs/about-form-8453
- 1040-X instructions (12/2025): https://www.irs.gov/instructions/i1040x
- 1040 instructions (2025): https://www.irs.gov/pub/irs-pdf/i1040gi.pdf
- IRS COLA (retirement): https://www.irs.gov/retirement-plans/cola-increases-for-dollar-limitations-on-benefits-and-contributions
- SSA wage base: https://www.ssa.gov/oact/cola/cbb.html
- StateMeF: https://statemef.com
- PSL Tax-Calculator: https://github.com/PSLmodels/Tax-Calculator
- OpenTaxSolver: https://opentaxsolver.sourceforge.net/
- tenforty: https://github.com/mmacpherson/tenforty
- HabuTax: https://github.com/habutax/habutax
- TXF v042 spec: https://taxdataexchange.org/docs/txf/v042/txf-spec.html

---

## Bottom-line architectural implications

1. **No free federal e-file path for Sched C + Sched E + multi-state.** Skill prepares data, fills PDFs / FFFF entries, hands off transmission. **No direct MeF attempt.**
2. **Use OBBBA-adjusted TY2025 numbers** (standard deduction, CTC, 1099-K threshold, QBI permanence), not Rev. Proc. 2024-40 alone.
3. **Prior-year rehydration cannot rely on TXF.** Use PDF extraction (Azure Unified US Tax for scans; pypdf for AcroForm-filled; pdfplumber for text-layer).
4. **Calculation engine**: borrow PSL Tax-Calculator parameters; reference HabuTax/tenforty/OTS for form-by-form calc logic; cross-validate against commercial tool on golden returns.
5. **Multi-state**: no unified schema; each state has its own MeF + reciprocity rules. Hard-code a per-state table.
6. **Schema versions churn mid-season**: pin 1040 MeF 2025v5.2; monitor release-memo page.
7. **FFFF is the practical free e-file channel** for this profile, but: ≤50 W-2s, ≤11 Sch E properties, no attachments, no state returns.
