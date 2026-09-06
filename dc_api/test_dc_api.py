"""
Contract tests for the DC evidence service.

Runs against the module directly, no server and no browser, so it belongs in
pytest. What it protects is the promise that makes this work at all: the Juniper
front end is unchanged, so every answer must carry exactly the fields its
PolicyResult type reads, and every number in one must come from the cache rather
than from anywhere else.

    python -m pytest dc_api/test_dc_api.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

pytest.importorskip("dc_api.server", reason="build the cache first: "
                                            "python dc_api/build_cache.py")
from dc_api import server as S  # noqa: E402
from dc_api.interpret import interpret  # noqa: E402

# Every field city/src/policy/types.ts reads off a response.
REQUIRED = {"runId", "originalQuestion", "specification", "status", "title",
            "explanation", "population", "geography", "timeframe", "estimate",
            "uncertainty", "evidence", "missingEvidence", "limitations",
            "validation"}

EXAMPLES = S.EXAMPLES


def ask(q):
    return S.run_query(q, interpret(q))


@pytest.mark.parametrize("question", EXAMPLES)
def test_every_example_returns_the_full_contract(question):
    r = ask(question)
    missing = REQUIRED - set(r)
    assert not missing, f"missing fields: {sorted(missing)}"
    assert isinstance(r["evidence"], list)
    assert isinstance(r["limitations"], list)
    assert isinstance(r["missingEvidence"], list)
    assert "description" in r["validation"]


@pytest.mark.parametrize("question", EXAMPLES)
def test_every_example_is_json_serialisable(question):
    json.dumps(ask(question))


def test_the_three_answerable_examples_answer():
    for q in EXAMPLES[:3]:
        r = ask(q)
        assert r["status"] == "ok", f"{q} -> {r['status']}"
        assert r["estimate"] and r["estimate"]["value"] is not None


def test_household_income_matches_the_cache_exactly():
    """The headline number must be a weighted count of ACS records, not a
    rounded or remembered figure."""
    r = ask("How many DC households had annual income below $50,000 in 2024?")
    inc, w = S.INCOME["incomes"], S.INCOME["weights"]
    expect = sum(wt for v, wt in zip(inc, w) if v < 50_000)
    assert r["estimate"]["value"] == round(expect)
    assert r["estimate"]["denominator"] == round(sum(w))


def test_ward_311_matches_the_cache_exactly():
    r = ask("How many 311 requests were recorded in Ward 8 in 2025?")
    assert r["estimate"]["value"] == S.SR["2025"]["byWard"]["Ward 8"]


def test_tract_prevalence_carries_the_publishers_interval():
    r = ask("What was diabetes prevalence in DC tract 11001000101 in 2023?")
    row = S.PLACES["byTract"]["11001000101|DIABETES|2023"]
    assert r["estimate"]["value"] == row["value"]
    assert r["uncertainty"]["lower"] == row["low"]
    assert r["uncertainty"]["upper"] == row["high"]


def test_affordability_is_declined_rather_than_substituted():
    """The one example we cannot source must say so, and must not quietly
    answer with a different measure."""
    r = ask("What share of DC adults reported being unable to afford a doctor in 2024?")
    assert r["status"] == "unsupported"
    assert r["estimate"] is None
    assert any("MEDCOST1" in m for m in r["missingEvidence"])
    joined = " ".join(r["limitations"]).lower()
    assert "not offered as a stand-in" in joined


def test_an_uninterpretable_question_keeps_its_constraints():
    r = ask("Which ward should we target to win the next election?")
    assert r["status"] == "unsupported"
    assert r["estimate"] is None
    assert r["missingEvidence"], "the unhandled question must be reported back"


def test_an_unknown_service_type_is_refused_not_guessed():
    r = ask("How many 311 requests were recorded in Ward 8 in 2025 for unicorns?")
    assert r["status"] == "unsupported"
    assert r["estimate"] is None


def test_an_unloaded_year_is_refused():
    r = ask("How many DC households had annual income below $50,000 in 1999?")
    assert r["status"] == "unsupported"
    assert r["estimate"] is None


def test_catalog_matches_the_loaded_evidence():
    c = S.catalog()
    assert c["apiVersion"] == "v1"
    kinds = {cap["kind"] for cap in c["capabilities"]}
    assert {"household_income", "service_requests", "health_prevalence"} <= kinds
    assert c["sources"] and all("url" in s for s in c["sources"])
    assert len(c["healthMeasures"]) == len(S.PLACES["measures"])


def test_runs_are_retrievable_for_show_on_map():
    r = ask(EXAMPLES[1])
    assert S.RUNS[r["runId"]] is r


def test_interpreter_does_not_drop_an_extra_restriction():
    """A question with a population restriction we cannot honour must come back
    unsupported, not silently answered for everybody."""
    spec = interpret("How many DC households with children under 5 had annual "
                     "income below $50,000 in 2024?")
    assert spec["kind"] == "unsupported"
    assert spec["unsupportedConstraints"]
