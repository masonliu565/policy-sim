"""
Evidence service for the Juniper DC atlas front end.

The front end is used ENTIRELY UNCHANGED. It expects a local service on
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

    health_affordability is NOT answered. BRFSS MEDCOST1 is not published for
    DC 2024 in any public aggregate that was reachable, so that request returns
    missingEvidence naming what is absent and the nearest measure that exists,
    rather than substituting something else. The front end renders that state.
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


def result(spec: Dict[str, Any], question: str, **kw) -> Dict[str, Any]:
    """A PolicyResult with every field the front end reads."""
    rid = str(uuid.uuid4())
    out = {
        "runId": rid, "originalQuestion": question, "specification": spec,
        "status": kw.get("status", "ok"), "title": kw.get("title", ""),
        "explanation": kw.get("explanation", ""),
        "population": spec.get("population", ""),
        "geography": spec.get("geography", {"kind": "district", "code": "11"}),
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
            f"(ADJINC {INCOME['adjinc']:.6f})."),
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
        hits = {k: v for k, v in pool.items() if service.lower() in k.lower()}
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
            f"{count:,} 311 service requests were recorded in {label} in "
            f"{year}. {detail} These are recorded requests, not unique "
            f"residents, and not completed work."),
        estimate={"kind": "count", "value": count, "unit": "requests",
                  "denominator": total,
                  "populationShare": round(count / total, 4) if total else None},
        uncertainty=None,
        limitations=[
            "A count of recorded requests. One resident may file many; some "
            "problems are never reported at all.",
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
    spec = {**spec, "outcome": "Change in DC child poverty rate"}
    return result(
        spec, question,
        title=f"{100 * (med - base):+.1f} points",
        explanation=(
            "Read as: " + " · ".join(parsed.understood) + ". Simulated on "
            f"{sim['n_households']:,} DC household records from the ACS 2024 "
            f"PUMS, {sim['n_seeds']} parameter draws. Child poverty in DC goes "
            f"from {100 * base:.1f}% to {100 * med:.1f}%, and the transfer "
            f"costs ${sim['impact']['annual_cost_usd']['median'] / 1e6:,.0f}M a "
            f"year in DC."),
        estimate={"kind": "percentage_point_change",
                  "value": round(100 * (med - base), 2), "unit": "points"},
        uncertainty={"level": 0.9, "lower": round(100 * (cpr["p05"] - base), 2),
                     "upper": round(100 * (cpr["p95"] - base), 2),
                     "method": f"{sim['n_seeds']} Latin hypercube draws over "
                               f"take-up, labour supply and MPC, with a "
                               f"Bayesian bootstrap over households.",
                     "limitations": "Parameter and sampling uncertainty only. "
                                    "It does not cover the model being wrong."},
        limitations=sim.get("warnings", [])[:6] + [
            "A simulation, not an observation. The 2021 CTC backtest missed by "
            "0.7 points and is reported as a miss in docs/backtest.md."],
        validation={"description": "Pre-registered backtest against the 2021 "
                                   "expanded CTC; the miss is reported rather "
                                   "than tuned away. See docs/backtest.md."})


def answer_unsupported(spec, question):
    kind = spec.get("kind")
    if kind == "health_affordability":
        return result(
            spec, question, status="unsupported",
            title="Not in the loaded evidence",
            explanation=(
                "BRFSS MEDCOST1 — whether an adult could not see a doctor "
                "because of cost — is not published for DC 2024 in any public "
                "aggregate this service could reach. Rather than substitute a "
                "different measure, it is left unanswered."),
            missingEvidence=[
                "BRFSS 2024 MEDCOST1 responses for the District of Columbia",
                "A survey-design interval and age, income and insurance "
                "breakdowns for that measure",
            ],
            limitations=[
                "The nearest loaded measure is CDC PLACES ACCESS2, the share of "
                "adults aged 18-64 without health insurance, by census tract. "
                "That is a different question and is not offered as a stand-in.",
            ])
    return result(
        spec, question, status="unsupported",
        title="Not interpreted",
        explanation=spec.get("clarification") or
        "This question was not recognised, and nothing in it has been ignored.",
        missingEvidence=spec.get("unsupportedConstraints", []),
        limitations=["Supported questions are listed in the catalog and in the "
                     "examples above the chat box."])


HANDLERS = {
    "household_income": answer_household_income,
    "service_requests": answer_service_requests,
    "health_prevalence": answer_health_prevalence,
    "policy_simulation": answer_policy_simulation,
}


def run_query(question: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    return HANDLERS.get(spec.get("kind"), answer_unsupported)(spec, question)


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
