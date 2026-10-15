"""Per-tenant namespace ResourceQuota and LimitRange example (#676).

Static checks always run. Render checks run ``helm template`` when a helm
binary is available (``HELM_BIN`` or ``helm`` on PATH) and skip otherwise.
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
EXAMPLE = CHART / "values-tenant.example.yaml"
README = ROOT / "k8s" / "README.md"
HELM = os.environ.get("HELM_BIN") or shutil.which("helm")

HARD_KEYS = (
    "requests.cpu",
    "requests.memory",
    "limits.cpu",
    "limits.memory",
    "pods",
    "persistentvolumeclaims",
    "services",
    "services.loadbalancers",
    "services.nodeports",
)


def test_example_values_are_a_tenant_quota_and_limitrange():
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "REDIS_URL:" not in text
    assert "Do not set REDIS_URL" in text
    example = yaml.safe_load(text)
    quota = example["namespaceQuota"]
    assert quota["enabled"] is True
    hard = quota["hard"]
    for key in HARD_KEYS:
        assert isinstance(hard[key], str) and hard[key], key
    assert hard["services.loadbalancers"] == "0"
    assert hard["services.nodeports"] == "0"
    assert "requests.storage" not in hard
    limit_range = example["limitRange"]
    assert limit_range["enabled"] is True
    for section in ("default", "defaultRequest", "max", "min"):
        assert limit_range[section]["cpu"]
        assert limit_range[section]["memory"]
    assert limit_range["max"]["cpu"] == "2"
    assert limit_range["max"]["memory"] == "8Gi"
    assert limit_range["min"]["cpu"] == "50m"
    assert limit_range["defaultRequest"]["memory"] == "128Mi"


def test_default_chart_does_not_enable_limitrange():
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    assert values["namespaceQuota"]["enabled"] is True
    assert not (values.get("limitRange") or {}).get("enabled")
    quota = (CHART / "templates" / "resourcequota.yaml").read_text(encoding="utf-8")
    assert "namespaceQuota.hard" in quota
    assert "values-tenant.example.yaml" in quota
    template = (CHART / "templates" / "limitrange.yaml").read_text(encoding="utf-8")
    assert "kind: LimitRange" in template
    assert ".Values.limitRange.enabled" in template
    # Nested if: a missing limitRange must not evaluate .enabled.
    assert template.index("if .Values.limitRange }}") < template.index("if .Values.limitRange.enabled }}")


def test_readme_documents_namespace_per_tenant():
    text = README.read_text(encoding="utf-8")
    assert "values-tenant.example.yaml" in text
    assert "ResourceQuota" in text
    assert "LimitRange" in text
    assert "tenant-a" in text
    assert "Do not set `REDIS_URL`" in text


needs_helm = pytest.mark.skipif(not HELM, reason="helm not installed (set HELM_BIN to run render checks)")


def _template(*extra: str) -> list[dict]:
    args = [HELM, "template", "tenant", str(CHART), *extra]
    out = subprocess.run(args, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return [doc for doc in yaml.safe_load_all(out.stdout) if doc]


@needs_helm
def test_default_render_has_quota_and_no_limitrange():
    docs = _template()
    kinds = {doc["kind"] for doc in docs}
    assert "LimitRange" not in kinds
    quotas = [doc for doc in docs if doc["kind"] == "ResourceQuota"]
    assert len(quotas) == 1
    assert "pods" not in quotas[0]["spec"]["hard"]


@needs_helm
def test_tenant_overlay_renders_quota_and_limitrange():
    docs = _template("-f", str(EXAMPLE))
    quotas = [doc for doc in docs if doc["kind"] == "ResourceQuota"]
    limits = [doc for doc in docs if doc["kind"] == "LimitRange"]
    assert len(quotas) == 1
    assert quotas[0]["spec"]["hard"]["pods"] == "80"
    assert quotas[0]["spec"]["hard"]["services.loadbalancers"] == "0"
    assert len(limits) == 1
    container = limits[0]["spec"]["limits"][0]
    assert container["type"] == "Container"
    assert container["max"]["memory"] == "8Gi"
    assert container["defaultRequest"]["cpu"] == "100m"
    assert container["min"]["cpu"] == "50m"
