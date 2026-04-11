# Tax Prep Skill — Architecture

Last updated: 2026-04-10

## What this is

A packaged, distributable Claude Code skill that prepares US individual income tax returns for TY2025 and forward. Federal + multi-state. Covers W-2, 1099-INT/DIV/B, Schedule C (self-employment), Schedule E (rental). Produces filled IRS PDFs, Free File Fillable Forms (FFFF) entry tables, per-state artifacts, and a paper-file bundle.

Each user installs the skill and runs it against their own tax data, which lives outside this repo in a user-chosen directory. No taxpayer data is ever committed.

## Why the design looks like this

### Individuals cannot transmit MeF directly

The IRS's Modernized e-File (MeF) transmission channel requires an EFIN — held only by authorized EROs, Transmitters, or Software Developers. Not an individual path. So the skill's "output" is never a transmitted return; it's always a bundle of artifacts the human hands to one of these channels:

- **Free File Fillable Forms (FFFF)** — web-based, no income limit, federal only. Primary free path for our target profile. Hard limits: ≤50 W-2s, ≤11 Schedule E properties, no document attachments, no state returns.
- **Commercial software** (TurboTax, FreeTaxUSA, H&R Block, Cash App Taxes) — for users who want assisted e-file. Skill outputs structured data they can import.
- **State DOR portals** — federal and state are always separate. Each state has its own submission path, and ~16 states have reciprocity agreements the skill must honor.
- **Paper file** — always an option. Skill outputs a signature-ready bundle.

### Hybrid calculation engine

Claude drives the interview (natural language, context-aware, resumable). Deterministic Python scripts do the math (testable, reproducible, year-over-year stable). Reference: [feedback_ai_paced_planning.md and project_tax_prep_skill.md in memory](../.claude/projects/-Users-discordwell-Projects-Taxes/memory/).

We plan to wrap **tenforty** (MIT, Python, TY2018–TY2025, wraps OpenTaxSolver) as the reference calc engine and layer our own code on top for OBBBA adjustments, QBI 8995-A complex path, multi-state apportionment, and new TY2025 forms. The exact strategy is gated on **CP4: tenforty OBBBA verification** — if tenforty's TY2025 numbers are current post-OBBBA we wrap cleanly; if there's a gap we patch on top; if the gap is large we fall back to HabuTax or our own calc code.

### OCR is first-class

Prior-year return ingestion handles three tiers automatically:
1. **pypdf AcroForm extraction** — when the PDF is a real IRS fillable form with widget values. Most reliable when it works.
2. **pdfplumber text-layer** — when the PDF has a text layer (commercial software prints, IRS PDFs viewed but not filled). Coordinate-aware table extraction.
3. **Azure AI Document Intelligence "Unified US Tax" prebuilt model** — for scans, photos, anything without a text layer. Specifically trained on W-2, 1098, 1099, and 1040 fields. Returns structured JSON. This is the only mainstream turnkey 1040-aware extractor as of April 2026.

A document classifier routes a folder of mixed PDFs to the right ingester. OCR is not deferred to a later phase — it's part of CP6.

### State scalability via plugin API

The skill supports "all states" through a plugin interface. Every state implements the same contract:

```python
class StatePlugin(Protocol):
    code: str  # "CA", "NY", ...
    def compute(self, return_: CanonicalReturn) -> StateReturn: ...
    def apportion_multi_state(self, return_: CanonicalReturn, days_in_state: int) -> ...: ...
    def reciprocity_partners(self) -> list[str]: ...
    def form_ids(self) -> list[str]: ...
    def render_pdfs(self, state_return: StateReturn, out_dir: Path) -> list[Path]: ...
    def submission_channel(self) -> SubmissionChannel: ...
```

Once this API is stable (**CP5**), individual state implementations fan out to parallel sub-agents — one agent per state. No state blocks another state, and adding state #44 tomorrow doesn't touch core code.

## Repository layout

