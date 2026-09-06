"""
Geometry for the isometric map.

Nothing in this module is an estimate. These are cartographic coordinates and
projection constants — decorative geometry for the map view. No policy number,
support percentage or impact figure ever lives here; those come only from
/scenarios/*.json.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Simplified continental-US outline, (lon, lat), traced clockwise from the
# Pacific Northwest. Deliberately coarse: the map is a schematic, not a
# reference atlas, and the tiling below quantises it further anyway.
# ---------------------------------------------------------------------------
US_OUTLINE: List[Tuple[float, float]] = [
    # Pacific coast, north to south
    (-124.7, 48.4), (-124.1, 46.9), (-124.4, 43.3), (-124.2, 41.8),
    (-123.8, 40.4), (-122.9, 38.9), (-121.9, 36.6), (-120.6, 34.5),
    (-118.4, 33.7), (-117.1, 32.5),
    # Mexican border, west to east
    (-114.7, 32.7), (-111.1, 31.3), (-108.2, 31.3), (-106.5, 31.8),
    (-104.9, 29.3), (-102.8, 29.8), (-101.4, 29.8), (-99.1, 26.4),
    (-97.1, 25.9),
    # Gulf coast, west to east
    (-97.4, 27.9), (-95.1, 29.1), (-93.8, 29.7), (-91.2, 29.2),
    (-89.4, 29.2), (-88.0, 30.4), (-85.8, 30.2), (-84.3, 30.0),
    (-82.8, 28.9), (-82.7, 27.5), (-81.8, 26.0), (-80.4, 25.2),
    # Atlantic coast, south to north
    (-80.1, 26.9), (-81.0, 29.2), (-81.4, 30.7), (-80.9, 32.1),
    (-79.2, 33.3), (-77.9, 34.2), (-75.8, 35.2), (-76.3, 36.9),
    (-75.1, 38.5), (-74.0, 40.5), (-71.9, 41.3), (-70.1, 41.7),
    (-70.8, 43.1), (-67.0, 44.8),
    # Northern border east to west, biting in around the Great Lakes
    (-69.2, 47.5), (-71.5, 45.0), (-74.7, 45.0), (-76.5, 44.2),
    (-79.0, 43.3), (-81.5, 42.1), (-83.1, 41.7), (-82.5, 43.0),
    (-83.0, 44.0), (-83.6, 45.0), (-84.7, 45.8), (-85.5, 45.0),
    (-86.5, 44.0), (-86.2, 42.4), (-87.6, 41.6), (-87.8, 42.5),
    (-87.0, 45.0), (-88.0, 46.8), (-90.4, 46.6), (-92.3, 46.7),
    (-95.2, 49.0), (-123.0, 49.0),
]

# ---------------------------------------------------------------------------
# City markers. `metro_key` is the key looked up in the scenario JSON's
# "by_metro" object; if that key is absent the marker renders in an explicit
# "no metro estimate available" state rather than showing anything invented.
# ---------------------------------------------------------------------------
CITIES: List[Dict[str, object]] = [
    {"metro_key": "new_york",     "label": "New York",      "lon": -74.01, "lat": 40.71},
    {"metro_key": "houston",      "label": "Houston",       "lon": -95.37, "lat": 29.76},
    {"metro_key": "detroit",      "label": "Detroit",       "lon": -83.05, "lat": 42.33},
    {"metro_key": "san_francisco","label": "San Francisco", "lon": -122.42, "lat": 37.77},
    {"metro_key": "phoenix",      "label": "Phoenix",       "lon": -112.07, "lat": 33.45},
    {"metro_key": "atlanta",      "label": "Atlanta",       "lon": -84.39, "lat": 33.75},
]

# Standard-parallel-ish correction so the map is not stretched east-west.
REF_LAT = 39.0
# Degrees of longitude/latitude per isometric tile. Smaller = chunkier detail
# and more SVG nodes; 0.7 keeps the node count near 3k, which stays smooth.
TILE_DEG = 0.7


def plane(lon: float, lat: float) -> Tuple[float, float]:
    """Equirectangular projection with a cosine correction at REF_LAT."""
    return lon * math.cos(math.radians(REF_LAT)), lat


def map_config() -> Dict[str, object]:
    """Everything the frontend needs to draw and place things on the map."""
    pts = [plane(lon, lat) for lon, lat in US_OUTLINE]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return {
        "outline": [list(p) for p in pts],
        "bounds": {"x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys)},
        "tile": TILE_DEG * math.cos(math.radians(REF_LAT)),
        "tile_y": TILE_DEG,
        "cities": [
            {
                "metro_key": c["metro_key"],
                "label": c["label"],
                "x": plane(float(c["lon"]), float(c["lat"]))[0],
                "y": plane(float(c["lon"]), float(c["lat"]))[1],
            }
            for c in CITIES
        ],
    }
