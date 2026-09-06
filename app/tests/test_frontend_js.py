"""The component's JS is real code; syntax-check and unit-test its pure parts.

Skipped when node is unavailable. The DOM, SVG and Streamlit message plumbing
still need a browser — these cover the geometry and formatting only.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
INDEX = REPO / "app" / "frontend" / "index.html"
node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

PURE = ["iso", "pointInPoly", "relief", "snapToLand", "fmt"]


def _js() -> str:
    return re.search(r"<script>(.*)</script>", INDEX.read_text(), re.S).group(1)


@node
def test_component_js_parses(tmp_path):
    f = tmp_path / "component.js"
    f.write_text(_js())
    subprocess.run(["node", "--check", str(f)], check=True, capture_output=True)


def _pure_module(tmp_path) -> pathlib.Path:
    js = _js()
    out = ["const TW=26, TH=13, EH=9;"]
    for name in PURE:
        m = re.search(rf"^function {name}\(.*?^\}}", js, re.S | re.M)
        assert m, f"{name} not found in the component"
        out.append(m.group(0))
    out.append("module.exports={" + ",".join(PURE) + "};")
    f = tmp_path / "pure.js"
    f.write_text("\n".join(out))
    return f


def _run(tmp_path, expr):
    mod = _pure_module(tmp_path)
    r = subprocess.run(
        ["node", "-e", f'const m=require({json.dumps(str(mod))});console.log(JSON.stringify({expr}))'],
        capture_output=True, text=True, check=True)
    return json.loads(r.stdout.strip())


@node
def test_point_in_polygon(tmp_path):
    sq = "[[0,0],[10,0],[10,10],[0,10]]"
    assert _run(tmp_path, f"m.pointInPoly(5,5,{sq})") is True
    assert _run(tmp_path, f"m.pointInPoly(15,5,{sq})") is False


@node
def test_isometric_projection_is_two_to_one(tmp_path):
    x, y = _run(tmp_path, "m.iso(2,0,0)")
    assert abs(x / y - 2.0) < 1e-9, "isometric tiles must be 2:1"


@node
def test_lift_raises_a_tile(tmp_path):
    assert _run(tmp_path, "m.iso(3,3,1)[1]") < _run(tmp_path, "m.iso(3,3,0)[1]")


@node
def test_relief_is_deterministic(tmp_path):
    """Decorative terrain must not be random — it must render identically twice."""
    assert _run(tmp_path, "m.relief(4,7)===m.relief(4,7)") is True


@node
def test_snap_picks_the_nearest_land_tile(tmp_path):
    tiles = "[{gx:0,gy:0,lift:0},{gx:10,gy:10,lift:1}]"
    assert _run(tmp_path, f"m.snapToLand(9,9,{tiles})") == [10, 10, 1]


@node
@pytest.mark.parametrize("expr,expected", [
    ("m.fmt(0.084,'percent')", "8.4%"),
    ("m.fmt(105000000000,'currency')", "$105.0B"),
    ("m.fmt(75900,'currency')", "$75,900"),
    ("m.fmt(null,'percent')", "—"),
])
def test_frontend_formatting_matches_python(tmp_path, expr, expected):
    assert _run(tmp_path, expr) == expected
