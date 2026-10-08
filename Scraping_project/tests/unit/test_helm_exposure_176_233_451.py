"""Helm exposure / durability defaults (#176, #233, #451).

Static checks always run. Render checks need ``helm`` (``HELM_BIN`` or PATH).
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
PROD = yaml.safe_load((CHART / "values-prod.yaml").read_text(encoding="utf-8"))
TEMPLATES = CHART / "templates"
HELM = os.environ.get("HELM_BIN") or shutil.which("helm")
needs_helm = pytest.mark.skipif(not HELM, reason="helm not installed (set HELM_BIN)")


def test_profile_defaults_to_development():
    assert VALUES["profile"] == "development"
    assert PROD["profile"] == "production"


def test_grafana_defaults_to_clusterip_not_loadbalancer():
    assert VALUES["grafana"]["service"]["type"] == "ClusterIP"
    assert VALUES["grafana"]["service"].get("allowPublicLoadBalancer") is False
    assert PROD["grafana"]["service"]["type"] == "ClusterIP"
    assert "LoadBalancer" not in yaml.dump(PROD["grafana"])


def test_ingress_tls_empty_in_dev_and_set_in_prod():
    assert VALUES["ingress"]["tls"] == []
    assert PROD["ingress"]["tls"]
    assert PROD["ingress"]["tls"][0]["secretName"] == "grafana-tls"
    assert "cert-manager.io/cluster-issuer" in PROD["ingress"]["annotations"]
    assert PROD["ingress"]["annotations"]["nginx.ingress.kubernetes.io/ssl-redirect"] == "true"


def test_kafka_dev_is_rf1_prod_is_rf3_with_matching_broker_count():
    assert VALUES["kafka"]["replicas"] == 1
    assert VALUES["kafka"]["config"]["defaultReplicationFactor"] == 1
    assert VALUES["kafka"]["config"]["minInsyncReplicas"] == 1
    assert PROD["kafka"]["replicas"] == 3
    assert PROD["kafka"]["config"]["defaultReplicationFactor"] == 3
    assert PROD["kafka"]["config"]["minInsyncReplicas"] == 2
    # RF = minISR + 1 so one broker loss still accepts acks=all
    assert PROD["kafka"]["config"]["defaultReplicationFactor"] == PROD["kafka"]["config"]["minInsyncReplicas"] + 1
    assert PROD["kafka"]["replicas"] >= PROD["kafka"]["config"]["defaultReplicationFactor"]


def test_validate_and_notes_and_helpers_exist():
    helpers = (TEMPLATES / "_helpers.tpl").read_text(encoding="utf-8")
    assert "scraping-pipeline.guardrailProblems" in helpers
    assert "scraping-pipeline.configErrors" in helpers
    assert "defaultReplicationFactor" in helpers and "LoadBalancer" in helpers and "ingress.tls" in helpers
    validate = (TEMPLATES / "validate.yaml").read_text(encoding="utf-8")
    assert 'profile | default "development") "production"' in validate
    assert "fail" in validate
    notes = (TEMPLATES / "NOTES.txt").read_text(encoding="utf-8")
    assert "port-forward" in notes and "WARNING" in notes
    kafka = (TEMPLATES / "kafka-statefulset.yaml").read_text(encoding="utf-8")
    assert "kafka.replicas" in kafka
    assert "OFFSETS_TOPIC_REPLICATION_FACTOR" in kafka
    assert ".Values.kafka.config.defaultReplicationFactor" in kafka


def test_k8s_readme_documents_tls_and_no_public_lb():
    text = (ROOT / "k8s" / "README.md").read_text(encoding="utf-8")
    assert "values-prod.yaml" in text
    assert "ClusterIP" in text and "LoadBalancer" in text
    assert "cert-manager" in text or "TLS" in text


def _helm(*extra: str) -> subprocess.CompletedProcess:
    args = [HELM, "template", "t", str(CHART), *extra]
    return subprocess.run(args, capture_output=True, text=True, timeout=120)


@needs_helm
def test_dev_render_warns_in_notes_but_succeeds():
    out = _helm()
    assert out.returncode == 0, out.stderr
    docs = [d for d in yaml.safe_load_all(out.stdout) if d]
    grafana_svc = next(d for d in docs if d["kind"] == "Service" and d["metadata"]["name"].endswith("-grafana"))
    assert grafana_svc["spec"]["type"] == "ClusterIP"
    kafka_ss = next(d for d in docs if d["kind"] == "StatefulSet" and d["metadata"]["name"].endswith("-kafka"))
    assert kafka_ss["spec"]["replicas"] == 1


@needs_helm
def test_prod_values_render_cleanly():
    out = _helm("-f", str(CHART / "values-prod.yaml"))
    assert out.returncode == 0, out.stderr
    docs = [d for d in yaml.safe_load_all(out.stdout) if d]
    kafka_ss = next(d for d in docs if d["kind"] == "StatefulSet" and d["metadata"]["name"].endswith("-kafka"))
    assert kafka_ss["spec"]["replicas"] == 3
    env = {e["name"]: e.get("value") for e in kafka_ss["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["KAFKA_DEFAULT_REPLICATION_FACTOR"] == "3"
    assert env["KAFKA_MIN_INSYNC_REPLICAS"] == "2"
    assert env["KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR"] == "3"
    ingress = next(d for d in docs if d["kind"] == "Ingress")
    assert ingress["spec"]["tls"]


@needs_helm
@pytest.mark.parametrize("broken,needle", [
    ("profile=production", "defaultReplicationFactor"),  # RF=1 from values.yaml
    ("profile=production,grafana.service.type=LoadBalancer", "LoadBalancer"),
    ("profile=production,kafka.replicas=3,kafka.config.defaultReplicationFactor=3,kafka.config.minInsyncReplicas=2",
     "ingress.tls"),  # ingress still TLS-empty
    ("kafka.replicas=1,kafka.config.defaultReplicationFactor=3", "greater than"),  # configErrors
])
def test_production_profile_refuses_unsafe_configs(broken, needle):
    out = _helm("--set", broken)
    assert out.returncode != 0, out.stdout
    assert needle.lower() in out.stderr.lower() or needle.lower() in out.stdout.lower()
