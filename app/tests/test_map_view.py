"""Isometric map view: shared scales, metro fallback, and honest band widths."""
import json
import math
import pathlib

import pytest

from app.geo import map_config
from app.map_view import (
    build_domains,
    build_payload,
    build_support_domain,
    _rank_groups,
)
from app.scenarios import metro_key

REPO = pathlib.Path(__file__).resolve().parents[2]

NATIONAL = {
    "policy_id": "p", "label": "P", "n_seeds": 500,
    "impact": {"child_poverty_rate":
               {"baseline": 0.142, "median": 0.084, "p05": 0.071, "p95": 0.098}},
    "opinion": {"overall_support": {"median": 0.58, "p05": 0.51, "p95": 0.65},
                "by_group": [
                    {"group_type": "income_quintile", "group": "Q1",
                     "disposable_income_delta": 3140, "pct_better_off": 0.91,
                     "support": {"median": 0.74, "p05": 0.66, "p95": 0.81},
                     "evidence_ids": ["ev_a"]},
                    {"group_type": "census_region", "group": "South",
                     "disposable_income_delta": 1820, "pct_better_off": 0.44,
                     "support": {"median": 0.55, "p05": 0.47, "p95": 0.63},
                     "evidence_ids": ["ev_a"]}]},
    "warnings": [],
    # One metro present, deliberately with a WIDER band than national.
    "by_metro": {"houston": {
        "impact": {"child_poverty_rate":
                   {"baseline": 0.161, "median": 0.092, "p05": 0.052, "p95": 0.131}},
        "by_group": [{"group_type": "income_quintile", "group": "Q1",
                      "support": {"median": 0.72, "p05": 0.58, "p95": 0.86},
                      "evidence_ids": ["ev_a"]}]}},
}


def test_frontend_component_exists():
    idx = REPO / "app" / "frontend" / "index.html"
    assert idx.exists()
    src = idx.read_text()
    assert "streamlit:componentReady" in src
    assert "streamlit:setComponentValue" in src


def test_scale_is_shared_between_national_and_metro():
    """The metro band must be drawn on the national axis, not its own.

    This is the guarantee that a wider metro interval LOOKS wider. If each
    panel got its own domain the bars would be normalised and the extra
    uncertainty would vanish.
    """
    d = build_domains(NATIONAL)["child_poverty_rate"]
    nat = NATIONAL["impact"]["child_poverty_rate"]
    met = NATIONAL["by_metro"]["houston"]["impact"]["child_poverty_rate"]
    for band in (nat, met):
        assert d[0] <= band["p05"] and band["p95"] <= d[1], "domain must cover both"

    span = d[1] - d[0]
    nat_w = (nat["p95"] - nat["p05"]) / span
    met_w = (met["p95"] - met["p05"]) / span
    assert met_w > nat_w * 2, (
        f"metro band should render visibly wider (nat={nat_w:.3f}, metro={met_w:.3f})")


def test_support_domain_covers_metro_groups_too():
    d = build_support_domain(NATIONAL)
    assert d[0] <= 0.58 and 0.86 <= d[1]


def test_groups_ranked_by_absolute_income_change():
    ranked = _rank_groups(NATIONAL["opinion"]["by_group"])
    assert [g["group"] for g in ranked] == ["Q1", "South"]


def test_groups_without_a_delta_are_not_scored_on_something_invented():
    groups = [{"group": "A", "support": {}}, {"group": "B", "disposable_income_delta": 10,
                                             "support": {}}]
    assert [g["group"] for g in _rank_groups(groups)] == ["B", "A"]


def test_payload_carries_no_computed_policy_numbers():
    """build_payload may reshape and scale. It must never alter a figure."""
    p = build_payload(NATIONAL, [])
    assert p["impact"]["child_poverty_rate"] == NATIONAL["impact"]["child_poverty_rate"]
    assert p["by_metro"]["houston"]["impact"] == NATIONAL["by_metro"]["houston"]["impact"]


def test_missing_by_metro_yields_no_entries_not_a_fallback():
    """A scenario with no by_metro must produce nothing, never national figures."""
    scenario = {k: v for k, v in NATIONAL.items() if k != "by_metro"}
    p = build_payload(scenario, [])
    assert p["by_metro"] == {}


def test_all_six_cities_are_declared():
    keys = {c["metro_key"] for c in map_config()["cities"]}
    assert keys == {"new_york", "houston", "detroit", "san_francisco", "phoenix", "atlanta"}


def test_track_a_metro_labels_resolve_to_the_map_keys():
    """Track A's crosswalk labels metros 'New York'; the map keys them 'new_york'."""
    track_a = ["New York", "Houston", "Detroit", "San Francisco", "Phoenix", "Atlanta"]
    keys = {c["metro_key"] for c in map_config()["cities"]}
    assert {metro_key(m) for m in track_a} == keys


def test_every_city_lands_inside_the_tile_grid():
    cfg = map_config()
    b, tx, ty = cfg["bounds"], cfg["tile"], cfg["tile_y"]
    nx = math.ceil((b["x1"] - b["x0"]) / tx)
    ny = math.ceil((b["y1"] - b["y0"]) / ty)
    for c in cfg["cities"]:
        gx = (c["x"] - b["x0"]) / tx
        gy = ny - (c["y"] - b["y0"]) / ty
        assert 0 <= gx <= nx and 0 <= gy <= ny, c["label"]


def test_markers_snap_to_land():
    """San Francisco sits outside the coarse outline; the marker must still land."""
    src = (REPO / "app" / "frontend" / "index.html").read_text()
    assert "snapToLand" in src


def test_bar_widths_are_never_normalised_in_the_frontend():
    src = (REPO / "app" / "frontend" / "index.html").read_text()
    assert "not normalised" in src or "not normalized" in src


def test_no_hardcoded_policy_numbers_in_the_frontend():
    """The component may carry geometry constants, never a policy figure."""
    src = (REPO / "app" / "frontend" / "index.html").read_text()
    for forbidden in ("0.142", "0.084", "105000000000", "0.58", "3140"):
        assert forbidden not in src, f"hardcoded figure {forbidden} in the frontend"
