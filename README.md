# policy-sim

Microsimulation of cash-transfer and child-tax-credit policy, with poststratified
opinion estimates and pre-registered backtests against the 2021 expanded CTC.

## Layout

- `model/`  simulation, validation, poststratification, backtests
- `app/`    streamlit demo (Track B — do not edit from Track A)
- `data/raw/`        downloaded ACS PUMS (gitignored)
- `data/processed/`  sampled population parquet
- `data/evidence.json`  hand-curated survey crosstabs (tracked)
- `scenarios/`  precomputed output JSON — this is what the demo reads
- `docs/`    plan, pre-registration, charts

Build plan: [docs/plan.md](docs/plan.md)
