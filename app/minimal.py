"""
Minimal presentation UI.

Four things on screen and nothing else: the map, the outcome box, the policy
text box, and reset. Everything richer -- the memo, the numeric verification
panel, the layer menu -- lives in app/main.py and is unchanged.

    POLICY_SIM_DEMO_MODE=1 streamlit run app/minimal.py

The map is a hand-drawn continental USA on a canvas, in the same illustrated
language as the reference project in the palette the deck uses: light beige
panels, soft green land, pastel blue water. Clicking a metro swaps the outcome
box to that metro's own numbers, which are computed on the metro subpopulation
rather than scaled down from the national figure.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import scenarios as S  # noqa: E402

_FRONTEND = Path(__file__).resolve().parent / "frontend_minimal"
_map = components.declare_component("policy_sim_minimal_map", path=str(_FRONTEND))

DEMO = os.environ.get("POLICY_SIM_DEMO_MODE", "").strip() not in ("", "0", "false")

BEIGE, GREEN, INK, MUTED = "#f9f9ef", "#31553f", "#344c40", "#7e8c73"

st.set_page_config(page_title="policy-sim", layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown(f"""
<style>
  #MainMenu, footer, header {{ visibility: hidden; }}
  .stAppDeployButton {{ display: none; }}
  .block-container {{ padding: 1.6rem 2.2rem 2.4rem; max-width: 1500px; }}
  html, body, [class*="css"] {{
     font-family: "DM Sans", -apple-system, "Segoe UI", sans-serif; color: {INK}; }}
  .stApp {{ background: #eef2e6; }}

  .ps-head {{ display:flex; align-items:baseline; gap:14px; margin-bottom:14px; }}
  .ps-title {{ font-size:26px; font-weight:700; letter-spacing:-.9px; color:{GREEN}; }}
  .ps-sub {{ font-size:11px; color:{MUTED}; letter-spacing:.02em; }}

  .ps-card {{ background:{BEIGE}; border:1px solid #dfe5d3; border-radius:14px;
              padding:18px 20px; }}
  .ps-eyebrow {{ font-size:8.5px; font-weight:650; letter-spacing:1.5px;
                 text-transform:uppercase; color:{MUTED}; }}
  .ps-scope {{ font-size:19px; font-weight:650; letter-spacing:-.4px;
               color:{GREEN}; margin:4px 0 14px; }}
  .ps-row {{ padding:11px 0; border-top:1px solid #e8ebdd; }}
  .ps-row:first-of-type {{ border-top:0; }}
  .ps-grid {{ display:grid; grid-template-columns:repeat(5,1fr); gap:0; }}
  .ps-cell {{ padding:2px 20px; border-left:1px solid #e8ebdd; }}
  .ps-cell:first-child {{ padding-left:0; border-left:0; }}
  .ps-label {{ font-size:10.5px; color:{MUTED}; }}
  .ps-value {{ font-size:23px; font-weight:650; letter-spacing:-.7px;
               color:{INK}; font-variant-numeric:tabular-nums; line-height:1.25; }}
  .ps-band {{ font-size:10.5px; color:#8d9a83; font-variant-numeric:tabular-nums; }}
  .ps-delta {{ font-size:10.5px; color:#5c7f63; font-variant-numeric:tabular-nums; }}
  .ps-none {{ font-size:12.5px; font-weight:600; color:#9aa48d; }}
  .ps-note {{ font-size:10.5px; color:{MUTED}; line-height:1.6; margin-top:4px; }}
  .ps-flag {{ display:inline-block; font-size:8.5px; font-weight:650;
              letter-spacing:.06em; background:#eaf0da; color:#5d7a4c;
              border-radius:4px; padding:2px 6px; margin-left:7px; }}

  div[data-testid="stTextArea"] textarea {{
     background:{BEIGE}; border:1px solid #dfe5d3; border-radius:10px;
     font-size:13px; color:{INK}; }}
  div[data-testid="stTextArea"] label {{ font-size:10.5px; color:{MUTED}; }}
  .stButton > button {{
     background:{BEIGE}; color:{GREEN}; border:1px solid #dfe5d3;
     border-radius:8px; font-size:11px; font-weight:600; padding:9px 15px; }}
  .stButton > button:hover {{ background:#eef2e2; border-color:#cdd8bd;
     color:{GREEN}; }}
  div[data-testid="stSelectbox"] label {{ font-size:10.5px; color:{MUTED}; }}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------
def pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{100 * v:.1f}%"


def usd(v: Optional[float]) -> str:
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:,.1f}B"
    if a >= 1e6:
        return f"${v / 1e6:,.1f}M"
    return f"${v:,.0f}"


ROWS = [
    ("child_poverty_rate", "Child poverty rate", pct, "pts"),
    ("overall_poverty_rate", "Overall poverty rate", pct, "pts"),
    ("median_disposable_income", "Median disposable income", usd, "usd"),
    ("annual_cost_usd", "Annual cost", usd, "usd"),
]


def band_cell(key: str, label: str, fmt, kind: str, impact: Dict[str, Any]) -> str:
    b = impact.get(key) or {}
    med, p05, p95, base = (b.get("median"), b.get("p05"),
                           b.get("p95"), b.get("baseline"))
    if kind == "pts" and med is not None and base is not None:
        d = 100 * (med - base)
        delta = f"{d:+.2f} pts vs baseline"
    elif med is not None and base is not None:
        delta = f"{usd(med - base)} vs baseline"
    else:
        delta = ""
    if kind == "usd" and base == 0:
        delta = ""          # "+$162B vs a baseline of zero" says nothing
    zero = (p05 is not None and p95 is not None and p05 == p95)
    band = (f"{fmt(p05)} – {fmt(p95)}" if not zero
            else f"{fmt(p05)} · no spread")
    return (f'<div class="ps-cell"><div class="ps-label">{label}</div>'
            f'<div class="ps-value">{fmt(med)}</div>'
            f'<div class="ps-band">{band}</div>'
            f'<div class="ps-delta">{delta}</div></div>')


def support_cell(sc: Dict[str, Any]) -> str:
    op = sc.get("opinion") or {}
    s = op.get("overall_support")
    if s:
        return (f'<div class="ps-cell"><div class="ps-label">Public support</div>'
                f'<div class="ps-value">{pct(s["median"])}</div>'
                f'<div class="ps-band">{pct(s["p05"])} – {pct(s["p95"])}</div></div>')
    reason = ("outside the evidence base" if sc.get("in_support") is False
              else "insufficient evidence")
    near = (sc.get("nearest_policies") or [{}])[0]
    tail = (f'<div class="ps-note">Nearest we have data on: '
            f'{near.get("label", "—")}</div>' if near.get("label") else "")
    return (f'<div class="ps-cell"><div class="ps-label">Public support</div>'
            f'<div class="ps-none">{reason}</div>'
            f'<div class="ps-note">Impact figures unaffected.</div>{tail}</div>')


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------
if "metro" not in st.session_state:
    st.session_state.metro = None
if "policy_text" not in st.session_state:
    st.session_state.policy_text = ""
if "readback" not in st.session_state:
    st.session_state.readback = None

options = S.list_scenarios()
if not options:
    st.error("No scenario files found in /scenarios.")
    st.stop()

st.markdown(
    '<div class="ps-head"><span class="ps-title">policy-sim</span>'
    f'<span class="ps-sub">{"demo mode · precomputed scenarios · no network calls" if DEMO else "reading precomputed scenarios"}'
    '</span></div>', unsafe_allow_html=True)

head_l, head_r = st.columns([5, 1])
with head_l:
    labels = [o["label"] for o in options]
    idx = labels.index("Expanded Child Tax Credit (2021)") \
        if "Expanded Child Tax Credit (2021)" in labels else 0
    choice = st.selectbox("Scenario", labels, index=idx, label_visibility="collapsed")
with head_r:
    if st.button("Reset", use_container_width=True):
        st.session_state.metro = None
        st.session_state.policy_text = ""
        st.session_state.readback = None
        st.rerun()

sc = S.load_scenario(Path(options[labels.index(choice)]["path"]))

# ---------------------------------------------------------------------------
# map + outcome box
# ---------------------------------------------------------------------------
metro_args = [
    {"metro": m.get("metro"),
     "value": pct(((m.get("impact") or {}).get("child_poverty_rate") or {}).get("median"))}
    for m in (sc.get("by_metro") or [])
]
picked = _map(metros=metro_args, selected=st.session_state.metro,
              key="usa_map", default=st.session_state.metro)
if picked != st.session_state.metro:
    st.session_state.metro = picked
    st.rerun()

st.write("")
metro = st.session_state.metro
entry = next((m for m in (sc.get("by_metro") or [])
              if m.get("metro") == metro), None) if metro else None
impact = (entry or sc).get("impact") or {}
scope = metro if entry else "United States"
flag = '<span class="ps-flag">bands ~3.9x wider than national</span>' if entry else ""
cells = "".join(band_cell(k, lbl, f, kind, impact) for k, lbl, f, kind in ROWS)
cells += support_cell(sc) if not entry else (
    '<div class="ps-cell"><div class="ps-label">Public support</div>'
    '<div class="ps-none">national only</div>'
    '<div class="ps-note">Opinion evidence is not broken out by metro.</div></div>')
st.markdown(
    f'<div class="ps-card"><div class="ps-eyebrow">Outcome</div>'
    f'<div class="ps-scope">{scope}{flag}</div>'
    f'<div class="ps-grid">{cells}</div></div>',
    unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# policy text box
# ---------------------------------------------------------------------------
st.write("")
box_l, box_r = st.columns([5, 1], gap="small")
with box_l:
    text = st.text_area(
        "Describe a policy in plain English", key="policy_text", height=78,
        placeholder="e.g. give every family $300 a month per kid, phase it out over $150k",
        label_visibility="collapsed")
with box_r:
    st.write("")
    read = st.button("Read it", use_container_width=True)

if read:
    if not text.strip():
        st.session_state.readback = ("err", "Enter a policy description first.")
    else:
        from app.parser import parse_policy  # imported lazily: needs pydantic
        out = parse_policy(text)
        if getattr(out, "ok", False):
            lv = out.spec.levers.model_dump()
            st.session_state.readback = ("ok", lv)
        else:
            msg = (f"{getattr(out, 'headline', 'Could not read that as a policy')} "
                   f"— {getattr(out, 'detail', '')}").strip(" —")
            # The parser's copy points at a sidebar, which app/main.py has and
            # this layout does not. Re-point it rather than editing the parser.
            msg = msg.replace("from the sidebar", "from the picker above")
            st.session_state.readback = ("err", msg)
    st.rerun()

rb = st.session_state.readback
if rb:
    kind, payload = rb
    if kind == "ok":
        items = "".join(
            f'<div class="ps-row"><div class="ps-label">{k.replace("_", " ")}</div>'
            f'<div class="ps-band">{v}</div></div>' for k, v in payload.items())
        st.markdown(f'<div class="ps-card"><div class="ps-eyebrow">Read back</div>'
                    f'<div class="ps-note">This is what the description was '
                    f'understood to mean. The figures above stay on the selected '
                    f'precomputed scenario.</div>{items}</div>',
                    unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="ps-card"><div class="ps-eyebrow">Read back</div>'
                    f'<div class="ps-none" style="margin-top:6px">{payload}</div>'
                    f'</div>', unsafe_allow_html=True)
