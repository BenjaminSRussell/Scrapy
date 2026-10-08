"""#733: ``start.py --dry-run`` validates and plans without touching Redis or anything else.

A dry run must not run external commands (docker, helm, kubectl), prompt, or create a
Redis client. A valid config exits 0 and prints the planned commands; an invalid one
exits 1 with every problem listed. A normal run still executes (covered here and in
test_start_local_services.py).
"""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def start(monkeypatch):
    monkeypatch.setenv("COMPOSE_CMD", "docker-compose")  # deterministic CLI choice (#342)
    monkeypatch.chdir(ROOT)  # start.py resolves the chart/values paths relative to cwd
    spec = importlib.util.spec_from_file_location("_ops_start_733", ROOT / "start.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def tools_present(start, monkeypatch):
    monkeypatch.setattr(start.shutil, "which", lambda tool: f"/usr/bin/{tool}")


@pytest.fixture()
def nothing_external(start, monkeypatch):
    """Any subprocess, prompt or Redis client fails the test."""

    def boom(*a, **k):
        raise AssertionError(f"dry run executed something: {a!r}")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(start.subprocess, name, boom)
    monkeypatch.setattr("builtins.input", boom)
    redis = pytest.importorskip("redis")
    monkeypatch.setattr(redis.Redis, "__init__", boom)
    monkeypatch.setattr(redis, "from_url", boom)
    monkeypatch.setattr(redis.ConnectionPool, "__init__", boom)


def test_local_dry_run_plans_without_executing(start, tools_present, nothing_external, capsys):
    assert start.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"{start.DRY_RUN_PREFIX} docker-compose up -d" in out
    assert "docker-compose exec -T postgres true" in out
    assert "configuration OK" in out
    assert "Started Successfully" not in out


def test_local_dry_run_with_reset_delta_plans_the_reset(start, tools_present, nothing_external, capsys, monkeypatch):
    seed = ROOT / "data" / "raw" / "uconn_urls.csv"
    monkeypatch.setattr(start, "LOCAL_SEED_FILE", seed if seed.exists() else Path(__file__))
    assert start.main(["--dry-run", "--reset-delta"]) == 0
    assert "python cli.py reset --force" in capsys.readouterr().out


def test_k8s_dry_run_never_prompts_and_prints_helm_plan(start, tools_present, nothing_external, capsys):
    assert start.main(["--dry-run", "--env", "k8s", "--set", "image.tag=abc"]) == 0
    out = capsys.readouterr().out
    assert "not prompting" in out
    assert (
        f"{start.DRY_RUN_PREFIX} helm upgrade --install scraping-pipeline k8s/helm/scraping-pipeline "
        "--namespace scraping --create-namespace -f k8s/helm/scraping-pipeline/values.yaml --set image.tag=abc"
    ) in out
    assert "Deployment complete" not in out


def test_k8s_dry_run_all_stages(start, tools_present, nothing_external, capsys):
    assert start.main(["--dry-run", "--env", "k8s", "--stage", "all-stages"]) == 0
    out = capsys.readouterr().out
    for stage in ("stage1", "stage2", "stage3"):
        assert f"helm upgrade --install scraping-pipeline-{stage} " in out


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--env", "k8s", "--values", "missing-values.yaml"], "Helm values file not found: missing-values.yaml"),
        (["--env", "k8s", "--chart", "no/such/chart"], "Helm chart not found at: no/such/chart"),
        (["--env", "k8s", "--set", "novalue"], "--set expects KEY=VALUE, got 'novalue'"),
        (["--env", "k8s", "--set", "=x"], "--set expects KEY=VALUE, got '=x'"),
    ],
)
def test_invalid_k8s_config_fails_with_diagnostics(start, tools_present, nothing_external, capsys, argv, expected):
    assert start.main(["--dry-run", *argv]) == 1
    err = capsys.readouterr().err
    assert "a real run would fail" in err
    assert expected in err


def test_all_problems_reported_together(start, tools_present, nothing_external, capsys):
    rc = start.main(["--dry-run", "--env", "k8s", "--values", "a.yaml", "--extra-values", "b.yaml", "--set", "x"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "a.yaml" in err and "b.yaml" in err and "'x'" in err


def test_unreadable_compose_file_fails(start, tools_present, nothing_external, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(start, "COMPOSE_FILE", tmp_path / "missing.yml")
    monkeypatch.setattr(start, "compose_services", lambda f=start.COMPOSE_FILE: [])
    assert start.main(["--dry-run"]) == 1
    assert "unreadable or defines no services" in capsys.readouterr().err


def test_missing_tools_reported_not_exited(start, nothing_external, capsys, monkeypatch):
    monkeypatch.setattr(start.shutil, "which", lambda tool: None)
    assert start.main(["--dry-run"]) == 1
    out, err = capsys.readouterr()
    assert "missing required tooling for 'local': docker, docker-compose" in err
    assert "docker-compose up -d" in out  # the plan is still shown


def test_dry_run_flag_does_not_leak_into_a_normal_run(start, tools_present, monkeypatch, capsys):
    """Normal run still executes: dry run first, then a real run in the same process."""
    calls: list[tuple] = []

    def fake_run(cmd, *a, **k):
        calls.append(tuple(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(start.subprocess, "run", fake_run)
    assert start.main(["--dry-run"]) == 0
    assert calls == [] and not start.is_dry_run()
    assert start.main(["--env", "local"]) == 0
    assert ("docker-compose", "up", "-d") in calls
    assert "Started Successfully" in capsys.readouterr().out


def test_dry_run_flag_reset_even_if_planning_raises(start, tools_present, monkeypatch):
    def broken(args):
        raise RuntimeError("boom")

    monkeypatch.setattr(start, "start_local", broken)
    with pytest.raises(RuntimeError):
        start.main(["--dry-run"])
    assert not start.is_dry_run()


def test_end_to_end_subprocess_touches_no_tool_and_no_redis(tmp_path):
    """Real interpreter: stub docker/helm/kubectl leave a marker if invoked; redis import is poisoned."""
    bin_dir, site_dir, marker = tmp_path / "bin", tmp_path / "site", tmp_path / "invoked"
    bin_dir.mkdir()
    site_dir.mkdir()
    for tool in ("docker", "docker-compose", "kubectl", "helm"):
        stub = bin_dir / tool
        stub.write_text(f"#!/bin/sh\necho {tool} \"$@\" >> '{marker}'\n")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    (site_dir / "sitecustomize.py").write_text(
        "import sys, types\n"
        "class _Poison(types.ModuleType):\n"
        "    def __getattr__(self, name):\n"
        "        raise RuntimeError('dry run touched redis.' + name)\n"
        "sys.modules['redis'] = _Poison('redis')\n"
    )
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONPATH"] = f"{site_dir}{os.pathsep}{env.get('PYTHONPATH', '')}"
    for argv in (["--dry-run"], ["--dry-run", "--env", "k8s", "--stage", "all-stages"]):
        r = subprocess.run(
            [sys.executable, str(ROOT / "start.py"), *argv],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "configuration OK" in r.stdout
        assert "touched redis" not in r.stderr
    assert not marker.exists(), marker.read_text()


def test_help_documents_dry_run():
    r = subprocess.run([sys.executable, str(ROOT / "start.py"), "--help"], capture_output=True, text=True, cwd=ROOT)
    assert "--dry-run" in r.stdout and "no Redis connection" in " ".join(r.stdout.split())
