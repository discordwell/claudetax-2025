# Claudepad — Tax Prep Skill

Session memory for this project. Top section = most recent session summaries (newest first, keep 20 max, overflow to `oldpad.md`). Bottom section = persistent key findings that survive across sessions.

---

## Session Summaries

### 2026-04-12 09:00 UTC — Wave 7B + 7D: state PDF renderers + wet test + version bump

**PR #2 reviewed and merged** (wave 7A — 8 federal forms, 158 tests, tenforty pin). Fast-forward merge, 3763 passed.

**32 stale worktrees pruned** from the remote session's parallel fan-out.

**Serial fixes #2 and #3** already resolved in prior waves (verified — Form 8829 docstring and graph-wrap cross-refs both present).

**Wave 7B dispatched** in 3-agent parallel fan-out (worktree-isolated):
- **Agent A** (AZ/CA/OR/VA): All 4 implemented — AcroForm fill via state DOR fillable PDFs (406/169/146/148 widgets respectively)
- **Agent B** (NY/NJ/PA/MA): 2 implemented (NY IT-201, MA Form 1). NJ deferred (digit-by-digit widget layout unusable), PA deferred (DOR site requires JS navigation)
- **Agent C** (NC/OH/MI): 2 implemented (NC D-400, MI MI-1040). OH deferred (fully flattened PDF, 0 AcroForm widgets)

**8 of 11 priority states now render filled PDFs**. 3 deferred with documentation.

**Wave 7D completed**:
- Hard wet test: complex multi-form return (W-2 $120K + 1099-INT $2.5K + 1099-DIV $5K + Schedule C $45K + Schedule E rental $24K + education credits + CA state). Pipeline produced 10 artifacts, all correct. AGI $163,931, total tax $33,688, CA state tax $11,001.
- SKILL.md updated with all 8 new federal forms (phases 3, 4b, 8, reference index)
- Version bump 0.1.0 → 0.2.0

**Code review findings** (non-blocking):
1. Dead dataclass factory functions in 5 state renderers (defined but not called in render path) — establishes pattern for future use
2. Three distinct render patterns across agents (direct ss.get loop vs dataclass+asdict vs inline construct) — cosmetic inconsistency
3. Render tests check file existence/size only, not field values — consistent with federal renderer tests

**Suite**: 3764 passed, 3 skipped.

---

### 2026-04-12 07:45 UTC — Wave 7A kickoff: 8 new federal forms via parallel fan-out

**Wave 7A dispatched and landed** — 8 parallel worktree agents implementing missing federal forms. Two agents (Schedule E, Form 8863) timed out on first attempt; retries succeeded. Cherry-pick merge choreography on pipeline.py was the main friction point (every agent adds a render gate to the same function signature + render section).

**Serial fixes (pre-fan-out)**:
- Pinned `tenforty==2025.8` in pyproject.toml + requirements.txt for gatekeeper stability
- Form 8829 docstring drift (fix 2) already landed in cef36a5
- Graph-wrap gap.md cross-refs (fix 3) already done in wave 6

**New forms (8 agents, all merged)**:
- **7A-1 Schedule E** — Rental real estate income/loss. Layer 1 compute fields per-property (up to 3), delegates to `schedule_e_property_net` from engine. Layer 2 reportlab scaffold. 10 tests.
- **7A-2 Form 2441** — Child and dependent care credit. AGI-based rate (35% → 20%), $3k/$6k expense cap, employer benefits exclusion. Added `DependentCareExpenses` model to CanonicalReturn. 21 tests.
- **7A-3 Form 8863** — Education credits (AOTC + LLC). Per-student AOTC ($2,500 max, 60/40 nonrefundable/refundable split), aggregate LLC ($2,000 max), MAGI phase-outs ($80k-$90k Single, $160k-$180k MFJ). Added `EducationStudent` + `EducationCredits` models. 19 tests.
- **7A-4 Form 8962** — Premium tax credit (ACA). FPL-based applicable figure, monthly PTC computation, repayment caps (Table 5). Added `Form1095A` + `Form1095AMonthly` models. 30 tests.
- **7A-5 Form 8606** — Nondeductible IRA basis tracking. Part I lines 1-14 (nontaxable percentage, distribution/conversion splits), Part II Roth conversion taxable amount. Added `IRAInfo` model. 13 tests.
- **7A-6 Schedule 1** — Additional income and adjustments. Routes engine values (Sch C/E/1099-G) to Part I, AdjustmentsToIncome to Part II, OBBBA to lines 9/25. 30 tests.
- **7A-7 Schedule 2** — Additional taxes. Pure read-and-format from ComputedTotals (AMT) and OtherTaxes (SE, Medicare, NIIT). 15 tests.
- **7A-8 Schedule 3** — Additional credits and payments. Routes Credits + Payments to Part I/II lines. 20 tests.

