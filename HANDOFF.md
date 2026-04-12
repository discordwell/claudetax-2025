# Tax Prep Skill — Agent Handoff

**Project**: `/Users/discordwell/Projects/Taxes`
**HEAD**: `cef36a5` on `main` (pushed to `github.com:discordwell/claudetax-2025.git`)
**Suite**: 3605 passed, 3 skipped
**Version**: 0.1.0 (`tax-prep` CLI installable via `pip install -e .`)

---

## What this is

A Claude Code skill that prepares US individual income tax returns (federal + all 50 states + DC) for TY2025. It ingests PDF tax documents, computes the return via a hybrid engine (tenforty/OpenTaxSolver + hand-rolled state plugins), renders filled IRS AcroForm PDFs, and emits a paper-mail bundle + FFFF (Free File Fillable Forms) entry transcript.

## Architecture in 60 seconds

```
PDFs on disk → classifier → ingester cascade → PartialReturn(s)
                                                      ↓
                                       _set_path → nested dict
                                                      ↓
                                             CanonicalReturn (Pydantic)
                                                      ↓
                                           engine.compute() [federal]
                                                      ↓
                                     _dispatch_state_plugins [51 states]
                                                      ↓
                                 render federal PDFs (10 forms)
                                                      ↓
                              paper_bundle.pdf + ffff_entries.txt
                                                      ↓
                                              result.json
```

### Key patterns

- **Two-layer renderer**: Layer 1 = frozen dataclass mapping `CanonicalReturn` fields to form line numbers. Layer 2 = `fill_acroform_pdf()` overlays values onto real IRS PDF via widget map JSON. All 10 federal forms use real IRS AcroForm widgets (no reportlab scaffolds).

- **State plugin protocol**: `StatePlugin` with `meta`, `compute(canonical, federal, residency, days_in_state) → StateReturn`, `apportion_income()`, `render_pdfs()`, `form_ids()`. Three flavors:
  - **Tenforty default backend** (11 states): AZ, CA, MA, MI, NC, NJ, NY, OH, OR, PA, VA
  - **Tenforty graph backend wraps** (10 states): AR, HI, IA, ID, LA, MS, MT, NM, SC, VT
  - **Hand-rolled** (11 states): AL, DE, IN, ME, MO, ND, NE, OK, RI, UT, WV — recover exemptions/credits graph omits
  - **Special** (2): DC (own tax code), WA (capital gains only)
  - **No-income-tax** (8): AK, FL, NV, NH, SD, TN, TX, WY
  - Every plugin pins a `$65k Single` gatekeeper test against DOR primary source

- **PartialReturn path addressing**: Ingesters emit `FieldExtraction` objects with canonical paths (`"w2s[0].box1_wages"`). `_set_path()` builds nested dicts; `_reindex_partial_paths()` offsets indices so multiple PDFs of the same type append instead of clobber. Pydantic validates once at the end.

- **Cascade ingester**: `IngestCascade` runs Tier-1 (pypdf AcroForm) first-wins per file. 9 Tier-1 ingesters: W-2, 1099-INT/DIV/B/NEC/R/G, SSA-1099, Schedule K-1. Seven have real IRS widget maps; 1099-B and SSA-1099 are flattened PDFs (OCR upgrade path documented).

- **OBBBA (P.L. 119-21)** TY2025 adjustments baked into engine: Schedule 1-A tips/overtime, senior deduction, Form 4547 Trump Account (always $0).

### Key files

