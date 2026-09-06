"""
policy-sim.

Four things on screen: the map, one outcome box, the policy box, and reset.

It runs on its own. No API key is required and no network call is made: the
policy box is read by app/local_parser.py offline, and the numbers come from
the real microsimulation running locally over the sampled ACS population. A
key is optional and only ever used to read unusual English into levers -- it
never produces a number.

    python -m streamlit run app/main.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit as st
import streamlit.components.v1 as components

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from app import live, local_parser  # noqa: E402
from app import scenarios as S  # noqa: E402
from app.geo import CITIES, US_OUTLINE  # noqa: E402

_map = components.declare_component("policy_sim_map",
                                    path=str(Path(__file__).parent / "frontend_map"))

BEIGE, GREEN, INK, MUTED = "#f9f9ef", "#31553f", "#344c40", "#7e8c73"
HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()
               or (REPO / ".env").exists())

st.set_page_config(page_title="policy-sim", layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown(f"""
<style>
  #MainMenu, footer, header {{ visibility: hidden; }}
  .stAppDeployButton {{ display: none; }}
  .block-container {{ padding: 1.5rem 2.2rem 3rem; max-width: 1420px; }}
  .stApp {{ background: #eef2e6; }}
  html, body, [class*="css"] {{
     font-family: "DM Sans", -apple-system, "Segoe UI", sans-serif; color: {INK}; }}
  .ps-head {{ display:flex; align-items:baseline; gap:13px; margin-bottom:13px; }}
  .ps-title {{ font-size:25px; font-weight:700; letter-spacing:-.9px; color:{GREEN}; }}
  .ps-sub {{ font-size:11px; color:{MUTED}; }}
  .ps-card {{ background:{BEIGE}; border:1px solid #dfe5d3; border-radius:14px;
              padding:17px 20px; }}
  .ps-eyebrow {{ font-size:8.5px; font-weight:650; letter-spacing:1.5px;
                 text-transform:uppercase; color:{MUTED}; }}
  .ps-scope {{ font-size:19px; font-weight:650; letter-spacing:-.4px;
               color:{GREEN}; margin:3px 0 13px; }}
  .ps-grid {{ display:grid; grid-template-columns:repeat(5,1fr); }}
  .ps-cell {{ padding:2px 18px; border-left:1px solid #e8ebdd; }}
  .ps-cell:first-child {{ padding-left:0; border-left:0; }}
  .ps-label {{ font-size:10.5px; color:{MUTED}; }}
  .ps-value {{ font-size:23px; font-weight:650; letter-spacing:-.7px; color:{INK};
               font-variant-numeric:tabular-nums; line-height:1.25; }}
  .ps-band {{ font-size:10.5px; color:#8d9a83; font-variant-numeric:tabular-nums; }}
  .ps-delta {{ font-size:10.5px; color:#5c7f63; font-variant-numeric:tabular-nums; }}
  .ps-none {{ font-size:12.5px; font-weight:600; color:#9aa48d; }}
  .ps-note {{ font-size:10.5px; color:{MUTED}; line-height:1.55; margin-top:4px; }}
  .ps-flag {{ display:inline-block; font-size:8.5px; font-weight:650;
              background:#eaf0da; color:#5d7a4c; border-radius:4px;
              padding:2px 6px; margin-left:7px; }}
  div[data-testid="stTextArea"] textarea {{
     background:{BEIGE}; border:1px solid #dfe5d3; border-radius:10px;
     font-size:13.5px; color:{INK}; }}
  .stButton > button {{ background:{BEIGE}; color:{GREEN}; border:1px solid #dfe5d3;
     border-radius:9px; font-size:12px; font-weight:600; padding:10px 15px; }}
  .stButton > button:hover {{ background:#eef2e2; border-color:#cdd8bd; color:{GREEN}; }}
  div[data-testid="stExpander"] {{ border:0; }}
  div[data-testid="stExpander"] summary {{ font-size:11px; color:{MUTED}; }}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
def pct(v):
    return "—" if v is None else f"{100 * v:.1f}%"


def usd(v):
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:,.1f}B"
    if a >= 1e6:
        return f"${v / 1e6:,.1f}M"
    return f"${v:,.0f}"


ROWS = [("child_poverty_rate", "Child poverty rate", pct, "pts"),
        ("overall_poverty_rate", "Overall poverty rate", pct, "pts"),
        ("median_disposable_income", "Median disposable income", usd, "usd"),
        ("annual_cost_usd", "Annual cost", usd, "usd")]


def cell(key, label, fmt, kind, impact):
    b = impact.get(key) or {}
    med, p05, p95, base = b.get("median"), b.get("p05"), b.get("p95"), b.get("baseline")
    if kind == "pts" and med is not None and base is not None:
        delta = f"{100 * (med - base):+.2f} pts vs baseline"
    elif med is not None and base:
        delta = f"{usd(med - base)} vs baseline"
    else:
        delta = ""
    band = (f"{fmt(p05)} – {fmt(p95)}" if p05 != p95 else f"{fmt(p05)} · no spread")
    return (f'<div class="ps-cell"><div class="ps-label">{label}</div>'
            f'<div class="ps-value">{fmt(med)}</div>'
            f'<div class="ps-band">{band}</div>'
            f'<div class="ps-delta">{delta}</div></div>')


def support_cell(sc, metro):
    if metro:
        return ('<div class="ps-cell"><div class="ps-label">Public support</div>'
                '<div class="ps-none">national only</div>'
                '<div class="ps-note">Opinion evidence is not broken out by '
                'metro.</div></div>')
    s = (sc.get("opinion") or {}).get("overall_support")
    if s:
        return (f'<div class="ps-cell"><div class="ps-label">Public support</div>'
                f'<div class="ps-value">{pct(s["median"])}</div>'
                f'<div class="ps-band">{pct(s["p05"])} – {pct(s["p95"])}</div></div>')
    reason = ("outside the evidence base" if sc.get("in_support") is False
              else "insufficient evidence")
    near = (sc.get("nearest_policies") or [{}])[0]
    tail = (f'<div class="ps-note">Nearest we have data on: {near["label"]}</div>'
            if near.get("label") else "")
    return (f'<div class="ps-cell"><div class="ps-label">Public support</div>'
            f'<div class="ps-none">{reason}</div>'
            f'<div class="ps-note">Impact figures unaffected.</div>{tail}</div>')


# ---------------------------------------------------------------------------
for k, v in (("metro", None), ("policy_text", ""), ("result", None),
             ("readback", None)):
    st.session_state.setdefault(k, v)

mode = ("running locally · no network calls" if not HAS_KEY
        else "running locally · Claude available for unusual phrasing")
st.markdown(f'<div class="ps-head"><span class="ps-title">policy-sim</span>'
            f'<span class="ps-sub">{mode}</span></div>', unsafe_allow_html=True)

options = S.list_scenarios()
labels = [o["label"] for o in options]
default = ("Expanded Child Tax Credit (2021)" if "Expanded Child Tax Credit (2021)"
           in labels else (labels[0] if labels else None))

c1, c2 = st.columns([5, 1])
with c1:
    # Indices with a format function, not label strings: the selection is
    # positional everywhere else (and in the tests), and two scenarios could
    # legitimately share a label.
    choice = st.selectbox("Scenario", range(len(labels)),
                          index=labels.index(default) if default else 0,
                          format_func=lambda i: labels[i],
                          label_visibility="collapsed", key="scenario_idx",
                          disabled=st.session_state.result is not None)
with c2:
    if st.button("Reset", use_container_width=True):
        for k in ("metro", "policy_text", "result", "readback"):
            st.session_state[k] = None if k != "policy_text" else ""
        st.rerun()

if st.session_state.result is not None:
    sc = st.session_state.result
else:
    entry = options[choice]
    if entry.get("error"):
        # A malformed file must not take the page down with it.
        st.error(f"{entry.get('filename', 'scenario')} could not be read:\n\n"
                 f"{entry['error']}")
        st.stop()
    try:
        sc = S.load_scenario(Path(entry["path"]))
    except Exception as exc:                       # noqa: BLE001
        st.error(f"{entry.get('filename', 'scenario')} could not be read:\n\n{exc}")
        st.stop()

# ---- map ------------------------------------------------------------------
by_metro = {m.get("metro"): m for m in (sc.get("by_metro") or [])}
metro_args = []
for c in CITIES:
    m = by_metro.get(c["label"])
    cpr = ((m or {}).get("impact") or {}).get("child_poverty_rate") or {}
    metro_args.append({"metro": c["label"], "lon": c["lon"], "lat": c["lat"],
                       "value": pct(cpr.get("median")) if m else None})

picked = _map(outline=[list(p) for p in US_OUTLINE], metros=metro_args,
              selected=st.session_state.metro or "", key="map",
              default=st.session_state.metro or "")
picked = picked or None          # "" is the component's "national" sentinel
if picked != st.session_state.metro:
    st.session_state.metro = picked
    st.rerun()

# ---- outcome box ----------------------------------------------------------
metro = st.session_state.metro
entry = by_metro.get(metro) if metro else None
impact = (entry or sc).get("impact") or {}
scope = metro if entry else "United States"
flag = ('<span class="ps-flag">bands ~3.9x wider than national</span>'
        if entry else ('<span class="ps-flag">simulated just now</span>'
                       if sc.get("_live") else ""))
cells = "".join(cell(k, l, f, kd, impact) for k, l, f, kd in ROWS)
cells += support_cell(sc, entry)
st.markdown(f'<div class="ps-card"><div class="ps-eyebrow">Outcome</div>'
            f'<div class="ps-scope">{scope}{flag}</div>'
            f'<div class="ps-grid">{cells}</div></div>', unsafe_allow_html=True)

# ---- policy box -----------------------------------------------------------
st.write("")
b1, b2 = st.columns([5, 1])
with b1:
    text = st.text_area("Policy", key="policy_text", height=76,
                        label_visibility="collapsed",
                        placeholder="Describe a policy — e.g. $300 a month per "
                                    "child, phased out over $150k")
with b2:
    st.write("")
    go = st.button("Run it", use_container_width=True, type="primary")

if go:
    parsed = local_parser.parse(text)
    if not parsed.ok and HAS_KEY:
        # The offline reader handles the phrasings people actually type. When it
        # cannot, and a key is configured, Claude gets a turn -- still only to
        # turn English into levers. It never produces a number.
        try:
            from app.parser import parse_policy
            out = parse_policy(text)
            if getattr(out, "ok", False):
                lv = out.spec.levers.model_dump()
                parsed = local_parser.LocalParse(
                    levers={**local_parser.EMPTY, **lv},
                    understood=[f"{k.replace('_', ' ')}: {v}"
                                for k, v in lv.items() if v not in (0, 0.0, None)],
                    notes=["Read by Claude; the offline reader could not parse it."],
                    ok=True)
        except Exception as exc:                    # noqa: BLE001
            parsed.notes.append(f"Claude could not be reached ({exc}). "
                                f"The offline reader's result stands.")
    if not parsed.ok:
        st.session_state.readback = ("err", parsed.notes)
        st.session_state.result = None
    elif not live.available():
        st.session_state.readback = ("err", [live.why_unavailable()])
    else:
        with st.spinner("Simulating on 30,000 households…"):
            st.session_state.result = live.run(parsed.levers)
        st.session_state.metro = None
        st.session_state.readback = ("ok", parsed.understood + parsed.notes)
    st.rerun()

rb = st.session_state.readback
if rb:
    kind, lines = rb
    if kind == "ok":
        st.markdown('<div class="ps-note"><b>Read as:</b> '
                    + " · ".join(lines) + "</div>", unsafe_allow_html=True)
    else:
        st.markdown('<div class="ps-card"><div class="ps-none">'
                    + "<br>".join(lines) + "</div></div>", unsafe_allow_html=True)

# ---- policy memo (optional; needs a key) ----------------------------------
# The button lives in an expander so the default page stays clean. The memo and
# its verification render at TOP LEVEL once one exists, because "0 unverified
# numbers" is the point of the feature and burying it defeats it.
with st.expander("Write a policy memo"):
    st.markdown('<div class="ps-note">Claude writes the prose. It is given the '
                'scenario JSON and the evidence records and forbidden from '
                'producing any number that is not already in them; the check '
                'below confirms that mechanically, afterwards. Every figure '
                'above was produced without it.</div>', unsafe_allow_html=True)
    if not HAS_KEY:
        st.markdown('<div class="ps-note">No ANTHROPIC_API_KEY is set, so the '
                    'memo is unavailable. Everything else on this page works '
                    'without one.</div>', unsafe_allow_html=True)
    if st.button("Write the memo", key="run_report", disabled=not HAS_KEY):
        from app.report import generate_report
        clean = {k: v for k, v in sc.items() if not str(k).startswith("_")}
        with st.spinner("Writing…"):
            st.session_state["report_outcome"] = generate_report(clean, [])

outcome = st.session_state.get("report_outcome")
if outcome is not None:
    from app.report import ReportError
    if isinstance(outcome, ReportError):
        st.markdown(f'<div class="ps-card"><div class="ps-none">'
                    f'{outcome.message}</div><div class="ps-note">'
                    f'{outcome.detail or ""}</div></div>', unsafe_allow_html=True)
    else:
        v = outcome.verification
        st.subheader("Numeric verification")
        if v.ok:
            st.markdown(f'<div class="ps-note"><b>{v.checked} numeric tokens '
                        f'checked · 0 unverified.</b> Every number in the memo '
                        f'appears in the scenario JSON or the evidence records '
                        f'it was given, and every cited evidence id exists.'
                        f'</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="ps-note"><b>{v.checked} numeric tokens '
                        f'checked · {v.unverified_count} unverified.</b> These '
                        f'appear in the memo but not in the input. Treat them as '
                        f'unsourced.</div>', unsafe_allow_html=True)
            for f in v.findings:
                st.markdown(f'<div class="ps-note"><code>{f.token}</code> — '
                            f'{f.context}</div>', unsafe_allow_html=True)
            for eid in v.missing_evidence_ids:
                st.markdown(f'<div class="ps-note">cited evidence id '
                            f'<code>{eid}</code> does not exist</div>',
                            unsafe_allow_html=True)
        st.markdown(outcome.text)

with st.expander("What this model does not know"):
    for w in (sc.get("warnings") or [])[:9]:
        st.markdown(f'<div class="ps-note">• {w}</div>', unsafe_allow_html=True)
