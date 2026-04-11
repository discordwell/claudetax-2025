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
    al,
    ar,
    az,
    ca,
    co,
    ct,
    de,
    dc,
    ga,
    hi,
    ia,
    id_,
    il,
    in_,
    ks,
    ky,
    la,
    ma,
    md,
    me,
    mi,
    mn,
    mo,
    ms,
    mt,
    nc,
    nd,
    ne,
    nj,
    nm,
    ny,
    oh,
    ok,
    or_,
    pa,
    ri,
    sc,
    ut,
    va,
    vt,
    wa,
    wi,
    wv,
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
    # Fan-out wave 5: 21 remaining taxing states using the post-probe rubric
    # (probe graph backend → cross-check against DOR primary source → wrap
    # if ±$5, hand-roll otherwise). See skill/reference/tenforty-ty2025-gap.md
    # for the decision tree and per-state probe table.
    #
    # Graph-backend WRAPS (10): the graph backend's $65k Single number
    # matched DOR to the cent (or within rounding noise). These plugins
    # import `tenforty` with `backend="graph"` like wi.py, and pin the
    # graph value in gatekeeper tests so drift trips CI.
    #
    #   AR — Form AR1000F (top-bracket formula 0.039*NTI − K)
    #   HI — Form N-11 (rate schedule I)
    #   IA — Form IA 1040 (flat 3.80% post-SF 2442)
    #   ID — Form 40 (flat 5.3% post-HB 40 2025)
    #   LA — Form IT-540 (flat 3.00% post-HB 10 2024)
    #   MS — Form 80-105 (flat 4.40% phase-down, HB 531)
    #   MT — Form 2 (2-bracket post-Tax Simplification)
    #   NM — Form PIT-1 (graduated, post-HB 252 2024)
    #   SC — Form SC1040 (starts from federal taxable income)
    #   VT — Form IN-111 (graduated, personal exemption folded into
    #        std ded via Act 65 of 2023)
    #
    # HAND-ROLLED (11): the graph backend systematically omits state-
    # specific personal exemptions or credits. Each hand-rolled plugin
    # cites its primary DOR booklet and ships a TestTenfortyStillHasGap
    # test that locks the observed delta. When tenforty fixes any of
    # them, the gatekeeper test fails and the plugin can be converted
    # to a graph wrap (cleanup, not correctness).
    #
    #   AL — Form 40 (graph omits federal-tax deduction + std ded +
    #        personal exemption; ~$488 gap)
    #   DE — Form 200-01 (graph omits $110 personal credit)
    #   IN — Form IT-40 (graph omits $1,000 personal exemption; $30 gap)
    #   ME — Form 1040ME (graph omits $5,150 personal exemption at 6.75%)
    #   MO — Form MO-1040 (graph omits Missouri federal-tax deduction)
    #   ND — Form ND-1 (graph's $15.11 IS mathematically correct — zero
    #        bracket cap $48,475, so $49,250 TI = $775 in 1.95% bracket.
    #        Hand-rolled anyway per CP8-B's now-retracted "stubbed" finding,
    #        with a gatekeeper that pins both the graph value AND the DOR
    #        formula for belt-and-suspenders coverage)
    #   NE — Form 1040N (graph omits personal exemption credit AND raises
    #        NotImplementedError for num_dependents > 0 — functional bug)
    #   OK — Form 511 (graph omits $1,000 personal exemption; $47.50 gap)
    #   RI — Form RI-1040 (graph omits $5,200 personal exemption)
    #   UT — Form TC-40 (graph applies full $945 Taxpayer Tax Credit but
    #        omits the 1.3% phase-out; ~$608 gap. Also: TY2025 rate is
    #        4.5% per HB 106 2025, not 4.55%)
    #   WV — Form IT-140 (graph omits $2,000 statutory exemption)
    for plugin_module in (
        al, ar, de, hi, ia, id_, in_, la, me, mo, ms, mt,
        nd, ne, nm, ok, ri, sc, ut, vt, wv,
    ):
        reg.register(plugin_module.PLUGIN)
    return reg


registry = _build_registry()
