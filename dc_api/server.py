"""
Evidence service for the DC atlas front end.

The front end is used almost unchanged -- see dc_api/patch_frontend.py for the
one patch and why it exists. It expects a local service on
/api/v1 with four endpoints and a specific answer shape; this implements that
shape over our own evidence, and serves the atlas's built files from the same
origin so the app is one process.

    python dc_api/server.py                  # http://127.0.0.1:4318
    python dc_api/server.py --port 4400

Endpoints, matching the front end's contract exactly:

    GET  /api/v1/health        readiness and how many sources loaded
    GET  /api/v1/catalog       capabilities, sources, measures, service types
    POST /api/v1/query         {question, specification?} -> PolicyResult
    GET  /api/v1/runs/<uuid>   a previous answer, for "Show on map"
    GET  /*                    the built atlas

WHAT IT ANSWERS, and from what:

    household_income   ACS 2024 1-Year PUMS, occupied DC households, ADJINC
                       adjusted. A weighted count over 3,083 household records,
                       computed exactly at the requested threshold rather than
                       read off pre-cut bands.
    service_requests   DC GIS 311 layers, counted by ward and service type.
    health_prevalence  CDC PLACES tract estimates, carrying the publisher's own
                       confidence limits.
    policy_simulation  our microsimulation, run on DC households.

    Anything else is answered by the reasoning layer (dc_api/reason.py), which
    argues from a pack of measured facts and is forbidden from stating a number
    that is not in it. The service never returns "more evidence is needed": a
    question the data cannot close still gets an answer that says what is
    established, what follows, and what would change it.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import uuid
from bisect import bisect_left, bisect_right
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CACHE = HERE / "cache"
sys.path.insert(0, str(REPO))

from dc_api.interpret import interpret as interpret_local  # noqa: E402
from dc_api import interpret_claude  # noqa: E402
from dc_api import facts as F  # noqa: E402
from dc_api import reason as R  # noqa: E402


def interpret(question: str):
    """Local patterns first; Claude only for what they cannot match.

    The pattern interpreter is deterministic, free and offline, so it always
    gets first refusal. Claude is asked only when the patterns return
    "unsupported", and even then it returns a REQUEST, never a number.
    """
    spec = interpret_local(question)
    if spec["kind"] != "unsupported":
        return spec
    if interpret_claude.available():
        got = interpret_claude.interpret(question)
        if got and got.get("kind") != "unsupported":
            return got
        if got:
            return got
    return spec

ENGINE_VERSION = "policy-sim-dc/1"
RUNS: Dict[str, Dict[str, Any]] = {}

WARD_LABEL = {str(i): f"Ward {i}" for i in range(1, 9)}

EXAMPLES = [
    "How many 311 requests were recorded in Ward 8 in 2025?",
    "How many DC households had annual income below $50,000 in 2024?",
    "What was diabetes prevalence in DC tract 11001000101 in 2023?",
    "What share of DC adults reported being unable to afford a doctor in 2024?",
]


def load(name: str) -> Dict[str, Any]:
    p = CACHE / name
    if not p.exists():
        raise SystemExit(
            f"ERROR: {p} missing. Build the evidence cache first:\n"
            f"    python dc_api/build_cache.py")
    return json.loads(p.read_text(encoding="utf-8"))


SR = load("service_requests.json")
PLACES = load("places.json")
INCOME = load("household_income.json")
MANIFEST = load("manifest.json")

SOURCE_BY_KIND = {
    "service_requests": MANIFEST["sources"][0],
    "health_prevalence": MANIFEST["sources"][1],
    "household_income": MANIFEST["sources"][2],
    "policy_simulation": MANIFEST["sources"][2],
}


def evidence_for(kind: str) -> List[Dict[str, str]]:
    s = SOURCE_BY_KIND.get(kind)
    if not s:
        return []
    return [{"publisher": s["publisher"], "dataset": s["datasetId"],
             "url": s["url"], "referencePeriod": s["referencePeriod"]}]


def _district_code(geo):
    geo = dict(geo or {"kind": "district", "code": "11"})
    if geo.get("kind") == "district" and not geo.get("code"):
        geo["code"] = "11"
    return geo


def result(spec: Dict[str, Any], question: str, **kw) -> Dict[str, Any]:
    """A PolicyResult with every field the front end reads."""
    rid = str(uuid.uuid4())
    out = {
        "runId": rid, "originalQuestion": question, "specification": spec,
        "status": kw.get("status", "ok"), "title": kw.get("title", ""),
        "explanation": kw.get("explanation", ""),
        "population": spec.get("population", ""),
        # The map resolves the District only on code "11"; a null code makes
        # the "Show on map" button read "Boundary unavailable", which looks
        # like a failure when the answer is District-wide and perfectly fine.
        "geography": _district_code(spec.get("geography")),
        "timeframe": spec.get("timeframe", ""),
        "estimate": kw.get("estimate"), "uncertainty": kw.get("uncertainty"),
        "evidence": kw.get("evidence", evidence_for(spec.get("kind", ""))),
        "missingEvidence": kw.get("missingEvidence", []),
        "limitations": kw.get("limitations", []),
        "validation": kw.get("validation", {
            "description": "No behavioural or causal forecast has been trained "
                           "or validated. Counts are counts."}),
    }
    if kw.get("surveyAnalysis"):
        out["surveyAnalysis"] = kw["surveyAnalysis"]
    RUNS[rid] = out
    return out



STOPWORDS = {"complaint", "complaints", "request", "requests", "issue", "issues",
             "problem", "problems", "service", "report", "reports", "about",
             "related", "and", "or", "the", "for", "in", "of",
             # verb forms of the same filler: "which ward complains most about
             # rats" was matching "Pet Waste Complaint" on "complains".
             "complain", "complains", "complained", "complaining", "reporting",
             "requesting", "asking", "asks", "filed", "files", "filing"}


def match_services(service: str, pool: Dict[str, int]) -> Dict[str, int]:
    """
    Find the 311 service types a phrase refers to.

    Naive substring matching failed twice in one question. "rodent/rat
    complaints" matched nothing, because no single service type contains that
    whole phrase -- the real one is "Rodent Inspection and Treatment". And
    searching for "rat" matched "DMV - Vehicle Registration Issues", because
    "rat" sits inside "Registration".

    So: split the phrase on separators, drop filler words, and match each
    remaining term on WORD BOUNDARIES. A term also matches a longer word it
    starts (rodent -> rodents), which is what people mean, without matching a
    word that merely contains it.
    """
    terms = [t for t in re.split(r"[\/,&]|\band\b|\bor\b", service.lower())
             if t.strip()]
    words = []
    for term in terms:
        for w in re.findall(r"[a-z]{3,}", term):
            if w not in STOPWORDS:
                words.append(w)
    if not words:
        words = [w for w in re.findall(r"[a-z]{3,}", service.lower())]
    # People type the plural; the service catalogue uses the singular ("rats"
    # against "Rat Replacement Containers"). Match the stem too. Anchoring on a
    # word boundary keeps "rat" out of "Registration".
    stems = set(words)
    for w in words:
        if len(w) > 3 and w.endswith("s"):
            stems.add(w[:-1])
    words = sorted(stems)
    hits = {}
    for name, count in pool.items():
        low = name.lower()
        if any(re.search(r"\b" + re.escape(w), low) for w in words):
            hits[name] = count
    return hits

# ---------------------------------------------------------------------------
def answer_household_income(spec, question):
    puma = spec["geography"].get("code") if spec["geography"]["kind"] == "puma" else None
    src = INCOME["byPuma"].get(puma) if puma else INCOME
    if src is None:
        return result(spec, question, status="unsupported",
                      title="That PUMA is not in the data",
                      explanation=f"PUMA {puma} has no occupied-household records "
                                  f"in the ACS 2024 DC sample.",
                      missingEvidence=[f"ACS 2024 records for PUMA {puma}"])
    # A free-form question often states no year. Refusing it would be pedantic
    # when exactly one year is loaded; assume that year and say so in the
    # answer rather than silently.
    assumed_year = spec.get("year") is None
    if assumed_year:
        spec = {**spec, "year": INCOME["year"],
                "timeframe": f"{INCOME['year']} (assumed: no year was stated)"}
    if spec["year"] != INCOME["year"]:
        return result(spec, question, status="unsupported",
                      title=f"{spec['year']} is not loaded",
                      explanation=f"Household income is loaded for "
                                  f"{INCOME['year']} only.",
                      missingEvidence=[f"ACS {spec['year']} DC household records"])

    incomes, weights = src["incomes"], src["weights"]
    thr = float(spec["incomeThreshold"])
    cut = bisect_left(incomes, thr) if spec["incomeComparison"] == "lt" \
        else bisect_right(incomes, thr)
    below_w = sum(weights[:cut])
    total_w = sum(weights)
    n_below, n_total = cut, len(incomes)
    share = below_w / total_w if total_w else 0.0

    # Sampling interval from the unweighted record count behind the estimate.
    # A weighted count from a survey is not exact and must not be shown as if
    # it were; this is a normal-approximation interval on the share, widened by
    # a conservative design effect for the household weights.
    deff = 1.6
    se = (share * (1 - share) / max(n_total, 1)) ** 0.5 * deff ** 0.5
    lo, hi = max(0.0, share - 1.96 * se), min(1.0, share + 1.96 * se)

    where = f"PUMA {puma}" if puma else "the District"
    comp = "below" if spec["incomeComparison"] == "lt" else "at or below"
    return result(
        spec, question,
        title=f"{below_w:,.0f} DC households",
        explanation=(
            f"{below_w:,.0f} of {total_w:,.0f} occupied households in {where} "
            f"reported annual income {comp} ${thr:,.0f} in {INCOME['year']}, "
            f"which is {share:.1%}. Weighted from {n_below:,} of {n_total:,} "
            f"ACS household records using the Census household weight, with "
            f"income adjusted to constant {INCOME['year']} dollars "
            f"(ADJINC {INCOME['adjinc']:.6f})."
            + (f" No year was stated in the question, so {INCOME['year']} was "
               f"used; it is the only year loaded." if assumed_year else "")),
        estimate={"kind": "count", "value": round(below_w),
                  "unit": "households", "populationShare": round(share, 4),
                  "denominator": round(total_w)},
        uncertainty={"level": 0.95, "lower": round(lo * total_w),
                     "upper": round(hi * total_w),
                     "method": f"Normal approximation on the share from "
                               f"{n_total:,} sampled households, with a design "
                               f"effect of {deff} for the household weights.",
                     "limitations": "Survey estimate, not a register count."},
        limitations=[
            "Occupied households only. Group quarters and vacant units are excluded.",
            "Income is household income for the previous 12 months, adjusted to "
            "constant dollars. It is not a tax or eligibility definition.",
            "No other eligibility condition, participation or behavioural "
            "response is represented.",
        ])


def answer_service_requests(spec, question):
    assumed_year = spec.get("year") is None
    if assumed_year:
        spec = {**spec, "year": max(int(y) for y in SR)}
    year = str(spec["year"])
    if year not in SR:
        return result(spec, question, status="unsupported",
                      title=f"{year} is not loaded",
                      explanation=f"311 layers loaded: {', '.join(sorted(SR))}.",
                      missingEvidence=[f"DC GIS 311 layer for {year}"])
    block = SR[year]
    ward = spec["geography"].get("code") if spec["geography"]["kind"] == "ward" else None
    label = WARD_LABEL.get(ward, "Ward ?") if ward else "the District"
    service = (spec.get("service") or "").strip() or None

    # "which ward complains most about rats" asks for a ranking. Returning a
    # District total answers a different question, so when the wording asks
    # which/most/highest and no ward was given, rank the wards we already hold
    # and answer with the top one, saying that is what happened.
    wants_rank = (not ward) and re.search(
        r"\bwhich ward\b|\bwhat ward\b|\bmost\b|\bhighest\b|\bworst\b|"
        r"\btop\b|\brank", question, re.I)
    ranked = None
    if wants_rank:
        svc = (spec.get("service") or "").strip()
        per = {}
        for wlabel, types in block["byWardService"].items():
            if not wlabel.startswith("Ward"):
                continue
            per[wlabel] = (sum(match_services(svc, types).values()) if svc
                           else block["byWard"].get(wlabel, 0))
        per = {k: v for k, v in per.items() if v}
        if per:
            ranked = sorted(per.items(), key=lambda kv: -kv[1])
            ward = ranked[0][0].split()[-1]
            spec = {**spec, "geography": {"kind": "ward", "code": ward}}

    if ward:
        pool = block["byWardService"].get(WARD_LABEL[ward], {})
        total = block["byWard"].get(WARD_LABEL[ward], 0)
    else:
        pool = {}
        for w in block["byWardService"].values():
            for k, v in w.items():
                pool[k] = pool.get(k, 0) + v
        total = block["total"]

    if service:
        hits = match_services(service, pool)
        if not hits:
            close = sorted(pool, key=lambda k: -pool[k])[:8]
            return result(
                spec, question, status="unsupported",
                title="No matching service type",
                explanation=f'No 311 service type in {year} matches "{service}".',
                missingEvidence=[f'A 311 service type matching "{service}"'],
                limitations=[f"Most frequent types in {label}: "
                             + "; ".join(close)])
        count = sum(hits.values())
        detail = (f'Service types matched: {", ".join(sorted(hits))}.'
                  if len(hits) > 1 else f"Service type: {list(hits)[0]}.")
    else:
        count = total
        detail = "All service types."

    return result(
        spec, question,
        title=f"{count:,} requests",
        explanation=(
            (f"{ranked[0][0]} recorded the most, {count:,}, of the eight "
             f"wards in {year}. {detail} Full ranking: "
             + ", ".join(f"{w.split()[-1]}: {c:,}" for w, c in ranked) + ". "
             if ranked else
             f"{count:,} 311 service requests were recorded in {label} in "
             f"{year}. {detail} ")
            + "These are recorded requests, not unique "
            "residents, and not completed work."
            + (f" No year was stated, so {year} was used, the most recent "
               f"loaded." if assumed_year else "")),
        estimate={"kind": "count", "value": count, "unit": "requests",
                  "denominator": total,
                  "populationShare": round(count / total, 4) if total else None},
        uncertainty=None,
        limitations=[
            "A count of recorded requests. One resident may file many; some "
            "problems are never reported at all.",
            *(["Ranked by recorded request volume, which reflects reporting "
               "behaviour as much as underlying conditions. A ward that reports "
               "less is not necessarily better off."] if ranked else []),
            "Not a measure of need, service quality, or completion time.",
            "Requests without a ward are excluded from ward totals.",
        ],
        validation={"description": "An administrative count, reported as "
                                   "published. No model is involved."})


def answer_health_prevalence(spec, question):
    tract = spec["geography"].get("code")
    measure = (spec.get("measure") or "").upper()
    year = str(spec["year"])
    key = f"{tract}|{measure}|{year}"
    row = PLACES["byTract"].get(key)
    if not row:
        have = sorted({k.split("|")[1] for k in PLACES["byTract"]
                       if k.startswith(f"{tract}|")})
        if not have:
            looks_like_a_place = not re.fullmatch(r"\d{11}", str(tract or ""))
            if looks_like_a_place:
                return result(
                    spec, question, status="unsupported",
                    title="That needs a tract number",
                    explanation=(
                        (f'"{tract}" is a place name, not a census tract. '
                         if tract else "No census tract was identified. ")
                        + f"Tract "
                        f"health estimates are published per tract, and this "
                        f"service does not hold a neighbourhood-to-tract lookup, "
                        f"so guessing which tracts you meant would be inventing "
                        f"the boundary. Give an 11-digit tract id, for example "
                        f"11001000101."),
                    missingEvidence=[
                        (f'a census tract id for "{tract}"' if tract
                         else "a census tract id"),
                        "a neighbourhood-to-tract crosswalk"])
            return result(spec, question, status="unsupported",
                          title="Tract not found",
                          explanation=f"No PLACES estimates for tract {tract}.",
                          missingEvidence=[f"CDC PLACES estimates for tract {tract}"])
        return result(
            spec, question, status="unsupported",
            title="Measure or year not published",
            explanation=f"PLACES has no {measure or 'that measure'} for tract "
                        f"{tract} in {year}.",
            missingEvidence=[f"PLACES {measure} for tract {tract}, {year}"],
            limitations=[f"Available for this tract: {', '.join(have[:14])}"])

    meta = PLACES["measures"].get(measure, {})
    return result(
        spec, question,
        title=f"{row['value']:.1f}%",
        explanation=(
            f"{meta.get('measure', measure)} in DC tract {tract} was "
            f"{row['value']:.1f}% in {year} ({row.get('type', 'prevalence')}). "
            f"This is a CDC model-based small-area estimate, not a direct "
            f"count of people in the tract."),
        estimate={"kind": "percentage", "value": row["value"], "unit": "%",
                  "denominator": row.get("pop18")},
        uncertainty=({"level": 0.95, "lower": row["low"], "upper": row["high"],
                      "method": "Publisher's 95% confidence interval, carried "
                                "through unchanged.",
                      "limitations": "Model-based estimate; the interval "
                                     "reflects the model, not a tract census."}
                     if row.get("low") is not None else None),
        limitations=[
            "PLACES estimates are modelled from national survey data with "
            "local covariates. They are not measurements taken in this tract.",
            "The target population is measure-specific and set by the publisher.",
            "Already modelled: not independent data for validating another model.",
        ])


# --- who a policy reaches --------------------------------------------------
# The headline of a policy answer is one number. The question people actually
# have is who it lands on, so the per-group impacts the engine already computes
# are rendered as a table rather than thrown away.

GROUP_LABEL = {
    "single_no_kids":   "One adult, no children",
    "couple_no_kids":   "Two adults, no children",
    "single_parent":    "One adult with children",
    "couple_with_kids": "Two adults with children",
    "other":            "Other household",
    "Q1": "Lowest fifth by income", "Q2": "Second fifth",
    "Q3": "Middle fifth", "Q4": "Fourth fifth",
    "Q5": "Highest fifth by income",
}
GROUP_ORDER = {n: i for i, n in enumerate(
    ["single_parent", "couple_with_kids", "single_no_kids", "couple_no_kids",
     "other", "Q1", "Q2", "Q3", "Q4", "Q5"])}
# Below this many ACS records a group interval is too thin to publish at all.
MIN_PUBLISH_N = 10
try:
    from model.params import LOW_SAMPLE_N
except Exception:      # noqa: BLE001
    LOW_SAMPLE_N = 100


def _money(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):,.0f}"


def _group_row(g: Dict[str, Any]) -> Dict[str, Any]:
    """One subgroup, as the front end's group shape.

    The share column carries the share of the group the transfer reaches, which
    is the only group quantity that is a share and so the only one the interval
    column can honestly hold. The dollar change rides in the label, because a
    dollar figure rendered through a percent formatter would be a wrong number.
    """
    name = g["group"]
    label = GROUP_LABEL.get(name, name)
    d = g["disposable_income_delta"]
    label = (f"{label} · {_money(d)}/yr "
             f"({_money(g['disposable_income_delta_p05'])} to "
             f"{_money(g['disposable_income_delta_p95'])})")
    if g["low_sample"]:
        label += " · thin sample"
    if g["sample_n"] < MIN_PUBLISH_N:
        return {"label": label, "validRecords": g["sample_n"], "suppressed": True,
                "share": None, "interval": None,
                "reason": f"Only {g['sample_n']} records; not published."}
    return {"label": label, "validRecords": g["sample_n"], "suppressed": False,
            "share": g["pct_better_off"],
            "interval": {"lower": g["pct_better_off_p05"],
                         "upper": g["pct_better_off_p95"]}}


def revenue_source_table(burden: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Where the money actually comes from.

    Share of income says who feels it; share of revenue says what the policy
    depends on. They are different questions and a tax answer needs both: a
    change can be mildly progressive per household and still rest almost
    entirely on one group, which is the exposure any behavioural response acts
    on first.
    """
    rows = []
    for b in burden:
        rows.append({
            "label": f"{GROUP_LABEL.get(b['group'], b['group'])} - gives up "
                     f"{100 * b['share_of_income']:.2f}% of its own income",
            "validRecords": b["sample_n"], "suppressed": False,
            "share": b["share_of_revenue"],
            "interval": {"lower": b["share_of_revenue_p05"],
                         "upper": b["share_of_revenue_p95"]}})
    return {"label": "Where the revenue comes from", "groups": rows,
            "columns": ["Income group and share of its own income taken",
                        "ACS records", "Share of the revenue", "90% interval"]}


