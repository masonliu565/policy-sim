"""
Fetch the DC evidence this backend answers from, and cache it to disk.

The Juniper atlas front end ships with its own map data and expects a local
service at /api/v1. This builds the evidence side of that service from public
sources, once, so the running app needs no network and no database:

    311            DC GIS service-request layers, counted server-side by ward
                   and service type. Requests, not people.
    tract health   CDC PLACES small-area estimates for DC census tracts, with
                   the publisher's own confidence limits carried through.
    household income
                   Computed here from the ACS 2024 1-Year PUMS already in
                   data/raw, restricted to occupied DC households, using the
                   same ADJINC adjustment and household weights as the rest of
                   the model. Not re-downloaded.

Nothing is estimated in this file. Every number written to the cache is either
a published figure or a weighted count of ACS records.

    python dc_api/build_cache.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(__file__).resolve().parent / "cache"
UA = "policy-sim/1.0 (local evidence cache builder)"

SR = ("https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/"
      "ServiceRequests/MapServer")
PLACES = "https://data.cdc.gov/resource/cwsq-ngmh.json"

# DC GIS publishes one layer per year.
SR_LAYERS = {2023: 15, 2024: 16, 2025: 18}


def get(url: str, params: dict | None = None, tries: int = 3):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:                      # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise SystemExit(f"ERROR fetching {url[:110]}: {last}")


# ---------------------------------------------------------------------------
def service_requests():
    """Ward and ward-by-service counts, aggregated on the server."""
    out = {}
    for year, layer in SR_LAYERS.items():
        base = f"{SR}/{layer}/query"
        by_ward = get(base, {
            "where": "1=1", "f": "json",
            "groupByFieldsForStatistics": "WARD",
            "outStatistics": json.dumps([{
                "statisticType": "count", "onStatisticField": "SERVICEREQUESTID",
                "outStatisticFieldName": "N"}]),
        })
        wards = {}
        for feat in by_ward.get("features", []):
            a = feat["attributes"]
            wards[str(a.get("WARD") or "unassigned")] = int(a["N"])

        # ward x service type, paged
        services, offset = {}, 0
        while True:
            page = get(base, {
                "where": "1=1", "f": "json", "resultOffset": offset,
                "resultRecordCount": 1000,
                "groupByFieldsForStatistics": "WARD,SERVICECODEDESCRIPTION",
                "outStatistics": json.dumps([{
                    "statisticType": "count",
                    "onStatisticField": "SERVICEREQUESTID",
                    "outStatisticFieldName": "N"}]),
            })
            feats = page.get("features", [])
            for feat in feats:
                a = feat["attributes"]
                w = str(a.get("WARD") or "unassigned")
                s = (a.get("SERVICECODEDESCRIPTION") or "unspecified").strip()
                services.setdefault(w, {})[s] = int(a["N"])
            if len(feats) < 1000:
                break
            offset += 1000
            if offset > 20000:
                break
        total = sum(wards.values())
        out[str(year)] = {"byWard": wards, "byWardService": services,
                          "total": total, "layer": layer}
        print(f"  311 {year}: {total:,} requests, {len(wards)} wards, "
              f"{sum(len(v) for v in services.values())} ward-service pairs")
    return out


def places():
    """CDC PLACES tract estimates for DC, every measure and year available."""
    rows, offset = [], 0
    while True:
        page = get(PLACES, {
            "stateabbr": "DC", "$limit": 5000, "$offset": offset,
            "$select": ("year,locationname,measureid,measure,data_value,"
                        "low_confidence_limit,high_confidence_limit,"
                        "totalpopulation,totalpop18plus,data_value_type,"
                        "datasource,short_question_text"),
        })
        rows.extend(page)
        if len(page) < 5000:
            break
        offset += 5000
    by_tract = {}
    measures = {}
    for r in rows:
        v = r.get("data_value")
        if v is None:
            continue
        key = f"{r['locationname']}|{r['measureid']}|{r['year']}"
        by_tract[key] = {
            "value": float(v),
            "low": float(r["low_confidence_limit"]) if r.get("low_confidence_limit") else None,
            "high": float(r["high_confidence_limit"]) if r.get("high_confidence_limit") else None,
            "pop18": int(float(r["totalpop18plus"])) if r.get("totalpop18plus") else None,
            "type": r.get("data_value_type"), "source": r.get("datasource"),
        }
        measures[r["measureid"]] = {"measure": r.get("measure"),
                                    "short": r.get("short_question_text")}
    years = sorted({r["year"] for r in rows})
    print(f"  PLACES: {len(by_tract):,} tract-measure-year rows, "
          f"{len(measures)} measures, years {years}")
    return {"byTract": by_tract, "measures": measures, "years": years}


def household_income():
    """
    DC household income distribution from the ACS 2024 1-Year PUMS.

    Same treatment as the rest of the model: occupied housing units only
    (TYPEHUGQ 1, NP > 0, WGTP > 0), income adjusted to constant 2024 dollars
    by ADJINC. Stored as a weighted, sorted income vector so any threshold can
    be answered exactly rather than from pre-cut bands.
    """
    import pandas as pd

    frames = []
    for name in ("psam_husa.csv", "psam_husb.csv"):
        path = REPO / "data" / "raw" / name
        if not path.exists():
            raise SystemExit(f"ERROR: {path} missing. Run model/download_pums.sh.")
        for chunk in pd.read_csv(
                path, usecols=["STATE", "PUMA", "WGTP", "NP", "TYPEHUGQ",
                               "HINCP", "ADJINC"],
                dtype={"STATE": str, "PUMA": str}, chunksize=400_000,
                low_memory=False):
            d = chunk[(chunk.STATE == "11") & (chunk.TYPEHUGQ == 1)
                      & (chunk.NP > 0) & (chunk.WGTP > 0) & chunk.HINCP.notna()]
            if len(d):
                frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    df["income"] = df["HINCP"] * df["ADJINC"] / 1_000_000.0
    df = df.sort_values("income")
    total_w = float(df["WGTP"].sum())
    by_puma = {}
    for puma, g in df.groupby("PUMA"):
        by_puma[puma] = {
            "incomes": [round(float(x), 2) for x in g["income"]],
            "weights": [float(x) for x in g["WGTP"]],
        }
    print(f"  ACS DC: {len(df):,} household records, "
          f"{total_w:,.0f} weighted households, {len(by_puma)} PUMAs")
    return {
        "year": 2024,
        "records": int(len(df)),
        "weighted": total_w,
        "incomes": [round(float(x), 2) for x in df["income"]],
        "weights": [float(x) for x in df["WGTP"]],
        "byPuma": by_puma,
        "adjinc": float(df["ADJINC"].iloc[0]) / 1_000_000.0,
    }


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    print("Building the DC evidence cache. Sources are fetched once; the "
          "running service needs no network.\n")

    print("311 service requests (DC GIS)")
    sr = service_requests()
    (CACHE / "service_requests.json").write_text(
        json.dumps(sr, separators=(",", ":")), encoding="utf-8")

    print("\nTract health (CDC PLACES)")
    pl = places()
    (CACHE / "places.json").write_text(
        json.dumps(pl, separators=(",", ":")), encoding="utf-8")

    print("\nHousehold income (ACS 2024 1-Year PUMS, already local)")
    hi = household_income()
    (CACHE / "household_income.json").write_text(
        json.dumps(hi, separators=(",", ":")), encoding="utf-8")

    manifest = {
        "builtAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sources": [
            {"datasetId": "dc-311-service-requests",
             "publisher": "DC GIS / Office of the Chief Technology Officer",
             "url": SR, "referencePeriod": "2023-2025",
             "note": "Counted server-side by ward and service type."},
            {"datasetId": "cdc-places-dc-tracts",
             "publisher": "Centers for Disease Control and Prevention, PLACES",
             "url": "https://data.cdc.gov/resource/cwsq-ngmh",
             "referencePeriod": ", ".join(pl["years"]),
             "note": "Model-based small-area estimates with publisher confidence limits."},
            {"datasetId": "acs-2024-pums-dc-households",
             "publisher": "U.S. Census Bureau",
             "url": "https://www2.census.gov/programs-surveys/acs/data/pums/2024/1-Year/",
             "referencePeriod": "2024",
             "note": "Occupied DC households, ADJINC-adjusted, household weights."},
        ],
    }
    (CACHE / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                         encoding="utf-8")
    total = sum(p.stat().st_size for p in CACHE.glob("*.json"))
    print(f"\nwrote {CACHE.relative_to(REPO)}  ({total / 1024:,.0f} KB)")


if __name__ == "__main__":
    main()
