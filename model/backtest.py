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


def backtest_impact(pop):
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
    print("BACKTEST 1 -- material impact: change in child poverty rate")
    print("=" * 96)
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

    return {"predicted": pred, "observed": obs, "observed_inside_interval": inside,
            "sensitivity": sens, "tuned": False}


# ---------------------------------------------------------------------------
# backtest 2
# ---------------------------------------------------------------------------
def backtest_opinion(df):
    """Predict held-out subgroups from non-holdout evidence only."""
    overall, _ids, by_group, warns = O.poststratify(df, n_draws=N_SEEDS,
                                                    include_holdout=False)
    pred_lookup = {}
    if overall is not None:
        pred_lookup[("national", "national")] = overall
    for g in by_group:
        if g["support"] is not None:
            pred_lookup[(g["group_type"], g["group"])] = g["support"]

    held, _ = O.load_evidence(include_holdout=True)
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

    print()
    print("=" * 96)
    print("BACKTEST 2 -- opinion: held-out Morning Consult #2110009 (Oct 2021)")
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
    print("  Interpretation, written before the numbers were seen (docs/backtest.md):")
    print("  the region crosstabs never enter the model -- every population cell")
    print("  resolves at the income_band level -- so regional predictions are")
    print("  population composition, not measured regional opinion. Flat regional")
    print("  predictions against varying observed values are the expected")
    print("  consequence of that, not a surprise.")

    return {"rows": rows, "mean_absolute_gap": mae,
            "interval_coverage": f"{hits}/{scored}", "warnings": warns}


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


def main():
    pop = E.Population.load()
    impact = backtest_impact(pop)
    op = backtest_opinion(pop.df)

    out = {
        "pre_registration": "docs/backtest.md (committed before this code existed)",
        "tuned": False,
        "n_seeds": N_SEEDS,
        "backtest_1_impact": impact,
        "backtest_2_opinion": op,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    print()
    print(f"wrote {OUT_JSON.relative_to(REPO)}")

    chart_impact(impact)
    chart_opinion(op)


if __name__ == "__main__":
    main()
