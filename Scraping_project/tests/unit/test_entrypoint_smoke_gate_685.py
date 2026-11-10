"""#685: compose and entrypoint changes run a container entrypoint smoke gate.

The gate pulls redis and a Python image, runs the shell entrypoints with mock
commands, and tears the containers down. It does not build the app image or
require production credentials.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT.parent
WF_PATH = REPO / ".github" / "workflows" / "ci-entrypoint-smoke.yml"
COMPOSE_PATH = ROOT / "docker-compose.entrypoint-smoke.yml"
HOST_SCRIPT = ROOT / "scripts" / "entrypoint_smoke_compose.sh"
INSIDE_SCRIPT = ROOT / "scripts" / "entrypoint_smoke_inside.sh"

PATHS = (
    "Scraping_project/docker-compose*.yml",
    "Scraping_project/docker/**",
    "Scraping_project/Dockerfile",
    "Scraping_project/scripts/entrypoint_smoke_compose.sh",
    "Scraping_project/scripts/entrypoint_smoke_inside.sh",
    ".github/workflows/ci-entrypoint-smoke.yml",
)


def test_workflow_runs_for_compose_and_entrypoint_changes():
    wf = yaml.safe_load(WF_PATH.read_text(encoding="utf-8"))
    triggers = wf[True]  # PyYAML parses `on:` as True
    for event in ("pull_request", "push"):
        assert triggers[event]["branches"] == ["main"]
        for path in PATHS:
            assert path in triggers[event]["paths"], path
    job = wf["jobs"]["entrypoint-smoke"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] >= 1
    run = "\n".join(step.get("run", "") for step in job["steps"])
    assert "bash scripts/entrypoint_smoke_compose.sh" in run
    assert "set -euo pipefail" in run
    text = WF_PATH.read_text(encoding="utf-8")
    assert "secrets." not in text


def test_smoke_compose_pulls_images_and_waits_until_healthy():
    doc = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    services = doc["services"]
    assert set(services) == {"redis", "smoke"}
    for name, svc in services.items():
        assert "build" not in svc, name
        assert "ports" not in svc, name
        assert svc["image"]
        assert "restart" in svc and svc["restart"] == "no"
    assert services["redis"]["image"] == "redis:7-alpine"
    assert services["smoke"]["image"] == "python:3.11-slim"
    assert "healthcheck" in services["redis"]
    assert services["smoke"]["depends_on"]["redis"]["condition"] == "service_healthy"
    env = services["smoke"]["environment"]
    assert env["REDIS_HOST"] == "redis"
    assert str(env["REDIS_PORT"]) == "6379"
    text = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "REDIS_URL" not in text
    assert "./data" not in text
    assert "POSTGRES_PASSWORD" not in text


def test_host_script_logs_failure_and_tears_down():
    text = HOST_SCRIPT.read_text(encoding="utf-8")
    assert "docker-compose.entrypoint-smoke.yml" in text
    assert " -f docker-compose.yml" not in text
    assert "docker-compose.observability.yml" not in text
    assert "down --remove-orphans" in text
    assert "startup failed" in text
    assert "Container logs:" in text
    assert "missing ready marker" in text
    assert "REDIS_URL" not in text
    assert "scrapy crawl" not in text
    assert text.startswith("#!")
    assert "\nset -euo pipefail\n" in text


def test_inside_script_covers_entrypoints_and_actionable_failure():
    text = INSIDE_SCRIPT.read_text(encoding="utf-8")
    for name in (
        "crawler-entrypoint.sh",
        "metrics-exporter-entrypoint.sh",
        "kafka-delta-ingest-entrypoint.sh",
    ):
        assert name in text
    assert "SMOKE_READY name=crawler" in text
    assert "SMOKE_READY name=metrics" in text
    assert "SMOKE_READY name=kafka" in text
    assert "SMOKE_READY name=crawler-streaming" in text
    assert "Kafka is ready!" in text
    assert "REDIS_HOST is not set" in text
    assert "negative check ok" in text
    assert "Logs follow:" in text
    assert "env -u REDIS_HOST" in text
    assert "REDIS_URL" not in text