**Suite**: 3605 → **3605 + 158 = 3763** (158 net new tests from wave 7A, 6 skipped for pypdf env issue).

**Pipeline integration**: `run_pipeline` now has render gates for all 8 new forms (render_schedule_1, render_schedule_2, render_schedule_3, render_schedule_e, render_form_2441, render_form_8606, render_form_8863, render_form_8962). Schema regenerated to include new model classes.

**Agent leak pattern**: Same wave-6 issue — agents occasionally write to the main working directory instead of their worktree via path substitution errors. Cleaned up by `git checkout -- <file>` + `rm` before each cherry-pick. Two agents (Form 2441, Schedule E retry) committed directly to main branch instead of using worktree branches.

**Remaining wave 7 items** (from HANDOFF.md):
- **7B**: State PDF renderers (43 states compute but don't render PDFs yet)
- **7C**: Real nonresident apportionment (CA 540NR, NY IT-203-B)
- **7D**: Hard wet test, SKILL.md update, FFFF map extension, version bump to 0.2.0

---

### 2026-04-11 22:30 UTC — Wave 6 complete: D/8949 + 6251 + 4562 + 8829 + state dispatch + FFFF + packaging

**Wave 6 dispatched and landed** in one 9-agent fan-out (8 parallel worktrees + 1 serial cleanup). The skill is now functionally complete end-to-end: every federal form a common filer needs, every state, every output channel.

**Agents**:
- **A0 cleanup** (d0cb8a5): gatekeeper test name unification to `test_resident_single_65k_tax_lock` across 16 hand-rolled plugins, `LOCK_VALUE` module-level constants, AR DFA primary-source citation + low-income $29/exemption credit post-hoc adjustment, gap.md docstring pointers on all 26 wave-4/5 state plugins.
- **A1 pipeline state dispatch** (9397125): `run_pipeline` now calls `_dispatch_state_plugins` after federal compute, relevant states = resident state + every W-2 `state_rows[*].state_code`, residency inferred from address match. New `state_source_wages_from_w2s` / `state_source_schedule_c` / `sourced_or_prorated_wages` helpers in `_hand_rolled_base.py` replace blind `day_prorate` across 18 hand-rolled plugins. CA/NY get real Schedule CA-NR / IT-203-B scaffolding (`Person.ny_workdays_in_ny` field). PA/IL multi-state fixture re-locked to sourced numbers PA $921 / IL $1,591.43.
- **A2 Schedule D + Form 8949** (01ed8ab): capital-gains compute chain with per-lot classification (box A-F short/long-term), 11-row overflow pagination, wash-sale adjustment, $3k/$1.5k loss cap. `Form1099BTransaction.form_8949_box_code` override for manual entries. Real IRS widget maps (agent built them directly from live f1040sd/f8949 PDFs — wave 7 "research" task deleted).
- **A3 Form 6251 AMT** (9d83599 — landed on main DURING fan-out by the agent): AMT compute + render, `AMTAdjustments` dataclass for ISO/PAB/depreciation manual inputs, engine integration adds AMT to `total_tax`, `ty2025-constants.json` AMT block with Part-III capital-gains worksheet left as TODO.
- **A4 Form 4562 depreciation** (148bc33): MACRS tables (3/5/7/10/15/20/25/27.5/39-year, half-year and mid-month conventions) from IRS Pub 946. `DepreciableAsset` model, §179 ($1.25M TY2025 limit, $3.13M phase-out, $31,300 SUV cap), 40% bonus depreciation, §280F auto caps. Schedule C line 13 hook via `_effective_line_13_depreciation`.
- **A5 Form 8829 home office** (ae49d48): simplified ($5/sq ft, 300 cap, $1,500 max) + regular-method compute + render. `HomeOffice` model, 58/58 IRS widgets mapped. 39-year straight-line mid-month depreciation on home. Engine dispatcher `apply_home_office_deductions` runs before any tenforty pass.
- **A6 real IRS AcroForm widgets** (ef48f93): 7 of 9 Tier-1 ingesters now carry BOTH synthetic and real widget names (W-2, 1099-INT/DIV/NEC/R/G, K-1 1065/1120-S). 1099-B and SSA-1099 confirmed as flattened (non-fillable) at canonical IRS URLs — upgrade path is Azure DI or OCR. Added `TestReal<FORM>AcroForm` classes (29 tests) verifying real IRS PDFs classify + ingest without crashing.
- **A7 FFFF entry map + bundle integration** (ae5f96c): new `build_ffff_entry_map` emits Form 1040 + Sch A/B/C/SE field-by-field transcript (ASCII-only `to_text()`, structured `to_json()`). `run_pipeline` now wires in `build_paper_bundle` + FFFF emit with `build_paper_bundle`/`emit_ffff_map` kwargs (both default True). Guards for Schedule D / 6251 future wire-ups present.
- **A8 distribution packaging** (ebb6dc8): CLI entry point `tax-prep` with `run/schema/version` subcommands, `pyproject.toml` 0.0.1 → 0.1.0 + full runtime deps, README with install story, 10 CLI tests. `pip install -e .` works cleanly.

**Suite**: 3310 → **3596 passed + 3 skipped** (+286 net new tests).

**Non-blocking wave 7 items (from review)**:
1. **Form 4562 / Form 8829 depreciation interaction** — when a single Schedule C has BOTH `depreciable_assets` AND regular-method `home_office`, `_sch_c_non_home_office_expenses` uses stale `expenses.line13_depreciation` instead of the `_effective_line_13_depreciation` override. No test covers this combo. Rare but real arithmetic leak.
2. **AR low-income credit pin** fragility — `$248.73 → $219.73` graph probe is pinned to current tenforty version; future version bump can shift by a cent. Consider pinning tenforty version.
3. **Form 8829 helper docstring drift** — claims "re-implement formula here" but imports from engine.
4. **Nonresident apportionment** (CA 540NR, NY IT-203-B) — scaffolding only; plugins still day-prorate for states without W-2 state rows. Real sourcing is per-state DOR work.
5. **Graph-backend WI output-field gaps** not cross-referenced from wrap plugin docstrings.

**Quick fixes applied inline after review**:
- `paper_bundle._FORM_ORDER` now explicitly includes `form_4562` (seq 67) + `form_6251` (seq 32) so they slot into IRS attachment sequence order instead of the alphabetical tail.
- Stale TODO comment in `pipeline.py` about "when agent 1's changes land" rewritten to describe the shipped state.

**Merge choreography surprise**: Agent 3 (Form 6251) and Agent 8 (packaging) committed directly to `main` from their worktrees rather than staying on a worktree branch, so by the time I started cherry-picking, those two commits were already on main. Cleaned up by using `git cherry-pick --skip` where the commit was already present. Also a pile of main-path file leaks from Agents 0/4/6 editing main by mistake (path substitution error in their Edit calls); stashed and dropped safely since every leaked file was duplicated in the worktree commits.

**Key patterns confirmed**:
- Real IRS widget maps are viable for Form 1040 + every schedule — the agents demonstrated this on the fly. "Synthetic widget names" as a wave-strategy crutch is retired.
- Post-hoc graph-backend credit adjustments (AR $29 low-income credit) are a clean pattern for fixing the handful of graph-wrap plugins that diverge only at edge incomes.
- Parallel worktree fan-out + cherry-pick merging scales to 8-9 agents with manageable pipeline.py conflict load (always in render-gates section + kwarg section). Merge cost is ~5 minutes per wave.

---

### 2026-04-11 18:00 UTC — Wave 5 complete: 51-state coverage + real AcroForm overlays

**Wave 5 dispatched and landed** in one 12-agent fan-out. Every one of the 21 remaining taxing states now has a plugin; real IRS AcroForm overlays replace the reportlab scaffolds for Form 1040 + Schedule A/B/C/SE; Schedule K-1 ingester lands; paper-file bundle generator + SKILL.md interview scaffold ship; multi-state PA→IL golden fixture locks the state plugin dispatch chain end-to-end.

**Final registry state**: **51 jurisdictions** (50 states + DC) — complete coverage for the first time in the project's history. Split across the four fan-out waves: wave 1 (CA/NY/WA/DC + 8 no-tax states), wave 2 (AZ/MA/MI/NJ/PA/VA), wave 3 (NC/OH/OR + IL/CO/GA hand-rolled, wave-4 remediated), wave 4 (CT/KS/KY/MD/MN hand-rolled + WI graph), wave 5 (21 states via probe-then-verify-then-decide: 10 wraps + 11 hand-rolls).

**Wave 5 state decisions** (probe table in `skill/reference/tenforty-ty2025-gap.md`):
- **Graph-backend wraps (10)**: AR, HI, IA, ID, LA, MS, MT, NM, SC, VT. Each matched DOR primary source within ±$5. Each wraps `backend="graph"` and pins the graph value in a gatekeeper test.
- **Hand-rolled (11)**: AL, DE, IN, ME, MO, ND, NE, OK, RI, UT, WV. Each recovers a state-specific personal exemption or credit that the graph backend omits. Gap magnitudes: $30 (IN), $47.50 (OK), $96.40 (WV), $110 (DE), $141 (IL — wave 3), $152 (MD — wave 4), $195 (RI), $347.63 (ME), $488 (AL), $510 (KS — wave 4), $608 (UT). AL and MO both miss the federal-tax-deduction that those states allow. NE has an additional functional bug: graph raises `NotImplementedError` on `num_dependents > 0`.
- **ND correction**: my CP8-B "$15.11 is broken/stubbed" finding was WRONG. A6 verified it is mathematically correct — ND's zero-bracket cap for Single is $48,475, so $49,250 TI leaves only $775 in the 1.95% middle bracket → $15.11. Hand-rolled anyway (safe call) with a gatekeeper pinning BOTH the graph value AND the DOR formula for belt-and-suspenders. `tenforty-ty2025-gap.md` updated with the retraction.
- **UT rate correction**: TY2025 is 4.5% per HB 106 2025, not the 4.55% I had in the brief. Agent caught it and locked the real number ($2,588.23, not the $1,980 graph-backend reports).

**Real AcroForm overlays** (B1 + B2):
- **B1 — Form 1040**: new shared `skill/scripts/output/_acroform_overlay.py` with `fill_acroform_pdf`, `load_widget_map`, `fetch_and_verify_source_pdf`, `build_widget_values`, `format_money`, `WidgetMap` dataclass. IRS `f1040.pdf` bundled at `skill/reference/irs_forms/` (SHA256 pinned, auto-fetch fallback, fail-loud on mismatch). Form 1040 renderer Layer 2 replaced: 8 canonical line widgets round-tripped (wages, AGI page-1 + page-2 mirror, line 16 tax, line 24 total tax, line 25a W-2 withholding, line 34 overpayment, line 37 amount owed). Zero silent fallbacks to reportlab.
- **B2 — Schedules A/B/C/SE**: 4 new widget maps + 4 bundled TY2025 source PDFs (all verified to be TY2025 at canonical IRS URLs). Per-schedule mapped/unmapped counts: Sch A 27/33, Sch B 67/72, Sch C 86/105, Sch SE 22/27. **Schedule C TY2025 delta**: line 27 split into 27a/27b (27b is the new §179D Energy efficient commercial buildings deduction) — flagged in unmapped since Layer 1 doesn't model it yet.
- **B1/B2 merge fixup**: B2 wrote a local stub of `_acroform_overlay.py` expecting dict-access semantics; B1 shipped a frozen `WidgetMap` dataclass. Resolved by (a) adding `load_widget_map_as_dict(path) -> dict` helper to B1's canonical version, (b) adding `verify_pdf_sha256(path, expected_sha) -> None` compatibility shim (raises RuntimeError with "missing" or "SHA-256 mismatch" substrings that B2's tests match on), (c) updating B2's 4 schedule renderers to call the dict variant. Also: paper-bundle test used title-case "Schedule A" detection but the real IRS header is "SCHEDULE A" all-caps → fixed with `.upper()`.

