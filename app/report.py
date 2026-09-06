"""
B4 — policy memo generation, and the numeric verification that follows it.

The model writes prose. It does not produce numbers. Every quantitative claim
must already exist in the sim_result JSON handed to it, and the verification
pass afterwards checks that mechanically rather than trusting the instruction
to have been followed.

That check is the point. "We tell the model not to hallucinate" is a claim.
"Here are the numbers in the report that are not in the input, and there are
zero of them" is a demonstration.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

DEFAULT_MODEL = os.environ.get("POLICY_SIM_MODEL", "claude-opus-5")
DEFAULT_TIMEOUT_S = float(os.environ.get("POLICY_SIM_REPORT_TIMEOUT", "60"))

SECTIONS = [
    "Plain-English description",
    "Key changes",
    "Direct beneficiaries",
    "Direct cost bearers",
    "Estimated support",
    "Most supportive groups",
    "Most opposed groups",
    "Historical analogues",
    "Uncertainty",
    "Limitations",
]

SYSTEM_PROMPT = """\
You write a one-page policy memo from a simulation result. You are an explainer, \
not an analyst: the arithmetic is already done and handed to you.

HARD GUARDRAILS — these are not style preferences.

1. EVERY QUANTITATIVE CLAIM MUST COME FROM THE PROVIDED JSON. Do not compute a \
new number. Do not estimate. Do not convert units, re-round to a different \
precision, sum two figures, take a difference, or annualise anything. If a \
number is not literally present in the input, it does not go in the memo. This \
includes numbers that would be "obviously" correct to derive.

2. EVERY HISTORICAL CLAIM MUST CITE AN evidence_id from the provided evidence \
records, written inline as (ev_xxx). A historical statement without an \
evidence_id is not allowed — drop the statement instead.

3. IF SOMETHING IS UNSUPPORTED, label it uncertain or leave it out. Never \
present an inference as a finding.

4. NEVER STATE A SUBGROUP PERCENTAGE THAT IS NOT IN THE INPUT. Not for a group \
that was not simulated, not as a range you construct yourself, not as an \
approximation of a group that was.

5. ALWAYS PAIR AN ESTIMATE WITH ITS INTERVAL. When you give a median, give the \
p05 and p95 that came with it, in the same sentence. A bare central estimate \
misrepresents the result.

STRUCTURE — use exactly these sections, as markdown level-3 headings, in order:

### Plain-English description
### Key changes
### Direct beneficiaries
### Direct cost bearers
### Estimated support
### Most supportive groups
### Most opposed groups
### Historical analogues
### Uncertainty
### Limitations

