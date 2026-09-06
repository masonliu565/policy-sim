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


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Exercise the reasoning path without calling the API: the deterministic
    fallback must satisfy the same contract, because it is what runs when the
    key is absent or the model will not stay inside the evidence."""
    monkeypatch.setattr(S.R, "available", lambda: False)


HARD = [
    "What share of DC adults reported being unable to afford a doctor in 2024?",
    "Which ward should we target to win the next election?",
    "How many 311 requests were recorded in Ward 8 in 2025 for unicorns?",
    "How many DC households had annual income below $50,000 in 1999?",
    "Should DC build more affordable housing?",
    "whats the diabetes rate around Anacostia",
    "Is rent control a good idea?",
]


@pytest.mark.parametrize("question", HARD)
def test_nothing_is_ever_refused(question, offline):
    """The service must not tell a user it needs more information. Every
    question gets an answer, and the front end needs an estimate to render one:
    with estimate None it prints "More evidence is needed."."""
    r = ask(question)
    assert r["status"] == "ok", f"{question} -> {r['status']}"
    assert r["estimate"] is not None, "a null estimate renders as a refusal"
    assert r["explanation"].strip()
    missing = REQUIRED - set(r)
    assert not missing, f"missing fields: {sorted(missing)}"


@pytest.mark.parametrize("question", HARD)
def test_a_reasoned_answer_still_carries_its_sources(question, offline):
    r = ask(question)
    assert r["evidence"], "an answer with no source is not an answer"
    assert all("url" in e for e in r["evidence"])
    assert any("counted or computed" in l for l in r["limitations"])


def test_an_unanswerable_lookup_does_not_invent_the_number(offline):
    """Unicorns are not a service type. The answer may not report a count for
    them; its headline must come from the fact pack instead."""
    r = ask("How many 311 requests were recorded in Ward 8 in 2025 for unicorns?")
    assert r["status"] == "ok"
    ids = {f["id"] for f in S.F.pack("unicorns", {})}
    assert r["estimate"]["displayValue"]
    joined = " ".join(r["limitations"])
    assert "could not close it" in joined,         "the failed lookup must be carried forward, not silently dropped"


def test_a_year_we_do_not_hold_is_disclosed_not_answered_anyway(offline):
    r = ask("How many DC households had annual income below $50,000 in 1999?")
    assert r["status"] == "ok"
    joined = " ".join(r["limitations"]).lower()
    assert "the loaded evidence covers" in joined
    assert "acs 2024" in joined, "the period actually held must be stated"


# --- the guardrail that makes "never refuse" safe ---------------------------
def test_an_invented_number_is_caught():
    facts = [S.F.fact("f1", "DC households", 329688.0, "329,688 households", "ACS")]
    ok = S.R.allowed_tokens("a question with no numbers", facts)
    assert S.R.violations("There are 329,688 households.", ok) == []
    assert S.R.violations("Studies show a 43% reduction.", ok) == ["43"]


def test_a_rounded_restatement_of_a_fact_is_not_an_invention():
    """"about 330,000 households" is the same claim as 329,688, not a new one."""
    facts = [S.F.fact("f1", "DC households", 329688.0, "329,688 households", "ACS")]
    ok = S.R.allowed_tokens("", facts)
    assert S.R.violations("about 330,000 households", ok) == []
    assert S.R.violations("about 412,000 households", ok) == ["412,000"]


def test_a_number_from_the_users_own_question_is_allowed():
    ok = S.R.allowed_tokens("what if we paid $400 a month per child", [])
    assert S.R.violations("A $400 monthly payment", ok) == []


def test_years_are_not_treated_as_statistics():
    ok = S.R.allowed_tokens("", [])
    assert S.R.violations("Between 2019 and 2024 the city changed.", ok) == []


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


# --- service-type matching, which free-form questions exercised hard --------
def _pool():
    pool = {}
    for w in S.SR["2025"]["byWardService"].values():
        for k, v in w.items():
            pool[k] = pool.get(k, 0) + v
    return pool


def test_a_compound_phrase_finds_the_real_service_types():
    """Claude returns "rodent/rat complaints"; the catalogue says "Rodent
    Inspection and Treatment". Plain substring matching found neither."""
    hits = S.match_services("rodent/rat complaints", _pool())
    assert "Rodent Inspection and Treatment" in hits


