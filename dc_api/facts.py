"""
The facts a question is allowed to be answered with.

This is the half of the "never say more information is needed" promise that
does the real work. An agent asked to reason about a policy with nothing in
front of it will produce fluent, plausible, invented numbers. An agent handed
the actual measured quantities that bear on the question will reason about
those instead, and can be held to them.

So before any question reaches the model, this assembles a fact pack: DC
baselines that are always relevant, plus whatever the question specifically
touches -- a wage threshold, an income cutoff, a service type, a health
measure. Every entry carries an id, the number, a rendered form and its source.
Nothing here is estimated by a language model; it is all counted or computed
from the ACS microdata, the city's own service records, CDC's published
estimates, or our microsimulation.

dc_api/reason.py then permits the model to state these numbers and no others.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

ACS = "ACS 2024 1-Year PUMS, DC records"
SR_SRC = "DC GIS 311 service requests"
PLACES_SRC = "CDC PLACES, census tract estimates"
SIM = "policy-sim microsimulation on DC households"


def fact(fid: str, label: str, value: Any, display: str, source: str) -> Dict[str, Any]:
    return {"id": fid, "label": label, "value": value, "display": display,
            "source": source}


def _money(x: float) -> str:
    return f"${x:,.0f}"


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


# --- DC baselines, always supplied -----------------------------------------
_BASE: Optional[List[Dict[str, Any]]] = None


def baseline() -> List[Dict[str, Any]]:
    global _BASE
    if _BASE is not None:
        return _BASE
    out: List[Dict[str, Any]] = []
    try:
        import numpy as np
        from dc_api import dc_engine
        from dc_api import server as S

        inc = np.asarray(S.INCOME["incomes"], dtype=float)
        w = np.asarray(S.INCOME["weights"], dtype=float)
        order = np.argsort(inc)
        cum = np.cumsum(w[order]) / w.sum()
        median = float(inc[order][int(np.searchsorted(cum, 0.5))])
        out += [
            fact("dc_households", "DC occupied households", float(w.sum()),
                 f"{w.sum():,.0f} households", ACS),
            fact("dc_household_records", "ACS household records for DC",
                 len(inc), f"{len(inc):,} records", ACS),
            fact("dc_median_household_income", "Median DC household income",
                 median, _money(median), ACS),
        ]

        pop = dc_engine.population()
        poor = pop.inc < pop.threshold
        child = float((poor * pop.cw).sum() / pop.cw.sum()) if pop.cw.sum() else 0.0
        allp = float((poor * pop.pw).sum() / pop.pw.sum()) if pop.pw.sum() else 0.0
        out += [
            fact("dc_child_poverty_rate",
                 "DC child poverty rate, federal thresholds, before any policy",
                 child, _pct(child), SIM + " / " + ACS),
            fact("dc_poverty_rate",
                 "DC poverty rate, all people, before any policy",
                 allp, _pct(allp), SIM + " / " + ACS),
            fact("dc_people", "People in DC households", float(pop.pw.sum()),
                 f"{pop.pw.sum():,.0f} people", ACS),
            fact("dc_children", "Children in DC households", float(pop.cw.sum()),
                 f"{pop.cw.sum():,.0f} children", ACS),
        ]
        for (gtype, gname), mask in pop.masks.items():
            if gtype != "household_type":
                continue
            hh = float(pop.dw[mask].sum())
            out.append(fact(f"dc_hh_{gname}", f"DC households, {gname}", hh,
                            f"{hh:,.0f} households", ACS))
    except Exception:                                           # noqa: BLE001
        pass

    try:
        from dc_api import dc_persons
        s = dc_persons.summary()
        if s:
            out += [
                fact("dc_workers", "DC wage earners with usable hours and weeks",
                     s["weighted_workers"], f"{s['weighted_workers']:,.0f} workers", ACS),
                fact("dc_median_hourly_wage", "Median implied hourly wage, DC workers",
                     s["median_hourly_wage"], f"${s['median_hourly_wage']:,.2f} an hour", ACS),
                fact("dc_wage_p10", "10th percentile implied hourly wage, DC workers",
                     s["percentiles"][.1], f"${s['percentiles'][.1]:,.2f} an hour", ACS),
                fact("dc_wage_p25", "25th percentile implied hourly wage, DC workers",
                     s["percentiles"][.25], f"${s['percentiles'][.25]:,.2f} an hour", ACS),
            ]
    except Exception:                                           # noqa: BLE001
        pass

    _BASE = out
    return out


# --- what this particular question touches ----------------------------------
MONEY = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(k|thousand)?", re.I)
HOURLY = re.compile(r"\$?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:an?\s+|per\s+|/)\s*(?:hour|hr)", re.I)


def _numbers(text: str) -> List[float]:
    vals = []
    for m in MONEY.finditer(text):
        v = float(m.group(1).replace(",", ""))
        if m.group(2):
            v *= 1_000
        vals.append(v)
    return vals


# Generic question words. match_services already drops request/complaint/report
# style filler; these are the words that make a question a question.
COMMON = {
    "what", "when", "where", "which", "whom", "whose", "does", "will", "would",
    "should", "could", "have", "with", "from", "that", "this", "they", "them",
    "than", "then", "there", "here", "much", "many", "most", "more", "less",
    "least", "about", "into", "over", "under", "just", "like", "want", "need",
    "make", "made", "give", "gives", "given", "take", "help", "good", "well",
    "better", "best", "worse", "worst", "high", "higher", "highest", "low",
    "lower", "lowest", "people", "person", "city", "district", "washington",
    "year", "years", "last", "next", "policy", "policies", "change", "changes",
    "happen", "happens", "affect", "affects", "impact", "impacts", "instead",
    "also", "even", "very", "really", "actually", "think", "know", "tell",
    "show", "look", "find", "many", "some", "each", "every", "been", "being",
    "were", "long", "term", "same", "different", "across", "among", "between",
}


def for_question(question: str, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Facts specific to this question, on top of the baselines."""
    out: List[Dict[str, Any]] = []
    q = question or ""

    # A stated hourly figure is a wage question, whatever else it is.
    for m in HOURLY.finditer(q):
        try:
            from dc_api import dc_persons
            got = dc_persons.workers_under(float(m.group(1)))
        except Exception:                                       # noqa: BLE001
            got = None
        if got:
            out.append(fact(
                f"workers_under_{m.group(1)}",
                f"DC wage earners with an implied hourly wage below "
                f"${float(m.group(1)):,.2f}",
                got["workers"],
                f"{got['workers']:,.0f} workers ({_pct(got['share'])} of "
                f"{got['denominator']:,.0f}), from {got['records']:,} records",
                ACS))
            break

    # A stated dollar amount that is not hourly: read it as an income cutoff.
    try:
        import numpy as np
        from dc_api import server as S
        inc = np.asarray(S.INCOME["incomes"], dtype=float)
        w = np.asarray(S.INCOME["weights"], dtype=float)
        seen = set()
        for v in _numbers(q):
            if v < 5_000 or v in seen or HOURLY.search(q):
                continue
            seen.add(v)
            under = float(w[inc < v].sum())
            out.append(fact(
                f"households_under_{int(v)}",
                f"DC households with annual income below {_money(v)}",
                under, f"{under:,.0f} households ({_pct(under / w.sum())})", ACS))
            if len(seen) >= 2:
                break
    except Exception:                                           # noqa: BLE001
        pass

    # A service the city actually records.
    try:
        from dc_api import server as S
        year = max(S.SR)
        pool: Dict[str, int] = {}
        for ward in S.SR[year]["byWardService"].values():
            for k, v in ward.items():
                pool[k] = pool.get(k, 0) + v
        # Matching the raw question against service names picks up junk: "how
        # bad is diabetes in DC" matched "DC - How Am I Driving?" on the word
        # "how". Only content words are offered.
        terms = [w for w in re.findall(r"[a-z]{4,}", q.lower()) if w not in COMMON]
        hits = S.match_services(" ".join(terms), pool) if terms else {}
        for name, count in sorted(hits.items(), key=lambda kv: -kv[1])[:3]:
            out.append(fact(
                f"sr_{re.sub(r'[^a-z0-9]+', '_', name.lower())}",
                f"311 requests recorded District-wide for {name} in {year}",
                count, f"{count:,} requests", SR_SRC))
    except Exception:                                           # noqa: BLE001
        pass

    # A health measure CDC publishes for DC tracts.
    try:
        import numpy as np
        from dc_api import server as S
        words = set(re.findall(r"[a-z]{4,}", q.lower()))
        for code, meta in S.PLACES["measures"].items():
            name = (meta.get("measure") or "").lower()
            if not (code.lower() in words or (name and
                    words & set(re.findall(r"[a-z]{4,}", name)))):
                continue
            vals = [r["value"] for k, r in S.PLACES["byTract"].items()
                    if k.split("|")[1] == code and r.get("value") is not None]
            if not vals:
                continue
            out.append(fact(
                f"places_{code.lower()}",
                f"{meta.get('measure') or code}: median across {len(vals)} DC "
                f"census tracts",
                float(np.median(vals)),
                f"{np.median(vals):.1f}% (tract range {min(vals):.1f}% to "
                f"{max(vals):.1f}%)", PLACES_SRC))
            break
    except Exception:                                           # noqa: BLE001
        pass

    return out


def pack(question: str, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    specific = for_question(question, spec)
    seen = {f["id"] for f in specific}
    return specific + [f for f in baseline() if f["id"] not in seen]
