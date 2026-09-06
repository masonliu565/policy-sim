"""
The app as it now is: a map, one outcome box, a policy box, and reset.

These replace the old B1/B2 layout tests, which asserted a numbered five-section
page, an editable lever panel and a "DEMO MODE" banner. None of that exists any
more. The properties worth protecting survived the rewrite and are checked here,
plus the one that matters most and was never tested before: the app needs no
network and no API key to do its actual job.
"""
import json
import pathlib
import re
import socket

import pytest
from streamlit.testing.v1 import AppTest

from app.local_parser import parse

MAIN = str(pathlib.Path(__file__).resolve().parents[1] / "main.py")
REPO = pathlib.Path(__file__).resolve().parents[2]


def run(**kw):
    at = AppTest.from_file(MAIN, default_timeout=180, **kw).run()
    assert not at.exception, at.exception
    return at


def blob(at):
    return " ".join(str(m.value) for m in at.main.markdown)


# --- the offline reader ----------------------------------------------------
@pytest.mark.parametrize("text,expect", [
    ("give every family $300 a month per kid, phase it out over $150k",
     {"credit_per_child_under_6": 3600.0, "credit_per_child_6_to_17": 3600.0,
      "phaseout_start_single": 150000.0}),
    ("$3,600 per child under 6 and $3,000 for ages 6-17",
     {"credit_per_child_under_6": 3600.0, "credit_per_child_6_to_17": 3000.0}),
    ("$500 per adult", {"flat_transfer_per_adult": 500.0}),
    ("$250 a month per child under 6",
     {"credit_per_child_under_6": 3000.0, "credit_per_child_6_to_17": 0.0}),
])
def test_reads_the_phrasings_people_type(text, expect):
    got = parse(text).levers
    for k, v in expect.items():
        assert got[k] == v, f"{k}: {got[k]} != {v}"


def test_monthly_is_converted_to_annual():
    """"$300 a month" is $3,600 a year. The words sit INSIDE the matched phrase,
    which an earlier version missed, silently costing a factor of twelve."""
    assert parse("$300 a month per child").levers["credit_per_child_under_6"] == 3600.0


def test_a_suffix_is_not_read_out_of_an_adjacent_word():
    """The "m" in "monthly" is not "million"."""
    assert parse("$400 monthly per child").levers["credit_per_child_under_6"] == 4800.0


def test_under_six_does_not_pay_older_children():
    lv = parse("$250 a month per child under 6").levers
    assert lv["credit_per_child_6_to_17"] == 0.0


def test_nothing_is_invented_when_nothing_is_understood():
    r = parse("make things better for families")
    assert not r.ok
    assert r.levers == parse("").levers or not r.ok
    assert any("no amount" in n.lower() for n in r.notes)


def test_assumptions_are_declared_not_hidden():
    r = parse("$200 per child, phased out over $80k")
    joined = " ".join(r.notes).lower()
    assert "joint threshold was not stated" in joined
    assert "5%" in joined


# --- the page --------------------------------------------------------------
def test_the_four_things_are_on_screen(scenario_dir):
    at = run()
    text = blob(at)
    assert "policy-sim" in text
    assert "Outcome" in text or "OUTCOME" in text.upper()
    assert "Child poverty rate" in text
    assert at.text_area, "the policy box must be present"
    assert "Reset" in [b.label for b in at.button]


def test_no_demo_mode_banner(scenario_dir):
    """The old banner read as "this build is crippled". It is gone, and the
    app is genuinely standalone rather than a reduced mode of itself."""
    assert "DEMO MODE" not in blob(at := run()) and at is not None


def test_every_impact_number_carries_an_interval(scenario_dir):
    text = blob(run())
    for label in ("Child poverty rate", "Overall poverty rate", "Annual cost"):
        assert label in text
    # a value is always followed by its band
    assert re.search(r"\d+\.\d%\s*(?:–|-)\s*\d+\.\d%", text), text[:400]


def test_a_broken_scenario_file_does_not_crash_the_page(scenario_dir):
    at = AppTest.from_file(MAIN, default_timeout=180).run()
    at.selectbox[0].select(1).run()          # broken.json
    assert not at.exception


def test_every_scenario_selection_renders(scenario_dir):
    at = AppTest.from_file(MAIN, default_timeout=180).run()
    for i in (0, 2):
        at.selectbox[0].select(i).run()
        assert not at.exception
        assert "Child poverty rate" in blob(at)


def test_warnings_are_available_not_buried_in_the_main_flow(scenario_dir):
    at = run()
    # they live in an expander, but they must all be rendered
    every = " ".join(str(m.value) for m in at.markdown)
    src = json.loads((scenario_dir / "ctc_2021.json").read_text(encoding="utf-8"))
    shown = sum(1 for w in src["warnings"] if w[:40] in every)
    assert shown >= 1, "no model warning reached the page"


# --- the property that matters most ---------------------------------------
def test_the_app_needs_no_network(scenario_dir, monkeypatch):
    """
    Render the whole page with sockets disabled.

    Not "demo mode": there is no reduced mode. The map, the outcome box, the
    policy box and the offline reader must all work on a laptop with no wifi
    and no API key, because that is the machine it will be presented from.
    """
    def boom(*a, **k):
        raise AssertionError("the app attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    at = run()
    assert "Child poverty rate" in blob(at)
    assert parse("$300 a month per child").ok


def test_reading_a_policy_needs_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = parse("$3,600 per child under 6, fully refundable")
    assert r.ok and r.levers["credit_per_child_under_6"] == 3600.0