| File | Role |
|---|---|
| `skill/scripts/pipeline.py` | `run_pipeline()` — the main entry point |
| `skill/scripts/calc/engine.py` | `compute()` — federal tax computation |
| `skill/scripts/models.py` | `CanonicalReturn` + all sub-models (Pydantic strict) |
| `skill/scripts/states/_registry.py` | 51-state plugin registry |
| `skill/scripts/states/_hand_rolled_base.py` | Shared helpers: `graduated_tax`, `day_prorate`, `state_source_wages_from_w2s` |
| `skill/scripts/states/_plugin_api.py` | `StatePlugin` protocol, `FederalTotals`, `StateReturn` |
| `skill/scripts/output/_acroform_overlay.py` | `fill_acroform_pdf()`, `load_widget_map`, `verify_pdf_sha256` |
| `skill/scripts/output/ffff_entry_map.py` | FFFF field-by-field transcript builder |
| `skill/scripts/output/paper_bundle.py` | Paper-mail bundle assembler |
| `skill/scripts/cli.py` | `tax-prep` CLI entry point |
| `skill/scripts/ingest/_pipeline.py` | `IngestCascade`, `PartialReturn`, `DocumentKind` |
| `skill/reference/tenforty-ty2025-gap.md` | Authoritative state plugin decision rubric |
| `skill/reference/ty2025-constants.json` | TY2025 thresholds (SS base, std ded, brackets, AMT, etc.) |
| `skill/SKILL.md` | Claude-facing interview prompt (9 phases, 561 lines) |

### Dependencies

```
pypdf>=5.1,<6          # AcroForm read/write
reportlab>=4.2,<5      # only used for synthetic test PDFs
pydantic>=2.9,<3       # strict models
tenforty>=0.2          # OpenTaxSolver wrapper (NOT version-pinned)
azure-ai-documentintelligence>=1.0,<2  # Tier-3 OCR (optional)
```

---

## What's done (waves 1-6)

### Federal forms (10 compute + render)
Form 1040, Schedule A, B, C, D, SE, Form 4562, 6251, 8829, 8949

### State plugins (51 jurisdictions)
Complete coverage — every US taxing jurisdiction for TY2025. All return `state_total_tax` in a validated `StateReturn.state_specific` dict.

### Ingesters (9 Tier-1)
W-2, 1099-INT/DIV/B/NEC/R/G, SSA-1099, Schedule K-1. Seven map real IRS widget names.

### Pipeline
`run_pipeline` produces: federal PDFs, state returns (compute only — most states don't render PDFs yet), paper_bundle.pdf, ffff_entries.json/txt, result.json.

### CLI
`tax-prep run/schema/version` — friendly error handling, exit code 2 on bad input.

### Testing
3605 tests. Every form, every state, every ingester, pipeline integration, CLI, FFFF validator, paper bundle, schema gate.

---

## What's next — Wave 7 plan

### Serial fixes (do first, ~30 min)

1. **Pin tenforty version** in pyproject.toml to current installed version. The AR low-income credit gatekeeper test ($248.73) and several graph-wrap gatekeeper tests are fragile against tenforty upgrades. Run `pip show tenforty` to get current version, pin as `tenforty==X.Y.Z`.

2. **Form 8829 docstring drift** — `skill/scripts/output/form_8829.py:163` says "we intentionally re-implement the formula here" but actually imports `_sch_c_total_expenses` from engine. Delete the misleading comment.

3. **Graph-wrap docstring cross-refs** — 10 graph-wrap plugins (AR, HI, IA, ID, LA, MS, MT, NM, SC, VT) should each mention `skill/reference/tenforty-ty2025-gap.md` re: `state_taxable_income` echo, `state_tax_bracket=0`, `state_effective_tax_rate=0` output-field gaps. Quick grep-and-append.

### Fan-out wave 7A: Missing federal forms (parallel agents)

These are the most common IRS forms not yet implemented. Each follows the established two-layer renderer pattern.

| # | Form | What it does | Complexity | Notes |
|---|---|---|---|---|
| 7A-1 | **Schedule E** | Rental real estate income/loss | LARGE | Model `ScheduleEProperty` already exists in models.py. Need compute (Part I rental, Part II partnership passthrough from K-1), render, pipeline gate. Up to 3 properties per Schedule E page. |
| 7A-2 | **Form 2441** | Child and dependent care credit | MEDIUM | Needs `DependentCareExpenses` model. Credit = 20-35% of up to $3k (1 qualifying) / $6k (2+). AGI phase-down schedule. Flows to Form 1040 Schedule 3 line 2. |
| 7A-3 | **Form 8863** | Education credits (AOTC + LLC) | MEDIUM | AOTC = up to $2,500/student (40% refundable). LLC = up to $2,000 (nonrefundable). MAGI phase-outs. Flows to Schedule 3 line 3. |
| 7A-4 | **Form 8962** | Premium tax credit (ACA) | MEDIUM | Reconciles advance PTC from Form 1095-A. Can result in additional tax (repayment) or refundable credit. Complex FPL-based percentage table. |
| 7A-5 | **Form 8606** | Nondeductible IRA contributions | SMALL | Tracks basis in traditional IRA. Needed to correctly tax Roth conversions and traditional IRA distributions. |
| 7A-6 | **Schedule 1** | Additional income and adjustments | MEDIUM | Aggregates: alimony, business income (from Sch C), rental (from Sch E), unemployment (1099-G), HSA deduction, student loan interest, SE tax deduction. Some lines already flow through engine.py but no dedicated renderer. |
| 7A-7 | **Schedule 2** | Additional taxes | SMALL | AMT (from 6251), SE tax (from SE), additional Medicare, NIIT. Engine computes these; needs renderer. |
| 7A-8 | **Schedule 3** | Additional credits and payments | SMALL | Foreign tax credit, education credits (8863), child care (2441), retirement savings credit, estimated tax payments. Needs renderer. |

**Priority**: 7A-1 (Schedule E — rental is extremely common), 7A-6/7/8 (Schedules 1-3 are technically required for many returns today but the engine bakes their values directly into Form 1040 lines).

### Fan-out wave 7B: State PDF renderers (parallel agents)

Today 43 state plugins compute real tax but `render_pdfs()` returns `[]`. Each state needs:
1. Fetch the state DOR's fillable PDF (or confirm it's flattened)
2. Build a widget map JSON
3. Implement `render_pdfs()` using `fill_acroform_pdf`
4. Add a render test

