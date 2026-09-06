"""
End-to-end smoke test against the running app.

Not part of pytest: it needs a live server and a browser. This is the check to
run before presenting, because the unit tests can prove the functions agree with
the contract but cannot prove the thing renders.

    POLICY_SIM_DEMO_MODE=1 python -m streamlit run app/main.py
    python app/tests/smoke_demo.py

Exits non-zero if anything fails.
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
SHOTS = REPO / "docs" / "screenshots"
URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8501"

fails = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)
    return ok


def settle(p, s=6.0):
    p.wait_for_load_state("networkidle")
    time.sleep(s)


def run(pw):
    SHOTS.mkdir(parents=True, exist_ok=True)
    br = pw.chromium.launch()
    p = br.new_page(viewport={"width": 1600, "height": 1050})
    errs = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    p.on("console", lambda m: errs.append(f"{m.type}: {m.text}")
         if m.type == "error" else None)

    print(f"\nAPP  {URL}")
    p.goto(URL, wait_until="domcontentloaded", timeout=60_000)
    settle(p, 10.0)
    body = p.inner_text("body")
    check("page renders", len(body) > 500, f"{len(body)} chars")
    check("no console errors", not errs, str(errs[:3]))
    for k in ("policy-sim", "demo mode", "Child poverty rate"):
        check(f"shows {k!r}", k.lower() in body.lower())
    check("no contract violations reported", "contract note" not in body.lower(),
          "a zero-width interval would appear here")

    fr = next(f for f in p.frames if f != p.main_frame)
    keys = fr.eval_on_selector_all(".marker", "els => els.map(e => e.dataset.key)")
    check("all six metro markers on the map", len(keys) == 6, str(sorted(keys)))
    nodes = fr.evaluate("() => document.querySelectorAll('path').length")
    check("map is actually drawn", nodes > 2000, f"{nodes:,} svg nodes")

    # The map has to be the real country, not a decorative blob. An earlier
    # outline was traced by hand at ~60 points and did not read as the US.
    sys.path.insert(0, str(REPO))
    from app.geo import CITIES, US_OUTLINE
    xs = [x for x, _ in US_OUTLINE]
    ys = [y for _, y in US_OUTLINE]
    check("outline spans the real contiguous US",
          -125.5 < min(xs) < -123.5 and -67.5 < max(xs) < -66.0
          and 24.0 < min(ys) < 25.5 and 49.0 < max(ys) < 49.6,
          f"lon {min(xs):.1f}..{max(xs):.1f}  lat {min(ys):.1f}..{max(ys):.1f}")
    check("outline is detailed enough to read as the US", len(US_OUTLINE) > 250,
          f"{len(US_OUTLINE)} points")
    check("six city markers configured", len(CITIES) == 6)

    p.screenshot(path=str(SHOTS / "app_national.png"))

    fr.locator('.marker[data-key="houston"]').click()
    settle(p, 3.5)
    # map_view renders the map AND the right-hand panel as one component, so
    # the panel text lives in the iframe, not in the page body.
    t = fr.inner_text("body")
    check("clicking a metro switches the panel to that metro", "Houston" in t)
    check("metro panel states the wider-band caveat",
          "wider" in t.lower() and "3.9" in t)
    p.screenshot(path=str(SHOTS / "app_metro.png"))

    back = fr.get_by_text("National view")
    if back.count():
        back.first.click()
        settle(p, 3.0)
        check("returns to the national view",
              "United States" in fr.inner_text("body"))

    boxes = p.get_by_role("combobox")
    if boxes.count():
        boxes.first.click(); settle(p, 1.5)
        opts = p.get_by_role("option")
        n = opts.count()
        check("all five scenarios in the picker", n == 5, f"{n} options")
        for i in range(n):
            if i:
                boxes.first.click(); settle(p, 1.2); opts = p.get_by_role("option")
            lab = opts.nth(i).inner_text()
            opts.nth(i).click(); settle(p, 3.0)
            check(f"renders: {lab.split('·')[0].strip()[:34]}",
                  len(p.inner_text("body")) > 500)

    check("still no console errors after interaction", not errs, str(errs[:3]))
    br.close()


with sync_playwright() as pw:
    run(pw)

print("\n" + "=" * 70)
if fails:
    print(f"{len(fails)} CHECK(S) FAILED: " + ", ".join(fails))
    sys.exit(1)
print("ALL CHECKS PASSED")
print("=" * 70)
