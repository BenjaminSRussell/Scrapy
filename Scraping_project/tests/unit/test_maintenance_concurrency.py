"""#702: optimize/vacuum are serialized with writers and retry commit conflicts."""

import threading

import pytest
from deltalake.exceptions import CommitFailedError
from deltalake.table import TableOptimizer
from prometheus_client import REGISTRY

import src.lakehouse.lakehouse_manager as lm
from src.lakehouse.lakehouse_manager import DeltaLakeManager

TABLE = "maint_table"


def _metric(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(lm, "MAINTENANCE_RETRY_BACKOFF", 0.0)
    mgr = DeltaLakeManager(base_path=str(tmp_path / "lake"), start_workers=False)
    for i in range(4):
        assert mgr.write(TABLE, [{"url": f"u{i}", "n": i}], async_write=False)
    yield mgr
    mgr.shutdown()


def test_compaction_holds_the_table_write_lock(manager, monkeypatch):
    seen = []
    original = TableOptimizer.compact

    def spy(self, *a, **kw):
        seen.append(manager._table_lock(TABLE).locked())
        return original(self, *a, **kw)

    monkeypatch.setattr(TableOptimizer, "compact", spy)
    manager._optimize_table(TABLE)
    assert seen == [True]


def test_compaction_conflict_is_retried_on_fresh_snapshot(manager, monkeypatch):
    calls = {"n": 0}
    original = TableOptimizer.compact

    def flaky(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise CommitFailedError("a concurrent transaction added new data")
        return original(self, *a, **kw)

    monkeypatch.setattr(TableOptimizer, "compact", flaky)
    before = _metric("delta_maintenance_conflicts_total", table=TABLE, operation="compact")
    skipped = _metric("delta_optimize_skipped_total", table=TABLE, reason="compact_conflict")
    manager._optimize_table(TABLE)
    assert calls["n"] == 3
    assert _metric("delta_maintenance_conflicts_total", table=TABLE, operation="compact") == before + 2
    assert _metric("delta_optimize_skipped_total", table=TABLE, reason="compact_conflict") == skipped
    assert manager.count(TABLE) == 4


def test_exhausted_conflicts_are_reported_as_conflict_skips(manager, monkeypatch):
    def always(self, *a, **kw):
        raise CommitFailedError("conflict")

    monkeypatch.setattr(TableOptimizer, "compact", always)
    before = _metric("delta_optimize_skipped_total", table=TABLE, reason="compact_conflict")
    manager._optimize_table(TABLE)  # logged + counted, never raises
    assert _metric("delta_optimize_skipped_total", table=TABLE, reason="compact_conflict") == before + 1
    assert manager.count(TABLE) == 4


def test_vacuum_conflict_is_retried(manager, monkeypatch):
    from deltalake import DeltaTable

    calls = {"n": 0}
    original = DeltaTable.vacuum

    def flaky(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise CommitFailedError("conflict")
        return original(self, *a, **kw)

    monkeypatch.setattr(DeltaTable, "vacuum", flaky)
    before = _metric("delta_maintenance_conflicts_total", table=TABLE, operation="vacuum")
    manager._vacuum_table(TABLE)
    assert calls["n"] == 2
    assert _metric("delta_maintenance_conflicts_total", table=TABLE, operation="vacuum") == before + 1


def test_maintenance_concurrent_with_appends_loses_nothing(manager):
    errors, failed_writes = [], []

    def writer():
        try:
            for i in range(30):
                if not manager.write(TABLE, [{"url": f"w{i}", "n": i}], async_write=False):
                    failed_writes.append(i)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    t = threading.Thread(target=writer)
    t.start()
    try:
        for _ in range(5):
            manager._optimize_table(TABLE)
            manager._vacuum_table(TABLE, retention_hours=0, enforce_retention_duration=False)
    finally:
        t.join()

    assert not errors and not failed_writes
    assert manager.count(TABLE) == 34
