"""Isometric map view, against the real output contract in docs/output_contract.md."""
import json
import math
import pathlib

import pytest

from app.geo import map_config
from app.map_view import (
    build_delta_domain,
    build_domains,
    build_payload,
    build_support_domain,
    _rank_groups,
    _group_rows,
)
from app.scenarios import list_scenarios, load_scenario, metro_key, validate

REPO = pathlib.Path(__file__).resolve().parents[2]

# by_metro is a LIST keyed by display label, per the contract.
SCEN = {
    "policy_id": "p", "label": "P", "n_seeds": 500, "in_support": True,
    "impact": {"child_poverty_rate":
               {"baseline": 0.142, "median": 0.084, "p05": 0.071, "p95": 0.098}},
    "opinion": {
        "overall_support": {"median": 0.58, "p05": 0.51, "p95": 0.65},
        "by_group": [
            {"group_type": "income_quintile", "group": "Q1",
             "disposable_income_delta": 3140, "pct_better_off": 0.91,
             "households_weighted": 26000000, "evidence_status": "ok",
             "support": {"median": 0.74, "p05": 0.66, "p95": 0.81},
             "evidence_ids": ["ev_a"]},
            {"group_type": "income_quintile", "group": "Q3",
             "disposable_income_delta": 1180, "pct_better_off": 0.42,
             "support": None, "evidence_status": "insufficient_evidence",
             "evidence_coverage": 0.0, "evidence_ids": []}]},
    "warnings": [],
    "by_metro": [{
        "metro": "Houston", "households_weighted": 2725020.0, "sample_n": 2000,
        "low_sample": False,
        "impact": {"child_poverty_rate":
                   {"baseline": 0.161, "median": 0.092, "p05": 0.052, "p95": 0.131}},
        "top_subgroups": [
            {"group_type": "household_type", "group": "couple_with_kids",
             "disposable_income_delta": 4820.0, "disposable_income_delta_p05": 4310.0,
             "disposable_income_delta_p95": 5290.0, "households_weighted": 512000.0,
             "sample_n": 340, "low_sample": False}]}],
}


def loaded(scen, tmp_path_factory=None):
    """Run it through the loader so by_metro_index exists, as at runtime."""
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(scen, f)
    try:
        return load_scenario(path)
    finally:
        os.unlink(path)


# --- the honesty guarantee -------------------------------------------------
def test_metro_band_renders_wider_on_the_shared_axis():
    """The contract: metro intervals are ~3.9x wider and must not be rescaled.

    One shared domain per outcome is what makes that visible. Per-panel
    autoscaling would draw both bands the same width and delete the finding.
    """
    s = loaded(SCEN)
    d = build_domains(s)["child_poverty_rate"]
    nat = s["impact"]["child_poverty_rate"]
    met = s["by_metro_index"]["houston"]["impact"]["child_poverty_rate"]

    for band in (nat, met):
        assert d[0] <= band["p05"] and band["p95"] <= d[1], "domain must cover both"

    span = d[1] - d[0]
    nat_w = (nat["p95"] - nat["p05"]) / span
    met_w = (met["p95"] - met["p05"]) / span
    assert met_w > nat_w * 2, f"metro band must render wider (nat={nat_w:.3f} metro={met_w:.3f})"


def test_real_scenario_metro_bands_are_drawn_on_the_national_axis():
    """Whatever the widths turn out to be, both are drawn on one shared axis.

    The contract predicts metro bands ~3.9x wider. In Track A's actual output
    that holds for some metro/outcome pairs and not others — Atlanta and
    Phoenix child-poverty bands come out NARROWER than national, and San
    Francisco's collapse to zero width. The app must not assume the prediction;
    it must draw whatever is there on a common scale and let it show.
    """
    s = load_scenario(REPO / "scenarios" / "ctc_2021.json")
    d = build_domains(s)["child_poverty_rate"]
    nat = s["impact"]["child_poverty_rate"]
    assert d[0] <= nat["p05"] and nat["p95"] <= d[1]
    for key, m in s["by_metro_index"].items():
        b = m["impact"]["child_poverty_rate"]
        assert d[0] <= b["p05"] and b["p95"] <= d[1], f"{key} falls outside the shared axis"


def test_degenerate_metro_intervals_are_detected():
    """A zero-width band must never render as an ordinary confident estimate.

    This used to assert that San Francisco's real bands had p05 == p95, which
    they did: the model carried parameter uncertainty but no sampling
    uncertainty, and SF has only 31 sampled households with children in
    poverty. That was a real bug and it has since been fixed upstream, so
    pinning the test to the live file made it fail the moment the data got
    better. The detector is still worth testing, so build the degenerate case
    explicitly instead of hoping the pipeline keeps producing one.
    """
    s = load_scenario(REPO / "scenarios" / "ctc_2021.json")
    sf = s["by_metro_index"]["san_francisco"]["impact"]["child_poverty_rate"]
    sf["p05"] = sf["p95"] = sf["median"]
    notes = validate(s, "synthetic")
    assert any("zero-width" in n for n in notes), notes


def test_real_scenarios_have_no_degenerate_metro_intervals():
    """The live files should be free of zero-width bands. If one comes back,
    the model has stopped propagating sampling uncertainty somewhere."""
    for name in ("ctc_2021", "ctc_1000", "flat_500", "eitc_match"):
        s = load_scenario(REPO / "scenarios" / f"{name}.json")
        bad = [n for n in s.get("_notes", []) if "zero-width" in n]
        assert not bad, f"{name}: {bad}"


