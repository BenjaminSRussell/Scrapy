"""#202: Prometheus and Grafana are published on loopback only.

A shared host reaches both UIs through the SSH tunnel documented in COMPOSE.md.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "docker-compose.yml"
COMPOSE_DOC = ROOT / "COMPOSE.md"
METRICS_UIS = {"prometheus": 9090, "grafana": 3000}


def test_metrics_uis_publish_on_loopback_only():
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    for name, port in METRICS_UIS.items():
        published = [str(entry) for entry in services[name].get("ports", [])]
        assert published == [f"127.0.0.1:{port}:{port}"], f"{name} must bind 127.0.0.1 only (#202): {published}"


def test_compose_doc_gives_the_ssh_tunnel_access_path():
    doc = COMPOSE_DOC.read_text(encoding="utf-8")
    assert "ssh -N -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090" in doc
