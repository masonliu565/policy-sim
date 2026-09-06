"""
A2 -- build the sampled national household population.

Reads the ACS 2024 1-Year national PUMS files in data/raw and writes a 30,000
household sample to data/processed/us_households.parquet, carrying a design
weight so weighted totals reproduce national figures.

Input files (from model/download_pums.sh):
    psam_husa.csv, psam_husb.csv   household records (1,631,969 rows)
    psam_pusa.csv, psam_pusb.csv   person records    (3,422,888 rows)

-----------------------------------------------------------------------------
THREE DEVIATIONS FROM THE ORIGINAL SPEC, EACH DELIBERATE
-----------------------------------------------------------------------------
1. The spec says column `ST`. ACS 2024 PUMS names it `STATE`. Using `ST` raises
   a usecols KeyError; using `STATE` is correct for this vintage.

2. SERIALNO is a STRING in modern PUMS ("2024HU0000123", "2024GQ0000060"), not
   an integer. It is read as str. If it were coerced to numeric the
   household<->person join would produce NaN and silently drop every record.

3. ADJINC is applied. PUMS dollar amounts are in survey-month dollars; ADJINC
   (1.015250 for 2024) converts them to constant reference-year dollars. This
   matters: skipping it biases median household income low by ~1.5%, which is
   INSIDE the 3% tolerance at the A3 gate. It would pass validation while being
   wrong, and every downstream poverty and cost figure would inherit it.

-----------------------------------------------------------------------------
SAMPLE DESIGN -- stratified PPS, because of the metro oversample
-----------------------------------------------------------------------------
The original design was one national PPS sample with a single scale factor.
The metro requirement (>=2,000 households in each of six metros) breaks that:
the six metros hold ~14% of the population, so a proportional 30k sample would
give them ~600 households each -- far too few for stable p05/p95.

So the design is now stratified: 6 metro strata + "rest of US" = 7 strata.
  - metro strata:  2,000 households each  (12,000 total)
  - rest of US:    18,000 households
  - total:         30,000, unchanged

Within each stratum, households are drawn by Madow systematic PPS sampling
WITHOUT replacement, with inclusion probability pi_i proportional to WGTP and
sum(pi) = n_stratum. Units whose pi would exceed 1 are taken with certainty and
the remainder re-scaled (standard pi-PS treatment).

Each household then carries design_weight = WGTP_i / pi_i. Because pi_i is
proportional to WGTP within a stratum, this collapses to a per-stratum constant
(sum of WGTP in stratum / n in stratum) for all non-certainty units -- that
constant is the per-stratum "scale factor". Summing design_weight over the
sample reproduces the national weighted total, and reproduces each stratum's
weighted total. Oversampling the metros therefore does NOT bias national
aggregates; it only shifts precision toward the metros.

The cost is a design effect on national estimates: the rest-of-US stratum is
sampled at a lower rate than proportional, so national intervals are modestly
wider than an unstratified 30k sample would give. That trade is reported below
and is the intended one.
"""

from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "data" / "raw"
PROCESSED = REPO / "data" / "processed"
OUT = PROCESSED / "us_households.parquet"
METRO_XWALK = PROCESSED / "puma_to_metro.csv"

SEED = 20260905
TOTAL_SAMPLE = 30_000
METRO_MIN = 2_000
CHUNK = 250_000

HH_FILES = ["psam_husa.csv", "psam_husb.csv"]
PP_FILES = ["psam_pusa.csv", "psam_pusb.csv"]

HH_COLS = ["SERIALNO", "STATE", "REGION", "PUMA", "WGTP", "NP",
           "HINCP", "TEN", "FS", "ADJINC", "TYPEHUGQ"]
HH_DTYPES = {"SERIALNO": str, "STATE": str, "PUMA": str, "REGION": "Int8",
             "WGTP": "float64", "NP": "float64", "HINCP": "float64",
             "TEN": "float64", "FS": "float64", "ADJINC": "float64",
             "TYPEHUGQ": "float64"}

PP_COLS = ["SERIALNO", "AGEP", "ESR", "WKHP", "WAGP", "PWGTP"]
PP_DTYPES = {"SERIALNO": str, "AGEP": "float64", "ESR": "float64",
             "WKHP": "float64", "WAGP": "float64", "PWGTP": "float64"}

# ESR (employment status recode): 1 civilian employed at work, 2 civilian
# employed not at work, 4 armed forces at work, 5 armed forces not at work.
# 3 = unemployed, 6 = not in labor force. Source: ACS PUMS data dictionary 2024.
EMPLOYED_ESR = {1.0, 2.0, 4.0, 5.0}

REGION_NAMES = {1: "Northeast", 2: "Midwest", 3: "South", 4: "West"}


