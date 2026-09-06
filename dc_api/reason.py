"""
The answer of last resort: reason from the evidence we hold, and never refuse.

Some questions do not map onto a query we can run. "Should DC build more
affordable housing" is a real question that no count answers. The old behaviour
was to return unsupported and list what was missing, which is honest but is
also a dead end -- and a dead end in front of an audience reads as the system
not working.

This answers those questions instead. It is handed a fact pack (see facts.py)
and asked to reason about the question using those numbers, policy mechanism,
and what the evidence cannot settle. What it may NOT do is invent a number.

HOW THAT IS ENFORCED, rather than merely requested. Every numeral in the
model's answer is checked against the fact pack, the question, and the run's
own computed figures. A numeral that traces to none of them is a fabrication,
and the answer is regenerated once with the offending values named. If it
fabricates again, the model is dropped entirely and the answer is assembled
from the facts by template. So the system always answers, and an invented
statistic never reaches the screen.

The model supplies reasoning. The numbers are always ours.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from dc_api import interpret_claude

SYSTEM = """You are the reasoning layer of a public-policy evidence service for \
Washington, DC. You explain what the evidence supports. You never collect it \
and you never estimate it.

ABSOLUTE RULE ON NUMBERS. You may state a number ONLY if it appears in the \
FACTS block or in the user's question. You may not supply a statistic from \
memory, from the research literature, or from your own estimation -- not even \
approximately, not even hedged, not even as "roughly" or "studies suggest \
around". If you want to express a magnitude you have no fact for, use words \
(large, small, concentrated among, a minority of) instead of digits. Writing a \
number that is not in FACTS is the single worst thing you can do here.

ABSOLUTE RULE ON REFUSING. You always answer. Never say more information is \
needed, never say you cannot answer, never ask the user to rephrase. If the \
evidence does not settle the question, say what it does establish, explain the \
mechanism that would decide it, and say plainly which part is measured and \
which part is reasoning. A question that the data cannot close is still a \
question you engage with.

Never write a fact's id in your prose. The ids (dc_wage_p10, couple_no_kids, tax_burden_Q5 and the like) are internal handles; a reader sees only your sentences, so name the thing in words -- "the lowest fifth by income", "couples without children". Ids belong in usedFactIds and nowhere else.

Be concrete and brief. A reader should finish knowing what is known, what \
follows from it, and what would change the answer. No preamble, no restating \
the question, no offers of further help.

