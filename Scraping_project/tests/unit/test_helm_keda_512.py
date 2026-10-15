"""#512: optional KEDA ScaledObject that scales Stage 2 on its queue backlog.

Static checks always run. Render checks run ``helm template`` when helm is on
PATH (or ``HELM_BIN``). Set ``HELM_REQUIRED=1`` to fail instead of skip, which
is what CI does.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "k8s" / "helm" / "scraping-pipeline"
VALUES = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
STAGE2 = VALUES["stage2Worker"]
HPA = (CHART / "templates" / "scrapy-hpa.yaml").read_text(encoding="utf-8")
SCALED_OBJECT = (CHART / "templates" / "keda-scaledobject.yaml").read_text(encoding="utf-8")
NETPOL = (CHART / "templates" / "networkpolicy.yaml").read_text(encoding="utf-8")
README = (ROOT / "k8s" / "README.md").read_text(encoding="utf-8")
DEPLOYMENT = (ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")
HELM = os.environ.get("HELM_BIN") or shutil.which("helm")
STAGE2_HPA = "t-scraping-pipeline-stage2"


def test_keda_is_off_by_default_and_queries_the_stage2_gauge():
    keda = STAGE2["autoscaling"]["keda"]
    assert keda["enabled"] is False
    assert keda["query"] == "max(stage2_queue_pending)"
    assert keda["threshold"] > 0
    assert keda["operatorNamespace"] == "keda"


def test_the_queried_gauge_is_served_by_the_stage2_worker():
    worker = (ROOT / "src" / "stage2" / "stage2_worker.py").read_text(encoding="utf-8")
    assert '"stage2_queue_pending"' in worker
    assert 'start_worker_metrics_server("stage2")' in worker
    deployment = (CHART / "templates" / "stage-workers-deployments.yaml").read_text(encoding="utf-8")
    assert 'command: ["python", "-u", "src/stage2/stage2_worker.py"]' in deployment


def test_native_stage2_hpa_yields_to_keda_and_stage3_is_untouched():
    assert (
        "{{- if and .Values.stage2Worker.enabled .Values.stage2Worker.autoscaling.enabled "
        "(not .Values.stage2Worker.autoscaling.keda.enabled) }}"
    ) in HPA
    assert "{{- if and .Values.stage3Worker.enabled .Values.stage3Worker.autoscaling.enabled }}" in HPA


def test_scaledobject_is_gated_and_keeps_cpu_and_memory_triggers():
    assert (
        "{{- if and $worker.enabled $worker.autoscaling.enabled $worker.autoscaling.keda.enabled }}"
    ) in SCALED_OBJECT
    assert "apiVersion: keda.sh/v1alpha1" in SCALED_OBJECT
    assert "kind: ScaledObject" in SCALED_OBJECT
    assert "type: cpu" in SCALED_OBJECT
    assert "type: memory" in SCALED_OBJECT
    assert "type: prometheus" in SCALED_OBJECT
    assert "ignoreNullValues" in SCALED_OBJECT
    assert "needs prometheus.enabled and workerMetrics.enabled" in SCALED_OBJECT


def test_networkpolicy_admits_the_keda_operator_namespace_only_when_keda_is_on():
    assert (
        "{{- if and .Values.stage2Worker.enabled .Values.stage2Worker.autoscaling.enabled "
        ".Values.stage2Worker.autoscaling.keda.enabled }}"
    ) in NETPOL
    assert "kubernetes.io/metadata.name: {{ .Values.stage2Worker.autoscaling.keda.operatorNamespace | quote }}" in NETPOL


def test_docs_cover_the_metrics_install_path():
    assert "#### Queue-depth autoscaling with KEDA (optional, #512)" in README
    assert "helm repo add kedacore https://kedacore.github.io/charts" in README
    assert "helm install keda kedacore/keda --namespace keda --create-namespace" in README
    assert "--set stage2Worker.autoscaling.keda.enabled=true" in README
    assert "Use `max()`, not `sum()`." in README
    assert "k8s/README.md#queue-depth-autoscaling-with-keda-optional-512" in DEPLOYMENT


def _require_helm() -> str:
    if HELM:
        return HELM
    if os.environ.get("HELM_REQUIRED") == "1":
        pytest.fail("HELM_REQUIRED=1 but helm is not installed")
    pytest.skip("helm not installed (set HELM_BIN)")


def _template(*extra: str) -> subprocess.CompletedProcess:
    helm = _require_helm()
    return subprocess.run(
        [helm, "template", "t", str(CHART), "--namespace", "scraping", *extra],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _docs(out: subprocess.CompletedProcess) -> list[dict]:
    assert out.returncode == 0, out.stderr
    return [doc for doc in yaml.safe_load_all(out.stdout) if doc]


def _named(docs: list[dict], kind: str, name: str) -> list[dict]:
    return [doc for doc in docs if doc.get("kind") == kind and doc["metadata"]["name"] == name]


def test_default_render_keeps_the_native_stage2_hpa_and_no_scaledobject():
    docs = _docs(_template())
    assert not [doc for doc in docs if doc.get("kind") == "ScaledObject"]
    assert _named(docs, "HorizontalPodAutoscaler", STAGE2_HPA)


def test_keda_on_renders_one_scaledobject_in_place_of_the_stage2_hpa():
    docs = _docs(
        _template(
            "--set",
            "stage2Worker.autoscaling.keda.enabled=true",
            "--set",
            "networkPolicy.enabled=true",
        )
    )
    scaled = [doc for doc in docs if doc.get("kind") == "ScaledObject"]
    assert len(scaled) == 1
    spec = scaled[0]["spec"]
    auto = STAGE2["autoscaling"]
    assert spec["scaleTargetRef"]["name"] == STAGE2_HPA
    assert spec["minReplicaCount"] == auto["minReplicas"]
    assert spec["maxReplicaCount"] == auto["maxReplicas"]
    triggers = {trigger["type"]: trigger["metadata"] for trigger in spec["triggers"]}
    assert triggers["cpu"]["value"] == str(auto["targetCPUUtilizationPercentage"])
    assert triggers["prometheus"]["query"] == "max(stage2_queue_pending)"
    assert triggers["prometheus"]["threshold"] == str(auto["keda"]["threshold"])
    port = VALUES["prometheus"]["service"]["port"]
    assert triggers["prometheus"]["serverAddress"] == f"http://t-scraping-pipeline-prometheus.scraping.svc:{port}"
    assert not _named(docs, "HorizontalPodAutoscaler", STAGE2_HPA)
    assert _named(docs, "HorizontalPodAutoscaler", "t-scraping-pipeline-scrapy")  # stage 1 keeps its HPA
    netpol = _named(docs, "NetworkPolicy", "t-scraping-pipeline-prometheus-query-ingress")[0]
    peers = [peer for rule in netpol["spec"]["ingress"] for peer in rule["from"]]
    assert {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "keda"}}} in peers


@pytest.mark.parametrize("override", ["prometheus.enabled=false", "workerMetrics.enabled=false"])
def test_keda_without_the_metrics_path_fails_the_render(override):
    out = _template("--set", "stage2Worker.autoscaling.keda.enabled=true", "--set", override)
    assert out.returncode != 0
    assert "needs prometheus.enabled and workerMetrics.enabled" in out.stderr
