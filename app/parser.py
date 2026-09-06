"""
B2 — free-form policy text -> validated policy_spec.

The LLM's only job here is translation: English in, levers out. It is never
asked for an approval percentage, a poverty effect or a cost, and the system
prompt forbids producing one. Every number the app displays comes from the
simulation, not from this file.

Nothing in this module raises on bad model output. A parse that fails returns
a ParseError object the caller renders; a demo must not die because a model
returned a stray markdown fence.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# ---------------------------------------------------------------------------
# Bounds. A per-child credit outside this range is a parse failure, not a
# policy — the model has almost certainly misread a figure or hallucinated a
# magnitude, and a $9,000,000 credit would sail through the engine and produce
# a confident, absurd cost.
# ---------------------------------------------------------------------------
MAX_PER_CHILD_CREDIT = 20_000.0
MAX_FLAT_TRANSFER = 100_000.0
MAX_PHASEOUT_START = 10_000_000.0

DEFAULT_MODEL = os.environ.get("POLICY_SIM_MODEL", "claude-opus-5")
DEFAULT_TIMEOUT_S = float(os.environ.get("POLICY_SIM_PARSER_TIMEOUT", "30"))


class Levers(BaseModel):
    """No-op defaults throughout: anything the text does not mention stays off."""

    model_config = ConfigDict(extra="forbid")  # unknown lever keys are an error

    credit_per_child_under_6: float = 0
    credit_per_child_6_to_17: float = 0
    fully_refundable: bool = False
    phaseout_start_single: Optional[float] = None
    phaseout_start_joint: Optional[float] = None
    phaseout_rate: float = 0
    flat_transfer_per_adult: float = 0

    @field_validator("credit_per_child_under_6", "credit_per_child_6_to_17")
    @classmethod
    def _child_credit_in_range(cls, v: float, info) -> float:
        if v < 0:
            raise ValueError(f"{info.field_name}: negative credit ({v})")
        if v > MAX_PER_CHILD_CREDIT:
            raise ValueError(
                f"{info.field_name}: ${v:,.0f} per child exceeds the ${MAX_PER_CHILD_CREDIT:,.0f} "
                "plausibility ceiling. Rejected as a parse failure rather than simulated."
            )
        return v

    @field_validator("flat_transfer_per_adult")
    @classmethod
    def _flat_in_range(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"flat_transfer_per_adult: negative transfer ({v})")
        if v > MAX_FLAT_TRANSFER:
            raise ValueError(
                f"flat_transfer_per_adult: ${v:,.0f} per adult exceeds the "
                f"${MAX_FLAT_TRANSFER:,.0f} plausibility ceiling."
            )
        return v

    @field_validator("phaseout_rate")
    @classmethod
    def _rate_is_a_rate(cls, v: float) -> float:
        if not 0 <= v <= 1:
            raise ValueError(
                f"phaseout_rate: {v} is not a fraction between 0 and 1. "
                "A 5% phaseout is 0.05, not 5."
            )
        return v

    @field_validator("phaseout_start_single", "phaseout_start_joint")
    @classmethod
    def _threshold_in_range(cls, v: Optional[float], info) -> Optional[float]:
        if v is None:
            return v
        if v < 0 or v > MAX_PHASEOUT_START:
            raise ValueError(f"{info.field_name}: implausible threshold ({v})")
        return v


class PolicySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str
    label: str
    domain: str
    instrument: str
    levers: Levers = Field(default_factory=Levers)
    parser_uncertainties: List[str] = Field(default_factory=list)

    @field_validator("domain")
    @classmethod
    def _in_scope(cls, v: str) -> str:
        if v != "cash_transfer":
            raise ValueError(
                f"domain must be 'cash_transfer'; got {v!r}. This simulator models "
                "cash transfers and child tax credits only."
            )
        return v


@dataclass
class ParseError:
    """A structured failure. Never raised — returned, and rendered in the app."""

    kind: str          # out_of_scope | implausible | invalid_json | schema | api | timeout
    message: str
    detail: str = ""
    raw: str = ""

    ok = False

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "message": self.message, "detail": self.detail}


@dataclass
class ParseResult:
    spec: PolicySpec
    raw: str = ""
    model: str = ""

    ok = True

    def as_dict(self) -> Dict[str, Any]:
        return self.spec.model_dump()


ParseOutcome = Union[ParseResult, ParseError]


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = f"""\
You convert a description of a cash-transfer or child-tax-credit policy into a \
JSON policy specification. You are a translator, not an analyst.

