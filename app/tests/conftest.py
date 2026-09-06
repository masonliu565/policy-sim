import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


@pytest.fixture
def scenario_dir(tmp_path, monkeypatch):
    """A scenario directory containing the real stub, a copy, and a broken file.

    Tests never write into /scenarios: Track A owns it and regenerates it.
    """
    shutil.copy(REPO / "scenarios" / "ctc_2021.json", tmp_path / "ctc_2021.json")
    base = json.loads((tmp_path / "ctc_2021.json").read_text(encoding="utf-8"))
    base["policy_id"], base["label"] = "baseline", "Baseline (no policy)"
    (tmp_path / "baseline.json").write_text(json.dumps(base, indent=2))
    (tmp_path / "broken.json").write_text('{"policy_id": "broken", NOT JSON')
    monkeypatch.setenv("POLICY_SIM_SCENARIO_DIR", str(tmp_path))
    return tmp_path
