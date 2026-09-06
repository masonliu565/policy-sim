"""
Palette and typography.

One place defines colour so the matplotlib charts, the Streamlit chrome and
the isometric map cannot drift apart. Two fonts maximum, high contrast,
sized for a projector rather than a laptop.

Day and night are the same structure with swapped values, so every consumer
can take a palette dict and stay agnostic about which is active.
"""
from __future__ import annotations

from typing import Dict

# Two fonts. A humanist sans for everything, a mono for figures so digits
# line up in columns. Each falls back through stacks that exist on macOS,
# Linux and Windows without a download.
FONT_SANS = "Avenir Next, Avenir, Segoe UI, Helvetica Neue, Arial, sans-serif"
FONT_MONO = "SF Mono, Menlo, DejaVu Sans Mono, Consolas, monospace"

# matplotlib wants family names, not CSS stacks.
MPL_SANS = ["Avenir Next", "Avenir", "Helvetica Neue", "DejaVu Sans"]
MPL_MONO = ["Menlo", "DejaVu Sans Mono"]

DAY: Dict[str, str] = {
    "name": "day",
    "ocean": "#A9D6E5",        # pastel blue
    "land": "#EADCC0",         # light beige
    "land_shadow": "#D6C3A1",  # beige, dropped — the isometric side faces
    "terrain": "#7CBF52",      # plastic green
    "terrain_shadow": "#5E9A3A",
    "panel": "#EFE4CE",        # same beige as land
    "panel_edge": "#D2BF9C",
    "ink": "#2E2A24",
    "ink_soft": "#6B6355",
    "band": "#3E7CB1",         # the p05-p95 interval bar
    "band_soft": "#9DC3DE",
    "median": "#1B3A57",       # the median marker
    "baseline": "#A6453B",     # the dashed baseline rule
    "accent": "#D98C3F",
    "warn_bg": "#F6E7C9",
    "warn_edge": "#C9A961",
}

NIGHT: Dict[str, str] = {
    "name": "night",
    "ocean": "#16283A",
    "land": "#3B3529",
    "land_shadow": "#2A281E",
    "terrain": "#4E7F38",
    "terrain_shadow": "#355926",
    "panel": "#2A2620",
    "panel_edge": "#4A4336",
    "ink": "#F0E7D5",
    "ink_soft": "#B3A88F",
    "band": "#6FA8DC",
    "band_soft": "#37576F",
    "median": "#DCEAF7",
    "baseline": "#E08A7D",
    "accent": "#E0A45E",
    "warn_bg": "#3A3020",
    "warn_edge": "#7A6636",
}

PALETTES = {"day": DAY, "night": NIGHT}


def palette(mode: str = "day") -> Dict[str, str]:
    return PALETTES.get(mode, DAY)


def apply_matplotlib(mode: str = "day") -> None:
    """Push the palette into matplotlib rcParams.

    Font sizes are set for 1280x720 projection: the smallest text in any
    figure is 12pt, axis labels 14pt, titles 16pt.
    """
    import matplotlib as mpl

    pal = palette(mode)
    mpl.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": MPL_SANS,
        "font.size": 13,
        "axes.titlesize": 16,
        "axes.labelsize": 14,
        "xtick.labelsize": 13,
        "ytick.labelsize": 14,
        "figure.facecolor": pal["panel"],
        "axes.facecolor": pal["panel"],
        "savefig.facecolor": pal["panel"],
        "text.color": pal["ink"],
        "axes.labelcolor": pal["ink"],
        "axes.edgecolor": pal["panel_edge"],
        "xtick.color": pal["ink_soft"],
        "ytick.color": pal["ink"],
        "grid.color": pal["panel_edge"],
        "grid.linewidth": 1.0,
        "axes.linewidth": 1.2,
        "lines.linewidth": 2.5,
    })
