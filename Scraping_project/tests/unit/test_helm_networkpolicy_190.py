"""Production NetworkPolicy for the data plane (#190).

Static checks always run. This file does not render the chart (helm is optional
and often absent). Render coverage lives in the helm-gated tests elsewhere.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "k8s" / "helm" / "scraping-pipeline"
VALUES = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
PROD = yaml.safe_load((CHART / "values-prod.yaml").read_text(encoding="utf-8"))
NETPOL = (CHART / "templates" / "networkpolicy.yaml").read_text(encoding="utf-8")


def test_dev_leaves_network_policy_off_and_prod_turns_it_on():
    assert VALUES["networkPolicy"]["enabled"] is False
    assert VALUES["networkPolicy"]["breakGlass"] is False
    assert VALUES["networkPolicy"]["policyTypes"] == ["Ingress", "Egress"]
    assert "10.0.0.0/8" in VALUES["networkPolicy"]["scrapeExcept"]
    assert "169.254.0.0/16" in VALUES["networkPolicy"]["scrapeExcept"]
    assert VALUES["networkPolicy"]["ingressNamespace"] == ""
    assert PROD["profile"] == "production"
    assert PROD["networkPolicy"]["enabled"] is True
    assert PROD["networkPolicy"]["breakGlass"] is False
    assert PROD["networkPolicy"]["ingressNamespace"] == "ingress-nginx"


def test_break_glass_skips_policies_and_production_refuses_a_silent_disable():
    assert "not .Values.networkPolicy.breakGlass" in NETPOL
    helpers = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
    assert "networkPolicy.breakGlass" in helpers
    assert "networkPolicy.enabled=false" in helpers
    notes = (CHART / "templates" / "NOTES.txt").read_text(encoding="utf-8")
    assert "networkPolicy.breakGlass" in notes
    assert "port-forward" in notes


def test_policies_cover_redis_kafka_postgres_and_the_app():
    for name in (
        "-redis-ingress",
        "-kafka-ingress",
        "-postgres-ingress",
        "-core-egress",
        "-scrape-egress",
        "-exporter-egress",
        "-prometheus-scrape",
    ):
        assert name in NETPOL
    core = NETPOL.split("-core-egress")[1].split("---")[0]
    redis = NETPOL.split("-redis-ingress")[1].split("---")[0]
    kafka_in = NETPOL.split("-kafka-ingress")[1].split("---")[0]
    postgres = NETPOL.split("-postgres-ingress")[1].split("---")[0]
    for section in (core, redis, kafka_in, postgres):
        assert "scrapy" in section
        assert "stage2" in section and "stage3" in section and "stage4" in section
    assert "redis" in core and "postgres" in core and "kafka" in core
    kafka_eg = NETPOL.split("-kafka-egress")[1].split("---")[0]
    # Subject plus the broker-to-broker allow (prod runs 3 brokers).
    assert kafka_eg.count("app.kubernetes.io/component: kafka") >= 2
    assert "zookeeper" in kafka_eg
    assert "postgres-exporter" in postgres
    assert "redis-exporter" in redis
    assert "kafka-jmx-exporter" in kafka_in
    assert "kafka-topics" in core and "kafka-topics" in kafka_in
    scrape = NETPOL.split("-scrape-egress")[1].split("---")[0]
    assert "ipBlock" in scrape and "scrapeExcept" in scrape
    assert "port: 443" in scrape and "port: 80" in scrape
    # Every component list that admits stage3 also admits stage4.
    lists = re.findall(r"values:\n((?:\s*- [\w-]+.*\n)+)", NETPOL)
    with_stage3 = [block for block in lists if re.search(r"- stage3\b", block)]
    assert len(with_stage3) >= 3
    for block in with_stage3:
        assert re.search(r"- stage4\b", block), block


def test_break_glass_is_documented():
    readme = (ROOT / "k8s" / "README.md").read_text(encoding="utf-8")
    assert "networkPolicy.breakGlass" in readme
    assert "port-forward" in readme
    assert "values-prod.yaml" in readme
    changelog = (ROOT.parent / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "#190" in changelog


def test_helm_does_not_publish_redis_url():
    app_env = (CHART / "templates" / "application-configmap.yaml").read_text(encoding="utf-8")
    # A comment may name REDIS_URL to forbid it. The key itself must not be set.
    assert not re.search(r"(?m)^\s*REDIS_URL\s*:", app_env)
    assert re.search(r"(?m)^\s*REDIS_HOST\s*:", app_env)
    assert re.search(r"(?m)^\s*REDIS_PORT\s*:", app_env)
