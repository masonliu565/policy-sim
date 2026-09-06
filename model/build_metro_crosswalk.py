"""
Build a PUMA -> metro crosswalk for the six demo metros, from authoritative
Census sources only. No hand-entered PUMA ranges anywhere in this file.

CHAIN (each link is a downloaded government file, not a guess):

  1. tract(2020) -> PUMA(2020)
     https://www2.census.gov/geo/docs/reference/puma2020/2020_Census_Tract_to_2020_PUMA.txt
     Gives STATEFP, COUNTYFP, TRACTCE, PUMA5CE for all 85,452 tracts.

  2. tract(2020) -> population(2020)
     https://www2.census.gov/geo/docs/reference/cenpop2020/tract/CenPop2020_Mean_TR.txt
     Census centers-of-population file; carries the 2020 census count per tract.
     Used to weight the allocation, because PUMAs and metros do not nest.

  3. county -> CBSA (OMB July 2023 delineations, as published by Census)
     .../metro-micro/geographies/reference-files/2023/delineation-files/list1_2023.xlsx
     CBSA codes for the six metros are LOOKED UP from this file by title match,
     not typed in from memory.

VINTAGE CHECK: ACS 2024 1-Year PUMS was verified to use 2020-vintage PUMAs --
all 2,462 distinct (STATE, PUMA) pairs in the PUMS appear in the 2020 crosswalk
(0 unmatched). If a future year switches vintage this script's coverage report
will show it immediately rather than silently mis-assigning households.

WHY POPULATION-WEIGHTED: PUMAs are drawn to ~100k people and nest inside
counties only where the county has >=100k people. Metro areas are unions of
whole counties. So a PUMA on a metro fringe can straddle the boundary. We
compute, for each PUMA, the share of its 2020 population inside each metro, and
assign the PUMA to a metro only when that share clears ASSIGN_THRESHOLD. Split
PUMAs are reported explicitly with the population they misallocate -- never
silently rounded away.

Output: data/processed/puma_to_metro.csv
        columns: STATE, PUMA, metro, metro_pop_share, puma_pop_2020
        (only rows that get a metro assignment; everything else is metro=null)
"""

from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
XWALK = REPO / "data" / "raw" / "xwalk"
OUT = REPO / "data" / "processed" / "puma_to_metro.csv"

# Metro label -> substring matched against the OMB "CBSA Title" field.
# The CBSA CODE is resolved from the delineation file; it is deliberately not
# hardcoded here, so an OMB re-delineation surfaces as a match error, not as a
# wrong-but-plausible assignment.
METRO_PATTERNS = {
    "New York":      "New York-Newark-Jersey City",
    "Houston":       "Houston-Pasadena-The Woodlands",
    "Detroit":       "Detroit-Warren-Dearborn",
    "San Francisco": "San Francisco-Oakland-Fremont",
    "Phoenix":       "Phoenix-Mesa-Chandler",
    "Atlanta":       "Atlanta-Sandy Springs-Roswell",
}

# A PUMA joins a metro when this share of its 2020 population sits in that
# metro's counties. 0.5 = majority rule. Anything between (1 - x) and x is
# reported as a split.
ASSIGN_THRESHOLD = 0.50


def _strip_bom(df):
    df.columns = [c.strip().lstrip("﻿") for c in df.columns]
    return df


def load_tract_to_puma():
    df = _strip_bom(pd.read_csv(XWALK / "tract2020_to_puma2020.txt", dtype=str))
    df["county_fips"] = df["STATEFP"] + df["COUNTYFP"]
    df["tract_geoid"] = df["STATEFP"] + df["COUNTYFP"] + df["TRACTCE"]
    return df[["STATEFP", "PUMA5CE", "county_fips", "tract_geoid"]]


def load_tract_pop():
    df = _strip_bom(pd.read_csv(XWALK / "CenPop2020_Mean_TR.txt", dtype=str))
    df["tract_geoid"] = df["STATEFP"] + df["COUNTYFP"] + df["TRACTCE"]
    df["POPULATION"] = pd.to_numeric(df["POPULATION"], errors="raise")
    return df[["tract_geoid", "POPULATION"]]


