"""B5 acceptance: demo mode is airtight, failures are readable, charts are legible."""
import os
import pathlib
import socket

import pytest
from streamlit.testing.v1 import AppTest

MAIN = str(pathlib.Path(__file__).resolve().parents[1] / "main.py")
REPO = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture
def no_network(monkeypatch):
    """Make any outbound socket fail, the way wifi-off does."""
    def blocked(*a, **k):
        raise AssertionError("a network connection was attempted")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    return True


def test_full_demo_path_makes_no_network_call(scenario_dir, no_network, monkeypatch):
    """The scripted demo, with the network hard-disabled. This is the wifi-off run."""
    monkeypatch.setenv("POLICY_SIM_DEMO_MODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-be-used")

    at = AppTest.from_file(MAIN, default_timeout=180).run()
    assert not at.exception, at.exception

    for idx in (0, 2):  # every readable scenario in the fixture dir
        at.selectbox[0].select(idx).run()
        assert not at.exception, at.exception
        headers = [h.value for h in at.main.header]
        for want in ("1 · Policy", "2 · Material impact",
                     "3 · Support by group", "4 · Limitations"):
            assert want in headers

    at.toggle[0].set_value(True).run()  # night palette
    assert not at.exception, at.exception


def test_demo_mode_banner_is_visible(scenario_dir, monkeypatch):
    monkeypatch.setenv("POLICY_SIM_DEMO_MODE", "1")
    at = AppTest.from_file(MAIN, default_timeout=120).run()
    at.selectbox[0].select(2).run()
    blob = " ".join(str(m.value) for m in at.main.markdown)
    assert "DEMO MODE" in blob
    assert "No network calls are made." in blob


def test_network_buttons_are_disabled_in_demo_mode(scenario_dir, monkeypatch):
    monkeypatch.setenv("POLICY_SIM_DEMO_MODE", "1")
    at = AppTest.from_file(MAIN, default_timeout=120).run()
    at.selectbox[0].select(2).run()
    labels = {b.label: b.disabled for b in at.button}
    assert labels.get("Read it") is True
    assert "Write the memo" not in labels or labels["Write the memo"] is True


def test_buttons_are_live_when_demo_mode_is_off(scenario_dir, monkeypatch):
    monkeypatch.delenv("POLICY_SIM_DEMO_MODE", raising=False)
    at = AppTest.from_file(MAIN, default_timeout=120).run()
    at.selectbox[0].select(2).run()
    assert {b.label: b.disabled for b in at.button}.get("Read it") is False


# --- timeouts and failure text --------------------------------------------
def test_parser_timeout_defaults_to_30s(monkeypatch):
    monkeypatch.delenv("POLICY_SIM_PARSER_TIMEOUT", raising=False)
    import importlib

    import app.parser as parser
    importlib.reload(parser)
    assert parser.DEFAULT_TIMEOUT_S == 30.0


def test_timeout_message_points_at_the_precomputed_scenarios():
    from app.parser import ParseError, parse_policy
    import anthropic

    class Boom:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise anthropic.APITimeoutError(request=None)

    os.environ.pop("POLICY_SIM_DEMO_MODE", None)
    out = parse_policy("give every kid $1000", client=Boom())
    assert isinstance(out, ParseError) and out.kind == "timeout"
    assert "30s" in out.message
    assert "scenario" in out.detail.lower()


def test_no_traceback_ever_reaches_the_user():
    """Every exception type becomes a message, not a stack trace."""
    from app.parser import ParseError, parse_policy

    for exc in (ValueError("x"), RuntimeError("y"), KeyError("z"), MemoryError()):
        class C:
            class messages:
                @staticmethod
                def create(**kwargs):
                    raise exc
        out = parse_policy("give every kid $1000", client=C())
        assert isinstance(out, ParseError)
        assert "Traceback" not in out.message


# --- legibility ------------------------------------------------------------
def test_chart_fonts_are_projector_sized():
    import matplotlib as mpl
    from app.theme import apply_matplotlib

    apply_matplotlib("day")
    assert mpl.rcParams["font.size"] >= 12
    assert mpl.rcParams["axes.labelsize"] >= 14
    assert mpl.rcParams["ytick.labelsize"] >= 14


def test_interval_bars_are_thick_enough_to_read():
    src = (REPO / "app" / "charts.py").read_text(encoding="utf-8")
    assert "linewidth=13" in src, "the p05-p95 bar must stay thick"


def test_theme_defines_both_palettes_with_identical_keys():
    from app.theme import DAY, NIGHT
    assert set(DAY) == set(NIGHT)


def test_theme_config_exists():
    cfg = (REPO / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    for key in ("primaryColor", "backgroundColor", "secondaryBackgroundColor", "textColor"):
        assert key in cfg


def test_no_gradient_backgrounds_in_the_app_css():
    """A TOML theme cannot express a gradient; the injected CSS can."""
    css = (REPO / "app" / "main.py").read_text(encoding="utf-8")
    assert "linear-gradient" not in css
    assert "radial-gradient" not in css


def test_at_most_two_font_families():
    from app import theme
    assert hasattr(theme, "FONT_SANS") and hasattr(theme, "FONT_MONO")
    assert not hasattr(theme, "FONT_SERIF"), "two fonts maximum"


def test_app_never_renders_a_bare_streamlit_metric(scenario_dir):
    """st.metric renders a point estimate with no interval. It must not appear."""
    src = (REPO / "app" / "main.py").read_text(encoding="utf-8")
    assert "st.metric(" not in src
