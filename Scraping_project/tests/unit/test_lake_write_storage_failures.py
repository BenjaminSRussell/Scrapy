"""#661: lakehouse writes under disk-full (ENOSPC) and permission (EACCES) failures.

Failures are injected at the storage layer (a real read-only directory, or
``write_deltalake`` raising the OSError the Rust writer surfaces), not by
stubbing ``_write_sync``. For each case: the write is reported as failed (never
silent success), the table stays readable with no partial rows, a retry after
recovery writes the batch exactly once, and when retries are exhausted the
batch is spilled, or, if even the spill fails, the loss is loud and counted.
"""

from __future__ import annotations

import errno
import logging
import os
import stat

import deltalake
import pytest

from src.lakehouse import lakehouse_manager as lm
from src.lakehouse.lakehouse_manager import LakehouseManager

needs_non_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores directory permissions"
)


@pytest.fixture
def mgr(tmp_path):
    m = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    m.write_retry_backoff = 0.0
    yield m
    m.shutdown_event.set()
    for p in [tmp_path, *tmp_path.rglob("*")]:  # let pytest clean up read-only dirs
        if p.is_dir() and not p.is_symlink():
            p.chmod(p.stat().st_mode | stat.S_IRWXU)


def _rows(prefix: str, n: int = 3) -> list[dict]:
    return [{"url": f"https://x.edu/{prefix}{i}", "n": i} for i in range(n)]


def _urls(mgr: LakehouseManager, table: str) -> list[str]:
    return sorted(r["url"] for r in mgr.read(table))


def _version(mgr: LakehouseManager, table: str) -> int:
    return deltalake.DeltaTable(str(mgr.get_table_path(table))).version()


def _counter(**labels):
    metric = lm.DELTA_WRITE_FAILURES
    return metric.labels(**labels)._value.get() if metric is not None else None


class _DiskFull:
    """write_deltalake that raises ENOSPC for the first ``times`` calls."""

    def __init__(self, times: int, err: int = errno.ENOSPC):
        self.real = deltalake.write_deltalake
        self.left = times
        self.err = err
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.left:
            self.left -= 1
            raise OSError(self.err, os.strerror(self.err))
        return self.real(*args, **kwargs)


# ------------------------------------------------------------------ ENOSPC


@pytest.mark.parametrize("err", [errno.ENOSPC, errno.EDQUOT])
def test_sync_write_on_full_disk_reports_failure_and_leaves_table_intact(mgr, monkeypatch, err):
    assert mgr.write("t661", _rows("a"), async_write=False) is True
    v0 = _version(mgr, "t661")

    monkeypatch.setattr(deltalake, "write_deltalake", _DiskFull(times=99, err=err))
    assert mgr.write("t661", _rows("b"), async_write=False) is False  # not silent success

    monkeypatch.undo()
    assert _version(mgr, "t661") == v0
    assert _urls(mgr, "t661") == sorted(r["url"] for r in _rows("a"))


def test_async_write_retries_through_transient_enospc_without_duplicates(mgr, monkeypatch):
    full = _DiskFull(times=2)
    monkeypatch.setattr(deltalake, "write_deltalake", full)
    assert mgr._write_with_retry("t661", _rows("a"), "append") is True
    assert full.calls == 3
    assert _urls(mgr, "t661") == sorted(r["url"] for r in _rows("a"))  # exactly once
    assert not mgr.spill_path.exists()


def test_persistent_enospc_spills_and_replays_exactly_once(mgr, monkeypatch):
    monkeypatch.setattr(deltalake, "write_deltalake", _DiskFull(times=99))
    assert mgr._write_with_retry("t661", _rows("a"), "append") is False
    assert len(list((mgr.spill_path / "t661").glob("*.jsonl"))) == 1

    monkeypatch.undo()  # space reclaimed
    assert mgr.replay_spilled_writes() == {"files": 1, "rows": 3, "failed": 0}
    assert mgr.replay_spilled_writes() == {"files": 0, "rows": 0, "failed": 0}
    assert _urls(mgr, "t661") == sorted(r["url"] for r in _rows("a"))


