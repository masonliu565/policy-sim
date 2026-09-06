"""
policy-sim — Streamlit demo.

Architecture rule, enforced by construction: LLMs parse and explain, the
statistical model produces every number. This file reads /scenarios/*.json and
renders it. It does not import from /model, and it never computes a policy
figure of its own.

Second rule, enforced in app/charts.py: nothing renders as a bare point
estimate. Every figure carries its p05-p95 band.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import charts
from app.scenarios import ScenarioError, list_scenarios, load_scenario
from app.theme import palette

st.set_page_config(page_title="policy-sim", layout="wide", initial_sidebar_state="expanded")


# ---------------------------------------------------------------------------
# Chrome
# ---------------------------------------------------------------------------
def inject_css(mode: str) -> None:
    pal = palette(mode)
    st.markdown(
        f"""
        <style>
          .stApp {{ background: {pal['ocean']}; }}
          .block-container {{ padding-top: 2.2rem; max-width: 1600px; }}
          section[data-testid="stSidebar"] {{ background: {pal['panel']}; }}
          h1, h2, h3, h4, p, li, label, span {{ color: {pal['ink']}; }}
          h1 {{ font-size: 2.5rem; letter-spacing: -0.02em; }}
          h2 {{ font-size: 1.7rem; margin-top: 0.4rem; }}
          .ps-panel {{
            background: {pal['panel']}; border: 1px solid {pal['panel_edge']};
            border-radius: 10px; padding: 1.1rem 1.3rem; margin-bottom: 1rem;
          }}
          .ps-metric {{ font-size: 1.55rem; font-weight: 650; color: {pal['ink']}; }}
          .ps-delta  {{ font-size: 1.05rem; color: {pal['ink_soft']}; }}
          .ps-caveat {{
            background: {pal['warn_bg']}; border-left: 5px solid {pal['warn_edge']};
            padding: 0.7rem 1rem; margin: 0.35rem 0; border-radius: 4px;
            font-size: 1.02rem; color: {pal['ink']};
          }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def panel(body: str) -> None:
    st.markdown(f'<div class="ps-panel">{body}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Sidebar — scenario switcher
# ---------------------------------------------------------------------------
def sidebar() -> dict:
    st.sidebar.title("policy-sim")

    entries = list_scenarios()
    if not entries:
        st.sidebar.error("No files in /scenarios. Track A's precompute step (A7) has not run.")
        st.stop()

    labels = [f"{e['label']}  ·  {e['filename']}" for e in entries]
    idx = st.sidebar.selectbox(
        "Scenario", range(len(entries)), format_func=lambda i: labels[i], key="scenario_idx"
    )
    chosen = entries[idx]

    if chosen["error"]:
        st.sidebar.error(f"{chosen['filename']} could not be read:\n\n{chosen['error']}")
        st.stop()

    st.sidebar.caption(f"{len(entries)} scenario file(s) on disk")
    st.sidebar.divider()
    mode = "night" if st.sidebar.toggle("Night palette", value=False, key="night") else "day"
    return {"entry": chosen, "mode": mode}


# ---------------------------------------------------------------------------
# Section 1 — policy input
# ---------------------------------------------------------------------------
def section_policy_input() -> None:
    st.header("1 · Policy")
    st.text_area(
        "Describe a policy in plain English",
        placeholder="e.g. give every family $300 a month per kid, phase it out over $150k",
        height=110,
        key="policy_text",
    )
    st.button("Run", type="primary", key="run_policy")
    st.caption("Parsing is wired in at B2. The scenarios in the sidebar are precomputed.")


# ---------------------------------------------------------------------------
# Section 2 — material impact
# ---------------------------------------------------------------------------
IMPACT_LABELS = {
    "child_poverty_rate": "Child poverty rate",
    "overall_poverty_rate": "Overall poverty rate",
    "median_disposable_income": "Median disposable income",
    "annual_cost_usd": "Annual cost",
}
IMPACT_ORDER = list(IMPACT_LABELS)


def ordered_impacts(impact: dict):
    """Known outcomes first in a fixed order, then anything Track A adds."""
    keys = [k for k in IMPACT_ORDER if k in impact]
    keys += [k for k in impact if k not in IMPACT_LABELS]
    return keys


def section_impact(scenario: dict, mode: str) -> None:
    st.header("2 · Material impact")
    st.caption(
        "Bar spans the 5th–95th percentile across "
        f"{scenario.get('n_seeds', '?')} simulation seeds. "
        "Vertical slab is the median. Dashed red rule is the baseline — the "
        "world without the policy."
    )

    impact = scenario["impact"]
    keys = ordered_impacts(impact)

    for left, right in zip(keys[0::2], keys[1::2] + [None] * (len(keys) % 2)):
        cols = st.columns(2, gap="large")
        for col, key in zip(cols, (left, right)):
            if key is None:
                continue
            with col:
                _impact_card(key, impact[key], mode)


def _impact_card(key: str, band: dict, mode: str) -> None:
    unit = charts.infer_unit(key)
    label = IMPACT_LABELS.get(key, key.replace("_", " ").capitalize())
    delta = charts.delta_text(band, unit)

    panel(
        f"<div style='font-size:1.05rem;letter-spacing:.02em'>{label}</div>"
        f"<div class='ps-metric'>{charts.interval_text(band, unit)}</div>"
        f"<div class='ps-delta'>{delta or ''}</div>"
    )
    fig = charts.interval_figure(
        [charts.impact_row(key, band, label)], unit=unit, mode=mode, width=8.0
    )
    st.pyplot(fig, width='stretch')
    charts.close(fig)


# ---------------------------------------------------------------------------
# Section 3 — support by group
# ---------------------------------------------------------------------------
GROUP_TYPE_LABELS = {
    "income_quintile": "By income quintile",
    "census_region": "By census region",
    "household_type": "By household type",
}


def section_opinion(scenario: dict, mode: str) -> None:
    st.header("3 · Support by group")

    opinion = scenario.get("opinion", {})
    overall = opinion.get("overall_support")
    if overall:
        panel(
            "<div style='font-size:1.05rem'>Overall support</div>"
            f"<div class='ps-metric'>{charts.interval_text(overall, charts.PERCENT)}</div>"
            "<div class='ps-delta'>poststratified from survey crosstabs; "
            "band combines crosstab sampling error with population sampling error</div>"
        )

    groups = opinion.get("by_group", [])
    if not groups:
        st.info("This scenario carries no subgroup opinion estimates.")
        return

    by_type: dict[str, list] = {}
    for g in groups:
        by_type.setdefault(g["group_type"], []).append(g)

    for gtype, members in by_type.items():
        st.subheader(GROUP_TYPE_LABELS.get(gtype, gtype.replace("_", " ").title()))
        rows = [
            {
                "label": m["group"],
                "p05": m["support"]["p05"],
                "median": m["support"]["median"],
                "p95": m["support"]["p95"],
                "baseline": None,
            }
            for m in members
        ]
        fig = charts.interval_figure(
            rows, unit=charts.PERCENT, mode=mode,
            xlabel="support (bar = 5th–95th percentile)", show_baseline=False, width=9.0,
        )
        st.pyplot(fig, width='stretch')
        charts.close(fig)

        for m in members:
            st.markdown(
                f"**{m['group']}** — support "
                f"{charts.interval_text(m['support'], charts.PERCENT)} · "
                f"evidence: `{'`, `'.join(m.get('evidence_ids') or ['none'])}`"
            )

        _uninterval_note(members)


def _uninterval_note(members: list) -> None:
    """Fields the scenario gives as bare numbers, shown as such and no other way.

    households_weighted, disposable_income_delta and pct_better_off arrive
    without p05/p95. Rendering them beside the banded figures would imply a
    precision the scenario does not claim, so they are quarantined here and
    labelled. This block is the honest alternative to either faking a band or
    dropping the data.
    """
    fields = ("households_weighted", "disposable_income_delta", "pct_better_off")
    present = [m for m in members if any(f in m for f in fields)]
    if not present:
        return

    lines = []
    for m in present:
        bits = []
        if "households_weighted" in m:
            bits.append(f"{m['households_weighted']:,.0f} households")
        if "disposable_income_delta" in m:
            bits.append(f"{charts.format_delta(m['disposable_income_delta'], charts.CURRENCY)} income")
        if "pct_better_off" in m:
            bits.append(f"{m['pct_better_off'] * 100:.0f}% better off")
        lines.append(f"<li><b>{m['group']}</b> — {' · '.join(bits)}</li>")

    st.markdown(
        "<div class='ps-caveat'><b>Point estimates — no interval published for these.</b> "
        "The scenario contract carries p05/p95 for outcomes and support only. "
        "Read the figures below as central values with unstated uncertainty."
        f"<ul style='margin:.4rem 0 0 0'>{''.join(lines)}</ul></div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 4 — limitations
# ---------------------------------------------------------------------------
def section_limitations(scenario: dict) -> None:
    st.header("4 · Limitations")
    st.caption("Every one of these is a reason a number above could be wrong. They are the model's own disclosures.")

    warnings = scenario.get("warnings") or []
    if not warnings:
        st.markdown(
            "<div class='ps-caveat'>This scenario declares no limitations. "
            "That is itself worth questioning.</div>",
            unsafe_allow_html=True,
        )
    for w in warnings:
        st.markdown(f"<div class='ps-caveat'>{w}</div>", unsafe_allow_html=True)

    for note in scenario.get("_notes") or []:
        st.markdown(
            f"<div class='ps-caveat'><b>Contract note:</b> {note}</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
def main() -> None:
    state = sidebar()
    inject_css(state["mode"])

    try:
        scenario = load_scenario(state["entry"]["path"])
    except ScenarioError as exc:
        st.error(f"Scenario failed validation: {exc}")
        st.stop()
        return

    st.title(scenario.get("label", scenario.get("policy_id", "policy-sim")))
    st.caption(
        f"`{scenario.get('policy_id')}` · {scenario.get('n_seeds', '?')} seeds · "
        f"read from `{Path(state['entry']['path']).name}` — no live model call"
    )

    section_policy_input()
    st.divider()
    section_impact(scenario, state["mode"])
    st.divider()
    section_opinion(scenario, state["mode"])
    st.divider()
    section_limitations(scenario)


main()