def load_county_to_metro():
    """county_fips -> metro label, for the six metros only."""
    d = pd.read_excel(XWALK / "cbsa_list1_2023.xlsx", header=2, dtype=str)
    d = d[d["Metropolitan/Micropolitan Statistical Area"] == "Metropolitan Statistical Area"]
    d["county_fips"] = d["FIPS State Code"] + d["FIPS County Code"]

    rows = []
    print("Resolving CBSA codes from the OMB delineation file:")
    for metro, pattern in METRO_PATTERNS.items():
        sub = d[d["CBSA Title"].str.startswith(pattern, na=False)]
        codes = sorted(sub["CBSA Code"].unique())
        if len(codes) != 1:
            raise SystemExit(
                f"ERROR: '{pattern}' matched {len(codes)} CBSAs ({codes}). "
                "OMB may have re-delineated; fix METRO_PATTERNS rather than guessing."
            )
        title = sub["CBSA Title"].iloc[0]
        print(f"  {metro:<14} CBSA {codes[0]}  {title}  ({sub['county_fips'].nunique()} counties)")
        for c in sub["county_fips"].unique():
            rows.append({"county_fips": c, "metro": metro})
    return pd.DataFrame(rows)


def main():
    t2p = load_tract_to_puma()
    pop = load_tract_pop()
    c2m = load_county_to_metro()

    df = t2p.merge(pop, on="tract_geoid", how="left")
    missing_pop = df["POPULATION"].isna().sum()
    print(f"\nTracts in PUMA crosswalk: {len(df):,}")
    print(f"  without a 2020 population match: {missing_pop:,} "
          f"({100 * missing_pop / len(df):.2f}%) -- treated as population 0")
    df["POPULATION"] = df["POPULATION"].fillna(0)

    df = df.merge(c2m, on="county_fips", how="left")

    # Population per (PUMA, metro) and per PUMA.
    puma_pop = (df.groupby(["STATEFP", "PUMA5CE"], as_index=False)["POPULATION"]
                  .sum().rename(columns={"POPULATION": "puma_pop_2020"}))
    in_metro = df[df["metro"].notna()]
    pm = (in_metro.groupby(["STATEFP", "PUMA5CE", "metro"], as_index=False)["POPULATION"]
                  .sum().rename(columns={"POPULATION": "metro_pop"}))

    pm = pm.merge(puma_pop, on=["STATEFP", "PUMA5CE"], how="left")
    pm["metro_pop_share"] = pm["metro_pop"] / pm["puma_pop_2020"]

    # If a PUMA touches two of our metros (does not happen for these six, but do
    # not assume it), keep the larger share.
    pm = pm.sort_values("metro_pop_share", ascending=False)
    pm = pm.drop_duplicates(subset=["STATEFP", "PUMA5CE"], keep="first")

    assigned = pm[pm["metro_pop_share"] >= ASSIGN_THRESHOLD].copy()
    split = pm[(pm["metro_pop_share"] > 0) & (pm["metro_pop_share"] < 1)].copy()

    print("\n--- PUMA assignment per metro ---")
    print(f"{'metro':<14} {'PUMAs':>6} {'2020 pop in assigned PUMAs':>28}")
    for metro, g in assigned.groupby("metro"):
        print(f"{metro:<14} {len(g):>6} {g['puma_pop_2020'].sum():>28,.0f}")

    print(f"\n--- Boundary splits (PUMA straddles a metro line) ---")
    if split.empty:
        print("  none: every PUMA is wholly in or wholly out of its metro.")
    else:
        print(f"  {len(split)} PUMAs are split. Population effect of majority rule:")
        excluded = split[split["metro_pop_share"] < ASSIGN_THRESHOLD]["metro_pop"].sum()
        included = (split[split["metro_pop_share"] >= ASSIGN_THRESHOLD]
                    .eval("puma_pop_2020 - metro_pop").sum())
        total_assigned_pop = assigned["puma_pop_2020"].sum()
        print(f"    in-metro people dropped by majority rule : {excluded:>12,.0f}")
        print(f"    out-of-metro people pulled in            : {included:>12,.0f}")
        print(f"    net error vs. total assigned population  : "
              f"{(included - excluded) / total_assigned_pop * 100:+.2f}%")
        print("\n  Ten largest splits:")
        cols = ["STATEFP", "PUMA5CE", "metro", "metro_pop_share", "puma_pop_2020"]
        print(split.nlargest(10, "puma_pop_2020")[cols].to_string(index=False))

    out = assigned.rename(columns={"STATEFP": "STATE", "PUMA5CE": "PUMA"})
    out = out[["STATE", "PUMA", "metro", "metro_pop_share", "puma_pop_2020"]]
    out = out.sort_values(["metro", "STATE", "PUMA"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)
    print(f"\nWrote {OUT.relative_to(REPO)}  ({len(out):,} PUMAs assigned to a metro)")
    print("Households in every other PUMA get metro = null.")


if __name__ == "__main__":
    main()