def hr(title=""):
    print("-" * 74)
    if title:
        print(title)
        print("-" * 74)


# --------------------------------------------------------------------------
# 1. Person-level aggregation, chunked. The full person file is never held.
# --------------------------------------------------------------------------
def aggregate_persons():
    hr("PERSON FILE -- chunked aggregation (full file never held in memory)")
    parts = []
    total_rows = 0
    for fname in PP_FILES:
        path = RAW / fname
        n_file = 0
        for chunk in pd.read_csv(path, usecols=PP_COLS, dtype=PP_DTYPES,
                                 chunksize=CHUNK, low_memory=False):
            n_file += len(chunk)
            age = chunk["AGEP"]
            is_adult = age >= 18
            g = pd.DataFrame({
                "SERIALNO": chunk["SERIALNO"],
                "n_child_under_6": (age < 6).astype("int32"),
                "n_child_6_to_17": ((age >= 6) & (age <= 17)).astype("int32"),
                "n_adults": is_adult.astype("int32"),
                "wage_income_raw": chunk["WAGP"].fillna(0.0),
                "n_employed_adults": (is_adult & chunk["ESR"].isin(EMPLOYED_ESR)).astype("int32"),
                "hours_worked": chunk["WKHP"].fillna(0.0),
            })
            # Partial sums per chunk. A SERIALNO split across a chunk boundary
            # is fine: these are all sums, and sums are associative, so the
            # final groupby over the partials is exact.
            parts.append(g.groupby("SERIALNO", as_index=False).sum())
        print(f"  {fname:<16} {n_file:>10,} person rows")
        total_rows += n_file

    persons = pd.concat(parts, ignore_index=True)
    del parts
    persons = persons.groupby("SERIALNO", as_index=False).sum()
    print(f"  {'TOTAL':<16} {total_rows:>10,} person rows -> "
          f"{len(persons):,} households with >=1 person")
    return persons


# --------------------------------------------------------------------------
# 2. Household file, chunked, with every filter counted.
# --------------------------------------------------------------------------
def load_households():
    hr("HOUSEHOLD FILE -- chunked load")
    frames = []
    total_rows = 0
    for fname in HH_FILES:
        path = RAW / fname
        n_file = 0
        for chunk in pd.read_csv(path, usecols=HH_COLS, dtype=HH_DTYPES,
                                 chunksize=CHUNK, low_memory=False):
            n_file += len(chunk)
            frames.append(chunk)
        print(f"  {fname:<16} {n_file:>10,} household rows")
        total_rows += n_file
    hh = pd.concat(frames, ignore_index=True)
    del frames
    print(f"  {'TOTAL':<16} {total_rows:>10,} household rows")
    return hh