**Other deliverables**:
- **C1 — Schedule K-1 ingester**: Tier-1 pypdf, all 19 ScheduleK1 model fields mapped (both 1065 partnership and 1120-S S-corp flavors), content-layer probe distinguishes them. `DocumentKind.SCHEDULE_K1_1065` and `SCHEDULE_K1_1120S` already existed. Wired into `pipeline.build_default_cascade()` (now 9 Tier-1 ingesters).
- **D1 — SKILL.md**: 561-line Claude-facing interview prompt + 5 worked example transcripts (19KB). 9 interview phases, CP8-A medical-floor warning explicitly wired into the Schedule A walk, CP8-D county collection in the address phase for MD residents, OBBBA senior/tips/overtime/Form 4547 covered, FFFF compatibility checklist. 46 structural tests.
- **D2 — Paper bundle generator**: `build_paper_bundle(canonical_return, rendered_pdf_paths, out_path)`. Cover sheet (name, SSN, summary table, IRS service-center address, FFFF status), IRS form-order reconciliation via `_FORM_ORDER` tuple, signature page (handles MFJ/MFS/QSS dual-name), mailing instructions with service-center lookup. New `skill/reference/irs-mailing-addresses.json` with 52 entries (50 states + DC + INTL) × with-payment/without-payment routing. 26 tests.
- **E1 — Multi-state golden fixture**: PA→IL part-year single filer ($30k Philly + $35k Chicago). Agent caught that my idealized hand-check (real per-state income sourcing) doesn't match the existing plugins — they day-prorate the full federal $65k. Locked the actual day-prorated v1 numbers (PA $989.55 / IL $1,550.86) with a loud TODO explaining that when real sourcing lands the test will need re-blessing to PA $921 / IL $1,591.43. First fixture that exercises state plugin dispatch end-to-end. 38 tests.

