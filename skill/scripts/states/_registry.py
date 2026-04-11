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
    for plugin in ALL_NO_TAX_PLUGINS.values():
        reg.register(plugin)
    return reg


registry = _build_registry()
