"""
Drive the running Streamlit app in a real browser and screenshot it.

This is NOT part of the pytest suite -- it needs a live server and a browser.
It exists because the unit tests can only prove the functions agree with the
contract; they cannot prove the app renders. Run:

    POLICY_SIM_DEMO_MODE=1 python -m streamlit run app/main.py --server.headless true
    python app/tests/drive_demo.py

Writes screenshots to docs/screenshots/ and prints what it actually saw.
"""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent.parent
SHOTS = REPO / "docs" / "screenshots"
URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8501"


def settle(page, seconds=3.0):
    """Streamlit streams its DOM over a websocket; give it time to finish."""
    page.wait_for_load_state("networkidle")
    time.sleep(seconds)


def shoot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    p = SHOTS / f"{name}.png"
    page.screenshot(path=str(p), full_page=True)
    print(f"  saved {p.relative_to(REPO)}  ({p.stat().st_size / 1024:.0f} KB)")
    return p


def main():
    findings = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        errors = []
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)

        print(f"opening {URL}")
        page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        settle(page, 6.0)

        title = page.title()
        body = page.inner_text("body")
        print(f"  title: {title!r}")
        print(f"  body text: {len(body):,} chars")
        shoot(page, "01_landing")

        # What did it actually render?
        checks = {
            "demo-mode banner": ["demo mode", "no network", "zero network"],
            "a scenario label": ["Child Tax Credit", "child tax credit"],
            "child poverty figure": ["child poverty", "Child poverty"],
            "an interval": ["p05", "90%", "interval", "–", "—"],
            "insufficient-evidence state": ["insufficient evidence",
                                            "Insufficient evidence"],
            "outside-evidence state": ["outside the evidence", "evidence base",
                                       "no estimate"],
        }
        low = body.lower()
        for label, needles in checks.items():
            hit = next((n for n in needles if n.lower() in low), None)
            print(f"  [{'OK ' if hit else '?? '}] {label}"
                  + (f"   -> {hit!r}" if hit else ""))
            findings.append((label, bool(hit)))

        # Try each scenario in the picker, if there is one.
        try:
            boxes = page.get_by_role("combobox")
            n = boxes.count()
            print(f"  comboboxes found: {n}")
            if n:
                boxes.first.click()
                settle(page, 1.5)
                opts = page.get_by_role("option")
                labels = [opts.nth(i).inner_text() for i in range(opts.count())]
                print(f"  scenario options: {labels}")
                shoot(page, "02_scenario_menu")
                for i, lab in enumerate(labels):
                    if i:  # reopen after the first selection
                        boxes.first.click()
                        settle(page, 1.0)
                        opts = page.get_by_role("option")
                    opts.nth(i).click()
                    settle(page, 3.0)
                    txt = page.inner_text("body")
                    slug = "".join(c if c.isalnum() else "_" for c in lab)[:28]
                    shoot(page, f"03_scenario_{i}_{slug}")
                    print(f"    [{lab}] rendered {len(txt):,} chars")
        except Exception as exc:  # noqa: BLE001
            print(f"  scenario picker: could not drive it ({exc})")

        if errors:
            print("\n  BROWSER ERRORS:")
            for e in errors[:15]:
                print(f"    {e}")
        else:
            print("\n  no browser console errors")

        browser.close()

    missing = [k for k, ok in findings if not ok]
    print("\n" + "=" * 70)
    if missing:
        print("NOT FOUND ON SCREEN: " + ", ".join(missing))
    else:
        print("every expected element rendered")
    print("=" * 70)


if __name__ == "__main__":
    main()