**Suite**: 1920 → **3310 passed + 3 skipped** (+1390 net new tests, zero regressions).

**Key patterns confirmed or discovered**:
- CP8-B probe + hand-rolled fallback is the right strategy for any state tenforty doesn't natively support. The 10/11 wrap-vs-hand-roll split is now well-calibrated; future waves can trust it.
- Graph backend's per-state omissions are almost entirely state-specific personal exemptions/credits. Any state that folds its exemption into std ded (VT via Act 65 2023, for example) wraps cleanly.
- `_hand_rolled_base.py` helpers were heavily used by wave 5 hand-rolled plugins. Keep extending rather than refactoring existing code.
- TY2025 IRS PDFs are already live at canonical URLs — no TY2024 fallback needed for any IRS form we've checked (1040, Sch A, B, C, SE).

---

### 2026-04-11 14:00 UTC — Wave 5 dispatch plan (archived — see above for completion)

**Goal**: 21 remaining taxing states + real AcroForm overlays + K-1 ingester + SKILL.md scaffold, in a single ~12-agent fan-out.

**CP8 pre-flight serial work** (must land before wave 5 dispatch):
1. **CP8-A ✅**: engine medical 7.5% floor fix (real-money calc bug) — DONE
2. **CP8-B ✅**: probe of 21 remaining tenforty states via graph backend — DONE. Finding: graph covers all 21 but has material gaps vs DOR for at least 4 of the 10 states we've already hand-rolled. NOT a drop-in replacement.
3. **CP8-C**: extract `skill/scripts/states/_hand_rolled_base.py` with shared Decimal helpers, day-proration, bracket lookup, and a StatePluginMeta factory. Reduces wave-5 state boilerplate.
4. **CP8-D**: canonical model extensions (serial). `Address.county` for MD-style local tax, top-level foreign-accounts flag for Schedule B Part III, W2 `box14_qualified_tips` / `box14_qualified_overtime` for OBBBA Schedule 1-A structured input. Regen schema.
5. **CP8-E**: minimal `skill/scripts/pipeline.py` end-to-end: input PDFs + taxpayer.json → classifier → ingesters → CanonicalReturn → compute() → renders Form 1040 + Sch A/B/C/SE PDFs → emits validation_report + result.json. First real wet-test harness.