```
Taxes/
├── ARCHITECTURE.md                        ← this file
├── claudepad.md                           ← session memory (per user CLAUDE.md)
├── README.md                              ← install + quickstart
├── requirements.txt                       ← pinned deps
├── pyproject.toml                         ← package metadata
├── .gitignore                             ← excludes .venv, __pycache__, user data
└── skill/                                 ← the distributable skill
    ├── SKILL.md                           ← Claude-driven interview flow
    ├── reference/                         ← heavy reference docs (ship with skill)
    │   ├── ty2025-landscape.md            ← canonical TY2025 research doc
    │   ├── ty2025-constants.json          ← OBBBA-adjusted numbers (CP2)
    │   ├── state-reciprocity.json         ← 30 pairs from Tax Foundation
    │   ├── ffff-limits.md                 ← FFFF hard caps
    │   └── forms-paper-only.md            ← paper-only attachments
    ├── schemas/
    │   └── return.schema.json             ← canonical return schema (CP3)
    ├── scripts/
    │   ├── calc/                          ← calculation hot spots (fan-out)
    │   ├── ingest/                        ← PDF / OCR / TXF ingestion (fan-out)
    │   ├── output/                        ← PDF fill, FFFF map, paper bundle (fan-out)
    │   ├── states/                        ← per-state plugins (fan-out x43)
    │   └── validate/                      ← schema + cross-check validation
    ├── fixtures/                          ← golden fixtures with expected output
    └── tests/                             ← pytest harness
```

## Critical path and fan-out

The build is split into a short **serial critical path** that establishes contracts, followed by a large **parallel fan-out** that implements against those contracts with independent sub-agents.

### Serial critical path

1. **CP1** — Scaffold + dependencies (this commit).
2. **CP2** — TY2025 constants module (OBBBA-adjusted).
3. **CP3** — Canonical return JSON schema + Pydantic models.
4. **CP4** — tenforty OBBBA verification → pick calc engine.
5. **CP5** — State plugin API + reciprocity table.
6. **CP6** — Ingestion pipeline interface + OCR cascade.
7. **CP7** — First golden fixture + pytest harness.

### Fan-out

Dispatched in parallel after CP7:

- **~12 calc hot spots** — QBI 8995-A, AMT, Sch SE, Sch E depreciation, NIIT, Add'l Medicare, EITC, CTC/ACTC, LTCG worksheet, 2210, Form 4547, Schedule 1-A.
- **~12 ingesters** — 1040 AcroForm, W-2, 1099-INT/DIV/B/NEC/K, 1098, K-1, TXF reader, Azure DI wrapper, document classifier.
- **~20 output renderers** — pypdf fill per IRS form, FFFF map, FFFF limits checker, paper bundle, MeF draft XML.
- **~6 golden fixtures** — investments+itemized, SE+home office, rental+depreciation, multi-state part-year, AMT stress, SE+rental combo.
- **43 state plugins** — 10 tenforty-backed (AZ/CA/MA/MI/NC/NJ/NY/OH/PA/VA) + 33 from-scratch.
- **Interview, packaging, wet test** — final integration tasks.

## Data handling

Taxpayer data is **never** stored in this repo. User data lives in a user-chosen directory (convention: `~/TaxData/<taxpayer>/tyYYYY/`). Format is canonical JSON validated against `schemas/return.schema.json`. The user owns disk-level security.

## Dependencies

Listed in `requirements.txt`. Notable choices:

- **pypdf** — primary PDF form fill + read. Actively maintained successor to PyPDF2.
- **pdfplumber** — coordinate-aware text extraction for non-AcroForm PDFs.
- **pydantic** — canonical return models with validation.
- **jsonschema** — JSON-only schema validation for non-Python consumers.
- **pytest** — test harness.
- **tenforty** — calc engine wrap target (pending CP4 verification).
- **azure-ai-documentintelligence** — OCR for scanned tax docs.
- **python-dateutil** — robust date parsing from varied input formats.

## References

- Full TY2025 tax landscape research: [skill/reference/ty2025-landscape.md](skill/reference/ty2025-landscape.md)
- Scope decisions: memory entries in `.claude/projects/-Users-discordwell-Projects-Taxes/memory/`