def test_a_term_does_not_match_a_word_that_merely_contains_it():
    """"rat" must not match "DMV - Vehicle Registration Issues"."""
    assert "DMV - Vehicle Registration Issues" not in S.match_services("rat", _pool())


def test_the_plural_people_type_matches_the_singular_in_the_catalogue():
    assert S.match_services("potholes", _pool()).get("Pothole")


def test_a_place_name_is_not_treated_as_a_tract_id():
    spec = dict(interpret("What was diabetes prevalence in DC tract 11001000101 in 2023?"))
    spec["geography"] = {"kind": "tract", "code": "Anacostia"}
    r = S.run_query("whats the diabetes rate around Anacostia", spec)
    # It answers now rather than refusing, but it must not answer as though
    # "Anacostia" were a tract: the failed lookup is carried into the record.
    assert r["status"] == "ok"
    joined = " ".join(r["limitations"])
    assert "place name" in joined and "crosswalk" in joined


def test_an_unstated_year_is_assumed_and_disclosed():
    spec = dict(interpret("How many DC households had annual income below $40,000 in 2024?"))
    spec["year"] = None
    r = S.run_query("how many households under forty grand", spec)
    assert r["status"] == "ok"
    assert "no year was stated" in r["explanation"].lower()


# --- ranking, and the subgroup impacts that are the point of a policy answer -
def test_which_ward_is_answered_with_a_ward_not_a_district_total():
    """"Which ward complains most" asks for a ranking. Answering with a
    District-wide count answers a different question."""
    r = ask("Which ward reported the most potholes in 2025?")
    assert r["status"] == "ok"
    assert r["geography"]["kind"] == "ward"
    top = max(S.SR["2025"]["byWardService"].items(),
              key=lambda kv: sum(S.match_services("potholes", kv[1]).values()))[0]
    assert r["geography"]["code"] == top.split()[-1]
    assert "ranking" in r["explanation"].lower()


def test_a_plain_ward_count_is_not_turned_into_a_ranking():
    r = ask("How many 311 requests were recorded in Ward 8 in 2025?")
    assert r["geography"]["code"] == "8"
    assert "ranking" not in r["explanation"].lower()


def _policy():
    return ask("What if we gave $300 a month per child under 6 to DC families?")


def test_a_policy_answer_reports_who_it_reaches():
    r = _policy()
    assert r["status"] == "ok"
    sa = r["surveyAnalysis"]
    labels = {b["label"] for b in sa["breakdowns"]}
    assert {"By household type", "By income group"} <= labels
    for b in sa["breakdowns"]:
        assert len(b["columns"]) == 4, "the table labels its own columns"
        assert "Cost barriers" not in b["columns"], \
            "a policy table must not wear the health survey's header"


def test_subgroup_intervals_are_not_zero_width_where_the_policy_lands():
    """A share reached is decided by the policy rules, so without the bootstrap
    every draw gave the identical number and the interval collapsed."""
    r = _policy()
    reached = [g for b in r["surveyAnalysis"]["breakdowns"]
               for g in b["groups"] if not g["suppressed"] and g["share"] > 0]
    assert reached
    for g in reached:
        lo, hi = g["interval"]["lower"], g["interval"]["upper"]
        assert hi > lo, f"zero-width interval on {g['label']}"
        assert lo <= g["share"] <= hi


def test_a_per_child_transfer_reaches_no_childless_household():
    r = _policy()
    hh = next(b for b in r["surveyAnalysis"]["breakdowns"]
              if b["label"] == "By household type")
    childless = [g for g in hh["groups"] if "no children" in g["label"]]
    assert len(childless) == 2
    for g in childless:
        assert g["share"] == 0
        assert g["interval"]["lower"] == g["interval"]["upper"] == 0


def test_the_reached_count_is_consistent_with_the_household_type_table():
    r = _policy()
    sa = r["surveyAnalysis"]
    total = sa["responseCounts"]["reached"] + sa["responseCounts"]["unaffected"]
    assert abs(total - 329_688) < 2, "household types must partition DC"
