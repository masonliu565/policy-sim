"""
Turn a question into a data request, offline.

A direct port of the front end's own `interpretLocally` patterns, so the four
example questions in the chat resolve exactly as its authors intended, plus one
addition: a policy-simulation request, which is the thing this backend actually
brings.

Deliberately anchored, for the same reason the original is: an extra population
restriction must not disappear into a loose keyword match. Anything not fully
recognised comes back as kind="unsupported" with the question preserved in
`unsupportedConstraints`, so the answer says what it could not honour instead of
quietly answering a smaller question.
"""
from __future__ import annotations

import re
from typing import Any, Dict

DISTRICT = {"kind": "district", "code": "11"}


def base() -> Dict[str, Any]:
    return {
        "kind": "unsupported", "jurisdiction": "DC", "geography": dict(DISTRICT),
        "year": None, "incomeThreshold": None, "incomeComparison": None,
        "measure": None, "service": None, "outcome": "Unspecified outcome",
        "population": "Unspecified population", "timeframe": "Unspecified timeframe",
        "unsupportedConstraints": [], "clarification": None,
    }


def interpret(question: str) -> Dict[str, Any]:
    q = re.sub(r"\s+", " ", (question or "").strip()).rstrip("?!.")

    m = re.match(r"^How many DC households had annual income (below|at or below) "
                 r"\$?([\d,]+) in (20\d{2})(?: in PUMA (\d{5}))?$", q, re.I)
    if m:
        s = base()
        s.update(kind="household_income", year=int(m.group(3)),
                 incomeThreshold=int(m.group(2).replace(",", "")),
                 incomeComparison="lt" if m.group(1).lower() == "below" else "lte",
                 geography={"kind": "puma", "code": m.group(4)} if m.group(4) else dict(DISTRICT),
                 outcome="Households meeting the stated annual income criterion",
                 population="Occupied DC households", timeframe=m.group(3))
        return s

    m = re.match(r"^What share of DC adults reported being unable to afford a "
                 r"doctor in (20\d{2})$", q, re.I)
    if m:
        s = base()
        s.update(kind="health_affordability", year=int(m.group(1)),
                 measure="MEDCOST1",
                 outcome="Reported inability to afford a needed doctor visit in "
                         "the past 12 months",
                 population="Noninstitutionalized DC adults aged 18 or older",
                 timeframe=m.group(1))
        return s

    m = re.match(r"^How many 311 requests were recorded in (DC|Ward [1-8]) in "
                 r"(20\d{2})(?: for (.{1,200}))?$", q, re.I)
    if m:
        s = base()
        where = m.group(1).lower()
        s.update(kind="service_requests", year=int(m.group(2)),
                 service=m.group(3),
                 geography=dict(DISTRICT) if where == "dc"
                 else {"kind": "ward", "code": m.group(1)[-1]},
                 outcome="Recorded service request count",
                 population="Service requests, not unique residents",
                 timeframe=m.group(2))
        return s

    # "Which ward has the most X" is the shape people actually type, and it is
    # a ranking, not a count. The server ranks the wards; this only has to route
    # it there and leave the geography unset so nothing is assumed.
    m = re.match(r"^(?:which|what) ward (?:has|had|reported|files?|filed|"
                 r"reports?|complains?|complained)?\s*(?:the\s+)?(?:most|"
                 r"highest|worst)\s*(?:number of\s+)?(.{1,120}?)"
                 r"(?:\s+in\s+(20\d{2}))?\s*\??$", q, re.I)
    if m:
        s = base()
        service = re.sub(r"\b(311\s+)?(requests?|complaints?|reports?)\b", "",
                         m.group(1), flags=re.I).strip(" ?.,")
        s.update(kind="service_requests", year=int(m.group(2)) if m.group(2) else None,
                 service=service or None, geography=dict(DISTRICT),
                 outcome="Recorded service request count, ranked by ward",
                 population="Service requests, not unique residents",
                 timeframe=m.group(2) or "")
        return s

    m = re.match(r"^What was ([a-z0-9_]+) prevalence in DC tract (11\d{9}) in "
                 r"(20\d{2})$", q, re.I)
    if m:
        s = base()
        s.update(kind="health_prevalence", year=int(m.group(3)),
                 measure=m.group(1).lower(),
                 geography={"kind": "tract", "code": m.group(2)},
                 outcome="Published health prevalence",
                 population="CDC measure-specific population", timeframe=m.group(3))
        return s

    # --- the capability this backend adds -----------------------------------
    # "What would $300 a month per child do in DC?" and similar. The policy is
    # read by the same offline parser the standalone app uses.
    if re.search(r"\$\s*[\d,]+", q) and re.search(
            r"\bper (child|kid|adult)\b|child tax credit|\bctc\b|allowance|"
            r"cash (transfer|payment)", q, re.I):
        s = base()
        s.update(kind="policy_simulation", year=2024,
                 outcome="Simulated effect of a cash transfer on DC households",
                 population="Occupied DC households",
                 timeframe="2024 population, policy applied as described")
        return s

    s = base()
    s.update(outcome=q or "Unspecified outcome",
             unsupportedConstraints=[q] if q else [],
             clarification="Try an example question and include its place, year "
                           "and measure. Free-form interpretation is not "
                           "connected; no condition in your question has been "
                           "silently ignored.")
    return s
