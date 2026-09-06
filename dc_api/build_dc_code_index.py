"""
Index the codified DC Code, so a policy answer can name the law it would change.

WHY. The service could compute what a policy does and could not say what it
would amend. "Raise the income tax" was answered with a revenue figure and no
mention of the statute that actually sets the rates. Naming a section from
memory is exactly the kind of citation this project refuses to print, so the
Code itself is the source.

SOURCE. DCCouncil/law-xml-codified, the Council's own codified XML, which is
what code.dccouncil.gov is built from. The repository is large and is not
vendored; dc_api/setup_dc_code.sh clones it and this builds a compact index of
every section's citation and heading. The full text stays in the clone and is
read on demand.

What is committed is the index -- citation, heading, title -- plus the verbatim
rate table of section 47-1806.03, because that one is used to check our tax
schedule against the statute rather than only against the tax office's website.

    bash dc_api/setup_dc_code.sh
    python dc_api/build_dc_code_index.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parent.parent
CLONE = REPO / "dc_code"
OUT = REPO / "data" / "dc_code"
CODE_URL = "https://code.dccouncil.gov/us/dc/council/code/sections/"

NUM = re.compile(r"<num>([^<]+)</num>")
HEADING = re.compile(r"<heading>(.*?)</heading>", re.S)
TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")


def clean(s: str) -> str:
    s = TAG.sub(" ", s)
    s = (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
          .replace("&#8212;", "-").replace("&#8217;", "'").replace("�", "-"))
    return WS.sub(" ", s).strip()


def title_name(title_dir: Path) -> str:
    idx = title_dir / "index.xml"
    if not idx.exists():
        return ""
    head = idx.read_text(encoding="utf-8", errors="replace")[:4000]
    m = HEADING.search(head)
    return clean(m.group(1)) if m else ""


def build(clone: Path) -> Dict[str, Any]:
    base = clone / "us" / "dc" / "council" / "code" / "titles"
    if not base.is_dir():
        raise FileNotFoundError(f"{base} not found. Run dc_api/setup_dc_code.sh")

    sections: List[Dict[str, str]] = []
    titles: Dict[str, str] = {}
    for tdir in sorted(base.iterdir(), key=lambda p: p.name):
        if not tdir.is_dir():
            continue
        titles[tdir.name] = title_name(tdir)
        sdir = tdir / "sections"
        if not sdir.is_dir():
            continue
        for f in sdir.glob("*.xml"):
            raw = f.read_text(encoding="utf-8", errors="replace")
            n = NUM.search(raw)
            h = HEADING.search(raw)
            if not n:
                continue
            cite = clean(n.group(1))
            sections.append({
                "cite": cite,
                "heading": clean(h.group(1)) if h else "",
                "title": tdir.name,
            })
    sections.sort(key=lambda s: s["cite"])
    return {"source": "DCCouncil/law-xml-codified",
            "url": "https://github.com/DCCouncil/law-xml-codified",
            "codeUrl": CODE_URL,
            "titles": titles,
            "sections": sections}


def rate_table(clone: Path) -> Dict[str, Any]:
    """The verbatim bracket language of 47-1806.03, kept so the tax schedule can
    be checked against the statute and not only against the tax office page."""
    f = (clone / "us" / "dc" / "council" / "code" / "titles" / "47" /
         "sections" / "47-1806.03.xml")
    text = clean(f.read_text(encoding="utf-8", errors="replace"))
    rows = re.findall(
        r"\$[\d,]+, plus [\d.]+% of the excess (?:over|above) \$[\d,]+", text)
    return {"cite": "47-1806.03",
            "heading": "Tax on residents and nonresidents - Imposition and rates.",
            "url": CODE_URL + "47-1806.03",
            "bracketLanguage": rows}


def main() -> int:
    clone = Path(sys.argv[1]) if len(sys.argv) > 1 else CLONE
    idx = build(clone)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "sections.json").write_text(
        json.dumps(idx, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    rt = rate_table(clone)
    (OUT / "income_tax_rates_47-1806.03.json").write_text(
        json.dumps(rt, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(idx['sections']):,} sections across {len(idx['titles'])} titles")
    print(f"47-1806.03: {len(rt['bracketLanguage'])} bracket rows captured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
