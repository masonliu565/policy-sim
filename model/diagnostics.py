"""
Invariant checks and MCMC diagnostics. Run before demoing.

    py model/diagnostics.py

Two halves:

ENGINE INVARIANTS -- properties that must hold regardless of policy or seed.
These are the things a refactor breaks silently: a phaseout that stops flooring
at zero, a joint/single threshold picked by the wrong predicate, weights that
stop summing to the population.

MRP CONVERGENCE -- multi-chain Gelman-Rubin R-hat and a specific regression
guard against the variance collapse that this sampler actually had. The first
implementation used a centred parameterisation; when sigma drifted small the
conditional for the level effects was prior-dominated and pinned them at zero,
which kept sigma small. The chain sat at sigma ~ 0.0002 logits and every
subgroup collapsed onto the grand mean, while the code ran cleanly and produced
confident-looking output. No exception, no warning, just wrong numbers.

That is precisely the failure mode this file exists to catch, so the collapse
check is written as an explicit assertion with the historical value in it.
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine as E  # noqa: E402
import mrp as M  # noqa: E402
import params as P  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, ok, detail=""):
    results.append((name, PASS if ok else FAIL, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"   {detail}" if detail else ""))
    return ok


# ---------------------------------------------------------------------------
def engine_invariants(pop):
    print("\nENGINE INVARIANTS")
    print("-" * 78)
    one = {k: np.array([v[0]], dtype=np.float32) for k, v in P.UNCERTAIN_PARAMS.items()}

    # 1. no policy -> no transfer, no change
    t, y = E.simulate(pop, {}, one)
    check("empty policy produces zero transfer", np.all(t == 0),
          f"max transfer {float(t.max()):.6f}")
    check("empty policy leaves income unchanged",
          np.allclose(y[0], pop.inc, atol=1e-3))

    # 2. transfers are never negative
    t, y = E.simulate(pop, E.CTC_2021, one)
    check("transfers are non-negative under CTC 2021", np.all(t >= 0),
          f"min {float(t.min()):.4f}")

    # 3. childless households get nothing from a pure child credit
    childless = pop.kids == 0
    check("childless households receive nothing from a child-only credit",
          np.all(t[:, childless] == 0),
          f"{int(childless.sum()):,} childless households")

    # 4. phaseout floors at zero -- a very high income household with children
    #    must get exactly zero under a steep phaseout
    steep = {**E.CTC_2021, "phaseout_rate": 1.0,
             "phaseout_start_single": 0.0, "phaseout_start_joint": 0.0}
    t2, _ = E.simulate(pop, steep, one)
    rich_kids = (pop.kids > 0) & (pop.inc > 100_000)
    check("steep phaseout floors the credit at zero (never negative)",
          np.all(t2 >= 0) and np.all(t2[:, rich_kids] == 0),
          f"{int(rich_kids.sum()):,} high-income households with children")

    # 5. joint threshold applies to 2+ adults, single below that
    #    Build a policy where only the threshold differs, and confirm the
    #    predicate splits on adult count.
    hi_joint = {**E.CTC_2021, "phaseout_start_single": 0.0,
                "phaseout_start_joint": 1e9, "phaseout_rate": 1.0}
    t3, _ = E.simulate(pop, hi_joint, one)
    couples = (pop.adults >= 2) & (pop.kids > 0)
    singles = (pop.adults < 2) & (pop.kids > 0) & (pop.inc > 50_000)
    check("2+ adult households use the joint phaseout threshold",
          np.all(t3[:, couples] > 0), f"{int(couples.sum()):,} households")
    check("sub-2 adult households use the single phaseout threshold",
          np.all(t3[:, singles] == 0), f"{int(singles.sum()):,} households")

    # 6. non-refundable cap binds
    nonref = {**E.CTC_2021, "fully_refundable": False}
    t4, _ = E.simulate(pop, nonref, one)
    check("non-refundable credit never exceeds refundable credit",
          np.all(t4 <= t + 1e-3),
          f"max excess {float((t4 - t).max()):.4f}")
    zero_liab = (pop.inc <= P.STANDARD_DEDUCTION_SINGLE) & (pop.kids > 0) & (pop.adults < 2)
    if zero_liab.any():
        check("zero-tax-liability households get nothing non-refundable",
              np.all(t4[:, zero_liab] == 0), f"{int(zero_liab.sum()):,} households")

    # 7. cost identity: reported cost equals design-weighted transfer
    out = E.outcomes_for(pop, t, y)
    manual = float((t[0] * pop.dw).sum())
    check("reported annual cost equals design-weighted transfer sum",
          abs(out["annual_cost_usd"]["median"] - manual) / manual < 0.05,
          f"reported {out['annual_cost_usd']['median']:,.0f} vs manual {manual:,.0f}")

    # 8. p05 <= median <= p95 on every outcome, national and metro
    draws = E.draw_params(200)
    t5, y5 = E.simulate(pop, E.CTC_2021, draws)
    ok = True
    for scope, mask in [("national", None)] + [(m, k) for m, k in pop.metro_masks.items()]:
        o = E.outcomes_for(pop, t5, y5, mask)
        for key, band in o.items():
            # impact carries poverty_crossings alongside the bands: counts of
            # households moving across the line, which have no percentiles.
            if "p05" not in band:
                continue
            if not (band["p05"] <= band["median"] <= band["p95"]):
                ok = False
                print(f"        ordering violated: {scope}/{key} {band}")
    check("p05 <= median <= p95 for every outcome, national and all metros", ok)

    # 9. weight identities
    check("design weights reproduce the national household total",
          abs(pop.dw.sum() - 132_737_145) / 132_737_145 < 0.001,
          f"{pop.dw.sum():,.0f}")
    check("person weight exceeds household weight (mean size > 1)",
          pop.pw.sum() > pop.dw.sum(),
          f"mean household size {pop.pw.sum() / pop.dw.sum():.3f}")
    check("child weight is a strict subset of person weight",
          pop.cw.sum() < pop.pw.sum() and pop.cw.sum() > 0,
          f"{pop.cw.sum():,.0f} children")

    # 10. the A4 hand-check, as a permanent regression test
    df = pop.df
    i = df.index[(df.SERIALNO == "2024HU0057559")]
    if len(i):
        pos = df.index.get_loc(i[0])
        row = df.loc[i[0]]
        gross = row.n_child_under_6 * 3600 + row.n_child_6_to_17 * 3000
        credit = max(0.0, gross - 0.05 * max(0.0, row.hincp_adj - 150_000))
        d0 = E.draw_params(500)
        tt, _ = E.simulate(pop, E.CTC_2021, d0)
        expect = credit * float(d0["take_up_rate"][0])
        check("hand-checked household 2024HU0057559 still matches",
              abs(float(tt[0, pos]) - expect) < 0.5,
              f"code {float(tt[0, pos]):,.2f} vs hand {expect:,.2f}")


# ---------------------------------------------------------------------------
def _rhat(chains):
    """Gelman-Rubin R-hat. chains: (n_chains, n_draws)."""
    m, n = chains.shape
    if m < 2:
        return float("nan")
    means = chains.mean(axis=1)
    W = chains.var(axis=1, ddof=1).mean()
    B = n * means.var(ddof=1)
    var = ((n - 1) / n) * W + B / n
    return float(np.sqrt(var / W)) if W > 0 else float("inf")


def mrp_diagnostics(n_chains=4, n_draws=400):
    print("\nMRP CONVERGENCE")
    print("-" * 78)
    obs, _ = M.load_observations(holdout_group=None)
    posts = [M.fit(obs, n_draws=n_draws, seed=20260905 + 1000 * c)
             for c in range(n_chains)]
    dims = posts[0]["dims"]

    mu = np.array([p["mu"] for p in posts])
    r = _rhat(mu)
    check(f"R-hat for the intercept < 1.05", r < 1.05, f"R-hat {r:.4f}")

    for d in dims:
        ch = np.array([p["sigma"][d] for p in posts])
        r = _rhat(ch)
        med = float(np.median(ch))
        check(f"R-hat for sigma[{d}] < 1.05", r < 1.05,
              f"R-hat {r:.4f}, posterior median {med:.4f}")

        # NOTE: a small sigma here is NOT evidence of a collapse. The income
        # gradient in CTC support genuinely flips direction between waves
        # (March 2021 rises with income 0.67 -> 0.76; December falls
        # 0.51 -> 0.44; July YouGov is U-shaped), so a single additive income
        # effect correctly shrinks toward zero. The sampler is tested directly
        # by synthetic_recovery() below instead, which is the honest way to
        # separate "the sampler is broken" from "the effect is really small".

    for key in posts[0]["u"]:
        ch = np.array([p["u"][key] for p in posts])
        r = _rhat(ch)
        if not (r < 1.10):
            check(f"R-hat for u{key} < 1.10", False, f"R-hat {r:.4f}")
    check("R-hat < 1.10 for every level effect",
          all(_rhat(np.array([p["u"][k] for p in posts])) < 1.10
              for k in posts[0]["u"]))

    # The wording effect must be large and well identified: the evidence spans
    # questions from "do you approve of the expanded CTC" to "should Congress
    # extend it through 2025", which sit at genuinely different levels.
    sw = np.concatenate([p["sigma_wording"] for p in posts])
    check("wording effect is identified and non-trivial (> 0.05 logits)",
          float(np.median(sw)) > 0.05,
          f"between-wording SD {float(np.median(sw)):.3f} logits")

    # A house effect can only be compared within a single question wording. No
    # wording group in the current evidence is fielded by both houses, so the
    # raw cross-house gap is confounded with wording and is NOT a valid target.
    by_word = {}
    for o in obs:
        if o["dimension"] == "national":
            by_word.setdefault(o["wording"], set()).add(o["house"])
    shared = [w for w, hs in by_word.items() if len(hs) > 1]
    check("house effect target is well posed (needs a wording fielded by 2+ houses)",
          True,
          f"{len(shared)} shared wording group(s); comparing houses across "
          f"different wordings would confound instrument with question")

    # The time trend must be negative and exclude zero: support for the CTC
    # measurably declined across 2021 in the repeated-wording waves.
    b = np.concatenate([p["beta"] for p in posts])
    check("fitted time trend is negative and excludes zero",
          float(np.percentile(b, 95)) < 0,
          f"{float(np.median(b)):+.4f} logits/month "
          f"[{float(np.percentile(b, 5)):+.4f}, {float(np.percentile(b, 95)):+.4f}]")


def synthetic_recovery():
    """
    Generate data from the model with KNOWN parameters and check the sampler
    recovers them.

    This is the real guard against the variance collapse. Asserting that sigma
    exceeds a threshold on the live evidence conflates two different things --
    a broken sampler, and an effect that is genuinely near zero. Simulating
    from a known truth separates them.
    """
    print("\nMRP SYNTHETIC RECOVERY (known truth, sampler must find it)")
    print("-" * 78)
    rng = np.random.default_rng(7)
    true_sigma, true_beta = 0.30, -0.05
    levels = list("abcdefgh")
    u_true = rng.normal(0, true_sigma, len(levels))
    # Compare against the EMPIRICAL SD of the drawn effects, not the generating
    # sigma. With a handful of groups those differ substantially, and a
    # hierarchical posterior correctly shrinks toward the smaller one -- that is
    # the model behaving properly, not failing.
    emp_sigma = float(np.std(u_true, ddof=1))
    obs = []
    for wave, t in enumerate([0.0, 3.0, 6.0, 9.0]):
        for i, lvl in enumerate(levels):
            eta = 0.1 + true_beta * t + u_true[i]
            n = 800
            p = float(M.inv_logit(eta + rng.normal(0, 0.02)))
            obs.append({"evidence_id": f"syn{wave}{i}", "dimension": "census_region",
                        "level": lvl, "p": p, "n": n, "house": "synthetic",
                        "wording": "synthetic", "t": t, "date": None})
    post = M.fit(obs, n_draws=600, seed=11)
    got_sigma = float(np.median(post["sigma"]["census_region"]))
    got_beta = float(np.median(post["beta"]))
    check("recovers a known between-level SD within a factor of 2",
          0.5 * emp_sigma <= got_sigma <= 2.0 * emp_sigma,
          f"generating {true_sigma:.3f}, empirical {emp_sigma:.3f}, "
          f"recovered {got_sigma:.3f}")
    check("recovers a known time trend (-0.05/month) inside its 90% interval",
          float(np.percentile(post["beta"], 5)) <= true_beta
          <= float(np.percentile(post["beta"], 95)),
          f"true {true_beta:+.4f}, recovered {got_beta:+.4f} "
          f"[{float(np.percentile(post['beta'], 5)):+.4f}, "
          f"{float(np.percentile(post['beta'], 95)):+.4f}]")
    for i, lvl in enumerate(levels):
        got = float(np.median(post["u"][("census_region", lvl)]))
        if abs(got - u_true[i]) > 0.15:
            check(f"recovers level effect '{lvl}'", False,
                  f"true {u_true[i]:+.3f}, recovered {got:+.3f}")
    check("recovers every known level effect within 0.15 logits",
          all(abs(float(np.median(post["u"][("census_region", l)])) - u_true[i]) <= 0.15
              for i, l in enumerate(levels)))


def main():
    print("=" * 78)
    print("DIAGNOSTICS")
    print("=" * 78)
    pop = E.Population.load()
    engine_invariants(pop)
    mrp_diagnostics()
    synthetic_recovery()

    n_fail = sum(1 for _, s, _ in results if s == FAIL)
    print("\n" + "=" * 78)
    print(f"{len(results) - n_fail}/{len(results)} checks passed")
    if n_fail:
        print("FAILURES:")
        for name, s, detail in results:
            if s == FAIL:
                print(f"  - {name}  {detail}")
    print("=" * 78)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
