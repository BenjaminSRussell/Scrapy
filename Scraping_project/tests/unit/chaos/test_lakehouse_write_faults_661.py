"""#661: lakehouse writes under disk-full (ENOSPC) and permission-denied (EACCES).

Faults are injected by patching ``deltalake.write_deltalake`` (imported lazily at
call time by ``LakehouseManager._write_sync_locked``; patched by dotted path so a test
that re-imports ``deltalake`` earlier in the session can't leave us a stale module), ``os.fsync``, and
``Path.mkdir``. The host filesystem is never made full or read-only. Every lake
lives under ``tmp_path``.

Contract under test (lakehouse_manager.py docstrings, #225):
- a failed write returns False, is logged, and is never reported as success;
- an uncommitted partial write is invisible to readers (Delta log is the truth);
- async-path writes that exhaust retries are spilled to ``_write_spill/`` as JSONL
  and replayed exactly once (file deleted after a successful replay);
- a failed spill is loud (CRITICAL "DATA LOSS"), never silent.
"""
from __future__ import annotations

import errno
import importlib
import logging
import os
from pathlib import Path

import pytest
from deltalake import DeltaTable

from src.lakehouse.lakehouse_manager import SPILL_DIR_NAME, LakehouseManager

TABLE = "stage2_errors"  # registered, not domain-partitioned


def _rows(n: int, start: int = 0) -> list[dict]:
    return [{"url": f"https://www.uconn.edu/p/{i}", "error": f"e{i}", "n": i} for i in range(start, start + n)]


def _table_rows(mgr: LakehouseManager, table: str = TABLE) -> list[dict]:
    path = mgr.base_path / table
    if not (path / "_delta_log").exists():
        return []
    return DeltaTable(str(path)).to_pyarrow_table().to_pylist()


def _version(mgr: LakehouseManager, table: str = TABLE) -> int | None:
    path = mgr.base_path / table
    return DeltaTable(str(path)).version() if (path / "_delta_log").exists() else None


@pytest.fixture
def mgr(tmp_path):
    m = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    m.write_retries = 3
    m.write_retry_backoff = 0.0
    yield m
    m.shutdown_event.set()