@needs_non_root
def test_spill_failure_is_loud_and_counted(mgr, monkeypatch, caplog):
    """Disk full for the lake AND the spill dir: the batch is lost, so say so."""
    monkeypatch.setattr(deltalake, "write_deltalake", _DiskFull(times=99))
    mgr.spill_path.mkdir(parents=True)
    mgr.spill_path.chmod(stat.S_IRUSR | stat.S_IXUSR)
    before = _counter(table="t661", outcome="lost")
    with caplog.at_level(logging.CRITICAL):
        assert mgr._write_with_retry("t661", _rows("a"), "append") is False
    assert any("DATA LOSS" in r.getMessage() and r.levelno == logging.CRITICAL for r in caplog.records)
    if before is not None:
        assert _counter(table="t661", outcome="lost") == before + 1


# ------------------------------------------------------------------ EACCES


@needs_non_root
def test_readonly_delta_log_fails_commit_without_partial_rows(mgr):
    """Data files get written but the commit can't be: readers must not see them."""
    assert mgr.write("t661", _rows("a"), async_write=False) is True
    log_dir = mgr.get_table_path("t661") / "_delta_log"
    log_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert mgr.write("t661", _rows("b"), async_write=False) is False
    finally:
        log_dir.chmod(stat.S_IRWXU)
    assert _urls(mgr, "t661") == sorted(r["url"] for r in _rows("a"))

    # Retry after the fix: written exactly once despite any orphan data files.
    assert mgr.write("t661", _rows("b"), async_write=False) is True
    assert _urls(mgr, "t661") == sorted(r["url"] for r in _rows("a") + _rows("b"))


@needs_non_root
def test_readonly_table_dir_on_first_write_reports_failure(mgr):
    table_dir = mgr.get_table_path("stage2_queue")  # pre-created, empty
    table_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert mgr.write("stage2_queue", _rows("a"), async_write=False) is False
    finally:
        table_dir.chmod(stat.S_IRWXU)
    assert mgr.write("stage2_queue", _rows("a"), async_write=False) is True
    assert _urls(mgr, "stage2_queue") == sorted(r["url"] for r in _rows("a"))


@needs_non_root
def test_new_table_under_readonly_lake_is_a_failure_not_an_exception(mgr):
    """A table not yet registered is created on first write; if the lake dir is
    read-only that mkdir fails. _write_sync's contract is 'False, logged'."""
    mgr.base_path.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert mgr.write("brand_new_661", _rows("a"), async_write=False) is False
    finally:
        mgr.base_path.chmod(stat.S_IRWXU)


@needs_non_root
def test_async_path_spills_when_write_raises_instead_of_dropping(mgr):
    """Exceptions escaping _write_sync used to skip retry and spill entirely:
    the queue worker logged 'Queue worker error' and the batch was gone."""
    mgr.spill_path.mkdir(parents=True, exist_ok=True)  # spill dir itself stays writable
    mgr.base_path.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert mgr._write_with_retry("brand_new_661", _rows("a"), "append") is False
    finally:
        mgr.base_path.chmod(stat.S_IRWXU)
    assert len(list((mgr.spill_path / "brand_new_661").glob("*.jsonl"))) == 1
    assert mgr.replay_spilled_writes()["rows"] == 3
    assert _urls(mgr, "brand_new_661") == sorted(r["url"] for r in _rows("a"))


def test_queue_worker_does_not_drop_batch_when_write_raises(mgr, monkeypatch):
    """End to end through _process_queue: an unexpected exception from the
    storage layer still ends in a spill file, not a lost batch."""

    def boom(*a, **k):
        raise RuntimeError("storage exploded")

    monkeypatch.setattr(mgr, "_write_sync", boom)
    mgr.write_queue.put(("t661", _rows("a"), "append"))
    mgr.write_queue.put(None)
    mgr._process_queue()
    assert len(list((mgr.spill_path / "t661").glob("*.jsonl"))) == 1
