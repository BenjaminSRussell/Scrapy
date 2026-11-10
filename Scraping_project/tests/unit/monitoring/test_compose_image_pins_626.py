"""Compose monitoring images stay on release tags, and Grafana matches Helm (#626)."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
CHART = ROOT / "k8s" / "helm" / "scraping-pipeline" / "values.yaml"
README = ROOT / "scripts" / "README.md"


def _image(repository: str, tag: str) -> str:
    assert tag and tag != "latest"
    assert ":" not in tag
    return f"{repository}:{tag}"


def test_grafana_and_siblings_match_helm_pins_and_are_not_latest():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    obs = yaml.safe_load((ROOT / "docker-compose.observability.yml").read_text(encoding="utf-8"))
    values = yaml.safe_load(CHART.read_text(encoding="utf-8"))
    grafana = _image(values["grafana"]["image"]["repository"], values["grafana"]["image"]["tag"])
    prometheus = _image(values["prometheus"]["image"]["repository"], values["prometheus"]["image"]["tag"])
    statsd = values["exporters"]["statsd"]["image"]
    assert compose["services"]["grafana"]["image"] == grafana
    assert obs["services"]["ops-grafana"]["image"] == grafana
    assert compose["services"]["prometheus"]["image"] == prometheus
    assert obs["services"]["ops-prometheus"]["image"] == prometheus
    assert obs["services"]["statsd-exporter"]["image"] == _image(statsd["repository"], statsd["tag"])
    for name in ("redis-exporter", "postgres-exporter", "prometheus", "grafana"):
        image = compose["services"][name]["image"]
        tag = image.split(":", 1)[1]
        assert ":" in image and not image.endswith(":latest")
        assert any(ch.isdigit() for ch in tag)


def test_scripts_readme_notes_the_grafana_upgrade_path():
    text = README.read_text(encoding="utf-8")
    assert "## Monitoring image pins (#626)" in text
    assert "grafana.image.tag" in text
    assert "k8s/helm/scraping-pipeline/values.yaml" in text
    assert "docker-compose.observability.yml" in text
    assert "`latest`" in text
    assert "docker compose --profile monitoring pull grafana prometheus" in text
