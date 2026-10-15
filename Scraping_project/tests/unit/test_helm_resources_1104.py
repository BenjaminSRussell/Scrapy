"""#1104: every deployable has cpu/memory requests and limits; profiles are documented.

Static checks always run. Render checks run ``helm lint`` and ``helm template``
when helm is on PATH (or ``HELM_BIN``). Set ``HELM_REQUIRED=1`` to fail instead
of skip, which is what CI does.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "k8s" / "helm" / "scraping-pipeline"
VALUES = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
SMALL = yaml.safe_load((CHART / "values-small.yaml").read_text(encoding="utf-8"))
PROD = yaml.safe_load((CHART / "values-prod.yaml").read_text(encoding="utf-8"))
DEPLOYMENT = (ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")
HELM = os.environ.get("HELM_BIN") or shutil.which("helm")

# Dotted values paths that render a container. Storage PVCs are not in this list.
WORKLOADS = (
    ("redis",),
    ("postgresql",),
    ("zookeeper",),
    ("kafka",),
    ("prometheus",),
    ("alertmanager",),
    ("grafana",),
    ("scrapyApp",),
    ("stage2Worker",),
    ("stage3Worker",),
    ("stage4Worker",),
    ("kafkaDeltaIngestor",),
    ("metricsExporter",),
    ("exporters", "redis"),
    ("exporters", "postgres"),
    ("exporters", "kafkaJmx"),
    ("exporters", "statsd"),
)
STAGE_WORKERS = ("scrapyApp", "stage2Worker", "stage3Worker", "stage4Worker")
STAGE_LABELS = {
    "scrapyApp": "scrapyApp (stage 1)",
    "stage2Worker": "stage2Worker",
    "stage3Worker": "stage3Worker",
    "stage4Worker": "stage4Worker",
}
POD_TEMPLATES = (
    "scrapy-deployment.yaml",
    "stage-workers-deployments.yaml",
    "metrics-exporter-deployment.yaml",
    "kafka-delta-ingestor-deployment.yaml",
    "exporters-deployments.yaml",
    "redis-statefulset.yaml",
    "postgresql-statefulset.yaml",
    "kafka-statefulset.yaml",
    "zookeeper-statefulset.yaml",
    "prometheus-statefulset.yaml",
    "alertmanager-statefulset.yaml",
    "grafana-deployment.yaml",
    "kafka-topics-job.yaml",
)


def _resources(doc: dict, path: tuple[str, ...]) -> dict:
    node = doc
    for part in path:
        node = node[part]
    return node["resources"]


def _assert_cpu_mem(resources: dict, name: str) -> None:
    assert isinstance(resources, dict), f"{name} resources={resources!r}"
    for side in ("requests", "limits"):
        assert side in resources, f"{name} missing {side}: {resources!r}"
        block = resources[side]
        assert block.get("cpu"), f"{name} {side}.cpu"
        assert block.get("memory"), f"{name} {side}.memory"


def _pair(resources: dict, side: str) -> str:
    return f"{resources[side]['cpu']} / {resources[side]['memory']}"


def test_dev_values_give_every_workload_requests_and_limits():
    for path in WORKLOADS:
        name = ".".join(path)
        _assert_cpu_mem(_resources(VALUES, path), name)


def test_small_and_prod_profiles_set_full_stage_resources():
    assert "profile" not in SMALL
    assert SMALL.get("redis", {}).get("enabled") is not False
    # A comment may name REDIS_URL to forbid it. The key itself must not be set.
    assert not re.search(r"(?m)^\s*REDIS_URL\s*:", (CHART / "values-small.yaml").read_text(encoding="utf-8"))
    assert not re.search(r"(?m)^\s*REDIS_URL\s*:", (CHART / "values-prod.yaml").read_text(encoding="utf-8"))
    for key in ("scrapyApp", "stage2Worker", "stage3Worker"):
        assert SMALL[key]["replicaCount"] == 1
        assert SMALL[key]["autoscaling"]["enabled"] is False
        _assert_cpu_mem(SMALL[key]["resources"], f"small.{key}")
    assert SMALL["stage4Worker"].get("enabled") is not True
    assert SMALL["stage4Worker"]["replicaCount"] == 1
    _assert_cpu_mem(SMALL["stage4Worker"]["resources"], "small.stage4Worker")
    assert PROD["profile"] == "production"
    for key in STAGE_WORKERS:
        _assert_cpu_mem(PROD[key]["resources"], f"prod.{key}")
        assert "replicaCount" not in PROD[key]


def test_deployment_table_matches_the_three_profiles():
    assert "### Helm resource profiles" in DEPLOYMENT
    assert "values-small.yaml" in DEPLOYMENT
    assert "values-prod.yaml" in DEPLOYMENT
    assert "namespaceQuota" in DEPLOYMENT
    assert "Do not set `REDIS_URL`." in DEPLOYMENT
    for key in STAGE_WORKERS:
        row = (
            f"| {STAGE_LABELS[key]} | {_pair(VALUES[key]['resources'], 'requests')} | "
            f"{_pair(VALUES[key]['resources'], 'limits')} | "
            f"{_pair(SMALL[key]['resources'], 'requests')} | "
            f"{_pair(SMALL[key]['resources'], 'limits')} | "
            f"{_pair(PROD[key]['resources'], 'requests')} | "
            f"{_pair(PROD[key]['resources'], 'limits')} |"
        )
        assert row in DEPLOYMENT, row
    assert (
        "Dev replicaCount is 3 for scrapyApp, 3 for stage2Worker, 2 for stage3Worker "
        "and 1 for stage4Worker (stage4Worker.enabled is false)."
    ) in DEPLOYMENT
    assert (
        "Small sets replicaCount 1 and turns autoscaling off for scrapyApp, "
        "stage2Worker and stage3Worker."
    ) in DEPLOYMENT
    assert "Prod keeps those replica counts and only replaces resources." in DEPLOYMENT
    assert VALUES["scrapyApp"]["replicaCount"] == 3
    assert VALUES["stage2Worker"]["replicaCount"] == 3
    assert VALUES["stage3Worker"]["replicaCount"] == 2
    assert VALUES["stage4Worker"]["replicaCount"] == 1
    assert VALUES["stage4Worker"]["enabled"] is False


def test_pod_templates_and_topics_job_declare_resources():
    templates = CHART / "templates"
    for name in POD_TEMPLATES:
        text = (templates / name).read_text(encoding="utf-8")
        assert "resources:" in text, name
        # Most containers toYaml a values block. The topics Job and the
        # ingestor init container inline both sides.
        assert ".resources" in text or ("requests:" in text and "limits:" in text), name
    job = (templates / "kafka-topics-job.yaml").read_text(encoding="utf-8")
    assert "requests:" in job and "limits:" in job
    ingestor = (templates / "kafka-delta-ingestor-deployment.yaml").read_text(encoding="utf-8")
    init_block, _, _ = ingestor.partition("\n      containers:")
    assert "name: wait-for-kafka" in init_block
    assert "requests:" in init_block and "limits:" in init_block
    helpers = (templates / "_helpers.tpl").read_text(encoding="utf-8")
    assert "scraping-pipeline.resourceErrors" in helpers
    assert "enabled workloads missing cpu and memory requests and limits" in helpers


def _require_helm() -> str:
    if HELM:
        return HELM
    if os.environ.get("HELM_REQUIRED") == "1":
        pytest.fail("HELM_REQUIRED=1 but helm is not installed")
    pytest.skip("helm not installed (set HELM_BIN)")


def _helm(*extra: str) -> subprocess.CompletedProcess:
    helm = _require_helm()
    return subprocess.run(
        [helm, *extra],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _render(*extra: str) -> list[dict]:
    lint = _helm("lint", str(CHART), *extra)
    assert lint.returncode == 0, lint.stdout + lint.stderr
    out = _helm("template", "scraping-pipeline", str(CHART), *extra)
    assert out.returncode == 0, out.stderr
    return [doc for doc in yaml.safe_load_all(out.stdout) if doc]


def _containers(doc: dict) -> list[tuple[str, dict]]:
    kind = doc.get("kind")
    spec = doc.get("spec") or {}
    if kind in {"Deployment", "StatefulSet", "DaemonSet", "Job"}:
        pod = spec["template"]["spec"]
    elif kind == "CronJob":
        pod = spec["jobTemplate"]["spec"]["template"]["spec"]
    elif kind == "Pod":
        pod = spec
    else:
        return []
    found = []
    for key in ("containers", "initContainers", "ephemeralContainers"):
        for container in pod.get(key) or []:
            found.append((f"{doc['metadata']['name']}/{container.get('name')}", container))
    return found


def _assert_rendered(docs: list[dict]) -> None:
    containers = [item for doc in docs for item in _containers(doc)]
    assert len(containers) >= 10, [name for name, _ in containers]
    for name, container in containers:
        resources = container.get("resources") or {}
        _assert_cpu_mem(resources, name)


def test_dev_small_and_prod_templates_set_container_resources():
    dev = _render()
    _assert_rendered(dev)
    names = {doc["metadata"]["name"] for doc in dev if doc.get("kind") == "Deployment"}
    assert any(name.endswith("-stage2") for name in names)
    assert not any(name.endswith("-stage4") for name in names)

    small = _render("-f", str(CHART / "values-small.yaml"))
    _assert_rendered(small)
    stage2 = next(doc for doc in small if doc.get("kind") == "Deployment" and doc["metadata"]["name"].endswith("-stage2"))
    assert stage2["spec"]["replicas"] == 1
    assert not any(doc.get("kind") == "HorizontalPodAutoscaler" and doc["metadata"]["name"].endswith("-stage2") for doc in small)
    mem = stage2["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["memory"]
    assert mem == SMALL["stage2Worker"]["resources"]["limits"]["memory"]

    prod = _render("-f", str(CHART / "values-prod.yaml"))
    _assert_rendered(prod)
    prod_stage2 = next(
        doc for doc in prod if doc.get("kind") == "Deployment" and doc["metadata"]["name"].endswith("-stage2")
    )
    assert prod_stage2["spec"]["replicas"] == VALUES["stage2Worker"]["replicaCount"]
    limit = prod_stage2["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["memory"]
    assert limit == PROD["stage2Worker"]["resources"]["limits"]["memory"]
    assert not any(
        doc.get("kind") == "StatefulSet" and doc["metadata"]["name"].endswith("-redis") for doc in prod
    )


def test_stage4_enabled_templates_in_each_profile():
    for extra in (
        ("--set", "stage4Worker.enabled=true"),
        ("-f", str(CHART / "values-small.yaml"), "--set", "stage4Worker.enabled=true"),
        ("-f", str(CHART / "values-prod.yaml"), "--set", "stage4Worker.enabled=true"),
    ):
        docs = _render(*extra)
        _assert_rendered(docs)
        stage4 = next(
            doc for doc in docs if doc.get("kind") == "Deployment" and doc["metadata"]["name"].endswith("-stage4")
        )
        _assert_cpu_mem(stage4["spec"]["template"]["spec"]["containers"][0]["resources"], "stage4")


def test_missing_limit_fails_the_render():
    out = _helm(
        "template",
        "scraping-pipeline",
        str(CHART),
        "--set",
        "stage2Worker.resources.limits.memory=",
    )
    assert out.returncode != 0, out.stdout
    assert "stage2Worker" in out.stderr
    assert "requests and limits" in out.stderr
