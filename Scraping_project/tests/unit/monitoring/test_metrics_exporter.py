from __future__ import annotations

"""
Unit tests for the Prometheus metrics exporter.

Coverage goals:
- Queue depth gauges (Redis + priority queue)
- Delta Lake record/size gauges and total counter aggregation
- Throughput counters (rate calculations per stage)
- Error summary rollup file and counter increments
"""

import sys
from pathlib import Path

# Add project root to path to allow absolute imports from src
# This is necessary for tests to run consistently, especially in CI
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Increment the metric for test path adjustments
from src.scrapy_prometheus import TEST_IMPORT_PATH_ADJUSTMENTS, MODULE_IMPORT_FAILURES
try:
    # Try to import the module to see if the path adjustment worked
    import monitoring.metrics_exporter
    if TEST_IMPORT_PATH_ADJUSTMENTS:
        TEST_IMPORT_PATH_ADJUSTMENTS.labels(test_file=Path(__file__).name).inc()
except ImportError:
    if MODULE_IMPORT_FAILURES:
        MODULE_IMPORT_FAILURES.labels(module_name="monitoring.metrics_exporter").inc()
    raise

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from monitoring import metrics_exporter as exporter_module


class MetricHandle:
    """Stub handle returned from Gauge/Counter.labels()."""

    def __init__(self, store: dict[tuple[tuple[str, str], ...], float], key: tuple[tuple[str, str], ...]):
        self._store = store
        self._key = key

    def set(self, value: float) -> None:
        self._store[self._key] = value

    def inc(self, amount: float = 1.0) -> None:
        self._store[self._key] = self._store.get(self._key, 0.0) + amount


class MetricStub:
    """Simple collector that records the latest value per label set."""

    def __init__(self) -> None:
        self.values: dict[tuple[tuple[str, str], ...], float] = {}

    def labels(self, **labels: str) -> MetricHandle:
        key = tuple(sorted(labels.items()))
        return MetricHandle(self.values, key)

    def set(self, value: float) -> None:
        """Set value for a metric with no labels."""
        self.values[()] = value

    def inc(self, amount: float = 1.0) -> None:
        """Increment value for a metric with no labels."""
        self.values[()] = self.values.get((), 0.0) + amount


@dataclass
class FakeRedisManager:
    queue_stats: dict[str, int] = field(default_factory=dict)
    queue_size: int = 0
    open_circuits: list[str] = field(default_factory=list)

    def get_all_queue_stats(self) -> dict[str, int]:
        return self.queue_stats

    def get_queue_size(self) -> int:
        return self.queue_size

    def get_open_circuits(self) -> list[str]:
        return self.open_circuits


@dataclass
class FakeDeltaManager:
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def read(self, table_name: str) -> list[dict[str, Any]]:
        return self.tables.get(table_name, [])


class FakeConfig:
    redis_config = {
        "host": "localhost",
        "port": 6379,
        "db": 0,
        "password": None,
    }

    def get(self, key: str, default: Any = None) -> Any:
        if key == "delta_lake.base_path":
            return "./data/delta_lake"
        if key == "delta_lake.queue_maxsize":
            return 50
        return default


@pytest.fixture
def metric_stubs(monkeypatch):
    stubs = {
        "redis_queue_length": MetricStub(),
        "delta_lake_records": MetricStub(),
        "delta_lake_total_records": MetricStub(),
        "delta_lake_size_bytes": MetricStub(),
        "urls_processed_total": MetricStub(),
        "urls_processed_per_second": MetricStub(),
        "errors_total": MetricStub(),
        "total_urls_discovered": MetricStub(),
    }

    for name, stub in stubs.items():
        monkeypatch.setattr(exporter_module, name, stub)

    return stubs