Return ONLY a JSON object:
{
  "headline": "<=12 words, the answer itself, not a topic label",
  "answer": "2-4 sentences answering directly, using FACTS where they bear",
  "mechanism": "2-3 sentences on how the policy would actually operate",
  "affected": "1-2 sentences on who is most affected, from FACTS where possible",
  "measured": ["which specific claims above are counted or computed"],
  "reasoned": ["which specific claims above are inference rather than measurement"],
  "wouldChange": ["what evidence would change this conclusion"],
  "usedFactIds": ["ids of FACTS you actually relied on"]
}"""

# Numerals that are never a factual claim about DC.
YEAR = re.compile(r"^(1[89]|20)\d{2}$")
NUMERAL = re.compile(r"\d[\d,]*(?:\.\d+)?")
FIELDS = ("headline", "answer", "mechanism", "affected")


def available() -> bool:
    return interpret_claude.available()


def _norm(tok: Any) -> str:
    t = str(tok).replace(",", "").rstrip(".")
    try:
        f = float(t)
    except ValueError:
        return t
    return str(int(f)) if f == int(f) else f"{f:g}"


def allowed_tokens(question: str, facts: List[Dict[str, Any]]) -> set:
    """Every numeric token the model is permitted to write."""
    ok = set()

    def add(x: Any) -> None:
        if x is None:
            return
        for tok in NUMERAL.findall(str(x)):
            n = _norm(tok)
            ok.add(n)
            try:
                f = float(n)
            except ValueError:
                continue
            # Rounded and rescaled renderings of the same fact are the same
            # claim: 329,688 households may be written as 330,000 or 329.7k.
            for r in (round(f), round(f, 1), round(f, 2), round(f / 1_000),
                      round(f / 1_000, 1), round(f / 1_000_000),
                      round(f / 1_000_000, 1), round(f, -2), round(f, -3)):
                ok.add(_norm(r))
            if 0 < f < 1:                      # a share written as a percentage
                for r in (round(f * 100), round(f * 100, 1)):
                    ok.add(_norm(r))

    for f in facts:
        add(f.get("value"))
        add(f.get("display"))
        add(f.get("label"))
    add(question)
    return ok


def violations(text: str, ok: set) -> List[str]:
    bad = []
    for tok in NUMERAL.findall(text or ""):
        n = _norm(tok)
        if n in ok or YEAR.match(n):
            continue
        try:
            f = float(n)
        except ValueError:
            continue
        if f <= 12 and f == int(f):        # small counts, list positions, ages
            continue
        bad.append(tok)
    return bad


def _prompt(question: str, facts: List[Dict[str, Any]], spec: Dict[str, Any]) -> str:
    lines = [f"- [{f['id']}] {f['label']}: {f['display']}  (source: {f['source']})"
             for f in facts]
    return (f"QUESTION: {question}\n\n"
            f"FACTS (the only numbers you may state):\n" + "\n".join(lines) +
            f"\n\nHow the question was parsed: {json.dumps(spec.get('kind'))}. "
            f"Answer the question.")


def _call(messages: List[Dict[str, str]], timeout: float) -> Optional[str]:
    key = interpret_claude.api_key()
    if not key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key, timeout=timeout, max_retries=0)
        # A tax answer carries 25+ facts and four prose fields; at 1400 the
        # JSON was being cut off mid-array, which looked exactly like the
        # guardrail rejecting a draft. It is not the same failure and should
        # not be diagnosed as one.
        msg = client.messages.create(model=interpret_claude.MODEL, max_tokens=3000,
                                     system=SYSTEM, messages=messages)
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    except Exception:                                           # noqa: BLE001
        return None


def _parse(raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        got = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return got if isinstance(got, dict) else None


def reason(question: str, spec: Dict[str, Any], facts: List[Dict[str, Any]],
           timeout: float = 40.0) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Returns (answer, notes). answer is None when the model was unusable."""
    ok = allowed_tokens(question, facts)
    notes: List[str] = []
    messages = [{"role": "user", "content": _prompt(question, facts, spec)}]

    for attempt in (1, 2):
        got = _parse(_call(messages, timeout) or "")
        if not got:
            notes.append("The reasoning model did not return a usable answer "
                         "(no parseable object, most often a truncated reply). "
                         "This is not the fabrication guard rejecting it.")
            return None, notes
        text = " ".join(str(got.get(f, "")) for f in FIELDS)
        bad = violations(text, ok)
        if not bad:
            if attempt == 2:
                notes.append("The first draft stated a figure that is not in the "
                             "evidence; it was rejected and regenerated.")
            return got, notes
        if attempt == 1:
            messages += [
                {"role": "assistant", "content": json.dumps(got)},
                {"role": "user", "content":
                    "These numbers appear in your answer but are in neither the "
                    "FACTS block nor the question: " + ", ".join(sorted(set(bad))) +
                    ". They are fabrications. Rewrite the answer stating only "
                    "numbers from FACTS or the question, replacing the rest with "
                    "words. Return the same JSON object."}]
    notes.append("The reasoning model twice stated figures that are not in the "
                 "evidence, so its text was discarded and this answer was "
                 "assembled from the measured facts alone.")
    return None, notes


def fallback(question: str, facts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """An answer built only from facts, used when the model is unavailable or
    would not stay inside the evidence. Still answers; still refuses nothing."""
    top = facts[:5]
    body = "; ".join(f"{f['label'].lower()} is {f['display']}" for f in top[:3])
    return {
        "headline": "What the DC evidence shows",
        "answer": ("This is answered from the measured evidence directly: "
                   + body + "." if body else
                   "The loaded evidence does not carry a quantity that speaks "
                   "to this question directly."),
        "mechanism": "",
        "affected": "",
        "measured": [f["label"] for f in top],
        "reasoned": [],
        "wouldChange": ["Evidence that measures this question directly."],
        "usedFactIds": [f["id"] for f in top],
    }