**Wave 5 dispatch (post-CP8, 12 agents)**:

Group A — States (6 agents, each covers 3-4 states using the post-probe rubric):
  - A1: AL, AR, DE (Southeast + DE) — probe graph → DOR-verify → wrap or hand-roll per state
  - A2: HI, ID, MT (mountain/pacific) — same rubric
  - A3: IA, IN, NE (plains) — same rubric
  - A4: LA, MS, OK (gulf) — same rubric
  - A5: ME, RI, VT, WV (new england + WV) — same rubric
  - A6: MO, NM, ND, SC, UT (leftovers) — **ND must be hand-rolled** (probe returned $15.11 on $65k, graph is broken/stubbed)

  Each agent must: (1) probe graph backend, (2) hand-compute against DOR primary source for $65k Single as the ground-truth test, (3) decide match→wrap or mismatch→hand-roll PER STATE (not per agent), (4) use `_hand_rolled_base.py` helpers for any hand-rolled paths, (5) pin the decision with a gatekeeper test so drift trips CI.

  **Critical prompt language**: explicit warning that the graph backend is NOT a drop-in replacement, with the wave-4 IL/KS/CT/MD divergences cited as examples. Agents must cross-check, not assume.

Group B — Real IRS AcroForm overlays (2 agents):
  - B1: Form 1040 real AcroForm renderer (Layer 2 replacement) using the wave-4 widget map at `skill/reference/form-1040-acroform-map.json`. Delete the reportlab scaffold; fill the real IRS f1040.pdf widgets via pypdf.
  - B2: Schedule A/B/C/SE AcroForm renderers (Layer 2 replacements, bundled). Reuses the wave-4 methodology doc to enumerate widgets for each form, produces per-form maps as reference JSON, and replaces Layer 2 for all four schedules.

