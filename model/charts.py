"""
Shared chart styling. Every figure in /docs is produced through this module so
the convergence plot and the two backtest charts read as one system.

DESIGN DECISIONS, and why:

  Projector legibility is the binding constraint. Base font 17pt, titles 23pt,
  line width 2.6, markers 10. Figures are sized so that at typical projection a
  value label is roughly the height of a line of body text on a slide.

  Palette is the validated categorical set, assigned by role and in fixed slot
  order -- never cycled, never a generated hue:
      slot 1 blue   #2a78d6   p05 / predicted
      slot 2 orange #eb6834   p95
      slot 8 red    #e34948   status: observed outside the interval
      green         #008300   status: observed inside the interval
  Status colors are reserved for exactly that and always ship with a text label,
  never color alone.

  No dual axes anywhere. The four convergence outcomes are on different scales
  (rates vs. dollars), so they are SMALL MULTIPLES with independent y-axes --
  four panels, not two y-scales on one panel.

  Identity is never carried by color alone: every series is also direct-labeled
  at the right edge, and every status marker carries text.

  Text uses ink tokens (near-black / grey), never the series color.
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SOFT = "#52514e"
GRID = "#dcdbd6"

BLUE = "#2a78d6"     # slot 1
ORANGE = "#eb6834"   # slot 2
AQUA = "#1baf7a"     # slot 3
RED = "#e34948"      # slot 8 -- status only
GREEN = "#008300"    # status only

BASE_FONT = 17


def apply_style():
    plt.rcParams.update({
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "font.size": BASE_FONT,
        "axes.titlesize": BASE_FONT + 4,
        "axes.labelsize": BASE_FONT,
        "xtick.labelsize": BASE_FONT - 2,
        "ytick.labelsize": BASE_FONT - 2,
        "legend.fontsize": BASE_FONT - 1,
        "axes.edgecolor": GRID,
        "axes.labelcolor": INK,
        "text.color": INK,
        "xtick.color": INK_SOFT,
        "ytick.color": INK_SOFT,
        "grid.color": GRID,
        "grid.linewidth": 1.0,
        "axes.grid": True,
        "axes.grid.axis": "y",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "lines.linewidth": 2.6,
        "lines.markersize": 10,
        "figure.autolayout": False,
        # Dollar signs in titles and tick labels must render as dollar signs.
        # With mathtext parsing on, "$49 at 50 seeds -> $76" becomes italic
        # maths between the two $ delimiters. Off, globally.
        "text.parse_math": False,
    })


def save(fig, name):
    DOCS.mkdir(parents=True, exist_ok=True)
    path = DOCS / name
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    size_kb = path.stat().st_size / 1024
    print(f"  saved {path.relative_to(REPO)}  ({size_kb:,.0f} KB)")
    return path
