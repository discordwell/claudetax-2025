# Claudepad — Tax Prep Skill

Session memory for this project. Top section = most recent session summaries (newest first, keep 20 max, overflow to `oldpad.md`). Bottom section = persistent key findings that survive across sessions.

---

## Session Summaries

### 2026-04-10 17:30 UTC — Session 1: scoping, research, plan, scaffold

- **Scoped the skill**: packaged distributable, TY2025 first, federal + all states, W-2 + 1099-INT/DIV/B + Sch C + Sch E, hybrid calc engine, local JSON storage, full golden-fixture tests. Not a real-filer build — deadline pressure off. Each user installs and runs independently.
- **Research agent ran**: full TY2025 landscape document saved at `skill/reference/ty2025-landscape.md`. Biggest findings: **Direct File is DEAD** (killed Nov 2025), **FFFF is alive** (no income limit, hard caps on W-2s/Sch E), **OBBBA retroactively changed TY2025 numbers** (standard deduction, CTC, 1099-K threshold reverted, QBI permanent, new Form 4547 + Schedule 1-A), **MeF schemas require e-Services login** to download, **tenforty** (MIT wrapper of OpenTaxSolver) is the best calc engine candidate, **Azure DI Unified US Tax** is the best OCR.
- **Planned**: split into 7-item serial critical path (scaffold → constants → schema → tenforty verify → state plugin API → ingestion interface → first golden fixture) then fan out ~80 parallel sub-agent tasks (calc hot spots, ingesters, output renderers, fixtures, 43 state plugins).
- **CP1 scaffold** (this commit): directory tree, ARCHITECTURE.md, README, requirements.txt, pyproject.toml, .gitignore, empty package modules, moved research doc into `skill/reference/`.

**CP2 landed** (97 tests): OBBBA-adjusted TY2025 constants in `reference/ty2025-constants.json`, typed loader at `scripts/calc/constants.py`, every number locked to a named source URL. Includes a `_todo` list of numbers not yet researched (AMT exemption, FEIE, education credit phase-outs) so calc modules block on research rather than guessing.

**CP3 landed** (21 tests, 118 total): canonical return schema as Pydantic models at `scripts/models.py`, generated JSON schema committed at `schemas/return.schema.json`, drift detection in tests. Covers taxpayer/spouse/dependents, W-2, 1099-INT/DIV/B/NEC, Schedule C (full Part II expense lines), Schedule E (per-property), adjustments, itemized, credits, other taxes, payments, state returns, carryforwards, computed totals. Strict model with SSN/EIN/state/zip format validation.

**CP4 landed** (11 tests, 129 total): **tenforty is OBBBA-current** on standard deductions ($15,750/$31,500/$23,625), federal brackets (exact match on MFJ $468,500 → $104,046), SE tax, LTCG 0% rate, Additional Medicare Tax, and California state pass-through. **Gap**: `num_dependents` does not trigger CTC — tenforty's high-level API has no child-specific data path, so our patch layer computes CTC ourselves. Full verification doc at `skill/reference/cp4-tenforty-verification.md`. **Architecture decision: wrap + patch** — tenforty for baseline federal + the 10 supported states, our own code for CTC, OBBBA senior deduction, Form 4547, Schedule 1-A, QBI 8995-A, and multi-state apportionment beyond tenforty's 10.

**CP5 landed** (32 tests, 161 total): State plugin Protocol at `scripts/states/_plugin_api.py` with `StatePluginMeta`, `SubmissionChannel`, `FederalTotals`, `IncomeApportionment`, `ReciprocityTable`. Reciprocity table at `reference/state-reciprocity.json` — 30 bilateral pairs loaded and symmetry-verified, DC universal nonresident exemption modeled, 8 no-tax states + WA (capital gains only). Reference implementation: `NoIncomeTaxPlugin` handles AK/FL/NV/NH/SD/TN/TX/WY, all 8 instantiated and registered. Registry at `scripts/states/_registry.py`. The 43 state fan-out agents code against `_plugin_api.py` — keep it stable.

**CP6 landed** (57 tests, 218 total): Ingestion pipeline at `scripts/ingest/_pipeline.py` with `DocumentKind`, `FieldExtraction`, `PartialReturn`, `IngestResult`, `Ingester` Protocol, and `IngestCascade` orchestrator. Classifier at `_classifier.py` with filename + content heuristics (custom alnum boundaries so underscores/hyphens separate). Three tier ingesters: `PyPdfAcroFormIngester` (tier 1, works on synthetic fillable PDF), `PdfPlumberTextIngester` (tier 2, base class), `AzureDocIntelligenceIngester` (tier 3, OCR — gracefully no-ops when credentials absent). OCR is first-class, not deferred.

