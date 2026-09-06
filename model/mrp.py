"""
Multilevel regression with poststratification (MRP).

WHY THIS REPLACES THE FALLBACK LADDER
The ladder in opinion.py resolves each population cell at the most specific
evidence level available and stops. That has three costs, all of which showed up
in the A6 backtest:

  1. It DISCARDS thin crosstabs. MIN_EVIDENCE_N = 300 threw away the n=252
     Northeast record and the n=287 $100k+ record entirely. Those records are
     not worthless, only noisy. Partial pooling uses them at a weight set by
     their own precision.
  2. Region evidence NEVER entered the model, because every cell resolved at
     the income_band level first. Regional variation in the output was purely
     population composition. MRP uses every dimension simultaneously.
  3. It cannot separate a HOUSE EFFECT from a real subgroup difference. YouGov
     asks approve/disapprove and reads 0.51 nationally; Morning Consult asks
     support/oppose with a "$300 a month" framing and reads 0.54. That 3-point
     gap is a property of the instrument, not of the public. Treating both as
     draws from one population inflates apparent subgroup variance. Here it is
     an explicit parameter.

THE MODEL
Observations are aggregate crosstab cells, not individual respondents, so this
is a hierarchical model on published subgroup proportions. Each record j is
transformed to the logit scale, where a binomial proportion is approximately
normal with a known variance (delta method):

    z_j = logit(p_j)              v_j = 1 / (n_j * p_j * (1 - p_j))

    z_j = mu + house[h_j] + u[d_j, l_j] + eps_j,     eps_j ~ N(0, v_j)

    house[h]  ~ N(0, sigma_house^2)      instrument effect
    u[d, l]   ~ N(0, sigma_d^2)          effect of level l of dimension d
    sigma_*   ~ half-t(3, A)             Gelman's folded-t, via scale mixture

Because the observation variances are KNOWN, every conditional is conjugate and
the posterior is sampled by plain Gibbs -- no Stan, no PyMC, no tuning. This is
the classic normal hierarchical model ("eight schools") generalised to several
crossed dimensions.

PREDICTION
    logit(p_cell) = mu + u[income_band, i] + u[census_region, r]

The house effect is set to ZERO when predicting, not to either pollster's value:
we want the average instrument, not YouGov's public or Morning Consult's public.

Cell probabilities are then poststratified onto the ACS population using
household design weights, with a Bayesian bootstrap on those weights so that
population sampling error is carried alongside model uncertainty.

WHAT THIS IS NOT
No LLM generates any number here. Every input is an observed, verified crosstab.
Cells with no direct evidence on a dimension are not invented: that dimension is
excluded from the model, and groups defined on it continue to report
insufficient_evidence. MRP interpolates between observed levels of observed
dimensions; it does not conjure dimensions we never measured.

KNOWN LIMITATIONS, stated rather than buried
  - ECOLOGICAL APPROXIMATION. Each published crosstab is a marginal over the
    other dimensions. We model effects as additive on the logit scale, which is
    the standard assumption but is an assumption: an interaction between income
    and region would not be recovered from marginals alone.
  - DESIGN EFFECT = 1. Published polls are weighted, so their effective sample
    size is below nominal. No pollster here publishes a design effect, and
    inventing one is not allowed, so v_j uses nominal n. Intervals are therefore
    somewhat optimistic. See DESIGN_EFFECT below to change this deliberately.
  - NO TIME TREND. Every usable training record was fielded in July 2021, so a
    time effect is not identified. This is exactly why backtest 2 over-predicts
    the October poll, and MRP does not fix it.
"""

import json
import sys
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import params as P  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
EVIDENCE_PATH = REPO / "data" / "evidence.json"

# Dimensions the model may use, mapped to the population column that carries
# them. A dimension is only included if the evidence actually observes it.
DIMENSION_COLUMNS = {
    "income_band": "income_band",
    "census_region": "region_name",
    "household_type": "has_children",
}

