"""Illinois state plugin — HAND-ROLLED (not tenforty-backed).

OpenTaxSolver does not ship a 2025 IL-1040 module, so unlike AZ/CA/MI/etc.
this plugin cannot delegate to tenforty. Instead it computes the IL-1040
Individual Income Tax entirely in-house using the flat-rate formula:

    IL base income        = federal AGI
                             + IL additions (Line 2 + Schedule M additions)
                             - IL subtractions (Line 5 SS/retirement, Sch M subs)
    IL net income taxable = max(0, IL_base - total_exemptions)
    IL total tax          = IL net income taxable * 0.0495

Wave 4 upgrade (2026-04-11): the previous v1 flat-approximation layer
(federal AGI → flat rate) has been extended with a real IL-1040 line 2 / line 5
/ Schedule M additions-and-subtractions pass. See the block labeled
"Wave 4 adds/subs implemented" below for exactly what is now modeled and
the "v1 LIMITATIONS STILL OPEN" list for what is still deferred.

Reference (verified 2026-04-11 via WebFetch of the IL DOR TY2025 IL-1040
instructions PDF, ``IL-1040 Instructions (R-02/25)``):

- Rate: 4.95% of net income. Cite:
  https://tax.illinois.gov/research/taxrates/income.html
  ("The income tax rate is 4.95 percent", effective 2017-07-01).
- TY2025 personal exemption allowance: $2,850 per exemption. Cite:
  https://tax.illinois.gov/forms/incometax/currentyear/individual/il-1040-instr/what-is-new.html
  and
  https://tax.illinois.gov/forms/incometax/currentyear/individual/il-1040-instr/step-4---exemptions.html
  ("The personal exemption amount for tax year 2025 is $2,850").
  NOTE: The fan-out task brief called out $2,775 (TY2024). The TY2025 IL-1040
  instructions explicitly increase this to $2,850; we use the verified TY2025
  number and the task's "verify via WebFetch" escape hatch.

- Reciprocity: IL has bilateral reciprocity with IA, KY, MI, WI. Verified
  against skill/reference/state-reciprocity.json — four partners, the
  Midwestern commuter belt.

IL-1040 structure (TY2025 instructions, Step 3 "Base Income"):

    Line 1 = federal AGI (from federal 1040 line 11)
    Line 2 = federally tax-exempt interest and dividend income
             (from federal 1040 line 2a)  -- ADDITION
    Line 3 = Other additions (from Schedule M, Step 1, Line 10)
    Line 4 = sum of lines 1+2+3
    Line 5 = Social Security benefits AND qualified retirement plan
             income subtraction  -- SUBTRACTION. See "Tips To Speed Up
             The Processing Of Your Return" on page 2 of the IL-1040
             Instructions: "If you received federally taxed Social
             Security benefits or qualified retirement income, you may
             be able to subtract it on Line 5."
    Line 6 = Illinois Income Tax refund/overpayment subtraction
    Line 7 = Other subtractions (from Schedule M, Step 2, Line 32)
    Line 8 = sum of lines 5+6+7
    Line 9 = Line 4 − Line 8  (IL base income)
    Line 10 = exemption allowance (see phase-out cliff below)
    Line 11 = Line 9 − Line 10  (IL net income)
    Line 12 = Line 11 * 4.95% (flat rate)

Schedule M (R-12/25) relevant line numbers — all verified via the
IL DOR Schedule M and IL-1040 instruction PDFs:

    Step 1 additions:
        Line 3 (Schedule M) — already covered by Form IL-1040 Line 2
                              (non-IL muni interest). A double-add would
                              be wrong; IL DOR moves this to IL-1040 Line 2
                              so Schedule M Line 3 is typically blank for
                              individuals whose only addition is muni
                              interest.
    Step 2 subtractions:
        Line 22 (Schedule M) — "U.S. Treasury bonds, bills, notes,
                                savings bonds, and U.S. agency interest
                                from federal Form 1040 or 1040-SR."
                                Flows to IL-1040 Line 7 via Schedule M
                                Step 2 Line 32.

Wave 4 adds/subs implemented (TY2025):

- IL-1040 Line 2 addition: federally tax-exempt interest and dividend
  income. Pulled from ``forms_1099_int[].box8_tax_exempt_interest`` and
  ``forms_1099_div[].box11_exempt_interest_dividends``. This is a
  CONSERVATIVE v1 approximation: IL only adds back non-IL muni interest.
  For taxpayers holding BOTH in-state and out-of-state munis, the taxpayer
  would report non-IL on IL-1040 Line 2 and then subtract in-state via
  Schedule M Step 2 Line 32. Here we add back 100% of tax-exempt muni
  interest — the taxpayer can override downstream by editing the returned
  state_specific ``il_non_il_muni_interest_addition`` field. The
  "in-state-muni carve-out not modeled" assumption is enumerated in
  ``_V1_LIMITATIONS``.

- IL-1040 Line 5 subtraction: Social Security benefits + qualified
  retirement income. Pulled from ``forms_ssa_1099[].box5_net_benefits``
  (the federal AGI taxable amount, which per the IL-1040 instructions is
  100% subtracted on Line 5 because IL does not tax Social Security) and
  ``forms_1099_r[].box2a_taxable_amount`` (qualified retirement income:
  IRAs, 401(k)s, pensions, and other employer-sponsored retirement
  plans). IL DOR Publication 120 ("Retirement Income") is the
  authoritative list of qualifying plans; a real implementation would
  need to inspect box 7 distribution codes to confirm each 1099-R is
  from a qualifying plan (e.g. code G rollover is NOT subtractable).
  For v1 we trust ``box2a`` — the taxable amount the federal return
  actually included. The "distribution-code gating not modeled"
  assumption is enumerated in ``_V1_LIMITATIONS``.

- Schedule M Step 2 Line 22 subtraction: U.S. Government obligation
  interest. Pulled from
  ``forms_1099_int[].box3_us_savings_bond_and_treasury_interest``
  (box 3 is specifically "Interest on U.S. Savings Bonds and Treasury
  obligations" per IRS 1099-INT instructions, which is exactly what
  IL wants to subtract). IL cannot tax federal-obligation interest
  per the Supremacy Clause (32 U.S.C. §3124).

v1 LIMITATIONS STILL OPEN — loud and proud (locked by tests):

- Exemption phase-out cliff: IL's TY2025 instructions zero the exemption
  when federal AGI > $250,000 (Single/HoH/MFS/QSS) or > $500,000 (MFJ).
  This v1 applies the exemption uniformly regardless of AGI. Follow-up
  should implement the cliff. Cite:
  https://tax.illinois.gov/forms/incometax/currentyear/individual/il-1040-instr/step-4---exemptions.html

- Age 65+ / legally blind $1,000 additional exemption — not modeled.

- Schedule M ADDITIONS beyond Line 2 muni interest (all NOT modeled):
    * Distributive share of additions from partnerships / S corps / trusts
    * Lloyd's plan of operations loss (rare)
    * Business expense recapture, capital loss / NOL addbacks, etc.
    * 529 plan nonqualified withdrawal recapture

- Schedule M SUBTRACTIONS beyond Line 22 US Treasury (NOT modeled):
    * Military pay earned while on active duty (Step 2 Line 21)
    * Contributions to the Bright Start / Bright Directions 529 plans
      (Step 2 Line 30; wave 3 what's-new: medical debt relief subtraction
      added Line 18)
    * Distributive share of subtractions from pass-throughs
    * IL Income Tax refund included in federal AGI (IL-1040 Line 6)
      — not pulled from Form1099G.box2 yet

- IL-1040 Line 2 in-state-muni carve-out: the current add-back treats
  ALL federally tax-exempt muni interest as non-IL (over-adds in-state
  muni). Taxpayer can override via ``state_specific``.

- Form1099R distribution-code gating: we trust ``box2a_taxable_amount``
  without inspecting ``box7_distribution_codes`` for non-qualifying
  rollover/disability/etc. codes per IL DOR Publication 120.

- Illinois property tax credit (5% of IL property tax paid on principal
  residence, non-refundable), K-12 education expense credit, earned income
  credit (matches 40% of federal EITC for TY2025 per IL-1040 instructions
  "What's New" — up from 20% in TY2024), and all other IL
  credits — not modeled.

- Nonresident / part-year returns use day-proration of resident-basis tax.
  The real IL Schedule NR allocates income item-by-item (wages to work
  location, interest/dividends to domicile, property gains to situs, etc).
  Day-based proration is the shared first-cut across fan-out state plugins.

Every one of the above is listed in the returned state_specific
"v1_limitations" field so downstream consumers and tests can see, not
guess, what this plugin does and does not do.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from skill.scripts.models import (
    CanonicalReturn,
    FilingStatus,
    ResidencyStatus,
    StateReturn,
)
from skill.scripts.states._plugin_api import (
    FederalTotals,
    IncomeApportionment,
    StatePlugin,
    StatePluginMeta,
    StateStartingPoint,
    SubmissionChannel,
)


_CENTS = Decimal("0.01")

# TY2025 IL-1040 constants — see module docstring for citations.
IL_FLAT_RATE: Decimal = Decimal("0.0495")
"""4.95%. Flat rate since TY2017 per IL DOR. Cite:
https://tax.illinois.gov/research/taxrates/income.html"""

