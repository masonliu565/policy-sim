"""
The DC Code index, and the statute check it makes possible.

The valuable test here is the last one. model/dc_tax.py took its rate schedule
from the Office of Tax and Revenue's website. The Council publishes the same
schedule as codified law, from a different system, and those two agreeing is
worth more than either on its own -- a transcription slip would have to happen
identically in two places to survive.

    python -m pytest dc_api/test_dc_code.py -q
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "model"))

INDEX = REPO / "data" / "dc_code" / "sections.json"
pytestmark = pytest.mark.skipif(
    not INDEX.exists(),
    reason="run bash dc_api/setup_dc_code.sh && python dc_api/build_dc_code_index.py")

from dc_api import dc_code as LAW  # noqa: E402


def test_the_index_covers_the_whole_code():
    idx = LAW.load()
    assert len(idx["sections"]) > 20_000
    assert len(idx["titles"]) >= 50
    assert all(s["cite"] and s["heading"] is not None for s in idx["sections"][:200])


def test_a_citation_resolves_to_the_councils_own_url():
    url = LAW.url_for("47-1806.03")
    assert url == ("https://code.dccouncil.gov/us/dc/council/code/sections/"
                   "47-1806.03")


def test_a_policy_question_finds_the_law_it_would_touch():
    hits = LAW.find("what if DC expanded paid family leave")
    assert hits
    assert any("leave" in h["heading"].lower() for h in hits)


def test_questions_with_no_statutory_hook_get_none():
    """A wrong statute cited confidently is worse than no statute. These must
    stay empty rather than reaching for something that shares a word."""
    for q in ["how many potholes in ward 7", "how bad is diabetes in DC",
              "what if we gave every family $400 a month per child"]:
        assert LAW.find(q) == [], q


def test_specific_language_outranks_generic_language():
    """Ranking by match COUNT put "Buildings exceeding 60 feet in height" above
    "Affordable housing" for a housing question, because "build" and "more" are
    two words and "affordable" is one."""
    hits = LAW.find("should DC build more affordable housing")
    assert hits
    assert "affordable" in hits[0]["heading"].lower()


def test_the_tax_schedule_matches_the_codified_statute():
    """model/dc_tax.py was transcribed from the tax office's website. This is
    the same schedule as enacted law, from a different publisher and a
    different system. They must agree bracket for bracket."""
    import dc_tax as T

    rates = LAW.income_tax_rates()
    assert rates and rates["bracketLanguage"]
    language = " | ".join(rates["bracketLanguage"])

    for lo, base, rate in T.BRACKETS[1:]:          # the first bracket has no base
        pct = f"{rate * 100:g}"
        pattern = (rf"\${base:,.0f}, plus {re.escape(pct)}% of the excess "
                   rf"(?:over|above) \${lo:,.0f}")
        assert re.search(pattern, language), (
            f"D.C. Code 47-1806.03 has no bracket '${base:,.0f}, plus {pct}% "
            f"of the excess over ${lo:,.0f}'. Either the statute changed or "
            f"model/dc_tax.py is wrong.")


def test_the_statute_extract_names_its_source():
    rates = LAW.income_tax_rates()
    assert rates["cite"] == "47-1806.03"
    assert rates["url"].startswith("https://code.dccouncil.gov/")
