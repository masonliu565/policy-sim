"""
Scroll the running app and capture the lower sections, then assert the three
render states are actually on screen with the literal text the contract requires.

    python app/tests/drive_sections.py [scenario_index] [url]
"""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent.parent
SHOTS = REPO / "docs" / "screenshots"
IDX = int(sys.argv[1]) if len(sys.argv) > 1 else 2      # 2 = ctc_2021
URL = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8501"


def settle(page, s=2.5):
    page.wait_for_load_state("networkidle")
    time.sleep(s)


def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1100})
        page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        settle(page, 6.0)

        box = page.get_by_role("combobox").first
        box.click()
        settle(page, 1.2)
        opts = page.get_by_role("option")
        label = opts.nth(IDX).inner_text()
        opts.nth(IDX).click()
        settle(page, 3.5)
        print(f"scenario: {label}")

        # Streamlit scrolls an INNER container, so document.body.scrollHeight
        # is 0 and window.scrollTo does nothing. Find the real scroller.
        sel = page.evaluate("""() => {
            const cands = [...document.querySelectorAll('section, div')];
            let best = null, bestH = 0;
            for (const el of cands) {
                if (el.scrollHeight > el.clientHeight + 50 && el.clientHeight > 300) {
                    if (el.scrollHeight > bestH) { bestH = el.scrollHeight; best = el; }
                }
            }
            if (!best) return null;
            best.setAttribute('data-drive-scroller', '1');
            return {h: best.scrollHeight, c: best.clientHeight};
        }""")
        if not sel:
            print("no inner scroller found; falling back to window")
            sel = {"h": page.evaluate("document.documentElement.scrollHeight"),
                   "c": 1100}
            scroll = "window.scrollTo(0, {y})"
        else:
            scroll = ("document.querySelector('[data-drive-scroller]')"
                      ".scrollTop = {y}")
        print(f"scroller height {sel['h']}px, viewport {sel['c']}px")
        step = max(600, sel["c"] - 120)
        shots = []
        for i, y in enumerate(range(0, sel["h"], step)):
            page.evaluate(scroll.format(y=y))
            time.sleep(1.0)
            sp = SHOTS / f"sec_{IDX}_{i:02d}.png"
            page.screenshot(path=str(sp))
            shots.append(sp)
        print(f"captured {len(shots)} section screenshots")

        # Open every expander so collapsed content is in the DOM text.
        exps = page.locator('[data-testid="stExpander"] summary')
        print(f"expanders found: {exps.count()}")
        for i in range(exps.count()):
            try:
                exps.nth(i).click(timeout=3000)
                time.sleep(0.3)
            except Exception:
                pass
        settle(page, 2.0)

        body = page.inner_text("body")
        (SHOTS / f"body_{IDX}.txt").write_text(body, encoding="utf-8")

        # The literal strings docs/output_contract.md requires.
        required = {
            'literal "insufficient evidence" text': "insufficient evidence",
            "metro section": "metro",
            "one of the six metros": "Houston",
            "wider-band caveat": "wider",
            "warnings surfaced": "warning",
            "evidence ids shown": "ev_",
        }
        low = body.lower()
        print()
        for label, needle in required.items():
            ok = needle.lower() in low
            print(f"  [{'OK ' if ok else 'MISS'}] {label}"
                  + ("" if ok else f"   (looked for {needle!r})"))

        # Guard against a zero rendering where the contract demands text.
        import re
        bad = re.findall(r"support[^\n]{0,40}\b0(?:\.0+)?%", body, re.I)
        print(f"\n  suspicious 'support 0%' renderings: {len(bad)}"
              + (f" -> {bad[:3]}" if bad else "  (good: none)"))
        browser.close()


if __name__ == "__main__":
    main()