**CP7 landed** (11 tests, 229 total): First end-to-end golden fixture `simple_w2_standard` — single filer, $65k W-2, standard deduction. Calc engine at `scripts/calc/engine.py` marshals canonical return → tenforty → populates `ComputedTotals`. Hand-computed expected values match exactly: AGI $65,000, OBBBA std ded $15,750, taxable $49,250, fed tax $5,755, refund $1,745, marginal 22%. The pattern every future golden copies from.

**Serial critical path COMPLETE.** Full test suite: 229 passed in 0.54s.

**Repo live** at https://github.com/discordwell/claudetax-2025 (public).

**Code review pass landed** (commit 0d92807, 11 files changed): 6 calc-engine blockers fixed (Sch C net profit, total_payments all categories, itemized + SALT cap, QSS spouse validation, total_income/adjustments_total semantics, adjustments marshaled via schedule_1_income). Interface enrichment: StateStartingPoint enum, FederalTotals enriched, PartialReturn.add() typed, ComputedTotals.computed_input_hash, W2StateRow multi-state list, Form1099R/G/SSA/K1 typed stubs, OBBBA adjustment fields (Sch 1-A tips/overtime, senior, Trump Account). 32 new regression tests. Suite: 261 passed.

**FAN-OUT wave 1 complete** (commits a6b2540 → 04d6c78, octopus merge + registry wiring): 12 parallel sub-agents dispatched via manual git worktrees under /tmp/claudetax-wt/. Each agent owned disjoint files, zero merge conflicts. Landed:
- **States (4)**: CA (fanout/ca +34), NY (fanout/ny +20), WA (fanout/wa +18 real $278k TY2025 threshold from DOR), DC (fanout/dc +27 real TY2025 brackets from OTR D-40ES). All 4 wired into `_registry.py`.
- **Calc patches (3)**: CTC/ACTC/ODC (fanout/ctc +14, OBBBA $2,200 + combined-phase-out ODC-first), NIIT Form 8960 (fanout/niit +20), EITC (fanout/eitc +21 full phase-in/plateau/phase-out from Rev. Proc. 2024-40). **NOT yet wired into engine.py** — deferred to wave 2.
- **Ingesters (3)**: W-2 pypdf (fanout/w2pdf +15), 1099-INT pypdf (fanout/int1099pdf +15) — both use SYNTHETIC field names with loud TODOs for real IRS widget research. W-2 Azure DI (fanout/w2azure +27 +1 skip, real schema from the document-intelligence-code-samples repo).
- **Golden fixtures (2)**: w2_investments_itemized (fanout/goldenw2inv +15, SALT cap regression-locked at $10k → deduction $35k, MFJ $218.5k → $6,253 owed), se_home_office (fanout/goldensehome +15, Sch C $120k gross - $30k expenses → AGI $83,641 regression-locking the net-profit fix).

Full suite: **503 passed + 1 skipped** in 1.07s. Registry now has 12 entries (8 no-tax + CA/NY/WA/DC).