class _Faulty:
    """Stand-in for write_deltalake: fail the first ``fail_times`` calls with ``exc``."""

    def __init__(self, exc: BaseException, fail_times: int = 10**9, partial: bool = False):
        self.real = importlib.import_module("deltalake").write_deltalake
        self.exc, self.fail_times, self.partial, self.calls = exc, fail_times, partial, 0

    def __call__(self, table_uri, data, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            if self.partial:
                # Simulate a writer that got a data file onto disk before the
                # commit (the _delta_log entry) failed.
                Path(table_uri).mkdir(parents=True, exist_ok=True)
                (Path(table_uri) / f"part-0000{self.calls}-orphan.zstd.parquet").write_bytes(b"PAR1 partial")
            raise self.exc
        return self.real(table_uri, data, *args, **kwargs)


ENOSPC = OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
EACCES = PermissionError(errno.EACCES, os.strerror(errno.EACCES))


@pytest.mark.parametrize("exc", [ENOSPC, EACCES], ids=["ENOSPC", "EACCES"])
def test_failed_create_returns_false_and_leaves_no_table(mgr, monkeypatch, caplog, exc):
    monkeypatch.setattr("deltalake.write_deltalake", _Faulty(exc))
    with caplog.at_level(logging.ERROR):
        assert mgr._write_sync(TABLE, _rows(3)) is False
    assert _version(mgr) is None and _table_rows(mgr) == []
    assert f"Write failed for {TABLE}" in caplog.text and os.strerror(exc.errno) in caplog.text


@pytest.mark.parametrize("exc", [ENOSPC, EACCES], ids=["ENOSPC", "EACCES"])
def test_failed_append_keeps_existing_rows_and_version(mgr, monkeypatch, exc):
    assert mgr._write_sync(TABLE, _rows(2)) is True
    v0 = _version(mgr)
    monkeypatch.setattr("deltalake.write_deltalake", _Faulty(exc))
    assert mgr._write_sync(TABLE, _rows(2, start=2)) is False
    assert _version(mgr) == v0
    assert sorted(r["n"] for r in _table_rows(mgr)) == [0, 1]


@pytest.mark.parametrize("exc", [ENOSPC, EACCES], ids=["ENOSPC", "EACCES"])
def test_partial_write_before_commit_is_invisible_and_retry_writes_once(mgr, monkeypatch, exc):
    assert mgr._write_sync(TABLE, _rows(1)) is True
    faulty = _Faulty(exc, fail_times=1, partial=True)
    monkeypatch.setattr("deltalake.write_deltalake", faulty)
    assert mgr._write_sync(TABLE, _rows(2, start=1)) is False
    assert list((mgr.base_path / TABLE).glob("*orphan*")), "fault injection should leave an orphan file"
    assert sorted(r["n"] for r in _table_rows(mgr)) == [0], "orphan data file must not be readable"
    # Disk recovers: the retried batch lands exactly once.
    assert mgr._write_sync(TABLE, _rows(2, start=1)) is True
    assert sorted(r["n"] for r in _table_rows(mgr)) == [0, 1, 2]


def test_async_path_exhausts_retries_then_spills_and_replays_once(mgr, monkeypatch, caplog):
    faulty = _Faulty(ENOSPC)
    monkeypatch.setattr("deltalake.write_deltalake", faulty)
    with caplog.at_level(logging.WARNING):
        assert mgr._write_with_retry(TABLE, _rows(3), "append") is False
    assert faulty.calls == mgr.write_retries
    assert "retrying" in caplog.text and "Spilled 3 rows" in caplog.text
    spilled = list((mgr.base_path / SPILL_DIR_NAME / TABLE).glob("*.jsonl"))
    assert len(spilled) == 1 and _table_rows(mgr) == []

    # Still full: replay keeps the spill file and reports the failure.
    assert mgr.replay_spilled_writes(TABLE) == {"files": 0, "rows": 0, "failed": 1}
    assert spilled[0].exists()

    monkeypatch.setattr("deltalake.write_deltalake", faulty.real)
    assert mgr.replay_spilled_writes(TABLE) == {"files": 1, "rows": 3, "failed": 0}
    assert not spilled[0].exists()
    # A second replay (operator re-run) must not duplicate rows.
    assert mgr.replay_spilled_writes(TABLE) == {"files": 0, "rows": 0, "failed": 0}
    assert sorted(r["n"] for r in _table_rows(mgr)) == [0, 1, 2]


def test_async_path_recovers_mid_retry_without_duplicates(mgr, monkeypatch):
    monkeypatch.setattr("deltalake.write_deltalake", _Faulty(ENOSPC, fail_times=mgr.write_retries - 1))
    assert mgr._write_with_retry(TABLE, _rows(4), "append") is True
    assert sorted(r["n"] for r in _table_rows(mgr)) == [0, 1, 2, 3]
    assert not (mgr.base_path / SPILL_DIR_NAME / TABLE).exists()


def test_spill_on_full_disk_is_loud_and_leaves_no_replayable_garbage(mgr, monkeypatch, caplog):
    monkeypatch.setattr("deltalake.write_deltalake", _Faulty(ENOSPC))

    def no_space(_fd):
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    monkeypatch.setattr(os, "fsync", no_space)
    with caplog.at_level(logging.CRITICAL):
        assert mgr._write_with_retry(TABLE, _rows(2), "append") is False
    assert any(r.levelno == logging.CRITICAL and "DATA LOSS" in r.getMessage() for r in caplog.records)
    # No half-written spill file is ever picked up as a valid batch.
    assert not list((mgr.base_path / SPILL_DIR_NAME).rglob("*.jsonl"))
    monkeypatch.undo()
    assert mgr.replay_spilled_writes() == {"files": 0, "rows": 0, "failed": 0}
    assert _table_rows(mgr) == []


def test_permission_denied_creating_new_table_dir_is_a_failed_write_not_a_crash(mgr, monkeypatch):
    """Dynamic (unregistered) tables mkdir their path before the guarded write."""
    real_mkdir = Path.mkdir

    def deny(self, *args, **kwargs):
        if self.name == "brand_new_table" and SPILL_DIR_NAME not in self.parts:
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(self))
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", deny)
    # _write_sync promises "Returns False if the write failed (logged, not raised)".
    assert mgr._write_sync("brand_new_table", _rows(1)) is False


def test_async_queue_never_drops_a_batch_when_the_write_raises(mgr, monkeypatch):
    """#225: a queued batch is written or durably spilled, never acked-and-lost."""
    real_mkdir = Path.mkdir

    def deny(self, *args, **kwargs):
        if self.name == "brand_new_table" and SPILL_DIR_NAME not in self.parts:
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(self))
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", deny)
    mgr.write_queue.put(("brand_new_table", _rows(2), "append"))
    mgr.write_queue.put(None)
    mgr._process_queue()
    spilled = list((mgr.base_path / SPILL_DIR_NAME).rglob("*.jsonl"))
    assert spilled, "batch was acknowledged but neither written nor spilled"


def test_async_queue_spills_even_if_the_retry_wrapper_itself_raises(mgr, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("unexpected writer bug")

    monkeypatch.setattr(mgr, "_write_with_retry", boom)
    mgr.write_queue.put((TABLE, _rows(2), "append"))
    mgr.write_queue.put(None)
    mgr._process_queue()
    spilled = list((mgr.base_path / SPILL_DIR_NAME / TABLE).glob("*.jsonl"))
    assert len(spilled) == 1
    assert mgr.write_queue.unfinished_tasks == 0
    monkeypatch.undo()
    assert mgr.replay_spilled_writes(TABLE)["rows"] == 2
    assert sorted(r["n"] for r in _table_rows(mgr)) == [0, 1]