def impact_breakdowns(sim: Dict[str, Any], understood: str,
                      share_label: str = "Share reached",
                      reached: str = "Households reached",
                      unaffected: str = "Households unaffected",
                      summary_label: str = "Who the policy reaches, by group",
                      share_note: str = "The share reached is the share of the "
                                        "group the transfer pays anything to. It "
                                        "is not a poverty change and not a "
                                        "welfare claim.",
                      extra_tables: Optional[List[Dict[str, Any]]] = None
                      ) -> Dict[str, Any]:
    by_group = sim.get("by_group", [])
    tables, reached, total = [], 0.0, 0.0
    for gtype, title in (("household_type", "By household type"),
                         ("income_quintile", "By income group")):
        rows = sorted((g for g in by_group if g["group_type"] == gtype),
                      key=lambda g: GROUP_ORDER.get(g["group"], 99))
        if not rows:
            continue
        tables.append({"label": title, "groups": [_group_row(g) for g in rows],
                       "columns": ["Group and change in disposable income",
                                   "ACS records", share_label,
                                   "90% interval"]})
        if gtype == "household_type":          # a partition, so it can be summed
            for g in rows:
                total += g["households_weighted"]
                reached += g["households_weighted"] * g["pct_better_off"]
    tables += list(extra_tables or [])
    n = sim["n_households"]
    return {
        "question": {"wording": understood,
                     "responseCodes": {"reached": reached,
                                       "unaffected": unaffected}},
        "overall": {"sampleRecords": n, "validRecords": n, "missingRecords": 0},
        "sampleText": (f"{n:,} DC household records, weighted to "
                       f"{round(total):,} households. Groups are ACS record "
                       f"counts; shares and intervals are weighted."),
        "summaryLabel": summary_label,
        "responseCounts": {"reached": round(reached),
                           "unaffected": round(total - reached)},
        "breakdowns": tables,
        "publicationRule": {"description":
            f"A group with fewer than {MIN_PUBLISH_N} ACS records is not "
            f"published. A group under {LOW_SAMPLE_N} is shown and marked a "
            f"thin sample: the interval is real but wide."},
        "limitations": [
            share_note,
            "Dollar changes are the median across parameter draws, per "
            "household per year, in constant 2024 dollars.",
            "Groups are defined on the household as the ACS records it, so a "
            "household appears in exactly one row of each table.",
        ],
    }