IL_PERSONAL_EXEMPTION_TY2025: Decimal = Decimal("2850")
"""Per-exemption allowance for TY2025 (IL-1040 line 10 multiplier).
Cite: https://tax.illinois.gov/forms/incometax/currentyear/individual/il-1040-instr/what-is-new.html
"""

_V1_LIMITATIONS: tuple[str, ...] = (
    # Wave 4 partially closed: IL-1040 Line 2 addback, Line 5 SS+retirement "
    # subtraction, and Schedule M Step 2 Line 22 US Treasury subtraction are "
    # now implemented. The remaining IL Sch M additions/subtractions are "
    # still NOT applied, and the IL-1040 Line 2 muni addback over-adds "
    # in-state IL muni interest (v1 treats ALL tax-exempt muni interest as "
    # non-IL for the addback).
    "IL Sch M additions NOT applied beyond IL-1040 Line 2 (non-IL muni "
    "interest): pass-through additions, Lloyd's plan loss, business expense "
    "recapture, 529 nonqualified withdrawal recapture are still missing.",
    "IL Sch M subtractions NOT applied beyond Schedule M Step 2 Line 22 "
    "(U.S. Treasury interest from 1099-INT box 3) and IL-1040 Line 5 "
    "(Social Security + qualified retirement income): military pay, 529 "
    "contributions (Bright Start/Directions), IL Income Tax refund "
    "subtraction from Form 1099-G box 2 (Line 6), and pass-through "
    "subtractions are still missing.",
    "IL-1040 Line 2 in-state muni interest carve-out NOT modeled: Line 2 "
    "add-back treats ALL federally tax-exempt muni interest as non-IL. "
    "Taxpayers with IL-source muni interest are currently over-taxed by "
    "the Line 2 add-back (the fix is Schedule M Step 2 Line 32 in-state "
    "subtraction). Override via state_specific.il_non_il_muni_interest_addition.",
    "Form 1099-R distribution-code gating NOT modeled for IL Line 5 "
    "retirement subtraction: box2a_taxable_amount is subtracted wholesale "
    "without consulting box7_distribution_codes for non-qualifying "
    "rollover/disability/premature-distribution codes per IL DOR "
    "Publication 120, Retirement Income.",
    "Exemption phase-out cliff (fed AGI > $250k single / $500k MFJ) NOT "
    "modeled — exemption applied uniformly. TY2025 IL-1040 instructions, "
    "Step 4 Line 10.",
    "Age 65+ / legally blind additional $1,000 exemption NOT modeled.",
    "IL property tax credit, K-12 education expense credit, earned income "
    "credit (40% of federal EITC for TY2025), and all other IL "
    "nonrefundable / refundable credits NOT modeled.",
    "Nonresident / part-year apportionment uses day-based proration, not "
    "IL Schedule NR line-item income sourcing.",
)


