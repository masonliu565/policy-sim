#!/usr/bin/env bash
# Clone the Council's codified DC Code, so policy answers can name the law they
# would change without anyone citing a section from memory.
#
# The Council publishes the Code as XML at github.com/DCCouncil/law-xml-codified,
# which is what code.dccouncil.gov is built from. It is large and it is not
# vendored: this clones it into dc_code/ (gitignored) and builds a compact index
# of every section's citation and heading, which IS committed, along with the
# verbatim rate table of 47-1806.03 that the tax schedule is checked against.
#
#   bash dc_api/setup_dc_code.sh
#   python -m pytest dc_api/test_dc_code.py -q
#
# The index is already in the repository, so this is only needed to rebuild it
# after the Code is amended.

set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$REPO/dc_code"
UPSTREAM="https://github.com/DCCouncil/law-xml-codified"

if [ -d "$DEST/.git" ]; then
  echo ">>> updating $DEST"
  git -C "$DEST" pull --ff-only
else
  echo ">>> cloning $UPSTREAM into $DEST (this is a large repository)"
  rm -rf "$DEST"
  git clone --depth 1 "$UPSTREAM" "$DEST"
fi

echo ">>> codified date"
grep -o '<codified-date>[^<]*' "$DEST/index.xml" | head -1 | cut -d'>' -f2 || true

echo ">>> building the section index"
python "$REPO/dc_api/build_dc_code_index.py" "$DEST" \
  || py "$REPO/dc_api/build_dc_code_index.py" "$DEST"

echo
echo "Next:"
echo "  python -m pytest dc_api/test_dc_code.py -q   # checks the tax schedule"
echo "                                               # against the statute"
