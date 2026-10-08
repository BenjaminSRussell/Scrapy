"""Grafana actually gets its dashboards and datasources (#157)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
MON = ROOT / "monitoring"
DASHBOARDS = MON / "dashboards"
CHART = ROOT / "k8s" / "helm" / "scraping-pipeline"
HELM_DASHBOARDS = CHART / "files" / "monitoring" / "dashboards"
HEALTH = DASHBOARDS / "scraping_pipeline_health.json"


def _compose_grafana() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["grafana"]


def _mounts() -> dict[str, str]:
    """container path -> host path for the compose grafana service."""
    out = {}
    for vol in _compose_grafana()["volumes"]:
        host, container = vol.split(":")[:2]
        out[container] = host
    return out


def _provider_path(path: Path) -> str:
    return yaml.safe_load(path.read_text())["providers"][0]["options"]["path"]


def test_health_dashboard_exists_with_the_documented_uid():
    doc = json.loads(HEALTH.read_text())
    assert doc["uid"] == "scraping-pipeline-health"
    assert doc["title"] == "Scraping Pipeline Health"
    names = [v["name"] for v in doc["templating"]["list"]]
    assert names == ["datasource", "spider", "stage"]
    rows = [p["title"] for p in doc["panels"] if p["type"] == "row"]
    assert rows == ["Overview - Pipeline Health", "Stage 1 - Discovery", "Error Tracking",
                    "Storage & Infrastructure"]


def test_panel_ids_unique_and_targets_use_the_datasource_variable():
    doc = json.loads(HEALTH.read_text())
    ids = [p["id"] for p in doc["panels"]]
    assert len(ids) == len(set(ids))
    for p in doc["panels"]:
        for t in p.get("targets", []):
            assert t["datasource"] == {"type": "prometheus", "uid": "${datasource}"}, p["title"]
            assert t["expr"].strip(), p["title"]


def test_compose_mounts_provisioning_where_grafana_reads_it():
    mounts = _mounts()
    assert mounts["/etc/grafana/provisioning/datasources/datasource.yml"] == "./monitoring/grafana_datasource.yml"
    assert mounts["/etc/grafana/provisioning/dashboards/dashboards.yml"] == "./monitoring/grafana_dashboards.yml"
    provider = _provider_path(MON / "grafana_dashboards.yml")
    assert mounts[provider] == "./monitoring/dashboards"
    assert (ROOT / mounts[provider]).joinpath(HEALTH.name).is_file()


def test_compose_grafana_has_what_the_datasources_need():
    env = "\n".join(_compose_grafana()["environment"])
    assert "GF_PLUGINS_PREINSTALL=redis-datasource" in env
    assert "POSTGRES_PASSWORD=" in env


def test_datasources_point_at_compose_services():
    services = set(yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"])
    sources = yaml.safe_load((MON / "grafana_datasource.yml").read_text())["datasources"]
    for ds in sources:
        host = re.sub(r"^[a-z]+://", "", ds["url"]).split(":")[0].split("/")[0]
        assert host in services, f"{ds['name']} points at {host!r}, which docker-compose.yml does not define"
    prom = next(ds for ds in sources if ds["name"] == "Prometheus")
    assert prom["uid"] == "prometheus" and prom["isDefault"] is True
    health = json.loads(HEALTH.read_text())
    assert health["templating"]["list"][0]["current"]["value"] == prom["uid"]
    pg = next(ds for ds in sources if ds["name"] == "PostgreSQL")
    compose_db = next(e.split("=", 1)[1] for e in yaml.safe_load((ROOT / "docker-compose.yml").read_text())
                      ["services"]["postgres"]["environment"] if e.startswith("POSTGRES_DB="))
    assert pg["database"] == compose_db
    assert pg["secureJsonData"]["password"].startswith("$__env{"), "no literal password in the repo"


def test_helm_dashboard_copies_match_monitoring():
    local = sorted(p.name for p in DASHBOARDS.glob("*.json"))
    helm = sorted(p.name for p in HELM_DASHBOARDS.glob("*.json"))
    assert local == helm
    for name in local:
        assert (HELM_DASHBOARDS / name).read_text() == (DASHBOARDS / name).read_text(), (
            f"k8s/helm/.../files/monitoring/dashboards/{name} drifted from monitoring/dashboards/{name}")


def test_both_providers_read_the_mounted_directory():
    assert _provider_path(MON / "grafana_dashboards.yml") == "/var/lib/grafana/dashboards"
    assert _provider_path(CHART / "files" / "monitoring" / "grafana_dashboards.yml") == "/var/lib/grafana/dashboards"


def _helm() -> str | None:
    return shutil.which("helm") or next(
        (p for p in ("/tmp/helm", "/tmp/linux-amd64/helm") if Path(p).is_file()), None)


@pytest.mark.skipif(_helm() is None, reason="helm not installed")
def test_helm_renders_every_dashboard_into_the_configmap():
    out = subprocess.run([_helm(), "template", "t", str(CHART)], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    docs = [d for d in yaml.safe_load_all(out.stdout) if d]
    cm = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("-grafana-dashboard-unified"))
    assert sorted(cm["data"]) == sorted(p.name for p in DASHBOARDS.glob("*.json"))
    for name, body in cm["data"].items():
        assert json.loads(body) == json.loads((DASHBOARDS / name).read_text())
    dep = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("-grafana"))
    mounts = {m["name"]: m["mountPath"] for m in dep["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]}
    assert mounts["dashboards"] == "/var/lib/grafana/dashboards"
    vol = next(v for v in dep["spec"]["template"]["spec"]["volumes"] if v["name"] == "dashboards")
    assert "items" not in vol["configMap"], "every key in the ConfigMap must be mounted"