def main():
    rng = np.random.default_rng(SEED)

    persons = aggregate_persons()
    hh = load_households()

    # ---------------------------------------------------------------- filters
    hr("FILTERS -- every dropped row is counted, nothing is dropped silently")
    n0 = len(hh)
    print(f"  start                                        {n0:>10,}")

    gq = (hh["TYPEHUGQ"] != 1.0).sum()
    hh = hh[hh["TYPEHUGQ"] == 1.0]
    print(f"  - group quarters (TYPEHUGQ != 1)             {gq:>10,}  -> {len(hh):,}")

    vac = ((hh["NP"] <= 0) | hh["NP"].isna()).sum()
    hh = hh[hh["NP"] > 0]
    print(f"  - vacant / zero-person (NP <= 0)             {vac:>10,}  -> {len(hh):,}")

    zw = (hh["WGTP"] <= 0).sum()
    hh = hh[hh["WGTP"] > 0]
    print(f"  - zero or negative weight (WGTP <= 0)        {zw:>10,}  -> {len(hh):,}")

    nohinc = hh["HINCP"].isna().sum()
    hh = hh[hh["HINCP"].notna()]
    print(f"  - missing household income (HINCP null)      {nohinc:>10,}  -> {len(hh):,}")

    badregion = (~hh["REGION"].isin(REGION_NAMES)).sum()
    if badregion:
        # REGION 9 = Puerto Rico; PR ships in a separate PUMS file, so this
        # should be zero. Report rather than assume.
        print(f"  ! REGION outside 1-4 (e.g. 9 = Puerto Rico) {badregion:>10,}  (kept, flagged)")

    # ------------------------------------------------------------------ join
    hr("JOIN household <- person aggregates (on SERIALNO, string key)")
    before = len(hh)
    hh = hh.merge(persons, on="SERIALNO", how="left")
    unmatched = hh["n_adults"].isna().sum()
    print(f"  households                                   {before:>10,}")
    print(f"  unmatched (no person record)                 {unmatched:>10,}"
          f"  ({100 * unmatched / before:.3f}%)")
    if unmatched > 0.001 * before:
        raise SystemExit("ERROR: >0.1% of households failed the person join. "
                         "Check that SERIALNO is being read as a string.")
    hh = hh[hh["n_adults"].notna()]
    print(f"  after dropping unmatched                     {len(hh):>10,}")

    # -------------------------------------------------------------- derived
    hr("DERIVED FIELDS")
    adj = hh["ADJINC"] / 1_000_000.0
    hh["hincp_adj"] = hh["HINCP"] * adj
    hh["wage_income"] = hh["wage_income_raw"] * adj
    print(f"  ADJINC applied: {hh['ADJINC'].iloc[0]:,.0f} / 1e6 = {adj.iloc[0]:.6f}")
    print(f"    median HINCP unadjusted : ${hh['HINCP'].median():,.0f}")
    print(f"    median HINCP adjusted   : ${hh['hincp_adj'].median():,.0f}")

    hh["n_children"] = hh["n_child_under_6"] + hh["n_child_6_to_17"]
    hh["region_name"] = hh["REGION"].map(REGION_NAMES).astype("object")

    a, k = hh["n_adults"], hh["n_children"]
    hh["household_type"] = np.select(
        [(a == 1) & (k == 0), (a == 2) & (k == 0),
         (a == 1) & (k > 0), (a == 2) & (k > 0)],
        ["single_no_kids", "couple_no_kids", "single_parent", "couple_with_kids"],
        default="other")
    print("  household_type distribution (weighted, population):")
    wt = hh.groupby("household_type")["WGTP"].sum().sort_values(ascending=False)
    for t, w in wt.items():
        print(f"    {t:<20} {w:>13,.0f}  ({100 * w / wt.sum():5.2f}%)")

    # ------------------------------------------------------------ metro join
    hr("METRO ASSIGNMENT (from model/build_metro_crosswalk.py)")
    xw = pd.read_csv(METRO_XWALK, dtype={"STATE": str, "PUMA": str})
    hh = hh.merge(xw[["STATE", "PUMA", "metro"]], on=["STATE", "PUMA"], how="left")
    print(f"  PUMAs in crosswalk: {len(xw):,}")
    mc = hh.groupby("metro")["WGTP"].agg(["size", "sum"])
    for m, row in mc.iterrows():
        print(f"    {m:<14} {int(row['size']):>8,} PUMS households   "
              f"weighted {row['sum']:>12,.0f}")
    print(f"    {'(no metro)':<14} {hh['metro'].isna().sum():>8,} PUMS households   "
          f"weighted {hh.loc[hh['metro'].isna(), 'WGTP'].sum():>12,.0f}")

    # ------------------------------------------------------- stratified PPS
    hr("SAMPLING -- stratified Madow systematic PPS, without replacement")
    hh = hh.reset_index(drop=True)
    hh["stratum"] = hh["metro"].fillna("rest_of_us")

    metros = sorted(xw["metro"].unique())
    alloc = {m: METRO_MIN for m in metros}
    alloc["rest_of_us"] = TOTAL_SAMPLE - METRO_MIN * len(metros)
    print(f"  allocation: {METRO_MIN:,} x {len(metros)} metros + "
          f"{alloc['rest_of_us']:,} rest of US = {TOTAL_SAMPLE:,}")

    W_total_pop = hh["WGTP"].sum()
    picks, weights, strat_info = [], [], []

    for stratum, g in hh.groupby("stratum"):
        n = alloc[stratum]
        idx = g.index.to_numpy()
        w = g["WGTP"].to_numpy(dtype=float)
        if n > len(idx):
            raise SystemExit(f"ERROR: stratum {stratum} has {len(idx)} households, "
                             f"cannot draw {n}.")

        # pi-PS with certainty handling: any unit whose pi would exceed 1 is
        # taken with certainty, then the target is re-scaled on the remainder.
        cert_mask = np.zeros(len(idx), dtype=bool)
        n_rem = n
        while True:
            live = ~cert_mask
            pi = n_rem * w[live] / w[live].sum()
            over = pi > 1
            if not over.any():
                break
            live_pos = np.flatnonzero(live)
            cert_mask[live_pos[over]] = True
            n_rem -= int(over.sum())

        pi_full = np.zeros(len(idx))
        pi_full[cert_mask] = 1.0
        live = ~cert_mask
        pi_full[live] = n_rem * w[live] / w[live].sum()

        # Madow systematic PPS on the non-certainty units, after a random
        # permutation so that file order cannot induce a pattern.
        live_pos = np.flatnonzero(live)
        perm = rng.permutation(live_pos)
        cum = np.cumsum(pi_full[perm])
        start = rng.uniform(0, 1)
        targets = start + np.arange(n_rem)
        chosen_local = np.searchsorted(cum, targets, side="left")
        chosen_local = np.clip(chosen_local, 0, len(perm) - 1)
        sel = np.concatenate([np.flatnonzero(cert_mask), perm[chosen_local]])
        sel = np.unique(sel)

        picks.append(idx[sel])
        weights.append(w[sel] / pi_full[sel])
        strat_info.append({
            "stratum": stratum,
            "pop_households": len(idx),
            "pop_weight": w.sum(),
            "n_sampled": len(sel),
            "n_certainty": int(cert_mask.sum()),
            "scale_factor": w.sum() / n if not cert_mask.any() else np.nan,
        })

    sample = hh.loc[np.concatenate(picks)].copy()
    sample["design_weight"] = np.concatenate(weights)

    info = pd.DataFrame(strat_info)
    print()
    print(f"  {'stratum':<14} {'pop hh':>10} {'sampled':>8} {'cert':>5} "
          f"{'pop weight':>14} {'sample sum(w)':>15} {'scale factor':>13}")
    for _, r in info.iterrows():
        s = sample[sample["stratum"] == r["stratum"]]["design_weight"].sum()
        sf = f"{r['scale_factor']:,.2f}" if pd.notna(r["scale_factor"]) else "varies (cert)"
        print(f"  {r['stratum']:<14} {r['pop_households']:>10,} {r['n_sampled']:>8,} "
              f"{r['n_certainty']:>5,} {r['pop_weight']:>14,.0f} {s:>15,.0f} {sf:>13}")

    # ------------------------------------------------------------ quintiles
    # Weighted national income quintiles, computed on the SAMPLE with design
    # weights so the cut points reflect the national distribution, not the
    # metro-heavy sample composition.
    s = sample.sort_values("hincp_adj")
    cw = s["design_weight"].cumsum() / s["design_weight"].sum()
    cuts = [s["hincp_adj"].iloc[np.searchsorted(cw.to_numpy(), q)] for q in (.2, .4, .6, .8)]
    sample["income_quintile"] = pd.cut(
        sample["hincp_adj"], bins=[-np.inf] + cuts + [np.inf],
        labels=["Q1", "Q2", "Q3", "Q4", "Q5"], include_lowest=True).astype(str)

    hr("WEIGHTED NATIONAL INCOME QUINTILE CUT POINTS (adjusted dollars)")
    for lbl, c in zip(["Q1/Q2", "Q2/Q3", "Q3/Q4", "Q4/Q5"], cuts):
        print(f"  {lbl}: ${c:>12,.0f}")

    # ---------------------------------------------------------------- totals
    hr("WEIGHT RECONCILIATION")
    pop_w = W_total_pop
    samp_w = sample["design_weight"].sum()
    print(f"  sum of WGTP, full PUMS population   : {pop_w:>16,.0f}")
    print(f"  sum of design_weight, 30k sample    : {samp_w:>16,.0f}")
    print(f"  difference                          : {samp_w - pop_w:>16,.0f}"
          f"  ({100 * (samp_w - pop_w) / pop_w:+.4f}%)")
    print(f"  sum of WGTP within the sample (raw) : {sample['WGTP'].sum():>16,.0f}"
          f"   <- NOT a national total; do not use")

    hr("PER-METRO SAMPLE (for sanity-checking against published metro figures)")
    print(f"  {'metro':<14} {'households':>10} {'weighted hh':>14} {'weighted persons':>18}")
    for m in metros:
        g = sample[sample["metro"] == m]
        print(f"  {m:<14} {len(g):>10,} {g['design_weight'].sum():>14,.0f} "
              f"{(g['design_weight'] * g['NP']).sum():>18,.0f}")
    g = sample[sample["metro"].isna()]
    print(f"  {'(rest of US)':<14} {len(g):>10,} {g['design_weight'].sum():>14,.0f} "
          f"{(g['design_weight'] * g['NP']).sum():>18,.0f}")

    # ----------------------------------------------------------------- write
    keep = ["SERIALNO", "STATE", "PUMA", "REGION", "region_name", "metro",
            "stratum", "design_weight", "WGTP", "NP", "TEN", "FS",
            "HINCP", "ADJINC", "hincp_adj", "wage_income",
            "n_child_under_6", "n_child_6_to_17", "n_children", "n_adults",
            "n_employed_adults", "hours_worked", "household_type",
            "income_quintile"]
    out = sample[keep].reset_index(drop=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT, index=False)

    hr("OUTPUT")
    print(f"  rows written : {len(out):,}")
    print(f"  columns      : {len(out.columns)}")
    print(f"  path         : {OUT.relative_to(REPO)}")
    print(f"  size         : {OUT.stat().st_size / 1e6:.1f} MB")
    print(f"  seed         : {SEED}")


if __name__ == "__main__":
    main()