**FAN-OUT wave 3 complete** (11 agents landed in primary octopus merge + form 1040 renderer landed via delayed retry after a 60-min timeout on the first attempt — 12 of 12 total). Octopus merge + registry wiring. Used Agent-tool `isolation: "worktree"` (automatic .claude/worktrees/ — added to .gitignore) instead of manual worktrees. Landed:
- **States wave 3 (6)**: NC (+26, tenforty flat 4.25%, verified against NCDOR), OH (+36, tenforty, compressed to 2 brackets with break at $100k per the official 2025 IT-1040 booklet page 18 — divergence from legacy 4-bracket assumption documented), OR (+23, tenforty graduated, file named `or_.py` with trailing underscore because `or` is a Python keyword — sole exception in the package), IL (+32, hand-rolled flat 4.95%, exemption corrected to TY2025 $2,850 via WebFetch), CO (+40, hand-rolled flat **4.40%** — TABOR rate cut did NOT trigger TY2025 per CO OSA audit 2557P and SB25-138 fiscal note; starts from federal taxable income), GA (+26, hand-rolled flat 5.19% verified against 2025 IT-511 booklet, $12k/$24k personal exemption + $4k/dep). Hand-rolled states (IL, CO, GA) v1 approximations — state additions/subtractions deferred and loudly documented. Registry: 24 plugins total.
- **Engine wiring** (fanout/wave3-engine-obbba +18 +1 skip): OBBBA senior-deduction + Schedule 1-A now folded into engine.compute() via a **gated two-pass tenforty strategy** — first pass with OBBBA adjustments zeroed to get clean MAGI (circularity-free), compute OBBBA patches, fold into AdjustmentsToIncome, second pass for authoritative bracket calc. Second pass is skipped when no filer age 65+ and no tips/overtime declared — all 3 pre-existing golden fixtures hit the single-pass hot path and are bit-for-bit unchanged. Locked in tests: $5k tips on $65k single saves $680 (not the naive marginal $1,100) — proves bracket-correctness matters.
- **Form 4547 Trump Account patch** (fanout/wave3-form4547 +13): Discovered via IRS primary research (irs.gov/forms-pubs/about-form-4547 + i4547 12/2025 instructions + f4547.pdf) that **Form 4547 is a pure election form with NO dollar lines — IRC §219 explicitly disallows any individual deduction for Trump Account contributions**. Patch correctly returns $0 always and exposes a loud §219 warning in the audit trail. The canonical model field `AdjustmentsToIncome.trump_account_deduction_form_4547` is based on an incorrect pre-statute assumption → **flag for wave-4 model cleanup**. Patch not wired into engine.compute() (concurrent engine-wiring agent held that file); wave-4 can either wire the zero-always patch for audit visibility or drop it and remove the model field.
- **Ingesters (2)**: 1099-R (+22, all 13 Form1099R fields mapped, DocumentKind.FORM_1099_R pre-existed in _pipeline.py so no shared-infra edit needed), 1099-G (+20, all 9 Form1099G fields). Both synthetic widget names.
- **Validation (1)**: FFFF compatibility checker (+23). 10 IRS-cited constants: 50 W-2s, 11 Sch E properties, 8 Form 8829, 4 Form 8283, 4 Form 8082, 1+25 Form 8938, 1-per-parent Form 4562, first-time-filer-age ≥16, no attachments (hard policy), no state returns, limited calculations, no income cap. Unsupported forms captured: 1040-SR, K-1, 8915-C/D, 1098-C, 4547, 1040-X (prior-year). New `skill/reference/ffff-limits.md` reference doc.
- **Form 1040 renderer** (fanout/wave3-form1040 +5, delayed retry): Two-layer design at `skill/scripts/output/form_1040.py` — `Form1040Fields` frozen dataclass with 47 fields (3 non-Decimal header + 44 Decimal lines covering lines 1a/1z/2a/2b/3a/3b/4a/4b/5a/5b/6a/6b/7/8..37) + `compute_form_1040_fields(return_)` (pure mapping, no recomputation) + `render_form_1040_pdf(fields, out_path)` (reportlab text fallback — SCAFFOLD until real IRS AcroForm widget names are researched). v1 simplifications loudly documented: line 6b (SS taxable worksheet), line 13 (QBI), lines 17/20/31 (Sch 2/3) all hard-zeroed; 1099-R routed entirely to line 4a/4b (box7 code classification deferred). First attempt timed out during research phase; retry succeeded by providing the TY2024 line structure verbatim in the prompt and forbidding WebFetch verification.

Full suite: **1128 passed + 2 skipped** in ~8s. Registry: 24 plugins (8 no-tax + CA/NY/WA/DC/AZ/MA/MI/NJ/PA/VA + NC/OH/OR/IL/CO/GA).