# Nominal n is used for the observation variance. Raise this above 1.0 to
# deliberately discount published Ns for survey design effects; it is left at
# 1.0 because no source here publishes a design effect and a made-up one would
# be a fabricated number in a model whose whole claim is that it has none.
DESIGN_EFFECT = 1.0

# half-t(3, A) scale for the group-level standard deviations, on the logit
# scale. A = 0.5 allows subgroup effects of roughly +/- 12 points before the
# prior starts to pull back -- weakly informative, not restrictive.
PRIOR_SCALE_GROUP = 0.5
PRIOR_SCALE_HOUSE = 0.5

N_BURN = 500
N_THIN = 2


def logit(p):
    return np.log(p / (1.0 - p))


def inv_logit(x):
    return 1.0 / (1.0 + np.exp(-x))


# ---------------------------------------------------------------------------
# data assembly
# ---------------------------------------------------------------------------
def load_observations(evidence_path=EVIDENCE_PATH, include_holdout=False):
    """
    Usable observations for the model.

    NOTE the deliberate difference from opinion.load_evidence: there is no
    MIN_EVIDENCE_N gate here. Using thin crosstabs at their correct weight is
    the entire point of partial pooling. A record is dropped only when its
    variance cannot be computed at all -- no sample size, or a degenerate
    proportion.
    """
    recs = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    obs, notes = [], []
    for r in recs:
        if str(r.get("evidence_id", "")).startswith("_"):
            continue
        if bool(r.get("holdout", False)) != bool(include_holdout):
            continue
        n = r.get("sample_size")
        p = r.get("support_pct")
        if n is None:
            notes.append(f"{r['evidence_id']}: no sample_size published, so no "
                         f"observation variance can be computed. Dropped "
                         f"(NOT imputed).")
            continue
        if p is None or not (0.0 < p < 1.0):
            notes.append(f"{r['evidence_id']}: degenerate proportion {p}. Dropped.")
            continue
        dim = r.get("subgroup_type")
        if dim not in DIMENSION_COLUMNS and dim != "national":
            notes.append(f"{r['evidence_id']}: dimension '{dim}' is not in the "
                         f"population, dropped.")
            continue
        obs.append({
            "evidence_id": r["evidence_id"],
            "dimension": dim,
            "level": r.get("subgroup"),
            "p": float(p),
            "n": float(n) / DESIGN_EFFECT,
            "house": _house_of(r),
        })
    return obs, notes


def _house_of(record):
    """Group records by survey instrument, since wording drives a real offset."""
    src = (record.get("source") or "").lower()
    if "yougov" in src or "economist" in src:
        return "yougov_economist"
    if "morning consult" in src:
        return "morning_consult"
    if "data for progress" in src:
        return "data_for_progress"
    return "other"


# ---------------------------------------------------------------------------
# Gibbs sampler
# ---------------------------------------------------------------------------
# Grid for the marginal posterior of each group-level SD. Sampling sigma from
# its MARGINAL (with the level effects integrated out) instead of from
# p(sigma | u) is what keeps this sampler out of the funnel; see the note in
# fit() below.
SIGMA_GRID = np.exp(np.linspace(np.log(1e-3), np.log(1.5), 240))


def _half_t_logpdf(sigma, scale, nu=3.0):
    """Unnormalised log density of a half-t(nu, scale) prior on sigma."""
    return -0.5 * (nu + 1.0) * np.log1p((sigma / scale) ** 2 / nu)


def _level_stats(r, prec, level_of_obs, n_levels):
    """Per-level sufficient statistics a = sum(1/v), b = sum(r/v), c = sum(r^2/v)."""
    a = np.zeros(n_levels)
    b = np.zeros(n_levels)
    for i in range(n_levels):
        m = level_of_obs == i
        if m.any():
            a[i] = prec[m].sum()
            b[i] = (r[m] * prec[m]).sum()
    return a, b


