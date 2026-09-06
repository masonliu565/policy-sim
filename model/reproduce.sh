#!/usr/bin/env bash
# Rebuild every derived artefact from the raw PUMS and verify it reproduces
# byte-for-byte.
#
# Usage:
#   ./model/reproduce.sh          rebuild and compare against current files
#   ./model/reproduce.sh --check  same, but fail loudly on any difference
#
# Everything downstream of the raw download is deterministic: the sample uses a
# fixed seed (20260905), the Latin hypercube is seeded, and the MRP Gibbs
# sampler is seeded. If any hash below changes without a deliberate code change,
# something non-deterministic has crept in and the demo numbers are no longer
# the numbers in the deck.
#
# This does NOT re-download the PUMS. Run model/download_pums.sh first if
# data/raw is empty.

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

ARTEFACTS=(
  data/processed/us_households.parquet
  data/processed/puma_to_metro.csv
  data/processed/poverty_thresholds_2024.csv
  scenarios/baseline.json
  scenarios/ctc_2021.json
  scenarios/ctc_1000.json
  scenarios/flat_500.json
  scenarios/eitc_match.json
  scenarios/backtest.json
)

if [ ! -f data/raw/psam_husa.csv ]; then
  echo "ERROR: data/raw is empty. Run ./model/download_pums.sh first."
  exit 1
fi

BEFORE=$(mktemp)
for f in "${ARTEFACTS[@]}"; do
  [ -f "$f" ] && md5sum "$f" >> "$BEFORE"
done
echo "Recorded $(wc -l < "$BEFORE") existing artefact hashes."
echo "------------------------------------------------------------"

echo ">>> model/build_metro_crosswalk.py"   && py model/build_metro_crosswalk.py   > /dev/null || exit 1
echo ">>> model/build_poverty_thresholds.py" && py model/build_poverty_thresholds.py > /dev/null || exit 1
echo ">>> model/build_population.py"        && py model/build_population.py        > /dev/null || exit 1
echo ">>> model/validate_population.py"     && py model/validate_population.py | tail -12
echo ">>> model/precompute.py"              && py model/precompute.py | tail -3
echo ">>> model/backtest.py"                && py model/backtest.py > /dev/null || exit 1

echo "------------------------------------------------------------"
echo "Reproducibility check:"
if md5sum -c "$BEFORE" 2>&1 | sed 's/^/  /'; then
  echo "------------------------------------------------------------"
  echo "ALL ARTEFACTS REPRODUCE BYTE-FOR-BYTE."
  rm -f "$BEFORE"
  exit 0
else
  echo "------------------------------------------------------------"
  echo "MISMATCH. Either a code change is responsible (expected -- commit the"
  echo "new outputs) or something non-deterministic has crept in (not expected"
  echo "-- find it before demoing)."
  rm -f "$BEFORE"
  [ "${1:-}" = "--check" ] && exit 1
  exit 0
fi