# --- the three contract states --------------------------------------------
def test_null_support_is_preserved_not_defaulted_to_zero():
    p = build_payload(loaded(SCEN), [])
    q3 = next(g for g in p["groups"] if g["group"] == "Q3")
    assert q3["support"] is None
    assert q3["evidence_status"] == "insufficient_evidence"


def test_out_of_support_scenario_carries_nearest_policies():
    s = load_scenario(REPO / "scenarios" / "flat_500.json")
    p = build_payload(s, [])
    assert p["in_support"] is False
    assert p["overall_support"] is None
    assert all(g["support"] is None for g in p["groups"])
    assert p["nearest_policies"], "an out-of-support scenario must offer alternatives"


def test_low_sample_flag_survives_into_the_payload():
    scen = json.loads(json.dumps(SCEN))
    scen["by_metro"][0]["top_subgroups"][0]["low_sample"] = True
    p = build_payload(loaded(scen), [])
    assert p["by_metro"]["houston"]["groups"][0]["low_sample"] is True


# --- income-change intervals ----------------------------------------------
def test_metro_delta_becomes_a_real_interval():
    p = build_payload(loaded(SCEN), [])
    band = p["by_metro"]["houston"]["groups"][0]["delta"]
    assert band == {"median": 4820.0, "p05": 4310.0, "p95": 5290.0, "baseline": 0.0}


def test_national_delta_without_bounds_is_not_faked_into_a_band():
    """A bare point estimate must not be rendered as if it had an interval."""
    p = build_payload(loaded(SCEN), [])
    q1 = next(g for g in p["groups"] if g["group"] == "Q1")
    assert q1["delta"] is None
    assert q1["delta_point"] == 3140


def test_delta_domain_spans_national_and_metro():
    d = build_delta_domain(loaded(SCEN))
    assert d[0] <= 4310.0 and 5290.0 <= d[1]


# --- ranking / integrity ---------------------------------------------------
def test_groups_ranked_by_absolute_income_change():
    rows = _group_rows(SCEN["opinion"]["by_group"])
    assert [g["group"] for g in _rank_groups(rows)] == ["Q1", "Q3"]


def test_payload_never_alters_a_figure():
    s = loaded(SCEN)
    p = build_payload(s, [])
    assert p["impact"] == s["impact"]
    assert p["by_metro"]["houston"]["impact"] == s["by_metro_index"]["houston"]["impact"]


def test_missing_by_metro_yields_no_entries_not_a_fallback():
    scen = {k: v for k, v in SCEN.items() if k != "by_metro"}
    assert build_payload(loaded(scen), [])["by_metro"] == {}


# --- geometry / keys -------------------------------------------------------
def test_all_six_cities_are_declared():
    assert {c["metro_key"] for c in map_config()["cities"]} == {
        "new_york", "houston", "detroit", "san_francisco", "phoenix", "atlanta"}


def test_track_a_metro_labels_resolve_to_the_map_keys():
    """Track A labels metros 'New York'; the geometry keys them 'new_york'."""
    s = load_scenario(REPO / "scenarios" / "ctc_2021.json")
    assert set(s["by_metro_index"]) == {c["metro_key"] for c in map_config()["cities"]}


def test_every_city_lands_inside_the_tile_grid():
    cfg = map_config()
    b, tx, ty = cfg["bounds"], cfg["tile"], cfg["tile_y"]
    nx, ny = math.ceil((b["x1"] - b["x0"]) / tx), math.ceil((b["y1"] - b["y0"]) / ty)
    for c in cfg["cities"]:
        gx, gy = (c["x"] - b["x0"]) / tx, ny - (c["y"] - b["y0"]) / ty
        assert 0 <= gx <= nx and 0 <= gy <= ny, c["label"]


# --- frontend integrity ----------------------------------------------------
def test_frontend_component_exists_and_speaks_the_protocol():
    src = (REPO / "app" / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "streamlit:componentReady" in src
    assert "streamlit:setComponentValue" in src
    assert "snapToLand" in src


def test_frontend_states_it_does_not_rescale_metro_bands():
    src = (REPO / "app" / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "not rescaled" in src


def test_frontend_shows_the_literal_words_the_contract_requires():
    """'insufficient evidence' — not a zero, not a blank, not a dash."""
    src = (REPO / "app" / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "insufficient evidence" in src


def test_no_hardcoded_policy_numbers_in_the_frontend():
    src = (REPO / "app" / "frontend" / "index.html").read_text(encoding="utf-8")
    for forbidden in ("0.142", "0.084", "105000000000", "3140"):
        assert forbidden not in src, f"hardcoded figure {forbidden} in the frontend"


# --- every real scenario loads and builds ---------------------------------
@pytest.mark.parametrize("name", ["ctc_2021", "baseline", "ctc_1000", "flat_500", "eitc_match"])
def test_every_real_scenario_builds_a_payload(name):
    s = load_scenario(REPO / "scenarios" / f"{name}.json")
    p = build_payload(s, [])
    assert len(p["impact"]) == 4
    assert len(p["by_metro"]) == 6
    assert p["groups"]


def test_backtest_json_is_not_offered_as_a_scenario():
    """/scenarios also holds A6 output, which is not a policy scenario."""
    assert "backtest.json" not in {e["filename"] for e in list_scenarios()}
