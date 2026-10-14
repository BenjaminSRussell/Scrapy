"""#545: cost-allocation proxy is request count times a stated unit cost.

The figure is not a measured cloud bill. site_id comes from metric_site_id()
(SITE_ID, else TENANT_ID, else REDIS_SITE_ID, else default). A value that
fails the site-id pattern is recorded as rejected. The series is not dropped.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from prometheus_client import REGISTRY

from src import scrapy_prometheus as sp
from src.utils.metric_labels import cost_proxy_labels

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "monitoring" / "recording_rules.yml"
HELM_RULES = ROOT / "k8s" / "helm" / "scraping-pipeline" / "files" / "monitoring" / "recording_rules.yml"


def _clear_job_context() -> None:
    from src.otel_tracing import _CRAWL_JOB_ID

    _CRAWL_JOB_ID.set(None)


def _isolate(monkeypatch, worker: str, *, site: str | None, job: str | None, unit: str | None = None) -> None:
    monkeypatch.setenv("WORKER_ID", worker)
    monkeypatch.setenv("METRIC_DOMAIN", "costtest")
    monkeypatch.setenv("PIPELINE_STAGE", "stage1")
    monkeypatch.delenv("TENANT_ID", raising=False)
    monkeypatch.delenv("REDIS_SITE_ID", raising=False)
    if site is None:
        monkeypatch.delenv("SITE_ID", raising=False)
    else:
        monkeypatch.setenv("SITE_ID", site)
    if job is None:
        monkeypatch.delenv("CRAWL_JOB_ID", raising=False)
    else:
        monkeypatch.setenv("CRAWL_JOB_ID", job)
    if unit is None:
        monkeypatch.delenv("COST_USD_PER_REQUEST", raising=False)
    else:
        monkeypatch.setenv("COST_USD_PER_REQUEST", unit)
    _clear_job_context()


def _extension():
    ext = sp.PrometheusExtension.__new__(sp.PrometheusExtension)
    return ext


def _value(labels: dict[str, str]) -> float | None:
    return REGISTRY.get_sample_value("pipeline_estimated_cost_usd", labels)


def test_proxy_is_request_count_times_stated_unit_cost(monkeypatch):
    _isolate(monkeypatch, "finops-count", site="tenant_a", job="job-count", unit="0.25")
    spider = SimpleNamespace(name="finops_count", crawl_job_id="job-count")
    request = SimpleNamespace(method="GET", url="https://evil.example/secret")
    ext = _extension()
    ext.request_scheduled(request, spider)
    ext.request_scheduled(request, spider)
    labels = cost_proxy_labels(spider)
    assert labels["crawl_job_id"] == "job-count"
    assert labels["site_id"] == "tenant_a"
    assert _value(labels) == pytest.approx(0.5)
    assert "https://evil.example/secret" not in labels.values()


def test_default_unit_cost_and_invalid_override(monkeypatch):
    assert sp.DEFAULT_COST_USD_PER_REQUEST == 0.0001
    _isolate(monkeypatch, "finops-default", site="tenant_a", job="job-default")
    spider = SimpleNamespace(name="finops_default")
    ext = _extension()
    ext.request_scheduled(SimpleNamespace(method="GET", url="https://host.example/a"), spider)
    labels = cost_proxy_labels(spider)
    assert labels["crawl_job_id"] == "job-default"
    assert _value(labels) == pytest.approx(0.0001)

    monkeypatch.setenv("COST_USD_PER_REQUEST", "not-a-number")
    assert sp.unit_cost_usd() == sp.DEFAULT_COST_USD_PER_REQUEST
    monkeypatch.setenv("COST_USD_PER_REQUEST", "-5")
    assert sp.unit_cost_usd() == sp.DEFAULT_COST_USD_PER_REQUEST


def test_site_id_follows_metric_site_id_not_job_or_host(monkeypatch):
    _isolate(monkeypatch, "finops-tenant", site=None, job="job-should-not-be-site")
    monkeypatch.setenv("TENANT_ID", "from_tenant")
    spider = SimpleNamespace(name="finops_tenant", url="https://raw.host.example/crawl")
    ext = _extension()
    ext.request_scheduled(SimpleNamespace(method="GET", url=spider.url), spider)
    labels = cost_proxy_labels(spider)
    assert labels["site_id"] == "from_tenant"
    assert labels["crawl_job_id"] == "job-should-not-be-site"
    assert labels["site_id"] != labels["crawl_job_id"]
    assert _value(labels) == pytest.approx(0.0001)

    _isolate(monkeypatch, "finops-redis-site", site=None, job="job-redis-site")
    monkeypatch.setenv("REDIS_SITE_ID", "from_redis_site")
    spider_r = SimpleNamespace(name="finops_redis")
    ext.request_scheduled(SimpleNamespace(method="GET", url="https://raw.host.example/b"), spider_r)
    labels_r = cost_proxy_labels(spider_r)
    assert labels_r["site_id"] == "from_redis_site"
    assert labels_r["crawl_job_id"] == "job-redis-site"

    _isolate(monkeypatch, "finops-default-site", site=None, job=None)
    spider_d = SimpleNamespace(name="finops_default_site", crawl_job_id="spider-job")
    monkeypatch.setenv("CRAWL_JOB_ID", "env-job-ignored")
    ext.request_scheduled(SimpleNamespace(method="GET", url="https://raw.host.example/c"), spider_d)
    labels_d = cost_proxy_labels(spider_d)
    assert labels_d["site_id"] == "default"
    assert labels_d["crawl_job_id"] == "spider-job"


def test_rejected_ids_keep_the_series(monkeypatch):
    _isolate(
        monkeypatch,
        "finops-rejected",
        site="https://uconn.edu/a?job=1",
        job="https://jobs.example/1",
    )
    spider = SimpleNamespace(
        name="finops_rejected",
        crawl_job_id="https://jobs.example/1",
        url="https://raw.host.example/d",
    )
    ext = _extension()
    ext.request_scheduled(SimpleNamespace(method="GET", url=spider.url), spider)
    labels = cost_proxy_labels(spider)
    assert labels["site_id"] == "rejected"
    assert labels["crawl_job_id"] == "rejected"
    assert _value(labels) == pytest.approx(sp.DEFAULT_COST_USD_PER_REQUEST)
    assert spider.url not in labels.values()


def test_missing_job_id_is_unknown_not_dropped(monkeypatch):
    _isolate(monkeypatch, "finops-unknown-job", site="tenant_a", job=None)
    spider = SimpleNamespace(name="finops_unknown")
    ext = _extension()
    ext.request_scheduled(SimpleNamespace(method="GET", url="https://raw.host.example/e"), spider)
    labels = cost_proxy_labels(spider)
    assert labels["crawl_job_id"] == "unknown"
    assert labels["site_id"] == "tenant_a"
    assert _value(labels) is not None


def test_recording_rule_sums_proxy_by_job_and_site():
    assert RULES.read_text() == HELM_RULES.read_text()
    rules = yaml.safe_load(RULES.read_text())
    group = next(g for g in rules["groups"] if g["name"] == "finops_cost_proxy")
    recorded = {r["record"]: r["expr"] for r in group["rules"]}
    expr = recorded["pipeline:estimated_cost_usd"]
    assert "pipeline_estimated_cost_usd" in expr
    assert "crawl_job_id" in expr
    assert "site_id" in expr
    slo = next(g for g in rules["groups"] if g["name"] == "pipeline_slos")
    slo_expr = slo["rules"][0]["expr"]
    assert 'stage2_rows_total{outcome="accepted"}' in slo_expr
    assert "sum(increase(stage2_rows_total[24h]))" in slo_expr


def test_deployment_finops_note_calls_it_a_proxy():
    text = (ROOT / "DEPLOYMENT.md").read_text()
    assert "pipeline_estimated_cost_usd" in text
    assert "crawl_job_id" in text
    assert "site_id" in text
    assert "0.0001" in text
    assert "COST_USD_PER_REQUEST" in text
    assert "not a measured cloud bill" in text
    assert "rejected" in text
    assert "SITE_ID" in text and "TENANT_ID" in text and "REDIS_SITE_ID" in text
