"""State plugin API — the contract every state implements.

Each state lives in its own file at skill/scripts/states/<xx>.py and exports a
`PLUGIN: StatePlugin` module-level constant. The registry (_registry.py)
discovers plugins by iterating the package.

This module defines:

- SubmissionChannel: how a state return is transmitted
- StatePluginMeta: metadata describing a state's tax setup
- FederalTotals: the subset of federal computation a state plugin needs
- StatePlugin: the Protocol every state implementation satisfies
- ReciprocityTable: loads and queries skill/reference/state-reciprocity.json
- IncomeApportionment: helper for part-year / nonresident income splitting

The 43 parallel state-implementation sub-agents all code against THIS file.
Keep it stable once fan-out starts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Protocol, runtime_checkable

from skill.scripts.models import (
    CanonicalReturn,
    ResidencyStatus,
    StateCode,
    StateReturn,
)


# ---------------------------------------------------------------------------
# Submission channels
# ---------------------------------------------------------------------------


class SubmissionChannel(str, Enum):
    """How a state return is actually transmitted to the state DOR.

    Individuals cannot talk to MeF directly even at the state level — the same
    EFIN/ERO constraint applies. These channels describe what the HUMAN will do
    with the artifacts the plugin produces.
    """

    STATE_DOR_FREE_PORTAL = "state_dor_free_portal"
    """State operates its own free e-file portal (e.g. CA CalFile, NY Free File).
    Plugin outputs a data pack keyed to the portal's fields."""

    FED_STATE_PIGGYBACK = "fed_state_piggyback"
    """State participates in the IRS Fed/State MeF program but requires commercial
    software to transmit. Plugin outputs filled state PDFs + return data JSON."""

    STATE_ONLY_MEF = "state_only_mef"
    """State runs its own MeF-style submission accepting XML. Plugin can emit XML,
    though an individual still can't transmit it without credentials."""

    PAPER_ONLY = "paper_only"
    """State does not e-file individual returns; only paper is accepted."""

    NO_RETURN_REQUIRED = "no_return_required"
    """State has no individual income tax. Plugin returns an empty StateReturn
    and emits a 'no return required' marker."""


# ---------------------------------------------------------------------------
# Plugin metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatePluginMeta:
    code: StateCode
    name: str
    has_income_tax: bool
    conforms_to_federal_agi: bool
    """True if state tax calculation starts from federal AGI (most states).
    False if state computes its own base (PA, NH historically)."""
    dor_url: str
    free_efile_url: str | None
    submission_channel: SubmissionChannel
    reciprocity_partners: tuple[StateCode, ...]
    supported_tax_years: tuple[int, ...]
    notes: str = ""


# ---------------------------------------------------------------------------
# Federal totals — what the state plugin needs from the federal calc
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FederalTotals:
    """The subset of federal calc results a state plugin consumes.

    Every state plugin gets this alongside the full canonical return. Plugins
    should prefer reading from this struct for consistency across plugins.
    """

    adjusted_gross_income: Decimal
    taxable_income: Decimal
    total_federal_tax: Decimal
    federal_income_tax: Decimal
    qbi_deduction: Decimal = Decimal("0")
    se_tax: Decimal = Decimal("0")


# ---------------------------------------------------------------------------
# Income apportionment helper
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IncomeApportionment:
    """How income is split for part-year / nonresident state returns.

    Days-based apportionment is the default; per-state plugins can compute more
    sophisticated apportionment (e.g. specific sourcing of investment income).
    """

    state_source_wages: Decimal
    state_source_interest: Decimal
    state_source_dividends: Decimal
    state_source_capital_gains: Decimal
    state_source_self_employment: Decimal
    state_source_rental: Decimal
    state_source_other: Decimal = Decimal("0")

    @property
    def state_source_total(self) -> Decimal:
        return (
            self.state_source_wages
            + self.state_source_interest
            + self.state_source_dividends
            + self.state_source_capital_gains
            + self.state_source_self_employment
            + self.state_source_rental
            + self.state_source_other
        )


