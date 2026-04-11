"""Washington state plugin — long-term capital gains tax only.

Washington has no broad individual income tax. It DOES levy an excise tax on
long-term capital gains under Chapter 82.87 RCW (originally ESSB 5096, 2021;
upheld by the Washington Supreme Court in Quinn v. State, 2023). For TY2025 the
rate is 7% and the standard deduction is $278,000 per individual, married
couple, or domestic partnership, indexed annually for inflation.

Because the tax is narrow (LTCG only), this plugin is a partial-income-tax
implementation: `has_income_tax=False` (there's no BROAD income tax) and
`starting_point=StateStartingPoint.NONE`, but `compute()` still returns a real
dollar figure in `state_specific["state_tax"]` when the taxpayer has enough
long-term capital gains.

Sources (verified 2026-04-10):
- https://dor.wa.gov/taxes-rates/other-taxes/capital-gains-tax
  ("The standard deduction for 2025 is $278,000."
   "7% tax on the sale or exchange of long-term capital assets...")
- https://app.leg.wa.gov/RCW/default.aspx?cite=82.87 (statute)
- https://app.leg.wa.gov/RCW/default.aspx?cite=82.87.100 (allocation/sourcing)

Nonresident sourcing: RCW 82.87.100 allocates intangible-asset gains only to
the taxpayer's domicile. A nonresident selling stocks/bonds generally owes WA
nothing. Tangible-property gains can be sourced to WA if the property sat in
WA at sale time (or during the year/prior year for certain resident-status
sales). The MVP implementation short-circuits nonresidents to $0 and flags the
full sourcing work as a follow-up TODO.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from skill.scripts.models import (
    CanonicalReturn,
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


# ---------------------------------------------------------------------------
# Module-level constants (TY2025)
# ---------------------------------------------------------------------------
# TODO: move these to skill/reference/ty2025-constants.json under a
# washington_state section in a follow-up task. Keeping them inline for now so
# this plugin can land in the fan-out without touching reference/* (forbidden
# edit scope for the fan-out sub-agent).

WA_LTCG_RATE = Decimal("0.07")
"""7% flat rate on the sale/exchange of long-term capital assets.
Source: RCW 82.87.040 and https://dor.wa.gov/taxes-rates/other-taxes/capital-gains-tax
(verified 2026-04-10)."""

WA_LTCG_EXEMPT_THRESHOLD_TY2025 = Decimal("278000")
"""TY2025 standard deduction against long-term capital gains before the 7% rate
applies. Indexed annually for inflation; TY2024 was $270,000.
Source: https://dor.wa.gov/taxes-rates/other-taxes/capital-gains-tax
("The standard deduction for 2025 is $278,000. In 2024 the standard deduction
was $270,000 per year per individual.") verified 2026-04-10."""

_WA_FORM_NAME = "WA Capital Gains Excise Tax Return"
"""Return filed via the WA Department of Revenue My DOR portal. DOR does not
publish a separate form number — taxpayers file directly in the portal (or via
supported tax software). See
https://dor.wa.gov/taxes-rates/other-taxes/capital-gains-tax."""


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WashingtonPlugin:
    """StatePlugin implementation for Washington.

    WA has no broad income tax; this plugin only computes the 7% long-term
    capital gains excise tax under Chapter 82.87 RCW. See module docstring for
    sources.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        total_ltcg = _sum_long_term_capital_gains(return_)

        # TODO: Schedule D may carry real estate gains that flow through a
        # 1099-B. Real estate is EXCLUDED from the WA cap gains tax (RCW
        # 82.87.050), but we currently can't distinguish real-property sales
        # from securities sales on a 1099-B transaction alone. MVP assumption:
        # 1099-B transactions represent securities (typical brokerage use),
        # so no real-estate exclusion is applied here. Revisit when the
        # ingester learns to tag 1099-B rows with asset type, or when a
        # dedicated real-estate input is added to the canonical return.

        # TODO: other RCW 82.87.050 exclusions not yet modeled because the
        # canonical return does not surface them distinctly: retirement
        # accounts (handled upstream — 1099-R gains never hit 1099-B),
        # livestock held for farming use, depreciable business property under
        # IRC section 167, timber/timberland, commercial fishing privileges,
        # and auto dealership goodwill. These need dedicated inputs to flag.

        if residency != ResidencyStatus.RESIDENT:
            # RCW 82.87.100: intangibles are sourced only to the taxpayer's
            # domicile, and tangibles only if located in WA at sale (or
            # certain resident-at-sale edge cases). MVP short-circuit: a
            # nonresident (or part-year) taxpayer pays $0 and we flag the
            # return for the follow-up sourcing pass.
            # TODO: implement full RCW 82.87.100 sourcing for nonresidents and
            # part-year residents, including the tangible-property
            # "located in WA" test and the part-year domicile window.
            return StateReturn(
                state=self.meta.code,
                residency=residency,
                days_in_state=days_in_state,
                state_specific={
                    "state_tax": Decimal("0"),
                    "total_ltcg": total_ltcg,
                    "exempt_threshold": WA_LTCG_EXEMPT_THRESHOLD_TY2025,
                    "taxable_ltcg": Decimal("0"),
                    "rate": WA_LTCG_RATE,
                    "nonresident_sourcing_todo": True,
                    "notes": (
                        "Nonresident / part-year WA capital gains sourcing "
                        "under RCW 82.87.100 is not yet implemented. "
                        "Treating as $0 owed pending follow-up."
                    ),
                },
            )

        if total_ltcg > WA_LTCG_EXEMPT_THRESHOLD_TY2025:
            taxable_ltcg = total_ltcg - WA_LTCG_EXEMPT_THRESHOLD_TY2025
            state_tax = (taxable_ltcg * WA_LTCG_RATE).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:
            taxable_ltcg = Decimal("0")
            state_tax = Decimal("0")

        return StateReturn(
            state=self.meta.code,
            residency=residency,
            days_in_state=days_in_state,
            state_specific={
                "state_tax": state_tax,
                "total_ltcg": total_ltcg,
                "exempt_threshold": WA_LTCG_EXEMPT_THRESHOLD_TY2025,
                "taxable_ltcg": taxable_ltcg,
                "rate": WA_LTCG_RATE,
                "form": _WA_FORM_NAME,
            },
        )

    def apportion_income(
        self,
        return_: CanonicalReturn,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> IncomeApportionment:
        # WA has no broad income tax, so only capital gains can be "state
        # source" — and only for residents under MVP sourcing rules.
        if residency == ResidencyStatus.RESIDENT:
            state_cap_gains = _sum_long_term_capital_gains(return_)
        else:
            state_cap_gains = Decimal("0")

        return IncomeApportionment(
            state_source_wages=Decimal("0"),
            state_source_interest=Decimal("0"),
            state_source_dividends=Decimal("0"),
            state_source_capital_gains=state_cap_gains,
            state_source_self_employment=Decimal("0"),
            state_source_rental=Decimal("0"),
        )

    def render_pdfs(self, state_return: StateReturn, out_dir: Path) -> list[Path]:
        # WA does not distribute a PDF form — taxpayers file in My DOR.
        # Fan-out can add a data-pack JSON export later.
        return []

    def form_ids(self) -> list[str]:
        return [_WA_FORM_NAME]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sum_long_term_capital_gains(return_: CanonicalReturn) -> Decimal:
    """Sum all long-term capital gains visible on the canonical return.

    Sources:
    - 1099-B transactions with is_long_term=True:
      proceeds - cost_basis + adjustment_amount
    - 1099-DIV box 2a (total capital gain distributions — always long-term
      per IRS; see Form 1099-DIV instructions).
    """
    total = Decimal("0")

    for broker in return_.forms_1099_b:
        for txn in broker.transactions:
            if txn.is_long_term:
                total += txn.proceeds - txn.cost_basis + txn.adjustment_amount

    for div in return_.forms_1099_div:
        total += div.box2a_total_capital_gain_distributions

    return total


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = WashingtonPlugin(
    meta=StatePluginMeta(
        code="WA",
        name="Washington",
        # has_income_tax=False because WA has no BROAD income tax; the narrow
        # LTCG excise tax is still computed by this plugin.
        has_income_tax=False,
        starting_point=StateStartingPoint.NONE,
        dor_url="https://dor.wa.gov/",
        # Filing happens in My DOR; DOR does not publish a distinct free-file
        # landing page beyond the cap gains tax info page itself.
        free_efile_url="https://dor.wa.gov/taxes-rates/other-taxes/capital-gains-tax",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Washington has no broad income tax but levies a 7% excise tax "
            "on long-term capital gains above a TY2025 standard deduction of "
            "$278,000 (RCW 82.87)."
        ),
    )
)
