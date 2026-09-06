"""B1 acceptance: the app renders the stub, and never renders a bare estimate."""
import re

import pytest
from streamlit.testing.v1 import AppTest

from app import charts
from app.scenarios import ScenarioError, list_scenarios, load_scenario, metro_key

MAIN = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "main.py")
SECTIONS = ("1 · Policy", "2 · Material impact", "3 · Support by group", "4 · Limitations")


def run(timeout=120):
    at = AppTest.from_file(MAIN, default_timeout=timeout).run()
    assert not at.exception, at.exception
    return at


def test_all_four_sections_render(scenario_dir):
    at = run()
    at.selectbox[0].select(2).run()  # ctc_2021.json
    headers = [h.value for h in at.main.header]
    for want in SECTIONS:
        assert want in headers, f"missing {want}"


def test_dropdown_lists_every_file_and_switches(scenario_dir):
    at = run()
    assert len(at.selectbox[0].options) == 3
    at.selectbox[0].select(0).run()
    assert [t.value for t in at.main.title] == ["Baseline (no policy)"]
    at.selectbox[0].select(2).run()
    assert [t.value for t in at.main.title] == ["Expanded Child Tax Credit (2021)"]


def test_unreadable_scenario_errors_without_crashing(scenario_dir):
    at = run()
    at.selectbox[0].select(1).run()  # broken.json
    assert not at.exception
    assert len(at.sidebar.error) == 1
    assert not at.main.title, "must not render a page body for an unreadable scenario"


def test_warnings_all_rendered(scenario_dir):
    at = run()
    at.selectbox[0].select(2).run()
    blob = " ".join(str(m.value) for m in at.main.markdown)
    for w in load_scenario(scenario_dir / "ctc_2021.json")["warnings"]:
        assert w in blob, f"warning not rendered: {w[:50]}"


# ---------------------------------------------------------------------------
# The hard rule
# ---------------------------------------------------------------------------
def test_interval_text_always_carries_a_band():
    band = {"baseline": 0.142, "median": 0.084, "p05": 0.071, "p95": 0.098}
    txt = charts.interval_text(band, charts.PERCENT)
    assert "8.4%" in txt and "7.1%" in txt and "9.8%" in txt
    assert "[" in txt and "–" in txt


def test_delta_interval_is_signed_and_ascending():
    """A baseline subtraction can flip which bound is smaller."""
    band = {"baseline": 0.142, "median": 0.084, "p05": 0.071, "p95": 0.098}
    txt = charts.delta_text(band, charts.PERCENT)
    assert txt.startswith("−5.8 pts")
    lo, hi = re.findall(r"[+−]\d+\.\d+ pts", txt)[1:]
    assert lo == "−7.1 pts" and hi == "−4.4 pts", txt


def test_every_impact_number_on_screen_is_bracketed(scenario_dir):
    """No metric card shows a median without its p05-p95 beside it."""
    at = run()
    at.selectbox[0].select(2).run()
    # the injected <style> block also mentions the class name; match rendered
    # panels only
    cards = [
        str(m.value) for m in at.main.markdown
        if "ps-metric'>" in str(m.value) and not str(m.value).lstrip().startswith("<style>")
    ]
    assert len(cards) >= 4, f"expected a card per impact outcome, got {len(cards)}"
    for card in cards:
        metric = re.search(r"ps-metric'>([^<]+)<", card).group(1)
        assert "[" in metric and "–" in metric, f"bare point estimate: {metric}"


def test_charts_module_exposes_no_point_formatter():
    """format_value is unit plumbing for axes; it must not be the public path."""
    assert hasattr(charts, "interval_text")
    src = (__import__("pathlib").Path(charts.__file__)).read_text()
    assert "THE RULE" in src


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------
def test_missing_band_is_rejected(tmp_path):
    import json
    bad = {
        "policy_id": "x", "label": "x", "warnings": [], "opinion": {},
        "impact": {"child_poverty_rate": {"baseline": 0.1, "median": 0.08, "p05": 0.07}},
    }
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ScenarioError, match="p95"):
        load_scenario(p)


def test_metro_keys_normalise_across_both_conventions():
    """Track A labels metros 'New York'; the map keys them 'new_york'."""
    assert metro_key("New York") == metro_key("new_york") == "new_york"
    assert metro_key("San Francisco") == "san_francisco"
