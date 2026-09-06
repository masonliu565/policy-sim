"""B2 acceptance: the read-back panel is visible and prominent, uncertainties render."""
import pathlib

from streamlit.testing.v1 import AppTest

from app.parser import ParseError, ParseResult, parse_response

MAIN = str(pathlib.Path(__file__).resolve().parents[1] / "main.py")

SPEC = """{"policy_id":"m300","label":"$300 Monthly Per-Child Transfer",
"domain":"cash_transfer","instrument":"child_tax_credit",
"levers":{"credit_per_child_under_6":3600,"credit_per_child_6_to_17":3600,
"fully_refundable":false,"phaseout_start_single":150000,
"phaseout_start_joint":150000,"phaseout_rate":0,"flat_transfer_per_adult":0},
"parser_uncertainties":["Monthly $300 annualised to $3,600.",
"Refundability not stated; defaulted to false."]}"""


def app_with(outcome, scenario_dir):
    at = AppTest.from_file(MAIN, default_timeout=120)
    at.session_state["parse_outcome"] = outcome
    at.run()
    assert not at.exception, at.exception
    return at


def blob(at):
    return " ".join(str(m.value) for m in at.main.markdown)


def test_readback_panel_is_prominent_and_not_an_expander(scenario_dir):
    at = app_with(parse_response(SPEC), scenario_dir)
    text = blob(at)
    assert "Here's how I read your policy" in text
    assert "ps-readback" in text, "panel must use the bordered prominent style"
    assert not at.expander, "the reading must not be hidden behind an expander"


def test_every_lever_is_editable(scenario_dir):
    at = app_with(parse_response(SPEC), scenario_dir)
    labels = [n.label for n in at.number_input] + [c.label for c in at.checkbox]
    for want in ("Credit per child under 6 ($/yr)", "Credit per child 6–17 ($/yr)",
                 "Fully refundable", "Phaseout starts, single ($)",
                 "Phaseout starts, joint ($)", "Phaseout rate (fraction)",
                 "Flat transfer per adult ($/yr)"):
        assert want in labels, f"lever not editable: {want}"


def test_uncertainties_render_as_a_visible_list(scenario_dir):
    at = app_with(parse_response(SPEC), scenario_dir)
    text = blob(at)
    assert "What I had to guess" in text
    assert "Monthly $300 annualised to $3,600." in text
    assert "Refundability not stated; defaulted to false." in text


def test_empty_uncertainties_are_called_out_not_hidden(scenario_dir):
    import json
    spec = json.loads(SPEC)
    spec["parser_uncertainties"] = []
    at = app_with(parse_response(json.dumps(spec)), scenario_dir)
    assert "reported no inferred fields" in blob(at)


def test_parse_error_renders_readably_and_simulates_nothing(scenario_dir):
    err = ParseError("out_of_scope", "That does not look like a cash transfer.",
                     "This is a transit infrastructure project.")
    at = app_with(err, scenario_dir)
    text = blob(at)
    assert "Could not read that as a policy" in text
    assert "transit infrastructure project" in text
    assert "Nothing was simulated" in text
    assert not at.exception


def test_scenario_figures_still_render_after_a_parse_failure(scenario_dir):
    """A bad parse must not take the rest of the page down."""
    at = app_with(ParseError("api", "The parser could not be reached.", ""), scenario_dir)
    at.selectbox[0].select(2).run()
    headers = [h.value for h in at.main.header]
    assert "2 · Material impact" in headers and "3 · Support by group" in headers