Group C — Ingesters (1 agent):
  - C1: Schedule K-1 pypdf ingester following the 1099-R/G/NEC/SSA pattern. Synthetic widget names with TODOs.

Group D — Pipeline extensions (2 agents):
  - D1: SKILL.md interview flow scaffold (natural-language Claude-driven interview that populates CanonicalReturn step by step; handoff to `pipeline.py`).
  - D2: Paper-file bundle generator (iterates all rendered PDFs + state PDFs + signature page + mailing instructions).

Group E — Fixtures (1 agent):
  - E1: Expand golden fixture coverage with a real-looking "multi-state part-year" case exercising CT residency + PA job (reciprocity) + IL job (no reciprocity), day-prorated. Tests the full ingest→compute→render→state plugin chain.

**Not in scope for wave 5** (deferred):
- Remaining ingester AcroForm real widget research (W-2 / 1099 family) — lower priority than state coverage.
- Azure DI expansion beyond W-2.
- Wave-4 hand-rolled state conversion to graph wrappers — do NOT do this; the hand-rolled plugins are correct and the graph backend has gaps.
- Per-state nonresident apportionment beyond day-proration — still deferred.

---

### 2026-04-11 10:00 UTC — Session 2: wave 3 finish + wave 4 fan-out

**Wave 3 serial cleanup** (commit `8d260b1` — 3 deferred items the user split off for "finish wave 3" before wave 4 dispatch):
- **S1 Form 4547 Trump Account AGI leak fix**: removed `trump_account_deduction_form_4547` from `_sum_adjustments` in engine.py:273 (IRC §219 disallows any individual deduction per wave-3 research). Wired `compute_trump_account_deduction` into engine.compute() as audit-only (always returns $0) and force-zero the field on returned adjustments. 4 regression tests lock a $1,000 leaked input → $0 AGI impact.
- **S2 FFFF validator wiring**: new `run_return_validation(return_)` entry point in `skill/scripts/validate/__init__.py`. New `ComputedTotals.validation_report: dict[str, Any] | None` field (schema regenerated). engine.compute() runs validator on patched return and stores the dict. 10 tests covering shape, JSON round-trip, engine wiring, K-1 blocker surfacing.
- **S3 golden fixture `senior_with_tips`**: Single age-67, $80k wages + $5k declared tips. Exercises BOTH OBBBA pre-tax-bracket patches (senior deduction $5,700 after 6% phase-out + Schedule 1-A tips $5,000) in a single compute() call. Locked: AGI $69,300, taxable $53,550, fed tax $6,701, refund $3,299. 6 tests.
- Suite: 1128 → 1148 passed + 2 skipped.

