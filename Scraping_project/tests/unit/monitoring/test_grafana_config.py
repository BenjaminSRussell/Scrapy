"""Validate Grafana datasource provisioning."""

from pathlib import Path

import yaml

import os

def load_datasources():
    base_dir = os.path.dirname(__file__)
    config_path = os.path.abspath(os.path.join(base_dir, "../../..", "monitoring", "grafana_datasource.yml"))
    return yaml.safe_load(Path(config_path).read_text())

def test_required_datasources_present():
    data = load_datasources()
    names = {ds["name"] for ds in data["datasources"]}
    assert {"Prometheus", "Prometheus-B", "Redis", "PostgreSQL"}.issubset(names)

def test_prometheus_datasource_defaults():
    data = load_datasources()
    prom = next(ds for ds in data["datasources"] if ds["name"] == "Prometheus")
    assert prom["isDefault"] is True
    assert prom["type"] == "prometheus"
    assert prom["jsonData"]["timeInterval"] == "5s"

def test_redis_datasource_uses_plugin():
    data = load_datasources()
    redis = next(ds for ds in data["datasources"] if ds["name"] == "Redis")
    assert redis["type"] == "redis-datasource"
    assert redis["jsonData"]["client"] == "standalone"
