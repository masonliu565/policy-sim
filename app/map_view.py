"""
Isometric map view.

Renders the whole main view — map plus right column — as one declared Streamlit
component, so clicking a city zooms and swaps the panel without a page rerun.
The component reports the click back to Python afterwards, which is what lets
the parser and the scenario selection stay server-side.

Every figure the component draws is read out of a scenario file. This module
computes nothing except display scales.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st
import streamlit.components.v1 as components

from app import charts
from app.geo import map_config
from app.theme import PALETTES

_FRONTEND = Path(__file__).resolve().parent / "frontend"
_component = components.declare_component("policy_sim_map", path=str(_FRONTEND))

IMPACT_LABELS = {
    "child_poverty_rate": "Child poverty rate",
    "overall_poverty_rate": "Overall poverty rate",
    "median_disposable_income": "Median disposable income",
    "annual_cost_usd": "Annual cost",
}


def _domain(values: List[float], pad: float = 0.14) -> List[float]:
    lo, hi = min(values), max(values)
    if lo == hi:
        span = abs(lo) * 0.2 or 1.0
        return [lo - span, hi + span]
    span = (hi - lo) * pad
    return [lo - span, hi + span]


def build_domains(scenario: Dict[str, Any]) -> Dict[str, List[float]]:
    """One shared scale per outcome, spanning national AND every metro.

    This is the honest choice and the whole reason the metro view is worth
    showing: a metro interval is wider than the national one, and putting both
    on the same axis is what makes that visible. Giving each panel its own
    axis would normalise the bar widths and hide it.
    """
    domains: Dict[str, List[float]] = {}
    metros = (scenario.get("by_metro") or {}).values()

    for key, band in (scenario.get("impact") or {}).items():
        pts = [band["p05"], band["p95"], band["median"]]
        if band.get("baseline") is not None:
            pts.append(band["baseline"])
        for m in metros:
            mb = (m.get("impact") or {}).get(key)
            if mb:
                pts += [mb["p05"], mb["p95"], mb["median"]]
                if mb.get("baseline") is not None:
                    pts.append(mb["baseline"])
        domains[key] = _domain(pts)
    return domains


def build_support_domain(scenario: Dict[str, Any]) -> List[float]:
    pts: List[float] = []
    for g in (scenario.get("opinion") or {}).get("by_group", []):
        pts += [g["support"]["p05"], g["support"]["p95"]]
    for m in (scenario.get("by_metro") or {}).values():
        for g in m.get("by_group", []):
            pts += [g["support"]["p05"], g["support"]["p95"]]
        if "support" in m:
            pts += [m["support"]["p05"], m["support"]["p95"]]
    return _domain(pts) if pts else [0.0, 1.0]


def _rank_groups(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Top changed groups, by the scenario's own income-change figure.

    Groups without that field keep their original order behind the ranked ones
    rather than being scored on something invented.
    """
    with_delta = [g for g in groups if g.get("disposable_income_delta") is not None]
    without = [g for g in groups if g.get("disposable_income_delta") is None]
    with_delta.sort(key=lambda g: abs(g["disposable_income_delta"]), reverse=True)
    return with_delta + without


def build_payload(scenario: Dict[str, Any], stack: List[str]) -> Dict[str, Any]:
    opinion = scenario.get("opinion") or {}
    by_metro = scenario.get("by_metro") or {}

    # Rank metro groups the same way, without touching the source dict.
    metros = {}
    for key, block in by_metro.items():
        metros[key] = dict(block)
        if block.get("by_group"):
            metros[key]["by_group"] = _rank_groups(block["by_group"])

    return {
        "label": scenario.get("label", scenario.get("policy_id", "")),
        "policy_id": scenario.get("policy_id"),
        "n_seeds": scenario.get("n_seeds"),
        "impact": scenario.get("impact") or {},
        "by_group": _rank_groups(opinion.get("by_group") or []),
        "overall_support": opinion.get("overall_support"),
        "by_metro": metros,
        "domains": build_domains(scenario),
        "support_domain": build_support_domain(scenario),
        "units": {k: charts.infer_unit(k) for k in (scenario.get("impact") or {})},
        "labels": {k: IMPACT_LABELS.get(k, k.replace("_", " ").capitalize())
                   for k in (scenario.get("impact") or {})},
        "stack": stack,
    }


def render(
    scenario: Dict[str, Any],
    mode: str = "day",
    stack: Optional[List[str]] = None,
    policy_note: str = "",
    rebuild: bool = False,
    height: int = 720,
    key: str = "isomap",
) -> Optional[Dict[str, Any]]:
    """Draw the map view. Returns the component's last event, or None."""
    return _component(
        config={"palettes": PALETTES, **map_config()},
        data=build_payload(scenario, stack or []),
        mode=mode,
        policy_note=policy_note,
        rebuild=rebuild,
        height=height,
        key=key,
        default=None,
    )
