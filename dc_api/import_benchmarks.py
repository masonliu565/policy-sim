"""
Import the historical policy benchmarks from the atlas PR, after checking them.

WHERE THESE CAME FROM. The upstream branch codex/dc-policy-sandbox-benchmarks
(kp224/simcity) added six benchmark manifests: a policy, the question it poses,
the outcome that actually happened, and the confounds that make scoring hard.
The methodology is good and matches ours -- pre-policy inputs are separated
from post-policy outcomes by a cutoff date, so a later result can score a
forecast but cannot inform it.

WHY THIS FILE EXISTS RATHER THAN A COPY. The manifests were machine-generated,
and citations that look right are the specific failure this project spends its
effort on. So every source URL and every scored figure was fetched and checked
against the actual document before anything was imported. 14 of 16 sources and
20 of 23 figures held up. What did not is corrected here, in the open:

  * dc-disposable-bag-fee carried a FABRICATED target. Its
    three_year_reusable_bag_household_share claimed 0.80 with a locator
    asserting the DOEE release says 80 percent of residents carry reusable bags
    at least some of the time. The release says no such thing in any form -- it
    has no reusable-bag statistic at all -- and the value is its sibling
    target's 0.80 duplicated. The target is REMOVED, not corrected: the figure
    circulating elsewhere is 79 percent and we did not confirm it either.

  * aca-medicaid-expansion reads four decimals off an UNLABELLED bar chart
    (P60-253 Figure 5). Two were confirmed by measuring the bars against the
    calibrated axis; the 43.0 baseline measures ~43.3 and fails the chart's own
    difference panel, and 38.4 measures ~38.47. None of the four appear as text
    anywhere in the report. The case is kept as documentation and BLOCKED for
    scoring.

  * dc-paid-family-leave's statute URL 404s: subchapter IV-A does not exist and
    the provisions are in IV. A broken link, not a fake law -- the manifest's
    own section numbers were right. Fixed.

  * Two sources carried paraphrased titles that misattributed authorship. The
    LA County bag study is a consultant report by AECOM for Sapphos
    Environmental, not a government report; the 2015 DC leave study is by
    Jeffrey Hayes at the Institute for Women's Policy Research. Both corrected,
    because "government report" is doing work in an evidence class.

Everything imported carries a verification block saying what was checked. Run:

    python dc_api/import_benchmarks.py <path-to-the-simcity-checkout>
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict

REPO = Path(__file__).resolve().parent.parent
DEST = REPO / "data" / "benchmarks"
CHECKED_ON = "2026-09-06"

# id -> what the audit found, and what we do about it.
AUDIT: Dict[str, Dict[str, Any]] = {
    "nyc-universal-pre-k": {
        "status": "verified",
        "sourcesVerified": 3, "sourcesTotal": 3,
        "figuresConfirmed": 3, "figuresTotal": 3,
        "notes": ["Every source resolved and every figure was found verbatim, "
                  "with correct page numbers. Enrollment 53,000 and 68,500 are "
                  "on page 29 of the NYC Social Indicators Report; the 5 "
                  "percentage point maternal labour force effect is the "
                  "abstract of Census working paper CES-25-62."],
    },
    "dc-paid-family-leave": {
        "status": "verified_with_fixes",
        "sourcesVerified": 2, "sourcesTotal": 3,
        "figuresConfirmed": 4, "figuresTotal": 4,
        "notes": ["Claim counts 8,430 parental, 2,901 medical, 848 family and "
                  "12,179 total are exact, from Table 1 of the DOES FY21 Q4 "
                  "report, and the manifest's window matches the report's own "
                  "framing.",
                  "The statute URL returned 404 and was repaired: the Universal "
                  "Paid Leave provisions are in subchapter IV, not IV-A.",
                  "The 2015 study's authorship was restored: Jeffrey Hayes, "
                  "Institute for Women's Policy Research."],
    },
    "stockton-seed": {
        "status": "verified",
        "sourcesVerified": 2, "sourcesTotal": 2,
        "figuresConfirmed": 2, "figuresTotal": 2,
        "notes": ["Both employment figures are verbatim on the SEED findings "
                  "page: recipients 28 percent to 40 percent full-time over the "
                  "year, control 32 percent to 37 percent. The design (125 "
                  "residents, $500 a month, 24 months) matches the locator "
                  "word for word."],
    },
    "seattle-minimum-wage": {
        "status": "verified",
        "sourcesVerified": 2, "sourcesTotal": 2,
        "figuresConfirmed": 2, "figuresTotal": 2,
        "notes": ["The ordinance and the DOL CLEAR review both resolved, and "
                  "the direction-only targets (wages up, hours down) match the "
                  "review, as does its low causal evidence rating. Recording "
                  "directions rather than magnitudes is the right call here: "
                  "the Seattle literature disagrees on magnitude."],
    },
    "dc-disposable-bag-fee": {
        "status": "verified_with_removal",
        "sourcesVerified": 3, "sourcesTotal": 4,
        "figuresConfirmed": 4, "figuresTotal": 5,
        "removedTargets": [{
            "id": "three_year_reusable_bag_household_share",
            "claimed": 0.80,
            "reason": "FABRICATED. The locator asserts the 2013 DOEE release "
                      "reports 80 percent of residents carrying reusable bags "
                      "at least some of the time. The release contains no "
                      "reusable-bag statistic at all; the value duplicates the "
                      "sibling target's 0.80. A figure of 79 percent circulates "
                      "elsewhere and was not confirmed either, so the target is "
                      "removed rather than corrected."}],
        "notes": ["The administrative figures are exact and are the hardest "
                  "kind to fake: 59,568,000 bags and $2,382,571.20 of receipts "
                  "are verbatim in Table 16, along with the 270 million "
                  "pre-policy baseline and the 78 percent reduction.",
                  "The source is a consultant report (AECOM for Sapphos "
                  "Environmental) hosted by LA County, not a government "
                  "report; its evidence class was corrected.",
                  "Table 16's column is headed 'Plastic Bags (est.)' while the "
                  "manifest describes covered paper and plastic bags. Recorded "
                  "as a confound."],
    },
    "aca-medicaid-expansion": {
        "status": "blocked_for_scoring",
        "sourcesVerified": 2, "sourcesTotal": 2,
        "figuresConfirmed": 2, "figuresTotal": 4,
        "notes": ["Both sources are genuine: Public Law 111-148 and Census "
                  "P60-253, and Figure 5 is on the cited page.",
                  "But none of the four values appear as text anywhere in the "
                  "report. Figure 5 is an unlabelled bar chart, and the figures "
                  "were obtained by measuring bar heights. Two check out "
                  "(34.8 and 25.5). The 43.0 baseline measures about 43.3 and "
                  "contradicts the chart's own difference panel, which shows "
                  "roughly 4.8 points against the 4.6 the manifest implies; "
                  "38.4 measures about 38.47.",
                  "Kept as documentation. Not usable for scoring a forecast "
                  "until the values are re-derived from a source that states "
                  "them in text."],
    },
}

FIXES = {
    "dc-paid-family-leave": {
        "sources": {
            "dc-pfl-law": {
                "url": "https://code.dccouncil.gov/us/dc/council/code/titles/32/"
                       "chapters/5/subchapters/IV",
                "_fix": "URL repaired: subchapter IV-A returned 404 and does "
                        "not exist; the provisions cited in the locator "
                        "(SS 32-541.03 to 32-541.08) are in subchapter IV.",
            },
            "dc-pfl-2015-simulation": {
                "title": "Final Report on the Costs and Benefits of Paid Family "
                         "and Medical Leave in the District of Columbia",
                "author": "Jeffrey Hayes, Institute for Women's Policy Research",
                "_fix": "Title and authorship restored from the document itself.",
            },
        },
    },
    "dc-disposable-bag-fee": {
        "sources": {
            "la-dpw-dc-bag-admin-2010": {
                "title": "Economic Impact Analysis: Proposed Ban on Plastic "
                         "Carryout Bags in Los Angeles County",
                "author": "AECOM Technical Services, prepared for Sapphos "
                          "Environmental",
                "evidenceClass": "consultant_report_citing_dc_administrative_data",
                "_fix": "Title, authorship and evidence class corrected. The "
                        "manifest described this as a government report; it is "
                        "a consultant report hosted on the LA County DPW site. "
                        "It does cite DC CFO administrative data, and the "
                        "figures taken from it were confirmed.",
            },
        },
        "addConfounds": [
            "Table 16's volume column is headed 'Plastic Bags (est.)', while "
            "this manifest describes the target as covered paper and plastic "
            "bags. The scope of the administrative figure may be narrower than "
            "the policy's coverage.",
        ],
    },
}


def apply(mid: str, m: Dict[str, Any]) -> Dict[str, Any]:
    audit = AUDIT[mid]
    fixes = FIXES.get(mid, {})

    for sid, patch in fixes.get("sources", {}).items():
        for src in m.get("sources", []):
            if src.get("id") == sid:
                src.update(patch)

    removed = {r["id"] for r in audit.get("removedTargets", [])}
    if removed:
        m["targets"] = [t for t in m["targets"] if t["id"] not in removed]

    if fixes.get("addConfounds"):
        m["confounds"] = list(m.get("confounds", [])) + fixes["addConfounds"]

    if audit["status"] == "blocked_for_scoring":
        m["modelStatus"] = "documentation_only"
        m["scoringBlocked"] = True

    m["verification"] = {
        "checkedOn": CHECKED_ON,
        "method": "Every source URL was fetched and compared against the "
                  "document it claims to be, and every scored figure was "
                  "looked for in the source it cites.",
        **{k: v for k, v in audit.items() if k != "notes"},
        "findings": audit["notes"],
    }
    return m


def main(src_root: Path) -> int:
    src = src_root / "policy-engine" / "benchmarks"
    if not src.is_dir():
        print(f"ERROR: {src} not found.")
        return 1
    DEST.mkdir(parents=True, exist_ok=True)
    for mid in sorted(AUDIT):
        f = src / mid / "manifest.json"
        if not f.exists():
            print(f"ERROR: {f} missing")
            return 1
        m = apply(mid, json.loads(f.read_text(encoding="utf-8")))
        out = DEST / mid
        out.mkdir(parents=True, exist_ok=True)
        (out / "manifest.json").write_text(
            json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        v = m["verification"]
        print(f"{mid:<26} {v['status']:<24} "
              f"sources {v['sourcesVerified']}/{v['sourcesTotal']}  "
              f"figures {v['figuresConfirmed']}/{v['figuresTotal']}"
              + (f"  REMOVED {len(v.get('removedTargets', []))}"
                 if v.get("removedTargets") else ""))
    return 0


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "frontend"
    raise SystemExit(main(root))
