# tenforty OTSState enum vs TY2025 reality

**Last verified**: 2026-04-11 during wave 4 fan-out.
**Applies to**: tenforty v0.x wrapping OpenTaxSolver (OTS), TY2025 specifically.

## The gap in one sentence

`tenforty.models.OTSState` lists 51 US states plus DC, but the default OTS
backend — invoked by `tenforty.evaluate_return(state=CODE, ...)` — only has
form configs for **11 states** in TY2025. Every other code raises
`ValueError: OTS does not support YYYY/ST_FORM`.

## Supported and unsupported, authoritative for TY2025

### ✅ tenforty DEFAULT BACKEND supports for TY2025 (11 states)

```
AZ  CA  MA  MI  NC  NJ  NY  OH  OR  PA  VA
```

Plus the 8 no-income-tax states, which are handled by
`NoIncomeTaxPlugin` and never call tenforty:

```
AK  FL  NH  NV  SD  TN  TX  WY
```

Plus DC and WA, which have their own hand-rolled plugins.

### ❌ tenforty default backend does NOT support for TY2025

Every other state code in `OTSState` raises
`ValueError: OTS does not support 2025/ST_FORM` when passed to
`evaluate_return`. Verified empirically by every wave-4 state agent.

Wave 4 confirmed the gap for:
**CT, KS, KY, MD, MN, WI** (6 hand-rolled recoveries).

Prior-wave agents also have `OTSState` entries but were NOT verified at
fan-out time; they may or may not work. Treat the enum as **aspirational
metadata**, not a capability oracle. **Always probe before assuming.**

### ⚠️ tenforty GRAPH BACKEND — broad TY2025 coverage (CP8-B finding)

WI had a `wi_form1_2025.json` graph definition discovered in wave 4.
The initial assumption was that WI was a one-off. **CP8-B probed the
remaining 21 taxing states and every single one is supported on the
graph backend at TY2025.** Invoked via `tenforty.evaluate_return(
backend="graph", state="ST", ...)`.

#### TY2025 probe results ($65k Single, standard deduction, no deps)

| State | Default | Graph | Graph-backend state_total_tax | Notes |
|-------|---------|-------|-------------------------------|-------|
| AL    | FAIL    | OK    | $3,210.00                     |       |
| AR    | FAIL    | OK    | $2,031.15                     |       |
| DE    | FAIL    | OK    | $3,059.00                     |       |
| HI    | FAIL    | OK    | $3,496.80                     |       |
| IA    | FAIL    | OK    | $1,871.50                     |       |
| ID    | FAIL    | OK    | $2,355.27                     |       |
| IN    | FAIL    | OK    | $1,950.00                     |       |
| LA    | FAIL    | OK    | $1,575.00                     |       |
| ME    | FAIL    | OK    | $3,069.78                     |       |
| MO    | FAIL    | OK    | $2,200.52                     |       |
| MS    | FAIL    | OK    | $2,054.80                     |       |
| MT    | FAIL    | OK    | $2,652.55                     |       |
| ND    | FAIL    | OK    | **$15.11**                    | ⚠️ suspect — verify against ND DOR Form ND-1 (ND has a very low flat rate but $15 on $65k looks like a stub) |
| NE    | FAIL    | OK    | $2,454.83                     |       |
| NM    | FAIL    | OK    | $1,905.75                     |       |
| OK    | FAIL    | OK    | $2,597.38                     |       |
| RI    | FAIL    | OK    | $2,028.75                     |       |
| SC    | FAIL    | OK    | $2,313.30                     |       |
| UT    | FAIL    | OK    | $1,980.00                     |       |
| VT    | FAIL    | OK    | $2,244.85                     |       |
| WV    | FAIL    | OK    | $2,294.50                     |       |

The probe was performed via `skill/scripts/probe_tenforty_states.py`
(re-runnable) on 2026-04-11 against tenforty version installed in the
project venv.

#### Known graph backend output-field gaps (from WI, wave 4)

- `state_tax_bracket` returns `0.0` (no marginal rate exposed).
- `state_effective_tax_rate` returns `0.0`.
- `state_taxable_income` may echo AGI instead of applying the
  state-specific standard deduction / personal exemption on the output
  side (verified on WI; affects other graph-backend states too).

#### Observed graph-backend correctness gaps (CP8-B cross-check)

CP8-B cross-checked the graph-backend $65k Single result against our
hand-rolled wave-3/wave-4 plugins for the 10 states we've already
authored. **Graph backend is NOT a drop-in replacement** — it has real
per-state gaps in which state-specific exemptions/credits are applied:

| State | Hand-rolled | Graph     | Delta     | Root cause                                         |
|-------|-------------|-----------|-----------|----------------------------------------------------|
| CO    | $2,167.00   | $2,167.00 | match     | —                                                  |
| GA    | $2,750.70   | $2,750.70 | match     | —                                                  |
| KY    | $2,469.20   | $2,469.20 | match     | —                                                  |
| MN    | $2,931.14   | $2,931.14 | match     | —                                                  |
| WI    | $2,861.80   | $2,861.80 | match     | (WI IS the graph wrapper — tautological)           |
| **IL** | $3,076.43  | $3,217.50 | **+$141** | Graph does NOT apply IL $2,850 personal exemption  |
| **KS** | $2,827.71  | $3,338.44 | **+$510** | Graph does NOT apply KS $9,160 exemption           |
| **CT** | $2,875.00  | $2,825.00 | **−$50**  | Graph missing line 5 phase-out recapture add-back  |
| **MD** | $2,723.88¹ | $2,875.88 | **+$152** | Graph missing something (verify)                   |
| **ND** | n/a        | $15.11    | **stub?** | $15 on $65k is implausible — likely broken graph   |