**Wave 4 fan-out** (13 parallel sub-agents via `isolation: "worktree"`, cherry-picked 13 commits in sequence because worktrees branched from pre-S1/S2/S3 HEAD — zero conflicts since every agent owned disjoint files). Landed:

- **States (6)**: CT +89 tests ($65k single = $2,875.00, hand-rolled from CT-1040 TCS Rev. 12/25 Tables A-E); MN +78 tests ($2,931.14, hand-rolled Form M1); MD +124 tests ($4,039.01 state+local, hand-rolled Form 502 **+ 22-jurisdiction county local tax table + Anne Arundel/Frederick progressive locals + Dorchester retroactive hike**); WI +40 tests ($2,861.80, **first plugin to use tenforty `backend="graph"`** via wi_form1_2025.json graph definition, known gaps on state_taxable_income/state_tax_bracket echo); KS +151 tests ($2,827.71, hand-rolled K-40 per IP25 booklet, SB 1 2024 two-bracket 5.20/5.58 split at $23k); KY +51 tests ($2,469.20, hand-rolled flat 4% per HB 8 2022 schedule, 7-state reciprocity network).
- **Output renderers (4)**: Schedule A +19 (SALT cap enforced, cross-check against `engine.itemized_total_capped`), Schedule B +22 ($1,500 required threshold, Part III foreign flags defaulted), Schedule C +21 (per-business dispatch via `compute_schedule_c_fields_all`, delegates Line 28/31 to engine helpers), Schedule SE +26 (TY2025 SS wage base $176,100 cited, $400 threshold, cross-checked against engine.other_taxes_total within $1).
- **Form 1040 AcroForm widget research**: +13 tests. Downloaded IRS f1040.pdf (SHA256 `3d31c226df0d189c...`), enumerated 199 terminal widgets, **mapped 52 to all 44 numeric Layer-1 fields**. **Surprise**: IRS URL already serves the TY2025 PDF, not TY2024 — renumbers lines 11/12/13/27 for OBBBA Schedule 1-A. Delta documented in per-field notes. Full methodology doc for repeating on Schedule A/B/C/SE.
- **SSA-1099 ingester**: +30 tests. `DocumentKind.FORM_SSA_1099` already existed. All 6 model boxes mapped (box3/4/5/6 + Medicare B/D from description narrative), synthetic widget names.
- **IL/CO/GA state adds-subs remediation**: +32 tests. IL Sch M (Treasury sub, non-IL muni addback, 100% SS sub, retirement sub), CO DR 0104AD (state-tax addback if itemizing, state refund sub, Treasury sub, age-based pension $20k/$24k combined cap with SS reducing), GA Sch 1 (non-GA muni addback, Treasury sub, 100% SS sub, retirement exclusion $35k age 62-64 / $65k age 65+ per filer). All 3 wave-3 $65k baselines unchanged because fixtures have W-2 income only.

**MAJOR FINDING (captured in `skill/reference/tenforty-ty2025-gap.md`)**: every wave-4 state agent independently discovered that tenforty's `OTSState` enum LIES about TY2025 capability. The default OTS backend has form configs for only 11 states (AZ/CA/MA/MI/NC/NJ/NY/OH/OR/PA/VA); every other enum code raises `ValueError: OTS does not support YYYY/ST_FORM`. Five agents hand-rolled (KS, KY, CT, MN, MD); one (WI) used graph backend. The reference doc lays out the decision rubric (probe → default → graph → hand-roll), the gatekeeper test pattern, and the authoritative supported-state list.

**Registry wiring** (commit `abfcbac`): Registry count 24 → 30. Update test_state_plugin_api.py registry_len to 30.

Suite: **1844 passed + 3 skipped** (new skip: KS tenforty-gatekeeper auto-activates when tenforty gains KS support). That's **+696 net new tests** from the wave-4 starting baseline of 1148.

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