**FAN-OUT wave 2 complete** (commits 0cf981c → a0a544a, octopus merge + registry wiring): 12 parallel sub-agents in manual git worktrees. Landed:
- **Engine wiring** (fanout/engwire +15): `compute()` now calls CTC/NIIT/EITC patches after tenforty, folds results into Credits/Payments/OtherTaxes. Lazy import breaks niit→engine cycle. Verified as bit-for-bit no-op on all 3 pre-existing golden fixtures (simple_w2_standard, w2_investments_itemized, se_home_office) — none have dependents or MAGI above NIIT/EITC thresholds, so no patches fire.
- **States wave 2 (6)**: AZ (+34, 2.5% flat), MA (+34, STATE_GROSS Part A/B/C), MI (+41, 4.25% flat, 6 partners, flagged potential tenforty exemption gap), NJ (+37, PA-only reciprocity, fixed 404'd e-file URL via WebFetch), PA (+42, PA_COMPENSATION_BASE flat 3.07% verified $1,995.50 on $65k), VA (+38, 5 partners incl. DC). All wired into registry.
- **Ingesters (3)**: 1099-DIV (+15), 1099-NEC (+15), 1099-B (+17 with explicit single-transaction-only limitation locked). All use synthetic field names — real IRS widget research pending.
- **OBBBA patches (2)**: Senior deduction §63(f)(3)-enhanced (+22, $6k/filer age 65+, 6% phase-out rate verified against 3 independent sources), Schedule 1-A tips/overtime (+30, caps $25k/$12.5k/$25k confirmed from IRS newsroom, phase-out rate $100/$1,000 assumed and loudly locked in tests as UNVERIFIED pending final IRS Schedule 1-A instructions).

Full suite: **844 passed + 1 skipped** in 1.72s. Registry: 18 plugins (8 no-tax + CA/NY/WA/DC + AZ/MA/MI/NJ/PA/VA).

**Deferred to wave 4 and beyond:**
- **Model cleanup**: remove or zero-out `AdjustmentsToIncome.trump_account_deduction_form_4547` now that wave 3 confirmed IRC §219 disallows the deduction; wire the zero-always Form 4547 patch into engine.compute() for audit visibility, or drop it
- **Real IRS AcroForm widget name research** for Form 1040 — wave-3 renderer is a reportlab scaffold; the real overlay onto the IRS fillable PDF needs widget identifiers
- Real per-state nonresident apportionment (CA 540NR, NY IT-203 source ratio, PA Sch NRH, MI Sch NR, WA RCW 82.87.100 sourcing, MA 1-NR/PY)
- Hand-rolled-state additions/subtractions (IL Sch M, CO DR 0104 add/sub, GA Sch 1) — v1 approximations landed in wave 3, these need real modeling
- Remaining ~21 taxing states (AL, AR, CT, DE, HI, ID, IN, IA, KS, KY, LA, ME, MD, MN, MS, MO, MT, NE, NM, ND, OK, RI, SC, UT, VT, WI, WV) — all hand-rolled since tenforty's 11-state list is now exhausted
- SSA-1099/K-1 ingesters (1099-R/G landed in wave 3)
- PDF output renderers per IRS form beyond 1040 (Sch A/B/C/D/E/SE, 8949, 8829, 6251, etc.)
- FFFF entry map + paper-file bundle (FFFF limits checker landed in wave 3)
- Azure Document Intelligence variants for 1098/1099 (beyond W-2)
- Real IRS W-2 / 1099-INT / 1099-DIV / 1099-NEC / 1099-B / 1099-R / 1099-G AcroForm field name research (replace synthetic field maps)
- SKILL.md interview flow
- Distribution packaging + wet test

---

## Key Findings (persistent across sessions)

### The "e-file" constraint shapes everything
Individuals cannot transmit MeF XML directly to the IRS. No EFIN, no Transmitter, no ATS. The skill's "output" is always a bundle of artifacts a human hands to an approved channel (FFFF for federal, state DOR portals per-state, commercial software import, or paper). Don't plan on MeF transmission — plan on producing the best possible bundle for each downstream channel.

### OBBBA (P.L. 119-21, signed 2025-07-04) retroactively changed TY2025
Any TY2025 number sourced only from Rev. Proc. 2024-40 is wrong. The OBBBA adjustments must overlay:
- Standard deduction: $15,750 S / $31,500 MFJ / $23,625 HoH (raised from Rev. Proc. by $750/$1,500/$1,125)
- CTC: $2,200 (refundable $1,700), now indexed
- 1099-K threshold: reverted to $20,000 AND 200 transactions
- Senior deduction: +$6,000 age 65+ (TY2025–2028), phase-out $75k/$150k
- QBI made permanent (was scheduled to sunset 12/31/2025)
- New Form 4547 (Trump Account election)
- New Schedule 1-A (tips/overtime temporary deductions)

### tenforty may lag OBBBA
tenforty wraps OpenTaxSolver, which has TY2025 support but may not yet reflect OBBBA's retroactive TY2025 changes. **CP4 verifies this before we commit to the wrap strategy.** If tenforty is current, wrap cleanly. If small gap, patch layer. If big gap, fall back to HabuTax or our own calc.

### State plugin API unblocks parallelism
Once CP5 freezes the `StatePlugin` interface and the reciprocity table, the 43 state implementations can fan out to parallel sub-agents. Each agent gets: the interface + the state's DOR forms page + a base fixture + a test harness that runs the plugin. Independent, no file collisions if we pre-declare paths (`skill/scripts/states/<xx>.py` + `skill/fixtures/state_<xx>/`).

### FFFF is the primary free federal e-file path for this profile
But it has hard limits: ≤50 W-2s, ≤11 Schedule E properties, no document attachments, no state returns, some forms force paper. The skill must check these limits before recommending FFFF and fall back to paper or commercial if exceeded. Track at `skill/reference/ffff-limits.md`.
