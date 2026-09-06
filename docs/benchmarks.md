# Historical benchmarks, and what we checked before trusting them

Six historical policy cases came in from the atlas repo's
`codex/dc-policy-sandbox-benchmarks` branch: a policy, the question it poses,
the outcome that actually happened, and the confounds that make scoring it
hard. The methodology is sound and it matches ours — pre-policy inputs are
separated from post-policy outcomes by a cutoff date, so a later result can
score a forecast but cannot inform it.

They were also machine-generated, and **a citation that looks right is the
specific failure this project spends its effort on**. So before anything was
imported, every source URL was fetched and compared against the document it
claims to be, and every scored figure was looked for in the source it cites.

**16 sources, 23 figures. 14 sources and 20 figures held up.** Nothing was
wholesale invented — every URL points at a real document, no phantom reports,
no invented authors — and the hardest figures to fake were verbatim matches
with exact locators. But three things were wrong, and one of them mattered a
lot.

| Case | Sources | Figures | Status |
|---|---|---|---|
| nyc-universal-pre-k | 3/3 | 3/3 | verified |
| stockton-seed | 2/2 | 2/2 | verified |
| seattle-minimum-wage | 2/2 | 2/2 | verified |
| dc-paid-family-leave | 2/3 | 4/4 | verified, URL and attribution repaired |
| dc-disposable-bag-fee | 3/4 | 4/5 | **one target removed as fabricated** |
| aca-medicaid-expansion | 2/2 | 2/4 | **blocked for scoring** |

## The fabricated target

`dc-disposable-bag-fee` claimed `three_year_reusable_bag_household_share =
0.80`, with a locator asserting the 2013 DOEE release reports that 80 percent
of residents carry reusable bags at least some of the time.

**The release says no such thing, in any form.** It contains no reusable-bag
statistic at all — its figures are 80 percent reducing disposable bag use,
10→4 bags a week, 79 percent of businesses, and the support and concern splits.
The claimed value is simply its sibling target's `0.80` duplicated. A figure of
79 percent for reusable-bag carrying does circulate elsewhere, attributed to
the OpinionWorks survey, but we did not confirm it either.

So the target is **removed, not corrected**. Substituting a number we also
have not verified would repeat the original error with better manners.

## The case that cannot score a forecast

`aca-medicaid-expansion` reads four decimal values off **an unlabelled bar
chart** — Figure 5 of Census P60-253. None of the four appear as text anywhere
in the report, and the narrative never states them. Measuring the bars against
the calibrated axis confirms two (34.8 and 25.5) but puts the `43.0` baseline
at about 43.3, which also contradicts the chart's own difference panel: that
panel shows roughly a 4.8 point drop where the manifest's numbers imply 4.6.
The `38.4` measures about 38.47.

Two of four values, one of them demonstrably off, all of them measured rather
than read. That is documentation, not a scoring target. The case is kept and
marked `scoringBlocked`, and it can be unblocked when the values are re-derived
from a source that states them in text.

## The smaller repairs

- **A 404.** The DC paid leave statute cited `subchapters/IV-A`, which does not
  exist; the Universal Paid Leave provisions are in `IV`. The manifest's own
  section numbers (§§ 32-541.03–32-541.08) were right, so this was a broken
  link rather than a fake law. Repaired.
- **Two misattributions.** The LA County bag study was labelled a government
  report; it is a consultant report by AECOM for Sapphos Environmental, hosted
  on the county DPW site. The 2015 DC leave study lost its authorship: Jeffrey
  Hayes, Institute for Women's Policy Research. Both corrected, because
  "government report" is doing real work inside an evidence class.
- **A scope caveat.** Table 16's volume column is headed "Plastic Bags (est.)"
  while the manifest describes covered paper *and* plastic bags. Recorded as a
  confound rather than silently accepted.

## What did verify, and it is worth saying plainly

The administrative figures are exact and are the hardest kind to fabricate:
**59,568,000 bags** and **$2,382,571.20** of first-year receipts, verbatim in
AECOM Table 16 with the 270 million pre-policy baseline and the 78 percent
reduction. DC paid leave claims — **8,430 parental, 2,901 medical, 848 family,
12,179 total** — are exact from Table 1 of the DOES FY21 Q4 report, and the
manifest's window matches the report's own framing. Stockton SEED's
**28%→40% treatment against 32%→37% control** is verbatim, and it carries its
control group, which is what makes it an experimental result rather than a
number. NYC pre-K is exemplary, right down to correct page numbers.

`dc_api/test_benchmarks.py` holds this in place: every target must cite a
source that exists, no source may carry the broken URL, the removed target must
stay removed, the blocked case must stay blocked, and every input source must
predate its case's cutoff date — the firewall the whole design rests on.

Re-run the import with `python dc_api/import_benchmarks.py <simcity-checkout>`.

## What was NOT taken

The same branch rewires the atlas's chat panel to POST `/api/v1/sandbox/agent-runs`
against a Node + Postgres + OpenAI service, instead of `/api/v1/query` against
this one. Taking it would disconnect the entire Python backend — the ACS
microdata, the published DC tax schedule, the pre-registered backtest, the
fabrication guard — and the app would look fine right up until it asked for an
OpenAI key. The atlas is therefore pinned in `dc_api/setup_frontend.sh` to the
commit before that branch, so the change cannot arrive silently through a pull.

The evidence was worth taking. The second engine was not.
