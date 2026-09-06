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



def test_theme_config_exists():
    cfg = (REPO / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    for key in ("primaryColor", "backgroundColor", "secondaryBackgroundColor", "textColor"):
        assert key in cfg


def test_no_gradient_backgrounds_in_the_app_css():
    """A TOML theme cannot express a gradient; the injected CSS can."""
    css = (REPO / "app" / "main.py").read_text(encoding="utf-8")
    assert "linear-gradient" not in css
    assert "radial-gradient" not in css



def test_app_never_renders_a_bare_streamlit_metric(scenario_dir):
    """st.metric renders a point estimate with no interval. It must not appear."""
    src = (REPO / "app" / "main.py").read_text(encoding="utf-8")
    assert "st.metric(" not in src
