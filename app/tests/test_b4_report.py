"""B4 acceptance: guardrails, the numeric check, and the always-visible panel."""
import json
import pathlib

from streamlit.testing.v1 import AppTest

from app.report import (
    SYSTEM_PROMPT,
    ReportError,
    ReportResult,
    Verification,
    collect_input_numbers,
    relevant_evidence,
    verify_numbers,
)

MAIN = str(pathlib.Path(__file__).resolve().parents[1] / "main.py")

SIM = {
    "policy_id": "ctc_2021", "label": "Expanded CTC", "n_seeds": 500,
    "impact": {
        "child_poverty_rate": {"baseline": 0.142, "median": 0.084, "p05": 0.071, "p95": 0.098},
        "annual_cost_usd": {"baseline": 0, "median": 105000000000,
                            "p05": 98000000000, "p95": 112000000000},
    },
    "opinion": {"overall_support": {"median": 0.58, "p05": 0.51, "p95": 0.65},
                "by_group": []},
    "warnings": ["No cost-of-living adjustment across metros."],
}


# --- the check itself ------------------------------------------------------
def test_clean_report_has_zero_unverified():
    text = ("Child poverty falls to 8.4% (5th-95th percentile: 7.1% to 9.8%) "
            "from a baseline of 14.2%. Overall support is 58% (51%-65%). "
            "Annual cost is $105.0B, ranging $98.0B to $112.0B.")
    v = verify_numbers(text, SIM)
    assert v.unverified_count == 0, [f.token for f in v.findings]
    assert v.checked > 5
    assert v.ok


def test_fabricated_subgroup_percentage_is_caught():
    v = verify_numbers("Support among rural voters is 44%.", SIM)
    assert [f.token for f in v.findings] == ["44%"]


def test_fabricated_count_is_caught():
    v = verify_numbers("Roughly 3.7 million children are lifted out of poverty.", SIM)
    assert any("3.7" in f.token for f in v.findings)


def test_rounding_to_a_different_value_is_caught():
    """8.9% is not a formatting variant of 8.4%."""
    v = verify_numbers("Child poverty falls to 8.9%.", SIM)
    assert v.unverified_count == 1


def test_reasonable_formatting_passes():
    """Percent/fraction, thousands separators and B suffixes are the same claim."""
    for text in ("The rate is 0.084.", "The rate is 8.4%.",
                 "Cost is $105,000,000,000.", "Cost is $105.0B."):
        assert verify_numbers(text, SIM).unverified_count == 0, text


def test_ordinals_and_ranges_are_not_treated_as_quantities():
    text = "The 5th-95th percentile band runs 7.1%-9.8%, and the 95th is 9.8%."
    assert verify_numbers(text, SIM).unverified_count == 0


def test_numbers_quoted_from_input_text_pass():
    """Evidence question wording is part of the input."""
    ev = [{"evidence_id": "ev_x", "support_pct": 0.54, "year": 2021,
           "question_wording": "up to $250 per month for each child 17 and under"}]
    text = "One 2021 survey tested $250 per month per child 17 and under (ev_x)."
    v = verify_numbers(text, SIM, ev)
    assert v.unverified_count == 0, [f.token for f in v.findings]


def test_unknown_evidence_id_is_caught():
    ev = [{"evidence_id": "ev_real", "support_pct": 0.5}]
    v = verify_numbers("As shown previously (ev_fake_042).", SIM, ev)
    assert v.missing_evidence_ids == ["ev_fake_042"]
    assert not v.ok


def test_known_evidence_id_passes():
    ev = [{"evidence_id": "ev_real", "support_pct": 0.5}]
    assert verify_numbers("As shown previously (ev_real).", SIM, ev).missing_evidence_ids == []


def test_input_pool_includes_percent_and_fraction_twins():
    pool = collect_input_numbers({"a": 0.084})
    assert 0.084 in pool and 8.4 in pool


# --- guardrails ------------------------------------------------------------
def test_system_prompt_carries_every_hard_guardrail():
    low = SYSTEM_PROMPT.lower()
    assert "every quantitative claim must come from the provided json" in low
    assert "do not compute" in low
    assert "evidence_id" in SYSTEM_PROMPT
    assert "never state a subgroup percentage" in low
    for section in ("Plain-English description", "Direct cost bearers",
                    "Historical analogues", "Uncertainty", "Limitations"):
        assert section in SYSTEM_PROMPT


def test_holdout_records_never_reach_the_report():
    ev = [{"evidence_id": "ev_a", "subgroup_type": "national", "holdout": False},
          {"evidence_id": "ev_h", "subgroup_type": "income_band", "holdout": True}]
    picked = relevant_evidence(SIM, ev)
    assert {r["evidence_id"] for r in picked} == {"ev_a"}


def test_demo_mode_generates_nothing_and_calls_nothing(monkeypatch):
    monkeypatch.setenv("POLICY_SIM_DEMO_MODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-never-used")
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network call")))
    out = generate = __import__("app.report", fromlist=["generate_report"]).generate_report
    res = out(SIM, [])
    assert isinstance(res, ReportError) and "DEMO_MODE" in res.message


# --- the panel -------------------------------------------------------------
def _app(outcome, scenario_dir):
    at = AppTest.from_file(MAIN, default_timeout=120)
    at.session_state["report_outcome"] = outcome
    at.run()
    assert not at.exception, at.exception
    return at


def _blob(at):
    return " ".join(str(m.value) for m in at.main.markdown)


def test_panel_shows_zero_unverified_when_clean(scenario_dir):
    v = Verification(findings=[], checked=74)
    at = _app(ReportResult(text="### Plain-English description\nBody.", verification=v),
              scenario_dir)
    text = _blob(at)
    assert "Numeric verification" in [s.value for s in at.main.subheader] or \
           "Numeric verification" in text
    assert "74 numeric tokens checked" in text
    assert "0 unverified" in text


def test_panel_lists_findings_when_dirty(scenario_dir):
    v = verify_numbers("Support among rural voters is 44%.", SIM)
    at = _app(ReportResult(text="body", verification=v), scenario_dir)
    text = _blob(at)
    assert "1 unverified" in text
    assert "44%" in text


def test_report_error_does_not_break_the_page(scenario_dir):
    at = _app(ReportError("Memo generation timed out after 60s.", "Figures unaffected."),
              scenario_dir)
    assert "timed out" in _blob(at)
    # The page must survive a memo failure: the outcome box is still there.
    assert "Child poverty rate" in _blob(at)
