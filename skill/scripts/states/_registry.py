"""State plugin registry.

Discovers and exposes every registered StatePlugin by state code. New state
implementations get added here as the fan-out phase lands them.

Usage:
    from skill.scripts.states._registry import registry
    plugin = registry.get("CA")
"""
from __future__ import annotations

from functools import lru_cache

from skill.scripts.models import StateCode
from skill.scripts.states import (
    az,
    ca,
    co,
    ct,
    dc,
    ga,
    il,
    ks,
    ky,
    ma,
    md,
    mi,
    mn,
    nc,
    nj,
    ny,
    oh,
    or_,
    pa,
    va,
    wa,
    wi,
)
from skill.scripts.states._no_income_tax import ALL_NO_TAX_PLUGINS
from skill.scripts.states._plugin_api import StatePlugin


class StateRegistry:
    """Lookup table from state code to plugin instance.

    Plugins register themselves here via module-level registration. Currently
    only the no-income-tax batch is wired up; taxing-state plugins land in fan-out.
    """

    def __init__(self) -> None:
        self._plugins: dict[StateCode, StatePlugin] = {}

    def register(self, plugin: StatePlugin) -> None:
        code = plugin.meta.code
        if code in self._plugins:
            raise ValueError(f"state plugin for {code} already registered")
        self._plugins[code] = plugin

    def get(self, code: StateCode) -> StatePlugin:
        if code not in self._plugins:
            raise KeyError(
                f"no state plugin registered for {code!r}. "
                f"Registered: {sorted(self._plugins.keys())}"
            )
        return self._plugins[code]

    def has(self, code: StateCode) -> bool:
        return code in self._plugins

    def codes(self) -> list[StateCode]:
        return sorted(self._plugins.keys())

    def __len__(self) -> int:
        return len(self._plugins)


@lru_cache(maxsize=1)
def _build_registry() -> StateRegistry:
    reg = StateRegistry()
    # No-income-tax batch (AK, FL, NV, NH, SD, TN, TX, WY)
    for plugin in ALL_NO_TAX_PLUGINS.values():
        reg.register(plugin)
    # Fan-out wave 1: CA, NY, WA (cap gains), DC
    for plugin_module in (ca, ny, wa, dc):
        reg.register(plugin_module.PLUGIN)
    # Fan-out wave 2: AZ, MA, MI, NJ, PA, VA (all tenforty-backed)
    for plugin_module in (az, ma, mi, nj, pa, va):
        reg.register(plugin_module.PLUGIN)
    # Fan-out wave 3:
    #   Tenforty-backed: NC (flat 4.25%), OH (graduated), OR (graduated, file
    #     named or_.py because `or` is a Python keyword).
    #   Hand-rolled: IL (flat 4.95%), CO (flat 4.40% from federal taxable
    #     income), GA (flat 5.19%). All three v1 approximations — state
    #     additions/subtractions were remediated in wave 4 (real Sch M /
    #     DR 0104AD / Sch 1 adds+subs with US Treasury + SS + retirement
    #     subtractions per DOR primary sources).
    for plugin_module in (nc, oh, or_, il, co, ga):
        reg.register(plugin_module.PLUGIN)
    # Fan-out wave 4: all 6 states below are HAND-ROLLED from DOR primary
    # sources. Despite tenforty's OTSState enum listing these codes, the
    # default OTS backend raises "OTS does not support YYYY/ST_FORM" for
    # every (year, form) pair. See skill/reference/tenforty-ty2025-gap.md
    # for the enumerated gap and the decision rubric. WI uniquely uses
    # tenforty's graph backend (wi_form1_2025.json); the other five (CT,
    # KS, KY, MD, MN) are fully hand-rolled from the DOR instructions.
    #
    #   CT — CT-1040 TCS Rev. 12/25 Tables A-E (hand-rolled)
    #   KS — Form K-40 IP25 booklet (hand-rolled, SB 1 2024 two-bracket)
    #   KY — Form 740 (hand-rolled, flat 4% per HB 8 2022 schedule)
    #   MD — Form 502 + county local tax table (hand-rolled, 22
    #        jurisdictions incl. Anne Arundel/Frederick progressive locals)
    #   MN — Form M1 (hand-rolled, graduated brackets 5.35/6.80/7.85/9.85)
    #   WI — Form 1 (tenforty graph backend wrapper — first plugin to use
    #        backend="graph"; state_taxable_income echoes AGI because graph
    #        backend doesn't apply the WI sliding-scale std ded on output.
    #        Tax total is still authoritative.)
    for plugin_module in (ct, ks, ky, md, mn, wi):
        reg.register(plugin_module.PLUGIN)
    return reg


registry = _build_registry()