# ---------------------------------------------------------------------------
# The Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class StatePlugin(Protocol):
    """Every state implementation satisfies this Protocol.

    Plugins are stateless — the same instance should be safe to call across
    multiple taxpayers. All mutable state lives in the returned StateReturn.
    """

    meta: StatePluginMeta

    def compute(
        self,
        return_: CanonicalReturn,
        federal: FederalTotals,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> StateReturn:
        """Compute the state return given canonical federal data.

        Args:
            return_: the full canonical return, read-only.
            federal: computed federal totals from the calc engine.
            residency: resident / nonresident / part-year for this state.
            days_in_state: days the taxpayer was domiciled/present in this state
                during the tax year. Ignored when residency is RESIDENT or when
                apportionment is not days-based.

        Returns:
            A StateReturn populated with the state's canonical-shape state_specific
            payload. The plugin is responsible for computing the state tax and
            writing it into state_specific under a documented key.
        """
        ...

    def apportion_income(
        self,
        return_: CanonicalReturn,
        residency: ResidencyStatus,
        days_in_state: int,
    ) -> IncomeApportionment:
        """Split each income category into state-source vs non-state-source.

        Plugins override this with state-specific sourcing rules.
        """
        ...

    def render_pdfs(
        self,
        state_return: StateReturn,
        out_dir: Path,
    ) -> list[Path]:
        """Fill and write the state's official PDF forms.

        Returns the list of written PDF paths (absolute).
        """
        ...

    def form_ids(self) -> list[str]:
        """Return the canonical form identifiers this plugin produces.

        Example: ["CA Form 540", "CA Schedule CA (540)"].
        """
        ...


# ---------------------------------------------------------------------------
# Reciprocity table
# ---------------------------------------------------------------------------


_RECIPROCITY_PATH = (
    Path(__file__).resolve().parent.parent.parent / "reference" / "state-reciprocity.json"
)


@lru_cache(maxsize=1)
def _load_reciprocity_raw() -> dict:
    with _RECIPROCITY_PATH.open() as fh:
        return json.load(fh)


@dataclass(frozen=True)
class ReciprocityTable:
    """Queryable view of state-reciprocity.json."""

    pairs: frozenset[frozenset[StateCode]]
    no_income_tax_states: frozenset[StateCode]
    capital_gains_only_states: frozenset[StateCode]
    dc_universal_exemption: bool

    @classmethod
    def load(cls) -> "ReciprocityTable":
        raw = _load_reciprocity_raw()
        pairs = frozenset(
            frozenset(entry["states"]) for entry in raw["agreements"]
        )
        no_tax = frozenset(entry["code"] for entry in raw["no_income_tax_states"])
        cg_only = frozenset(entry["code"] for entry in raw["capital_gains_only_states"])
        dc = raw.get("dc_nonresident_exemption", {}).get("applies", False)
        return cls(
            pairs=pairs,
            no_income_tax_states=no_tax,
            capital_gains_only_states=cg_only,
            dc_universal_exemption=dc,
        )

    def are_reciprocal(self, a: StateCode, b: StateCode) -> bool:
        """True iff states a and b have a bilateral reciprocity agreement."""
        if a == b:
            return False
        return frozenset([a, b]) in self.pairs

    def partners_of(self, state: StateCode) -> frozenset[StateCode]:
        """Return the set of states that have bilateral reciprocity with `state`."""
        out: set[StateCode] = set()
        for pair in self.pairs:
            if state in pair:
                other = next(s for s in pair if s != state)
                out.add(other)
        return frozenset(out)

    def has_income_tax(self, state: StateCode) -> bool:
        return state not in self.no_income_tax_states

    def taxes_only_capital_gains(self, state: StateCode) -> bool:
        return state in self.capital_gains_only_states

    def dc_exempts_nonresident_employee(self, work_state: StateCode) -> bool:
        """DC-specific rule: DC does not tax employees who are nonresidents of DC,
        regardless of their home state."""
        return self.dc_universal_exemption and work_state == "DC"
