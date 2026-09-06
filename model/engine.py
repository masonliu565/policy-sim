"""
A4 -- vectorized microsimulation of cash transfer and child tax credit policy.

DESIGN CONSTRAINT: seeds are an array dimension, not a loop. Every quantity is
computed on (n_seeds, n_households) arrays. There is no Python loop over
households anywhere in this file. 30,000 households x 500 seeds runs in a few
seconds; see `python model/engine.py --bench`.

POLICY SPEC (levers)
    credit_per_child_under_6      dollars per child aged 0-5
    credit_per_child_6_to_17      dollars per child aged 6-17
    fully_refundable              bool; if False the credit is capped at
                                  estimated tax liability
    phaseout_start_single         income where the credit begins to phase out,
                                  households with fewer than 2 adults
    phaseout_start_joint          same, households with 2+ adults
    phaseout_rate                 credit reduction per dollar of income above
                                  the threshold
    flat_transfer_per_adult       unconditional dollars per adult

PIPELINE (per seed, all vectorized)
    1. baseline disposable income from HINCP, inflation-adjusted (hincp_adj)
    2. gross credit from child counts
    3. phaseout, floored at zero, joint threshold when 2+ adults
    4. if not fully refundable, cap at estimated tax liability
    5. multiply by this seed's take-up rate
    6. add flat_transfer_per_adult * adults
    7. new disposable income = baseline + transfer - labor supply response
    8. poverty status against the official Census threshold for the household's
       size and child count, counting the transfer as income
    9. fiscal cost = design-weighted sum of transfers

UNCERTAINTY: take_up_rate, labor_supply_elasticity and
marginal_propensity_to_consume are drawn per seed by scipy.stats.qmc Latin
hypercube (not np.random). Central values and ranges live in params.py, each
with a source comment or an explicit TODO.

WEIGHTS -- three different ones, and using the wrong one is the classic bug:
    design_weight   households. Use for household counts, fiscal cost, and the
                    median-household-income statistic.
    person_weight   people. Use for the overall poverty RATE denominator.
    child_weight    children under 18. Use for the child poverty RATE.
All three are PWGTP/WGTP-derived and reproduce published ACS totals; see
build_population.py.

OUTPUT: matches scenarios/ctc_2021.json, plus three additive keys the app
renders as distinct states (see docs/output_contract.md):
    low_sample         group interval is real but thin -- render, mark visibly
    evidence_status    "insufficient_evidence" -> show text, not a zero
    in_support         False -> policy is outside the evidence base entirely
and `by_metro`, computed on metro subpopulations through the same code path as
the national numbers -- never approximated from national results.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import qmc

sys.path.insert(0, str(Path(__file__).resolve().parent))
import params as P  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PARQUET = REPO / "data" / "processed" / "us_households.parquet"

DEFAULT_SEED = 20260905
METROS = ["New York", "Houston", "Detroit", "San Francisco", "Phoenix", "Atlanta"]

EMPTY_POLICY = {
    "credit_per_child_under_6": 0.0,
    "credit_per_child_6_to_17": 0.0,
    "fully_refundable": True,
    "phaseout_start_single": float("inf"),
    "phaseout_start_joint": float("inf"),
    "phaseout_rate": 0.0,
    "flat_transfer_per_adult": 0.0,
}

# The 2021 expanded CTC. Statutory parameters, used by the backtest.
# SOURCE: American Rescue Plan Act of 2021, Sec. 9611 (amending IRC Sec. 24).
CTC_2021 = {
    "credit_per_child_under_6": 3600.0,
    "credit_per_child_6_to_17": 3000.0,
    "fully_refundable": True,
    "phaseout_start_single": 75000.0,
    "phaseout_start_joint": 150000.0,
    "phaseout_rate": 0.05,
    "flat_transfer_per_adult": 0.0,
}


# ---------------------------------------------------------------------------
# population
# ---------------------------------------------------------------------------
class Population:
    """Household arrays, prepared once and reused across scenario runs."""

    def __init__(self, df):
        self.df = df
        f = np.float32
        self.n = len(df)
        self.inc = df["hincp_adj"].to_numpy(f)
        self.earn = df["wage_income"].to_numpy(f)
        self.ku6 = df["n_child_under_6"].to_numpy(f)
        self.k617 = df["n_child_6_to_17"].to_numpy(f)
        self.kids = df["n_children"].to_numpy(f)
        self.adults = df["n_adults"].to_numpy(f)
        self.dw = df["design_weight"].to_numpy(np.float64)
        self.pw = df["person_weight"].to_numpy(np.float64)
        self.cw = df["child_weight"].to_numpy(np.float64)
        self.has_earn = (self.earn > 0).astype(f)

        # Official Census poverty threshold per household, by family size and
        # number of related children under 18. Sizes above 9 use the 9-person
        # row (the published table stops at "nine people or more").
        thr = P.load_poverty_thresholds()
        size = np.clip(df["NP"].to_numpy(int), 1, 9)
        nkid = np.clip(df["n_children"].to_numpy(int), 0, 8)
        nkid = np.minimum(nkid, size - 1)  # cannot have more children than members
        self.threshold = np.array([thr[(s, k)] for s, k in zip(size, nkid)], dtype=f)

        self.baseline_poor = self.inc < self.threshold

        # group masks
        self.masks = {}
        for q in ["Q1", "Q2", "Q3", "Q4", "Q5"]:
            self.masks[("income_quintile", q)] = (df["income_quintile"] == q).to_numpy()
        for t in sorted(df["household_type"].unique()):
            self.masks[("household_type", t)] = (df["household_type"] == t).to_numpy()
        for r in sorted(df["region_name"].dropna().unique()):
            self.masks[("census_region", r)] = (df["region_name"] == r).to_numpy()
        self.metro_masks = {m: (df["metro"] == m).to_numpy() for m in METROS
                            if (df["metro"] == m).any()}

    @classmethod
    def load(cls, path=PARQUET):
        if not path.exists():
            raise SystemExit(f"ERROR: {path} not found. Run model/build_population.py first.")
        return cls(pd.read_parquet(path))


# ---------------------------------------------------------------------------
# uncertainty draws
# ---------------------------------------------------------------------------
def draw_params(n_seeds, seed=DEFAULT_SEED):
    """Latin hypercube over the three uncertain parameters. Shape (n_seeds,)."""
    names = list(P.UNCERTAIN_PARAMS)
    sampler = qmc.LatinHypercube(d=len(names), seed=seed)
    u = sampler.random(n_seeds)
    out = {}
    for j, name in enumerate(names):
        _central, lo, hi = P.UNCERTAIN_PARAMS[name]
        out[name] = (lo + u[:, j] * (hi - lo)).astype(np.float32)
    return out


# ---------------------------------------------------------------------------
# core simulation -- no Python loop over households
# ---------------------------------------------------------------------------
def simulate(pop, policy, draws):
    """Returns (transfer, new_income) each shaped (n_seeds, n_households)."""
    spec = {**EMPTY_POLICY, **policy}
    f = np.float32

    # 2. gross credit
    gross = (f(spec["credit_per_child_under_6"]) * pop.ku6
             + f(spec["credit_per_child_6_to_17"]) * pop.k617)

    # 3. phaseout -- joint threshold when the household has 2+ adults
    start = np.where(pop.adults >= 2,
                     np.float32(spec["phaseout_start_joint"]),
                     np.float32(spec["phaseout_start_single"])).astype(f)
    excess = np.maximum(np.float32(0.0), pop.inc - start)
    excess = np.nan_to_num(excess, nan=0.0, posinf=0.0)
    credit = np.maximum(np.float32(0.0), gross - f(spec["phaseout_rate"]) * excess)

    # 4. non-refundable credits are capped at estimated tax liability
    if not spec["fully_refundable"]:
        deduction = np.where(pop.adults >= 2,
                             np.float32(P.STANDARD_DEDUCTION_JOINT),
                             np.float32(P.STANDARD_DEDUCTION_SINGLE)).astype(f)
        liability = f(P.EFFECTIVE_TAX_RATE) * np.maximum(np.float32(0.0),
                                                         pop.inc - deduction)
        credit = np.minimum(credit, liability)

    # 5-6. take-up applies to the credit; the flat transfer is unconditional
    take_up = draws["take_up_rate"][:, None]
    flat = f(spec["flat_transfer_per_adult"]) * pop.adults
    transfer = take_up * credit[None, :] + flat[None, :]

    # 7. extensive-margin labor supply response, earners only
    lse = draws["labor_supply_elasticity"][:, None]
    earnings_loss = lse * transfer * pop.has_earn[None, :]
    new_income = pop.inc[None, :] + transfer - earnings_loss

    return transfer, new_income


def _weighted_median_rows(values, weights):
    """
    Weighted median of each row of `values`.

    values (S, H). weights is either (H,) -- the same design weights for every
    seed -- or (S, H), one bootstrap-perturbed weight vector per seed, so the
    median carries sampling uncertainty like the other three outcomes. Without
    the 2-D path the median was the only outcome with no sampling error in its
    band, which showed up on screen as a zero-width interval next to three
    banded ones.
    """
    order = np.argsort(values, axis=1)
    v = np.take_along_axis(values, order, axis=1)
    w = (np.take_along_axis(weights, order, axis=1)
         if weights.ndim == 2 else weights[order])
    cw = np.cumsum(w, axis=1)
    half = 0.5 * cw[:, -1][:, None]
    idx = np.argmax(cw >= half, axis=1)
    return v[np.arange(values.shape[0]), idx]


def _band(arr, baseline):
    a = np.asarray(arr, dtype=float)
    return {"baseline": float(baseline),
            "median": float(np.median(a)),
            "p05": float(np.percentile(a, 5)),
            "p95": float(np.percentile(a, 95))}


def bootstrap_weights(n_seeds, n_households, seed=DEFAULT_SEED):
    """
    Bayesian bootstrap multipliers, shape (n_seeds, n_households).

    WHY THIS EXISTS. Without it the p05/p95 bands carry PARAMETER uncertainty
    only -- how much the answer moves when take-up or the elasticity moves --
    and none of the SAMPLING uncertainty from estimating a rate on a finite
    sample. Nationally that omission is invisible. On a metro it is not: San
    Francisco has 2,000 sampled households of which only 31 have children in
    poverty, so the child poverty rate took just TWO distinct values across 500
    seeds and the interval collapsed to zero width. A zero-width band claims
    certainty we do not have, and the app correctly flagged it on screen.

    One multiplier matrix is drawn per run and shared across the national and
    metro calls, so a household that is up-weighted in the national figure is
    up-weighted in its metro figure too. Drawing them independently would let
    the two disagree.
    """
    rng = np.random.default_rng(seed + 777)
    b = rng.exponential(size=(n_seeds, n_households)).astype(np.float32)
    return b / b.mean(axis=1, keepdims=True)


def outcomes_for(pop, transfer, new_income, mask=None, boot=None):
    """
    The four contract outcomes for a subpopulation.

    THE SAME FUNCTION produces the national numbers and every metro's numbers.
    Metro results are never derived from national results.

    `boot` adds population sampling uncertainty on top of parameter
    uncertainty. It is passed by run(); the pre-registered backtest deliberately
    does NOT use it, because widening an interval after seeing it miss is
    exactly what docs/backtest.md forbids.
    """
    if mask is None:
        mask = np.ones(pop.n, dtype=bool)
    t = transfer[:, mask]
    y = new_income[:, mask]
    inc = pop.inc[mask]
    thr = pop.threshold[mask]
    dw, pw, cw = pop.dw[mask], pop.pw[mask], pop.cw[mask]

    poor = y < thr[None, :]
    base_poor = inc < thr

    # Per-seed weights. With `boot` these carry sampling uncertainty as well as
    # parameter uncertainty; without it they are the fixed design weights.
    bs = boot[:, mask] if boot is not None else None
    dw_s = dw[None, :] * bs if bs is not None else np.broadcast_to(
        dw[None, :], y.shape)
    pw_s = pw[None, :] * bs if bs is not None else np.broadcast_to(
        pw[None, :], y.shape)
    cw_s = cw[None, :] * bs if bs is not None else np.broadcast_to(
        cw[None, :], y.shape)

    cw_tot = cw_s.sum(1)
    if cw.sum() > 0:
        child_rate = (poor * cw_s).sum(1) / np.where(cw_tot > 0, cw_tot, 1.0)
        child_base = float((base_poor * cw).sum() / cw.sum())
    else:
        child_rate = np.zeros(y.shape[0])
        child_base = 0.0
    all_rate = (poor * pw_s).sum(1) / pw_s.sum(1)
    all_base = float((base_poor * pw).sum() / pw.sum())

    med = _weighted_median_rows(y, dw_s if bs is not None else dw)
    med_base = float(_weighted_median_rows(inc[None, :].astype(np.float32), dw)[0])

    cost = (t * dw_s).sum(1)

    return {
        "child_poverty_rate": _band(child_rate, child_base),
        "overall_poverty_rate": _band(all_rate, all_base),
        "median_disposable_income": _band(med, med_base),
        "annual_cost_usd": _band(cost, 0.0),
    }


def group_stats(pop, transfer, new_income, mask):
    """Per-group distributional summary used by by_group and by_metro rankings."""
    dw = pop.dw[mask]
    if mask.sum() == 0 or dw.sum() == 0:
        return None
    delta = (new_income[:, mask] - pop.inc[mask][None, :])
    weighted_delta = (delta * dw[None, :]).sum(1) / dw.sum()
    better = ((transfer[:, mask] > 0) * dw[None, :]).sum(1) / dw.sum()
    return {
        "households_weighted": float(dw.sum()),
        "sample_n": int(mask.sum()),
        "disposable_income_delta": float(np.median(weighted_delta)),
        "disposable_income_delta_p05": float(np.percentile(weighted_delta, 5)),
        "disposable_income_delta_p95": float(np.percentile(weighted_delta, 95)),
        "pct_better_off": float(np.median(better)),
        "low_sample": bool(mask.sum() < P.LOW_SAMPLE_N),
    }


def run(policy_spec, policy_id, label, n_seeds=500, seed=DEFAULT_SEED, pop=None):
    pop = pop if pop is not None else Population.load()
    draws = draw_params(n_seeds, seed)
    transfer, new_income = simulate(pop, policy_spec, draws)
    boot = bootstrap_weights(n_seeds, pop.n, seed)

    result = {
        "policy_id": policy_id,
        "label": label,
        "n_seeds": int(n_seeds),
        "policy_spec": {**EMPTY_POLICY, **policy_spec},
        "impact": outcomes_for(pop, transfer, new_income, boot=boot),
        "by_metro": [],
        "opinion": {"overall_support": None, "by_group": []},
        "warnings": [],
    }

    # ---- national by_group -------------------------------------------------
    by_group = []
    for (gtype, gname), mask in pop.masks.items():
        st = group_stats(pop, transfer, new_income, mask)
        if st is None:
            continue
        by_group.append({"group_type": gtype, "group": gname, **st})
    result["_by_group_impact"] = by_group

    # ---- by_metro ----------------------------------------------------------
    for metro, mmask in pop.metro_masks.items():
        entry = {
            "metro": metro,
            "households_weighted": float(pop.dw[mmask].sum()),
            "sample_n": int(mmask.sum()),
            "low_sample": bool(mmask.sum() < P.LOW_SAMPLE_N),
            "impact": outcomes_for(pop, transfer, new_income, mmask, boot=boot),
            "top_subgroups": [],
        }
        ranked = []
        for (gtype, gname), gmask in pop.masks.items():
            if gtype == "census_region":
                continue  # a metro sits inside one region; not informative
            sub = mmask & gmask
            st = group_stats(pop, transfer, new_income, sub)
            if st is None or st["households_weighted"] == 0:
                continue
            ranked.append({"group_type": gtype, "group": gname, **st})
        ranked.sort(key=lambda r: r["disposable_income_delta"], reverse=True)
        entry["top_subgroups"] = ranked[:3]
        result["by_metro"].append(entry)
    result["by_metro"].sort(key=lambda e: e["households_weighted"], reverse=True)

    # ---- consumption response (MPC is reported, never used for poverty) ----
    mpc = draws["marginal_propensity_to_consume"][:, None]
    induced = ((transfer * mpc) * pop.dw[None, :]).sum(1)
    result["induced_consumption_usd"] = _band(induced, 0.0)

    # ---- warnings ----------------------------------------------------------
    result["warnings"] = [
        "Support estimates poststratify national survey crosstabs onto population "
        "cells; no fitted hierarchical model.",
        "No cost-of-living adjustment across metros; federal poverty thresholds "
        "applied uniformly.",
        "Unsourced parameters (marked TODO in model/params.py): "
        + ", ".join(P.UNSOURCED) + ".",
        "Poverty thresholds for 1- and 2-person households use the "
        "'householder under 65' variant; the sample does not carry householder "
        "age. Does not affect child poverty.",
        "Metro results come from a 2,000-household oversample per metro. "
        "Intervals are roughly 3.9x wider than the national ones.",
        "Non-refundable credits are capped using a flat effective tax rate, not "
        "a tax calculator.",
    ]
    return result


# ---------------------------------------------------------------------------
# bench entry point
# ---------------------------------------------------------------------------
def bench(n_seeds=500):
    pop = Population.load()
    t0 = time.perf_counter()
    draws = draw_params(n_seeds)
    transfer, new_income = simulate(pop, CTC_2021, draws)
    out = outcomes_for(pop, transfer, new_income)
    t1 = time.perf_counter()
    ok = "PASS" if (t1 - t0) < 15 else "FAIL"
    print(f"{pop.n:,} households x {n_seeds} seeds -> {t1 - t0:.2f}s "
          f"(budget 15s)  [{ok}]")
    print(json.dumps(out, indent=2))
    return t1 - t0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", action="store_true")
    ap.add_argument("--seeds", type=int, default=500)
    a = ap.parse_args()
    if a.bench:
        bench(a.seeds)
