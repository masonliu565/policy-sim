#!/usr/bin/env bash
# Clone and build the Juniper DC atlas front end, UNCHANGED.
#
# The atlas is upstream work (github.com/kp224/simcity). It is not vendored into
# this repository: it is ~109 MB of bundled DC map assets, and copying it in
# would fork it. This clones it, builds it, and leaves it in frontend/, which is
# gitignored. Not one line of it is edited -- the whole point is that our
# evidence service satisfies its existing /api/v1 contract, save for one
# narrow patch to the breakdown table's hardcoded column labels.
#
#   bash dc_api/setup_frontend.sh
#   python dc_api/build_cache.py      # fetch the DC evidence, once
#   python dc_api/server.py           # http://127.0.0.1:4318

set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$REPO/frontend"
UPSTREAM="https://github.com/kp224/simcity"
# Pinned deliberately. The atlas's chat panel is what talks to our service, and
# an upstream branch exists (codex/dc-policy-sandbox-benchmarks) that rewrites
# it to POST /api/v1/sandbox/agent-runs against a Node + Postgres + OpenAI
# service instead of /api/v1/query against this one. Merged upstream and pulled
# blind, that would disconnect the Python backend and every number in it, and
# the app would look like it works right up until it asked for an OpenAI key.
# Move this pin deliberately, run the patch script, and run the tests.
UPSTREAM_COMMIT="bf1e13d6d30830f1f6ad26393566e226e8e67125"

command -v node >/dev/null || { echo "ERROR: node is required (v20+)."; exit 1; }
if ! command -v pnpm >/dev/null; then
  echo ">>> installing pnpm"
  npm install -g pnpm
fi

if [ -d "$DEST/.git" ]; then
  echo ">>> fetching $UPSTREAM_COMMIT into $DEST"
  git -C "$DEST" fetch -q origin
else
  echo ">>> cloning $UPSTREAM into $DEST"
  rm -rf "$DEST"
  git clone -q "$UPSTREAM" "$DEST"
fi
echo ">>> checking out the pinned commit"
git -C "$DEST" checkout -q "$UPSTREAM_COMMIT"
git -C "$DEST" --no-pager log --oneline -1

# One patch, applied here rather than vendored: the atlas's breakdown table
# hardcodes health-survey column headers, and our policy answers put subgroup
# impacts in that table. See dc_api/patch_frontend.py for exactly what and why.
echo ">>> making the breakdown table label itself from the data"
python dc_api/patch_frontend.py "$DEST" || py dc_api/patch_frontend.py "$DEST"

echo ">>> installing atlas dependencies"
pnpm --dir "$DEST/city" install

echo ">>> building the atlas"
pnpm --dir "$DEST/city" build

echo
echo "Atlas built at $DEST/city/dist"
du -sh "$DEST/city/dist" 2>/dev/null || true
echo
echo "Next:"
echo "  python dc_api/build_cache.py    # fetch DC evidence once"
echo "  python dc_api/server.py         # serve atlas + /api/v1 together"
