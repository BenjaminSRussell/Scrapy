"""#705: cross-region Delta replication is a dry-run rclone stub.

``make delta-replicate`` prints an rclone sync plan and a concurrent-writer
warning. It must not call rclone, read the lake, or copy files.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "delta_replicate.sh"


def _run(env_extra=None, args=None):
    env = os.environ.copy()
    for key in ("LAKE_SRC", "RCLONE_DEST", "RCLONE_FLAGS", "DRY_RUN"):
        env.pop(key, None)
    env["CI"] = "true"
    env["OBS_OFFLINE"] = "1"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(SCRIPT), *(args or [])],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_dry_run_prints_rclone_plan_and_writer_warning():
    r = _run()
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "mode: dry-run" in out
    assert "command: rclone sync --dry-run ./data/delta_lake dr:delta-lake" in out
    assert "concurrent writers" in out
    assert "does not call rclone" in out
    assert "not read" in out
    assert not (ROOT / "data" / "delta_lake").exists()


def test_dry_run_does_not_execute_rclone(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "rclone-ran"
    rclone = bin_dir / "rclone"
    rclone.write_text(f"#!/bin/bash\necho ran > {marker}\nexit 99\n", encoding="utf-8")
    rclone.chmod(rclone.stat().st_mode | stat.S_IXUSR)
    env_path = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    r = _run({"PATH": env_path})
    assert r.returncode == 0, r.stderr
    assert not marker.exists()


def test_custom_lake_src_is_printed_and_not_created(tmp_path):
    src = tmp_path / "lake"
    r = _run({"LAKE_SRC": str(src), "RCLONE_DEST": "dr:other"})
    assert r.returncode == 0, r.stderr
    assert f"command: rclone sync --dry-run {src} dr:other" in r.stdout
    assert "not read" in r.stdout
    assert not src.exists()


def test_real_run_is_refused():
    r = _run({"DRY_RUN": "0"})
    assert r.returncode == 2
    assert "refusing" in r.stderr
    assert "stub" in r.stderr
    assert "does not copy" in r.stderr


def test_embedded_credentials_are_not_echoed():
    secret = "s3://AKIASECRET:supersecret@bucket/delta"
    r = _run({"RCLONE_DEST": secret})
    assert r.returncode == 2
    assert secret not in r.stdout
    assert secret not in r.stderr
    assert "credentials" in r.stderr


def test_extra_args_rejected():
    r = _run(args=["--apply"])
    assert r.returncode == 2


def test_make_target_is_the_same_dry_run():
    env = os.environ.copy()
    env.update({"DRY_RUN": "", "LAKE_SRC": "", "RCLONE_DEST": "", "RCLONE_FLAGS": ""})
    r = subprocess.run(
        ["make", "delta-replicate"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert "command: rclone sync --dry-run ./data/delta_lake dr:delta-lake" in r.stdout
    assert "concurrent writers" in r.stdout


def test_deployment_docs_cover_the_stub_and_the_warning():
    text = (ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "make delta-replicate" in text
    assert "rclone sync" in text
    assert "Concurrent writers" in text
    assert "single region" in text
    assert "delta-replicate" in (ROOT / "Makefile").read_text(encoding="utf-8")
