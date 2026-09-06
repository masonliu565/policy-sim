"""
Drive the natural-language policy box and see what a user actually gets.

Run against a server started WITHOUT POLICY_SIM_DEMO_MODE and without an API
key: that is the failure a live demo can hit on stage (expired key, no .env on
the presenting laptop), and the only honest way to know what it looks like is to
do it.

    python -m streamlit run app/main.py --server.headless true --server.port 8503
    python app/tests/drive_parser.py http://localhost:8503
"""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent.parent
SHOTS = REPO / "docs" / "screenshots"
URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8503"
POLICY = "give every family $250 a month per child under 6, phase it out over $120k"


def settle(page, s=2.5):
    page.wait_for_load_state("networkidle")
    time.sleep(s)


def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        crashes = []
        page.on("pageerror", lambda e: crashes.append(str(e)))
        page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        settle(page, 6.0)

        before = page.inner_text("body")
        print(f"demo banner present: "
              f"{'demo mode' in before.lower()}   (expected False here)")

        box = page.get_by_placeholder("e.g.", exact=False)
        if box.count() == 0:
            box = page.locator("textarea, input[type=text]").first
        box.first.fill(POLICY)
        print(f"typed: {POLICY!r}")
        settle(page, 1.0)

        btn = page.get_by_role("button", name="Read it")
        if btn.count() == 0:
            btn = page.get_by_role("button").filter(has_text="Read")
        btn.first.click()
        print("clicked 'Read it'")
        settle(page, 8.0)

        after = page.inner_text("body")
        page.screenshot(path=str(SHOTS / "10_parser_no_key.png"), full_page=False)
        print(f"  saved docs/screenshots/10_parser_no_key.png")

        new = after[len(before):] if after.startswith(before) else after
        low = after.lower()

        traceback_markers = ["traceback", "most recent call last",
                             "exception", 'file "', "anthropic.", "keyerror"]
        found_tb = [m for m in traceback_markers if m in low]
        readable = [m for m in ["api key", "api_key", "not configured",
                                "no key", "missing", "set anthropic",
                                "demo mode", "unavailable"] if m in low]

        print()
        print(f"  raw traceback leaked : {found_tb or 'NO  (good)'}")
        print(f"  readable explanation : {readable or 'NONE FOUND (bad)'}")
        print(f"  browser page errors  : {crashes or 'none'}")

        # Show the message the user would actually read.
        for line in after.splitlines():
            l = line.strip()
            if any(k in l.lower() for k in ("api key", "api_key", "unavailable",
                                            "could not", "cannot", "unable")):
                print(f"  on screen: {l[:160]}")
        browser.close()


if __name__ == "__main__":
    main()
