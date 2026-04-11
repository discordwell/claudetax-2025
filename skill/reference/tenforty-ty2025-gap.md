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

### ⚠️ tenforty GRAPH BACKEND (partial, only WI confirmed)

WI has a `wi_form1_2025.json` graph definition. Invoked via
`tenforty.evaluate_return(backend="graph", state="WI", ...)`. Known gaps
of the graph backend:

- `state_tax_bracket` returns `0.0` (no marginal rate exposed).
- `state_effective_tax_rate` returns `0.0`.
- `state_taxable_income` echoes AGI (the WI sliding-scale standard
  deduction and personal exemption are applied internally but not
  reflected on the output side).

The final `state_total_tax` number IS authoritative — the bracket calc
is correct; only the display fields are gapped.

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
