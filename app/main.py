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

import json
import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import charts
from app.parser import ParseError, ParseResult, parse_policy
from app.report import ReportError, ReportResult, generate_report
from app.scenarios import ScenarioError, list_scenarios, load_scenario
from app.theme import FONT_MONO, palette

st.set_page_config(page_title="policy-sim", layout="wide", initial_sidebar_state="expanded")


def demo_mode() -> bool:
    """Scenario files only, zero network calls.

    Set POLICY_SIM_DEMO_MODE=1 for the live demo. The parser and the memo are
    the only things that touch the network, and both refuse to run under it —
    so the scripted path cannot be broken by wifi, a rate limit or an expired
    key. Read per call rather than cached so it can be flipped without a
    restart.
    """
    return os.environ.get("POLICY_SIM_DEMO_MODE", "").lower() in ("1", "true", "yes")


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
          .ps-demo {{
            background: {pal['terrain']}; color: {pal['panel']};
            padding: .5rem 1rem; border-radius: 6px; font-weight: 650;
            margin-bottom: .9rem; font-size: 1.05rem;
          }}
          .ps-figure {{
            border-top: 2px solid {pal['ink']}; padding: .55rem 0 .1rem 0;
            margin-bottom: .2rem;
          }}
          .ps-figure-label {{ font-size: 1.05rem; color: {pal['ink_soft']}; }}
          .ps-metric {{ font-size: 1.55rem; font-weight: 650; color: {pal['ink']}; }}
          .ps-delta  {{ font-size: 1.05rem; color: {pal['ink_soft']}; }}
          .ps-readback {{
            background: {pal['panel']}; border: 3px solid {pal['accent']};
            border-radius: 12px; padding: 1.2rem 1.4rem; margin: 1rem 0 0.6rem 0;
          }}
          .ps-readback h3 {{ margin: 0 0 .2rem 0; font-size: 1.45rem; }}
          .ps-lever {{ font-family: {FONT_MONO}; font-size: 1.05rem; }}
          .ps-verify-ok {{
            background: {pal['panel']}; border-left: 6px solid {pal['terrain']};
            padding: .9rem 1.1rem; border-radius: 6px; font-size: 1.1rem;
          }}
          .ps-verify-bad {{
            background: {pal['warn_bg']}; border-left: 6px solid {pal['baseline']};
            padding: .9rem 1.1rem; border-radius: 6px; font-size: 1.05rem;
          }}
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
# Section 1 — policy input, and the read-back panel
# ---------------------------------------------------------------------------
LEVER_LABELS = {
    "credit_per_child_under_6": "Credit per child under 6 ($/yr)",
    "credit_per_child_6_to_17": "Credit per child 6–17 ($/yr)",
    "fully_refundable": "Fully refundable",
    "phaseout_start_single": "Phaseout starts, single ($)",
    "phaseout_start_joint": "Phaseout starts, joint ($)",
    "phaseout_rate": "Phaseout rate (fraction)",
    "flat_transfer_per_adult": "Flat transfer per adult ($/yr)",
}


def section_policy_input() -> None:
    st.header("1 · Policy")

    col_text, col_go = st.columns([5, 1], gap="medium", vertical_alignment="bottom")
    with col_text:
        text = st.text_area(
            "Describe a policy in plain English",
            placeholder="e.g. give every family $300 a month per kid, phase it out over $150k",
            height=110,
            key="policy_text",
        )
    with col_go:
        run = st.button("Read it", type="primary", key="run_policy",
                        width="stretch", disabled=demo_mode())

    if demo_mode():
        st.caption(
            "Parsing is disabled in demo mode — it is the only part of this page "
            "that would call out to a model. Every figure below is precomputed."
        )

    if run and not demo_mode():
        with st.spinner("Reading the policy…"):
            st.session_state["parse_outcome"] = parse_policy(text)

    outcome = st.session_state.get("parse_outcome")
    if outcome is None:
        st.caption(
            "The scenarios in the sidebar are precomputed and need no network call. "
            "Parsing free text calls the model to translate it into levers — it never "
            "produces a number you see on this page."
        )
        return

    if isinstance(outcome, ParseError):
        _render_parse_error(outcome)
    else:
        _render_readback(outcome)


def _render_parse_error(err: ParseError) -> None:
    st.markdown(
        f"<div class='ps-readback' style='border-color:#A6453B'>"
        f"<h3>Could not read that as a policy</h3>"
        f"<div style='font-size:1.1rem'>{err.message}</div>"
        + (f"<div class='ps-delta' style='margin-top:.5rem'>{err.detail}</div>" if err.detail else "")
        + "<div class='ps-delta' style='margin-top:.6rem'>Nothing was simulated. "
          "Pick a precomputed scenario from the sidebar.</div></div>",
        unsafe_allow_html=True,
    )