**Wave 4 code review findings (1 fix landed, rest tracked):**
- **FIXED**: Schedule A `line_17_total_itemized` vs `engine.itemized_total_capped` semantic gap. Code was form-accurate (medical line 4 = max(0, raw - 7.5% * AGI) per IRS form); docstring and test wrongly claimed line 17 matches engine's capped total. The engine passes RAW medical to tenforty, so engine_total is pre-floor on medical and diverges from form-level line 17 by exactly `min(raw_medical, floor)`. Fix: updated the module docstring section to document the divergence, renamed the zero-medical cross-check test, and added a new `test_line17_vs_engine_delta_equals_7_5pct_agi_floor_on_medical` test locking the expected delta. Suite 1844 → 1845.
- **TRACKED (non-blocking)**: SSA-1099 ingester + Schedule A/B/C/SE renderers are dark (no caller assembles their Ingester/output cascade). Pre-existing pattern — every ingester and output renderer is dark until the SKILL.md interview or a bundler imports them. Wave 5 or later pipeline step will assemble.
- **TRACKED**: `wi.py` imports private `_to_tenforty_input` from `engine` (symbolic, not public). Same pattern as oh/nj/mi/etc. If wave 5 touches the engine's tenforty marshalling, expect to update 6 importers.
- **TRACKED**: `tenforty-ty2025-gap.md` decision rubric doesn't enumerate "tenforty silently returns zero" case (e.g. stubbed graph file). Wave-4 gatekeeper tests all pin specific nonzero figures, so drift is caught, but the rubric could call this out explicitly in step 2a.
- **TRACKED**: MD Garrett (2.65%) and Cecil (2.74%) county local rates look atypical but are cited to Withholding Tax Facts 2025. Worth an independent double-check before MD goes user-facing.

**Deferred to wave 5 and beyond:**
- **Real AcroForm overlay for Form 1040 / Sch A/B/C/SE**: wave 4 produced the widget-name map for Form 1040 (52 of 199 mapped) and reportlab scaffolds for all 4 schedules. Wave 5 should replace Layer 2 of each renderer with a real `pypdf` AcroForm widget overlay, and apply the methodology doc to produce Schedule A/B/C/SE widget maps.
- Real per-state nonresident apportionment (CA 540NR, NY IT-203 source ratio, PA Sch NRH, MI Sch NR, WA RCW 82.87.100 sourcing, MA 1-NR/PY, MN M1NR, KY 740-NP Sch A, etc.). Current wave-4 plugins use day-proration as a stopgap.
- **Remaining ~21 taxing states**: AL, AR, DE, HI, IA, ID, IN, LA, ME, MO, MS, MT, ND, NE, NM, OK, RI, SC, UT, VT, WV. Per `skill/reference/tenforty-ty2025-gap.md`, every one must be hand-rolled (tenforty's default backend has no TY2025 config for any of them). Each needs its own DOR primary-source research pass.
- Schedule K-1 ingester (SSA-1099 landed in wave 4).
- PDF output renderers per IRS form beyond 1040/A/B/C/SE (Sch D, 8949, 8829, 6251, Sch E, Form 4562, etc.).
- FFFF entry map + paper-file bundle (FFFF compat checker + validator entry-point wired in wave 3/4).
- Azure Document Intelligence variants for 1098/1099 (beyond W-2).
- Real IRS W-2 / 1099-INT / 1099-DIV / 1099-NEC / 1099-B / 1099-R / 1099-G / SSA-1099 AcroForm field name research (replace synthetic field maps, reusing the wave-4 methodology).
- **WI graph backend output-field gaps**: `state_taxable_income` echoes AGI, `state_tax_bracket` / `state_effective_tax_rate` return 0.0. Tax total is correct but the display fields need fixing upstream in tenforty or replaced with a hand-computed post-process.
- **Hand-rolled state deep cleanup**: CO TABOR refund, CO SALT-cap pro-rata addback, GA $4k earned-income sub-cap on retirement exclusion, KY MFJ column split (up to $130.80 taxpayer-unfavorable), KY Family Size Tax Credit, MD county-specific surtaxes, MN dependent-exemption high-income phaseouts, CT EITC (40% federal match), CT property tax credit, KS Schedule S Part A, etc. Each plugin enumerates its own V1 limitations list.
- SKILL.md interview flow.
- Distribution packaging + wet test.

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