@pytest.fixture
def fake_backends(monkeypatch):
    redis_backend = FakeRedisManager()
    delta_backend = FakeDeltaManager()

    # Patch Config.get_instance() to return FakeConfig
    monkeypatch.setattr("src.common.config.Config.get_instance", lambda: FakeConfig())
    # Patch get_redis_manager to return our fake
    monkeypatch.setattr(exporter_module, "get_redis_manager", lambda **kwargs: redis_backend)
    # Patch DeltaLakeManager.get_instance() to return our fake
    monkeypatch.setattr("src.common.delta_lake.DeltaLakeManager.get_instance", lambda: delta_backend)

    return redis_backend, delta_backend


@pytest.fixture
def exporter(tmp_path, metric_stubs, fake_backends):
    exporter_instance = exporter_module.MetricsExporter(
        port=9999,
        update_interval=5,
        exports_dir=tmp_path / "exports",
    )
    return exporter_instance


def test_update_queue_metrics_records_lengths(exporter, metric_stubs, fake_backends):
    redis_backend, _ = fake_backends
    redis_backend.queue_stats = {"stage2_queue": 7}
    redis_backend.queue_size = 3

    exporter._update_queue_metrics()

    gauge_values = metric_stubs["redis_queue_length"].values
    assert gauge_values[(("queue", "stage2_queue"),)] == 7
    assert gauge_values[(("queue", "priority_queue"),)] == 3


def test_update_delta_lake_metrics_tracks_counts_and_sizes(
    tmp_path, exporter, metric_stubs, fake_backends, monkeypatch
):
    _, delta_backend = fake_backends
    delta_backend.tables = {
        "stage1_discovery": [{"url": "http://example.com"}] * 5,
        "stage2_page_analysis": [{"url": "http://example.com"}],
    }

    monkeypatch.chdir(tmp_path)
    discovery_path = Path("data/delta_lake/stage1_discovery")
    discovery_path.mkdir(parents=True)
    (discovery_path / "part-0000.parquet").write_bytes(b"0123456789")

    exporter._update_delta_lake_metrics()

    records_gauge = metric_stubs["delta_lake_records"].values
    size_gauge = metric_stubs["delta_lake_size_bytes"].values
    total_gauge = metric_stubs["delta_lake_total_records"].values
    discovered_total = metric_stubs["total_urls_discovered"].values

    assert records_gauge[(("table", "stage1_discovery"),)] == 5
    assert records_gauge[(("table", "stage2_page_analysis"),)] == 1
    assert size_gauge[(("table", "stage1_discovery"),)] == 10
    assert total_gauge[()] == 6
    assert discovered_total[()] == 5


def test_update_throughput_metrics_increments_counters(monkeypatch, exporter, metric_stubs, fake_backends):
    _, delta_backend = fake_backends
    delta_backend.tables = {
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

    counter_values = metric_stubs["urls_processed_total"].values
    rate_values = metric_stubs["urls_processed_per_second"].values

    assert counter_values[(("stage", "stage1"),)] == 10
    assert rate_values[(("stage", "stage1"),)] == pytest.approx(1.0)
    assert counter_values[(("stage", "stage2"),)] == 3
    assert rate_values[(("stage", "stage2"),)] == pytest.approx(0.3)


def test_update_error_metrics_writes_summary(tmp_path, exporter, metric_stubs, fake_backends):
    _, delta_backend = fake_backends
    delta_backend.tables = {
        "stage1_errors": [
            {"error_type": "Timeout"},
            {"error_type": "Timeout"},
            {"error_type": "DNS"},
        ]
    }

    exporter._update_error_metrics()

    counter_values = metric_stubs["errors_total"].values
    assert counter_values[(("error_type", "Timeout"), ("stage", "stage1"))] == 2
    assert counter_values[(("error_type", "DNS"), ("stage", "stage1"))] == 1

    summary_path = exporter.error_summary_path
    assert summary_path.exists()

    summary = json.loads(summary_path.read_text())
    assert summary["total_errors"] == 3
    error_types = {entry["type"]: entry["count"] for entry in summary["error_types"]}
    assert error_types == {"Timeout": 2, "DNS": 1}
