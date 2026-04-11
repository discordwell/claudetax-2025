"""Probe tenforty's per-state TY2025 support across both backends.

Rerunnable: ``python -m skill.scripts.probe_tenforty_states``.

For every US state + DC (minus the 8 no-income-tax states), this script
invokes ``tenforty.evaluate_return`` with a minimal $65k Single / no
dependents / standard deduction return and records whether the default
OTS backend and/or the graph backend succeeds. When the graph backend
succeeds, it captures ``state_total_tax`` for quick visual sanity.

This script is the reference implementation that produced the probe
table in ``skill/reference/tenforty-ty2025-gap.md``. Re-run it whenever
tenforty updates to verify the gap still holds and to extend the table.

Output: a CSV-ish printed table to stdout. No files are written.
"""
from __future__ import annotations

import tenforty


# Every taxing jurisdiction + DC, ordered alphabetically.
TAXING_STATES: tuple[str, ...] = (
    "AL", "AR", "AZ", "CA", "CO", "CT", "DC", "DE", "GA", "HI",
    "IA", "ID", "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME",
    "MI", "MN", "MO", "MS", "MT", "NC", "ND", "NE", "NJ", "NM",
    "NY", "OH", "OK", "OR", "PA", "RI", "SC", "UT", "VA", "VT",
    "WI", "WV",
)


def _probe(state: str, backend: str | None) -> tuple[str, str]:
    """Run a single probe; return (status, note)."""
    kwargs = dict(
        year=2025,
        state=state,
        filing_status="Single",
        w2_income=65000,
        taxable_interest=0,
        qualified_dividends=0,
        ordinary_dividends=0,
        short_term_capital_gains=0,
        long_term_capital_gains=0,
        self_employment_income=0,
        rental_income=0,
        schedule_1_income=0,
        standard_or_itemized="Standard",
        itemized_deductions=0,
        num_dependents=0,
    )
    if backend is not None:
        kwargs["backend"] = backend
    try:
        r = tenforty.evaluate_return(**kwargs)
        total = r.state_total_tax
        note = f"total_tax=${float(total):.2f}" if total is not None else "(no total)"
        return "OK", note
    except TypeError as e:
        # backend kwarg not accepted by this tenforty version
        return "BACKEND_KWARG_UNAVAILABLE", str(e)[:80]
    except Exception as e:
        return "FAIL", str(e)[:80]


def main() -> None:
    print(f"Probing {len(TAXING_STATES)} taxing states against tenforty TY2025\n")
    print(f"{'STATE':<6} {'DEFAULT':<10} {'GRAPH':<10} {'GRAPH RESULT'}")
    print("-" * 70)
    for state in TAXING_STATES:
        default_status, default_note = _probe(state, backend=None)
        if default_status == "OK":
            # Default works — no need to probe graph
            graph_status, graph_note = "n/a", "(default supported)"
        else:
            graph_status, graph_note = _probe(state, backend="graph")
        note = default_note if default_status == "OK" else graph_note
        print(f"{state:<6} {default_status:<10} {graph_status:<10} {note}")


if __name__ == "__main__":
    main()
