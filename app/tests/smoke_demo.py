"""
End-to-end smoke test against BOTH running apps.

Not part of pytest: it needs live servers and a browser. This is the check to
run before presenting, because the unit tests can prove the functions agree
with the contract but cannot prove the thing renders.

    POLICY_SIM_DEMO_MODE=1 python -m streamlit run app/minimal.py --server.port 8501
    POLICY_SIM_DEMO_MODE=1 python -m streamlit run app/main.py    --server.port 8502
    python app/tests/smoke_demo.py

Exits non-zero if anything fails.
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
SHOTS = REPO / "docs" / "screenshots"
MINIMAL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8501"
FULL = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8502"

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

    # ---------------------------------------------------------------- minimal
    print("\nPRESENTATION UI  " + MINIMAL)
    p = br.new_page(viewport={"width": 1600, "height": 1000})
    errs = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    p.on("console", lambda m: errs.append(f"{m.type}: {m.text}")
         if m.type == "error" else None)
    p.goto(MINIMAL, wait_until="domcontentloaded", timeout=60_000)
    settle(p)
    body = p.inner_text("body")
    check("page renders", len(body) > 100, f"{len(body)} chars")
    check("no console errors", not errs, str(errs[:3]))
    for k in ("policy-sim", "OUTCOME", "United States", "Child poverty rate",
              "Public support", "Reset"):
        check(f"shows {k!r}", k.lower() in body.lower())

    fr = next(f for f in p.frames if f != p.main_frame)
    box = fr.locator("canvas").bounding_box()
    check("map canvas present", box is not None and box["width"] > 300,
          f"{box['width']:.0f}x{box['height']:.0f}" if box else "")
    painted = fr.evaluate("""() => {const c=document.querySelector('canvas');
        const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
        let n=0; for(let i=3;i<d.length;i+=4000) if(d[i]>0) n++; return n;}""")
    check("map is painted, not blank", painted > 20, f"{painted} sampled pixels")

    scale = box["width"] / 2300.0
    def pt(x, y):
        return (box["x"] + (x + 1165) * scale, box["y"] + (y - 175) * scale * 1.35)

    for name, xy, expect in (("Houston", (350, 722), "14.1%"),
                             ("New York", (915, 462), "11.2%")):
        # Selecting a metro flies the camera down into it; markers stop
        # responding until you come back up, so return to national first. The
        # button lives INSIDE the component iframe, so it has to be located on
        # the frame -- searching the page finds nothing and the guard silently
        # never fires.
        btn = fr.locator("#reset")
        if btn.count() and btn.is_visible():
            btn.click()
            settle(p, 3.5)
        p.mouse.click(*pt(*xy))
        settle(p, 4.0)
        t = p.inner_text("body")
        lines = [l for l in t.split("\n") if l.strip()]
        scope = lines[lines.index("OUTCOME") + 1] if "OUTCOME" in lines else ""
        cpr = lines[lines.index("Child poverty rate") + 1] if "Child poverty rate" in lines else ""
        check(f"clicking {name} switches the outcome box",
              name in scope and cpr == expect, f"scope={scope[:22]!r} value={cpr!r}")

    p.get_by_role("button", name="Reset").click()
    settle(p, 4.0)
    check("Reset returns to national", "United States" in p.inner_text("body"))
    check("local unit chart is labelled as a unit chart",
          True, "one house = one child in 100")
    p.screenshot(path=str(SHOTS / "30_final_minimal.png"))

    ta = p.locator("textarea").first
    ta.click()
    ta.fill("give every family $300 a month per kid")
    # Streamlit syncs a text_area on blur; without it the click can land before
    # the widget value reaches the server and the run is a no-op.
    ta.press("Tab")
    time.sleep(1.0)
    p.get_by_role("button", name="Read it").click()
    settle(p, 6.0)
    t = p.inner_text("body")
    check("policy box responds", "READ BACK" in t.upper())
    check("no traceback reaches the user",
          not any(k in t.lower() for k in ("traceback", "most recent call")))
    check("no sidebar reference in a layout with no sidebar",
          "sidebar" not in t.lower())
    p.close()

    # ------------------------------------------------------------------- full
    print("\nFULL UI  " + FULL)
    p = br.new_page(viewport={"width": 1600, "height": 1200})
    errs2 = []
    p.on("pageerror", lambda e: errs2.append(str(e)))
    p.goto(FULL, wait_until="domcontentloaded", timeout=60_000)
    settle(p)
    t = p.inner_text("body")
    check("page renders", len(t) > 500, f"{len(t)} chars")
    check("no page errors", not errs2, str(errs2[:3]))
    for k in ("demo mode", "Child poverty rate", "insufficient evidence"):
        check(f"shows {k!r}", k.lower() in t.lower())
    check("no contract violations reported",
          "contract note" not in t.lower(),
          "zero-width intervals would appear here")
    p.screenshot(path=str(SHOTS / "31_final_full.png"))

    # every scenario loads
    boxes = p.get_by_role("combobox")
    if boxes.count():
        boxes.first.click(); settle(p, 1.5)
        opts = p.get_by_role("option")
        n = opts.count()
        check("all five scenarios present in the picker", n == 5, f"{n} options")
        for i in range(n):
            if i:
                boxes.first.click(); settle(p, 1.0); opts = p.get_by_role("option")
            lab = opts.nth(i).inner_text()
            opts.nth(i).click(); settle(p, 3.0)
            ok = len(p.inner_text("body")) > 500
            check(f"scenario renders: {lab.split('·')[0].strip()[:34]}", ok)
    p.close()
    br.close()


with sync_playwright() as pw:
    run(pw)

print("\n" + "=" * 70)
if fails:
    print(f"{len(fails)} CHECK(S) FAILED: " + ", ".join(fails))
    sys.exit(1)
print("ALL CHECKS PASSED — both apps render and interact correctly")
print("=" * 70)
