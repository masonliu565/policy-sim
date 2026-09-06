# Track A — Model, Validation, Backtests

Every step: paste the prompt, run what it gives you, check the acceptance box before moving on. Don't skip acceptance checks — they're the only thing standing between you and a demo built on wrong numbers.

**Directory you own:** `/model`, `/data`. Don't touch `/app`.

---

## STEP 0 — Do this together, 10 minutes, before either track starts

Create the repo and commit two stub files. Both of you paste this:

```
Create a Python project with this structure:

/model      - simulation code
/app        - streamlit app
/data/raw   - downloaded data (gitignored)
/data/processed
/scenarios  - precomputed output JSON
/docs

Create these two stub files with EXACTLY this content, no changes:

/scenarios/ctc_2021.json:
{
  "policy_id": "ctc_2021",
  "label": "Expanded Child Tax Credit (2021)",
  "n_seeds": 500,
  "impact": {
    "child_poverty_rate":       {"baseline": 0.142, "median": 0.084, "p05": 0.071, "p95": 0.098},
    "overall_poverty_rate":     {"baseline": 0.128, "median": 0.101, "p05": 0.093, "p95": 0.110},
    "median_disposable_income": {"baseline": 74000, "median": 75900, "p05": 75200, "p95": 76700},
    "annual_cost_usd":          {"baseline": 0, "median": 105000000000, "p05": 98000000000, "p95": 112000000000}
  },
  "opinion": {
    "overall_support": {"median": 0.58, "p05": 0.51, "p95": 0.65},
    "by_group": [
      {"group_type": "income_quintile", "group": "Q1", "households_weighted": 26000000,
       "disposable_income_delta": 3140, "pct_better_off": 0.91,
       "support": {"median": 0.74, "p05": 0.66, "p95": 0.81},
       "evidence_ids": ["ev_001"]},
      {"group_type": "census_region", "group": "South", "households_weighted": 48000000,
       "disposable_income_delta": 1820, "pct_better_off": 0.44,
       "support": {"median": 0.55, "p05": 0.47, "p95": 0.63},
       "evidence_ids": ["ev_001"]}
    ]
  },
  "warnings": [
    "Support estimates poststratify national survey crosstabs onto population cells; no fitted hierarchical model.",
    "No cost-of-living adjustment across metros; federal poverty thresholds applied uniformly."
  ]
}

/data/evidence.json:
[
  {
    "evidence_id": "ev_001",
    "policy_name": "Expanded Child Tax Credit (2021)",
    "jurisdiction": "United States",
    "year": 2021,
    "subgroup_type": "income_quintile",
    "subgroup": "Q1",
    "support_pct": 0.74,
    "sample_size": 412,
    "estimate_type": "observed_survey",
    "source": "PLACEHOLDER",
    "url": "",
    "holdout": false
  }
]

Also create /model/params.py with an empty PARAMS dict and a comment block
explaining that every coefficient needs a source citation.

Set up a .gitignore that excludes /data/raw and *.csv but NOT /data/evidence.json.
```

☐ Both files committed and pushed. Now split up.

---

## A1 — Download national PUMS

Start the download immediately; it runs while you write the loader.

```
I need to download ACS 1-year PUMS microdata for the entire United States.

Base URL: https://www2.census.gov/programs-surveys/acs/data/pums/

Write a bash script that:
1. Lists the available year directories so I can confirm the most recent year
   (do NOT hardcode a year — fetch and show me what's there)
2. Downloads the national household file and national person file from the
   1-Year subdirectory for that year into /data/raw
3. Unzips them

Important: the national files are often split into multiple parts (a/b suffixes).
Handle all parts, don't assume a single file. Print the resulting filenames and
sizes when done.

Give me the script and tell me roughly how large these files are so I know what
to expect.
```

☐ Files downloaded, sizes printed, filenames noted.

---

## A2 — Load and sample without exploding memory

```
I have ACS 1-year national PUMS files in /data/raw:
[paste the actual filenames from A1]

Write /model/build_population.py that produces a sampled national household
dataset. Requirements:

MEMORY: These files are large. Read in chunks with pandas chunksize, use
`usecols` to load only needed columns, pass explicit dtypes, and sample DURING
iteration rather than concatenating everything then sampling. I should never
hold the full person file in memory.

HOUSEHOLD COLUMNS: SERIALNO, ST, REGION, PUMA, WGTP, NP, HINCP, TEN, FS
PERSON COLUMNS: SERIALNO, AGEP, ESR, WKHP, WAGP, PWGTP

PROCESSING:
1. From the person file, aggregate per SERIALNO: count of children under 6,
   count of children aged 6-17, count of adults, total wage income,
   count of employed adults
2. Join that onto the household file by SERIALNO
3. Drop group-quarters records (NP = 0 or WGTP = 0)
4. Map ST to census region if REGION isn't directly usable
5. Sample 30,000 households with probability proportional to WGTP, WITHOUT
   replacement, using a fixed seed
6. Store a scale factor so weighted totals from the sample still reproduce
   national totals
7. Write to /data/processed/us_households.parquet

Print the row count, the sum of weights before and after sampling, and the
scale factor. Do not silently drop rows — print a count for every filter applied.
```

☐ Parquet written, row count ~30,000, scale factor printed.

---

## A3 — Validate the population (GATE 1)

**This is the check that catches everything.** Don't skip it.

```
Write /model/validate_population.py that loads /data/processed/us_households.parquet
and prints a comparison table of weighted estimates from my sample against
published national figures:

- Total households
- Total population (sum of household sizes, weighted)
- Median household income
- Share of households with at least one child under 18
- Distribution across the four census regions

For each: my weighted estimate, and a column for me to fill in the published
ACS figure, and a percent-difference column.

Then look up the published ACS figures for the year of my data and fill them in,
citing your source for each. If you are not confident in a published figure,
say so and leave it blank rather than guessing.
```