def answer_policy_simulation(spec, question):
    """Our microsimulation, run on DC households only."""
    try:
        from app.local_parser import parse
        from dc_api.dc_engine import run_dc
    except Exception as exc:                          # noqa: BLE001
        return result(spec, question, status="unsupported",
                      title="Simulation unavailable",
                      explanation=str(exc),
                      missingEvidence=["the local microsimulation"])
    parsed = parse(question)
    if not parsed.ok:
        return result(spec, question, status="unsupported",
                      title="Could not read that policy",
                      explanation=" ".join(parsed.notes),
                      missingEvidence=["a readable policy description"])
    sim = run_dc(parsed.levers)
    cpr = sim["impact"]["child_poverty_rate"]
    base, med = cpr["baseline"], cpr["median"]
    # The CHANGE is a paired difference: the baseline is recomputed under each
    # bootstrap draw, so the interval measures the policy effect instead of
    # sampling noise in the level. See _band in model/engine.py.
    chg, chg_lo, chg_hi = (cpr["change_median"], cpr["change_p05"],
                           cpr["change_p95"])
    cross = sim["impact"]["poverty_crossings"]
    spec = {**spec, "outcome": "Change in DC child poverty rate"}
    return result(
        spec, question,
        title=f"{100 * chg:+.1f} points",
        explanation=(
            "Read as: " + " · ".join(parsed.understood) + ". Simulated on "
            f"{sim['n_households']:,} DC household records from the ACS 2024 "
            f"PUMS, {sim['n_seeds']} parameter draws. Child poverty in DC goes "
            f"from {100 * base:.1f}% to {100 * med:.1f}%, and the transfer "
            f"costs ${sim['impact']['annual_cost_usd']['median'] / 1e6:,.0f}M a "
            f"year in DC. "
            + (f"It lifts {cross['leaving_people']:,.0f} people above the "
               f"federal poverty line, {cross['leaving_children']:,.0f} of them "
               f"children. " if cross["leaving_people"] >= 1 else "")
            + ("A rate is an average; this is the count of people who actually "
               "cross the line, which is the number the rate is made of.")),
        estimate={"kind": "percentage_point_change",
                  "value": round(100 * chg, 2), "unit": "points",
                  # Spelled out, because the front end's default formatter
                  # rounds a non-percent unit to a whole number and -1.33
                  # points would show as "-1". One decimal, not two: the
                  # interval is several points wide.
                  "displayValue": f"{100 * chg:+.1f} points",
                  "label": "change in the DC child poverty rate"},
        uncertainty={"level": 0.9, "lower": round(100 * chg_lo, 2),
                     "upper": round(100 * chg_hi, 2),
                     "displayRange": f"{100 * chg_lo:+.1f} to "
                                     f"{100 * chg_hi:+.1f} points",
                     "method": f"{sim['n_seeds']} Latin hypercube draws over "
                               f"take-up, labour supply and MPC, with a "
                               f"Bayesian bootstrap over households.",
                     "limitations": "Parameter and sampling uncertainty only. "
                                    "It does not cover the model being wrong."},
        surveyAnalysis=impact_breakdowns(sim, " · ".join(parsed.understood)),
        limitations=sim.get("warnings", [])[:6] + [
            "A simulation, not an observation. The 2021 CTC backtest missed by "
            "0.7 points and is reported as a miss in docs/backtest.md."],
        validation={"description": "Pre-registered backtest against the 2021 "
                                   "expanded CTC; the miss is reported rather "
                                   "than tuned away. See docs/backtest.md."})


