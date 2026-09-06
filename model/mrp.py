"""
Multilevel regression with poststratification (MRP).

WHY THIS REPLACED THE FALLBACK LADDER
The ladder in opinion.py resolved each population cell at the most specific
evidence level available and stopped. Three costs, all visible in the A6
backtest:

  1. It DISCARDED thin crosstabs. MIN_EVIDENCE_N = 300 threw away the n=252
     Northeast and n=287 $100k+ records entirely. Those records are not
     worthless, only noisy. Partial pooling uses them at their own precision.
  2. Region evidence NEVER entered, because every cell resolved at income_band
     first. Regional variation in the output was population composition, not
     measured opinion.
  3. It could not separate a HOUSE effect from a real subgroup difference, nor
     a TIME effect from either.

THE MODEL
Observations are aggregate crosstab cells, not individual respondents. Each
record is moved to the logit scale, where a binomial proportion is approximately
normal with a known variance (delta method):

    z_j = logit(p_j)              v_j = 1 / (n_j * p_j * (1 - p_j))

    z_j = alpha[house_j] + gamma[wording_j] + beta * (t_j - t_ref)
          + u[dim_j, level_j] + eps_j,        eps_j ~ N(0, v_j)

    gamma[w] ~ N(0, sigma_wording^2)     question-wording effect
    u[d, l]  ~ N(0, sigma_d^2)           effect of level l of dimension d
    beta                                 linear time trend, logits per month
    sigma_*  ~ half-t(3, A)

The observation variances are KNOWN, so every conditional is conjugate and the
posterior is sampled by plain Gibbs -- no Stan, no PyMC, no tuning.

FOUR THINGS THE MODEL SEPARATES, each of which the ladder confounded:

  policy_key   Records are partitioned by the policy they asked about. A CTC
               estimate never pools in stimulus-check, UBI or whole-package
               polling. Without this, "79% support $1,400 checks" would inflate
               the CTC number.
  house        YouGov reads 0.51 nationally where Morning Consult reads 0.54 on
               a differently worded item. That gap belongs to the instrument.
               Prediction uses the AVERAGE house, not either pollster.
  wording      Even within one house, "do you approve of the expanded CTC" and
               "should Congress extend it through 2025" are different questions
               sitting at different levels.
  time         Support for the CTC fell through 2021. With two waves of
               identical wording in training (Morning Consult July and October),
               the slope is identified and the model can extrapolate forward.

PREDICTION
    logit(p_cell) = mean(alpha) + gamma_ref + beta * (t_predict - t_ref)
                    + u[income_band, i] + u[census_region, r]

The house effect is set to the mean rather than to any one pollster's value.
Cell probabilities are then poststratified onto the ACS population with a
Bayesian bootstrap on the household design weights, so population sampling error
travels alongside model uncertainty.

WHAT THIS IS NOT
No LLM generates any number here. Every input is an observed, verified crosstab
read out of a source PDF. Cells with no direct evidence on a dimension are not
invented: that dimension is excluded from the model and groups defined on it
report insufficient_evidence.

KNOWN LIMITATIONS
  - ECOLOGICAL APPROXIMATION. Each published crosstab is a marginal over the
    other dimensions. Effects are additive on the logit scale, which is standard
    but is an assumption: an income x region interaction is not recoverable from
    marginals alone.
  - DESIGN EFFECT = 1. Published polls are weighted, so effective n is below
    nominal. No pollster here publishes a design effect and inventing one is not
    allowed, so v_j uses nominal n. Intervals are somewhat optimistic.
  - THE TIME TREND IS AN EXTRAPOLATION, identified from a small number of
    repeated-wording waves. It is reported with its posterior interval and
    should not be pushed far beyond the observed window.
"""

import json
import sys
import zlib
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import params as P  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
EVIDENCE_PATH = REPO / "data" / "evidence.json"

DIMENSION_COLUMNS = {
    "income_band": "income_band",
    "census_region": "region_name",
    "household_type": "has_children",
}

