"""Illinois state plugin — HAND-ROLLED (not tenforty-backed).

OpenTaxSolver does not ship a 2025 IL-1040 module, so unlike AZ/CA/MI/etc.
this plugin cannot delegate to tenforty. Instead it computes the IL-1040
Individual Income Tax entirely in-house using the flat-rate formula:

    IL net income taxable = max(0, IL_base - total_exemptions)
    IL total tax          = IL net income taxable * 0.0495

Reference (verified 2026-04-11 via WebFetch of the IL DOR TY2025 IL-1040
instructions):

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

v1 LIMITATIONS — loud and proud (locked by tests):

The IL "base income" that feeds line 9 of the IL-1040 is NOT federal AGI
directly. It's federal AGI +/- Schedule M additions/subtractions. This v1
skips Schedule M entirely and uses federal AGI as a proxy. That means the
following TY2025 IL items are NOT modeled yet:

- Schedule M ADDITIONS:
    * Federally tax-exempt interest and dividend income from non-IL
      municipal bonds (line 1 of IL-1040, from Sch M line 1)
    * Distributive share of additions from partnerships / S corps / trusts
    * Lloyd's plan of operations loss (rare)
    * Business expense recapture, capital loss / NOL addbacks, etc.

- Schedule M SUBTRACTIONS:
    * Federally taxable retirement income from qualified employee benefit
      plans (IRAs, 401(k)s, pensions) — IL is a "retirement-friendly" state
      and excludes most of this. Big miss for retirees.
    * Social Security benefits included in federal AGI (IL does not tax SS)
    * Illinois state income tax refund included in federal AGI
    * U.S. Government interest (Treasuries, Savings Bonds) — IL cannot tax
      federal-obligation interest per the Supremacy Clause
    * Military pay earned while on active duty
    * Contributions to the Bright Start / Bright Directions 529 plans
    * Distributive share of subtractions from pass-throughs

- Exemption phase-out cliff: IL's TY2025 instructions zero the exemption
  when federal AGI > $250,000 (Single/HoH/MFS/QSS) or > $500,000 (MFJ).
  This v1 applies the exemption uniformly regardless of AGI. Follow-up
  should implement the cliff. Cite:
  https://tax.illinois.gov/forms/incometax/currentyear/individual/il-1040-instr/step-4---exemptions.html

- Age 65+ / legally blind $1,000 additional exemption — not modeled.

- Illinois property tax credit (5% of IL property tax paid on principal
  residence, non-refundable), K-12 education expense credit, earned income
  credit (matches 20% of federal EITC for TY2025), and all other IL
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
    "IL Sch M additions/subtractions NOT applied — base income approximated "
    "as federal AGI directly. Federally-tax-exempt non-IL muni interest is "
    "not added back; IL retirement subtraction, Social Security subtraction, "
    "U.S. Government interest subtraction, and state tax refund subtraction "
    "are not applied.",
    "Exemption phase-out cliff (fed AGI > $250k single / $500k MFJ) NOT "
    "modeled — exemption applied uniformly. TY2025 IL-1040 instructions, "
    "Step 4 Line 10.",
    "Age 65+ / legally blind additional $1,000 exemption NOT modeled.",
    "IL property tax credit, K-12 education expense credit, earned income "
    "credit (20% of federal EITC), and all other IL nonrefundable / "
    "refundable credits NOT modeled.",
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
        # Step 1: IL base income. v1 approximation = federal AGI. A real
        # IL-1040 starts from federal AGI and then applies Schedule M
        # additions/subtractions — not modeled here. See _V1_LIMITATIONS.
        base_income = _cents(federal.adjusted_gross_income)

        # Step 2: exemption allowance. Uniform per-exemption rate with no
        # phase-out cliff (v1 limitation). Count = 1 + spouse (MFJ/QSS) +
        # dependents.
        exemption_count = _exemption_count(
            federal.filing_status, federal.num_dependents
        )
        exemption_total = _cents(
            IL_PERSONAL_EXEMPTION_TY2025 * Decimal(exemption_count)
        )

        # Step 3: net income taxable = max(0, base - exemption).
        taxable = base_income - exemption_total
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
            "state_base_income_approx": base_income,
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
