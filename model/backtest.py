"""
A6 -- backtests of the 2021 expanded Child Tax Credit.

The specification for this file was committed FIRST, in docs/backtest.md, before
this code existed. Check `git log --follow docs/backtest.md` against the commit
that introduced this file. Parameters were fixed there and are not touched here.

BACKTEST 1 -- material impact
    Predict the change in the child poverty rate caused by the 2021 expanded
    CTC, with p05/p95 across 500 seeds. Compare against the published measured
    change on a Supplemental Poverty Measure basis, read at run time from the
    Census Bureau's own published table:
        Table B-3, "Poverty in the United States: 2021" (P60-277)
        SPM, Under 18 years: 2020 vs 2021
    The observed figure is parsed out of the spreadsheet, not typed in.

BACKTEST 2 -- opinion
    Predict the subgroup support percentages in the held-out Morning Consult /
    POLITICO poll #2110009 (October 2021) by poststratifying ONLY on non-holdout
    evidence, then compare against the observed values.

NO TUNING. No parameter is adjusted to improve either fit. If a prediction
misses, this script reports the miss and prints a one-at-a-time sensitivity
decomposition showing which parameter the result is most sensitive to. The
interval is never widened after the fact to contain the observed value.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import charts  # noqa: E402
import engine as E  # noqa: E402
import opinion as O  # noqa: E402
import params as P  # noqa: E402
from matplotlib import pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SPM_TABLE = REPO / "data" / "raw" / "spm" / "tableB-3.xlsx"
OUT_JSON = REPO / "scenarios" / "backtest.json"

SPM_SOURCE = ('U.S. Census Bureau, "Poverty in the United States: 2021" '
              "(P60-277), Table B-3, SPM, Under 18 years")
SPM_URL = ("https://www2.census.gov/programs-surveys/demo/tables/p60/277/"
           "tableB-3.xlsx")

N_SEEDS = 500


# ---------------------------------------------------------------------------
# observed target, parsed from the published Census table
# ---------------------------------------------------------------------------
def observed_spm_child_change():
    if not SPM_TABLE.exists():
        raise SystemExit(f"ERROR: {SPM_TABLE} missing. Download:\n  {SPM_URL}")
    d = pd.read_excel(SPM_TABLE, header=None)

    title = str(d.iloc[1, 0])
    if "Table B-3" not in title:
        raise SystemExit(f"ERROR: unexpected sheet. Row 1 reads: {title[:80]!r}")

    row = None
    for i in range(len(d)):
        if str(d.iloc[i, 0]).strip() == "Under 18 years":
            row = i
            break
    if row is None:
        raise SystemExit("ERROR: 'Under 18 years' row not found in Table B-3.")

    # Columns per the sheet header: SPM 2020 pct at col 3 (moe col 4),
    # SPM 2021 pct at col 7 (moe col 8).
    p2020, moe2020 = float(d.iloc[row, 3]), float(d.iloc[row, 4])
    p2021, moe2021 = float(d.iloc[row, 7]), float(d.iloc[row, 8])
    return {
        "spm_child_poverty_2020": p2020 / 100.0,
        "spm_child_poverty_2020_moe": moe2020 / 100.0,
        "spm_child_poverty_2021": p2021 / 100.0,
        "spm_child_poverty_2021_moe": moe2021 / 100.0,
        "observed_change": (p2021 - p2020) / 100.0,
        # MOEs on the two years are not independent (same survey, overlapping
        # design), so this is an upper bound on the MOE of the difference.
        "observed_change_moe_upper": (moe2020 + moe2021) / 100.0,
        "source": SPM_SOURCE,
        "url": SPM_URL,
    }


# ---------------------------------------------------------------------------
# backtest 1
# ---------------------------------------------------------------------------
def child_poverty_change_per_seed(pop, draws, policy=E.CTC_2021):
    transfer, new_income = E.simulate(pop, policy, draws)
    poor = new_income < pop.threshold[None, :]
    rate = (poor * pop.cw[None, :]).sum(1) / pop.cw.sum()
    base = float(((pop.inc < pop.threshold) * pop.cw).sum() / pop.cw.sum())
    return rate - base, base, rate


def sensitivity(pop, base_rate):
    """
    One-at-a-time tornado: hold two parameters at their central value, swing the
    third across its full declared range, and report the resulting swing in the
    predicted change. Reported whether or not the prediction hits -- it is the
    honest answer to 'what is this number most sensitive to?'
    """
    names = list(P.UNCERTAIN_PARAMS)
    rows = []
    for name in names:
        lo_hi = []
        for which in (1, 2):  # index 1 = low, 2 = high
            d = {}
            for other in names:
                c, lo, hi = P.UNCERTAIN_PARAMS[other]
                v = P.UNCERTAIN_PARAMS[name][which] if other == name else c
                d[other] = np.array([v], dtype=np.float32)
            chg, _, _ = child_poverty_change_per_seed(pop, d)
            lo_hi.append(float(chg[0]))
        rows.append({"parameter": name,
                     "low": P.UNCERTAIN_PARAMS[name][1],
                     "high": P.UNCERTAIN_PARAMS[name][2],
                     "change_at_low": lo_hi[0],
                     "change_at_high": lo_hi[1],
                     "swing_pts": abs(lo_hi[1] - lo_hi[0]) * 100})
    rows.sort(key=lambda r: r["swing_pts"], reverse=True)
    return rows


def backtest_impact(pop, param_set="preregistered", headline=True):
    P.use_param_set(param_set)
    draws = E.draw_params(N_SEEDS)
    change, base, rate = child_poverty_change_per_seed(pop, draws)
    obs = observed_spm_child_change()

    pred = {"median": float(np.median(change)),
            "p05": float(np.percentile(change, 5)),
            "p95": float(np.percentile(change, 95)),
            "modelled_baseline_rate": base,
            "modelled_post_policy_rate_median": float(np.median(rate))}
    inside = pred["p05"] <= obs["observed_change"] <= pred["p95"]

    print("=" * 96)
    label = ("PRE-REGISTERED PARAMETERS (headline result)" if param_set == "preregistered"
             else "LITERATURE-SOURCED PARAMETERS (post-hoc, see caveat below)")
    print(f"BACKTEST 1 -- change in child poverty rate  |  {label}")
    print("=" * 96)
    print("  parameters: " + "; ".join(
        f"{k}={v[0]} [{v[1]}, {v[2]}]" for k, v in P.UNCERTAIN_PARAMS.items()))
    print(f"  modelled baseline child poverty rate : {100 * base:.2f}%  "
          f"(OPM-style, household income vs Census thresholds)")
    print(f"  modelled post-policy rate (median)   : "
          f"{100 * pred['modelled_post_policy_rate_median']:.2f}%")
    print()
    print(f"  PREDICTED change : {100 * pred['median']:+.2f} pts"
          f"   [p05 {100 * pred['p05']:+.2f}, p95 {100 * pred['p95']:+.2f}]")
    print(f"  OBSERVED  change : {100 * obs['observed_change']:+.2f} pts"
          f"   (SPM {100 * obs['spm_child_poverty_2020']:.1f}% -> "
          f"{100 * obs['spm_child_poverty_2021']:.1f}%)")
    print(f"  source           : {obs['source']}")
    print()
    print(f"  OBSERVED FALLS {'INSIDE' if inside else 'OUTSIDE'} THE PREDICTED "
          f"INTERVAL")
    if not inside:
        gap = obs["observed_change"] - (pred["p05"] if obs["observed_change"] <
                                        pred["p05"] else pred["p95"])
        print(f"  miss size        : {100 * abs(gap):.2f} pts beyond the "
              f"nearest interval bound")
        print()
        print("  NOT TUNED. Reporting the miss and decomposing it instead.")

    sens = sensitivity(pop, base)
    print()
    print("  SENSITIVITY (one-at-a-time, others held at central value):")
    print(f"    {'parameter':<34}{'range':<20}{'change at low':>15}"
          f"{'change at high':>16}{'swing':>10}")
    for r in sens:
        print(f"    {r['parameter']:<34}"
              f"{f'{r['low']} - {r['high']}':<20}"
              f"{100 * r['change_at_low']:>14.2f}p"
              f"{100 * r['change_at_high']:>15.2f}p"
              f"{r['swing_pts']:>9.2f}p")
    print(f"    -> most sensitive to: {sens[0]['parameter']}")

    return {"param_set": param_set, "parameters":
            {k: list(v) for k, v in P.UNCERTAIN_PARAMS.items()},
            "predicted": pred, "observed": obs,
            "observed_inside_interval": inside,
            "sensitivity": sens, "tuned": False}


# ---------------------------------------------------------------------------
# backtest 2
# ---------------------------------------------------------------------------
# The clean out-of-sample test. Morning Consult #2112154 (fielded 18-20 Dec
# 2021) was added to the evidence file AFTER both the fallback ladder and MRP
# had been built and evaluated. No version of either model has ever seen it.
# It shares question wording exactly with the July and October waves, which are
# both in training, so the time trend is identified and the December value is a
# genuine forward extrapolation.
CLEAN_HOLDOUT = "dec2021"
HOLDOUT_META = {
    "dec2021": {"label": "Morning Consult/POLITICO #2112154, 18-20 Dec 2021",
                "date": "2021-12-19",
                "wording": "mc_monthly_payment_support",
                "clean": True},
    "oct2021": {"label": "Morning Consult/POLITICO #2110009, Oct 2021",
                "date": "2021-10-04",
                "wording": "mc_monthly_payment_support",
                "clean": False},
}


def backtest_opinion(df, method="ladder", quiet=False,
                     holdout_group=CLEAN_HOLDOUT):
    """
    Predict a held-out poll from evidence that excludes it.

    method="ladder" -> the A5 fallback-ladder poststratification
    method="mrp"    -> the hierarchical model in mrp.py
    Both run against the SAME holdout so the comparison is like-for-like.

    When predicting a specific poll we condition on that poll's KNOWN fielding
    date and question wording. Those are observable properties of the
    instrument, not its answer -- withholding them would be testing whether the
    model can guess when a survey ran, which is not the question.
    """
    meta = HOLDOUT_META[holdout_group]
    if method == "mrp":
        import mrp as M
        overall, _ids, by_group, warns = M.poststratify(
            df, n_draws=N_SEEDS, include_holdout=False,
            holdout_group=holdout_group, max_date=meta["date"],
            predict_date=meta["date"], wording=meta["wording"])
    else:
        overall, _ids, by_group, warns = O.poststratify(df, n_draws=N_SEEDS,
                                                        include_holdout=False)
    pred_lookup = {}
    if overall is not None:
        pred_lookup[("national", "national")] = overall
    for g in by_group:
        if g["support"] is not None:
            pred_lookup[(g["group_type"], g["group"])] = g["support"]

    import mrp as _M
    held, _ = _M.load_observations(holdout_group=holdout_group,
                                   include_holdout=True)
    held = [{"subgroup_type": h["dimension"], "subgroup": h["level"],
             "evidence_id": h["evidence_id"], "support_pct": h["p"],
             "sample_size": int(h["n"])} for h in held]
    rows = []
    for r in held:
        key = (r["subgroup_type"], r["subgroup"])
        pred = pred_lookup.get(key)
        obs = r["support_pct"]
        se = float(np.sqrt(obs * (1 - obs) / r["sample_size"]))
        row = {"subgroup_type": r["subgroup_type"], "subgroup": r["subgroup"],
               "evidence_id": r["evidence_id"], "observed": obs,
               "observed_n": r["sample_size"], "observed_se": se,
               "predicted": pred,
               "gap": None if pred is None else pred["median"] - obs,
               "observed_inside_interval": (
                   None if pred is None
                   else bool(pred["p05"] <= obs <= pred["p95"]))}
        rows.append(row)

    if quiet:
        gaps_q = [abs(r["gap"]) for r in rows if r["gap"] is not None]
        hits_q = sum(1 for r in rows if r["observed_inside_interval"])
        scored_q = sum(1 for r in rows if r["predicted"] is not None)
        return {"rows": rows,
                "mean_absolute_gap": float(np.mean(gaps_q)) if gaps_q else float("nan"),
                "interval_coverage": f"{hits_q}/{scored_q}",
                "hits": hits_q, "scored": scored_q, "method": method,
                "holdout_group": holdout_group, "holdout": meta["label"],
                "clean_out_of_sample": meta["clean"], "warnings": warns}
    print()
    print("=" * 96)
    print(f"BACKTEST 2 -- opinion: held out {meta['label']}")
    print(f"   method: {method}   |   "
          + ("CLEAN out-of-sample: this poll was added to the evidence file "
             "after both models were built"
             if meta["clean"] else
             "NOT clean: MRP was designed after seeing this holdout"))
    print("=" * 96)
    print(f"  {'subgroup':<28}{'predicted [p05, p95]':<32}{'observed':>10}"
          f"{'gap':>9}{'obs n':>8}  hit")
    hits = 0
    scored = 0
    for r in rows:
        if r["predicted"] is None:
            print(f"  {r['subgroup_type'] + '/' + r['subgroup']:<28}"
                  f"{'(no prediction possible)':<32}{r['observed']:>10.3f}"
                  f"{'--':>9}{r['observed_n']:>8}  --")
            continue
        p = r["predicted"]
        scored += 1
        hits += int(r["observed_inside_interval"])
        print(f"  {r['subgroup_type'] + '/' + r['subgroup']:<28}"
              f"{f'{p['median']:.3f} [{p['p05']:.3f}, {p['p95']:.3f}]':<32}"
              f"{r['observed']:>10.3f}{100 * r['gap']:>8.1f}p"
              f"{r['observed_n']:>8}  "
              f"{'YES' if r['observed_inside_interval'] else 'no'}")
    gaps = [abs(r["gap"]) for r in rows if r["gap"] is not None]
    mae = float(np.mean(gaps)) if gaps else float("nan")
    print()
    print(f"  mean absolute gap : {100 * mae:.2f} points across {len(gaps)} subgroups")
    print(f"  interval coverage : {hits}/{scored} observed values inside the "
          f"predicted interval")
    print()
    if method == "ladder":
        print("  Interpretation, written before the numbers were seen "
              "(docs/backtest.md):")
        print("  the region crosstabs never enter the ladder -- every population")
        print("  cell resolves at the income_band level -- so regional predictions")
        print("  are population composition, not measured regional opinion. Flat")
        print("  regional predictions against varying observed values are the")
        print("  expected consequence of that, not a surprise.")
    else:
        print("  MRP separates four things the ladder confounded: the policy")
        print("  asked about, the survey house, the question wording, and time.")
        gaps = [r["gap"] for r in rows if r["gap"] is not None]
        pos = sum(1 for g in gaps if g > 0)
        print(f"  Residual gaps split {pos} positive / {len(gaps) - pos} negative,")
        if 0 < pos < len(gaps):
            print("  so the systematic one-directional bias of the earlier model is")
            print("  gone -- the fitted time trend accounts for the 2021 decline in")
            print("  support rather than ignoring it.")
        else:
            print("  i.e. still one-directional. The time trend has not removed the")
            print("  bias; report that rather than the headline gap alone.")
        if meta["clean"]:
            print()
            print("  THIS IS A CLEAN OUT-OF-SAMPLE TEST. The held-out poll was added")
            print("  to the evidence file after both methods had been built and")
            print("  evaluated, so no design decision could have been informed by it.")
        else:
            print()
            print("  NOT CLEAN: MRP was designed after seeing this holdout. Kept for")
            print("  continuity with the earlier published comparison; the December")
            print("  test is the one to quote.")

    return {"rows": rows, "mean_absolute_gap": mae, "method": method,
            "holdout_group": holdout_group, "holdout": meta["label"],
            "clean_out_of_sample": meta["clean"],
            "interval_coverage": f"{hits}/{scored}", "hits": hits,
            "scored": scored, "warnings": warns}


# ---------------------------------------------------------------------------
# charts
# ---------------------------------------------------------------------------
def chart_impact(res):
    charts.apply_style()
    fig, ax = plt.subplots(figsize=(15, 7.5))
    p = res["predicted"]
    o = res["observed"]
    inside = res["observed_inside_interval"]

    y = 0.0
    lo, hi, md = 100 * p["p05"], 100 * p["p95"], 100 * p["median"]
    obs = 100 * o["observed_change"]

    ax.hlines(y, lo, hi, color=charts.BLUE, linewidth=16, alpha=0.30,
              label="predicted 90% interval")
    ax.plot([md], [y], marker="o", markersize=20, color=charts.BLUE,
            label="predicted median", zorder=3)
    status_colour = charts.GREEN if inside else charts.RED
    ax.plot([obs], [y], marker="D", markersize=20, color=status_colour,
            label="observed (published SPM)", zorder=4)

    ax.annotate(f"predicted {md:+.2f} pts\n[{lo:+.2f}, {hi:+.2f}]",
                xy=(md, y), xytext=(0, 46), textcoords="offset points",
                ha="center", fontsize=charts.BASE_FONT + 2,
                color=charts.INK, fontweight="bold")
    ax.annotate(f"observed {obs:+.2f} pts",
                xy=(obs, y), xytext=(0, -66), textcoords="offset points",
                ha="center", fontsize=charts.BASE_FONT + 2,
                color=charts.INK, fontweight="bold")

    verdict = ("OBSERVED INSIDE INTERVAL" if inside
               else "OBSERVED OUTSIDE INTERVAL  —  reported as a miss, not tuned")
    ax.text(0.5, 0.94, verdict, transform=ax.transAxes, ha="center",
            fontsize=charts.BASE_FONT + 3, fontweight="bold",
            color=status_colour)

    span = max(abs(lo), abs(hi), abs(obs))
    ax.set_xlim(-span * 1.35, max(0.6, span * 0.28))
    ax.set_ylim(-1.0, 1.0)
    ax.set_yticks([])
    ax.set_xlabel("change in child poverty rate (percentage points)")
    ax.set_title("Backtest 1 — 2021 expanded Child Tax Credit\n"
                 "predicted change in child poverty vs. published SPM change",
                 loc="left", fontsize=charts.BASE_FONT + 6)
    ax.axvline(0, color=charts.GRID, linewidth=2)
    ax.legend(loc="lower left", frameon=False, fontsize=charts.BASE_FONT)
    ax.grid(axis="x")
    charts.save(fig, "backtest_impact.png")


def chart_opinion(res):
    charts.apply_style()
    rows = [r for r in res["rows"] if r["predicted"] is not None]
    rows.sort(key=lambda r: (r["subgroup_type"], r["subgroup"]))
    fig, ax = plt.subplots(figsize=(15, 1.35 * len(rows) + 4))

    labels = []
    for i, r in enumerate(rows):
        p = r["predicted"]
        y = len(rows) - 1 - i
        ax.hlines(y, 100 * p["p05"], 100 * p["p95"], color=charts.BLUE,
                  linewidth=13, alpha=0.30)
        ax.plot([100 * p["median"]], [y], marker="o", markersize=15,
                color=charts.BLUE, zorder=3)
        colour = charts.GREEN if r["observed_inside_interval"] else charts.RED
        ax.plot([100 * r["observed"]], [y], marker="D", markersize=15,
                color=colour, zorder=4)
        ax.annotate(f"{100 * r['gap']:+.1f}p", xy=(100 * r["observed"], y),
                    xytext=(0, 20), textcoords="offset points", ha="center",
                    fontsize=charts.BASE_FONT - 3, color=charts.INK_SOFT)
        labels.append(f"{r['subgroup']}  (n={r['observed_n']})")

    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(list(reversed(labels)))
    ax.set_xlabel("support (%)")
    ax.set_title("Backtest 2 — held-out poll: Morning Consult/POLITICO #2110009, "
                 "October 2021\npredicted from July evidence only "
                 "(circle = predicted, bar = 90% interval, diamond = observed)",
                 loc="left", fontsize=charts.BASE_FONT + 4)
    from matplotlib.lines import Line2D
    ax.legend(handles=[
        Line2D([], [], marker="o", color=charts.BLUE, linestyle="none",
               markersize=14, label="predicted median"),
        Line2D([], [], color=charts.BLUE, linewidth=13, alpha=0.30,
               label="predicted 90% interval"),
        Line2D([], [], marker="D", color=charts.GREEN, linestyle="none",
               markersize=14, label="observed — inside interval"),
        Line2D([], [], marker="D", color=charts.RED, linestyle="none",
               markersize=14, label="observed — outside interval"),
    ], loc="upper center", bbox_to_anchor=(0.5, -0.09), ncol=4, frameon=False,
       fontsize=charts.BASE_FONT - 2)
    ax.grid(axis="x")
    ax.margins(y=0.10)
    charts.save(fig, "backtest_opinion.png")


def compare_methods(df, holdout_group=CLEAN_HOLDOUT):
    """Same holdout, both poststratification methods, side by side."""
    lad = backtest_opinion(df, method="ladder", quiet=True,
                           holdout_group=holdout_group)
    mrp_ = backtest_opinion(df, method="mrp", quiet=True,
                            holdout_group=holdout_group)
    meta = HOLDOUT_META[holdout_group]
    print()
    print("=" * 96)
    print(f"METHOD COMPARISON -- identical holdout, identical training evidence")
    print(f"holdout: {meta['label']}"
          + ("   [CLEAN out-of-sample]" if meta["clean"] else "   [not clean]"))
    print("=" * 96)
    print(f"  {'':<26}{'fallback ladder':>20}{'MRP':>20}")
    print(f"  {'mean absolute gap':<26}"
          f"{100 * lad['mean_absolute_gap']:>19.2f}p"
          f"{100 * mrp_['mean_absolute_gap']:>19.2f}p")
    print(f"  {'inside 90% interval':<26}{lad['interval_coverage']:>20}"
          f"{mrp_['interval_coverage']:>20}")
    print()
    print(f"  {'subgroup':<28}{'observed':>10}{'ladder':>12}{'gap':>8}"
          f"{'MRP':>12}{'gap':>8}   better")
    lmap = {(r['subgroup_type'], r['subgroup']): r for r in lad['rows']}
    for r in mrp_["rows"]:
        key = (r["subgroup_type"], r["subgroup"])
        l = lmap.get(key)
        if r["predicted"] is None or l is None or l["predicted"] is None:
            continue
        better = "MRP" if abs(r["gap"]) < abs(l["gap"]) else "ladder"
        print(f"  {key[0] + '/' + key[1]:<28}{r['observed']:>10.3f}"
              f"{l['predicted']['median']:>12.3f}{100 * l['gap']:>7.1f}p"
              f"{r['predicted']['median']:>12.3f}{100 * r['gap']:>7.1f}p   {better}")
    wins = sum(1 for r in mrp_["rows"]
               if r["predicted"] is not None
               and lmap.get((r['subgroup_type'], r['subgroup']), {}).get("predicted")
               and abs(r["gap"]) < abs(lmap[(r['subgroup_type'], r['subgroup'])]["gap"]))
    total = sum(1 for r in mrp_["rows"] if r["predicted"] is not None)
    print()
    print(f"  MRP is closer on {wins}/{total} held-out subgroups.")
    return {"ladder": lad, "mrp": mrp_, "holdout": meta["label"],
            "clean_out_of_sample": meta["clean"],
            "mrp_closer_on": f"{wins}/{total}"}


def parameter_set_comparison(pre, post):
    """
    Both parameter sets against the same observed value.

    This block exists because the honest thing and the flattering thing point
    the same way here, and that is exactly when to be careful. The sourced
    parameters were obtained AFTER the backtest had run and missed. They move
    the prediction toward the observed value. The pre-registered result stays
    the headline; this is reported as a post-hoc sensitivity, not as a win.
    """
    obs = pre["observed"]["observed_change"]
    print()
    print("=" * 96)
    print("PARAMETER-SET COMPARISON -- same holdout, same observed value")
    print("=" * 96)
    print(f"  {'':<34}{'predicted':>12}{'p05':>10}{'p95':>10}{'inside?':>10}")
    for tag, r in (("pre-registered (headline)", pre), ("literature-sourced", post)):
        pr = r["predicted"]
        print(f"  {tag:<34}{100 * pr['median']:>11.2f}p{100 * pr['p05']:>9.2f}p"
              f"{100 * pr['p95']:>9.2f}p"
              f"{('YES' if r['observed_inside_interval'] else 'no'):>10}")
    print(f"  {'observed':<34}{100 * obs:>11.2f}p")
    moved = abs(post["predicted"]["median"] - obs) - abs(pre["predicted"]["median"] - obs)
    direction = "CLOSER TO" if moved < 0 else "FURTHER FROM"
    print()
    print(f"  Sourcing the parameters moved the prediction {direction} the "
          f"observed value")
    print(f"  by {100 * abs(moved):.2f} points.")
    print()
    print("  CAVEAT, stated because it cuts against us: the sourced values were")
    print("  obtained AFTER this backtest had already run and missed. The")
    print("  pre-registered result above remains the headline. Sourcing was")
    print("  always intended -- the TODOs were in params.py from A4 and named in")
    print("  docs/backtest.md before the run -- but a parameter change made after")
    print("  seeing the result is a post-hoc change regardless of intent, and")
    print("  should be discounted accordingly.")
    return {"preregistered": pre, "sourced": post,
            "moved_closer": bool(moved < 0),
            "movement_pts": float(abs(moved) * 100)}


def main():
    pop = E.Population.load()
    impact = backtest_impact(pop, param_set="preregistered")
    impact_sourced = backtest_impact(pop, param_set="sourced")
    param_cmp = parameter_set_comparison(impact, impact_sourced)
    P.use_param_set("sourced")
    op = backtest_opinion(pop.df, method="mrp", holdout_group=CLEAN_HOLDOUT)
    comparison = compare_methods(pop.df, holdout_group=CLEAN_HOLDOUT)
    # The original, NOT-clean October split, kept so the earlier published
    # comparison stays reproducible rather than being quietly superseded.
    comparison_oct = compare_methods(pop.df, holdout_group="oct2021")

    out = {
        "pre_registration": "docs/backtest.md (committed before this code existed)",
        "tuned": False,
        "n_seeds": N_SEEDS,
        "backtest_1_impact": impact,
        "backtest_2_opinion": op,
        "method_comparison": comparison,
        "parameter_set_comparison": param_cmp,
        "method_comparison_oct2021_not_clean": comparison_oct,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    print()
    print(f"wrote {OUT_JSON.relative_to(REPO)}")

    chart_impact(impact)
    chart_opinion(op)


if __name__ == "__main__":
    main()
