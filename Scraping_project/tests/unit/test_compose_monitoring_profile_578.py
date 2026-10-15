"""#578: `docker compose --profile monitoring up` starts the metrics stack only.

Static checks on docker-compose.yml and DEPLOYMENT.md. No Docker daemon is needed.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SERVICES = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
DEPLOYMENT = (ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")

# Redis and Postgres are here because the exporters read from them.
METRICS_STACK = {
    "prometheus",
    "grafana",
    "redis-exporter",
    "postgres-exporter",
    "dashboard",
    "redis",
    "postgres",
}
PIPELINE = {"scraper", "stage1-worker", "stage2-worker", "stage3-worker", "stage4-worker", "kafka"}


def _profiles(name: str) -> set[str]:
    return set(SERVICES[name].get("profiles") or [])


def _enabled_by(profile: str) -> set[str]:
    return {name for name in SERVICES if profile in _profiles(name)}


def test_monitoring_profile_starts_exactly_the_metrics_stack():
    assert _enabled_by("monitoring") == METRICS_STACK


def test_monitoring_profile_never_starts_pipeline_services():
    assert PIPELINE <= set(SERVICES), "a pipeline service was renamed; update PIPELINE"
    assert not _enabled_by("monitoring") & PIPELINE


def test_monitoring_services_depend_only_on_monitoring_services():
    # `--profile monitoring up` must not need a service from another profile.
    enabled = _enabled_by("monitoring")
    for name in sorted(enabled):
        for dep in list(SERVICES[name].get("depends_on") or []):
            assert dep in enabled, f"{name} depends on {dep}, which is not on the monitoring profile"


def test_every_service_has_a_profile_so_bare_up_starts_nothing():
    assert all(_profiles(name) for name in SERVICES)


def test_deployment_documents_the_metrics_only_command():
    assert "docker compose --profile monitoring up -d" in DEPLOYMENT
    assert "docker-compose up -d" not in DEPLOYMENT
    section = DEPLOYMENT.split("### Metrics only (existing data)", 1)[1]
    section = re.split(r"\n#{2,3} ", section, maxsplit=1)[0]
    for name in METRICS_STACK:
        assert f"`{name}`" in section, f"{name} missing from the metrics-only section"