def _render_readback(result: ParseResult) -> None:
    """The panel that makes this an instrument rather than a black box.

    Deliberately not an expander: the whole point is that the reading is
    visible without the user going looking for it.
    """
    spec = result.spec
    st.markdown(
        f"<div class='ps-readback'><h3>Here's how I read your policy</h3>"
        f"<div class='ps-delta'>{spec.label} · <code>{spec.instrument}</code></div></div>",
        unsafe_allow_html=True,
    )

    st.caption("Every field is editable. If the reading is wrong, correct it here.")
    levers = spec.levers.model_dump()
    edited: dict = {}

    cols = st.columns(3, gap="medium")
    for i, (name, value) in enumerate(levers.items()):
        label = LEVER_LABELS.get(name, name)
        with cols[i % 3]:
            if isinstance(value, bool):
                edited[name] = st.checkbox(label, value=value, key=f"lv_{name}")
            elif name == "phaseout_rate":
                edited[name] = st.number_input(
                    label, value=float(value), min_value=0.0, max_value=1.0,
                    step=0.01, format="%.3f", key=f"lv_{name}",
                )
            else:
                edited[name] = st.number_input(
                    label, value=None if value is None else float(value),
                    min_value=0.0, step=100.0, format="%.0f", key=f"lv_{name}",
                    placeholder="not set",
                )

    _render_uncertainties(spec.parser_uncertainties)

    st.markdown(
        "<div class='ps-caveat'><b>These levers are not yet simulated.</b> "
        "The figures below come from the precomputed scenario selected in the "
        "sidebar, not from this reading. Wiring the live engine is Track A's A7 "
        "step.</div>",
        unsafe_allow_html=True,
    )
    st.session_state["edited_levers"] = edited


def _render_uncertainties(items: list) -> None:
    st.markdown("#### What I had to guess")
    if not items:
        st.markdown(
            "<div class='ps-caveat'>The parser reported no inferred fields — it "
            "claims your text stated everything explicitly. Worth checking against "
            "the levers above.</div>",
            unsafe_allow_html=True,
        )
        return
    for item in items:
        st.markdown(f"<div class='ps-caveat'>{item}</div>", unsafe_allow_html=True)


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

    # Deliberately not a bordered card. Four identical rounded boxes in a grid
    # read as chrome; a label, a figure and a rule read as a result.
    st.markdown(
        f"<div class='ps-figure'>"
        f"<div class='ps-figure-label'>{label}</div>"
        f"<div class='ps-metric'>{charts.interval_text(band, unit)}</div>"
        f"<div class='ps-delta'>{delta or ''}</div>"
        f"</div>",
        unsafe_allow_html=True,
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
# Section 5 — memo, and the numeric verification of it
# ---------------------------------------------------------------------------
def section_report(scenario: dict) -> None:
    st.header("5 · Policy memo")
    st.caption(
        "The model writes the prose. It is given the scenario JSON and the "
        "evidence records, and forbidden from producing any number that is not "
        "already in them. The panel below checks that mechanically, afterwards."
    )

    if demo_mode():
        st.caption(
            "Memo generation is disabled in demo mode. The scenario figures and "
            "their intervals above are unaffected — they never needed the model."
        )
        return

    if st.button("Write the memo", key="run_report", disabled=demo_mode()):
        clean = {k: v for k, v in scenario.items() if not k.startswith("_")}
        with st.spinner("Writing…"):
            st.session_state["report_outcome"] = generate_report(clean, load_evidence())

    outcome = st.session_state.get("report_outcome")
    if outcome is None:
        return

    if isinstance(outcome, ReportError):
        st.markdown(
            f"<div class='ps-caveat'><b>{outcome.message}</b>"
            + (f"<br>{outcome.detail}" if outcome.detail else "")
            + "</div>",
            unsafe_allow_html=True,
        )
        return

    _render_verification(outcome.verification)
    st.markdown("---")
    st.markdown(outcome.text)


def _render_verification(v) -> None:
    """Always rendered. "0 unverified numbers" is the result worth showing."""
    st.subheader("Numeric verification")

    if v.ok:
        st.markdown(
            f"<div class='ps-verify-ok'><b>{v.checked} numeric tokens checked · "
            f"0 unverified.</b><br>Every number in the memo below appears in the "
            f"scenario JSON or the evidence records it was given. Every cited "
            f"evidence_id exists.</div>",
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        f"<div class='ps-verify-bad'><b>{v.checked} numeric tokens checked · "
        f"{v.unverified_count} unverified.</b><br>These appear in the memo but "
        f"not in the input. Treat them as unsourced.</div>",
        unsafe_allow_html=True,
    )
    for f in v.findings:
        st.markdown(
            f"<div class='ps-caveat'><code>{f.token}</code> — {f.context}</div>",
            unsafe_allow_html=True,
        )
    for eid in v.missing_evidence_ids:
        st.markdown(
            f"<div class='ps-caveat'><b>Unknown evidence_id cited:</b> "
            f"<code>{eid}</code></div>",
            unsafe_allow_html=True,
        )


@st.cache_data(show_spinner=False)
def load_evidence() -> list:
    path = Path(__file__).resolve().parent.parent / "data" / "evidence.json"
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001 — a missing evidence file must not kill the app
        return []


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

    if demo_mode():
        st.markdown(
            "<div class='ps-demo'>DEMO MODE — reading precomputed scenarios only. "
            "No network calls are made.</div>",
            unsafe_allow_html=True,
        )

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
    st.divider()
    section_report(scenario)


main()
