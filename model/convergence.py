"""
A4 convergence check -- where do p05 and p95 stop moving?

Runs the engine at 50 / 100 / 250 / 500 / 1000 seeds and plots the p05, median
and p95 of each of the four contract outcomes against seed count. The question
the chart answers is narrow and worth stating out loud when presenting: at how
many seeds does the INTERVAL stop moving? The median stabilises early and tells
you almost nothing; the tails are what cost seeds.

Output: docs/convergence.png  (four small multiples, independent y-axes -- the
outcomes are on different scales, and a dual-axis chart would be a lie)
"""

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import charts  # noqa: E402
import engine as E  # noqa: E402
from matplotlib import pyplot as plt  # noqa: E402

SEED_COUNTS = [50, 100, 250, 500, 1000]
PRODUCTION_SEEDS = 500

PANELS = [
    ("child_poverty_rate", "Child poverty rate", "pct"),
    ("overall_poverty_rate", "Overall poverty rate", "pct"),
    ("median_disposable_income", "Median disposable income", "usd"),
    ("annual_cost_usd", "Annual fiscal cost", "bn"),
]


def fmt(kind):
    if kind == "pct":
        return lambda v: f"{100 * v:.1f}%"
    if kind == "usd":
        return lambda v: f"${v:,.0f}"
    return lambda v: f"${v / 1e9:.0f}B"


def main():
    pop = E.Population.load()
    results = {}
    print(f"Convergence check on {pop.n:,} households")
    for n in SEED_COUNTS:
        t0 = time.perf_counter()
        draws = E.draw_params(n)
        transfer, new_income = E.simulate(pop, E.CTC_2021, draws)
        results[n] = E.outcomes_for(pop, transfer, new_income)
        print(f"  {n:>5} seeds  {time.perf_counter() - t0:>6.2f}s")

    charts.apply_style()
    fig, axes = plt.subplots(2, 2, figsize=(19, 12))
    fig.suptitle("Convergence: where the interval stops moving\n"
                 "2021 expanded CTC, 30,000 households",
                 fontsize=charts.BASE_FONT + 8, y=0.99)

    x = np.array(SEED_COUNTS, dtype=float)
    for ax, (key, title, kind) in zip(axes.ravel(), PANELS):
        f = fmt(kind)
        p05 = np.array([results[n][key]["p05"] for n in SEED_COUNTS])
        p95 = np.array([results[n][key]["p95"] for n in SEED_COUNTS])
        med = np.array([results[n][key]["median"] for n in SEED_COUNTS])

        ax.fill_between(x, p05, p95, color=charts.BLUE, alpha=0.10, linewidth=0)
        ax.plot(x, p95, color=charts.ORANGE, marker="o", label="p95")
        ax.plot(x, med, color=charts.INK_SOFT, marker="o", linestyle="--",
                linewidth=2.0, label="median")
        ax.plot(x, p05, color=charts.BLUE, marker="o", label="p05")

        ax.axvline(PRODUCTION_SEEDS, color=charts.GRID, linewidth=2.0, zorder=0)
        ax.annotate(f"{PRODUCTION_SEEDS} seeds\n(production)",
                    xy=(PRODUCTION_SEEDS, ax.get_ylim()[0]),
                    xytext=(6, 8), textcoords="offset points",
                    fontsize=charts.BASE_FONT - 4, color=charts.INK_SOFT, va="bottom")

        # Direct labels at the right edge so identity is never colour-alone.
        for series, colour, name in ((p95, charts.ORANGE, "p95"),
                                     (p05, charts.BLUE, "p05")):
            ax.annotate(f"{name}  {f(series[-1])}",
                        xy=(x[-1], series[-1]), xytext=(10, 0),
                        textcoords="offset points", va="center",
                        fontsize=charts.BASE_FONT - 3, color=charts.INK,
                        fontweight="bold")

        width_first = p95[0] - p05[0]
        width_last = p95[-1] - p05[-1]
        ax.set_title(f"{title}\ninterval width {f(width_first)} at 50 seeds "
                     f"-> {f(width_last)} at 1000", loc="left",
                     fontsize=charts.BASE_FONT + 1)
        ax.set_xscale("log")
        ax.set_xticks(SEED_COUNTS)
        ax.set_xticklabels([str(s) for s in SEED_COUNTS])
        ax.set_xlabel("seeds (log scale)")
        ax.yaxis.set_major_formatter(lambda v, _, f=f: f(v))
        ax.margins(x=0.22)

    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=3, frameon=False,
               bbox_to_anchor=(0.5, 0.925), fontsize=charts.BASE_FONT)
    fig.tight_layout(rect=[0, 0, 1, 0.90])
    charts.save(fig, "convergence.png")

    print("\nInterval width by seed count (p95 - p05):")
    hdr = "  " + "outcome".ljust(30) + "".join(f"{n:>12}" for n in SEED_COUNTS)
    print(hdr)
    for key, title, kind in PANELS:
        f = fmt(kind)
        widths = [results[n][key]["p95"] - results[n][key]["p05"] for n in SEED_COUNTS]
        print("  " + title.ljust(30) + "".join(f"{f(w):>12}" for w in widths))
    print("\n  Read this as: the interval is converged when the last two columns "
          "agree.")


if __name__ == "__main__":
    main()
