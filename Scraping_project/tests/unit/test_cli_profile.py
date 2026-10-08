"""`cli.py --profile` (#465): off by default, writes a JSON + pstats artifact."""
from __future__ import annotations

import json
import pstats
import subprocess
import sys
from pathlib import Path

import pytest

from src.utils.profiler import run_profiled

ROOT = Path(__file__).resolve().parents[2]


def _work():
    return sum(i * i for i in range(20000))


def test_run_profiled_writes_artifacts(tmp_path):
    result = run_profiled(_work, label="pipeline run", out_dir=str(tmp_path))
    js = list(tmp_path.glob("profile_pipeline_run_*.json"))
    ps = list(tmp_path.glob("profile_pipeline_run_*.pstats"))
    assert len(js) == 1 and len(ps) == 1
    data = json.loads(js[0].read_text())
    assert data["outcome"] == "ok" and data["wall_time_s"] >= 0
    assert any("_work" in r["function"] for r in data["top_cumulative"])
    pstats.Stats(str(ps[0]))  # loadable
    assert result["pstats"] == str(ps[0])


def test_run_profiled_writes_even_on_failure(tmp_path):
    def boom():
        raise SystemExit(2)

    with pytest.raises(SystemExit):
        run_profiled(boom, label="x", out_dir=str(tmp_path))
    data = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert data["outcome"].startswith("SystemExit")


def _cli(*args, cwd):
    return subprocess.run([sys.executable, str(ROOT / "cli.py"), *args], cwd=cwd, capture_output=True, text=True, timeout=120)


def test_cli_profile_flag_produces_artifact(tmp_path):
    out = tmp_path / "prof"
    lock = tmp_path / "empty.lock.json"
    lock.write_text('{"models": []}')
    r = _cli("--profile", str(out), "setup", "--verify-only", "--lock", str(lock), cwd=ROOT)
    assert r.returncode == 0, r.stderr[-1500:]
    data = json.loads(next(out.glob("profile_setup_*.json")).read_text())
    assert data["label"] == "setup" and data["outcome"] == "ok"
    assert list(out.glob("profile_setup_*.pstats"))


def test_cli_without_profile_writes_nothing(tmp_path):
    lock = tmp_path / "empty.lock.json"
    lock.write_text('{"models": []}')
    r = _cli("setup", "--verify-only", "--lock", str(lock), cwd=tmp_path)
    assert r.returncode == 0, r.stderr[-1500:]
    assert not list(tmp_path.rglob("profile_*"))


def test_profile_is_off_by_default():
    import cli

    parser_src = Path(cli.__file__).read_text()
    assert 'default=None, metavar="DIR"' in parser_src
    makefile = (ROOT / "Makefile").read_text()
    assert "profile: ##" in makefile and "--profile" in makefile
