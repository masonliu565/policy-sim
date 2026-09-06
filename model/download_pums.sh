#!/usr/bin/env bash
# Download national ACS 1-Year PUMS microdata (household + person) into data/raw.
#
# Usage:
#   ./model/download_pums.sh              # use the most recent year found
#   ./model/download_pums.sh 2023         # pin a year
#   ./model/download_pums.sh --list       # just show available years and exit
#
# Notes on why this is written defensively:
#   - The year is NEVER hardcoded. It is discovered from the live directory index.
#   - www2.census.gov fronts some directory indexes with a WAF that returns
#     "Request Rejected" for automated listings. So after discovering the year we
#     do not rely on parsing the 1-Year index: we HEAD-probe the known national
#     filename patterns and take whatever actually answers 200.
#   - National files are split. Recent years ship one combined zip per record type
#     (csv_hus.zip / csv_pus.zip) that unpacks to lettered parts (psam_husa.csv,
#     psam_husb.csv, ...). Older years ship separate lettered zips
#     (csv_husa.zip, csv_husb.zip, ...). Both layouts are probed and handled.
#   - Downloads resume (curl -C -) so a dropped connection does not restart 600 MB.

set -uo pipefail

BASE="https://www2.census.gov/programs-surveys/acs/data/pums"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW_DIR="$REPO_ROOT/data/raw"
UA="Mozilla/5.0 (compatible; policy-sim/1.0; +https://github.com/masonliu565/policy-sim)"

say() { printf '%s\n' "$*"; }
hr()  { printf '%s\n' "------------------------------------------------------------"; }

# ---------------------------------------------------------------- 1. list years
say "Fetching available PUMS year directories from:"
say "  $BASE/"
hr

YEARS=$(curl -s --max-time 90 -A "$UA" "$BASE/" \
        | grep -oE 'href="[0-9]{4}/"' \
        | grep -oE '[0-9]{4}' \
        | sort -u)

if [ -z "$YEARS" ]; then
  say "ERROR: could not read the year index (network down, or WAF rejection)."
  say "Re-run in a minute, or pass a year explicitly: ./model/download_pums.sh 2024"
  exit 1
fi

say "Available years:"
printf '%s\n' "$YEARS" | tr '\n' ' '; echo; echo
LATEST=$(printf '%s\n' "$YEARS" | tail -1)

if [ "${1:-}" = "--list" ]; then
  say "Most recent: $LATEST"
  say "(--list requested; stopping here.)"
  exit 0
fi

YEAR="${1:-$LATEST}"
if ! printf '%s\n' "$YEARS" | grep -qx "$YEAR"; then
  say "ERROR: year $YEAR is not in the index above."
  exit 1
fi

# There is no 2020 ACS 1-year PUMS — collection was disrupted by COVID and only an
# experimental file was released. Refuse rather than silently produce a bad year.
if [ "$YEAR" = "2020" ]; then
  say "ERROR: 2020 has no standard ACS 1-Year PUMS release (COVID; experimental only)."
  say "Pick a different year."
  exit 1
fi

DIR="$BASE/$YEAR/1-Year"
say "Using year: $YEAR"
say "Source dir: $DIR"
hr

# ------------------------------------------------- 2. probe which files exist
# Returns 0 and prints Content-Length if the URL answers 200.
probe() {
  local url="$1" out
  out=$(curl -sI --max-time 60 -A "$UA" "$url")
  printf '%s' "$out" | head -1 | grep -q ' 200' || return 1
  printf '%s' "$out" | grep -i '^content-length:' | tr -d '\r' | awk '{print $2}'
}

human() { awk -v b="$1" 'BEGIN{ s="B KB MB GB"; split(s,u," "); i=1;
          while (b>=1024 && i<4){ b/=1024; i++ } printf "%.1f %s", b, u[i] }'; }

TARGETS=()
TOTAL=0
for kind in hus pus; do
  found_any=0
  # Layout A: one combined zip per record type.
  if size=$(probe "$DIR/csv_${kind}.zip"); then
    TARGETS+=("$DIR/csv_${kind}.zip")
    TOTAL=$((TOTAL + size))
    say "  found csv_${kind}.zip           $(human "$size")"
    found_any=1
  fi
  # Layout B: separate lettered part zips (older years).
  if [ "$found_any" -eq 0 ]; then
    for letter in a b c d e f; do
      if size=$(probe "$DIR/csv_${kind}${letter}.zip"); then
        TARGETS+=("$DIR/csv_${kind}${letter}.zip")
        TOTAL=$((TOTAL + size))
        say "  found csv_${kind}${letter}.zip          $(human "$size")"
        found_any=1
      fi
    done
  fi
  if [ "$found_any" -eq 0 ]; then
    say "ERROR: no national '$kind' file found under $DIR"
    say "Check the directory in a browser; the naming may have changed for $YEAR."
    exit 1
  fi
done

hr
say "Total download: $(human "$TOTAL")  into  $RAW_DIR"
say "Expect roughly 5-6x that on disk after unzip."
hr

mkdir -p "$RAW_DIR"

# ------------------------------------------------------------ 3. download
for url in "${TARGETS[@]}"; do
  fname=$(basename "$url")
  say ">>> $fname"
  curl -L --fail --retry 5 --retry-delay 5 --retry-all-errors \
       -C - -A "$UA" --progress-bar \
       -o "$RAW_DIR/$fname" "$url" \
    || { say "ERROR: download failed for $fname"; exit 1; }
done

# ------------------------------------------------------------ 4. unzip
hr
say "Unzipping..."
for url in "${TARGETS[@]}"; do
  fname=$(basename "$url")
  say ">>> $fname"
  unzip -o -q "$RAW_DIR/$fname" -d "$RAW_DIR" || { say "ERROR: unzip failed for $fname"; exit 1; }
done

# ------------------------------------------------------------ 5. report
hr
say "Contents of $RAW_DIR:"
hr
ls -la "$RAW_DIR"
hr
say "CSV row counts (data rows, excluding header):"
for f in "$RAW_DIR"/*.csv; do
  [ -e "$f" ] || continue
  n=$(( $(wc -l < "$f") - 1 ))
  printf '  %-24s %12s rows\n' "$(basename "$f")" "$n"
done
hr
say "Year used: $YEAR"
say "Record this year — validate_population.py (A3) must compare against the"
say "published ACS figures for THIS year, not a different one."