This is highly parallelizable — one agent per 5-6 states, same pattern as the federal renderers. Start with the 11 tenforty-default-backend states (AZ, CA, MA, MI, NC, NJ, NY, OH, OR, PA, VA) since they're the most common filer states.

### Fan-out wave 7C: Real nonresident apportionment (parallel agents)

Currently all plugins day-prorate or use W-2 state-row sums. Real per-state sourcing requires:
- CA: Schedule CA-NR (540NR filer) — line-by-line CA-source vs total income
- NY: Form IT-203 with IT-203-B (workday allocation for wages)
- PA: Schedule NRH (nonresident/part-year)
- Each state with a nonresident form gets its own apportionment module

This is lower priority than 7A/7B — the W-2 state-row method is correct for the common case (single-state W-2 filer). Real sourcing matters for self-employed multi-state filers.

### Wave 7D: Distribution and wet test

After 7A-7C merge:
1. Hard wet test of the full pipeline with a complex multi-form return (W-2 + 1099-INT + 1099-DIV + Schedule C + rental property + education credit + multi-state)
2. Update SKILL.md interview prompt to reference new forms
3. Update FFFF entry map to emit Schedule E / 2441 / 8863 / 8962 lines
4. Final version bump to 0.2.0

---

## Running the project

```bash
cd /Users/discordwell/Projects/Taxes
source .venv/bin/activate
pip install -e .

# Run tests
pytest skill/tests/ -x -q

# Run the pipeline
tax-prep run --input ./user_pdfs --taxpayer-info ./taxpayer.json --output ./out

# Print schema
tax-prep schema | python -m json.tool
```

## Agent conventions

- Read `CLAUDE.md` at project root for user preferences
- Read `claudepad.md` for session history (top = newest, bottom = persistent findings)
- Every fix requires a test
- Use `.venv/bin/python` for running tests (system python may lack tenforty)
- Parallel fan-out uses `Agent(isolation: "worktree")` — cherry-pick merge at end
- State plugins follow probe-then-verify-then-decide rubric in `skill/reference/tenforty-ty2025-gap.md`
- After major revisions: code review sub-agent → claudepad update → commit → push