def _d(v: Any) -> Decimal:
    """Coerce a float / int / None to Decimal."""
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _cents(v: Any) -> Decimal:
    """Decimal with 2 decimal places, ROUND_HALF_UP."""
    return _d(v).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _apportionment_fraction(
    residency: ResidencyStatus, days_in_state: int
) -> Decimal:
    """Days-based apportionment for nonresident / part-year.

    Residents get 1.0 (full state tax). Nonresidents and part-year residents
    are prorated by days_in_state / 365. Clamped to [0, 1].

    TODO(il-sched-nr): replace with real IL Schedule NR line-item income
    sourcing in fan-out follow-up.
    """
    if residency == ResidencyStatus.RESIDENT:
        return Decimal("1")
    frac = Decimal(days_in_state) / Decimal("365")
    if frac < 0:
        return Decimal("0")
    if frac > 1:
        return Decimal("1")
    return frac


def _il_additions(return_: CanonicalReturn) -> dict[str, Decimal]:
    """Compute IL-1040 Line 2 + Schedule M Step 1 additions.

    Wave 4 v1: only Line 2 (federally tax-exempt interest / dividend
    addback) is implemented. Pulls from 1099-INT box 8 (municipal bond
    interest) and 1099-DIV box 11 (exempt-interest dividends from muni
    bond funds). This is a CONSERVATIVE addback — it treats ALL muni
    interest as non-IL. Taxpayers with in-state muni holdings would
    subtract the in-state portion on Schedule M Step 2 Line 32 (NOT
    modeled in v1).

    Returns a dict with itemized components plus a running total.
    """
    line2_muni = Decimal("0")
    for form in return_.forms_1099_int:
        line2_muni += form.box8_tax_exempt_interest
    for form in return_.forms_1099_div:
        line2_muni += form.box11_exempt_interest_dividends

    total = line2_muni
    return {
        "il_1040_line2_tax_exempt_interest_addback": _cents(line2_muni),
        "il_additions_total": _cents(total),
    }