def _sample_sigma_marginal(a, b, prior_scale, rng):
    """
    Draw sigma from its marginal posterior with the level effects u integrated
    out. For one level with observations r ~ N(u, diag(v)) and u ~ N(0, s^2),
    the marginal is r ~ N(0, diag(v) + s^2 * J). Sherman-Morrison gives

        r' Sigma^-1 r = c - s^2 b^2 / (1 + s^2 a)
        log|Sigma|    = log|D| + log(1 + s^2 a)

    with a = sum(1/v), b = sum(r/v). Terms not depending on s drop out. The
    result is evaluated on SIGMA_GRID and sampled directly.

    WHY THIS AND NOT THE CONDITIONAL: sampling sigma from p(sigma | u) creates
    the classic centred-parameterisation funnel. If sigma drifts small, the
    conditional for u is prior-dominated and pins every u at zero; sigma then
    stays small because the likelihood only reaches it through sum(u^2). The
    chain gets stuck at sigma ~ 0 and every subgroup collapses onto the grand
    mean. That is exactly what this model did before the fix -- between-level
    SDs of 0.0002 logits and a house effect of exactly zero, despite raw
    observations spanning 0.45 to 0.57.
    """
    s2 = SIGMA_GRID ** 2
    denom = 1.0 + s2[:, None] * a[None, :]
    quad = (s2[:, None] * b[None, :] ** 2) / denom
    logp = (-0.5 * np.log(denom).sum(axis=1) + 0.5 * quad.sum(axis=1)
            + _half_t_logpdf(SIGMA_GRID, prior_scale))
    logp -= logp.max()
    w = np.exp(logp)
    w /= w.sum()
    return float(rng.choice(SIGMA_GRID, p=w))


def fit(obs, n_draws=500, seed=20260905):
    """
    Collapsed Gibbs sampler.

    Parameterisation choices, both deliberate:

    * HOUSE EFFECTS ARE FIXED, not random. There are only two or three survey
      houses, each with many observations, so a variance component over them is
      barely identified and contributes its own funnel. Instead each house gets
      its own intercept alpha_h. Prediction uses the MEAN of the alphas -- the
      average instrument, not any one pollster's. This also removes the
      mu/house confounding entirely: there is no separate global intercept.

    * LEVEL EFFECTS ARE COLLAPSED. sigma_d is drawn from its marginal posterior
      with u integrated out, then u is drawn from its exact conditional. Each
      published crosstab speaks to exactly one dimension-level, so the design is
      block diagonal by level and the marginal has a closed form.

    Returns draws: alpha {house: (S,)}, u {(dim, level): (S,)},
    sigma {dim: (S,)}, plus bookkeeping.
    """
    rng = np.random.default_rng(seed)

    z = np.array([logit(o["p"]) for o in obs])
    v = np.array([1.0 / (o["n"] * o["p"] * (1.0 - o["p"])) for o in obs])
    prec = 1.0 / v

    houses = sorted({o["house"] for o in obs})
    h_idx = np.array([houses.index(o["house"]) for o in obs])

    dims = sorted({o["dimension"] for o in obs if o["dimension"] != "national"})
    keys, dim_of_key = [], []
    for d in dims:
        for l in sorted({o["level"] for o in obs if o["dimension"] == d}):
            keys.append((d, l))
            dim_of_key.append(d)
    k_idx = np.array([keys.index((o["dimension"], o["level"]))
                      if o["dimension"] != "national" else -1 for o in obs])
    key_pos = {d: np.array([i for i, k in enumerate(keys) if k[0] == d]) for d in dims}

    alpha = np.array([float(np.average(z[h_idx == a], weights=prec[h_idx == a]))
                      for a in range(len(houses))])
    u = np.zeros(len(keys))
    sigma = {d: 0.15 for d in dims}

    keep_alpha, keep_u = [], []
    keep_sigma = {d: [] for d in dims}

    total = N_BURN + n_draws * N_THIN
    for it in range(total):
        # ---- alpha_h | u, data  (fixed effects, weak N(0, 4) prior)
        u_obs = np.where(k_idx >= 0, u[np.clip(k_idx, 0, None)], 0.0)
        r = z - u_obs
        for a in range(len(houses)):
            m = h_idx == a
            pa = prec[m].sum() + 1.0 / 4.0
            alpha[a] = rng.normal(float((r[m] * prec[m]).sum() / pa),
                                  np.sqrt(1.0 / pa))

        # ---- sigma_d from its MARGINAL, then u | sigma_d
        r = z - alpha[h_idx]
        for d in dims:
            pos = key_pos[d]
            local = -np.ones(len(obs), dtype=int)
            for j, kk in enumerate(pos):
                local[k_idx == kk] = j
            a_l, b_l = _level_stats(r, prec, local, len(pos))
            sigma[d] = _sample_sigma_marginal(a_l, b_l, PRIOR_SCALE_GROUP, rng)
            s2 = sigma[d] ** 2
            post_var = s2 / (1.0 + s2 * a_l)
            post_mean = s2 * b_l / (1.0 + s2 * a_l)
            u[pos] = rng.normal(post_mean, np.sqrt(post_var))

        if it >= N_BURN and (it - N_BURN) % N_THIN == 0:
            keep_alpha.append(alpha.copy())
            keep_u.append(u.copy())
            for d in dims:
                keep_sigma[d].append(sigma[d])

    A = np.array(keep_alpha)
    U = np.array(keep_u)
    return {
        "alpha": {houses[i]: A[:, i] for i in range(len(houses))},
        "mu": A.mean(axis=1),          # the "average instrument" intercept
        "house": {houses[i]: A[:, i] - A.mean(axis=1) for i in range(len(houses))},
        "u": {keys[i]: U[:, i] for i in range(len(keys))},
        "sigma": {d: np.array(keep_sigma[d]) for d in dims},
        "dims": dims, "keys": keys, "houses": houses, "n_obs": len(obs),
    }


