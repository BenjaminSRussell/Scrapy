"""#653: Stage 2 HPA can scale on stage2_queue_pending, not only CPU."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "k8s" / "helm" / "scraping-pipeline"
TEMPLATES = CHART / "templates"
MON_RULES = ROOT / "monitoring" / "alerting_rules.yml"
HELM_RULES = CHART / "files" / "monitoring" / "alerting_rules.yml"
EXAMPLE = CHART / "values-stage2-queue-hpa.example.yaml"
ADAPTER = CHART / "examples" / "prometheus-adapter-stage2-queue.yaml"
HELM = os.environ.get("HELM_BIN") or shutil.which("helm")

TARGET = 100


def _alerts(path: Path) -> dict:
    groups = yaml.safe_load(path.read_text())["groups"]
    return {r["alert"]: r for g in groups for r in g["rules"] if "alert" in r}


def test_values_keep_cpu_and_document_queue_target():
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    auto = values["stage2Worker"]["autoscaling"]
    assert auto["enabled"] is True
    assert auto["targetCPUUtilizationPercentage"] == 70
    assert auto["queue"]["enabled"] is False
    assert auto["queue"]["metricName"] == "stage2_queue_pending"
    assert auto["queue"]["targetPendingPerReplica"] == TARGET
    assert auto["keda"]["enabled"] is False


def test_example_overlay_enables_queue_not_keda():
    overlay = yaml.safe_load(EXAMPLE.read_text())
    queue = overlay["stage2Worker"]["autoscaling"]["queue"]
    assert queue["enabled"] is True
    assert queue["metricName"] == "stage2_queue_pending"
    assert queue["targetPendingPerReplica"] == TARGET
    assert "keda" not in overlay["stage2Worker"]["autoscaling"]
    text = EXAMPLE.read_text()
    assert "keda.enabled" in text  # documented alternate, not activated


def test_adapter_example_is_not_a_chart_template():
    text = ADAPTER.read_text()
    assert "externalRules:" in text
    assert "stage2_queue_pending" in text
    assert "max(stage2_queue_pending)" in text
    assert "templates" not in ADAPTER.parts


def test_hpa_and_keda_templates_are_mutually_exclusive():
    hpa = (TEMPLATES / "scrapy-hpa.yaml").read_text()
    keda = (TEMPLATES / "stage2-keda-scaledobject.yaml").read_text()
    helper = (TEMPLATES / "_helpers.tpl").read_text()
    assert "scraping-pipeline.stage2QueueScaleMode" in hpa
    assert 'ne $stage2ScaleMode "keda"' in hpa
    assert 'eq $stage2ScaleMode "queue"' in hpa
    assert "type: External" in hpa
    assert "type: Value" in hpa
    external = hpa.split("type: External", 1)[1].split("behavior:", 1)[0]
    assert "averageValue:" not in external
    assert "kind: ScaledObject" in keda
    assert "type: prometheus" in keda
    assert "type: cpu" in keda
    assert 'eq (include "scraping-pipeline.stage2QueueScaleMode" .) "keda"' in keda
    assert "enable only one (#653)" in helper


def test_pending_without_scale_alert_matches_the_value_target():
    assert MON_RULES.read_text() == HELM_RULES.read_text()
    alert = _alerts(MON_RULES)["Stage2QueuePendingWithoutScale"]
    assert alert["expr"] == f"max(stage2_queue_pending) > count(stage2_queue_pending) * {TARGET}"
    assert alert["for"] == "10m"
    assert alert["labels"]["severity"] == "warning"
    for key in ("summary", "description", "impact", "action"):
        assert alert["annotations"][key]
    groups = yaml.safe_load(MON_RULES.read_text())["groups"]
    home = next(g["name"] for g in groups for r in g["rules"] if r.get("alert") == "Stage2QueuePendingWithoutScale")
    assert home == "stage2_queue"
    assert home != "pipeline_slos"


def test_three_slos_unchanged():
    alerts = _alerts(MON_RULES)
    assert "> 10000" in alerts["QueueBacklog"]["expr"]
    assert "> 250000" in alerts["KafkaConsumerLagSLO"]["expr"]
    stage2 = alerts["Stage2SuccessSLO"]["expr"]
    assert "0.99" in stage2
    assert "bool" not in stage2


needs_helm = pytest.mark.skipif(not HELM, reason="helm not installed (set HELM_BIN to run render checks)")


def _render(*sets: str, values_file: Path | None = None) -> str:
    args = [HELM, "template", "t", str(CHART)]
    if values_file is not None:
        args += ["-f", str(values_file)]
    for s in sets:
        args += ["--set", s]
    out = subprocess.run(args, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return out.stdout


@needs_helm
def test_default_render_is_cpu_hpa_without_keda():
    text = _render()
    assert "kind: HorizontalPodAutoscaler" in text
    assert "type: External" not in text
    assert "kind: ScaledObject" not in text


@needs_helm
def test_example_overlay_adds_external_metric_value():
    text = _render(values_file=EXAMPLE)
    docs = [d for d in yaml.safe_load_all(text) if d and d.get("kind") == "HorizontalPodAutoscaler"]
    stage2 = next(d for d in docs if d["metadata"]["name"].endswith("-stage2"))
    external = [m for m in stage2["spec"]["metrics"] if m["type"] == "External"]
    assert len(external) == 1
    assert external[0]["external"]["metric"]["name"] == "stage2_queue_pending"
    assert external[0]["external"]["target"]["type"] == "Value"
    assert str(external[0]["external"]["target"]["value"]) in {"100", "100.0"}
    assert any(m["type"] == "Resource" and m["resource"]["name"] == "cpu" for m in stage2["spec"]["metrics"])
    assert "kind: ScaledObject" not in text


@needs_helm
def test_keda_replaces_stage2_hpa_and_keeps_cpu_trigger():
    text = _render("stage2Worker.autoscaling.keda.enabled=true")
    docs = [d for d in yaml.safe_load_all(text) if d]
    hpas = [d for d in docs if d["kind"] == "HorizontalPodAutoscaler"]
    assert not any(d["metadata"]["name"].endswith("-stage2") for d in hpas)
    assert any(d["metadata"]["name"].endswith("-scrapy") for d in hpas)
    scaled = next(d for d in docs if d["kind"] == "ScaledObject")
    kinds = {t["type"] for t in scaled["spec"]["triggers"]}
    assert kinds == {"cpu", "prometheus"}
    prom = next(t for t in scaled["spec"]["triggers"] if t["type"] == "prometheus")
    assert prom["metadata"]["metricName"] == "stage2_queue_pending"
    assert prom["metadata"]["query"] == "max(stage2_queue_pending)"
    assert prom["metadata"]["threshold"] == "100"
    assert prom["metadata"]["serverAddress"].endswith("-prometheus:9100")


@needs_helm
def test_queue_and_keda_together_fail_the_render():
    args = [
        HELM, "template", "t", str(CHART),
        "--set", "stage2Worker.autoscaling.queue.enabled=true",
        "--set", "stage2Worker.autoscaling.keda.enabled=true",
    ]
    out = subprocess.run(args, capture_output=True, text=True, timeout=120)
    assert out.returncode != 0
    assert "enable only one" in out.stderr