Write plainly. No preamble before the first heading and no summary after the \
last. Reproduce the limitations from the input's warnings array rather than \
inventing your own.
"""


# ---------------------------------------------------------------------------
# Numeric verification
# ---------------------------------------------------------------------------
# Matches 3, 3.4, 3,400, $3,400, 74%, -2.7, 1.9B. The trailing suffix and the
# leading currency symbol are stripped before comparison.
#
# The (?<![\w.%]) guard stops the hyphen in a range like "5th-95th" or
# "51%-65%" being read as a minus sign, and stops the digits inside tokens like
# "p05" or "ev_001" being picked up as standalone quantities.
NUMBER_RE = re.compile(
    r"(?<![\w.%])[-+\u2212]?\$?\d[\d,]*(?:\.\d+)?"
    r"\s*(?:%|percent|pts?|[BMKT]\b)?"
    r"(?P<ordinal>st|nd|rd|th)?"
)

# Ordinals, section numbers and small counting words produce noise; a memo that
# says "the first group" is not making a quantitative claim. Bare integers 0-12
# are treated as prose unless they also appear in the input.
PROSE_INTEGER_CEILING = 12


@dataclass
class NumericFinding:
    token: str          # as written in the report
    value: float        # parsed
    context: str        # surrounding words, for the panel

    def as_dict(self) -> Dict[str, Any]:
        return {"token": self.token, "value": self.value, "context": self.context}


@dataclass
class Verification:
    findings: List[NumericFinding] = field(default_factory=list)
    checked: int = 0
    missing_evidence_ids: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings and not self.missing_evidence_ids

    @property
    def unverified_count(self) -> int:
        return len(self.findings)


def _parse_token(tok: str) -> Optional[Tuple[float, bool]]:
    """Return (value, is_percent). Suffixes are expanded to their full value."""
    t = tok.strip().replace("−", "-").replace("$", "").replace(",", "").strip()
    is_pct = False
    if t.endswith("%"):
        is_pct, t = True, t[:-1]
    for word in ("percent", "pts", "pt"):
        if t.endswith(word):
            is_pct, t = True, t[: -len(word)]
    mult = 1.0
    t = t.strip()
    if t and t[-1] in "BMKT":
        mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[t[-1]]
        t = t[:-1]
    try:
        return float(t.strip()) * mult, is_pct
    except ValueError:
        return None


def collect_input_numbers(*payloads: Any) -> Set[float]:
    """Every number anywhere in the input, plus its percent/fraction twin.

    A scenario stores a rate as 0.084; a memo writes it as 8.4%. Both are the
    same claim, so each input number is admitted in both forms. This widens
    what passes — deliberately. The check exists to catch numbers with no
    origin in the input at all, not to police formatting.
    """
    out: Set[float] = set()

    def walk(node: Any) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            v = float(node)
            out.add(v)
            out.add(round(v * 100, 6))   # 0.084 -> 8.4
            out.add(round(v / 100, 6))   # 8.4   -> 0.084
        elif isinstance(node, str):
            # Numbers written inside input text are still numbers present in
            # the input. Evidence records carry question wording like "$250 per
            # month per child 17 and under"; a memo quoting that is citing the
            # source, not inventing a figure.
            for m in NUMBER_RE.finditer(node):
                if m.group("ordinal"):
                    continue
                parsed = _parse_token(m.group(0))
                if parsed is None:
                    continue
                v = parsed[0]
                out.add(v)
                out.add(round(v * 100, 6))
                out.add(round(v / 100, 6))
        elif isinstance(node, dict):
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    for p in payloads:
        walk(p)
    return out


def _close_to_any(value: float, pool: Set[float]) -> bool:
    """Rounding-tolerant membership.

    A memo rounding 0.0843 to 8.4% must pass; 8.9% must not. Tolerance is one
    unit in the last place the memo actually wrote, floored at a relative 0.5%.
    """
    for cand in pool:
        if cand == value:
            return True
        scale = max(abs(cand), abs(value), 1e-9)
        # The floor has to follow the magnitude. 0.05 is the right slack for a
        # figure written as 8.4, and far too much for the same figure stored as
        # 0.084 — at that scale it would accept 8.9% as a match for 8.4%.
        floor = 0.05 if scale >= 1 else 0.0005
        if abs(cand - value) <= max(floor, scale * 0.005):
            return True
    return False


def verify_numbers(report_text: str, *payloads: Any) -> Verification:
    """Every numeric token in the report that is not present in the input.

    This runs AFTER generation and is displayed whether or not it finds
    anything. "0 unverified numbers" is the result worth showing.
    """
    pool = collect_input_numbers(*payloads)
    result = Verification()

    for match in NUMBER_RE.finditer(report_text):
        tok = match.group(0).strip()
        if match.group("ordinal"):
            # "the 95th percentile" is prose about the interval, not a claim
            # about a quantity.
            continue
        parsed = _parse_token(tok)
        if parsed is None:
            continue
        value, is_pct = parsed
        result.checked += 1

        candidates = {value}
        if is_pct:
            candidates.add(value / 100.0)   # 8.4% may be stored as 0.084
        else:
            candidates.add(value * 100.0)

        # Small bare integers are prose ("the first two groups") unless the
        # input actually contains them.
        if (not is_pct and float(value).is_integer()
                and abs(value) <= PROSE_INTEGER_CEILING and "$" not in tok):
            if not any(_close_to_any(c, pool) for c in candidates):
                continue

        if any(_close_to_any(c, pool) for c in candidates):
            continue

        start, end = max(0, match.start() - 45), min(len(report_text), match.end() + 45)
        ctx = " ".join(report_text[start:end].split())
        result.findings.append(NumericFinding(token=tok, value=value, context=f"…{ctx}…"))

    result.missing_evidence_ids = _unknown_evidence_ids(report_text, payloads)
    return result


def _unknown_evidence_ids(text: str, payloads: Iterable[Any]) -> List[str]:
    """evidence_ids cited in the memo that do not exist in the input."""
    known: Set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("evidence_id"), str):
                known.add(node["evidence_id"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and node.startswith("ev_"):
            known.add(node)

    for p in payloads:
        walk(p)

    cited = set(re.findall(r"\bev_[A-Za-z0-9_]+", text))
    return sorted(cited - known)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
@dataclass
class ReportResult:
    text: str
    verification: Verification
    model: str = ""
    ok: bool = True


@dataclass
class ReportError:
    message: str
    detail: str = ""
    ok: bool = False


def build_user_prompt(sim_result: Dict[str, Any], evidence: List[Dict[str, Any]]) -> str:
    return (
        "SIMULATION RESULT (the only source of numbers):\n"
        f"{json.dumps(sim_result, indent=2)}\n\n"
        "EVIDENCE RECORDS (the only source of historical claims; cite by evidence_id):\n"
        f"{json.dumps(evidence, indent=2)}\n\n"
        "Write the memo."
    )


def relevant_evidence(
    sim_result: Dict[str, Any], evidence: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Evidence the scenario actually cites, plus non-holdout national context.

    Holdout records are excluded here as they are everywhere else — they are
    reserved for the opinion backtest and must not leak into a generated memo.
    """
    cited = {
        eid
        for group in sim_result.get("opinion", {}).get("by_group", [])
        for eid in (group.get("evidence_ids") or [])
    }
    keep = [r for r in evidence if not r.get("holdout")]
    if cited:
        picked = [r for r in keep if r.get("evidence_id") in cited]
        if picked:
            return picked
    return [r for r in keep if r.get("subgroup_type") == "national"] or keep