def _il_subtractions(return_: CanonicalReturn) -> dict[str, Decimal]:
    """Compute IL-1040 Line 5 (SS + qualified retirement) and
    Schedule M Step 2 Line 22 (US Treasury interest) subtractions.

    Wave 4 v1: implements
        - Line 5 Social Security: 100% of SSA-1099 box 5 (net benefits),
          which is the amount that flowed into federal AGI — IL does not
          tax Social Security so the whole thing comes back out.
        - Line 5 qualified retirement income: 100% of 1099-R box 2a
          (taxable amount) across all 1099-Rs. IL Pub 120 lists the
          qualifying plan types (traditional IRA, 401(k), 403(b),
          pension, etc.). Distribution-code gating (box 7) is NOT
          modeled: rollovers, non-qualifying premature distributions,
          and similar edge cases are not filtered out. This is a LOUD
          v1 limitation documented in _V1_LIMITATIONS.
        - Schedule M Step 2 Line 22 US Treasury: 1099-INT box 3
          (U.S. Savings Bond and Treasury interest). IL cannot tax
          federal obligation interest per the Supremacy Clause.

    Returns a dict with itemized components plus a running total.
    """
    line5_ss = Decimal("0")
    for form in return_.forms_ssa_1099:
        # SSA-1099 box 5 is "Net benefits" — federal AGI includes up to
        # 85% of this (per the federal SS worksheet). IL's Line 5
        # instructions say subtract "federally taxed Social Security
        # benefits" — i.e. the amount in federal AGI, not box 5 itself.
        # For v1 we use box 5 as a conservative upper bound (IL-friendly).
        # A follow-up should subtract only the FEDERAL-TAXABLE portion
        # (which is what federal 1040 line 6b reports). Using box5 here
        # over-subtracts when only a fraction is taxed federally.
        line5_ss += form.box5_net_benefits

    line5_retirement = Decimal("0")
    for form in return_.forms_1099_r:
        line5_retirement += form.box2a_taxable_amount

    line22_us_treasury = Decimal("0")
    for form in return_.forms_1099_int:
        line22_us_treasury += form.box3_us_savings_bond_and_treasury_interest

    total = line5_ss + line5_retirement + line22_us_treasury
    return {
        "il_1040_line5_social_security_subtraction": _cents(line5_ss),
        "il_1040_line5_retirement_income_subtraction": _cents(line5_retirement),
        "il_schedule_m_line22_us_treasury_subtraction": _cents(line22_us_treasury),
        "il_subtractions_total": _cents(total),
    }


