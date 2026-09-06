"""
Contract tests for the imported historical benchmarks.

These exist because the manifests arrived machine-generated, with citations
that looked right and mostly were -- but not entirely. One target asserted a
figure its cited source does not contain, in any form. That is the failure mode
this project is built to catch, so the checks that caught it are written down
here rather than left as something someone did once.

    python -m pytest dc_api/test_benchmarks.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "data" / "benchmarks"

IDS = ["aca-medicaid-expansion", "dc-disposable-bag-fee", "dc-paid-family-leave",
       "nyc-universal-pre-k", "seattle-minimum-wage", "stockton-seed"]


def load(mid):
    return json.loads((BENCH / mid / "manifest.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("mid", IDS)
def test_every_benchmark_records_how_it_was_checked(mid):
    """An imported citation with no verification record is exactly as good as
    an unverified one."""
    v = load(mid)["verification"]
    assert v["checkedOn"] and v["method"] and v["findings"]
    assert v["sourcesVerified"] <= v["sourcesTotal"]
    assert v["figuresConfirmed"] <= v["figuresTotal"]


@pytest.mark.parametrize("mid", IDS)
def test_every_target_cites_a_source_that_exists_in_the_manifest(mid):
    m = load(mid)
    ids = {s["id"] for s in m["sources"]}
    for t in m["targets"]:
        assert t["sourceId"] in ids, f"{t['id']} cites missing source"


@pytest.mark.parametrize("mid", IDS)
def test_no_source_url_is_the_broken_one(mid):
    """The DC paid leave statute cited subchapter IV-A, which 404s and does not
    exist. The provisions are in IV."""
    for s in load(mid)["sources"]:
        assert "subchapters/IV-A" not in s["url"]
        assert s["url"].startswith("https://")


def test_the_fabricated_bag_fee_target_is_gone():
    """It claimed 80% of residents carry reusable bags, citing a DOEE release
    that contains no reusable-bag statistic at all, with a value that merely
    duplicated its sibling target."""
    m = load("dc-disposable-bag-fee")
    assert all(t["id"] != "three_year_reusable_bag_household_share"
               for t in m["targets"])
    removed = m["verification"]["removedTargets"]
    assert removed and "FABRICATED" in removed[0]["reason"]


def test_the_bag_fee_figures_that_did_verify_are_intact():
    """Removing the bad target must not have disturbed the good ones."""
    t = {x["id"]: x["observation"]["value"] for x in load("dc-disposable-bag-fee")["targets"]}
    assert t["first_year_disposable_bags"] == 59_568_000
    assert t["first_year_bag_fee_revenue"] == pytest.approx(2_382_571.2)
    assert t["first_year_bag_volume_reduction"] == pytest.approx(0.78)


def test_the_aca_case_cannot_be_used_to_score_a_forecast():
    """Its four values were measured off an unlabelled bar chart and one of
    them fails the chart's own internal consistency check."""
    m = load("aca-medicaid-expansion")
    assert m["scoringBlocked"] is True
    assert m["modelStatus"] == "documentation_only"


def test_the_cases_cleared_for_scoring_are_the_ones_that_fully_verified():
    scoreable = [m for m in IDS if not load(m).get("scoringBlocked")]
    assert set(scoreable) == {"dc-disposable-bag-fee", "dc-paid-family-leave",
                              "nyc-universal-pre-k", "seattle-minimum-wage",
                              "stockton-seed"}


def test_the_cutoff_predates_every_outcome_source():
    """The whole point of the design: a post-policy result may score a forecast
    but must not be able to inform it."""
    for mid in IDS:
        m = load(mid)
        cutoff = m["cutoffDate"]
        for s in m["sources"]:
            if s.get("role") == "input":
                assert s["publishedAt"] <= cutoff, \
                    f"{mid}: input {s['id']} published after the cutoff"


def test_stockton_carries_the_control_group():
    """A treatment number without its control is not an experimental result."""
    t = {x["id"]: x["observation"]["value"] for x in load("stockton-seed")["targets"]}
    assert t["recipient_full_time_employment"] == 40
    assert t["control_full_time_employment"] == 37