DESIGN_EFFECT = 1.0
PRIOR_SCALE_GROUP = 0.5
PRIOR_SCALE_WORDING = 0.5
PRIOR_SD_BETA = 0.10          # logits per month, weakly regularising
TIME_EPOCH = date(2021, 1, 1)

N_BURN = 500
N_THIN = 2

SIGMA_GRID = np.exp(np.linspace(np.log(1e-3), np.log(1.5), 240))


def logit(p):
    return np.log(p / (1.0 - p))


def inv_logit(x):
    return 1.0 / (1.0 + np.exp(-x))


def _months(d):
    if not d:
        return 0.0
    y, m, dd = (int(x) for x in str(d)[:10].split("-"))
    return (date(y, m, dd) - TIME_EPOCH).days / 30.44


# ---------------------------------------------------------------------------
# data assembly
# ---------------------------------------------------------------------------
def load_observations(evidence_path=EVIDENCE_PATH, policy_key="ctc_2021",
                      holdout_group="dec2021", include_holdout=False,
                      max_date=None):
    """
    Usable observations for one policy.

    NOTE the deliberate difference from opinion.load_evidence: there is no
    MIN_EVIDENCE_N gate here. Using thin crosstabs at their correct weight is
    the entire point of partial pooling. A record is dropped only when its
    variance cannot be computed -- no sample size, or a degenerate proportion.
    """
    recs = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    obs, notes = [], []
    n_other_policy = 0
    n_future = 0
    for r in recs:
        if str(r.get("evidence_id", "")).startswith("_"):
            continue
        if r.get("policy_key", "ctc_2021") != policy_key:
            n_other_policy += 1
            continue
        # holdout_group=None means production: use every record, hold nothing
        # back. The holdout only exists for backtesting.
        if holdout_group is not None:
            is_held = r.get("holdout_group") == holdout_group
            if is_held != bool(include_holdout):
                continue
        elif include_holdout:
            continue
        # TEMPORAL SPLIT. Training must not contain anything fielded on or after
        # the held-out poll's own date. Without this, holding out the October
        # wave while leaving December in training lets the model INTERPOLATE
        # between July and December instead of extrapolating forward -- which
        # flattered the October result to a 0.69 point mean gap and 8/8
        # coverage. That number was leakage, not skill.
        if ((not include_holdout) and max_date and r.get("fielded_date")
                and str(r["fielded_date"]) >= str(max_date)):
            n_future += 1
            continue
        n, p = r.get("sample_size"), r.get("support_pct")
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
            notes.append(f"{r['evidence_id']}: dimension '{dim}' is not carried "
                         f"by the population. Dropped.")
            continue
        obs.append({
            "evidence_id": r["evidence_id"],
            "dimension": dim,
            "level": r.get("subgroup"),
            "p": float(p),
            "n": float(n) / DESIGN_EFFECT,
            "house": _house_of(r),
            "wording": r.get("wording_group", "unknown"),
            "t": _months(r.get("fielded_date")),
            "date": r.get("fielded_date"),
        })
    if n_future:
        notes.append(
            f"{n_future} record(s) fielded on or after {max_date} were excluded "
            f"from training. A model predicting that date must not see data from "
            f"it or later.")
    if n_other_policy:
        notes.append(
            f"{n_other_policy} record(s) about a different policy were excluded "
            f"by policy_key. Pooling them into a '{policy_key}' estimate would "
            f"mix answers to different questions about different policies.")
    return obs, notes


def _house_of(record):
    src = (record.get("source") or "").lower()
    if "yougov" in src or "economist" in src:
        return "yougov_economist"
    if "morning consult" in src:
        return "morning_consult"
    if "monmouth" in src:
        return "monmouth"
    if "data for progress" in src:
        return "data_for_progress"
    return "other"


# ---------------------------------------------------------------------------
# collapsed Gibbs
# ---------------------------------------------------------------------------
def _half_t_logpdf(sigma, scale, nu=3.0):
    return -0.5 * (nu + 1.0) * np.log1p((sigma / scale) ** 2 / nu)


