"""
Scenario loading.

The app reads /scenarios/*.json and nothing else. It never imports from
/model and never computes a policy number itself: every figure on screen is
read from a precomputed scenario file, so the demo path has no dependency on
the live model.

Validation here is deliberately loud. A scenario missing a p05/p95 is a
contract violation by whatever produced it, and the app must say so rather
than quietly rendering a bare point estimate.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent

def scenario_dir() -> Path:
    """Where scenario JSON lives.

    Resolved per call, not at import, so the env override still applies to a
    process that imported this module first (pytest, Streamlit reruns). Tests
    point it at a fixture dir rather than writing into /scenarios, which Track A
    owns and regenerates.
    """
    return Path(os.environ.get("POLICY_SIM_SCENARIO_DIR", REPO / "scenarios"))

# Every impact outcome must carry all four. `baseline` is what the dashed
# reference line is drawn at; without it there is nothing to compare against.
IMPACT_KEYS = ("baseline", "median", "p05", "p95")
# Opinion support has no baseline — there is no "support before the policy".
SUPPORT_KEYS = ("median", "p05", "p95")


class ScenarioError(ValueError):
    """A scenario file violates the JSON contract."""


# ---------------------------------------------------------------------------
# Metro key normalisation.
#
# Track A's crosswalk labels metros "New York", "San Francisco", ... while the
# map geometry keys them "new_york", "san_francisco". Neither side is wrong;
# the contract for by_metro was never pinned down. Rather than guess which one
# lands in the scenario JSON, both are folded to the same canonical form so
# whichever Track A emits resolves. If a key matches nothing, the marker
# renders as "no estimate" — it never falls back to another metro's numbers.
# ---------------------------------------------------------------------------
def metro_key(raw: str) -> str:
    """'New York' -> 'new_york'; 'new_york' -> 'new_york'."""
    return re.sub(r"[^a-z0-9]+", "_", str(raw).strip().lower()).strip("_")


def _require(obj: Dict[str, Any], keys, where: str) -> None:
    missing = [k for k in keys if k not in obj]
    if missing:
        raise ScenarioError(f"{where}: missing {', '.join(missing)}")
    for k in keys:
        if not isinstance(obj[k], (int, float)) or isinstance(obj[k], bool):
            raise ScenarioError(f"{where}: {k!r} is not numeric ({obj[k]!r})")


def validate(scenario: Dict[str, Any], where: str = "scenario") -> List[str]:
    """Check the contract in docs/output_contract.md. Returns non-fatal notes.

    Three states from that contract are legitimate and must not raise:
      - opinion.overall_support may be null (not enough evidence nationally)
      - a by_group entry's support may be null with an evidence_status
      - in_support may be false, in which case every opinion figure is null
        while impact stays fully valid
    """
    notes: List[str] = []

    for key in ("policy_id", "label", "impact", "opinion", "warnings"):
        if key not in scenario:
            raise ScenarioError(f"{where}: missing top-level key {key!r}")

    for name, band in (scenario["impact"] or {}).items():
        _require(band, IMPACT_KEYS, f"{where}: impact.{name}")
        if not (band["p05"] <= band["median"] <= band["p95"]):
            notes.append(
                f"impact.{name}: interval is not ordered "
                f"(p05={band['p05']}, median={band['median']}, p95={band['p95']})"
            )

    opinion = scenario.get("opinion") or {}
    overall = opinion.get("overall_support")
    if overall is not None:
        _require(overall, SUPPORT_KEYS, f"{where}: opinion.overall_support")

    for i, group in enumerate(opinion.get("by_group") or []):
        tag = f"{where}: opinion.by_group[{i}]"
        for key in ("group_type", "group"):
            if key not in group:
                raise ScenarioError(f"{tag}: missing {key!r}")
        support = group.get("support")
        if support is not None:
            _require(support, SUPPORT_KEYS, f"{tag}.support")
        elif group.get("evidence_status") in (None, "ok"):
            notes.append(
                f"opinion.by_group[{i}] ({group['group']}): support is null but "
                f"evidence_status is {group.get('evidence_status')!r}"
            )
        if group.get("evidence_ids") is None:
            notes.append(f"opinion.by_group[{i}] ({group['group']}): evidence_ids is null")

    for i, block in enumerate(scenario.get("by_metro") or []):
        tag = f"{where}: by_metro[{i}]"
        if "metro" not in block:
            raise ScenarioError(f"{tag}: missing 'metro'")
        for name, band in (block.get("impact") or {}).items():
            _require(band, IMPACT_KEYS, f"{tag} ({block['metro']}): impact.{name}")
            # A collapsed interval claims perfect certainty on a 2,000-household
            # subsample, which is not credible. Surface it rather than drawing
            # it as an ordinary confident estimate.
            if band["p05"] == band["p95"]:
                notes.append(
                    f"by_metro {block['metro']}: {name} has a zero-width interval "
                    f"(p05 == p95 == {band['p05']:.6g}) — shown as a point, not a band"
                )

    if scenario.get("in_support") is False and not scenario.get("nearest_policies"):
        notes.append("in_support is false but nearest_policies is empty — "
                     "there is nothing to offer in place of the refusal")

    return notes


def load_scenario(path: Path) -> Dict[str, Any]:
    """Load and validate one scenario file."""
    try:
        scenario = json.loads(Path(path).read_text())
    except json.JSONDecodeError as exc:
        raise ScenarioError(f"{Path(path).name}: not valid JSON — {exc}") from exc

    scenario["_notes"] = validate(scenario, where=Path(path).name)
    scenario["_path"] = str(path)

    # by_metro is a LIST in the contract, keyed by a display label ("New York").
    # Index it once here so the map can look a metro up by the key the geometry
    # uses ("new_york"), without discarding the list the contract specifies.
    scenario["by_metro_index"] = {
        metro_key(b["metro"]): b for b in (scenario.get("by_metro") or []) if b.get("metro")
    }

    return scenario


def list_scenarios(directory: Optional[Path] = None) -> List[Dict[str, str]]:
    """Every scenario file on disk, for the sidebar dropdown.

    A file that fails to load is still listed, carrying its error, so a broken
    scenario is visible in the UI instead of silently vanishing from the menu.
    """
    directory = Path(directory or scenario_dir())
    out: List[Dict[str, str]] = []
    for path in sorted(directory.glob("*.json")):
        entry = {"path": str(path), "filename": path.name, "error": ""}
        try:
            scenario = json.loads(path.read_text())
            # /scenarios also holds backtest.json, which is A6 output rather
            # than a policy scenario. Identify it by shape, not by filename.
            if "policy_id" not in scenario or "impact" not in scenario:
                continue
            entry["policy_id"] = scenario.get("policy_id", path.stem)
            entry["label"] = scenario.get("label", path.stem)
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI, never raised
            entry["policy_id"] = path.stem
            entry["label"] = f"{path.stem} (unreadable)"
            entry["error"] = str(exc)
        out.append(entry)
    return out
