"""
Free-form question interpretation with Claude. Optional.

The atlas front end has a `freeformAvailable` flag: when the backend can
interpret arbitrary English, the chat lets you type anything; when it cannot,
it steers you to the example questions. Juniper's own backend fills that role
with OpenAI. This one uses Claude, because that is the key we have.

THE RULE IS UNCHANGED, AND IT IS THE WHOLE POINT: Claude turns English into a
REQUEST. It never returns a number, a rate, a count or an interval. The server
then answers that request from the cached evidence. If Claude hallucinated a
statistic it would have nowhere to put it -- the schema has no field for one,
and the server ignores anything outside the schema.

Set the key in the environment or in a .env file at the repository root:

    ANTHROPIC_API_KEY=sk-ant-...

Without it, everything still works; only free-form phrasing is unavailable and
the four example questions are matched by app-local patterns instead.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

REPO = Path(__file__).resolve().parent.parent

MODEL = os.environ.get("POLICY_SIM_MODEL", "claude-sonnet-5")

SYSTEM = """You translate a question into a neutral, factual data request about \
Washington DC. You do NOT answer it and you do NOT calculate, estimate, recall \
or invent any number. Return only the request.

Return JSON matching this schema exactly:
{"kind": one of "household_income" | "service_requests" | "health_prevalence" \
| "health_affordability" | "policy_simulation" | "unsupported",
 "jurisdiction": "DC" | "other" | "unspecified",
 "geography": {"kind": "district"|"puma"|"ward"|"tract"|"other", "code": string or null},
 "year": integer or null,
 "incomeThreshold": number or null,
 "incomeComparison": "lt" | "lte" | null,
 "measure": string or null,
 "service": string or null,
 "outcome": string,
 "population": string,
 "timeframe": string,
 "unsupportedConstraints": [string],
 "clarification": string or null}

Rules:
- EVERY constraint you cannot represent in the fields above MUST appear in
  unsupportedConstraints. Never silently drop an age limit, a household type, a
  date range, a ward, an exclusion, or a request for a causal, behavioural or
  future prediction. Dropping one is the worst thing you can do here.
- These capabilities are historical data plus one cash-transfer simulation.
  They do not rank policies, predict elections, or forecast individual
  behaviour. For anything like that use kind "unsupported" and keep the
  requested outcome.
- A missing year stays null. "now" and "current" are not historical years.
- Use these exact population strings for supported kinds:
  household_income   -> "Occupied DC households"
  service_requests   -> "Service requests, not unique residents"
  health_prevalence  -> "CDC measure-specific population"
  health_affordability -> "Noninstitutionalized DC adults aged 18 or older"
  policy_simulation  -> "Occupied DC households"
- household_income covers all occupied households in DC or one PUMA. Any other
  population restriction is unsupported.
- service_requests counts recorded requests, not people and not completion time.
- policy_simulation is for a described cash transfer or child credit, for
  example "$300 a month per child, phased out over $150k". Keep the full
  description in outcome so the server can parse the amounts itself. Do not
  compute its effect.
- Ward codes are the digit alone, "7". Tract codes are the 11-digit GEOID.
- Record any ambiguity in clarification.

Return the JSON object only."""


def api_key() -> Optional[str]:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*ANTHROPIC_API_KEY\s*=\s*(.+?)\s*$", line)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    return None


def available() -> bool:
    if not api_key():
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except Exception:      # noqa: BLE001
        return False


ALLOWED = {"household_income", "service_requests", "health_prevalence",
           "health_affordability", "policy_simulation", "unsupported"}


def interpret(question: str, timeout: float = 20.0) -> Optional[Dict[str, Any]]:
    """
    Returns a specification dict, or None if unavailable or unusable.

    Anything the model returns is checked against the schema here. The server
    never trusts it: a stray field is dropped and a bad kind becomes
    "unsupported" rather than being passed through.
    """
    key = api_key()
    if not key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key, timeout=timeout, max_retries=0)
        msg = client.messages.create(
            model=MODEL, max_tokens=900, system=SYSTEM,
            messages=[{"role": "user", "content": question}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    except Exception:      # noqa: BLE001
        return None

    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        raw = json.loads(m.group(0))
    except Exception:      # noqa: BLE001
        return None

    from dc_api.interpret import base
    spec = base()
    kind = raw.get("kind")
    spec["kind"] = kind if kind in ALLOWED else "unsupported"
    geo = raw.get("geography") or {}
    if isinstance(geo, dict) and geo.get("kind") in (
            "district", "puma", "ward", "tract", "other"):
        code = geo.get("code")
        spec["geography"] = {"kind": geo["kind"],
                             "code": str(code) if code is not None else None}
    for f, typ in (("year", int), ("incomeThreshold", float)):
        v = raw.get(f)
        if isinstance(v, (int, float)):
            spec[f] = typ(v)
    if raw.get("incomeComparison") in ("lt", "lte"):
        spec["incomeComparison"] = raw["incomeComparison"]
    for f in ("measure", "service", "clarification"):
        v = raw.get(f)
        if isinstance(v, str) and v.strip():
            spec[f] = v.strip()[:500]
    for f in ("outcome", "population", "timeframe"):
        v = raw.get(f)
        if isinstance(v, str) and v.strip():
            spec[f] = v.strip()[:500]
    uc = raw.get("unsupportedConstraints")
    if isinstance(uc, list):
        spec["unsupportedConstraints"] = [str(x)[:500] for x in uc][:20]
    spec["_interpreter"] = {"type": "anthropic", "model": MODEL}
    return spec
