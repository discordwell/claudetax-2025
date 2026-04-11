# CP4 — tenforty OBBBA verification findings

**Date:** 2026-04-10
**Library version:** `tenforty==2025.8` (pypi)
**Python:** 3.12.12
**Question:** Can tenforty be wrapped as our reference calc engine for TY2025, given the OBBBA retroactive changes?

## TL;DR

**Yes, with a thin patch layer for CTC and OBBBA-specific additions.** tenforty has current OBBBA-adjusted TY2025 numbers for standard deductions, brackets, SE tax, LTCG worksheet, and Additional Medicare Tax out of the box. The Child Tax Credit is NOT exposed through tenforty's high-level API, and the OBBBA senior deduction / Form 4547 / Schedule 1-A additions need our own code.

**Architecture decision:** Wrap + patch. tenforty provides the reference baseline; our calc engine layers overrides for the few gaps.

## Verification scenarios

All runs used `tenforty.evaluate_return(year=2025, standard_or_itemized='Standard', ...)`. "Implied deduction" = `AGI - taxable_income`.

| # | Scenario | AGI | Taxable Income | Implied Deduction | Expected OBBBA std ded | Federal tax | Total tax | Notes |
|---|---|---|---|---|---|---|---|---|
| 1 | Single, $65k W-2 | 65,000 | 49,250 | **15,750** ✓ | 15,750 | 5,755 | 5,755 | Matches IRS tax tables (bracket-formula gives 5,749; IRS tables use $50 midpoints → 5,755) |
| 2 | MFJ, $150k W-2 | 150,000 | 118,500 | **31,500** ✓ | 31,500 | 15,898 | 15,898 | Hand-check: 2385 + 8772 + (22% × 21,550) = 15,898 exact match |
| 3 | HoH, $80k W-2 | 80,000 | 56,375 | **23,625** ✓ | 23,625 | 6,425 | 6,425 | Hand-check: 1700 + (12% × 39,375) = 6,425 exact match |
| 4 | MFS, $65k W-2 | 65,000 | 49,250 | **15,750** ✓ | 15,750 | 5,755 | 5,755 | Correct — MFS/Single use same brackets below 35% |
| 5a | Single, $60k, 0 deps | 60,000 | 44,250 | 15,750 ✓ | 15,750 | 5,075 | 5,075 | |
| 5b | Single, $60k, **1 dep** | 60,000 | 44,250 | 15,750 | 15,750 | **5,075** | **5,075** | **NO CTC APPLIED** — tax identical to 5a |
| 6 | Single, $50k SE | 46,467.61 | 30,717.61 | 15,750 | 15,750 | 3,449 | 10,513.77 | AGI correctly reduced by ½ SE tax (3,532.39); SE tax included in total_tax |
| 7 | Single, $40k W-2 + $5k LTCG | 45,000 | 29,250 | 15,750 | 15,750 | 2,675 | 2,675 | LTCG at 0% rate (under $48,350 single threshold) |
| 8 | MFJ, $500k W-2 | 500,000 | 468,500 | 31,500 ✓ | 31,500 | 104,046 | 106,296 | Federal tax exact match. Total - federal = 2,250 = 0.9% × (500k - 250k) → **Additional Medicare Tax working** |
| 9 | Single, $65k W-2, CA | 65,000 | 49,250 | 15,750 ✓ | 15,750 | 5,755 | 5,755 | **State pass-through works**: state_total_tax=1,975, state_bracket=8% for CA |

## What's working

- ✅ **Standard deductions OBBBA-adjusted**: $15,750 single/MFS, $31,500 MFJ, $23,625 HoH
- ✅ **Federal ordinary brackets for TY2025**: exact hand-check match at Single $49,250, MFJ $118,500, HoH $56,375, MFJ $468,500
- ✅ **IRS tax tables used for income < $100k** (explains the $5-$6 delta between bracket formulas and actual output)
- ✅ **Schedule SE**: 50k SE → 3,532.39 half-SE-tax adjustment, 7,064.77 SE tax, correct to the penny
- ✅ **LTCG worksheet**: 0% rate correctly applied when taxable income is under the threshold
- ✅ **Additional Medicare Tax (Form 8959)**: 0.9% over $250k MFJ, appears in `total_tax - federal_income_tax` delta
- ✅ **California state**: state AGI, brackets, tax all populated. We'll verify the other 9 tenforty-supported states (AZ, MA, MI, NC, NJ, NY, OH, PA, VA) in CP5 state plugin work
- ✅ **Multi-income scenarios**: W-2 + LTCG + SE combine correctly

## What's NOT working

- ❌ **Child Tax Credit**: tenforty's high-level API has `num_dependents` and `dependent_exemptions` fields, but passing `num_dependents=1` to a `$60k W-2 Single` return produces **identical** tax to `num_dependents=0`. The $2,200 OBBBA-raised CTC is not applied. This is a real gap, not a documentation issue — tenforty's `TaxReturnInput` model simply doesn't expose the child-specific data OTS needs for CTC.
- ❌ **OBBBA senior deduction (+$6,000 age 65+)**: not tested here directly, but tenforty's API has no "is_65_or_older" flag. Need our own code.
- ❌ **Form 4547 Trump Account election**: new under OBBBA. Not in tenforty.
- ❌ **Schedule 1-A (tips / overtime deductions)**: new under OBBBA. Not in tenforty.
- ❌ **Many exemption / adjustment line items**: the high-level API is coarse (just `schedule_1_income` as a single number). For fine-grained deductions we either compute adjustments ourselves and pass the net or use tenforty's lower-level `OTSField` / `OTSForm` layer.

## Architecture decision: wrap + patch

**Baseline**: tenforty produces the authoritative federal calc (AGI, ordinary tax, SE tax, LTCG, AMT if present, Add'l Medicare, and the 10 supported state returns).

**Patch layer** (our own code):
1. **CTC / ACTC** — compute from dependents list, subtract from nonrefundable credits, add refundable portion to payments.
2. **OBBBA senior deduction** — compute from taxpayer/spouse age at year-end, stack on top of standard deduction before calling tenforty (reduce the number we pass via `schedule_1_income` or use itemized path as an adjustment).
3. **Form 4547 Trump Account deduction** — compute, stack as adjustment.
4. **Schedule 1-A tips/overtime** — compute, stack as adjustment.
5. **NIIT (Form 8960)** — needs verification (may already be in tenforty via `total_tax`); if not, compute.
6. **QBI 8995-A complex path** — tenforty does the simplified 8995 path; 8995-A phase-in / SSTB logic needs our own code.
7. **Multi-state apportionment** beyond the 10 supported states — our own code + per-state plugin.

## Next steps

This verification unblocks CP5 (state plugin API) and CP6 (ingestion). The calc engine itself will be built in the fan-out phase, implemented as `skill/scripts/calc/engine.py` with:

```python
def compute(canonical_return: CanonicalReturn) -> CanonicalReturn:
    tf_result = _call_tenforty(canonical_return)
    result = _apply_patch_layer(canonical_return, tf_result)
    return result
```

Where `_apply_patch_layer` handles the gaps documented above.

## Verification tests

Automated tests that lock these findings live in `skill/tests/test_tenforty_obbba_verification.py`. They run on every pytest invocation and will fail loudly if a future tenforty update changes the OBBBA numbers underneath us.
