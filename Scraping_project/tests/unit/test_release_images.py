"""Release images (#449) and the observability compose overlay (#476) match the repo layout."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]  # Scraping_project/
REPO = ROOT.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
STAGES = {m.group(1) for m in re.finditer(r"^FROM\s+\S+\s+as\s+(\S+)", DOCKERFILE, re.I | re.M)}
CD = yaml.safe_load((REPO / ".github" / "workflows" / "cd-release.yml").read_text(encoding="utf-8"))
CD_JOB = CD["jobs"]["build-and-push"]
CD_IMAGES = [entry["image"] for entry in CD_JOB["strategy"]["matrix"]["include"]]


def _stage_block(name: str) -> str:
    start = re.search(rf"^FROM\s+\S+\s+as\s+{re.escape(name)}\s*$", DOCKERFILE, re.I | re.M)
    assert start, name
    nxt = re.search(r"^FROM\s", DOCKERFILE[start.end():], re.M)
    return DOCKERFILE[start.start(): start.end() + (nxt.start() if nxt else len(DOCKERFILE))]


# --- #449: CD targets exist ------------------------------------------------------------

def test_every_cd_image_is_a_dockerfile_target():
    assert set(CD_IMAGES) == {"crawler", "metrics", "kafka-delta-ingest"}
    assert set(CD_IMAGES) <= STAGES, set(CD_IMAGES) - STAGES
    assert "production" in STAGES  # docker-compose.yml target unchanged


def test_cd_builds_matrix_target_and_smoke_tests_before_push():
    steps = CD_JOB["steps"]
    builds = [s for s in steps if "build-push-action" in s.get("uses", "")]
    assert builds and all(s["with"]["target"] == "${{ matrix.image }}" for s in builds)
    load = next(s for s in builds if s["with"].get("load"))
    names = [s.get("name", "") for s in steps]
    smoke = next(i for i, n in enumerate(names) if n.startswith("Smoke test"))
    assert steps.index(load) < smoke
    assert "if" not in steps[smoke], "smoke test must run on PRs too, not only when pushing"
    push = next(s for s in builds if s["with"].get("push") is True)
    assert push.get("if") == "env.PUSH == 'true'"


def test_cd_runs_on_prs_touching_the_image_recipe():
    triggers = CD[True]  # PyYAML parses the `on:` key as boolean True
    paths = triggers["pull_request"]["paths"]
    for p in ("Scraping_project/Dockerfile", "Scraping_project/kafka-delta-ingest/**",
              "Scraping_project/docker/**", ".github/workflows/cd-release.yml"):
        assert p in paths
    assert "tags" in triggers["push"]
    assert "github.event_name == 'push'" in CD_JOB["env"]["PUSH"], "PR runs must never push"


def test_kafka_delta_ingest_stage_builds_the_locked_crate():
    build = _stage_block("kafka-delta-ingest-build")
    runtime = _stage_block("kafka-delta-ingest")
    assert "cargo build --release --locked" in build
    assert (ROOT / "kafka-delta-ingest" / "Cargo.lock").is_file()
    for src in re.findall(r"^COPY\s+(?!--from)(\S+)", build + runtime, re.M):
        assert (ROOT / src).exists(), src
    assert "--from=kafka-delta-ingest-build" in runtime
    assert "kafka-delta-ingest-entrypoint.sh" in runtime and "ENTRYPOINT" in runtime
    assert "procps" in runtime  # Helm liveness probe uses pgrep


def test_entrypoint_execs_the_binary_with_args():
    text = (ROOT / "docker" / "entrypoints" / "kafka-delta-ingest-entrypoint.sh").read_text(encoding="utf-8")
    assert 'exec kafka-delta-ingest "$@"' in text


def test_helm_ingestor_args_match_the_rust_cli():
    tpl = (ROOT / "k8s" / "helm" / "scraping-pipeline" / "templates" / "kafka-delta-ingestor-deployment.yaml")
    text = tpl.read_text(encoding="utf-8")
    container = text[text.index("- name: kafka-delta-ingestor"):]
    container = container[: container.index("envFrom:")]
    assert "\n        command:" not in container, "command: would bypass the image ENTRYPOINT"
    assert re.search(r"args:\n\s+- ingest\n", container)
    main_rs = (ROOT / "kafka-delta-ingest" / "src" / "main.rs").read_text(encoding="utf-8")
    ingest = main_rs[main_rs.index("    Ingest {"): main_rs.index("    },", main_rs.index("    Ingest {"))]
    fields = set(re.findall(r"^\s+(\w+):", ingest, re.M))
    for flag in re.findall(r"- (--[a-z-]+)", container):
        assert flag[2:].replace("-", "_") in fields, flag


def test_metrics_exporter_ships_in_build_context():
    ignore = [ln.strip() for ln in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()]
    assert "monitoring/" not in ignore
    assert "!monitoring/metrics_exporter.py" in ignore
    for path in ("kafka-delta-ingest", "kafka-delta-ingest/", "docker/", "docker", "cli.py"):
        assert path not in ignore
    assert "/app/monitoring/metrics_exporter.py" in _stage_block("metrics")


def test_metrics_launcher_runs_exporter_help():
    # Same call the launcher in the `metrics` stage makes, with the repo as /app.
    code = ("import runpy, sys; sys.argv = ['metrics_exporter.py', '--help']; "
            "runpy.run_path('monitoring/metrics_exporter.py', run_name='__main__')")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                       timeout=120, check=False)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "--statsd-host" in r.stdout


# --- #476: one production/observability compose path ----------------------------------

MAIN = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
OBS_PATH = ROOT / "docker-compose.observability.yml"


def test_orphan_root_stack_is_gone():
    assert not (REPO / "docker-compose.production.yml").exists()
    assert not any((REPO / "monitoring").glob("*")), "root monitoring/ configs moved to Scraping_project/monitoring/"
    assert OBS_PATH.is_file()


def test_overlay_networks_and_mounts_resolve_against_main_stack():
    obs = yaml.safe_load(OBS_PATH.read_text(encoding="utf-8"))
    main_networks = set(MAIN["networks"])
    assert "networks" not in obs or not any(
        (cfg or {}).get("external") for cfg in obs["networks"].values()
    )
    for name, svc in obs["services"].items():
        assert set(svc.get("networks", [])) <= main_networks, name
        for vol in svc.get("volumes", []):
            src = vol.split(":", 1)[0]
            if src.startswith("./"):
                assert (ROOT / src).exists(), f"{name}: {src}"


def test_overlay_does_not_collide_with_main_stack():
    obs = yaml.safe_load(OBS_PATH.read_text(encoding="utf-8"))
    assert not set(obs["services"]) & set(MAIN["services"])

    def host_ports(services):
        out = set()
        for svc in services.values():
            for p in svc.get("ports", []):
                parts = str(p).split("/")[0].split(":")
                out.add(parts[-2] if len(parts) >= 2 else parts[0])
        return out

    assert not host_ports(obs["services"]) & host_ports(MAIN["services"])


@pytest.mark.parametrize("doc", ["MONITORING.md", "Scraping_project/README.md"])
def test_docs_point_at_the_overlay(doc):
    text = (REPO / doc).read_text(encoding="utf-8")
    assert "docker-compose.observability.yml" in text
    assert "docker-compose.production.yml" not in text
