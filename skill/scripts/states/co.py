"""Colorado (CO) state plugin — TY2025.

Colorado is NOT supported by tenforty/OpenTaxSolver (``OTS does not support
2025/CO_Form104``), so this module hand-rolls the CO Form DR 0104 calculation.

Colorado has a **flat** individual income tax. The **permanent** statutory
rate is 4.40% (Colo. Rev. Stat. §39-22-104(1.7)). For tax years 2024 through
2034, C.R.S. §39-22-627 authorizes a **temporary** income tax rate reduction
as one of the TABOR refund mechanisms when TABOR surplus exceeds specific
thresholds; for example, TY2024 used a temporarily reduced 4.25% rate because
the TY2023 TABOR surplus triggered the mechanism.

**TY2025 rate = 4.40%** — the temporary reduction mechanism did **not** fire
for TY2025 because the remaining excess state revenues after the property tax
exemption reimbursement (~$111.2M) fell below the $300 million threshold
required to activate the income tax rate reduction mechanism (SB24-228).

Sources (verified 2026-04-11):

- Colorado OSA "Schedule of TABOR Revenue — Fiscal Year 2025 Performance
  Audit" (October 2025, report 2557P), page 18 "Temporary Income Tax Rate
  Reduction" and page 19 "At June 30, 2025, ... approximately $182.1 million
  of this refund liability will be refunded through the property tax
  exemptions reimbursement. The remaining approximately $111.2 million of
  the Fiscal Year 2025 excess state revenues is expected to trigger the
  sales tax refund mechanism."
  https://content.leg.colorado.gov/sites/default/files/documents/audits/2557p_schedule_of_tabor_revenue_fy_25.pdf
  (triggering thresholds — the rate reduction only activates when remaining
  excess state revenues exceed $300M; at ~$111.2M, only the six-tier sales
  tax refund mechanism fires)

- Colorado Legislative Council Staff, "SB 25-138 Fiscal Note" (May 22, 2025):
  "For tax years 2025 through 2034, the bill reduces the state income tax
  rate from 4.40 percent to 4.25 percent." SB25-138 was postponed
  indefinitely on 2025-02-27, confirming the permanent rate remains 4.40%.
  https://leg.colorado.gov/sites/default/files/documents/2025A/bills/fn/2025a_sb138_f1.pdf

- Colorado Department of Revenue, "DR 0104 — 2025 Colorado Individual
  Income Tax Return" (10/03/25), line 1 "Federal Taxable Income from your
  federal income tax form: 1040, 1040 SR, or 1040 SP line 15": CO's starting
  point is **federal taxable income**, not federal AGI.
  https://tax.colorado.gov/sites/tax/files/documents/DR0104_2025.pdf

Starting point: ``StateStartingPoint.FEDERAL_TAXABLE_INCOME``. CO takes the
federal taxable income number (after the federal standard or itemized
deduction), then applies CO additions (state tax add-back, nonqualified
CollegeInvest/ABLE distributions, out-of-state muni bond interest, etc.) and
CO subtractions (DR 0104AD: social security, pension exclusion, military
pension, CollegeInvest contributions, etc.). v1 approximates CO taxable
income as **federal taxable income itself** with an explicit ``v1_limitations``
list locked into tests.

Reciprocity: CO has **no** bilateral reciprocity agreements with any other
state. Verified against ``skill/reference/state-reciprocity.json`` — the
``agreements`` array contains zero pairs involving CO.

TABOR refund: the TABOR sales tax refund is claimed on DR 0104 lines 34-38
but is deferred in v1 — modeling the six-tier tiered refund requires a
filing-status-and-MAGI lookup table that changes per fiscal year. A
``tabor_refund_deferred`` flag is set on ``state_specific`` so downstream
consumers know to surface a warning.

Nonresident / part-year handling: a real CO nonresident return uses Form
DR 0104PN (Part-Year Resident/Nonresident Tax Calculation Schedule) which
apportions CO tax by a CO-source-income ratio applied to the full-year
resident tax. v1 uses day-based proration as a first-order approximation;
the real DR 0104PN ratio is fan-out follow-up work.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

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
# TY2025 constants
# ---------------------------------------------------------------------------


CO_TY2025_FLAT_RATE: Decimal = Decimal("0.044")
"""Colorado TY2025 individual income tax flat rate = 4.40%.