Return RAW JSON ONLY. No prose before or after. No markdown code fences. No \
explanation. The first character of your response must be {{ and the last }}.

SCHEMA — return exactly these keys, no others:

{{
  "policy_id": string,        // short snake_case slug, e.g. "ctc_3600"
  "label": string,            // human-readable, e.g. "Child Tax Credit, $3,600"
  "domain": string,           // MUST be "cash_transfer"
  "instrument": string,       // e.g. "child_tax_credit", "flat_transfer", "eitc_expansion"
  "levers": {{
    "credit_per_child_under_6": number,   // ANNUAL dollars per child. Default 0
    "credit_per_child_6_to_17": number,   // ANNUAL dollars per child. Default 0
    "fully_refundable": boolean,          // Default false
    "phaseout_start_single": number|null, // Dollars of income. Default null
    "phaseout_start_joint": number|null,  // Dollars of income. Default null
    "phaseout_rate": number,              // FRACTION, not percent. 5% is 0.05. Default 0
    "flat_transfer_per_adult": number     // ANNUAL dollars per adult. Default 0
  }},
  "parser_uncertainties": [string]
}}

RULES

1. NO-OP DEFAULTS. Anything the text does not state gets its default: 0 for \
numbers, false for booleans, null for thresholds. Never fill a lever with a \
value from a real-world policy the text merely resembles.

2. ANNUALISE. Levers are annual. "$300 a month per kid" is 3600.

3. PARSER_UNCERTAINTIES. List every field you inferred rather than read \
directly, and say what you assumed. Examples: "Text said 'per kid' without an \
age split; applied the same amount to both age bands." / "Refundability not \
stated; defaulted to false." If the text is vague, this list should be long. \
An empty list claims the text stated everything explicitly.

4. INVENT NOTHING QUANTITATIVE BEYOND THE LEVERS. Never output an approval \
rating, support percentage, poverty effect, cost estimate, or any other \
outcome. Those come from a simulation, not from you. There is no field for \
them; do not add one.

5. OUT OF SCOPE. If the policy is not a cash transfer or child tax credit \
(infrastructure, healthcare, education, tax rates on corporations, anything \
that is not money paid to households), return exactly:

{{"error": "out_of_scope", "reason": "<one sentence saying what it is instead>"}}

