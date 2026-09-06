"""
Interval rendering.

THE RULE: no number reaches the screen without its uncertainty. There is no
function in this module that renders a point estimate, and there must never
be one. `interval_text` is the only text formatter, and it always emits the
p05-p95 band alongside the median.

Every impact chart draws three things:
  - a thick horizontal bar spanning p05 to p95
  - a high-contrast marker at the median
  - a dashed vertical rule at the baseline

Bar widths are never normalised across charts. A metro interval is wider than
the national one because a metro subsample carries more sampling error; making
those bars the same length would hide exactly the thing the bands exist to show.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from app.theme import apply_matplotlib, palette

# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
PERCENT, CURRENCY, NUMBER = "percent", "currency", "number"


def infer_unit(key: str) -> str:
    """Guess a display unit from an outcome key.

    Inferred rather than hardcoded per outcome so that an outcome Track A adds
    later still formats sensibly instead of needing a change here.
    """
    k = key.lower()
    if k.endswith("_rate") or k.endswith("_share") or k.startswith("pct_") or k.endswith("_pct"):
        return PERCENT
    if "usd" in k or "income" in k or "cost" in k or "dollar" in k or "credit" in k:
        return CURRENCY
    return NUMBER


def _money(v: float) -> str:
    a = abs(v)
    if a >= 1e12:
        return f"${v / 1e12:,.2f}T"
    if a >= 1e9:
        return f"${v / 1e9:,.1f}B"
    if a >= 1e6:
        return f"${v / 1e6:,.1f}M"
    return f"${v:,.0f}"


def format_value(v: float, unit: str) -> str:
    if unit == PERCENT:
        return f"{v * 100:.1f}%"
    if unit == CURRENCY:
        return _money(v)
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:,.2f}"


def interval_text(band: Dict[str, float], unit: str) -> str:
    """The ONLY text formatter in the app. Always carries the interval."""
    return (
        f"{format_value(band['median'], unit)} "
        f"[{format_value(band['p05'], unit)} – {format_value(band['p95'], unit)}]"
    )


def format_delta(v: float, unit: str) -> str:
    """A signed change. Percent deltas are points, not percentages of a rate."""
    sign = "+" if v >= 0 else "\u2212"
    if unit == PERCENT:
        return f"{sign}{abs(v) * 100:.1f} pts"
    return f"{sign}{format_value(abs(v), unit)}"


def delta_text(band: Dict[str, float], unit: str) -> Optional[str]:
    """Change vs baseline, itself expressed as an interval.

    Bounds keep their own signs and are ordered low-to-high: subtracting a
    baseline can flip which of p05/p95 is the smaller change, and an interval
    printed backwards or unsigned reads as a different number entirely.
    """
    if "baseline" not in band or band["baseline"] is None:
        return None
    b = band["baseline"]
    lo, hi = sorted((band["p05"] - b, band["p95"] - b))
    mid = band["median"] - b
    return (
        f"{format_delta(mid, unit)} "
        f"[{format_delta(lo, unit)} \u2013 {format_delta(hi, unit)}] vs baseline"
    )


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def interval_figure(
    rows: Sequence[Dict[str, Any]],
    unit: str = NUMBER,
    mode: str = "day",
    xlabel: str = "",
    height_per_row: float = 0.92,
    width: float = 9.0,
    show_baseline: bool = True,
) -> Figure:
    """Horizontal p05-p95 bars with median markers and a dashed baseline.

    `rows` items: {"label": str, "p05": f, "median": f, "p95": f,
                   "baseline": f | None, "note": str | None}
    """
    apply_matplotlib(mode)
    pal = palette(mode)

    n = max(1, len(rows))
    fig, ax = plt.subplots(figsize=(width, max(1.7, 0.95 + n * height_per_row)))

    ys = list(range(n - 1, -1, -1))  # first row on top

    for y, row in zip(ys, rows):
        p05, med, p95 = float(row["p05"]), float(row["median"]), float(row["p95"])

        # The interval bar. Thick enough to read across a room.
        ax.plot([p05, p95], [y, y], color=pal["band"], linewidth=13,
                solid_capstyle="butt", zorder=2, alpha=0.85)
        # End caps make the bounds unambiguous where bars are short.
        for x in (p05, p95):
            ax.plot([x, x], [y - 0.20, y + 0.20], color=pal["median"],
                    linewidth=2.5, zorder=3)
        # Median marker: a light slab against the dark bar, high contrast.
        ax.plot([med], [y], marker="|", markersize=26, markeredgewidth=5,
                color=pal["panel"], zorder=4)
        ax.plot([med], [y], marker="|", markersize=26, markeredgewidth=2.2,
                color=pal["median"], zorder=5)

    # Dashed baseline rule. Drawn per-row when baselines differ, once across
    # the axes when they agree, so a shared reference reads as one line.
    if show_baseline:
        bases = [r.get("baseline") for r in rows if r.get("baseline") is not None]
        if bases and len(set(bases)) == 1:
            ax.axvline(bases[0], color=pal["baseline"], linestyle="--",
                       linewidth=2.4, zorder=1, label="baseline")
        else:
            for y, row in zip(ys, rows):
                if row.get("baseline") is not None:
                    ax.plot([row["baseline"]] * 2, [y - 0.42, y + 0.42],
                            color=pal["baseline"], linestyle="--",
                            linewidth=2.4, zorder=1)

    ax.set_yticks(ys)
    ax.set_yticklabels([r["label"] for r in rows], fontsize=14)
    ax.set_ylim(-0.75, n - 0.25)

    lo = min(float(r["p05"]) for r in rows)
    hi = max(float(r["p95"]) for r in rows)
    if show_baseline:
        bases = [r.get("baseline") for r in rows if r.get("baseline") is not None]
        if bases:
            lo, hi = min(lo, *bases), max(hi, *bases)
    pad = (hi - lo) * 0.18 or (abs(hi) * 0.12 or 1.0)
    ax.set_xlim(lo - pad, hi + pad)

    if unit == PERCENT:
        ax.xaxis.set_major_formatter(lambda v, _: f"{v * 100:.0f}%")
    elif unit == CURRENCY:
        ax.xaxis.set_major_formatter(lambda v, _: _money(v))

    ax.set_xlabel(xlabel or _default_xlabel(unit))
    ax.grid(axis="x", alpha=0.5, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    return fig


def _default_xlabel(unit: str) -> str:
    return {
        PERCENT: "rate (dashed line = baseline, bar = 5th–95th percentile)",
        CURRENCY: "US dollars (dashed line = baseline, bar = 5th–95th percentile)",
    }.get(unit, "value (dashed line = baseline, bar = 5th–95th percentile)")


def impact_row(name: str, band: Dict[str, float], label: Optional[str] = None) -> Dict[str, Any]:
    return {
        "label": label or name.replace("_", " "),
        "p05": band["p05"], "median": band["median"], "p95": band["p95"],
        "baseline": band.get("baseline"),
    }


def close(fig: Figure) -> None:
    plt.close(fig)
