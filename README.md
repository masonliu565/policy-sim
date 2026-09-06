# policy-sim

Microsimulation of cash-transfer and child-tax-credit policy, with poststratified
opinion estimates and pre-registered backtests against the 2021 expanded CTC.
You describe a policy in plain English; a language model translates it into
levers and shows you its reading so you can correct it; a vectorized
microsimulation over ACS PUMS households produces every number on screen, each
one carrying the 5th–95th percentile band from a 500-seed run. Nothing is
rendered as a bare point estimate, and a verification pass checks the generated
memo against the simulation output number by number.

![policy-sim](docs/screenshots/app_national.png)

## The architecture rule

**LLMs parse and explain. Statistical models produce every number.**

This is enforced by construction, not by convention:

| Component | Touches a model? | Produces a number? |
|---|---|---|
| `app/parser.py` | yes — English → levers | no. `extra="forbid"` rejects any invented outcome field outright |
| `model/` | no | yes. Every figure originates here |
| `app/main.py` | no | no. Reads `scenarios/*.json` and renders it |
| `app/report.py` | yes — writes the memo | no. Every number it emits is checked back against the input |

`app/` never imports from `model/`. The demo reads precomputed JSON, so the
app runs the same engine that produced the precomputed files, locally.

### The interval rule

Every figure in the outcome box is rendered with its p05-p95 band beside it.
There is no code path that renders a median on its own. When an interval has
zero width the box says "no spread" rather than presenting a point estimate as
a confident one.

Metro bands are wider than national ones and are never rescaled to match. A
metro carries about 2,000 households against 30,000, so its sampling error is
genuinely larger; equalising the bars would hide the exact thing the bands
exist to show, so the metro view labels itself instead.

### The numeric verification panel

After the memo is generated, `verify_numbers` extracts every numeric token from
the text and checks it against the simulation JSON and evidence records it was
given. Percent/fraction twins, thousands separators and B/M/K/T suffixes pass;
a derived difference, a re-rounded figure or an invented subgroup percentage
does not. Cited `evidence_id`s that do not exist are reported too.

The panel renders whether or not it finds anything — **"0 unverified numbers"
is the point**.

## Run it

```bash
python -m pip install -r requirements.txt
python -m streamlit run app/main.py
```

Then open <http://localhost:8501>. That is the whole setup. No API key, no
account, no external service.

Four things on screen: the map, one outcome box, the policy box, and reset.

- **Click a metro pin** to zoom in. The outcome box switches to figures computed
  on that metro's own subpopulation -- not scaled down from the national ones --
  and says that its bands are legitimately ~3.9x wider. `Back to national`, or
  `Esc`, zooms out.
- **Type a policy in plain English and press Run it.** The description is read
  offline by `app/local_parser.py`, and the real microsimulation runs locally
  over the 30,000-household sample in about two seconds. The line under the box
  says exactly what it understood, including any assumption it had to make.
- **Reset** clears the metro, the typed policy and the result.

### It works with no network

Nothing on that path touches the internet. The parser is regular expressions,
the numbers come from local numpy over a local parquet file, and the map is a
canvas. `app/tests/test_app_ui.py::test_the_app_needs_no_network` renders the
whole page with `socket.connect` patched to raise, which is the wifi-off run as
a test.

There is no "demo mode" any more. There was, and its banner read as though the
build were crippled, when the truth is the opposite: the app has no external
dependency to lose.

### An API key is optional

```bash
echo 'ANTHROPIC_API_KEY=sk-...' > .env
```

With a key present, two things become available, neither of which produces a
number:

- if the offline reader cannot parse an unusual phrasing, Claude gets a turn at
  turning the English into levers;
- **Write a policy memo** drafts the prose, and the numeric verification panel
  then checks every numeric token in it against the simulation output and the
  evidence records. "0 unverified numbers" is the point of that panel.

### The map

The outline is the U.S. Census Bureau's dissolved national boundary
(`cb_2023_us_nation_20m`), simplified to 338 points by
`model/build_us_outline.py` and drawn in Albers Equal Area Conic, the projection
US maps actually use. City pins sit at the population-weighted centroid of the
tracts inside each metro's PUMAs, from the 2020 centers-of-population file --
not coordinates typed from memory. Regenerate with:

