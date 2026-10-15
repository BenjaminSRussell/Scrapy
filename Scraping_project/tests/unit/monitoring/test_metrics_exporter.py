"""Tests for monitoring/metrics_exporter.py's MetricsExporter.

Rewritten against the current UDP-StatsD, fire-and-forget design
(StatsDClient.gauge()/counter() sending UDP packets). The previous
version of this file tested a module-level prometheus_client Gauge
design (monkeypatching module attributes like exporter_module.
redis_queue_length) that hasn't existed since metrics_exporter.py was
rewritten - see the fix in this same branch that repointed it from the
deleted get_redis_manager()/config.redis_config to the current
src.utils.redis / src.core.config APIs.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any

import pytest

from unittest.mock import MagicMock

from monitoring import metrics_exporter as exporter_module
from src.utils.metric_labels import metric_domain, metric_stage, metric_worker_id


def _tags(site: str, **extra: str) -> tuple[tuple[str, str], ...]:
    """Identity labels the exporter always sends, plus this family's own keys."""
    labels = {
        "domain": metric_domain(),
        "site_id": site,
        "worker_id": metric_worker_id(),
        "stage": extra.pop("stage", metric_stage(default="exporter")),
    }
    labels.update(extra)
    return tuple(sorted(labels.items()))

class StatsDRecorder:
    """Records gauge()/counter() calls in place of real UDP sends."""

    def __init__(self) -> None:
        self.gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self.counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    def gauge(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        key = (name, tuple(sorted((tags or {}).items())))
        self.gauges[key] = value

    def counter(self, name: str, value: float = 1, tags: dict[str, str] | None = None) -> None:
        key = (name, tuple(sorted((tags or {}).items())))
        self.counters[key] = self.counters.get(key, 0) + value

    def timing(self, name: str, value_ms: float, tags: dict[str, str] | None = None) -> None:
        pass

@dataclass
class FakeJSPriorityQueue:
    queue_size: int = 0

    def size(self) -> int:
        return self.queue_size

@dataclass
class FakeDeltaManager:
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def read(self, table_name: str) -> list[dict[str, Any]]:
        return self.tables.get(table_name, [])

class FakeConfig:
    def get_section(self, section: str) -> dict[str, Any]:
        if section == "redis":
            return {"host": "localhost", "port": 6379, "db": 0, "password": None}
        return {}

    def get(self, key: str, default: Any = None) -> Any:
        return default

@pytest.fixture
def exporter(tmp_path, monkeypatch):
    fake_redis_helper = MagicMock()
    fake_redis_helper.client = MagicMock()
    fake_redis_helper.get_open_circuits.return_value = []

    monkeypatch.setattr(exporter_module.Config, "get_instance", staticmethod(lambda: FakeConfig()))
    monkeypatch.setattr(exporter_module.DeltaLakeManager, "get_instance", staticmethod(lambda: FakeDeltaManager()))
    monkeypatch.setattr(exporter_module, "get_redis", lambda **kwargs: fake_redis_helper)
    monkeypatch.setattr(exporter_module, "JSPriorityQueue", lambda client: FakeJSPriorityQueue())

    exporter_instance = exporter_module.MetricsExporter(
        statsd_port=19999,
        update_interval=5,
        exports_dir=tmp_path / "exports",
    )
    exporter_instance.statsd = StatsDRecorder()
    return exporter_instance

def test_update_queue_metrics_records_priority_queue_size(exporter):
    exporter.js_priority_queue.queue_size = 7

    exporter._update_queue_metrics()

    site = exporter.site_id
    assert exporter.statsd.gauges[("redis.queue.length", _tags(site, queue="priority_queue"))] == 7

def test_update_delta_lake_metrics_tracks_counts(exporter):
    exporter.delta.tables = {
        "stage1_discovery": [{"url": "http://example.com"}] * 5,
        "stage2_page_analysis": [{"url": "http://example.com"}],
    }

    exporter._update_delta_lake_metrics()

    records = exporter.statsd.gauges
    assert records[("delta_lake.records", (("table", "stage1_discovery"),))] == 5
    assert records[("delta_lake.records", (("table", "stage2_page_analysis"),))] == 1
    assert records[("urls.discovered.total", _tags(exporter.site_id, stage="stage1"))] == 5
    assert records[("delta_lake.total_records", ())] == 6

def test_update_throughput_metrics_increments_counters(exporter, monkeypatch):
    exporter.delta.tables = {
        "stage1_discovery": [{}] * 20,
        "stage2_page_analysis": [{}] * 4,
        "stage3_summaries": [{}] * 2,
        "stage4_large_doc_summaries": [{}] * 1,
    }

    exporter.previous_counts = {
        "stage1_discovery": 10,
        "stage2_page_analysis": 1,
        "stage3_summaries": 1,
        "stage4_large_doc_summaries": 0,
    }
    exporter.last_update_time = 100.0
    monkeypatch.setattr(exporter_module.time, "time", lambda: 110.0)

    exporter._update_throughput_metrics()

    counters = exporter.statsd.counters
    gauges = exporter.statsd.gauges

    site = exporter.site_id
    assert counters[("urls.processed.total", _tags(site, stage="stage1"))] == 10
    assert gauges[("urls.processed.per_second", _tags(site, stage="stage1"))] == pytest.approx(1.0)
    assert counters[("urls.processed.total", _tags(site, stage="stage2"))] == 3
    assert gauges[("urls.processed.per_second", _tags(site, stage="stage2"))] == pytest.approx(0.3)
    assert counters[("urls.processed.total", _tags(site, stage="stage4"))] == 1

def test_update_error_metrics_writes_summary(exporter):
    exporter.delta.tables = {
        "stage1_errors": [
            {"error_type": "Timeout"},
            {"error_type": "Timeout"},
            {"error_type": "DNS"},
        ]
    }

    exporter._update_error_metrics()

    counters = exporter.statsd.counters
    site = exporter.site_id
    assert counters[("errors.total", _tags(site, stage="stage1", error_type="Timeout"))] == 2
    assert counters[("errors.total", _tags(site, stage="stage1", error_type="DNS"))] == 1

    summary_path = exporter.error_summary_path
    assert summary_path.exists()

    summary = json.loads(summary_path.read_text())
    assert summary["total_errors"] == 3
    error_types = {entry["type"]: entry["count"] for entry in summary["error_types"]}
    assert error_types == {"Timeout": 2, "DNS": 1}

def test_update_circuit_breaker_metrics_counts_open_circuits(exporter, monkeypatch):
    monkeypatch.setattr(exporter.redis, "get_open_circuits", lambda: ["a.example.com", "b.example.com"])

    exporter._update_circuit_breaker_metrics()

    assert exporter.statsd.gauges[("circuit_breaker.open_count", ())] == 2
    assert exporter.statsd.gauges[("circuit_breaker.state", (("host", "a.example.com"), ("name", "redis")))] == 1
    assert exporter.statsd.gauges[("circuit_breaker.state", (("host", "b.example.com"), ("name", "redis")))] == 1


def test_circuit_breaker_state_exports_shared_rows_and_clears_stale_series(exporter, monkeypatch):
    monkeypatch.setattr(exporter.redis, "get_open_circuits", lambda: ["legacy.example"])
    monkeypatch.setattr(
        exporter.redis,
        "list_shared_circuits",
        lambda: [
            {"name": "stage2:down.example", "host": "down.example", "state": "open"},
            {"name": "http", "host": "", "state": "half-open"},
        ],
    )

    exporter._update_circuit_breaker_metrics()

    gauges = exporter.statsd.gauges
    assert gauges[("circuit_breaker.state", (("host", "down.example"), ("name", "stage2:down.example")))] == 1
    assert gauges[("circuit_breaker.state", (("host", "-"), ("name", "http")))] == 2
    assert gauges[("circuit_breaker.state", (("host", "legacy.example"), ("name", "redis")))] == 1

    monkeypatch.setattr(exporter.redis, "get_open_circuits", lambda: [])
    monkeypatch.setattr(exporter.redis, "list_shared_circuits", lambda: [])
    exporter._update_circuit_breaker_metrics()

    assert gauges[("circuit_breaker.state", (("host", "down.example"), ("name", "stage2:down.example")))] == 0
    assert gauges[("circuit_breaker.state", (("host", "-"), ("name", "http")))] == 0
    assert gauges[("circuit_breaker.state", (("host", "legacy.example"), ("name", "redis")))] == 0


# ---- #208: no blocking wait; HTTP health for k8s probes --------------------

def test_start_does_not_wait_for_crawl_data(exporter, monkeypatch):
    """With no seeds/discovery the first update cycle must still run at once."""
    import time as _time

    cycles = []

    def one_cycle():
        cycles.append(_time.monotonic())
        raise KeyboardInterrupt  # stop the infinite loop after the first cycle

    monkeypatch.setattr(exporter, "_update_queue_metrics", one_cycle)
    t0 = _time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        exporter.start()
    assert cycles and cycles[0] - t0 < 1.0
    assert not hasattr(exporter, "_wait_for_scraping_to_start")


def test_health_endpoints_ready_without_seeds(exporter):
    import urllib.error
    import urllib.request

    server = exporter.start_health_server(0, host="127.0.0.1")
    port = server.server_address[1]
    try:
        body = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5).read().decode()
        assert "metrics_exporter_up 1" in body
        assert urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5).status == 200

        exporter.update_interval = 1
        exporter.started_ts = exporter.last_update_ts = 0.0  # loop stalled long ago
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5)
        assert err.value.code == 503
    finally:
        server.shutdown()
        server.server_close()