def generate_report(
    sim_result: Dict[str, Any],
    evidence: List[Dict[str, Any]],
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT_S,
    client: Any = None,
):
    """Generate the memo, then verify it. Returns ReportResult or ReportError."""
    if os.environ.get("POLICY_SIM_DEMO_MODE", "").lower() in ("1", "true", "yes"):
        return ReportError(
            "DEMO_MODE is on — no memo is generated and no network call is made.",
            "The scenario figures and their intervals are shown above.",
        )

    import anthropic

    subset = relevant_evidence(sim_result, evidence)

    try:
        if client is None:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                return ReportError(
                    "No ANTHROPIC_API_KEY is set, so no memo can be written.",
                    "Every number above comes from the scenario file and is unaffected.",
                )
            client = anthropic.Anthropic(timeout=timeout, max_retries=1)

        response = client.messages.create(
            model=model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_prompt(sim_result, subset)}],
        )
        if response.stop_reason == "refusal":
            return ReportError("The model declined to write this memo.",
                               getattr(response.stop_details, "explanation", "") or "")
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    except anthropic.APITimeoutError:
        return ReportError(f"Memo generation timed out after {timeout:.0f}s.",
                           "The figures above are unaffected — they come from the scenario file.")
    except anthropic.AuthenticationError:
        return ReportError("The ANTHROPIC_API_KEY was rejected.", "")
    except anthropic.RateLimitError:
        return ReportError("Rate limited while writing the memo.", "")
    except anthropic.APIConnectionError:
        return ReportError("Could not reach the model (network).", "")
    except anthropic.APIStatusError as exc:
        return ReportError(f"Memo generation failed ({exc.status_code}).",
                           str(getattr(exc, "message", exc))[:300])
    except Exception as exc:  # noqa: BLE001
        return ReportError("Memo generation failed unexpectedly.",
                           f"{type(exc).__name__}: {exc}"[:300])

    return ReportResult(
        text=text, verification=verify_numbers(text, sim_result, subset), model=model
    )
