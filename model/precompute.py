"""
A7 -- precompute every scenario the demo reads.

The live path exists (engine.run + opinion.attach), but the scripted demo must
never depend on it. These files are what the app loads.

Run:  py model/precompute.py
Out:  scenarios/baseline.json, ctc_2021.json, ctc_1000.json, flat_500.json,
      eitc_match.json

Each file matches docs/output_contract.md exactly.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine as E  # noqa: E402
import opinion as O  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SCENARIOS = REPO / "scenarios"
N_SEEDS = 500

# ---------------------------------------------------------------------------
# NOTE ON eitc_match
# The build spec asks for "a 25% increase to existing refundable credits". The
# lever schema has no EITC parameter and the engine does not model the EITC, so
# this CANNOT be implemented literally. It is operationalised as a 25% uplift on
# the current-law refundable child credit: 25% of the $2,000 TCJA credit = $500
# per child, fully refundable, carrying the TCJA phaseout structure.
#
# That is an approximation of a different policy, not the policy named. It is
# labelled as such in the scenario's `label` and stated in its `warnings`, so
# nobody reads "EITC" off the screen and believes the EITC was modelled.
# ---------------------------------------------------------------------------

SCENARIOS_SPEC = [
    ("baseline", "No policy (baseline)", {}),

    ("ctc_2021", "Expanded Child Tax Credit (2021)", E.CTC_2021),

    ("ctc_1000", "$1,000 per child, fully refundable, no phaseout", {
        "credit_per_child_under_6": 1000.0,
        "credit_per_child_6_to_17": 1000.0,
        "fully_refundable": True,
        "phaseout_start_single": float("inf"),
        "phaseout_start_joint": float("inf"),
        "phaseout_rate": 0.0,
        "flat_transfer_per_adult": 0.0,
    }),

    ("flat_500", "$500 per adult, no child component", {
        "credit_per_child_under_6": 0.0,
        "credit_per_child_6_to_17": 0.0,
        "fully_refundable": True,
        "phaseout_start_single": float("inf"),
        "phaseout_start_joint": float("inf"),
        "phaseout_rate": 0.0,
        "flat_transfer_per_adult": 500.0,
    }),

    ("eitc_match", "25% uplift to the refundable child credit "
                   "(EITC not modelled -- see warnings)", {
        "credit_per_child_under_6": 500.0,
        "credit_per_child_6_to_17": 500.0,
        "fully_refundable": True,
        "phaseout_start_single": 200000.0,
        "phaseout_start_joint": 400000.0,
        "phaseout_rate": 0.05,
        "flat_transfer_per_adult": 0.0,
    }),
]

EXTRA_WARNINGS = {
    "eitc_match": [
        "The EITC is NOT modelled. This scenario approximates 'a 25% increase "
        "to existing refundable credits' as a $500 per child fully refundable "
        "uplift on the current-law CTC, carrying TCJA phaseout thresholds. It "
        "is an approximation of a different policy, not the EITC.",
    ],
    "baseline": [
        "Baseline scenario: no transfer of any kind. All impact medians equal "
        "their baselines by construction and annual cost is zero.",
    ],
}


def to_contract(result):
    """Drop internals and JSON-clean infinities in policy_spec."""
    spec = dict(result.get("policy_spec", {}))
    for k, v in spec.items():
        if isinstance(v, float) and v == float("inf"):
            spec[k] = None  # JSON has no infinity; None reads as "no phaseout"
    result["policy_spec"] = spec
    result.pop("_by_group_impact", None)
    return result


def main():
    pop = E.Population.load()
    SCENARIOS.mkdir(parents=True, exist_ok=True)
    print(f"Population: {pop.n:,} households, {N_SEEDS} seeds per scenario\n")
    print(f"{'scenario':<14}{'runtime':>9}  {'child pov':>22}  "
          f"{'cost':>14}  {'in_support':>11}  {'support':>16}")
    print("-" * 100)

    written = []
    for pid, label, spec in SCENARIOS_SPEC:
        t0 = time.perf_counter()
        res = E.run(spec, pid, label, n_seeds=N_SEEDS, pop=pop)
        O.attach(res, pop.df)
        res["warnings"] = EXTRA_WARNINGS.get(pid, []) + res["warnings"]
        res = to_contract(res)
        dt = time.perf_counter() - t0

        path = SCENARIOS / f"{pid}.json"
        path.write_text(json.dumps(res, indent=2, default=float), encoding="utf-8")
        written.append(path)

        cp = res["impact"]["child_poverty_rate"]
        cost = res["impact"]["annual_cost_usd"]["median"]
        sup = res["opinion"]["overall_support"]
        sup_s = "--" if sup is None else f"{sup['median']:.3f}"
        print(f"{pid:<14}{dt:>8.2f}s  "
              f"{100 * cp['median']:>7.2f}% [{100 * cp['p05']:>5.2f}, "
              f"{100 * cp['p95']:>5.2f}]  "
              f"${cost / 1e9:>12,.1f}B  {str(res['in_support']):>11}  "
              f"{sup_s:>16}")

    print("-" * 100)
    print(f"\nbaseline child poverty rate: "
          f"{100 * res['impact']['child_poverty_rate']['baseline']:.2f}% "
          f"(identical across scenarios by construction)")

    print(f"\n{len(written)} files written to scenarios/:")
    for p in sorted(SCENARIOS.glob("*.json")):
        print(f"  {p.name:<22}{p.stat().st_size / 1024:>8.1f} KB")

    # Contract self-check, so a schema drift fails here and not in the demo.
    print("\nContract check against docs/output_contract.md:")
    required_impact = ["child_poverty_rate", "overall_poverty_rate",
                       "median_disposable_income", "annual_cost_usd"]
    ok = True
    for p in written:
        d = json.loads(p.read_text(encoding="utf-8"))
        problems = []
        for k in ("policy_id", "label", "n_seeds", "impact", "opinion",
                  "by_metro", "warnings", "in_support", "nearest_policies"):
            if k not in d:
                problems.append(f"missing top-level '{k}'")
        for k in required_impact:
            if k not in d["impact"]:
                problems.append(f"missing impact.{k}")
                continue
            for b in ("baseline", "median", "p05", "p95"):
                if b not in d["impact"][k]:
                    problems.append(f"missing impact.{k}.{b}")
        if len(d["by_metro"]) != 6:
            problems.append(f"by_metro has {len(d['by_metro'])} entries, expected 6")
        for m in d["by_metro"]:
            for k in required_impact:
                for b in ("baseline", "median", "p05", "p95"):
                    if b not in m["impact"].get(k, {}):
                        problems.append(f"by_metro[{m['metro']}].impact.{k}.{b} missing")
        for g in d["opinion"]["by_group"]:
            if "evidence_ids" not in g or g["evidence_ids"] is None:
                problems.append(f"by_group[{g['group']}] evidence_ids missing/null")
            if "evidence_status" not in g:
                problems.append(f"by_group[{g['group']}] evidence_status missing")
            if "low_sample" not in g:
                problems.append(f"by_group[{g['group']}] low_sample missing")
        status = "OK" if not problems else "FAIL"
        ok = ok and not problems
        print(f"  {p.name:<22}{status}")
        for pr in problems:
            print(f"      - {pr}")
    print(f"\n{'ALL SCENARIOS MATCH THE CONTRACT' if ok else 'CONTRACT VIOLATIONS ABOVE'}")


if __name__ == "__main__":
    main()
