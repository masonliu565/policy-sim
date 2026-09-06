"""
Run the real microsimulation for a policy the user typed.

Until now the app could only read the five precomputed scenarios, so typing a
policy showed you what it had understood and then nothing happened. That is not
a product. This runs the actual engine -- the same code that produced the
precomputed files -- on the same 30,000 household sample, in about two and a
half seconds.

Still no network and still no API key: the engine is local numpy over a local
parquet file. "Works on its own" means exactly that.

The architecture rule is unchanged and worth restating, because this module is
the one place the app touches the model: the LLM never produces a number. It is
optional here, used only to read English into levers, and app/local_parser.py
does that offline anyway. Every figure on screen comes out of the simulation.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit as st

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
MODEL = REPO / "model"
if str(MODEL) not in sys.path:
    sys.path.insert(0, str(MODEL))

POPULATION = REPO / "data" / "processed" / "us_households.parquet"


def available() -> bool:
    """Can we run live at all? The sample must have been built."""
    return POPULATION.exists()


@st.cache_resource(show_spinner=False)
def _population():
    import engine as E
    return E.Population.load()


def _key(levers: Dict[str, Any], seeds: int):
    return (seeds,) + tuple(sorted((k, str(v)) for k, v in levers.items()))


@st.cache_data(show_spinner=False, max_entries=24)
def _run_cached(key, levers: Dict[str, Any], seeds: int) -> Dict[str, Any]:
    import engine as E
    import opinion as O

    spec = dict(levers)
    # The engine wants an actual number for "no phase-out"; the parser reports
    # None because the user did not say. Infinity is the honest encoding.
    for side in ("phaseout_start_single", "phaseout_start_joint"):
        if spec.get(side) is None:
            spec[side] = float("inf")

    pop = _population()
    res = E.run(spec, "typed_policy", "Your policy", n_seeds=seeds, pop=pop)
    O.attach(res, pop.df)

    # Match what precompute.py writes, so the rest of the app cannot tell a
    # live result from a precomputed one.
    out = dict(res)
    out.pop("_by_group_impact", None)
    clean = {}
    for k, v in (out.get("policy_spec") or {}).items():
        clean[k] = None if isinstance(v, float) and v == float("inf") else v
    out["policy_spec"] = clean
    out["_live"] = True
    return out


def run(levers: Dict[str, Any], seeds: int = 300) -> Dict[str, Any]:
    """Simulate a typed policy. Cached, so re-selecting a metro is instant."""
    return _run_cached(_key(levers, seeds), levers, seeds)


def why_unavailable() -> Optional[str]:
    if available():
        return None
    return ("The 30,000-household sample has not been built yet, so a typed "
            "policy cannot be simulated. Run `python model/build_population.py` "
            "once. The precomputed scenarios still work without it.")
