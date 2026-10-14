"""Packaged Prometheus + Grafana for lag, Redis depth, and Delta commits (#1097)."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
MON = ROOT / "monitoring"
OBS = ROOT / "docker-compose.observability.yml"
MAIN = ROOT / "docker-compose.yml"
HELM_RULES = ROOT / "k8s" / "helm" / "scraping-pipeline" / "files" / "monitoring" / "alerting_rules.yml"
DASH = MON / "dashboards" / "ops_lag_depth_commits.json"
HELM_DASH = (
    ROOT / "k8s" / "helm" / "scraping-pipeline" / "files" / "monitoring" / "dashboards" / "ops_lag_depth_commits.json"
)

PACK_SERVICES = ("statsd-exporter", "ops-prometheus", "ops-grafana")
LAG_ALERTS = ("KafkaHighConsumerLag", "KafkaVeryHighConsumerLag", "KafkaConsumerLagSLO")


def _alerts(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {r["alert"]: r for g in doc["groups"] for r in g["rules"] if "alert" in r}


def _norm(expr: str) -> str:
    return " ".join(expr.split())


def _host_ports(services: dict) -> set[str]:
    out = set()
    for svc in services.values():
        for port in svc.get("ports", []):
            parts = str(port).split("/")[0].split(":")
            out.add(parts[-2] if len(parts) >= 2 else parts[0])
    return out


def test_pack_file_starts_alone_and_does_not_collide():
    obs = yaml.safe_load(OBS.read_text(encoding="utf-8"))
    main = yaml.safe_load(MAIN.read_text(encoding="utf-8"))
    assert set(PACK_SERVICES) <= set(obs["services"])
    assert not set(obs["services"]) & set(main["services"])
    assert not _host_ports(obs["services"]) & _host_ports(main["services"])
    net = obs["networks"]["scraper-network"]
    assert net.get("driver") == "bridge"
    assert not net.get("external")
    for name in PACK_SERVICES:
        svc = obs["services"][name]
        assert "profiles" not in svc, name
        image = svc["image"]
        assert ":" in image and not image.endswith(":latest"), image
    for name, svc in obs["services"].items():
        assert set(svc.get("networks", [])) <= set(main["networks"]), name
        for vol in svc.get("volumes", []):
            src = vol.split(":", 1)[0]
            if src.startswith("./"):
                assert (ROOT / src).exists(), f"{name}: {src}"
    rendered = OBS.read_text(encoding="utf-8")
    assert "REDIS_URL" not in rendered


def test_pack_scrapes_optional_and_app_targets():
    doc = yaml.safe_load((MON / "prometheus-pack.yml").read_text(encoding="utf-8"))
    jobs = {j["job_name"]: j for j in doc["scrape_configs"]}
    assert {"prometheus", "statsd", "redis", "scrapy_app", "rust_sitemap", "kafka_jmx",
            "stage2_worker", "stage3_worker", "stage4_worker"} <= set(jobs)
    targets = {}
    for name, job in jobs.items():
        for sc in job.get("static_configs", []):
            for target in sc["targets"]:
                targets[name] = target
    assert targets["statsd"] == "statsd-exporter:9102"
    assert targets["redis"] == "redis-exporter:9121"
    assert targets["scrapy_app"] == "scraper:9410"
    assert targets["rust_sitemap"] == "rust-sitemap:9464"
    assert targets["kafka_jmx"] == "kafka-jmx-exporter:5556"
    assert doc["rule_files"] == [
        "/etc/prometheus/recording_rules.yml",
        "/etc/prometheus/alerting_rules.yml",
    ]
    assert "${" not in str(doc["global"].get("external_labels", {}))


def test_pack_alerts_match_consumer_lag_and_do_not_retune_slos():
    main = _alerts(MON / "alerting_rules.yml")
    pack = _alerts(MON / "alerting_rules_pack.yml")
    assert (HELM_RULES).read_text(encoding="utf-8") == (MON / "alerting_rules.yml").read_text(encoding="utf-8")
    for name in LAG_ALERTS:
        assert _norm(pack[name]["expr"]) == _norm(main[name]["expr"])
    assert "> 250000" in main["KafkaConsumerLagSLO"]["expr"]
    assert "> 10000" in main["QueueBacklog"]["expr"]
    assert "bool" not in main["Stage2SuccessSLO"]["expr"]
    assert "0.99" in main["Stage2SuccessSLO"]["expr"]
    assert _norm(pack["FrontierGrowth"]["expr"]) == _norm(main["FrontierGrowth"]["expr"])
    assert "> 10000" not in main["FrontierGrowth"]["expr"]
    assert all("up == 0" not in rule["expr"] for rule in pack.values())
    assert not (MON / "alerts.yml").exists()


def test_dashboard_shows_lag_crawl_rate_and_delta_commits():
    doc = json.loads(DASH.read_text(encoding="utf-8"))
    assert doc["uid"] == "ops-lag-depth-commits"
    exprs = [t["expr"] for p in doc["panels"] for t in p.get("targets", [])]
    blob = "\n".join(exprs)
    assert "kafka_consumergroup_lag_max" in blob
    assert "redis_queue_length" in blob
    assert "scrapy_items_scraped_total" in blob
    assert "kafka_delta_ingest_offsets_committed_total" in blob
    assert "delta_optimize_skipped_total" in blob
    assert "delta_maintenance_conflicts_total" in blob
    for expr in exprs:
        assert "{group" not in expr and "{consumergroup" not in expr
    assert HELM_DASH.read_text(encoding="utf-8") == DASH.read_text(encoding="utf-8")
    readme = (ROOT.parent / "README.md").read_text(encoding="utf-8")
    assert "## Observability" in readme
    assert "ops-lag-depth-commits" in readme
    cc = (ROOT / "dashboard" / "README.md").read_text(encoding="utf-8")
    assert "does not embed Grafana" in cc
    assert "viewPanel=2" in cc