¹ MD hand-rolled state-only (local tax is separate).

Five of ten agree; five have material gaps. This means graph backend
wrapping is a **starting point**, not a finished wrap — wave 5 state
agents must cross-check the graph backend against a DOR primary-source
$65k Single worked example before committing to the wrap.

#### Implication for wave 5 (REVISED)

Wave 5's 21 remaining-state plugins should follow this decision tree:

1. **Probe**: run the graph backend on a $65k Single return.
2. **Verify**: compute the same return by hand against the state DOR's
   primary instructions.
3. **Three outcomes**:
   - **Exact match**: wrap with graph backend (like `wi.py`). Pin the
     state-specific output-field gaps in tests (state_taxable_income,
     state_tax_bracket, state_effective_tax_rate).
   - **Close match (within ~$5)**: wrap with graph backend AND document
     the discrepancy in the plugin docstring as a loud TODO. Pin the
     graph-backend number in tests so drift is visible.
   - **Material mismatch (>$5)**: hand-roll from DOR primary source,
     mirroring the CT/KS/KY/MN pattern. The graph backend is doing
     something wrong for this state — do NOT trust it.

The five wave-4 hand-rolled plugins (CT/KS/KY/MN/MD) are all correct as
written — do NOT convert them to graph wrappers. ND's $15.11 on $65k
specifically means the ND graph definition is broken or stubbed; wave
5's ND agent must hand-roll from ND DOR Form ND-EZ / ND-1 rather than
trust the graph output. Wave 4's pattern of 5-out-of-6 hand-rolled
recoveries from an enum that silently over-claims coverage was
well-calibrated.

#### Implication for wave 5

Wave 5's 21 remaining-state plugins should each use the **graph backend
wrapper pattern established by `wi.py`**, NOT the hand-rolled path used
by CT/KS/KY/MD/MN in wave 4. Hand-rolling should be reserved for:
- States where the graph-backend tax number diverges materially from
  DOR primary-source verification (wave 5 agents must verify).
- States where the graph backend produces a suspicious number (e.g. ND
  at $15.11 on $65k — flagged above).
- Cases where local taxes / county-level piggyback are required (MD
  was hand-rolled in wave 4 specifically because of the 22-county
  local tax table — not because the state tax itself was missing).

The five wave-4 hand-rolled plugins (CT/KS/KY/MN — not MD, which has
county local tax — plus optionally revisiting them) could eventually
be converted to graph-backend wrappers for lower maintenance. That is
**optional** — the hand-rolled plugins are correct, tested, and cite
DOR primary sources. Conversion is a cleanup task, not a correctness
fix.

## Decision rubric for future state plugins

When implementing a new state plugin, follow this decision tree:

1. **Probe first**. Call
   `tenforty.evaluate_return(year=2025, state='ST', filing_status='Single', w2_income=65000)`
   in a throwaway script. Three outcomes:

   a. **Returns a result**: wrap it. Copy the shape of `nc.py` (flat) or
      `oh.py` (graduated). Add a `test_tenforty_supports_st_ty2025` test
      that re-runs the probe. Done.

   b. **Raises `ValueError: OTS does not support`**: the default backend
      has no config. Try `backend="graph"` next.

   c. **Raises something else**: not a support gap — file a project issue
      and investigate before wrapping anything.

2. **Probe with graph backend**. Call
   `tenforty.evaluate_return(backend="graph", year=2025, state='ST', ...)`:

   a. **Returns a result**: wrap on the graph backend like `wi.py`. Pin
      each graph-gapped output field in tests so upstream fixes trip CI.

   b. **Raises**: hand-roll.

3. **Hand-roll**. Copy the shape of `ks.py` (flat) or `ct.py` / `mn.py`
   (graduated). Pull brackets + adds/subs + standard-deduction values
   from the state DOR's primary booklet (cite the URL in the docstring).

## Gatekeeper test pattern

Every hand-rolled plugin should include this test so the gap is
auto-detected if tenforty ever gains support:

```python
class TestTenfortyStillDoesNotSupportST:
    """When this test STARTS FAILING, tenforty has added ST for TY2025.
    Rewrite the plugin as a tenforty wrapper (copy nc.py or oh.py shape)
    and delete this test. Locks the plugin choice to current reality."""

    def test_tenforty_raises_on_st_ty2025(self):
        import pytest
        import tenforty
        with pytest.raises(ValueError, match="OTS does not support"):
            tenforty.evaluate_return(
                year=2025,
                state="ST",
                filing_status="Single",
                w2_income=65_000,
                num_dependents=0,
                standard_or_itemized="Standard",
                itemized_deductions=0,
            )
```

For WI (graph-backend wrap), the gatekeeper is slightly different: it
pins the `state_taxable_income == AGI` invariant so when tenforty fixes
the graph backend's std-ded pass-through, CI will fail and point to the
plugin for update.

## Why this matters

The pre-wave-4 assumption was "tenforty supports these 10-11 states; the
other 40 need hand-rolling." Wave 4 proved that's correct as a
*capability* count, but the `OTSState` enum hides this by listing all
states. Any future agent that reads the enum without probing will write
a broken wrapper.

Current authoritative supported-state list: read this doc, not the enum.

## See also

- `skill/scripts/states/_registry.py` — comments the gap in the
  registration for CT/KS/KY/MD/MN/WI.
- `skill/scripts/states/wi.py` — the one graph-backend wrapper.
- `skill/scripts/states/ks.py` / `ct.py` / `mn.py` / `ky.py` / `md.py` —
  hand-rolled recoveries from wave 4.
- `skill/reference/cp4-tenforty-verification.md` — original CP4 doc,
  predates the enum-gap discovery.
