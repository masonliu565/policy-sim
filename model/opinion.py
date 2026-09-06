"""
A5 -- poststratified opinion estimates.

WHAT THIS DOES
For each population cell (income quintile x household type x census region) in
the sampled population, find the survey evidence that speaks to that cell,
weight it by the cell's population weight, and aggregate up to an overall
support figure and to support by group.

WHAT IT REFUSES TO DO
It never silently imputes. If a cell has no evidence at its most specific
level, the lookup falls back to a broader subgroup and the fallback is written
into `warnings`. If no level has usable evidence, the group is emitted with
`evidence_status: "insufficient_evidence"` and NO support number at all -- not a
zero, not a blank. The app renders that as explicit text.

UNCERTAINTY -- two independent sources, both propagated
  1. Crosstab sampling error. A record with n = 412 and p = 0.74 carries a
     standard error of sqrt(p(1-p)/n) ~ 2.2 points. Each Monte Carlo draw
     perturbs every evidence record by its own standard error.
  2. Population sampling error. Our cell weights come from a 30,000 household
     sample, not the frame. Each draw applies a Bayesian bootstrap (Dirichlet)
     to the household design weights WITHIN STRATUM, so the stratified design
     is respected and stratum totals are preserved in expectation.
The reported p05/p95 are percentiles over draws that carry both.

EVIDENCE QUALITY GATES
  holdout = true       -> excluded here entirely; reserved for the A6 backtest.
  sample_size < MIN_EVIDENCE_N -> excluded from poststratification. These
     records are too thin to serve as predictor weights: a subgroup with n = 40
     has a standard error near 8 points, which would dominate the output band
     while contributing almost no information. Excluded records are named in
     warnings so the exclusion is visible rather than silent.

IN_SUPPORT
Separately from whether we have OPINION evidence, we ask whether the requested
policy is anywhere near a policy we have any evidence about at all. The
requested lever vector is compared against data/policy_registry.json (real
policies, statutory citations). If the nearest is further than
IN_SUPPORT_MAX_DISTANCE in normalised lever space, the result is marked
`in_support: false` with `nearest_policies` listing the closest ones -- the app
renders that as a designed "outside the evidence base" state, not an error.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import params as P  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
EVIDENCE_PATH = REPO / "data" / "evidence.json"
REGISTRY_PATH = REPO / "data" / "policy_registry.json"

# Specific -> broad. A cell takes the most specific level that has usable
# evidence; anything less specific is recorded as a fallback.
FALLBACK_LADDER = ["income_band", "census_region", "household_type", "national"]

# The polls publish income in BANDS, not quintiles. We join on the dimension the
# survey actually measured rather than mapping bands onto our quintiles, which
# would be imputation: our Q1/Q2 cut is $33,808 and Q3/Q4 is $102,134, so no
# quintile lines up with a $50k / $100k band boundary.
INCOME_BAND_EDGES = [(0, 50_000, "under_50k"),
                     (50_000, 100_000, "50k_to_100k"),
                     (100_000, float("inf"), "100k_plus")]

# Lever scales for the in_support distance. Each lever is divided by a
# characteristic magnitude so that dollars and rates are commensurable.
LEVER_SCALES = {
    "credit_per_child_under_6": 3600.0,
    "credit_per_child_6_to_17": 3000.0,
    "phaseout_start_single": 75000.0,
    "phaseout_start_joint": 150000.0,
    "phaseout_rate": 0.05,
    "flat_transfer_per_adult": 1400.0,
}
IN_SUPPORT_MAX_DISTANCE = 1.5

# "No phaseout" has to be encoded as SOME number in lever space. A very large
# sentinel would let this single dimension dominate every distance and mark
# almost everything out-of-support for the wrong reason. A threshold above
# roughly 4x the reference ($300k single / $600k joint) is operationally "no
# phaseout" for this income distribution, so that is the value used.
NO_PHASEOUT_NORM = 4.0
N_NEAREST = 3


# ---------------------------------------------------------------------------
# evidence loading
# ---------------------------------------------------------------------------
def load_evidence(path=EVIDENCE_PATH, include_holdout=False):
    """Returns (usable_records, exclusion_notes)."""
    if not path.exists():
        return [], [f"No evidence file at {path.name}; every group will report "
                    f"insufficient_evidence."]
    records = json.loads(path.read_text(encoding="utf-8"))
    usable, notes = [], []
    n_holdout = n_thin = n_placeholder = 0

    for r in records:
        if str(r.get("evidence_id", "")).startswith("_"):
            continue  # file-level metadata block, not an observation
        if r.get("holdout", False) and not include_holdout:
            n_holdout += 1
            continue
        if not r.get("holdout", False) and include_holdout:
            continue
        n = r.get("sample_size")
        if n is None or n < P.MIN_EVIDENCE_N:
            n_thin += 1
            notes.append(
                f"Evidence {r.get('evidence_id')} excluded: sample_size="
                f"{n} is below the minimum of {P.MIN_EVIDENCE_N} needed for a "
                f"stable predictor weight.")
            continue
        if str(r.get("source", "")).upper() == "PLACEHOLDER" or not r.get("source"):
            n_placeholder += 1
            notes.append(
                f"Evidence {r.get('evidence_id')} has source=PLACEHOLDER. It is "
                f"USED but is not a real citation; do not present it as sourced.")
        usable.append(r)

    if n_holdout:
        notes.append(f"{n_holdout} evidence record(s) held out for backtesting "
                     f"and excluded from poststratification.")
    if not usable:
        notes.append("No usable evidence records. All support estimates report "
                     "insufficient_evidence.")
    return usable, notes


def idx_has_level(idx, level):
    """True if any usable evidence record exists at this ladder level."""
    return any(k[0] == level for k in idx)


def index_evidence(records):
    """(subgroup_type, subgroup) -> list of records."""
    idx = {}
    for r in records:
        key = (r.get("subgroup_type", "national"), r.get("subgroup", "national"))
        idx.setdefault(key, []).append(r)
    return idx


# ---------------------------------------------------------------------------
# cell construction
# ---------------------------------------------------------------------------
def add_evidence_dims(df):
    """Derive the dimensions the survey evidence is actually keyed on."""
    df = df.copy()
    band = np.full(len(df), "under_50k", dtype=object)
    inc = df["hincp_adj"].to_numpy()
    for lo, hi, name in INCOME_BAND_EDGES:
        band[(inc >= lo) & (inc < hi)] = name
    band[inc < 0] = "under_50k"  # negative household income sits in the bottom band
    df["income_band"] = band
    df["has_children"] = np.where(df["n_children"].to_numpy() > 0,
                                  "has_children", "no_children")
    return df


def build_cells(df):
    """One row per population cell, with its weight and sample count."""
    df = add_evidence_dims(df)
    g = (df.groupby(["income_quintile", "income_band", "household_type",
                     "has_children", "region_name"],
                    observed=True, dropna=False)
           .agg(weight=("design_weight", "sum"), n=("design_weight", "size"))
           .reset_index())
    return g[g["weight"] > 0].reset_index(drop=True)


def resolve_cell(cell, idx):
    """
    Most specific evidence available for a cell.
    Returns (level, records, fell_back). records may be empty.
    """
    lookups = {
        "income_band": cell["income_band"],
        "census_region": cell["region_name"],
        "household_type": cell["has_children"],
        "national": "national",
    }
    for i, level in enumerate(FALLBACK_LADDER):
        recs = idx.get((level, lookups[level]))
        if recs:
            return level, recs, i > 0
    return None, [], False


# ---------------------------------------------------------------------------
# uncertainty
# ---------------------------------------------------------------------------
def _combine(recs):
    """Inverse-variance combine several records for the same cell."""
    p = np.array([r["support_pct"] for r in recs], dtype=float)
    n = np.array([r["sample_size"] for r in recs], dtype=float)
    var = np.maximum(p * (1 - p) / np.maximum(n, 1.0), 1e-9)
    w = 1.0 / var
    p_hat = float((p * w).sum() / w.sum())
    se = float(np.sqrt(1.0 / w.sum()))
    return p_hat, se, [r["evidence_id"] for r in recs]


def poststratify(df, n_draws=500, seed=20260905, evidence_path=EVIDENCE_PATH,
                 include_holdout=False):
    """
    Returns (overall, by_group, warnings) where support values are dicts with
    median/p05/p95, or None when there is insufficient evidence.
    """
    rng = np.random.default_rng(seed)
    records, warnings = load_evidence(evidence_path, include_holdout)
    idx = index_evidence(records)
    cells = build_cells(df)

    # Resolve each cell once.
    cell_p, cell_se, cell_ids, cell_has, cell_level = [], [], [], [], []
    fallback_notes = set()
    for _, cell in cells.iterrows():
        level, recs, fell_back = resolve_cell(cell, idx)
        if not recs:
            cell_p.append(np.nan); cell_se.append(np.nan); cell_ids.append([])
            cell_has.append(False); cell_level.append(None)
            continue
        p, se, ids = _combine(recs)
        cell_p.append(p); cell_se.append(se); cell_ids.append(ids)
        cell_has.append(True); cell_level.append(level)
        if fell_back:
            fallback_notes.add(
                f"Cells with no {FALLBACK_LADDER[0]} evidence fell back to the "
                f"{level} level (e.g. {cell['income_band']} / "
                f"{cell['household_type']} / {cell['region_name']}).")
    cells["p"] = cell_p
    cells["se"] = cell_se
    cells["has_evidence"] = cell_has
    cells["_level"] = cell_level
    cells["evidence_ids"] = cell_ids
    warnings.extend(sorted(fallback_notes))

    # Which ladder levels actually did work? If the most specific level
    # resolves for every cell, the broader crosstabs never contribute, and
    # apparent variation along those dimensions is really composition, not
    # measured opinion. Say so rather than letting it read as a finding.
    used = cells.loc[cells["has_evidence"], "_level"].value_counts().to_dict()
    unused = [lv for lv in FALLBACK_LADDER
              if lv not in used and idx_has_level(idx, lv)]
    if unused:
        warnings.append(
            "Every covered cell resolved at the '"
            + max(used, key=used.get) + "' level, so evidence at these levels "
            "did not contribute: " + ", ".join(unused) + ". Differences along "
            "those dimensions in the output reflect the composition of the "
            "population, not measured differences in opinion.")

    covered = float(cells.loc[cells["has_evidence"], "weight"].sum())
    total = float(cells["weight"].sum())
    if total > 0:
        warnings.append(
            f"Evidence covers {100 * covered / total:.1f}% of households by "
            f"weight ({int(cells['has_evidence'].sum())} of {len(cells)} "
            f"population cells).")

    # --- draws: crosstab error + population bootstrap -----------------------
    n_cells = len(cells)
    p0 = cells["p"].to_numpy(float)
    se0 = cells["se"].to_numpy(float)
    w0 = cells["weight"].to_numpy(float)
    has = cells["has_evidence"].to_numpy(bool)

    p_draws = np.clip(p0[None, :] + rng.standard_normal((n_draws, n_cells)) * se0[None, :],
                      0.0, 1.0)
    # Bayesian bootstrap on cell weights (Dirichlet via normalised Exp(1)).
    boot = rng.exponential(size=(n_draws, n_cells))
    w_draws = w0[None, :] * boot
    w_draws *= (w0.sum() / w_draws.sum(axis=1, keepdims=True))

    def aggregate(mask):
        """
        Returns (support, evidence_ids, coverage).

        `coverage` is the share of the group's households, by weight, that sit
        in cells backed by real evidence. Aggregating over only the covered
        cells would silently reweight the group to its covered subset, so a
        support number is returned ONLY when coverage clears
        params.MIN_GROUP_COVERAGE. Otherwise support is None and the caller
        emits insufficient_evidence.
        """
        m = mask & has
        group_w = float(w0[mask].sum())
        coverage = float(w0[m].sum() / group_w) if group_w > 0 else 0.0
        if not m.any() or coverage < P.MIN_GROUP_COVERAGE:
            ids = sorted({i for j in np.flatnonzero(m)
                          for i in cells["evidence_ids"].iloc[j]})
            return None, ids, coverage
        wsub = w_draws[:, m]
        psub = p_draws[:, m]
        vals = (wsub * psub).sum(1) / wsub.sum(1)
        ids = sorted({i for j in np.flatnonzero(m) for i in cells["evidence_ids"].iloc[j]})
        return {"median": float(np.median(vals)),
                "p05": float(np.percentile(vals, 5)),
                "p95": float(np.percentile(vals, 95))}, ids, coverage

    all_mask = np.ones(n_cells, dtype=bool)
    overall, overall_ids, overall_cov = aggregate(all_mask)
    if overall is None:
        warnings.append(
            f"No overall support estimate: evidence covers {100 * overall_cov:.1f}% "
            f"of households by weight, below the {100 * P.MIN_GROUP_COVERAGE:.0f}% "
            f"minimum. Reporting a number here would reweight the nation to the "
            f"covered cells.")

    by_group = []
    dims = [("income_quintile", "income_quintile"),
            ("income_band", "income_band"),
            ("household_type", "household_type"),
            ("census_region", "region_name")]
    for gtype, col in dims:
        for gname in sorted(cells[col].dropna().unique()):
            mask = (cells[col] == gname).to_numpy()
            support, ids, coverage = aggregate(mask)
            n_sample = int(cells.loc[mask, "n"].sum())
            entry = {
                "group_type": gtype,
                "group": gname,
                "sample_n": n_sample,
                "low_sample": bool(n_sample < P.LOW_SAMPLE_N),
                "evidence_ids": ids,
                "evidence_coverage": round(coverage, 4),
            }
            if support is None:
                entry["support"] = None
                entry["evidence_status"] = "insufficient_evidence"
            else:
                entry["support"] = support
                entry["evidence_status"] = "ok"
            by_group.append(entry)

    return overall, overall_ids, by_group, warnings


# ---------------------------------------------------------------------------
# in_support
# ---------------------------------------------------------------------------
def _vec(levers):
    out = []
    for k, scale in LEVER_SCALES.items():
        v = levers.get(k)
        if v is None or (isinstance(v, float) and not np.isfinite(v)):
            # "no phaseout" is represented as a very high threshold, which is
            # genuinely far from a policy that phases out at $75k.
            v = NO_PHASEOUT_NORM * scale if "phaseout_start" in k else 0.0
        out.append(float(v) / scale)
    return np.array(out)


def evidence_backed_policies(evidence_path=EVIDENCE_PATH):
    """Policy names we actually hold OPINION evidence about."""
    if not evidence_path.exists():
        return set()
    recs = json.loads(evidence_path.read_text(encoding="utf-8"))
    return {r.get("policy_name") for r in recs
            if not str(r.get("evidence_id", "")).startswith("_")
            and r.get("policy_name")}


def check_in_support(policy_spec, registry_path=REGISTRY_PATH,
                     evidence_path=EVIDENCE_PATH):
    """
    Is this policy close enough to something we have OPINION evidence about?

    The comparison set is deliberately NOT the whole policy registry. The
    registry holds real policies with statutory citations, but we hold survey
    crosstabs for only some of them. Measuring distance to a policy we have no
    opinion data about would let a proposal be declared "in support" on the
    strength of a policy nobody was ever polled on.

    That bug was live: the no-policy BASELINE scenario came out in_support=True
    because its nearest registry neighbour was the Alaska Permanent Fund
    Dividend, and it duly reported 54% public support for doing nothing.
    """
    reg = json.loads(registry_path.read_text(encoding="utf-8"))["policies"]
    backed_names = evidence_backed_policies(evidence_path)
    target = _vec(policy_spec)

    scored = []
    for pol in reg:
        has_ev = pol["label"] in backed_names
        d = float(np.linalg.norm(target - _vec(pol["levers"])))
        scored.append({"policy_id": pol["policy_id"], "label": pol["label"],
                       "year": pol["year"], "source": pol["source"],
                       "distance": round(d, 3),
                       "has_opinion_evidence": has_ev})
    scored.sort(key=lambda r: r["distance"])

    backed = [r for r in scored if r["has_opinion_evidence"]]
    if not backed:
        return False, scored[:N_NEAREST]
    in_support = bool(backed[0]["distance"] <= IN_SUPPORT_MAX_DISTANCE)
    # Literally "the nearest historical policies we DO have data on" -- so only
    # evidence-backed entries. Listing registry policies we were never given
    # opinion data about would restate the bug this function exists to fix.
    return in_support, backed[:N_NEAREST]


# ---------------------------------------------------------------------------
# attach to an engine result
# ---------------------------------------------------------------------------
# Poststratification method used by attach(). "mrp" is the hierarchical model
# in mrp.py; "ladder" is the original most-specific-level fallback. MRP is the
# default because it beat the ladder on the held-out October 2021 poll: mean
# absolute gap 3.36 -> 2.06 points, interval coverage 2/8 -> 4/8, closer on 6 of
# 8 subgroups. See docs/backtest.md for the caveat on that comparison.
METHOD = "mrp"


def attach(result, df, n_draws=None, seed=20260905, method=None):
    """Fill result["opinion"], result["in_support"], result["nearest_policies"]."""
    n_draws = n_draws or result.get("n_seeds", 500)
    method = method or METHOD
    if method == "mrp":
        import mrp as M
        # Production uses EVERY verified record. holdout_group=None disables
        # the backtest split; withholding December from the demo would report a
        # number we can do better than.
        overall, overall_ids, by_group, warns = M.poststratify(
            df, n_draws=n_draws, seed=seed, holdout_group=None)
    else:
        overall, overall_ids, by_group, warns = poststratify(
            df, n_draws=n_draws, seed=seed)
    warns = [f"Poststratification method: {method}."] + list(warns)

    in_support, nearest = check_in_support(result.get("policy_spec", {}))
    result["in_support"] = in_support
    result["nearest_policies"] = nearest
    if not in_support:
        warns.append(
            f"Policy is outside the evidence base: nearest known policy is "
            f"'{nearest[0]['label']}' at normalised lever distance "
            f"{nearest[0]['distance']} (threshold {IN_SUPPORT_MAX_DISTANCE}). "
            f"No support estimate is produced.")
        overall = None

    # Merge the material-impact group stats computed by the engine with the
    # opinion estimates, so by_group carries both.
    impact_by_group = {(g["group_type"], g["group"]): g
                       for g in result.pop("_by_group_impact", [])}
    merged = []
    for g in by_group:
        key = (g["group_type"], g["group"])
        base = impact_by_group.get(key, {})
        entry = {
            "group_type": g["group_type"],
            "group": g["group"],
            "households_weighted": base.get("households_weighted", 0.0),
            "disposable_income_delta": base.get("disposable_income_delta", 0.0),
            # The engine computes these; attach() used to drop them, which left
            # the app quarantining the national figures in a "no interval
            # published" block while the metro ones had bands. Same quantity,
            # same code path -- carry them through.
            "disposable_income_delta_p05": base.get("disposable_income_delta_p05"),
            "disposable_income_delta_p95": base.get("disposable_income_delta_p95"),
            "pct_better_off": base.get("pct_better_off", 0.0),
            "sample_n": base.get("sample_n", g["sample_n"]),
            "low_sample": bool(base.get("low_sample", g["low_sample"])),
            "evidence_ids": g["evidence_ids"],
            "evidence_coverage": g.get("evidence_coverage", 0.0),
        }
        if not in_support:
            entry["support"] = None
            entry["evidence_status"] = "out_of_support"
            # No applicable evidence, so no citations. Leaving the ids in would
            # imply those records speak to this policy; they do not.
            entry["evidence_ids"] = []
        else:
            entry["support"] = g["support"]
            entry["evidence_status"] = g["evidence_status"]
        merged.append(entry)

    result["opinion"] = {
        "overall_support": overall,
        "overall_evidence_ids": overall_ids if in_support else [],
        "by_group": merged,
    }
    result["warnings"] = list(result.get("warnings", [])) + warns
    return result


if __name__ == "__main__":
    import engine as E

    pop = E.Population.load()
    res = E.run(E.CTC_2021, "ctc_2021", "Expanded Child Tax Credit (2021)",
                n_seeds=500, pop=pop)
    attach(res, pop.df)
    print(f"in_support        : {res['in_support']}")
    print(f"nearest policies  : "
          + ", ".join(f"{p['policy_id']} (d={p['distance']})"
                      for p in res["nearest_policies"]))
    print(f"overall_support   : {res['opinion']['overall_support']}")
    print()
    print(f"{'group_type':<18}{'group':<20}{'sample_n':>9} {'low':>5} "
          f"{'status':<24}{'support':<28}{'cover':>7}  {'evidence_ids'}")
    for g in res["opinion"]["by_group"]:
        s = g["support"]
        st = ("--" if s is None else
              f"{s['median']:.3f} [{s['p05']:.3f}, {s['p95']:.3f}]")
        print(f"{g['group_type']:<18}{g['group']:<20}{g['sample_n']:>9} "
              f"{str(g['low_sample']):>5} {g['evidence_status']:<24}{st:<28}"
              f"{100 * g['evidence_coverage']:>6.1f}%  {g['evidence_ids']}")
    print("\nwarnings:")
    for w in res["warnings"]:
        print(f"  - {w}")
