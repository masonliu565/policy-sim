"""
Run the microsimulation on DC households.

The national sample carries only a handful of DC households, because DC is
about 0.25% of the country and the sample is drawn proportionally. Answering a
DC question off those records would be a made-up number with a real interval
printed next to it, which is worse than not answering.

So this builds a DC-specific population from the same ACS 2024 PUMS files the
national one uses -- all 3,083 occupied DC household records, with the same
filters, the same ADJINC adjustment, the same PWGTP-derived person and child
weights -- and runs the same engine over it. Nothing about the engine changes;
only the population it is handed.

Built once into data/processed/dc_households.parquet and reused.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

REPO = Path(__file__).resolve().parent.parent
MODEL = REPO / "model"
for p in (str(REPO), str(MODEL)):
    if p not in sys.path:
        sys.path.insert(0, p)

PARQUET = REPO / "data" / "processed" / "dc_households.parquet"

HH_COLS = ["SERIALNO", "STATE", "REGION", "PUMA", "WGTP", "NP", "HINCP",
           "TEN", "FS", "ADJINC", "TYPEHUGQ"]
PP_COLS = ["SERIALNO", "AGEP", "ESR", "WKHP", "WAGP", "PWGTP"]
EMPLOYED = {1.0, 2.0, 4.0, 5.0}


def build() -> "Any":
    import numpy as np
    import pandas as pd

    raw = REPO / "data" / "raw"
    hh = []
    for name in ("psam_husa.csv", "psam_husb.csv"):
        for c in pd.read_csv(raw / name, usecols=HH_COLS,
                             dtype={"SERIALNO": str, "STATE": str, "PUMA": str},
                             chunksize=400_000, low_memory=False):
            d = c[(c.STATE == "11") & (c.TYPEHUGQ == 1) & (c.NP > 0)
                  & (c.WGTP > 0) & c.HINCP.notna()]
            if len(d):
                hh.append(d)
    hh = pd.concat(hh, ignore_index=True)
    keep = set(hh["SERIALNO"])

    parts = []
    for name in ("psam_pusa.csv", "psam_pusb.csv"):
        for c in pd.read_csv(raw / name, usecols=PP_COLS, dtype={"SERIALNO": str},
                             chunksize=500_000, low_memory=False):
            c = c[c.SERIALNO.isin(keep)]
            if not len(c):
                continue
            age = c["AGEP"]
            adult = age >= 18
            parts.append(pd.DataFrame({
                "SERIALNO": c["SERIALNO"],
                "n_child_under_6": (age < 6).astype("int32"),
                "n_child_6_to_17": ((age >= 6) & (age <= 17)).astype("int32"),
                "n_adults": adult.astype("int32"),
                "wage_income_raw": c["WAGP"].fillna(0.0),
                "n_employed_adults": (adult & c["ESR"].isin(EMPLOYED)).astype("int32"),
                "hours_worked": c["WKHP"].fillna(0.0),
                "pwgtp_sum": c["PWGTP"].fillna(0.0),
                "pwgtp_child_sum": c["PWGTP"].fillna(0.0).where(age < 18, 0.0),
                "pwgtp_adult_sum": c["PWGTP"].fillna(0.0).where(adult, 0.0),
            }).groupby("SERIALNO", as_index=False).sum())
    people = pd.concat(parts, ignore_index=True).groupby("SERIALNO", as_index=False).sum()

    df = hh.merge(people, on="SERIALNO", how="inner")
    adj = df["ADJINC"] / 1_000_000.0
    df["hincp_adj"] = df["HINCP"] * adj
    df["wage_income"] = df["wage_income_raw"] * adj
    df["n_children"] = df["n_child_under_6"] + df["n_child_6_to_17"]
    df["region_name"] = "South"          # the Census places DC in the South
    df["metro"] = None

    a, k = df["n_adults"], df["n_children"]
    df["household_type"] = np.select(
        [(a == 1) & (k == 0), (a == 2) & (k == 0), (a == 1) & (k > 0),
         (a == 2) & (k > 0)],
        ["single_no_kids", "couple_no_kids", "single_parent", "couple_with_kids"],
        default="other")

    # Every DC household is in the sample, so the design weight IS the ACS
    # household weight: there is no second stage to correct for.
    df["design_weight"] = df["WGTP"].astype(float)
    ratio = df["design_weight"] / df["WGTP"]
    df["person_weight"] = ratio * df["pwgtp_sum"]
    df["child_weight"] = ratio * df["pwgtp_child_sum"]
    df["adult_weight"] = ratio * df["pwgtp_adult_sum"]

    s = df.sort_values("hincp_adj")
    cw = s["design_weight"].cumsum() / s["design_weight"].sum()
    cuts = [s["hincp_adj"].iloc[int(np.searchsorted(cw.to_numpy(), q))]
            for q in (.2, .4, .6, .8)]
    df["income_quintile"] = pd.cut(
        df["hincp_adj"], bins=[-np.inf] + list(cuts) + [np.inf],
        labels=["Q1", "Q2", "Q3", "Q4", "Q5"], include_lowest=True).astype(str)

    PARQUET.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(PARQUET, index=False)
    return df


_POP = None


def population():
    global _POP
    if _POP is None:
        import pandas as pd
        import engine as E
        df = pd.read_parquet(PARQUET) if PARQUET.exists() else build()
        _POP = E.Population(df)
    return _POP


_CACHE: Dict[tuple, Dict[str, Any]] = {}


def run_dc(levers: Dict[str, Any], seeds: int = 300) -> Dict[str, Any]:
    key = (seeds,) + tuple(sorted((k, str(v)) for k, v in levers.items()))
    if key in _CACHE:
        return _CACHE[key]

    import engine as E

    spec = dict(levers)
    for side in ("phaseout_start_single", "phaseout_start_joint"):
        if spec.get(side) is None:
            spec[side] = float("inf")

    pop = population()
    res = E.run(spec, "dc_policy", "DC policy", n_seeds=seeds, pop=pop)
    # The per-group impacts are the point of the exercise -- who the policy
    # reaches, not just the headline -- so they are kept, not dropped.
    res["by_group"] = res.pop("_by_group_impact", [])
    res["n_households"] = pop.n
    res["n_seeds"] = seeds
    res["warnings"] = [
        "Simulated on DC households only, from the ACS 2024 1-Year PUMS.",
        "Federal poverty thresholds are applied without any cost-of-living "
        "adjustment; DC is an expensive place and this understates hardship.",
    ] + list(res.get("warnings", []))
    _CACHE[key] = res
    return res
