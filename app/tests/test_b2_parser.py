"""B2 acceptance: every validation path, exercised without a network call.

parse_response is pure, so the clamping, fence-stripping and scope rules are
tested against fixture strings rather than live model output. The five spec
inputs were additionally run against the real API; these lock the behaviour in.
"""
import json

import pytest

from app.parser import (
    MAX_PER_CHILD_CREDIT,
    Levers,
    ParseError,
    ParseResult,
    PolicySpec,
    parse_policy,
    parse_response,
)


def spec_json(**levers):
    base = {
        "policy_id": "t", "label": "T", "domain": "cash_transfer",
        "instrument": "child_tax_credit",
        "levers": {"credit_per_child_under_6": 0, "credit_per_child_6_to_17": 0,
                   "fully_refundable": False, "phaseout_start_single": None,
                   "phaseout_start_joint": None, "phaseout_rate": 0,
                   "flat_transfer_per_adult": 0},
        "parser_uncertainties": [],
    }
    base["levers"].update(levers)
    return json.dumps(base)


# --- happy path ------------------------------------------------------------
def test_valid_spec_parses():
    out = parse_response(spec_json(credit_per_child_under_6=3600, fully_refundable=True))
    assert isinstance(out, ParseResult)
    assert out.spec.levers.credit_per_child_under_6 == 3600
    assert out.spec.levers.fully_refundable is True


def test_noop_defaults_when_levers_omitted():
    out = parse_response(json.dumps({
        "policy_id": "t", "label": "T", "domain": "cash_transfer",
        "instrument": "flat_transfer", "parser_uncertainties": ["everything inferred"],
    }))
    assert isinstance(out, ParseResult)
    lv = out.spec.levers
    assert (lv.credit_per_child_under_6, lv.credit_per_child_6_to_17) == (0, 0)
    assert lv.fully_refundable is False
    assert lv.phaseout_start_single is None and lv.phaseout_rate == 0


# --- defensive de-fencing --------------------------------------------------
@pytest.mark.parametrize("wrap", [
    "```json\n{body}\n```",
    "```\n{body}\n```",
    "Here is the spec:\n{body}\nLet me know if you need changes.",
])
def test_markdown_fences_and_prose_are_stripped(wrap):
    out = parse_response(wrap.format(body=spec_json(credit_per_child_6_to_17=3000)))
    assert isinstance(out, ParseResult), getattr(out, "detail", "")
    assert out.spec.levers.credit_per_child_6_to_17 == 3000


# --- rejections ------------------------------------------------------------
def test_out_of_scope_signal_from_model():
    out = parse_response('{"error": "out_of_scope", "reason": "A transit project."}')
    assert isinstance(out, ParseError) and out.kind == "out_of_scope"
    assert "transit" in out.detail


def test_out_of_scope_via_domain_field():
    bad = json.loads(spec_json())
    bad["domain"] = "infrastructure"
    out = parse_response(json.dumps(bad))
    assert isinstance(out, ParseError) and out.kind == "out_of_scope"


def test_implausible_credit_rejected_not_clamped():
    """$9,000,000 must fail the parse, not be silently pulled to the ceiling."""
    out = parse_response(spec_json(credit_per_child_under_6=9_000_000))
    assert isinstance(out, ParseError) and out.kind == "implausible"
    assert "20,000" in out.detail


def test_credit_exactly_at_ceiling_is_allowed():
    out = parse_response(spec_json(credit_per_child_under_6=MAX_PER_CHILD_CREDIT))
    assert isinstance(out, ParseResult)


def test_negative_credit_rejected():
    out = parse_response(spec_json(credit_per_child_under_6=-100))
    assert isinstance(out, ParseError) and out.kind == "implausible"


def test_unknown_lever_key_rejected():
    bad = json.loads(spec_json())
    bad["levers"]["credit_per_dog"] = 500
    out = parse_response(json.dumps(bad))
    assert isinstance(out, ParseError) and out.kind == "schema"
    assert "credit_per_dog" in out.detail


def test_percent_style_phaseout_rate_rejected():
    """5 is not 5%. A rate outside 0-1 would silently 100x the phaseout."""
    out = parse_response(spec_json(phaseout_rate=5))
    assert isinstance(out, ParseError)
    assert "0.05" in out.detail


def test_non_json_returns_structured_error_not_exception():
    out = parse_response("I'm sorry, I can't help with that.")
    assert isinstance(out, ParseError) and out.kind == "invalid_json"


def test_invented_outcome_field_is_rejected():
    """The model must never hand back a support or impact number."""
    bad = json.loads(spec_json())
    bad["estimated_support"] = 0.62
    out = parse_response(json.dumps(bad))
    assert isinstance(out, ParseError) and out.kind == "schema"
    assert "estimated_support" in out.detail


# --- never raises ----------------------------------------------------------
@pytest.mark.parametrize("junk", ["", "   ", "{", "[]", "null", '{"levers": 5}', "\x00\x01"])
def test_parse_response_never_raises(junk):
    out = parse_response(junk)
    assert isinstance(out, (ParseResult, ParseError))


def test_empty_input_short_circuits_without_network(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = parse_policy("   ")
    assert isinstance(out, ParseError)


def test_missing_api_key_is_a_readable_message(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("POLICY_SIM_DEMO_MODE", raising=False)
    out = parse_policy("give every kid $1000")
    assert isinstance(out, ParseError) and out.kind == "api"
    assert "ANTHROPIC_API_KEY" in out.message


def test_demo_mode_makes_no_network_call(monkeypatch):
    monkeypatch.setenv("POLICY_SIM_DEMO_MODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-be-used")

    def boom(*a, **k):
        raise AssertionError("DEMO_MODE made a network call")

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", boom)
    out = parse_policy("give every kid $1000")
    assert isinstance(out, ParseError) and "DEMO_MODE" in out.message


# --- system prompt contract ------------------------------------------------
def test_system_prompt_forbids_inventing_outcomes():
    from app.parser import SYSTEM_PROMPT
    lowered = SYSTEM_PROMPT.lower()
    assert "raw json only" in lowered
    assert "no markdown code fences" in lowered
    assert "approval rating" in lowered and "support percentage" in lowered
    assert "out_of_scope" in SYSTEM_PROMPT
    for lever in Levers.model_fields:
        assert lever in SYSTEM_PROMPT, f"{lever} missing from the schema in the prompt"
