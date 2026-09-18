from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from monitoring import metric_helpers

class MetricHandle:

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
    return MetricStub(name="test_gauge")

@pytest.fixture
def counter_stub():
    return MetricStub(name="test_counter")

@pytest.fixture
def histogram_stub():
    return MetricStub(name="test_histogram")

@pytest.fixture
def delta_manager():
    return FakeDeltaManager()

def test_safe_set_gauge_happy_path(gauge_stub):
    metric_helpers.safe_set_gauge(gauge_stub, {"table": "stage1_discovery"}, 42.0)

    assert gauge_stub.values[(("table", "stage1_discovery"),)] == 42.0

def test_safe_set_gauge_handles_exception(gauge_stub, caplog):
    gauge_stub.should_raise_on_labels = True

    metric_helpers.safe_set_gauge(gauge_stub, {"table": "stage1_discovery"}, 42.0)

    assert len(gauge_stub.values) == 0

def test_safe_set_gauge_handles_set_exception(gauge_stub, caplog):
    gauge_stub.should_raise_on_set = True

    metric_helpers.safe_set_gauge(gauge_stub, {"table": "stage1_discovery"}, 42.0)

    assert len(gauge_stub.values) == 0

def test_safe_set_gauge_no_labels_happy_path(gauge_stub):
    metric_helpers.safe_set_gauge_no_labels(gauge_stub, 100.0)

    assert gauge_stub.val == 100.0

def test_safe_set_gauge_no_labels_handles_exception(gauge_stub, caplog):
    gauge_stub.should_raise_on_set = True

    metric_helpers.safe_set_gauge_no_labels(gauge_stub, 100.0)

    assert gauge_stub.val == 0.0

def test_safe_inc_counter_happy_path(counter_stub):
    metric_helpers.safe_inc_counter(counter_stub, {"stage": "stage1"}, 5.0)

    assert counter_stub.values[(("stage", "stage1"),)] == 5.0

def test_safe_inc_counter_default_amount(counter_stub):
    metric_helpers.safe_inc_counter(counter_stub, {"stage": "stage1"})

    assert counter_stub.values[(("stage", "stage1"),)] == 1.0

def test_safe_inc_counter_handles_exception(counter_stub, caplog):
    counter_stub.should_raise_on_labels = True

    metric_helpers.safe_inc_counter(counter_stub, {"stage": "stage1"}, 5.0)

    assert len(counter_stub.values) == 0

def test_safe_observe_histogram_happy_path(histogram_stub):
    metric_helpers.safe_observe_histogram(histogram_stub, {"stage": "stage1"}, 2.5)

    assert histogram_stub.values[(("stage", "stage1"),)] == 2.5

def test_safe_observe_histogram_handles_exception(histogram_stub, caplog):
    histogram_stub.should_raise_on_labels = True

    metric_helpers.safe_observe_histogram(histogram_stub, {"stage": "stage1"}, 2.5)

    assert len(histogram_stub.values) == 0

def test_update_delta_table_gauge_happy_path(gauge_stub, delta_manager):
    delta_manager.tables = {"stage1_discovery": [{"url": "http://example.com"}] * 10}

    count = metric_helpers.update_delta_table_gauge(gauge_stub, delta_manager, "stage1_discovery")

    assert count == 10
    assert gauge_stub.values[(("table", "stage1_discovery"),)] == 10

def test_update_delta_table_gauge_empty_table(gauge_stub, delta_manager):
    delta_manager.tables = {"stage1_discovery": []}

    count = metric_helpers.update_delta_table_gauge(gauge_stub, delta_manager, "stage1_discovery")

    assert count == 0
    assert gauge_stub.values[(("table", "stage1_discovery"),)] == 0

def test_update_delta_table_gauge_missing_table(gauge_stub, delta_manager, caplog):

    count = metric_helpers.update_delta_table_gauge(gauge_stub, delta_manager, "nonexistent_table")

    assert count is None
    assert len(gauge_stub.values) == 0

def test_update_delta_table_gauge_handles_delta_exception(gauge_stub, delta_manager, caplog):
    delta_manager.should_raise = True

    count = metric_helpers.update_delta_table_gauge(gauge_stub, delta_manager, "stage1_discovery")

    assert count is None
    assert len(gauge_stub.values) == 0

def test_get_delta_table_size_bytes_happy_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    table_path = Path("data/delta_lake/stage1_discovery")
    table_path.mkdir(parents=True)

    (table_path / "part-0000.parquet").write_bytes(b"0" * 100)
    (table_path / "part-0001.parquet").write_bytes(b"1" * 200)

    log_path = table_path / "_delta_log"
    log_path.mkdir()
    (log_path / "00000000000000000000.json").write_bytes(b"x" * 1000)

    size = metric_helpers.get_delta_table_size_bytes(None, "stage1_discovery")

    assert size == 300

def test_get_delta_table_size_bytes_missing_table(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    size = metric_helpers.get_delta_table_size_bytes(None, "nonexistent_table")

    assert size is None

def test_get_delta_table_size_bytes_no_parquet_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    table_path = Path("data/delta_lake/stage1_discovery")
    table_path.mkdir(parents=True)

    log_path = table_path / "_delta_log"
    log_path.mkdir()
    (log_path / "00000000000000000000.json").write_bytes(b"x" * 1000)

    size = metric_helpers.get_delta_table_size_bytes(None, "stage1_discovery")

    assert size is None

def test_get_delta_table_size_bytes_handles_exception(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)

    Path("data/delta_lake").mkdir(parents=True)
    Path("data/delta_lake/stage1_discovery").write_text("not a directory")

    size = metric_helpers.get_delta_table_size_bytes(None, "stage1_discovery")

    assert size is None

def test_update_delta_table_size_gauge_happy_path(tmp_path, monkeypatch, gauge_stub):
    monkeypatch.chdir(tmp_path)

    table_path = Path("data/delta_lake/stage1_discovery")
    table_path.mkdir(parents=True)
    (table_path / "part-0000.parquet").write_bytes(b"0" * 500)

    size = metric_helpers.update_delta_table_size_gauge(gauge_stub, None, "stage1_discovery")

    assert size == 500
    assert gauge_stub.values[(("table", "stage1_discovery"),)] == 500

def test_update_delta_table_size_gauge_missing_table(tmp_path, monkeypatch, gauge_stub):
    monkeypatch.chdir(tmp_path)

    size = metric_helpers.update_delta_table_size_gauge(gauge_stub, None, "nonexistent_table")

    assert size is None
    assert len(gauge_stub.values) == 0

def test_update_delta_table_size_gauge_handles_gauge_exception(tmp_path, monkeypatch, gauge_stub):
    monkeypatch.chdir(tmp_path)

    table_path = Path("data/delta_lake/stage1_discovery")
    table_path.mkdir(parents=True)
    (table_path / "part-0000.parquet").write_bytes(b"0" * 500)

    gauge_stub.should_raise_on_labels = True

    size = metric_helpers.update_delta_table_size_gauge(gauge_stub, None, "stage1_discovery")

    assert size == 500
    assert len(gauge_stub.values) == 0