# --- the answer of last resort ---------------------------------------------
def coverage_sentence() -> str:
    """What periods the loaded evidence actually spans, stated on every
    reasoned answer so a question about 1999 is never quietly answered with
    2024 numbers."""
    yrs = sorted(SR)
    return (f"ACS {INCOME['year']} 1-Year PUMS for households and people, "
            f"311 service requests {yrs[0]}-{yrs[-1]}, and CDC PLACES "
            f"{', '.join(sorted(PLACES['years']))}")


def fact_source_kinds(source: str) -> List[str]:
    """Map a fact's source string onto the manifest entries it came from, so a
    reasoned answer cites the same datasets a counted one would."""
    out = []
    if F.ACS in source or F.SIM in source:
        out.append("household_income")
    if F.SR_SRC in source:
        out.append("service_requests")
    if F.PLACES_SRC in source:
        out.append("health_prevalence")
    return out


# --- a change to the tax schedule ------------------------------------------
# The published DC rate schedule, cited where it came from. A rate recalled by
# a language model is exactly the number this project will not print, so the
# schedule was retrieved from the body that publishes it and checked for
# internal consistency at import. See model/dc_tax.py.
OTR_EVIDENCE = {
    "publisher": "DC Office of Tax and Revenue",
    "dataset": "dc-individual-income-tax-rate-schedule-2024",
    "url": "https://otr.cfo.dc.gov/page/dc-individual-and-fiduciary-income-tax-rates",
    "referencePeriod": "tax years beginning after 2021-12-31",
}