```bash
python model/build_us_outline.py
```

### Checks

```bash
python -m pytest app/tests -q       # 64 tests
python app/tests/smoke_demo.py      # drives the running app in a browser
python model/diagnostics.py         # 25 engine invariants + MCMC convergence
bash model/reproduce.sh             # rebuild every artefact from raw PUMS
```

### Environment variables

| Variable | Default | Effect |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for the parser and the memo only |
| `POLICY_SIM_MODEL` | `claude-opus-5` | Model for parsing and the memo |
| `POLICY_SIM_PARSER_TIMEOUT` | `30` | Seconds before the parser falls back |
| `POLICY_SIM_REPORT_TIMEOUT` | `60` | Seconds before memo generation gives up |
| `POLICY_SIM_SCENARIO_DIR` | `scenarios/` | Where to read scenarios from (tests) |

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest app/tests -q
```

107 tests. `test_frontend_js.py` needs `node` on PATH and skips without it.

## Data sources

**Population.** ACS 1-year PUMS, national household and person files, from the
[Census PUMS directory](https://www2.census.gov/programs-surveys/acs/data/pums/).
Sampled to 30,000 households with probability proportional to `WGTP`. Built by
`model/build_population.py`; validated against published ACS totals by
`model/validate_population.py`.

**Metro definitions.** OMB CBSA delineation files, via
`model/build_metro_crosswalk.py`. PUMAs are assigned to a metro by majority of
2020 population; CBSA codes are looked up by title rather than hardcoded, so an
OMB re-delineation surfaces as an error rather than a wrong assignment.

**Opinion.** Hand-curated survey crosstabs in `data/evidence.json`, each record
carrying its pollster, field dates, question wording, sample size and URL. Every
figure was read out of the primary topline or crosstab document, not a secondary
write-up:

- [The Economist/YouGov, 4–6 Aug 2024](https://d3nkl3psvxxpe9.cloudfront.net/documents/econTabReport_qdE4wzP.pdf) (N=1,618) — CTC eligibility expansion and inflation adjustment, with income-band crosstabs
- [Data for Progress, Apr–May 2021](https://filesforprogress.org/datasets/2021/5/dfp-permanent-child-tax-credit-expansion-TOPS.pdf) (N=1,189 / N=1,402) — the 2021 ARPA CTC parameterisation
- [Data for Progress / Mayors for a Guaranteed Income, Jun–Jul 2021](https://www.filesforprogress.org/memos/voters-support-a-guaranteed-income.pdf) (N=1,137) — guaranteed income
- [American Compass / YouGov, 10–13 Aug 2021](https://americancompass.org/child-tax-credit-expansion-survey/) (N=2,000) — one-year vs permanent vs unconditional expansion

### Known gaps in the evidence

Stated here because they change how the support estimates should be read:

- **No census-region crosstabs exist** in any source found. Region cells fall
  back to broader subgroups; the fallback is recorded in the scenario warnings.
- **Income bands are not quintiles.** The available breaks are
  `<50K / 50–100K / 100K+`. They are labelled `income_band`, not
  `income_quintile`, and mapping them onto quintiles is an explicit modelling
  step, not a relabelling.
- **No 2021 CTC poll with income crosstabs was found.** The only income-band
  CTC crosstabs are from 2024, which constrains what the opinion backtest can
  hold out.
- **No EITC records.** Only secondary summaries were available and they were
  not used.

## Layout

- `model/` simulation, validation, poststratification, backtests (Track A)
- `app/` the streamlit app: map, outcome box, offline policy reader
- `data/raw/` downloaded ACS PUMS (gitignored)
- `data/processed/` sampled population parquet
- `data/evidence.json` hand-curated survey crosstabs (tracked)
- `scenarios/` precomputed output JSON — the starting scenarios
- `docs/` plan, pre-registration, charts

Build plan: [docs/plan.md](docs/plan.md)
