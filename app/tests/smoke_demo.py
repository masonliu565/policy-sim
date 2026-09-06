"""
End-to-end smoke test against the running app.

Not part of pytest: it needs a live server and a browser. Run it before
presenting -- the unit tests prove the functions agree with the contract, but
only this proves the thing renders and responds.

    python -m streamlit run app/main.py
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
    p = br.new_page(viewport={"width": 1500, "height": 1000})
    errs = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    p.on("console", lambda m: errs.append(f"{m.type}: {m.text}")
         if m.type == "error" else None)

    print(f"\nAPP  {URL}")
    p.goto(URL, wait_until="domcontentloaded", timeout=60_000)
    p.wait_for_selector("iframe", timeout=45_000)
    settle(p, 8.0)

    body = p.inner_text("body")
    check("page renders", len(body) > 200, f"{len(body)} chars")
    check("no console errors", not errs, str(errs[:3]))
    check("no demo-mode banner", "demo mode" not in body.lower())
    for k in ("policy-sim", "OUTCOME", "United States", "Child poverty rate",
              "Public support", "Reset"):
        check(f"shows {k!r}", k.lower() in body.lower())

    fr = next(f for f in p.frames if f != p.main_frame)
    fr.wait_for_selector("canvas", timeout=20_000)
    box = fr.locator("canvas").bounding_box()
    check("map canvas present", box and box["width"] > 400,
          f"{box['width']:.0f}x{box['height']:.0f}" if box else "")
    painted = fr.evaluate("""() => {const c=document.querySelector('canvas');
        const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
        let n=0; for(let i=3;i<d.length;i+=4000) if(d[i]>0) n++; return n;}""")
    check("map is painted", painted > 40, f"{painted} sampled pixels")
    names = fr.evaluate("() => metros.map(m => m.metro)")
    check("all six metro pins", len(names) == 6, str(sorted(names)))

    # the map has to be the real country, not a decorative shape
    sys.path.insert(0, str(REPO))
    from app.geo import CITIES, US_OUTLINE
    xs = [x for x, _ in US_OUTLINE]
    ys = [y for _, y in US_OUTLINE]
    check("outline is the real contiguous US",
          -125.5 < min(xs) < -123.5 and -67.5 < max(xs) < -66.0
          and 24.0 < min(ys) < 25.5 and 49.0 < max(ys) < 49.6,
          f"lon {min(xs):.1f}..{max(xs):.1f} lat {min(ys):.1f}..{max(ys):.1f}")
    check("outline detailed enough to read as the US", len(US_OUTLINE) > 250,
          f"{len(US_OUTLINE)} points")
    check("six cities configured", len(CITIES) == 6)
    p.screenshot(path=str(SHOTS / "app_national.png"))

    # click a metro
    xy = fr.evaluate("""() => {const m = metros.find(v => v.metro === 'Houston');
        return [sx(m._x), sy(m._y)];}""")
    p.mouse.click(box["x"] + xy[0], box["y"] + xy[1])
    settle(p, 4.0)
    t = p.inner_text("body")
    check("clicking a metro switches the outcome box", "Houston" in t)
    check("metro states the wider-band caveat", "wider" in t.lower())
    p.screenshot(path=str(SHOTS / "app_metro.png"))

    fr.locator("#back").click()
    settle(p, 3.5)
    check("back returns to national", "United States" in p.inner_text("body"))

    # type a policy and actually simulate it
    ta = p.locator("textarea").first
    ta.click()
    ta.fill("$250 a month per child under 6, phased out over $120k")
    ta.press("Tab")
    time.sleep(1.0)
    t0 = time.time()
    p.get_by_role("button", name="Run it").click()
    settle(p, 12.0)
    t = p.inner_text("body")
    check("typing a policy runs a real simulation",
          "simulated just now" in t.lower(), f"{time.time() - t0:.0f}s")
    check("it says what it understood", "read as" in t.lower())
    check("no traceback reaches the user",
          not any(k in t.lower() for k in ("traceback", "most recent call")))
    p.screenshot(path=str(SHOTS / "app_live.png"))

    p.get_by_role("button", name="Reset").click()
    settle(p, 3.5)
    check("reset returns to a scenario",
          "simulated just now" not in p.inner_text("body").lower())

    check("still no console errors", not errs, str(errs[:3]))
    br.close()


with sync_playwright() as pw:
    run(pw)

print("\n" + "=" * 70)
if fails:
    print(f"{len(fails)} CHECK(S) FAILED: " + ", ".join(fails))
    sys.exit(1)
print("ALL CHECKS PASSED")
print("=" * 70)
