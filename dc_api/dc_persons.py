"""
DC person records from the ACS PUMS, kept at the person level.

dc_engine.py sums the person file into household totals, because that is what
the microsimulation needs. That sum throws away the thing a lot of policy
questions are actually about: what one worker earns. A minimum wage question
cannot be answered from a household's combined wage income, and answering it
from the household figure anyway would be a wrong number rather than a missing
one.

So this keeps the person rows -- age, wage income, usual hours, employment
status and the person weight -- for the District only, and derives an implied
hourly wage where the record supports one. Same PUMS files, same ADJINC
adjustment to constant 2024 dollars, same filters.

Built once into data/processed/dc_persons.parquet and reused.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

PARQUET = REPO / "data" / "processed" / "dc_persons.parquet"
COLS = ["SERIALNO", "ST", "AGEP", "WAGP", "WKHP", "WKWN", "ESR", "PWGTP", "ADJINC"]
# ESR 1,2,4,5 are the employed codes; 3 is unemployed, 6 not in labour force.
EMPLOYED = {1.0, 2.0, 4.0, 5.0}
WEEKS_PER_YEAR = 52


def build() -> "Any":
    import pandas as pd

    raw = REPO / "data" / "raw"
    parts = []
    for name in ("psam_pusa.csv", "psam_pusb.csv"):
        path = raw / name
        if not path.exists():
            continue
        head = pd.read_csv(path, nrows=0)
        state_col = "ST" if "ST" in head.columns else "STATE"
        use = [c if c != "ST" else state_col for c in COLS]
        use = [c for c in use if c in head.columns]
        for chunk in pd.read_csv(path, usecols=use, dtype={"SERIALNO": str},
                                 chunksize=500_000, low_memory=False):
            d = chunk[chunk[state_col].astype(str).str.zfill(2) == "11"]
            if len(d):
                parts.append(d.rename(columns={state_col: "ST"}))
    if not parts:
        raise FileNotFoundError(
            "No DC person records found. Download the PUMS files first: "
            "bash model/download_pums.sh")

    df = pd.concat(parts, ignore_index=True)
    adj = df["ADJINC"] / 1_000_000.0
    df["wage_income"] = df["WAGP"].fillna(0.0) * adj
    df["hours"] = df["WKHP"].fillna(0.0)
    # WKWN is weeks worked in the past 12 months. Without it an implied hourly
    # wage divides an annual wage by a full year of usual hours, so someone who
    # worked three months at 40 hours looks like a quarter-wage worker. That
    # error is what puts records below any minimum wage, so use the real weeks
    # where the record reports them.
    df["weeks"] = df["WKWN"] if "WKWN" in df.columns else float("nan")
    df["weeks"] = df["weeks"].where(df["weeks"].between(1, 52))
    df["employed"] = df["ESR"].isin(EMPLOYED)
    df["adult"] = df["AGEP"] >= 18
    df["weight"] = df["PWGTP"].fillna(0.0)

    # An implied hourly wage only means something for someone who reports both
    # wage income and usual hours. Everyone else is left null rather than
    # divided by zero and quietly counted as earning nothing.
    ok = (df["hours"] > 0) & (df["wage_income"] > 0) & df["employed"] & df["weeks"].notna()
    df["hourly_wage"] = (df["wage_income"] / (df["hours"] * df["weeks"])).where(ok)

    out = df[["SERIALNO", "AGEP", "wage_income", "hours", "weeks", "employed",
              "adult", "weight", "hourly_wage"]].copy()
    PARQUET.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(PARQUET, index=False)
    return out


_DF = None


def persons() -> Optional["Any"]:
    """The DC person table, or None if the PUMS files are not present."""
    global _DF
    if _DF is None:
        try:
            import pandas as pd
            _DF = pd.read_parquet(PARQUET) if PARQUET.exists() else build()
        except Exception:                                       # noqa: BLE001
            _DF = False
    return None if _DF is False else _DF


def wage_earners() -> Optional["Any"]:
    df = persons()
    if df is None:
        return None
    return df[df["hourly_wage"].notna()]


def summary() -> Dict[str, Any]:
    """Weighted DC wage facts, or {} when the person file is unavailable."""
    import numpy as np

    df = wage_earners()
    if df is None or not len(df):
        return {}
    w = df["weight"].to_numpy(dtype=float)
    wage = df["hourly_wage"].to_numpy(dtype=float)
    order = np.argsort(wage)
    wage_s, w_s = wage[order], w[order]
    cum = np.cumsum(w_s) / w_s.sum()
    pct = {q: float(wage_s[int(np.searchsorted(cum, q))]) for q in (.1, .25, .5, .75, .9)}
    return {
        "records": int(len(df)),
        "weighted_workers": float(w.sum()),
        "median_hourly_wage": pct[.5],
        "percentiles": pct,
        "annual_wage_median": float(np.median(df["wage_income"])),
    }


def workers_under(hourly: float) -> Optional[Dict[str, Any]]:
    """Weighted count of DC wage earners with an implied hourly wage below a
    threshold. Returns None rather than a zero when the file is missing."""
    df = wage_earners()
    if df is None or not len(df):
        return None
    w = df["weight"].to_numpy(dtype=float)
    under = (df["hourly_wage"].to_numpy(dtype=float) < float(hourly))
    return {"threshold": float(hourly), "workers": float(w[under].sum()),
            "share": float(w[under].sum() / w.sum()), "records": int(under.sum()),
            "denominator": float(w.sum())}


if __name__ == "__main__":
    print(f"building {PARQUET} ...")
    d = build()
    print(f"{len(d):,} DC person records")
    print(summary())
