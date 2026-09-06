"""
Regenerate the continental-US outline that app/geo.py hands to the map.

WHY
The outline used to be traced by hand -- about sixty points, eyeballed. Tiled,
it did not read as the United States. This replaces it with the Census Bureau's
own dissolved national boundary, simplified to a node count the tiler can
handle, in exactly the same (lon, lat) convention app/geo.py already uses. No
other part of the app changes.

    source  U.S. Census Bureau cartographic boundary file, nation, 1:20m
            https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_nation_20m.zip
            The largest ring of that file is the contiguous US, including the
            Great Lakes shoreline, so the lakes fall outside the tiled interior
            the way they should.

City markers are the population-weighted centroid of the tracts inside each
metro's PUMAs, from the 2020 centers-of-population file -- not coordinates
typed from memory.

    python model/build_us_outline.py        # rewrites the block in app/geo.py
"""

import math
import re
import sys
from pathlib import Path

import pandas as pd
import shapefile

REPO = Path(__file__).resolve().parent.parent
SHP = REPO / "data" / "raw" / "geo" / "cb_2023_us_nation_20m"
XWALK = REPO / "data" / "raw" / "xwalk"
METRO_CSV = REPO / "data" / "processed" / "puma_to_metro.csv"
GEO_PY = REPO / "app" / "geo.py"

TARGET_POINTS = 340     # the tiler stays smooth to roughly this node count

METRO_LABELS = {
    "new_york": "New York", "houston": "Houston", "detroit": "Detroit",
    "san_francisco": "San Francisco", "phoenix": "Phoenix", "atlanta": "Atlanta",
}


def rdp(points, eps):
    if len(points) < 3:
        return points
    x0, y0 = points[0]
    x1, y1 = points[-1]
    dx, dy = x1 - x0, y1 - y0
    norm = math.hypot(dx, dy)
    idx, dmax = 0, -1.0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        d = (abs(dy * px - dx * py + x1 * y0 - y1 * x0) / norm) if norm \
            else math.hypot(px - x0, py - y0)
        if d > dmax:
            idx, dmax = i, d
    if dmax > eps:
        return rdp(points[:idx + 1], eps)[:-1] + rdp(points[idx:], eps)
    return [points[0], points[-1]]


def outline():
    if not SHP.with_suffix(".shp").exists():
        raise SystemExit(f"ERROR: {SHP}.shp missing. Download and unzip:\n"
                         "  https://www2.census.gov/geo/tiger/GENZ2023/shp/"
                         "cb_2023_us_nation_20m.zip")
    sys.setrecursionlimit(20000)
    shp = shapefile.Reader(str(SHP)).shapes()[0]
    parts = list(shp.parts) + [len(shp.points)]
    rings = [shp.points[a:b] for a, b in zip(parts[:-1], parts[1:])]
    # The contiguous US is the largest ring; AK, HI and every island are their
    # own rings and are not part of this outline.
    ring = max(rings, key=len)
    lo, hi = 0.0, 1.0
    for _ in range(40):                      # binary search the tolerance
        mid = (lo + hi) / 2
        got = rdp(ring, mid)
        if len(got) > TARGET_POINTS:
            lo = mid
        else:
            hi = mid
    pts = rdp(ring, hi)
    if pts[0] == pts[-1]:
        pts = pts[:-1]
    print(f"  outline: {len(ring):,} points -> {len(pts)} at tolerance {hi:.5f}")
    return [(round(x, 2), round(y, 2)) for x, y in pts]


def cities():
    t2p = pd.read_csv(XWALK / "tract2020_to_puma2020.txt", dtype=str)
    t2p.columns = [c.strip().lstrip("﻿") for c in t2p.columns]
    pop = pd.read_csv(XWALK / "CenPop2020_Mean_TR.txt", dtype=str)
    pop.columns = [c.strip().lstrip("﻿") for c in pop.columns]
    for df in (t2p, pop):
        df["key"] = df["STATEFP"] + df["COUNTYFP"] + df["TRACTCE"]
    for c in ("POPULATION", "LATITUDE", "LONGITUDE"):
        pop[c] = pd.to_numeric(pop[c], errors="coerce")
    xw = pd.read_csv(METRO_CSV, dtype={"STATE": str, "PUMA": str})
    j = (t2p.merge(xw[["STATE", "PUMA", "metro"]], left_on=["STATEFP", "PUMA5CE"],
                   right_on=["STATE", "PUMA"], how="inner")
             .merge(pop[["key", "POPULATION", "LATITUDE", "LONGITUDE"]],
                    on="key", how="inner"))
    out = []
    for name, g in j.groupby("metro"):
        w = g["POPULATION"].to_numpy()
        lat = float((g["LATITUDE"] * w).sum() / w.sum())
        lon = float((g["LONGITUDE"] * w).sum() / w.sum())
        key = name.lower().replace(" ", "_")
        out.append((key, name, round(lon, 2), round(lat, 2), len(g)))
        print(f"  {name:<14} {len(g):>5} tracts -> {lat:.2f}, {lon:.2f}")
    return sorted(out, key=lambda r: r[1])


def main():
    pts = outline()
    cs = cities()

    body = ["US_OUTLINE: List[Tuple[float, float]] = ["]
    for i in range(0, len(pts), 4):
        body.append("    " + " ".join(f"({x}, {y})," for x, y in pts[i:i + 4]))
    body.append("]")

    cbody = ["CITIES: List[Dict[str, object]] = ["]
    for key, label, lon, lat, n in cs:
        cbody.append(f'    {{"metro_key": "{key}", "label": "{label}", '
                     f'"lon": {lon}, "lat": {lat}}},   # {n:,} tracts')
    cbody.append("]")

    src = GEO_PY.read_text(encoding="utf-8")
    src = re.sub(r"US_OUTLINE: List\[Tuple\[float, float\]\] = \[.*?\n\]",
                 "\n".join(body), src, flags=re.S)
    src = re.sub(r"CITIES: List\[Dict\[str, object\]\] = \[.*?\n\]",
                 "\n".join(cbody), src, flags=re.S)
    src = src.replace(
        "# Simplified continental-US outline, (lon, lat), traced clockwise from the\n"
        "# Pacific Northwest. Deliberately coarse: the map is a schematic, not a\n"
        "# reference atlas, and the tiling below quantises it further anyway.",
        "# Continental-US outline, (lon, lat). GENERATED by\n"
        "# model/build_us_outline.py from the U.S. Census Bureau cartographic\n"
        "# boundary file cb_2023_us_nation_20m -- the largest ring of the dissolved\n"
        "# national boundary, simplified by Ramer-Douglas-Peucker. It was previously\n"
        "# traced by hand at about sixty points and did not read as the United\n"
        "# States once tiled. Do not hand-edit; re-run the script.")
    src = src.replace(
        "# City markers. `metro_key` is the key looked up in the scenario JSON's",
        "# City markers. GENERATED: each is the population-weighted centroid of the\n"
        "# tracts inside that metro's PUMAs, from the 2020 centers-of-population\n"
        "# file, not a coordinate typed from memory.\n"
        "# `metro_key` is the key looked up in the scenario JSON's")
    GEO_PY.write_text(src, encoding="utf-8")
    print(f"\nrewrote {GEO_PY.relative_to(REPO)}: {len(pts)} outline points, "
          f"{len(cs)} cities")


if __name__ == "__main__":
    main()