# ---------------------------------------------------------------------------
# prediction + poststratification
# ---------------------------------------------------------------------------
def cell_probabilities(cells, post, dims):
    """(n_draws, n_cells) predicted support, house effect set to zero."""
    S = len(post["mu"])
    lin = np.repeat(post["mu"][:, None], len(cells), axis=1)
    for d in dims:
        col = DIMENSION_COLUMNS[d]
        levels = cells[col].to_numpy()
        for lvl in pd.unique(levels):
            key = (d, lvl)
            if key not in post["u"]:
                # Level never observed for this dimension. Draw from the fitted
                # group SD rather than assuming zero effect, so the uncertainty
                # reflects that we have not measured it.
                sd = post["sigma"][d]
                seed_k = abs(zlib.crc32(f"{d}|{lvl}".encode())) % (2 ** 32)
                draw = np.random.default_rng(seed_k).standard_normal(S) * sd
            else:
                draw = post["u"][key]
            lin[:, levels == lvl] += draw[:, None]
    return inv_logit(lin)


def poststratify(df, n_draws=500, seed=20260905, evidence_path=EVIDENCE_PATH,
                 include_holdout=False):
    """
    Drop-in replacement for opinion.poststratify, using MRP.
    Returns (overall, overall_ids, by_group, warnings).
    """
    import opinion as O

    obs, notes = load_observations(evidence_path, include_holdout)
    warnings = list(notes)
    if not obs:
        return None, [], [], warnings + ["No usable evidence; MRP not fitted."]

    post = fit(obs, n_draws=n_draws, seed=seed)
    dims = post["dims"]
    cells = O.build_cells(df)

    thin = [o["evidence_id"] for o in obs if o["n"] * DESIGN_EFFECT < P.MIN_EVIDENCE_N]
    warnings.append(
        f"MRP fitted on {post['n_obs']} crosstab observations across "
        f"{len(dims)} dimension(s): {', '.join(dims)}. Partial pooling, so no "
        f"MIN_EVIDENCE_N exclusion is applied"
        + (f" -- {len(thin)} thin record(s) are USED at reduced weight: "
           f"{', '.join(thin)}." if thin else "."))

    hs = {h: float(np.median(v)) for h, v in post["house"].items()}
    spread = (max(hs.values()) - min(hs.values())) if len(hs) > 1 else 0.0
    warnings.append(
        "Estimated survey house effects (logit scale, median): "
        + "; ".join(f"{h} {v:+.3f}" for h, v in sorted(hs.items()))
        + f". Spread {spread:.3f} logits ~ "
          f"{100 * (inv_logit(0.5 * spread) - inv_logit(-0.5 * spread)):.1f} "
          f"points at the midpoint -- instrument effect, removed from the "
          f"population estimate.")
    for d in dims:
        warnings.append(
            f"Between-level SD for {d}: {float(np.median(post['sigma'][d])):.3f} "
            f"logits (median posterior).")

    missing = [d for d in DIMENSION_COLUMNS if d not in dims]
    if missing:
        warnings.append(
            f"No usable evidence on: {', '.join(missing)}. These dimensions are "
            f"EXCLUDED from the model rather than imputed, and groups defined on "
            f"them report insufficient_evidence.")

    p_cells = cell_probabilities(cells, post, dims)

    rng = np.random.default_rng(seed + 1)
    w0 = cells["weight"].to_numpy(float)
    boot = rng.exponential(size=(len(post["mu"]), len(cells)))
    w = w0[None, :] * boot
    w *= (w0.sum() / w.sum(axis=1, keepdims=True))

    ids_by_dim = {}
    for o in obs:
        ids_by_dim.setdefault(o["dimension"], set()).add(o["evidence_id"])
    all_ids = sorted({o["evidence_id"] for o in obs})

    def agg(mask):
        ws, ps = w[:, mask], p_cells[:, mask]
        vals = (ws * ps).sum(1) / ws.sum(1)
        return {"median": float(np.median(vals)),
                "p05": float(np.percentile(vals, 5)),
                "p95": float(np.percentile(vals, 95))}

    overall = agg(np.ones(len(cells), dtype=bool))

    by_group = []
    for gtype, col in [("income_quintile", "income_quintile"),
                       ("income_band", "income_band"),
                       ("household_type", "household_type"),
                       ("census_region", "region_name")]:
        # A group only gets a number when the model actually contains a
        # dimension informed by evidence. income_quintile is not a modelled
        # dimension, but its cells inherit income_band and region effects, so it
        # is legitimately estimable.
        estimable = (gtype in dims) or (gtype == "income_quintile" and
                                        "income_band" in dims)
        for gname in sorted(cells[col].dropna().unique()):
            mask = (cells[col] == gname).to_numpy()
            n_sample = int(cells.loc[mask, "n"].sum())
            entry = {"group_type": gtype, "group": gname,
                     "sample_n": n_sample,
                     "low_sample": bool(n_sample < P.LOW_SAMPLE_N),
                     "evidence_coverage": 1.0 if estimable else 0.0,
                     "method": "mrp"}
            if estimable:
                entry["support"] = agg(mask)
                entry["evidence_status"] = "ok"
                src = "income_band" if gtype == "income_quintile" else gtype
                entry["evidence_ids"] = sorted(ids_by_dim.get(src, set())
                                               | ids_by_dim.get("national", set()))
            else:
                entry["support"] = None
                entry["evidence_status"] = "insufficient_evidence"
                entry["evidence_ids"] = []
            by_group.append(entry)

    return overall, all_ids, by_group, warnings


if __name__ == "__main__":
    import engine as E

    pop = E.Population.load()
    overall, ids, groups, warns = poststratify(pop.df, n_draws=500)
    print(f"MRP overall support: {overall['median']:.3f} "
          f"[{overall['p05']:.3f}, {overall['p95']:.3f}]\n")
    print(f"{'group_type':<18}{'group':<20}{'status':<24}{'support':<28}")
    for g in groups:
        s = g["support"]
        st = "--" if s is None else f"{s['median']:.3f} [{s['p05']:.3f}, {s['p95']:.3f}]"
        print(f"{g['group_type']:<18}{g['group']:<20}{g['evidence_status']:<24}{st:<28}")
    print("\nwarnings:")
    for w in warns:
        print(f"  - {w}")