# The survey evidence base holds cash transfers only -- the 2021 expanded CTC,
# the 2021 stimulus payments, UBI and the ARP package. Nothing in it was ever
# asked about a tax increase, so no support estimate is offered for one. This
# is the in_support: false state, stated in words rather than extrapolated from
# polling about a different policy.
OPINION_COVERAGE = (
    "No public-support estimate is offered. The opinion evidence holds polling "
    "on cash transfers -- the 2021 expanded Child Tax Credit, the 2021 stimulus "
    "payments, universal basic income and the American Rescue Plan -- and none "
    "of it asked about a tax increase, in DC or anywhere. Poststratifying "
    "transfer polling onto a tax question would be a support number for a "
    "policy nobody was surveyed on.")


def tax_facts(sim: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The measured tax figures, in the fact-pack shape, so the side-effects
    paragraph is written against numbers rather than around them."""
    rev = sim["annual_revenue_usd"]
    pushed = sim["pushed_into_poverty"]
    top = max(sim["burden_by_income"], key=lambda b: b["share_of_revenue"])
    out = [
        F.fact("tax_revenue", "Annual DC revenue from the change",
               rev["median"], f"${abs(rev['median']) / 1e9:,.2f} billion a year",
               F.SIM),
        F.fact("tax_base", "Current DC individual income tax base",
               sim["baseline_liability_usd"],
               f"${sim['baseline_liability_usd'] / 1e9:,.2f} billion a year", F.SIM),
        F.fact("tax_paying_more", "DC households whose liability rises",
               sim["households_paying_more"],
               f"{sim['households_paying_more']:,.0f} of "
               f"{sim['households_total']:,.0f} households", F.SIM),
        F.fact("tax_pushed_poverty",
               "People moved below the federal poverty line by the change",
               pushed["people"], f"{pushed['people']:,.0f} people "
               f"({pushed['records']} ACS records)", F.SIM),
        F.fact("tax_revenue_concentration",
               f"Share of the revenue coming from the {top['group']} income group",
               top["share_of_revenue"],
               f"{100 * top['share_of_revenue']:.1f}% of the total", F.SIM),
        # Figures the answer itself prints. Without them here the model gets
        # flagged for repeating a number the reader can see two lines above,
        # and the whole section is thrown away over a false positive.
        F.fact("tax_share_paying_more",
               "Share of DC households whose liability rises",
               sim["households_paying_more"] / sim["households_total"],
               f"{100 * sim['households_paying_more'] / sim['households_total']:.0f}%"
               f" of households", F.SIM),
        F.fact("tax_child_poverty_change",
               "Change in the DC child poverty rate",
               sim["impact"]["child_poverty_rate"]["change_median"],
               f"{100 * sim['impact']['child_poverty_rate']['change_median']:+.2f}"
               f" percentage points", F.SIM),
    ]
    for b in sim["burden_by_income"]:
        out.append(F.fact(
            f"tax_burden_{b['group']}",
            f"Share of its own income taken from the {b['group']} income group",
            b["share_of_income"], f"{100 * b['share_of_income']:.2f}% of income",
            F.SIM))
    return out


def tax_side_effects(sim: Dict[str, Any], understood: str) -> Optional[str]:
    """What might follow, argued from the measured figures only.

    The reasoning layer is bound by the same rule as everywhere else: it may
    state the numbers in the fact pack and no others. So it can say the revenue
    is concentrated in the top fifth and reason about what that exposes, and it
    cannot produce a migration elasticity, an approval rating, or a job-loss
    figure -- because we hold none of those.
    """
    if not R.available():
        return None
    facts = tax_facts(sim) + F.baseline()
    question = (
        f"Policy: {understood}. The revenue and distributional figures are "
        f"measured and given. What are the likely side effects and political "
        f"risks in Washington DC specifically -- who would object, what "
        f"behavioural response is plausible given the District's size and its "
        f"borders with Maryland and Virginia, and what could go wrong that the "
        f"revenue figure does not capture? Do not estimate the size of any "
        f"response; we hold no elasticity and no polling on this.")
    got, _notes = R.reason(question, {"kind": "tax_policy"}, facts, timeout=45.0)
    if not got:
        return None
    text = " ".join(x for x in (got.get("answer"), got.get("mechanism"),
                                got.get("affected")) if x).strip()
    return text or None


def tax_side_effects_measured(sim: Dict[str, Any]) -> str:
    """The side-effects section without the reasoning model.

    It runs whenever the model is absent or would not stay inside the evidence,
    so the section never silently disappears -- a missing paragraph reads as
    the system having nothing to say, when in fact the exposure is measured.
    """
    top = max(sim["burden_by_income"], key=lambda b: b["share_of_revenue"])
    return (
        f"The measured exposure: {100 * top['share_of_revenue']:.0f}% of the "
        f"revenue comes from the {GROUP_LABEL.get(top['group'], top['group'])} "
        f"alone, so the yield depends on that group staying and continuing to "
        f"report income in the District. How much of it would respond by "
        f"moving, working less or shifting income is not modelled and is not "
        f"estimated here: no elasticity for DC is held, and DC is an unusual "
        f"case for one, being small enough that a move across the Maryland or "
        f"Virginia line need not change anybody's commute. Nor is any approval "
        f"figure offered, because the opinion evidence is polling about cash "
        f"transfers and nobody in it was asked about a tax.")


def answer_tax_policy(spec, question):
    """Simulate a change to the DC income tax schedule."""
    points = spec.get("taxChangePoints")
    proportional = spec.get("taxChangeProportional")
    if points is None and proportional is None:
        return result(spec, question, status="unsupported",
                      title="No rate change given",
                      explanation="No size of tax change was stated.",
                      missingEvidence=["a rate change, in percentage points"])
    try:
        from dc_api.tax_sim import run_tax
        sim = run_tax(points=points, proportional=proportional)
    except Exception as exc:                                    # noqa: BLE001
        return result(spec, question, status="unsupported",
                      title="Tax simulation unavailable",
                      explanation=str(exc),
                      missingEvidence=["the DC tax microsimulation"])

    rev = sim["annual_revenue_usd"]
    cpr = sim["impact"]["child_poverty_rate"]
    raising = rev["median"] >= 0
    verb = "raises" if raising else "returns"
    verb_past = "raised" if raising else "returned"
    share_paying = sim["households_paying_more"] / sim["households_total"]
    pushed = sim["pushed_into_poverty"]
    burden = sim["burden_by_income"]
    top = max(burden, key=lambda b: b["share_of_revenue"])
    lo, hi = burden[0], burden[-1]
    side = tax_side_effects(sim, sim["understood"]) or \
        tax_side_effects_measured(sim)

    return result(
        spec, question,
        title=f"${abs(rev['median']) / 1e9:,.2f}B a year",
        explanation=(
            f"Read as: {sim['understood']}. Applied to {sim['n_households']:,} "
            f"DC household records from the ACS 2024 PUMS, using the published "
            f"DC schedule and the standard deduction. It {verb} "
            f"${abs(rev['median']) / 1e9:,.2f} billion a year against a current "
            f"DC individual income tax base of "
            f"${sim['baseline_liability_usd'] / 1e9:,.2f} billion. "
            f"{sim['households_paying_more']:,.0f} households "
            f"({100 * share_paying:.0f}%) owe more; the rest owe nothing extra "
            f"because their taxable income is below the standard deduction. "
            f"WHO CARRIES IT: the {lo['group']} income group gives up "
            f"{100 * lo['share_of_income']:.2f}% of its income and the "
            f"{hi['group']} group {100 * hi['share_of_income']:.2f}%, so the "
            f"change is progressive in share-of-income terms, and "
            f"{100 * top['share_of_revenue']:.0f}% of the money comes from the "
            f"{top['group']} group alone. Child poverty moves "
            f"{100 * cpr['change_median']:+.2f} points and "
            f"{pushed['people']:,.0f} people cross the federal poverty line, "
            f"because households under it have little or no taxable income to "
            f"begin with. "
            + (f"SIDE EFFECTS: {side}" if side else "")),
        estimate={"kind": "annual_revenue", "value": round(rev["median"], 0),
                  "unit": "dollars",
                  "displayValue": f"${abs(rev['median']) / 1e9:,.2f}B a year",
                  "label": f"DC revenue {verb_past}, statutory"},
        uncertainty={"level": 0.9, "lower": round(rev["p05"], 0),
                     "upper": round(rev["p95"], 0),
                     "displayRange": f"${rev['p05'] / 1e9:,.2f}B to "
                                     f"${rev['p95'] / 1e9:,.2f}B",
                     "method": f"Bayesian bootstrap over {sim['n_households']:,} "
                               f"DC household records, {sim['n_seeds']} draws. "
                               f"The schedule itself is exact, so this interval "
                               f"is sampling uncertainty only.",
                     "limitations": "It does not cover behavioural response, "
                                    "credits, or itemised deductions."},
        surveyAnalysis=impact_breakdowns(
            sim, sim["understood"],
            share_label="Share paying more",
            reached="Households owing more", unaffected="Households owing no more",
            summary_label="Who pays it, by group",
            share_note="The share paying more is the share of the group whose "
                       "DC liability rises at all. The dollar figure is the "
                       "average change in disposable income across the whole "
                       "group, including those who owe nothing extra.",
            extra_tables=[revenue_source_table(burden)]),
        evidence=[OTR_EVIDENCE] + evidence_for("household_income"),
        missingEvidence=[
            "Polling on a DC income tax increase. The opinion evidence covers "
            "cash transfers only, so no approval or disapproval figure is "
            "produced for this.",
            "A migration or labour-supply elasticity for DC. Without one, the "
            "risk that revenue concentrated in the top fifth walks across the "
            "Maryland or Virginia line can be described but not sized.",
            "A revenue estimate from the DC Chief Financial Officer, which "
            "would incorporate behavioural response and administrative data",
            "Itemised deduction and credit take-up, to net down liability",
        ],
        limitations=list(sim["warnings"]) + [
            OPINION_COVERAGE,
            "The DC schedule is the published one, retrieved from the Office "
            "of Tax and Revenue and checked at import: every bracket's base "
            "amount must equal the tax accumulated below it.",
        ] + ([
            "The side-effects paragraph is reasoning, not measurement. It is "
            "written against the figures above and may not state any number "
            "that is not among them; it therefore names no elasticity, "
            "approval rating or job-loss figure, because we hold none."]),
        validation={"description":
                    "The rate schedule reproduces every published bracket "
                    "boundary exactly ($400 at $10,000 through $91,525 at "
                    "$1,000,000). The distributional machinery is the same "
                    "code the transfer simulation uses."})


def answer_reasoned(spec, question, carried=None):
    """Answer a question no query can close, without ever refusing it.

    The fact pack is assembled first, so the reasoning model argues from
    measured quantities rather than from nothing. Every numeral it writes is
    checked back against that pack; see dc_api/reason.py. If it will not stay
    inside the evidence, its text is dropped and the answer is built from the
    facts alone -- which still answers, just more plainly.
    """
    pack = F.pack(question, spec)
    notes = []
    got = None
    if R.available():
        got, notes = R.reason(question, spec, pack)
    else:
        notes.append("No reasoning model is configured, so this answer is "
                     "assembled from the measured evidence alone. Set "
                     "ANTHROPIC_API_KEY to enable the reasoning layer.")
    reasoned_by_model = got is not None
    if got is None:
        got = R.fallback(question, pack)

    by_id = {f["id"]: f for f in pack}
    lead = next((by_id[i] for i in got.get("usedFactIds", []) if i in by_id),
                pack[0] if pack else None)

    body = " ".join(x for x in (got.get("answer"), got.get("mechanism"),
                                got.get("affected")) if x).strip()
    used = [by_id[i] for i in got.get("usedFactIds", []) if i in by_id] or pack[:4]
    seen, ev = set(), []
    for f in used:
        for kind in fact_source_kinds(f["source"]):
            if kind in seen:
                continue
            seen.add(kind)
            ev += evidence_for(kind)

    limits = []
    if got.get("measured"):
        limits.append("Measured, not inferred: " + "; ".join(got["measured"]) + ".")
    if got.get("reasoned"):
        limits.append("Reasoning rather than measurement: "
                      + "; ".join(got["reasoned"]) + ".")
    limits += notes
    limits.append("The loaded evidence covers " + coverage_sentence() + ". A "
                  "question about another period or place is answered from "
                  "these, and that substitution is not silent.")
    limits.append("Every figure above is counted or computed from the loaded "
                  "evidence. The reasoning model may not state a number that is "
                  "not in it, and any answer that does is regenerated or "
                  "discarded.")
    if carried:
        limits += carried

    est = None
    if lead is not None:
        est = {"kind": "supporting_fact", "value": lead["value"]
               if isinstance(lead["value"], (int, float)) else 0,
               "unit": "count", "displayValue": lead["display"],
               "label": lead["label"]}

    return result(
        spec, question,
        status="ok",
        title=got.get("headline") or "What the evidence shows",
        explanation=body or got.get("answer", ""),
        estimate=est,
        evidence=ev,
        missingEvidence=got.get("wouldChange", []),
        limitations=limits,
        validation={"description":
                    "Reasoning grounded in the loaded evidence. The measured "
                    "quantities are counts and model output; the argument "
                    "connecting them is generated and labelled as such."
                    if reasoned_by_model else
                    "Assembled from the measured evidence without a reasoning "
                    "model."})


HANDLERS = {
    "household_income": answer_household_income,
    "service_requests": answer_service_requests,
    "health_prevalence": answer_health_prevalence,
    "policy_simulation": answer_policy_simulation,
    "tax_policy": answer_tax_policy,
}


def run_query(question: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Answer every question.

    A structured handler runs first, because a counted answer beats a reasoned
    one whenever the count exists. If no handler applies, or the handler cannot
    close the question -- an unknown service type, a year we do not hold, a
    neighbourhood name where a tract id is needed -- the reasoning layer takes
    it instead, carrying forward what the handler established was missing. The
    service does not return "more evidence is needed" to a user.
    """
    handler = HANDLERS.get(spec.get("kind"))
    if handler is None:
        return answer_reasoned(spec, question)
    out = handler(spec, question)
    if out.get("status") == "ok":
        return out
    carried = []
    if out.get("explanation"):
        carried.append("A direct lookup was tried first and could not close it: "
                       + out["explanation"])
    carried += out.get("missingEvidence", []) + out.get("limitations", [])
    return answer_reasoned(spec, question, carried=carried)


def catalog() -> Dict[str, Any]:
    years_sr = sorted(int(y) for y in SR)
    return {
        "apiVersion": "v1", "engineVersion": ENGINE_VERSION,
        "freeformAvailable": interpret_claude.available(), "examples": EXAMPLES,
        "capabilities": [
            {"kind": "household_income",
             "outcome": "Household count and population share under an explicit "
                        "annual income threshold",
             "years": [INCOME["year"]], "geographies": ["district", "puma"],
             "limitations": "Occupied households; constant 2024 dollars. No "
                            "other eligibility, participation or behavioural "
                            "response."},
            {"kind": "service_requests",
             "outcome": "Recorded service request count",
             "years": years_sr, "geographies": ["district", "ward"],
             "limitations": "Requests, not people. No completion-time measure."},
            {"kind": "health_prevalence",
             "outcome": "CDC published tract prevalence and publisher interval",
             "years": [int(y) for y in PLACES["years"]], "geographies": ["tract"],
             "limitations": "Model-based small-area estimates. Already modelled; "
                            "not independent validation data."},
            {"kind": "tax_policy",
             "outcome": "Revenue and household impact of a change to the DC "
                        "income tax rate schedule",
             "years": [2024], "geographies": ["district"],
             "limitations": "Statutory calculation. No behavioural response, "
                            "no credits, no itemised deductions."},
            {"kind": "policy_simulation",
             "outcome": "Simulated effect of a cash transfer on DC households",
             "years": [2024], "geographies": ["district"],
             "limitations": "A simulation. Pre-registered backtest reported, "
                            "including its miss."},
        ],
        "sources": MANIFEST["sources"],
        "importAttempts": [],
        "healthMeasures": [{"measure": k, "definition": v.get("measure"),
                            "reference_period": ", ".join(PLACES["years"])}
                           for k, v in sorted(PLACES["measures"].items())],
        "serviceTypes": sorted({s for y in SR.values()
                                for w in y["byWardService"].values() for s in w})[:400],
        "validation": {"status": "not_trained",
                       "description": "Counts are counts. The one model here is "
                                      "the cash-transfer microsimulation, whose "
                                      "backtest is pre-registered and whose miss "
                                      "is reported."},
    }


# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "policy-sim-dc"
    dist: Path = Path()

    def log_message(self, fmt, *args):        # quieter console
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("  %s\n" % (fmt % args))

    def _json(self, code: int, body: Any):
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):                          # noqa: N802
        path = self.path.split("?")[0]
        if path == "/api/v1/health":
            return self._json(200, {"status": "ready", "readySources": len(MANIFEST["sources"])})
        if path == "/api/v1/catalog":
            return self._json(200, catalog())
        m = re.match(r"^/api/v1/runs/([0-9a-fA-F-]{36})$", path)
        if m:
            run = RUNS.get(m.group(1))
            return self._json(200 if run else 404, run or {"error": "Run not found"})
        if path.startswith("/api/"):
            return self._json(404, {"error": "Not found"})
        return self._static(path)

    def do_HEAD(self):                         # noqa: N802
        self.do_GET()

    def do_POST(self):                         # noqa: N802
        if self.path.split("?")[0] != "/api/v1/query":
            return self._json(404, {"error": "Not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 32768:
                return self._json(413, {"error": "Request too large"})
            payload = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:                      # noqa: BLE001
            return self._json(400, {"error": "Invalid JSON"})
        question = (payload.get("question") or "").strip()
        if not question:
            return self._json(400, {"error": "A question is required"})
        spec = payload.get("specification") or interpret(question)
        try:
            return self._json(200, run_query(question, spec))
        except Exception as exc:               # noqa: BLE001
            sys.stderr.write(f"  query failed: {exc}\n")
            return self._json(200, result(
                spec, question, status="unsupported",
                title="That request could not be completed",
                explanation=str(exc),
                missingEvidence=["a completed evidence lookup"]))

    def _static(self, path: str):
        root = self.dist.resolve()
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (root / rel).resolve()
        if not str(target).startswith(str(root)) or not target.is_file():
            target = root / "index.html"
            if not target.is_file():
                return self._json(404, {"error": "Atlas is not built. Run "
                                                 "dc_api/setup_frontend.sh."})
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        # The atlas ships pre-compressed .gz assets and fetches them directly.
        if target.suffix == ".gz":
            inner = mimetypes.guess_type(target.stem)[0] or "application/octet-stream"
            self.send_header("Content-Type", inner)
            self.send_header("Content-Encoding", "gzip")
        else:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 4318)))
    ap.add_argument("--dist", default=os.environ.get(
        "JUNIPER_DIST", str(REPO / "frontend" / "city" / "dist")))
    a = ap.parse_args()

    Handler.dist = Path(a.dist)
    built = (Handler.dist / "index.html").is_file()
    print(f"policy-sim DC evidence service")
    print(f"  atlas   : {Handler.dist}" + ("" if built else "   [NOT BUILT]"))
    print(f"  evidence: 311 {sorted(SR)} · PLACES {PLACES['years']} "
          f"({len(PLACES['measures'])} measures) · ACS {INCOME['year']} "
          f"({INCOME['records']:,} DC households)")
    print(f"  http://127.0.0.1:{a.port}\n")
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
