"""start.py local mode only waits on / prints services Compose defines (#399); --stage is k8s-only (#492)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SERVICES = list(yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"])


@pytest.fixture()
def start():
    spec = importlib.util.spec_from_file_location("_ops_start", ROOT / "start.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compose_services_reads_the_real_file(start):
    assert start.compose_services() == SERVICES


def test_compose_services_unreadable_returns_empty(start, tmp_path, capsys):
    assert start.compose_services(tmp_path / "missing.yml") == []
    assert "could not read" in capsys.readouterr().err


def test_log_hints_only_name_defined_services(start):
    hints = start.local_log_hints(["redis", "scraper", "custom-svc"])
    joined = "\n".join(hints)
    assert "docker-compose logs -f redis" in joined
    assert "docker-compose logs -f scraper" in joined
    assert "docker-compose logs -f custom-svc" in joined  # unknown names still listed
    for absent in ("scrapy-app", "kafka", "postgres", "stage4-worker"):
        assert f"logs -f {absent}" not in joined


def _record_start_local(start, monkeypatch, services, **arg_overrides):
    calls: list[tuple] = []
    waited: list[str] = []
    monkeypatch.setattr(start, "compose_services", lambda *a, **k: list(services))
    monkeypatch.setattr(start, "run_command", lambda cmd, **k: calls.append(tuple(cmd)))
    monkeypatch.setattr(start, "wait_for_exec", lambda svc, timeout: waited.append(svc))
    monkeypatch.setattr(start, "LOCAL_SEED_FILE", ROOT / "docker-compose.yml")  # any existing file
    args = SimpleNamespace(wait_timeout=1, reset_delta=False, stage="pipeline")
    for k, v in arg_overrides.items():
        setattr(args, k, v)
    start.start_local(args)
    return calls, waited


def test_start_local_with_current_compose(start, monkeypatch, capsys):
    calls, waited = _record_start_local(start, monkeypatch, SERVICES, reset_delta=True)
    out = capsys.readouterr().out
    assert calls[0] == ("docker-compose", "up", "-d")
    assert waited == ["postgres"] and "postgres" in SERVICES
    reset = calls[1]
    assert reset[:6] == ("docker-compose", "run", "--rm", "--no-deps", "-T", "scraper")
    assert "Compose services: " + ", ".join(SERVICES) in out
    for svc in SERVICES:
        assert f"docker-compose logs -f {svc}" in out
    for absent in ("scrapy-app", "kafka"):
        assert f"logs -f {absent}" not in out
    assert "9091" not in out and "9097" not in out


def test_start_local_falls_back_to_redis_and_legacy_app(start, monkeypatch):
    calls, waited = _record_start_local(start, monkeypatch, ["redis", "scrapy-app"], reset_delta=True)
    assert waited == ["redis"]
    assert calls[1][5] == "scrapy-app"


def test_start_local_without_readiness_service(start, monkeypatch, capsys):
    _, waited = _record_start_local(start, monkeypatch, ["grafana"])
    assert waited == []
    assert "skipping readiness wait" in capsys.readouterr().out


def test_stage_flag_warns_in_local_mode(start, monkeypatch, capsys):
    _record_start_local(start, monkeypatch, SERVICES, stage="stage2")
    err = capsys.readouterr().err
    assert "--stage stage2 only applies to --env k8s" in err


def test_help_says_stage_is_k8s_only_and_mentions_stage4():
    out = subprocess.run(
        [sys.executable, str(ROOT / "start.py"), "--help"], capture_output=True, text=True, check=True, cwd=ROOT
    ).stdout
    text = " ".join(out.split())
    assert "Kubernetes only (--env k8s)" in text
    assert "Ignored by --env local" in text
    assert "Stage 4 (PDF/OCR, stage4Worker) is off by default" in text  # #504 (was "no Stage 4", #492)
    assert "stage4" in text


def test_k8s_stage_overrides_cover_chart_workloads(start):
    values = yaml.safe_load((ROOT / start.DEFAULT_HELM_VALUES).read_text(encoding="utf-8"))
    workloads = {k for k in ("scrapyApp", "stage2Worker", "stage3Worker", "stage4Worker") if k in values}
    assert "stage4Worker" in workloads  # #504: the chart has Stage 4 (off by default)
    for stage, cfg in start.K8S_STAGE_DEFAULTS.items():
        toggled = {o.split(".")[0] for o in cfg["set_overrides"]}
        assert toggled <= workloads, (stage, toggled - workloads)