The permanent statutory rate under C.R.S. §39-22-104(1.7) is 4.40%. The
TABOR temporary rate reduction mechanism (C.R.S. §39-22-627) is available
for tax years 2024-2034, but only triggers when remaining excess state
revenues after the property tax exemption reimbursement exceed $300 million.

For FY2025 (TY2025), the OSA audit (report 2557P, October 2025) reported:
- Total TABOR refund obligation: $293.3 million
- Property tax exemption reimbursement: ~$182.1 million
- Remaining: ~$111.2 million  <-- below $300M threshold
- Triggers: six-tier sales tax refund mechanism only

The TY2025 rate therefore remains at the permanent 4.40% rate.
Source: https://content.leg.colorado.gov/sites/default/files/documents/audits/2557p_schedule_of_tabor_revenue_fy_25.pdf
"""


CO_V1_LIMITATIONS: tuple[str, ...] = (
    "CO additions not applied: state income tax add-back (DR 0104 line 2), "
    "QBI deduction add-back (line 3), standard/itemized federal deduction "
    "add-back (line 4), business meals deducted under IRC §274(k) (line 5), "
    "nonqualified CollegeInvest Tuition Savings Account distributions "
    "(line 6), nonqualified Colorado ABLE Account distributions (line 7), "
    "other additions (line 9).",
    "CO subtractions not applied (DR 0104AD): US government interest, "
    "pension and annuity exclusion (up to $20k age 55-64 / $24k age 65+), "
    "military retirement exclusion, social security benefits (taxed for "
    "federal but deductible for CO up to the pension cap), CollegeInvest "
    "contributions, ABLE account contributions, charitable contribution "
    "subtraction for non-itemizers, wildfire mitigation measures, "
    "conservation easement deduction, first-time home buyer savings.",
    "CO credits not applied (DR 0104CR): CO CTC, state EITC (match of "
    "federal EITC), child care expenses credit, nonrefundable credits "
    "(DR 0104CR lines 1-26 list), innovative motor vehicle credit "
    "(DR 0617), enterprise zone credits (DR 1366), CHIPS zone credit "
    "(DR 1370), strategic capital credit (DR 1330).",
    "CO AMT not computed (DR 0104AMT — CO has its own AMT tied to federal "
    "AMTI with CO additions/subtractions; rate is 3.47% of CO AMTI over "
    "federal taxable income).",
    "CO TABOR sales tax refund not computed (DR 0104 lines 34-38 "
    "six-tier refund table). See ``tabor_refund_deferred`` flag.",
    "Nonresident / part-year apportionment uses day-based proration "
    "(days / 365) instead of the DR 0104PN income-source ratio.",
)


_CENTS = Decimal("0.01")


def _cents(v: Decimal) -> Decimal:
    """Quantize a Decimal to cents with half-up rounding."""
    return v.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _apportionment_fraction(
    residency: ResidencyStatus, days_in_state: int
) -> Decimal:
    """Days-based apportionment fraction for nonresident / part-year.

    Residents get 1.0 (full state tax). Nonresidents and part-year residents
    are prorated by days_in_state / 365. Clamped to [0, 1].

    TODO: a real nonresident CO calculation uses Form DR 0104PN which
    computes CO-source income as a fraction of total federal AGI and applies
    that ratio to the full-year resident tax. Wage income is sourced to the
    work state, investment income to the domicile, rental to the property
    state, and gambling winnings to the event state. Day-based proration is
    a first-order approximation; fan-out will tighten this with DR 0104PN
    logic.
    """
    if residency == ResidencyStatus.RESIDENT:
        return Decimal("1")
    frac = Decimal(days_in_state) / Decimal("365")
    if frac < 0:
        return Decimal("0")
    if frac > 1:
        return Decimal("1")
    return frac


def _co_base_income_from_federal(federal: FederalTotals) -> Decimal:
    """Approximate CO base income.

    v1: CO base income = federal taxable income (DR 0104 line 1).

    A real DR 0104 computation applies CO additions (lines 2-9) and CO
    subtractions (DR 0104AD line 22) before computing tax at line 13. None
    of those adjustments are modeled in v1 — they are enumerated in
    ``CO_V1_LIMITATIONS`` so downstream consumers can warn the taxpayer.
    Negative federal taxable income (e.g. large itemized deductions)
    clamps to zero.
    """
    return max(Decimal("0"), federal.taxable_income)


def _co_tax(co_base_income: Decimal) -> Decimal:
    """Compute CO tax = base income * flat rate, quantized to cents."""
    if co_base_income <= 0:
        return Decimal("0")
    return _cents(co_base_income * CO_TY2025_FLAT_RATE)


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColoradoPlugin:
    """State plugin for Colorado.

    Hand-rolled (tenforty does not support CO Form 104). Computes CO tax as
    ``federal_taxable_income * 4.40%``, approximating the full DR 0104 flow
    and explicitly listing the CO adjustments it doesn't yet model in
    ``CO_V1_LIMITATIONS``. TABOR sales tax refund is deferred.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        co_base = _co_base_income_from_federal(federal)
        co_tax_full = _co_tax(co_base)

        # Apportion tax for nonresident / part-year. TODO: replace with
        # real DR 0104PN income-source ratio in fan-out.
        fraction = _apportionment_fraction(residency, days_in_state)
        co_tax_apportioned = _cents(co_tax_full * fraction)

        state_specific: dict[str, Any] = {
            "state_base_income_approx": _cents(co_base),
            "state_total_tax": co_tax_apportioned,
            "state_total_tax_resident_basis": co_tax_full,
            "flat_rate": CO_TY2025_FLAT_RATE,
            "apportionment_fraction": fraction,
            "v1_limitations": list(CO_V1_LIMITATIONS),
            "tabor_refund_deferred": True,
            "tabor_refund_reason": (
                "DR 0104 lines 34-38 six-tier sales tax refund not "
                "computed in v1. TY2025 CO TABOR refund will be issued via "
                "the sales tax refund mechanism (OSA 2557P, October 2025) "
                "based on the taxpayer's modified AGI tier."
            ),
            "starting_point": "federal_taxable_income",
            "federal_taxable_income": _cents(federal.taxable_income),
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
        """Split canonical income into CO-source vs non-CO-source.

        Residents: everything is CO-source. Nonresident / part-year: prorate
        each category by days_in_state / 365.

        TODO: CO DR 0104PN sources each income type differently — wages to
        the work location, investment income to domicile, rental to property
        state, etc. Day-based proration is the shared first-cut across all
        fan-out state plugins; refine in follow-up with the real DR 0104PN
        apportionment ratio.
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
            (
                f.box2a_total_capital_gain_distributions
                for f in return_.forms_1099_div
            ),
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

        # Schedule C net profit / Schedule E rental net — reuse engine helpers.
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
        # TODO: fan-out follow-up — fill CO Form DR 0104 (and DR 0104PN,
        # DR 0104AD, DR 0104CR where applicable) using pypdf against the CO
        # DOR's fillable PDFs. The output renderer suite is the right home
        # for this; this plugin returns structured state_specific data that
        # the renderer will consume.
        return []

    def form_ids(self) -> list[str]:
        return ["CO Form DR 0104"]


# ---------------------------------------------------------------------------
# Module-level plugin instance
# ---------------------------------------------------------------------------


PLUGIN: StatePlugin = ColoradoPlugin(
    meta=StatePluginMeta(
        code="CO",
        name="Colorado",
        has_income_tax=True,
        starting_point=StateStartingPoint.FEDERAL_TAXABLE_INCOME,
        dor_url="https://tax.colorado.gov/individual-income-tax",
        free_efile_url="https://www.colorado.gov/revenueonline",
        submission_channel=SubmissionChannel.STATE_DOR_FREE_PORTAL,
        reciprocity_partners=(),
        supported_tax_years=(2025,),
        notes=(
            "Hand-rolled CO Form DR 0104 calc (tenforty does not support "
            "2025/CO_Form104). Permanent flat rate 4.40% per C.R.S. "
            "§39-22-104(1.7); TY2025 TABOR temporary rate reduction did "
            "NOT trigger (remaining excess state revenues ~$111.2M fell "
            "below the $300M threshold per OSA 2557P, October 2025), so "
            "TY2025 uses the permanent 4.40% rate. Starting point: "
            "federal taxable income (DR 0104 line 1). No reciprocity "
            "agreements. CO TABOR sales tax refund deferred in v1."
        ),
    )
)