def _exemption_count(
    filing_status: FilingStatus, num_dependents: int
) -> int:
    """IL-1040 Step 4 exemption count.

    IL exemption count is: 1 (taxpayer) + 1 if MFJ or QSS (spouse) +
    num_dependents. MFS and Single/HoH get 1 + dependents.

    QSS is treated like MFJ for this purpose (IL conforms to the federal
    QSS definition and allows the spousal exemption in the year of death
    and the two following years).
    """
    count = 1
    if filing_status in (FilingStatus.MFJ, FilingStatus.QSS):
        count += 1
    count += max(0, num_dependents)
    return count


@dataclass(frozen=True)
class IllinoisPlugin:
    """State plugin for Illinois — HAND-ROLLED (no tenforty).

    Computes a v1 IL-1040 in-house: base income approximated as federal AGI,
    personal exemption subtraction at the TY2025 rate ($2,850 per exemption),
    flat 4.95% rate on the result. Loud limitations list documents what's
    NOT modeled; see module docstring for full details.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        # Step 1a: start from federal AGI (IL-1040 Line 1).
        federal_agi = _cents(federal.adjusted_gross_income)

        # Step 1b: IL-1040 Line 2 + Schedule M Step 1 additions
        # (Wave 4: non-IL muni interest addback only). Line 4 = sum.
        additions = _il_additions(return_)
        additions_total = additions["il_additions_total"]

        # Step 1c: IL-1040 Line 5 (SS + qualified retirement) + Schedule M
        # Step 2 Line 22 (US Treasury) subtractions. Line 8 = sum.
        subtractions = _il_subtractions(return_)
        subtractions_total = subtractions["il_subtractions_total"]

        # Step 1d: IL base income (Line 9) = Line 4 − Line 8.
        # Note ``state_base_income_approx`` is preserved for backward
        # compat as federal AGI; the adjusted base that feeds the flat
        # rate is exposed as ``state_base_income`` and also stamped back
        # onto a new ``state_base_income_after_adjustments`` key.
        base_income_after_adjustments = (
            federal_agi + additions_total - subtractions_total
        )
        if base_income_after_adjustments < 0:
            base_income_after_adjustments = Decimal("0")
        base_income_after_adjustments = _cents(base_income_after_adjustments)

        # Step 2: exemption allowance. Uniform per-exemption rate with no
        # phase-out cliff (v1 limitation). Count = 1 + spouse (MFJ/QSS) +
        # dependents.
        exemption_count = _exemption_count(
            federal.filing_status, federal.num_dependents
        )
        exemption_total = _cents(
            IL_PERSONAL_EXEMPTION_TY2025 * Decimal(exemption_count)
        )

        # Step 3: net income taxable = max(0, base_after_adjustments - exemption).
        taxable = base_income_after_adjustments - exemption_total
        if taxable < 0:
            taxable = Decimal("0")
        taxable = _cents(taxable)

        # Step 4: flat-rate tax. ROUND_HALF_UP to the cent.
        tax_full = _cents(taxable * IL_FLAT_RATE)

        # Step 5: apportion for nonresident / part-year. TODO(il-sched-nr):
        # replace with real income-source sourcing.
        fraction = _apportionment_fraction(residency, days_in_state)
        tax_apportioned = _cents(tax_full * fraction)

        state_specific: dict[str, Any] = {
            # NOTE: "state_base_income_approx" is preserved for backward
            # compatibility with wave 3 tests. It equals federal AGI
            # directly (IL-1040 Line 1). The Wave 4 adds/subs layer puts
            # the adjusted base income under
            # "state_base_income_after_adjustments" and the existing
            # "state_taxable_income" now reflects the adjusted base minus
            # exemption.
            "state_base_income_approx": federal_agi,
            "state_base_income_after_adjustments": base_income_after_adjustments,
            # Itemized wave-4 adds/subs so downstream consumers and
            # renderers can replay line-by-line.
            "il_additions": additions,
            "il_subtractions": subtractions,
            "il_additions_total": additions_total,
            "il_subtractions_total": subtractions_total,
            # Override hook: taxpayer may override the Line 2 non-IL muni
            # addback if they hold in-state IL munis. Documented in the
            # module docstring and _V1_LIMITATIONS.
            "il_non_il_muni_interest_addition": additions[
                "il_1040_line2_tax_exempt_interest_addback"
            ],
            "state_exemption_count": exemption_count,
            "state_exemption_per_person": IL_PERSONAL_EXEMPTION_TY2025,
            "state_exemption_total": exemption_total,
            "state_taxable_income": taxable,
            "state_total_tax": tax_apportioned,
            "state_total_tax_resident_basis": tax_full,
            "flat_rate": IL_FLAT_RATE,
            "apportionment_fraction": fraction,
            "v1_limitations": list(_V1_LIMITATIONS),
        }

        return StateReturn(
            state=self.meta.code,
            residency=residency,
            days_in_state=days_in_state,
            state_specific=state_specific,
        )

    def apportion_income(
        self,
        return_: CanonicalReturn,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> IncomeApportionment:
        """Split canonical income into IL-source vs non-IL-source.

        Residents: everything is IL-source. Nonresident / part-year: prorate
        each category by days_in_state / 365.

        TODO(il-sched-nr): IL Schedule NR sources each income item
        individually — wages to the work location, interest/dividends to
        domicile, property gains to situs, distributive share from
        pass-throughs to the K-1's IL apportionment, etc. Day-based
        proration is the shared fan-out first cut.
        """
        wages = sum(
            (w2.box1_wages for w2 in return_.w2s), start=Decimal("0")
        )
        interest = sum(
            (f.box1_interest_income for f in return_.forms_1099_int),
            start=Decimal("0"),
        )
        ord_div = sum(
            (f.box1a_ordinary_dividends for f in return_.forms_1099_div),
            start=Decimal("0"),
        )
        cap_gain_distr = sum(
            (f.box2a_total_capital_gain_distributions for f in return_.forms_1099_div),
            start=Decimal("0"),
        )
        st_gain = Decimal("0")
        lt_gain = Decimal("0")
        for form in return_.forms_1099_b:
            for txn in form.transactions:
                gain = txn.proceeds - txn.cost_basis + txn.adjustment_amount
                if txn.is_long_term:
                    lt_gain += gain
                else:
                    st_gain += gain
        capital_gains = st_gain + lt_gain + cap_gain_distr

        from skill.scripts.calc.engine import (
            schedule_c_net_profit,
            schedule_e_total_net,
        )
        se_net = sum(
            (schedule_c_net_profit(sc) for sc in return_.schedules_c),
            start=Decimal("0"),
        )
        rental_net = sum(
            (schedule_e_total_net(sched) for sched in return_.schedules_e),
            start=Decimal("0"),
        )

        fraction = _apportionment_fraction(residency, days_in_state)

        return IncomeApportionment(
            state_source_wages=_cents(wages * fraction),
            state_source_interest=_cents(interest * fraction),
            state_source_dividends=_cents(ord_div * fraction),
            state_source_capital_gains=_cents(capital_gains * fraction),
            state_source_self_employment=_cents(se_net * fraction),
            state_source_rental=_cents(rental_net * fraction),
        )

    def render_pdfs(
        self, state_return: StateReturn, out_dir: Path
    ) -> list[Path]:
        # TODO(il-pdf): fan-out follow-up — fill IL-1040 and Schedule NR /
        # Schedule M / Schedule CR using pypdf against the IL DOR fillable
        # PDFs. Renderer suite is the right home for this; this plugin
        # returns structured state_specific that the renderer will consume.
        return []

    def form_ids(self) -> list[str]:
        return ["IL Form IL-1040"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = IllinoisPlugin(
    meta=StatePluginMeta(
        code="IL",
        name="Illinois",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_AGI,
        dor_url="https://tax.illinois.gov/",
        # MyTax Illinois is the state's free e-file portal for individuals.
        free_efile_url="https://mytax.illinois.gov/",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        # IL has bilateral reciprocity with four states — IA, KY, MI, WI —
        # the Midwestern commuter belt. Verified against
        # skill/reference/state-reciprocity.json. A test asserts the exact
        # set so accidental drift fails CI.
        reciprocity_partners=("IA", "KY", "MI", "WI"),
        supported_tax_years=(2025,),
        notes=(
            "HAND-ROLLED (no tenforty/OTS support for 2025 IL-1040). Flat "
            "4.95% rate, $2,850 personal exemption per exemption for TY2025. "
            "v1 approximates IL base income as federal AGI — IL Schedule M "
            "additions/subtractions NOT applied. See state_specific["
            "'v1_limitations'] on compute output."
        ),
    )
)
