"""
Unit tests for monitoring/metric_helpers.py

Coverage goals:
- safe_set_gauge: happy path and exception handling
- safe_set_gauge_no_labels: happy path and exception handling
- safe_inc_counter: happy path and exception handling
- safe_observe_histogram: happy path and exception handling
- update_delta_table_gauge: happy path and exception handling
- get_delta_table_size_bytes: happy path, missing table, empty table
- update_delta_table_size_gauge: happy path and exception handling
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from monitoring import metric_helpers


class MetricHandle:
    """Stub handle returned from Gauge/Counter/Histogram.labels()."""

    def __init__(self, store: dict[tuple[tuple[str, str], ...], float], key: tuple[tuple[str, str], ...]):
        self._store = store
        self._key = key
        self.should_raise = False

    def set(self, value: float) -> None:
        if self.should_raise:
            raise RuntimeError("Simulated metric failure")
        self._store[self._key] = value

    def inc(self, amount: float = 1.0) -> None:
        if self.should_raise:
            raise RuntimeError("Simulated metric failure")
        self._store[self._key] = self._store.get(self._key, 0.0) + amount

    def observe(self, value: float) -> None:
        if self.should_raise:
            raise RuntimeError("Simulated metric failure")
        self._store[self._key] = self._store.get(self._key, 0.0) + value


class MetricStub:
    """Simple collector that records the latest value per label set."""

    def __init__(self, name: str = "test_metric") -> None:
        self.values: dict[tuple[tuple[str, str], ...], float] = {}
        self.val: float = 0.0
        self._name = name
        self.should_raise_on_labels = False
        self.should_raise_on_set = False

    def labels(self, **labels: str) -> MetricHandle:
        if self.should_raise_on_labels:
            raise RuntimeError("Simulated labels failure")
        key = tuple(sorted(labels.items()))
        handle = MetricHandle(self.values, key)
        handle.should_raise = self.should_raise_on_set
        return handle

    def set(self, value: float) -> None:
        if self.should_raise_on_set:
            raise RuntimeError("Simulated set failure")
        self.val = value

    def inc(self, amount: float = 1.0) -> None:
        if self.should_raise_on_set:
            raise RuntimeError("Simulated inc failure")
        self.val += amount


@dataclass
class FakeDeltaManager:
    """Fake DeltaLakeManager for testing."""

    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    should_raise: bool = False

    def read(self, table_name: str) -> list[dict[str, Any]]:
        if self.should_raise:
            raise RuntimeError(f"Simulated delta read failure for {table_name}")
        if table_name not in self.tables:
            raise FileNotFoundError(f"Table {table_name} not found")
        return self.tables[table_name]


@pytest.fixture
def gauge_stub():
    """Create a gauge metric stub."""
    return MetricStub(name="test_gauge")


@pytest.fixture
def counter_stub():
    """Create a counter metric stub."""
    return MetricStub(name="test_counter")


@pytest.fixture
def histogram_stub():
    """Create a histogram metric stub."""
    return MetricStub(name="test_histogram")


@pytest.fixture
def delta_manager():
    """Create a fake delta manager."""
    return FakeDeltaManager()


# Tests for safe_set_gauge
def test_safe_set_gauge_happy_path(gauge_stub):
    """Test safe_set_gauge successfully sets gauge with labels."""
    metric_helpers.safe_set_gauge(gauge_stub, {"table": "stage1_discovery"}, 42.0)

    assert gauge_stub.values[(("table", "stage1_discovery"),)] == 42.0


def test_safe_set_gauge_handles_exception(gauge_stub, caplog):
    """Test safe_set_gauge suppresses exceptions and logs them."""
    gauge_stub.should_raise_on_labels = True

    # Should not raise, should log debug message
    metric_helpers.safe_set_gauge(gauge_stub, {"table": "stage1_discovery"}, 42.0)

    # Check that nothing was set (exception was caught)
    assert len(gauge_stub.values) == 0


def test_safe_set_gauge_handles_set_exception(gauge_stub, caplog):
    """Test safe_set_gauge handles exceptions during set operation."""
    gauge_stub.should_raise_on_set = True

    # Should not raise, should log debug message
    metric_helpers.safe_set_gauge(gauge_stub, {"table": "stage1_discovery"}, 42.0)

    # Check that nothing was set (exception was caught)
    assert len(gauge_stub.values) == 0


# Tests for safe_set_gauge_no_labels
def test_safe_set_gauge_no_labels_happy_path(gauge_stub):
    """Test safe_set_gauge_no_labels successfully sets gauge without labels."""
    metric_helpers.safe_set_gauge_no_labels(gauge_stub, 100.0)

    assert gauge_stub.val == 100.0


def test_safe_set_gauge_no_labels_handles_exception(gauge_stub, caplog):
    """Test safe_set_gauge_no_labels suppresses exceptions."""
    gauge_stub.should_raise_on_set = True

    # Should not raise, should log debug message
    metric_helpers.safe_set_gauge_no_labels(gauge_stub, 100.0)

    # Check that value was not set
    assert gauge_stub.val == 0.0


# Tests for safe_inc_counter
def test_safe_inc_counter_happy_path(counter_stub):
    """Test safe_inc_counter successfully increments counter."""
    metric_helpers.safe_inc_counter(counter_stub, {"stage": "stage1"}, 5.0)

    assert counter_stub.values[(("stage", "stage1"),)] == 5.0


def test_safe_inc_counter_default_amount(counter_stub):
    """Test safe_inc_counter uses default increment of 1.0."""
    metric_helpers.safe_inc_counter(counter_stub, {"stage": "stage1"})

    assert counter_stub.values[(("stage", "stage1"),)] == 1.0


def test_safe_inc_counter_handles_exception(counter_stub, caplog):
    """Test safe_inc_counter suppresses exceptions."""
    counter_stub.should_raise_on_labels = True

    # Should not raise
    metric_helpers.safe_inc_counter(counter_stub, {"stage": "stage1"}, 5.0)

    # Check that nothing was incremented
    assert len(counter_stub.values) == 0


# Tests for safe_observe_histogram
def test_safe_observe_histogram_happy_path(histogram_stub):
    """Test safe_observe_histogram successfully observes value."""
    metric_helpers.safe_observe_histogram(histogram_stub, {"stage": "stage1"}, 2.5)

    # In our stub, observe adds to the value
    assert histogram_stub.values[(("stage", "stage1"),)] == 2.5


def test_safe_observe_histogram_handles_exception(histogram_stub, caplog):
    """Test safe_observe_histogram suppresses exceptions."""
    histogram_stub.should_raise_on_labels = True

    # Should not raise
    metric_helpers.safe_observe_histogram(histogram_stub, {"stage": "stage1"}, 2.5)

    # Check that nothing was observed
    assert len(histogram_stub.values) == 0


# Tests for update_delta_table_gauge
def test_update_delta_table_gauge_happy_path(gauge_stub, delta_manager):
    """Test update_delta_table_gauge successfully updates gauge."""
    delta_manager.tables = {"stage1_discovery": [{"url": "http://example.com"}] * 10}

    count = metric_helpers.update_delta_table_gauge(gauge_stub, delta_manager, "stage1_discovery")

    assert count == 10
    assert gauge_stub.values[(("table", "stage1_discovery"),)] == 10


def test_update_delta_table_gauge_empty_table(gauge_stub, delta_manager):
    """Test update_delta_table_gauge handles empty table."""
    delta_manager.tables = {"stage1_discovery": []}

    count = metric_helpers.update_delta_table_gauge(gauge_stub, delta_manager, "stage1_discovery")

    assert count == 0
    assert gauge_stub.values[(("table", "stage1_discovery"),)] == 0


def test_update_delta_table_gauge_missing_table(gauge_stub, delta_manager, caplog):
    """Test update_delta_table_gauge handles missing table."""
    # Table not in delta_manager.tables

    count = metric_helpers.update_delta_table_gauge(gauge_stub, delta_manager, "nonexistent_table")

    assert count is None
    # No gauge should be set
    assert len(gauge_stub.values) == 0


def test_update_delta_table_gauge_handles_delta_exception(gauge_stub, delta_manager, caplog):
    """Test update_delta_table_gauge handles delta manager exceptions."""
    delta_manager.should_raise = True

    count = metric_helpers.update_delta_table_gauge(gauge_stub, delta_manager, "stage1_discovery")

    assert count is None
    assert len(gauge_stub.values) == 0


# Tests for get_delta_table_size_bytes
def test_get_delta_table_size_bytes_happy_path(tmp_path, monkeypatch):
    """Test get_delta_table_size_bytes calculates size correctly."""
    monkeypatch.chdir(tmp_path)

    # Create fake table structure
    table_path = Path("data/delta_lake/stage1_discovery")
    table_path.mkdir(parents=True)

    # Create some parquet files
    (table_path / "part-0000.parquet").write_bytes(b"0" * 100)
    (table_path / "part-0001.parquet").write_bytes(b"1" * 200)

    # Create _delta_log directory (should be ignored)
    log_path = table_path / "_delta_log"
    log_path.mkdir()
    (log_path / "00000000000000000000.json").write_bytes(b"x" * 1000)

    size = metric_helpers.get_delta_table_size_bytes(None, "stage1_discovery")

    # Should only count parquet files (100 + 200), not the log file
    assert size == 300


def test_get_delta_table_size_bytes_missing_table(tmp_path, monkeypatch):
    """Test get_delta_table_size_bytes handles missing table."""
    monkeypatch.chdir(tmp_path)

    size = metric_helpers.get_delta_table_size_bytes(None, "nonexistent_table")

    assert size is None


def test_get_delta_table_size_bytes_no_parquet_files(tmp_path, monkeypatch):
    """Test get_delta_table_size_bytes handles table with no parquet files."""
    monkeypatch.chdir(tmp_path)

    # Create table directory but no parquet files
    table_path = Path("data/delta_lake/stage1_discovery")
    table_path.mkdir(parents=True)

    # Only create _delta_log
    log_path = table_path / "_delta_log"
    log_path.mkdir()
    (log_path / "00000000000000000000.json").write_bytes(b"x" * 1000)

    size = metric_helpers.get_delta_table_size_bytes(None, "stage1_discovery")

    assert size is None


def test_get_delta_table_size_bytes_handles_exception(tmp_path, monkeypatch, caplog):
    """Test get_delta_table_size_bytes handles filesystem exceptions."""
    monkeypatch.chdir(tmp_path)

    # Create a file instead of directory to cause an error
    Path("data/delta_lake").mkdir(parents=True)
    Path("data/delta_lake/stage1_discovery").write_text("not a directory")

    size = metric_helpers.get_delta_table_size_bytes(None, "stage1_discovery")

    assert size is None


# Tests for update_delta_table_size_gauge
def test_update_delta_table_size_gauge_happy_path(tmp_path, monkeypatch, gauge_stub):
    """Test update_delta_table_size_gauge successfully updates gauge."""
    monkeypatch.chdir(tmp_path)

    # Create fake table structure
    table_path = Path("data/delta_lake/stage1_discovery")
    table_path.mkdir(parents=True)
    (table_path / "part-0000.parquet").write_bytes(b"0" * 500)

    size = metric_helpers.update_delta_table_size_gauge(gauge_stub, None, "stage1_discovery")

    assert size == 500
    assert gauge_stub.values[(("table", "stage1_discovery"),)] == 500


def test_update_delta_table_size_gauge_missing_table(tmp_path, monkeypatch, gauge_stub):
    """Test update_delta_table_size_gauge handles missing table."""
    monkeypatch.chdir(tmp_path)

    size = metric_helpers.update_delta_table_size_gauge(gauge_stub, None, "nonexistent_table")

    assert size is None
    # No gauge should be set
    assert len(gauge_stub.values) == 0


def test_update_delta_table_size_gauge_handles_gauge_exception(tmp_path, monkeypatch, gauge_stub):
    """Test update_delta_table_size_gauge handles gauge exceptions gracefully."""
    monkeypatch.chdir(tmp_path)

    # Create table
    table_path = Path("data/delta_lake/stage1_discovery")
    table_path.mkdir(parents=True)
    (table_path / "part-0000.parquet").write_bytes(b"0" * 500)

    # Make gauge raise an exception
    gauge_stub.should_raise_on_labels = True

    # Should not raise, but size calculation should still work
    size = metric_helpers.update_delta_table_size_gauge(gauge_stub, None, "stage1_discovery")

    assert size == 500
    # Gauge should not be set due to exception
    assert len(gauge_stub.values) == 0
