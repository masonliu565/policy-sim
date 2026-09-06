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
    """Padded display range.

    Padding never crosses zero when the data does not: an annual cost padded
    to -$23.6B spends half the axis on a region the quantity cannot occupy.
    """
    lo, hi = min(values), max(values)
    if lo == hi:
        span = abs(lo) * 0.2 or 1.0
        out = [lo - span, hi + span]
    else:
        span = (hi - lo) * pad
        out = [lo - span, hi + span]
    if lo >= 0 and out[0] < 0:
        out[0] = 0.0
    if hi <= 0 and out[1] > 0:
        out[1] = 0.0
    return out


def build_domains(scenario: Dict[str, Any]) -> Dict[str, List[float]]:
    """One shared scale per outcome, spanning national AND every metro.

    The contract puts it plainly: metro intervals are legitimately ~3.9x wider
    than national ones, because a metro carries ~2,000 households against
    30,000. Drawing both on one axis is what makes that visible. Giving each
    panel its own autoscaled axis would render the two bands the same width and
    delete the finding.
    """
    domains: Dict[str, List[float]] = {}
    metros = (scenario.get("by_metro_index") or {}).values()

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
    """Support axis, over the groups that actually have a support estimate."""
    pts: List[float] = []
    for g in (scenario.get("opinion") or {}).get("by_group") or []:
        if g.get("support"):
            pts += [g["support"]["p05"], g["support"]["p95"]]
    overall = (scenario.get("opinion") or {}).get("overall_support")
    if overall:
        pts += [overall["p05"], overall["p95"]]
    return _domain(pts) if pts else [0.0, 1.0]


def build_delta_domain(scenario: Dict[str, Any]) -> List[float]:
    """Shared axis for disposable-income change, national and metro together.

    The contract now ships p05/p95 alongside every disposable_income_delta, so
    these render as intervals like everything else rather than as bare points.
    """
    pts: List[float] = []

    def add(rows):
        for r in rows or []:
            for k in ("disposable_income_delta_p05", "disposable_income_delta",
                      "disposable_income_delta_p95"):
                if r.get(k) is not None:
                    pts.append(r[k])

    add((scenario.get("opinion") or {}).get("by_group"))
    for m in (scenario.get("by_metro_index") or {}).values():
        add(m.get("top_subgroups"))
    return _domain(pts) if pts else [0.0, 1.0]


def _delta_band(row: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """The income-change interval, when the scenario provides one."""
    med = row.get("disposable_income_delta")
    lo = row.get("disposable_income_delta_p05")
    hi = row.get("disposable_income_delta_p95")
    if med is None:
        return None
    if lo is None or hi is None:
        return None  # a bare point estimate is not rendered as a band
    return {"median": med, "p05": lo, "p95": hi, "baseline": 0.0}


def _group_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalise national by_group and metro top_subgroups into one shape."""
    out = []
    for r in rows or []:
        out.append({
            "group_type": r.get("group_type", ""),
            "group": r.get("group", ""),
            "support": r.get("support"),
            "evidence_status": r.get("evidence_status", "ok" if r.get("support") else None),
            "evidence_coverage": r.get("evidence_coverage"),
            "evidence_ids": r.get("evidence_ids") or [],
            "households_weighted": r.get("households_weighted"),
            "sample_n": r.get("sample_n"),
            "low_sample": bool(r.get("low_sample")),
            "pct_better_off": r.get("pct_better_off"),
            "delta": _delta_band(r),
            "delta_point": r.get("disposable_income_delta"),
        })
    return out


def _rank_groups(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Top changed groups, by the scenario's own income-change figure.

    Groups without that field keep their original order behind the ranked ones
    rather than being scored on something invented.
    """
    with_delta = [g for g in groups if g.get("delta_point") is not None]
    without = [g for g in groups if g.get("delta_point") is None]
    with_delta.sort(key=lambda g: abs(g["delta_point"]), reverse=True)
    return with_delta + without


def build_payload(scenario: Dict[str, Any], stack: List[str]) -> Dict[str, Any]:
    opinion = scenario.get("opinion") or {}

    metros = {}
    for key, block in (scenario.get("by_metro_index") or {}).items():
        metros[key] = {
            "metro": block.get("metro"),
            "impact": block.get("impact") or {},
            "households_weighted": block.get("households_weighted"),
            "sample_n": block.get("sample_n"),
            "low_sample": bool(block.get("low_sample")),
            "groups": _rank_groups(_group_rows(block.get("top_subgroups"))),
        }

    return {
        "label": scenario.get("label", scenario.get("policy_id", "")),
        "policy_id": scenario.get("policy_id"),
        "n_seeds": scenario.get("n_seeds"),
        "impact": scenario.get("impact") or {},
        "groups": _rank_groups(_group_rows(opinion.get("by_group"))),
        "overall_support": opinion.get("overall_support"),
        "in_support": scenario.get("in_support", True),
        "nearest_policies": scenario.get("nearest_policies") or [],
        "by_metro": metros,
        "domains": build_domains(scenario),
        "support_domain": build_support_domain(scenario),
        "delta_domain": build_delta_domain(scenario),
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
