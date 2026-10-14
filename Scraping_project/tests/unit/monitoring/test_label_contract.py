"""#243: one label set per metric family, in every process that emits it."""

from __future__ import annotations

import inspect
import socket

import pytest

from src.common.metrics_manager import MetricsManager
from src.stage4.large_doc_processor import stage4_http_failures_total, stage4_http_requests_total
from src.utils.metric_labels import (
    BASE_LABELS,
    REJECTED,
    SHARED_STATSD_FAMILIES,
    STAGE4_HTTP_FAILURE_LABELS,
    STAGE4_HTTP_REQUEST_LABELS,
    STATSD_FAMILIES,
    core_tags,
    identity_labels,
    labels_for,
    metric_domain,
    metric_worker_id,
    sample_labels,
)
from src.utils.redis import metric_site_id
from src import scrapy_prometheus as sp
from monitoring import metrics_exporter as exporter_module


class _Sock:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def sendto(self, payload: bytes, addr) -> None:
        self.sent.append(payload.decode())

    def close(self) -> None:
        return None


def _parse(line: str) -> tuple[str, frozenset[str]]:
    body, _, tags = line.partition("|#")
    name = body.split(":", 1)[0]
    keys = frozenset(part.partition(":")[0] for part in tags.split(",") if part)
    return name, keys


def _emitted_keys(recorder) -> dict[str, set[frozenset[str]]]:
    found: dict[str, set[frozenset[str]]] = {}
    for store in (recorder.gauges, recorder.counters):
        for name, tags in store:
            found.setdefault(name, set()).add(frozenset(key for key, _ in tags))
    return found


