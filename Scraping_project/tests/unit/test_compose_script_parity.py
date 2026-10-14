"""Ops scripts must only reference what docker-compose.yml defines (#324, #326, #383, #403)."""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
SERVICES = set(COMPOSE["services"])
VOLUMES = list(COMPOSE.get("volumes") or {})
LIB = ROOT / "scripts" / "compose_lib.sh"

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")


def _bash(script: str, services: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, COMPOSE_SERVICES=services)
    return subprocess.run(["bash", script, *args], cwd=ROOT, env=env, capture_output=True,
                          text=True, timeout=30, check=False)


def _lib(snippet: str, services: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, COMPOSE_SERVICES=services)
    return subprocess.run(["bash", "-c", f'. "{LIB}"; {snippet}'], env=env, capture_output=True,
                          text=True, timeout=30, check=False)


# --- scripts/compose_lib.sh ------------------------------------------------------------

@needs_bash
def test_compose_filter_keeps_defined_and_warns_about_missing():
    r = _lib("compose_filter redis kafka scraper", "redis scraper grafana")
    assert r.returncode == 0
    assert r.stdout.strip() == "redis scraper"
    assert "kafka" in r.stderr and "#145" in r.stderr


@needs_bash
def test_compose_filter_matches_whole_names_only():
    r = _lib("compose_filter prometheus", "prometheus-a")
    assert r.stdout.strip() == ""


@needs_bash
def test_compose_first_and_has():
    assert _lib("compose_first scrapy-app scraper", "scraper redis").stdout.strip() == "scraper"
    assert _lib("compose_first scrapy-app", "scraper").returncode == 1
    assert _lib("compose_has redis", "redis").returncode == 0
    assert _lib("compose_has kafka", "redis").returncode == 1


# --- complete_reset.sh (#326) ----------------------------------------------------------

@needs_bash
def test_complete_reset_dry_run_only_starts_defined_services():
    r = _bash("scripts/complete_reset.sh", " ".join(sorted(SERVICES)), "--dry-run")
    assert r.returncode == 0, r.stderr
    planned = set()
    for line in r.stdout.splitlines():
        group, _, names = line.partition(":")
        assert group in {"infra", "monitoring", "exporters", "apps"}
        planned.update(names.split())
    assert planned, "dry run planned nothing"
    assert planned <= SERVICES, planned - SERVICES
    # Every service of the current compose file gets started by some step.
    assert SERVICES <= planned, SERVICES - planned
    assert "skipping" in r.stderr  # full-stack-only names are reported, not run


@needs_bash
def test_complete_reset_full_stack_names_pass_through():
    r = _bash("scripts/complete_reset.sh", "redis kafka zookeeper scrapy-app metrics-exporter", "--dry-run")
    assert r.returncode == 0
    assert "infra: redis zookeeper kafka" in r.stdout
    assert "exporters: metrics-exporter" in r.stdout
    assert "apps: scrapy-app" in r.stdout


def test_complete_reset_never_prints_env_values_or_hardcodes_volume_names():
    text = (ROOT / "scripts" / "complete_reset.sh").read_text(encoding="utf-8")
    assert "cat .env" not in text
    assert "down -v" in text  # volumes come from the compose project itself
    assert "kafka_data" not in text and "prometheus_a_data" not in text


# --- diagnose.sh / diagnose_issues.sh (#403, #326) -------------------------------------

_LITERAL_SERVICE = re.compile(r"\bcompose (?:exec -T|logs(?: --tail=\d+)?|restart|up -d|run --rm --no-deps(?: -T)?) ([a-z][a-z0-9-]*)")


@pytest.mark.parametrize("rel", ["diagnose.sh", "scripts/diagnose_issues.sh", "scripts/complete_reset.sh", "rebuild_env.sh"])
def test_literal_service_names_exist_in_compose(rel):
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert "compose_lib.sh" in text
    # Names behind an explicit `compose_has NAME` guard (full-stack-only services) are fine.
    guarded = set(re.findall(r"compose_has ([a-z][a-z0-9-]*)", text))
    literal = set(_LITERAL_SERVICE.findall(text)) - guarded
    assert literal <= SERVICES, f"{rel} hard-codes services not in docker-compose.yml: {literal - SERVICES}"
    assert "docker-compose exec" not in text and "docker-compose logs" not in text