Then verify at least two of those published figures yourself against census.gov. Claude will sometimes produce a confident wrong reference value, and a wrong reference value makes a broken population look fine.

☐ Total households within 2%. ☐ Total population within 2%. ☐ Median income within 3%. ☐ Region shares within 2 points each.

**If any check fails, stop and fix it before A4.** Everything downstream inherits this error.

---

## A4 — Impact engine

```
Write /model/engine.py — a vectorized microsimulation of cash transfer and
child tax credit policies over /data/processed/us_households.parquet.

CRITICAL: fully vectorized numpy. Seeds are an array dimension, not a loop.
No Python loop over households anywhere. 30k households x 500 seeds must run
in under 15 seconds.

INPUT: a policy_spec dict with this levers schema:
  credit_per_child_under_6, credit_per_child_6_to_17, fully_refundable,
  phaseout_start_single, phaseout_start_joint, phaseout_rate,
  flat_transfer_per_adult

PIPELINE per seed:
1. Baseline disposable income from HINCP
2. Compute gross credit from child counts
3. Apply phaseout: reduce credit by phaseout_rate for each dollar of income
   above the threshold, floored at zero. Use joint threshold when the household
   has 2+ adults, single otherwise.
4. If not fully_refundable, cap the credit at estimated tax liability
5. Multiply by a take-up rate drawn for this seed
6. Add flat_transfer_per_adult * adult count
7. New disposable income = baseline + transfer, adjusted by labor supply response
8. Poverty status: federal poverty threshold by household size, counting the
   transfer as income
9. Fiscal cost = weighted sum of transfers

UNCERTAINTY — three parameters drawn per seed via scipy.stats.qmc Latin
hypercube (NOT np.random):
  - take_up_rate
  - labor_supply_elasticity (extensive margin)
  - marginal_propensity_to_consume

Put their central values and ranges in /model/params.py as named constants,
each with a source comment. Leave the citation as TODO if you don't have one —
do not invent a citation.

OUTPUT: a dict matching /scenarios/ctc_2021.json exactly. Every outcome needs
baseline, median, p05, p95. Aggregate by income quintile, household type, and
census region — all weighted.

Also write a convergence check that runs 50/100/250/500/1000 seeds and plots
where p05 and p95 stabilize. Save to /docs/convergence.png.
```

☐ Runs in under 15s. ☐ Output matches the stub schema exactly. ☐ Convergence plot saved. ☐ Sanity check by hand: pick one household, compute its credit on paper, confirm the code agrees.

---

## A5 — Poststratification

```
Add /model/opinion.py.

Load /data/evidence.json. For each population cell (income quintile x household
type x census region) in my sampled population, look up the matching support
percentage from the evidence records, weight by population weight, and aggregate
to overall support and support by group.

REQUIREMENTS:
- Propagate the crosstab sampling error into the output intervals. A record with
  sample_size 412 carries real uncertainty; the output bands must reflect it,
  combined with the population sampling uncertainty.
- Skip any record with holdout = true. Those are reserved for validation.
- Every output group must list the evidence_ids that contributed to it.
- If a cell has no matching evidence, fall back to the next-broader subgroup
  and record that fallback in the warnings array. Never silently impute.

Output slots into the "opinion" key of the result dict from engine.py.
```

☐ Support figures carry evidence_ids. ☐ Fallbacks appear in warnings. ☐ Holdout records excluded.

---

## A6 — Backtests (GATE 3, and the whole pitch)

**Write and commit `/docs/backtest.md` BEFORE running this.** State which parameters are fixed and that only pre-2021 evidence was used. That commit timestamp is your pre-registration.

```
Write /model/backtest.py running two validations of the 2021 expanded Child
Tax Credit.

BACKTEST 1 — material impact:
Build the 2021 CTC policy_spec: $3,600 per child under 6, $3,000 ages 6-17,
fully refundable, phaseout starting $75,000 single / $150,000 joint, phaseout
rate 5%.
Run the engine. Report the predicted change in child poverty rate with p05/p95.
Compare against the published measured 2021 change on a Supplemental Poverty
Measure basis. Report whether observed fell inside the interval.

BACKTEST 2 — opinion:
Load evidence records marked holdout = true. Predict those subgroup support
percentages using poststratification on the non-holdout records only. Report
predicted vs. observed per subgroup with the gap.

OUTPUT: /scenarios/backtest.json plus two matplotlib charts saved to /docs/ —
predicted interval vs. observed point, one per backtest. Make them legible at
projector size: large fonts, high contrast.

Do NOT adjust any parameter to improve the fit. If the prediction misses,
report the miss and print a decomposition of which parameter the result is most
sensitive to.
```

☐ Both numbers exist. ☐ Charts readable from across a room. ☐ You did not tune.

If a prediction misses: report and decompose. Say on stage that you didn't tune — it's the most credible sentence available to you, and almost nobody else will say it.

---

## A7 — Precompute scenarios

```
Write /model/precompute.py generating /scenarios/*.json for:
  baseline (no policy)
  ctc_2021 (the backtest policy)
  ctc_1000 ($1,000 per child, fully refundable, no phaseout)
  flat_500 ($500 per adult, no child component)
  eitc_match (25% increase to existing refundable credits)

Run each at 500 seeds. Print runtime for each. These files are what the demo
reads — the live path exists but the scripted demo must never depend on it.
```

☐ Five files in `/scenarios`. ☐ Teammate confirms the app loads all five.

---

## Then

Write your teammate a one-page cheat sheet: what each number means, what the bands represent, which three limitations to name. He's delivering the pitch; you're defending it. He needs to explain a p05 without looking at you.

Sleep. Deck at 8.
