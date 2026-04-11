"""Reference state plugin implementation: states with no individual income tax.

This is ONE class instantiated for each of the 8 no-income-tax states. It's the
first concrete StatePlugin implementation and validates the Protocol shape.
Once CP5 locks, state fan-out agents will copy this pattern for taxing states.

States handled:
    AK, FL, NV, NH, SD, TN, TX, WY

Washington (WA) is NOT handled here — it has a capital gains tax so it needs
its own implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from skill.scripts.models import (
    CanonicalReturn,
    ResidencyStatus,
    StateCode,
    StateReturn,
)
from skill.scripts.states._plugin_api import (
    FederalTotals,
    IncomeApportionment,
    StatePluginMeta,
    SubmissionChannel,
)


@dataclass(frozen=True)
class NoIncomeTaxPlugin:
    """State plugin for states with no individual income tax.

    Returns a StateReturn with zero tax and a marker in state_specific so
    downstream output modules know to emit a "no return required" note.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        return StateReturn(
            state=self.meta.code,
            residency=residency,
            days_in_state=days_in_state,
            state_specific={
                "state_tax": 0,
                "no_return_required": True,
                "reason": f"{self.meta.name} has no individual income tax for TY2025.",
            },
        )

    def apportion_income(
        self,
        return_: CanonicalReturn,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> IncomeApportionment:
        return IncomeApportionment(
            state_source_wages=Decimal("0"),
            state_source_interest=Decimal("0"),
            state_source_dividends=Decimal("0"),
            state_source_capital_gains=Decimal("0"),
            state_source_self_employment=Decimal("0"),
            state_source_rental=Decimal("0"),
        )

    def render_pdfs(self, state_return: StateReturn, out_dir: Path) -> list[Path]:
        return []

    def form_ids(self) -> list[str]:
        return []


# ---------------------------------------------------------------------------
# Instances for the 8 no-income-tax states
# ---------------------------------------------------------------------------


def _make(code: StateCode, name: str, dor_url: str, notes: str = "") -> NoIncomeTaxPlugin:
    return NoIncomeTaxPlugin(
        meta=StatePluginMeta(
            code=code,
            name=name,
            has_income_tax=False,
            conforms_to_federal_agi=False,
            dor_url=dor_url,
            free_efile_url=None,
            submission_channel=SubmissionChannel.NO_RETURN_REQUIRED,
            reciprocity_partners=(),
            supported_tax_years=(2025,),
            notes=notes,
        )
    )


ALASKA = _make("AK", "Alaska", "https://tax.alaska.gov/")
FLORIDA = _make("FL", "Florida", "https://floridarevenue.com/taxes/")
NEVADA = _make("NV", "Nevada", "https://tax.nv.gov/")
NEW_HAMPSHIRE = _make(
    "NH",
    "New Hampshire",
    "https://www.revenue.nh.gov/",
    notes="NH previously taxed interest and dividends only. Tax fully phased out after TY2024.",
)
SOUTH_DAKOTA = _make("SD", "South Dakota", "https://dor.sd.gov/")
TENNESSEE = _make("TN", "Tennessee", "https://www.tn.gov/revenue.html")
TEXAS = _make("TX", "Texas", "https://comptroller.texas.gov/taxes/")
WYOMING = _make("WY", "Wyoming", "https://revenue.wyo.gov/")

ALL_NO_TAX_PLUGINS: dict[StateCode, NoIncomeTaxPlugin] = {
    p.meta.code: p
    for p in (ALASKA, FLORIDA, NEVADA, NEW_HAMPSHIRE, SOUTH_DAKOTA, TENNESSEE, TEXAS, WYOMING)
}