def test_diagnose_has_no_removed_service_or_module_names():
    text = (ROOT / "diagnose.sh").read_text(encoding="utf-8")
    for stale in ("metrics-exporter", "src.common.delta_lake", "DeltaLakeManager"):
        assert stale not in text


@needs_bash
@pytest.mark.parametrize("rel", ["diagnose.sh", "run_all_tests.sh", "scripts/diagnose_issues.sh",
                                 "scripts/complete_reset.sh", "scripts/compose_lib.sh"])
def test_shell_syntax(rel):
    r = subprocess.run(["bash", "-n", str(ROOT / rel)], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr


# --- run_all_tests.sh (#324) -----------------------------------------------------------

def test_run_all_tests_is_non_interactive_and_targets_existing_paths():
    text = (ROOT / "run_all_tests.sh").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"(^|\s)read(\s|$)", code), "must not block on read"
    for gone in ("test_single_spider.py", "test_start_requests.py", "test_minimal_urls.py"):
        assert gone not in code
    assert "pytest" in code and "tests/" in code
    assert (ROOT / "tests").is_dir()


# --- shutdown.py (#383) ----------------------------------------------------------------

def _shutdown_module():
    spec = importlib.util.spec_from_file_location("_ops_shutdown", ROOT / "shutdown.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shutdown_prune_list_is_compose_volumes():
    sd = _shutdown_module()
    assert sd.compose_volume_names() == VOLUMES
    assert sd.planned_volume_prune(purge_data=False) == [v for v in VOLUMES if "delta" not in v]
    for stale in ("kafka_data", "zookeeper_data", "prometheus_a_data", "alertmanager_1_data"):
        assert stale not in sd.planned_volume_prune(purge_data=True)


def test_shutdown_delta_volumes_need_purge_flag(tmp_path):
    sd = _shutdown_module()
    f = tmp_path / "docker-compose.yml"
    f.write_text("services: {}\nvolumes:\n  redis-data:\n  delta-data:\n", encoding="utf-8")
    assert sd.planned_volume_prune(False, f) == ["redis-data"]
    assert sd.planned_volume_prune(True, f) == ["redis-data", "delta-data"]


def test_shutdown_falls_back_when_compose_missing(tmp_path, capsys):
    sd = _shutdown_module()
    assert sd.compose_volume_names(tmp_path / "nope.yml") == list(sd.DEFAULT_COMPOSE_VOLUMES)
    assert "default volume list" in capsys.readouterr().err


def test_shutdown_matches_project_prefixed_names():
    sd = _shutdown_module()
    existing = ["scraping_project_redis-data", "other_grafana-data", "redis-data-backup", "redis-data"]
    assert sd.matching_docker_volumes("redis-data", existing) == ["scraping_project_redis-data", "redis-data"]


def test_shutdown_dry_run_prints_names_without_touching_docker(monkeypatch, capsys):
    sd = _shutdown_module()

    def boom(*a, **k):
        raise AssertionError("dry run must not call docker")

    monkeypatch.setattr(sd.subprocess, "run", boom)
    monkeypatch.setattr(sd.sys, "argv", ["shutdown.py", "--dry-run"])
    sd.main()
    out = capsys.readouterr().out
    assert "Would prune Compose volumes: " + ", ".join(VOLUMES) in out


def test_shutdown_help_lists_volume_names(monkeypatch, capsys):
    sd = _shutdown_module()
    monkeypatch.setattr(sd.sys, "argv", ["shutdown.py", "--help"])
    with pytest.raises(SystemExit):
        sd.parse_args()
    out = " ".join(capsys.readouterr().out.split())
    for v in VOLUMES:
        assert v in out