6. AMBIGUOUS AMOUNTS. Report the number the text gives. Do not clamp, round to \
a familiar policy value, or substitute a plausible one. If the text says a \
number that seems absurd, still report it — downstream validation handles that.
"""


def _strip_fences(text: str) -> str:
    """Defensive de-fencing.

    Rule 1 of the system prompt forbids fences; models emit them anyway, and a
    demo that dies on ```json is a demo that dies on stage.
    """
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    # Fall back to the outermost brace pair if there is still stray prose.
    if not t.startswith("{"):
        i, j = t.find("{"), t.rfind("}")
        if i != -1 and j > i:
            t = t[i : j + 1]
    return t.strip()


def parse_response(text: str) -> ParseOutcome:
    """Validate raw model text into a PolicySpec. Pure — no network.

    Split out from the API call so the five B2 test cases, and the app's own
    tests, can exercise every validation path without a key or a connection.
    """
    cleaned = _strip_fences(text)

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return ParseError(
            kind="invalid_json",
            message="The parser returned something that was not JSON.",
            detail=str(exc),
            raw=text[:800],
        )

    if not isinstance(payload, dict):
        return ParseError("invalid_json", "Parser returned JSON that was not an object.", raw=text[:800])

    # The model's own out-of-scope signal (rule 5).
    if "error" in payload:
        return ParseError(
            kind="out_of_scope" if payload.get("error") == "out_of_scope" else "schema",
            message="That does not look like a cash transfer or child tax credit.",
            detail=str(payload.get("reason", "")).strip(),
            raw=text[:800],
        )

    try:
        spec = PolicySpec.model_validate(payload)
    except ValidationError as exc:
        return _classify(exc, text)

    return ParseResult(spec=spec, raw=text)


def _classify(exc: ValidationError, raw: str) -> ParseError:
    """Turn a pydantic error into something a human on a stage can read."""
    problems, kind = [], "schema"

    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        msg = err["msg"].removeprefix("Value error, ")
        problems.append(f"{loc}: {msg}")

        if err["type"] == "extra_forbidden":
            kind = "schema"
        elif "plausibility ceiling" in msg or "negative" in msg or "implausible" in msg:
            kind = "implausible"
        elif loc == "domain":
            kind = "out_of_scope"

    headline = {
        "implausible": "That value is outside the range this simulator will model.",
        "out_of_scope": "That does not look like a cash transfer or child tax credit.",
        "schema": "The parser returned a specification this app cannot use.",
    }[kind]

    return ParseError(kind=kind, message=headline, detail="; ".join(problems), raw=raw[:800])


# ---------------------------------------------------------------------------
# The network call
# ---------------------------------------------------------------------------
def parse_policy(
    text: str,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT_S,
    client: Any = None,
) -> ParseOutcome:
    """Parse policy text via the Anthropic API.

    Returns a ParseError rather than raising on any failure, including a
    missing key, a network error and a timeout, so the caller has exactly one
    shape to handle.
    """
    if not (text or "").strip():
        return ParseError("schema", "Enter a policy description first.", "")

    if os.environ.get("POLICY_SIM_DEMO_MODE", "").lower() in ("1", "true", "yes"):
        return ParseError(
            kind="api",
            message="DEMO_MODE is on — the parser makes no network calls.",
            detail="Pick a precomputed scenario from the sidebar.",
        )

    import anthropic  # imported here so the module loads without the SDK present

    try:
        if client is None:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                return ParseError(
                    kind="api",
                    message="No ANTHROPIC_API_KEY is set, so the policy text cannot be parsed.",
                    detail="The precomputed scenarios in the sidebar do not need a key.",
                )
            client = anthropic.Anthropic(timeout=timeout, max_retries=1)

        response = client.messages.create(
            model=model,
            max_tokens=2000,
            # Schema translation is a simple task and this call sits on the
            # demo's critical path behind a 30s timeout, so it runs at low
            # effort. Thinking stays on (the default on Opus 5); thinking
            # blocks are filtered out below, only text blocks are parsed.
            output_config={"effort": "low"},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text.strip()}],
        )
        if response.stop_reason == "refusal":
            return ParseError(
                kind="api",
                message="The parser declined to process that text.",
                detail=getattr(response.stop_details, "explanation", "") or "",
            )
        raw = "".join(
            block.text for block in response.content
            if getattr(block, "type", "") == "text"
        )
    except anthropic.APITimeoutError:
        return ParseError(
            kind="timeout",
            message=f"The parser did not respond within {timeout:.0f}s.",
            detail="Pick a precomputed scenario from the sidebar instead.",
        )
    except anthropic.AuthenticationError:
        return ParseError("api", "The ANTHROPIC_API_KEY was rejected.",
                          "The precomputed scenarios do not need a key.")
    except anthropic.RateLimitError:
        return ParseError("api", "The parser is rate limited right now.",
                          "Pick a precomputed scenario from the sidebar.")
    except anthropic.APIConnectionError:
        return ParseError("api", "Could not reach the parser (network).",
                          "Pick a precomputed scenario from the sidebar.")
    except anthropic.APIStatusError as exc:
        return ParseError("api", f"The parser returned an error ({exc.status_code}).",
                          str(getattr(exc, "message", exc))[:300])
    except Exception as exc:  # noqa: BLE001 - nothing reaches the user as a traceback
        return ParseError("api", "The parser failed unexpectedly.",
                          f"{type(exc).__name__}: {exc}"[:300])

    outcome = parse_response(raw)
    if isinstance(outcome, ParseResult):
        outcome.model = model
    return outcome