def test_update_cycle_records_progress(exporter, monkeypatch):
    for name in ("_update_queue_metrics", "_update_circuit_breaker_metrics", "_update_delta_lake_metrics",
                 "_update_error_metrics", "_update_throughput_metrics"):
        monkeypatch.setattr(exporter, name, lambda: None)

    def stop(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(exporter_module.time, "sleep", stop)
    with pytest.raises(KeyboardInterrupt):
        exporter._update_loop()
    assert exporter.updates_total == 1 and exporter.last_update_ts > 0
    assert exporter.is_healthy()


def test_baseline_core_metrics_carry_configured_site_id(exporter):
    exporter.statsd = StatsDRecorder()
    exporter.site_id = "tenant_a"
    exporter._initialize_metrics()
    assert exporter.statsd.gauges[
        ("redis.queue.length", _tags("tenant_a", queue="priority_queue"))
    ] == 0
    assert exporter.statsd.gauges[
        ("urls.processed.per_second", _tags("tenant_a", stage="stage1"))
    ] == 0.0
    assert exporter.statsd.counters[
        ("errors.total", _tags("tenant_a", stage="stage1", error_type="none"))
    ] == 0
    # A caller-supplied site_id cannot replace the configured one.
    exporter._update_queue_metrics()
    assert (
        "redis.queue.length",
        _tags("tenant_a", queue="priority_queue"),
    ) in exporter.statsd.gauges


def test_metric_site_id_is_configured_id_never_a_url_or_job(monkeypatch):
    from src.utils.redis import metric_site_id

    for name in ("SITE_ID", "TENANT_ID", "REDIS_SITE_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CRAWL_JOB_ID", "job-12345")
    assert metric_site_id() == "default"
    monkeypatch.setenv("SITE_ID", "tenant_a")
    assert metric_site_id() == "tenant_a"
    monkeypatch.setenv("SITE_ID", "https://uconn.edu/jobs/1")
    assert metric_site_id() == "rejected"
    monkeypatch.setenv("SITE_ID", "not a host")
    assert metric_site_id() == "rejected"


def test_cli_accepts_port_flag_used_by_helm(monkeypatch):
    captured = {}

    class Stub:
        def __init__(self, **kw):
            captured["init"] = kw

        def start(self, health_port=0):
            captured["port"] = health_port

    monkeypatch.setattr(exporter_module, "MetricsExporter", Stub)
    monkeypatch.setattr(sys, "argv", ["metrics_exporter.py", "--port", "9100", "--interval", "5"])
    exporter_module.main()
    assert captured["port"] == 9100 and captured["init"]["update_interval"] == 5
