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
from dataclasses import dataclass, field
from typing import Any

import pytest

from unittest.mock import MagicMock

from monitoring import metrics_exporter as exporter_module

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

    assert exporter.statsd.gauges[("redis.queue.length", (("queue", "priority_queue"),))] == 7

def test_update_delta_lake_metrics_tracks_counts(exporter):
    exporter.delta.tables = {
        "stage1_discovery": [{"url": "http://example.com"}] * 5,
        "stage2_page_analysis": [{"url": "http://example.com"}],
    }

    exporter._update_delta_lake_metrics()

    records = exporter.statsd.gauges
    assert records[("delta_lake.records", (("table", "stage1_discovery"),))] == 5
    assert records[("delta_lake.records", (("table", "stage2_page_analysis"),))] == 1
    assert records[("urls.discovered.total", ())] == 5
    assert records[("delta_lake.total_records", ())] == 6

def test_update_throughput_metrics_increments_counters(exporter, monkeypatch):
    exporter.delta.tables = {
        "stage1_discovery": [{}] * 20,
        "stage2_page_analysis": [{}] * 4,
        "stage3_summaries": [{}] * 2,
        "stage4_summaries": [{}] * 1,
    }

    exporter.previous_counts = {
        "stage1_discovery": 10,
        "stage2_page_analysis": 1,
        "stage3_summaries": 1,
        "stage4_summaries": 0,
    }
    exporter.last_update_time = 100.0
    monkeypatch.setattr(exporter_module.time, "time", lambda: 110.0)

    exporter._update_throughput_metrics()

    counters = exporter.statsd.counters
    gauges = exporter.statsd.gauges

    assert counters[("urls.processed.total", (("stage", "stage1"),))] == 10
    assert gauges[("urls.processed.per_second", (("stage", "stage1"),))] == pytest.approx(1.0)
    assert counters[("urls.processed.total", (("stage", "stage2"),))] == 3
    assert gauges[("urls.processed.per_second", (("stage", "stage2"),))] == pytest.approx(0.3)

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
    assert counters[("errors.total", (("error_type", "Timeout"), ("stage", "stage1")))] == 2
    assert counters[("errors.total", (("error_type", "DNS"), ("stage", "stage1")))] == 1

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