def _group_stats(r, prec, idx, n_groups):
    a = np.zeros(n_groups)
    b = np.zeros(n_groups)
    for i in range(n_groups):
        m = idx == i
        if m.any():
            a[i] = prec[m].sum()
            b[i] = (r[m] * prec[m]).sum()
    return a, b


def _sample_sigma_marginal(a, b, prior_scale, rng):
    """
    Draw sigma from its marginal posterior with the group effects integrated
    out. For one group, r ~ N(effect, diag(v)) with effect ~ N(0, s^2) gives
    r ~ N(0, diag(v) + s^2 J), and Sherman-Morrison gives

        r' Sigma^-1 r = c - s^2 b^2 / (1 + s^2 a)
        log|Sigma|    = log|D| + log(1 + s^2 a)

    with a = sum(1/v) and b = sum(r/v). Terms free of s drop out.

    WHY MARGINAL AND NOT CONDITIONAL: sampling sigma from p(sigma | effects)
    creates the centred-parameterisation funnel. If sigma drifts small the
    conditional for the effects is prior-dominated and pins them at zero, and
    sigma then stays small because the likelihood only reaches it through
    sum(effect^2). The chain sticks at sigma ~ 0 and every subgroup collapses
    onto the grand mean. That is exactly what this model did before the fix --
    between-level SDs of 0.0002 logits and a house effect of exactly zero,
    despite observations spanning 0.42 to 0.79, with no exception and no
    warning. model/diagnostics.py asserts against a recurrence.
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


def fit(obs, n_draws=500, seed=20260905, t_ref=None):
    """
    Posterior draws by partially collapsed Gibbs.

    House effects are FIXED: there are few houses, each with many observations,
    and a variance component over them is barely identified while contributing
    its own funnel. Wording and level effects are random, each sampled jointly
    with its own sigma.
    """
    rng = np.random.default_rng(seed)

    z = np.array([logit(o["p"]) for o in obs])
    v = np.array([1.0 / (o["n"] * o["p"] * (1.0 - o["p"])) for o in obs])
    prec = 1.0 / v
    t_all = np.array([o["t"] for o in obs])
    if t_ref is None:
        t_ref = float(np.median(t_all))
    tt = t_all - t_ref

    houses = sorted({o["house"] for o in obs})
    h_idx = np.array([houses.index(o["house"]) for o in obs])
    words = sorted({o["wording"] for o in obs})
    w_idx = np.array([words.index(o["wording"]) for o in obs])

    dims = sorted({o["dimension"] for o in obs if o["dimension"] != "national"})
    keys = []
    for d in dims:
        for l in sorted({o["level"] for o in obs if o["dimension"] == d}):
            keys.append((d, l))
    k_idx = np.array([keys.index((o["dimension"], o["level"]))
                      if o["dimension"] != "national" else -1 for o in obs])
    key_pos = {d: np.array([i for i, k in enumerate(keys) if k[0] == d]) for d in dims}

    alpha = np.array([float(np.average(z[h_idx == a], weights=prec[h_idx == a]))
                      for a in range(len(houses))])
    gamma = np.zeros(len(words))
    beta = 0.0
    u = np.zeros(len(keys))
    sigma = {d: 0.15 for d in dims}
    sigma_w = 0.15

    keep = {"alpha": [], "gamma": [], "beta": [], "u": [],
            "sigma": {d: [] for d in dims}, "sigma_wording": []}

    total = N_BURN + n_draws * N_THIN
    for it in range(total):
        u_obs = np.where(k_idx >= 0, u[np.clip(k_idx, 0, None)], 0.0)

        # alpha (fixed effects, weak N(0, 4) prior)
        r = z - gamma[w_idx] - beta * tt - u_obs
        for a in range(len(houses)):
            m = h_idx == a
            pa = prec[m].sum() + 0.25
            alpha[a] = rng.normal(float((r[m] * prec[m]).sum() / pa),
                                  np.sqrt(1.0 / pa))

        # (sigma_wording, gamma) as one block
        r = z - alpha[h_idx] - beta * tt - u_obs
        a_w, b_w = _group_stats(r, prec, w_idx, len(words))
        sigma_w = _sample_sigma_marginal(a_w, b_w, PRIOR_SCALE_WORDING, rng)
        s2 = sigma_w ** 2
        gamma = rng.normal(s2 * b_w / (1.0 + s2 * a_w),
                           np.sqrt(s2 / (1.0 + s2 * a_w)))
        # PARAMETER EXPANSION (Liu & Wu 1999). alpha and gamma are both
        # intercept-like, so their sum is identified but the split is not. Left
        # alone the chain drifts along that ridge and the intercept mixes badly
        # -- R-hat reached 1.28. Shifting gamma's mean into alpha each sweep
        # collapses the ridge without changing any fitted value.
        shift = float(gamma.mean())
        gamma -= shift
        alpha += shift

        # beta | rest
        r = z - alpha[h_idx] - gamma[w_idx] - u_obs
        pb = float((tt ** 2 * prec).sum()) + 1.0 / PRIOR_SD_BETA ** 2
        beta = rng.normal(float((r * tt * prec).sum() / pb), np.sqrt(1.0 / pb))

        # (sigma_d, u_d) as one block per dimension
        r = z - alpha[h_idx] - gamma[w_idx] - beta * tt
        for d in dims:
            pos = key_pos[d]
            local = -np.ones(len(obs), dtype=int)
            for j, kk in enumerate(pos):
                local[k_idx == kk] = j
            a_l, b_l = _group_stats(r, prec, local, len(pos))
            sigma[d] = _sample_sigma_marginal(a_l, b_l, PRIOR_SCALE_GROUP, rng)
            s2 = sigma[d] ** 2
            u[pos] = rng.normal(s2 * b_l / (1.0 + s2 * a_l),
                                np.sqrt(s2 / (1.0 + s2 * a_l)))

        if it >= N_BURN and (it - N_BURN) % N_THIN == 0:
            keep["alpha"].append(alpha.copy())
            keep["gamma"].append(gamma.copy())
            keep["beta"].append(beta)
            keep["u"].append(u.copy())
            keep["sigma_wording"].append(sigma_w)
            for d in dims:
                keep["sigma"][d].append(sigma[d])

    A = np.array(keep["alpha"])
    G = np.array(keep["gamma"])
    U = np.array(keep["u"])
    return {
        "alpha": {houses[i]: A[:, i] for i in range(len(houses))},
        "mu": A.mean(axis=1),
        "house": {houses[i]: A[:, i] - A.mean(axis=1) for i in range(len(houses))},
        "gamma": {words[i]: G[:, i] for i in range(len(words))},
        "beta": np.array(keep["beta"]),
        "u": {keys[i]: U[:, i] for i in range(len(keys))},
        "sigma": {d: np.array(keep["sigma"][d]) for d in dims},
        "sigma_wording": np.array(keep["sigma_wording"]),
        "dims": dims, "keys": keys, "houses": houses, "words": words,
        "t_ref": t_ref, "n_obs": len(obs),
    }


# ---------------------------------------------------------------------------
# prediction
# ---------------------------------------------------------------------------
def cell_probabilities(cells, post, dims, predict_date=None, wording=None):
    S = len(post["mu"])
    lin = np.repeat(post["mu"][:, None], len(cells), axis=1)

    if predict_date is not None:
        dt = _months(predict_date) - post["t_ref"]
        lin += (post["beta"] * dt)[:, None]
    if wording is not None and wording in post["gamma"]:
        lin += post["gamma"][wording][:, None]

    for d in dims:
        col = DIMENSION_COLUMNS[d]
        levels = cells[col].to_numpy()
        for lvl in pd.unique(levels):
            key = (d, lvl)
            if key in post["u"]:
                draw = post["u"][key]
            else:
                # Level never observed on this dimension. Draw from the fitted
                # group SD rather than assuming a zero effect, so the interval
                # reflects that we have not measured it.
                seed_k = zlib.crc32(f"{d}|{lvl}".encode()) % (2 ** 32)
                draw = (np.random.default_rng(seed_k).standard_normal(S)
                        * post["sigma"][d])
            lin[:, levels == lvl] += draw[:, None]
    return inv_logit(lin)


def poststratify(df, n_draws=500, seed=20260905, evidence_path=EVIDENCE_PATH,
                 include_holdout=False, policy_key="ctc_2021",
                 holdout_group="dec2021", predict_date=None, wording=None,
                 max_date=None):
    """Drop-in replacement for opinion.poststratify.
    Returns (overall, evidence_ids, by_group, warnings)."""
    import opinion as O

    obs, notes = load_observations(evidence_path, policy_key=policy_key,
                                   holdout_group=holdout_group,
                                   include_holdout=include_holdout,
                                   max_date=max_date)
    warnings = list(notes)
    if not obs:
        return None, [], [], warnings + [
            f"No usable evidence for policy '{policy_key}'; MRP not fitted."]

    post = fit(obs, n_draws=n_draws, seed=seed)
    dims = post["dims"]
    cells = O.build_cells(df)

    dates = sorted({o["date"] for o in obs if o["date"]})
    if predict_date is None:
        predict_date = dates[-1] if dates else None

    thin = [o["evidence_id"] for o in obs
            if o["n"] * DESIGN_EFFECT < P.MIN_EVIDENCE_N]
    warnings.append(
        f"MRP fitted on {post['n_obs']} crosstab observations for policy "
        f"'{policy_key}', across {len(dims)} dimension(s) ({', '.join(dims)}), "
        f"{len(post['houses'])} survey house(s), {len(post['words'])} question "
        f"wording(s) and {len(dates)} fielding date(s) ({dates[0]} to "
        f"{dates[-1]}). No MIN_EVIDENCE_N exclusion is applied"
        + (f"; {len(thin)} thin record(s) are USED at reduced weight."
           if thin else "."))
    warnings.append(f"Support is reported as of {predict_date}"
                    + (f", using the '{wording}' question wording."
                       if wording else ", averaged over question wordings."))

    b = post["beta"]
    warnings.append(
        f"Fitted time trend: {float(np.median(b)):+.4f} logits/month "
        f"[{float(np.percentile(b, 5)):+.4f}, {float(np.percentile(b, 95)):+.4f}]. "
        f"Identified from repeated-wording waves; treat as an extrapolation "
        f"outside the observed window.")

    hs = {h: float(np.median(v)) for h, v in post["house"].items()}
    if len(hs) > 1:
        spread = max(hs.values()) - min(hs.values())
        warnings.append(
            "Survey house effects (logit scale, median): "
            + "; ".join(f"{h} {v:+.3f}" for h, v in sorted(hs.items()))
            + f". Spread {spread:.3f} logits -- instrument effect, removed from "
              f"the population estimate.")
    warnings.append(
        f"Between-wording SD: {float(np.median(post['sigma_wording'])):.3f} "
        f"logits. "
        + "; ".join(f"Between-level SD for {d}: "
                    f"{float(np.median(post['sigma'][d])):.3f}" for d in dims))

    missing = [d for d in DIMENSION_COLUMNS if d not in dims]
    if missing:
        warnings.append(
            f"No usable evidence on: {', '.join(missing)}. Excluded from the "
            f"model rather than imputed; groups defined on them report "
            f"insufficient_evidence.")

    p_cells = cell_probabilities(cells, post, dims, predict_date, wording)

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
        estimable = (gtype in dims) or (gtype == "income_quintile"
                                        and "income_band" in dims)
        for gname in sorted(cells[col].dropna().unique()):
            mask = (cells[col] == gname).to_numpy()
            n_sample = int(cells.loc[mask, "n"].sum())
            entry = {"group_type": gtype, "group": gname, "sample_n": n_sample,
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
        st = "--" if s is None else (f"{s['median']:.3f} "
                                     f"[{s['p05']:.3f}, {s['p95']:.3f}]")
        print(f"{g['group_type']:<18}{g['group']:<20}"
              f"{g['evidence_status']:<24}{st:<28}")
    print("\nwarnings:")
    for w in warns:
        print(f"  - {w}")
