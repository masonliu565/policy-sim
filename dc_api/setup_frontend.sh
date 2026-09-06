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

command -v node >/dev/null || { echo "ERROR: node is required (v20+)."; exit 1; }
if ! command -v pnpm >/dev/null; then
  echo ">>> installing pnpm"
  npm install -g pnpm
fi

if [ -d "$DEST/.git" ]; then
  echo ">>> updating $DEST"
  git -C "$DEST" pull --ff-only
else
  echo ">>> cloning $UPSTREAM into $DEST"
  rm -rf "$DEST"
  git clone --depth 1 "$UPSTREAM" "$DEST"
fi

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