@pytest.fixture
def exporter(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from tests.unit.monitoring.test_metrics_exporter import FakeConfig, FakeDeltaManager, FakeJSPriorityQueue, StatsDRecorder

    fake_redis = MagicMock()
    fake_redis.client = MagicMock()
    fake_redis.get_open_circuits.return_value = []
    fake_redis.list_shared_circuits.return_value = []
    monkeypatch.setattr(exporter_module.Config, "get_instance", staticmethod(lambda: FakeConfig()))
    monkeypatch.setattr(exporter_module.DeltaLakeManager, "get_instance", staticmethod(lambda: FakeDeltaManager()))
    monkeypatch.setattr(exporter_module, "get_redis", lambda **kwargs: fake_redis)
    monkeypatch.setattr(exporter_module, "JSPriorityQueue", lambda client: FakeJSPriorityQueue())
    monkeypatch.delenv("PIPELINE_STAGE", raising=False)
    monkeypatch.delenv("METRIC_DOMAIN", raising=False)
    instance = exporter_module.MetricsExporter(
        statsd_port=19999,
        update_interval=5,
        exports_dir=tmp_path / "exports",
    )
    instance.statsd = StatsDRecorder()
    return instance


def test_helpers_do_not_accept_a_url_job_or_host():
    assert inspect.signature(metric_site_id).parameters == {}
    assert inspect.signature(metric_domain).parameters == {}
    assert inspect.signature(metric_worker_id).parameters == {}
    # stage is chosen by the call site or PIPELINE_STAGE, never a free-form host argument
    # on the site/domain/worker helpers.
    assert "site_id" not in inspect.signature(identity_labels).parameters
    assert "domain" not in inspect.signature(identity_labels).parameters
    assert "worker_id" not in inspect.signature(identity_labels).parameters


def test_bad_tokens_are_rejected_and_the_series_is_still_recorded(monkeypatch):
    monkeypatch.delenv("TENANT_ID", raising=False)
    monkeypatch.delenv("REDIS_SITE_ID", raising=False)
    monkeypatch.setenv("SITE_ID", "tenant_a")
    monkeypatch.setenv("METRIC_DOMAIN", "https://uconn.edu/jobs/1")
    monkeypatch.setenv("WORKER_ID", "https://worker.example/9")
    monkeypatch.setenv("CRAWL_JOB_ID", "job-999")
    monkeypatch.setenv("PIPELINE_STAGE", "not a stage")

    assert metric_domain() == REJECTED
    assert metric_worker_id() == REJECTED
    assert metric_site_id() == "tenant_a"
    labels = identity_labels()
    assert labels["stage"] == REJECTED
    assert labels["domain"] == REJECTED
    assert labels["worker_id"] == REJECTED
    assert "job-999" not in labels.values()
    assert all(labels[name] for name in ("stage", "worker_id", "domain", "site_id"))

    # A caller-supplied URL cannot replace the configured labels.
    overridden = core_tags(
        {"stage": "stage2", "site_id": "https://evil.example/j", "domain": "uconn.edu", "queue": "priority_queue"},
        site_id="https://evil.example/a",
    )
    assert overridden["stage"] == "stage2"
    assert overridden["site_id"] == REJECTED
    assert overridden["domain"] == REJECTED  # env is still the URL above
    assert overridden["queue"] == "priority_queue"
    assert set(BASE_LABELS) <= set(overridden)

    ext = sp.PrometheusExtension.__new__(sp.PrometheusExtension)
    spider = type("S", (), {"name": "label_contract_rejected"})()
    ext.item_scraped({"url": "https://evil.example/job/1"}, spider)
    from prometheus_client import REGISTRY

    assert REGISTRY.get_sample_value(
        "scrapy_items_scraped_total",
        sample_labels("scrapy_items_scraped", spider="label_contract_rejected"),
    ) == 1


def test_exporter_and_metrics_manager_share_one_label_set(exporter, monkeypatch):
    sock = _Sock()
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: sock)
    manager = MetricsManager()
    manager.record_stage1_url_discovered()
    manager.record_stage2_page_analyzed(4)
    manager.record_stage3_summary_created()
    manager_keys: dict[str, set[frozenset[str]]] = {}
    for line in sock.sent:
        name, keys = _parse(line)
        manager_keys.setdefault(name, set()).add(keys)

    exporter.statsd = exporter.statsd  # recorder installed by the fixture
    exporter._initialize_metrics()
    exporter.delta.tables = {
        "stage1_discovery": [{}] * 3,
        "stage1_errors": [{"error_type": "Timeout"}],
    }
    exporter.previous_counts = {"stage1_discovery": 1}
    exporter._update_queue_metrics()
    exporter._update_throughput_metrics()
    exporter._update_error_metrics()
    exporter._update_delta_lake_metrics()
    exporter._update_circuit_breaker_metrics()
    emitted = _emitted_keys(exporter.statsd)

    unknown = set(emitted) - set(STATSD_FAMILIES)
    assert not unknown, f"emitted families missing from the contract: {sorted(unknown)}"
    for name, labels in STATSD_FAMILIES.items():
        if name not in emitted:
            continue
        assert emitted[name] == {frozenset(labels)}, f"{name} changed label set: {emitted[name]}"

    for name in SHARED_STATSD_FAMILIES:
        assert name in manager_keys, name
        assert name in emitted, name
        assert manager_keys[name] == {frozenset(STATSD_FAMILIES[name])}
        assert emitted[name] == manager_keys[name]

    assert manager_keys["summaries.created.total"] == {frozenset(STATSD_FAMILIES["summaries.created.total"])}
    assert frozenset(stage4_http_requests_total._labelnames) == frozenset(STAGE4_HTTP_REQUEST_LABELS)
    assert frozenset(stage4_http_failures_total._labelnames) == frozenset(STAGE4_HTTP_FAILURE_LABELS)
    assert frozenset(STAGE4_HTTP_REQUEST_LABELS) == frozenset(emitted["stage4.http.requests.total"].pop())
    failure_sets = emitted["stage4.http.failures.total"]
    assert failure_sets == {frozenset(STAGE4_HTTP_FAILURE_LABELS)}


def test_labels_for_ignores_a_caller_url(monkeypatch):
    monkeypatch.setenv("SITE_ID", "tenant_b")
    monkeypatch.delenv("TENANT_ID", raising=False)
    monkeypatch.delenv("REDIS_SITE_ID", raising=False)
    monkeypatch.delenv("METRIC_DOMAIN", raising=False)
    child = labels_for(
        sp.ITEMS_SCRAPED,
        spider="caller_url",
        site_id="https://evil.example/job",
        domain="https://evil.example",
        worker_id="https://worker",
    )
    child.inc()
    from prometheus_client import REGISTRY

    assert REGISTRY.get_sample_value(
        "scrapy_items_scraped_total",
        sample_labels("scrapy_items_scraped", spider="caller_url"),
    ) == 1
