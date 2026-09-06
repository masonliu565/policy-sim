"""
Which existing DC law a proposed change would sit on top of.

A policy is never written on a blank page. "Raise the income tax" changes a
schedule that already exists in D.C. Code 47-1806.03; a paid leave change edits
Title 32. Saying so turns a simulated number into a proposal someone could
actually draft, and it costs nothing to be right about, because the citations
come from the Council's own codified XML rather than from anyone's memory.

The index is built by dc_api/build_dc_code_index.py from
DCCouncil/law-xml-codified: 24,113 sections across 55 titles, each with its
citation and heading. Matching is deliberately conservative. A wrong statute
cited confidently is worse than no statute, so a section is only offered when
the question's own distinctive words appear in its heading, and generic legal
vocabulary is excluded.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
INDEX = REPO / "data" / "dc_code" / "sections.json"
RATES = REPO / "data" / "dc_code" / "income_tax_rates_47-1806.03.json"
CODE_URL = "https://code.dccouncil.gov/us/dc/council/code/sections/"

# Words that appear in thousands of headings and identify nothing.
GENERIC = {
    "district", "columbia", "council", "code", "section", "sections", "act",
    "law", "laws", "rules", "rule", "regulation", "regulations", "provisions",
    "general", "definitions", "purpose", "scope", "application", "authority",
    "powers", "duties", "requirements", "procedures", "penalties", "violation",
    "violations", "enforcement", "administration", "establishment", "created",
    "board", "commission", "office", "department", "agency", "director",
    "certain", "other", "same", "shall", "with", "from", "that", "this",
    "under", "into", "upon", "such", "than", "when", "where", "which", "their",
    "public", "person", "persons", "property", "issued", "filing", "report",
    "reports", "annual", "amount", "amounts", "payment", "payments", "fund",
    "funds", "year", "years", "time", "term", "terms", "more", "less", "than",
    "district's", "used", "make", "made", "give", "given", "take", "taken",
}

# Policies whose governing section we know exactly, so nothing has to be
# guessed from a heading match.
KNOWN_STATUTE = {
    "tax_policy": ("47-1806.03",
                   "Tax on residents and nonresidents - Imposition and rates."),
}

_IDX: Optional[Dict[str, Any]] = None


def load() -> Optional[Dict[str, Any]]:
    global _IDX
    if _IDX is None:
        try:
            _IDX = json.loads(INDEX.read_text(encoding="utf-8"))
        except Exception:                                       # noqa: BLE001
            _IDX = False
    return None if _IDX is False else _IDX


def url_for(cite: str) -> str:
    return CODE_URL + cite


def _terms(question: str) -> List[str]:
    words = re.findall(r"[a-z]{4,}", (question or "").lower())
    return [w for w in dict.fromkeys(words) if w not in GENERIC]


def find(question: str, limit: int = 3) -> List[Dict[str, str]]:
    """Sections whose headings the question's distinctive words appear in.

    Requires at least two matching terms, or one long specific term, so a
    question about diabetes does not surface a statute because both contain the
    word "health".
    """
    idx = load()
    if not idx:
        return []
    terms = _terms(question)
    if not terms:
        return []
    scored = []
    for s in idx["sections"]:
        h = s["heading"].lower()
        hits = [t for t in terms if re.search(r"\b" + re.escape(t), h)]
        if not hits:
            continue
        # Rank by how much distinctive language matched, not by how many words.
        # Counting words put "Buildings exceeding 60 feet in height" above
        # "Affordable Housing Opportunities" for a question about affordable
        # housing, because "build" and "more" are two words and "affordable"
        # is one. Character length is a crude proxy for specificity and it gets
        # that pair the right way round.
        weight = sum(len(t) for t in hits)
        if weight < 12:            # roughly: two ordinary words, or one long one
            continue
        scored.append((-weight, len(s["heading"]), s, hits))
    scored.sort(key=lambda r: (r[0], r[1]))
    out = []
    for _w, _l, s, hits in scored[:limit]:
        out.append({"cite": s["cite"], "heading": s["heading"],
                    "title": s["title"],
                    "titleName": idx["titles"].get(s["title"], ""),
                    "url": url_for(s["cite"]),
                    "matched": ", ".join(hits)})
    return out


def for_kind(kind: str) -> Optional[Dict[str, str]]:
    """The governing section for a policy kind we handle explicitly."""
    got = KNOWN_STATUTE.get(kind)
    if not got:
        return None
    cite, heading = got
    return {"cite": cite, "heading": heading, "url": url_for(cite)}


def income_tax_rates() -> Optional[Dict[str, Any]]:
    """The verbatim bracket language of 47-1806.03, as codified."""
    try:
        return json.loads(RATES.read_text(encoding="utf-8"))
    except Exception:                                           # noqa: BLE001
        return None


def statute_evidence(cite: str, heading: str = "") -> Dict[str, str]:
    return {"publisher": "Council of the District of Columbia",
            "dataset": f"D.C. Code {cite}" + (f" - {heading}" if heading else ""),
            "url": url_for(cite),
            "referencePeriod": "codified DC Code"}
