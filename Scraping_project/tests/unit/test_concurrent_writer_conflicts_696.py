"""#696: overlapping Delta writers conflict, retry, and keep committed rows."""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import pytest
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import CommitFailedError
from deltalake.transaction import CommitProperties

from src.lakehouse.lakehouse_manager import LakehouseManager
from src.utils.delta import DeltaHelper

TABLE = "stage2_queue"
# _write_sync_locked: commit conflicts retry this many times (backoff 0.05 * attempt),
# then the write fails closed. Not delta_lake.write_retries (that spills async batches).
CONFLICT_ATTEMPTS = 5
CONFLICT_BACKOFF = 0.05


def _row(url_hash: str) -> dict[str, str]:
    return {"url": f"https://example.com/{url_hash}", "url_hash": url_hash}


def _adapter(base: Path) -> DeltaHelper:
    helper = DeltaHelper(base)
    # Private manager, no writer thread: sync writes only, and nothing is queued.
    helper._manager = LakehouseManager(str(base), start_workers=False)
    return helper


def _hashes(helper: DeltaHelper, version: int | None = None) -> list[str]:
    return [row["url_hash"] for row in helper.manager.read(TABLE, version=version)]


def _write_versions(helper: DeltaHelper) -> list[int]:
    history = helper.manager.get_table_history(TABLE)
    return sorted(entry["version"] for entry in history if entry.get("operation") == "WRITE")


def _drop_table(base: Path) -> None:
    """Remove the isolated table (and any uncommitted spill) before the temp dir goes away."""
    shutil.rmtree(base / TABLE, ignore_errors=True)
    shutil.rmtree(base / "_write_spill", ignore_errors=True)
    assert not (base / TABLE).exists()
    assert not (base / "_write_spill").exists()


class _OverlappingWrites:
    """Both writers snapshot one version; the loser then hits a real commit conflict.

    delta-rs retries a stale snapshot internally unless ``max_commit_retries`` is 0,
    which would hide the conflict from the adapter. The losing attempt asks the
    engine to fail immediately so ``_write_sync_locked`` has to detect and retry it.
    The retry itself uses the adapter's normal commit (engine retries left on).
    """

    def __init__(self) -> None:
        self.real = write_deltalake
        self.barrier = threading.Barrier(2)
        self.winner_done = threading.Event()
        self.lock = threading.Lock()
        self.order: list[int] = []
        self.attempts: dict[int, int] = {}
        self.conflicts = 0

    def __call__(self, table_or_uri, data, **kwargs):
        ident = threading.get_ident()
        with self.lock:
            seen = self.attempts.get(ident, 0) + 1
            self.attempts[ident] = seen
        if seen > 1:
            return self.real(table_or_uri, data, **kwargs)

        snapshot = DeltaTable(str(table_or_uri))
        self.barrier.wait(timeout=15)
        with self.lock:
            self.order.append(ident)
            winner = len(self.order) == 1
        if winner:
            try:
                return self.real(table_or_uri, data, **kwargs)
            finally:
                self.winner_done.set()

        if not self.winner_done.wait(timeout=15):
            raise TimeoutError("winning writer did not commit")
        conflict_kwargs = dict(kwargs)
        conflict_kwargs["commit_properties"] = CommitProperties(max_commit_retries=0)
        try:
            self.real(snapshot, data, **conflict_kwargs)
        except CommitFailedError:
            with self.lock:
                self.conflicts += 1
            raise
        raise AssertionError("overlapping commit did not conflict")


class _StaleSnapshotConflicts:
    """Every attempt commits a snapshot taken before the other writer won."""

    def __init__(self, snapshots: list[DeltaTable]) -> None:
        self.real = write_deltalake
        self.snapshots = list(snapshots)
        self.calls = 0

    def __call__(self, table_or_uri, data, **kwargs):
        self.calls += 1
        snapshot = self.snapshots.pop(0)
        conflict_kwargs = dict(kwargs)
        conflict_kwargs["commit_properties"] = CommitProperties(max_commit_retries=0)
        try:
            self.real(snapshot, data, **conflict_kwargs)
        except CommitFailedError:
            raise
        raise AssertionError(f"attempt {self.calls} did not conflict with the committed writer")


@pytest.fixture
def adapters(tmp_path: Path):
    base = tmp_path / "lake"
    opened: list[DeltaHelper] = []

    def open_adapter() -> DeltaHelper:
        helper = _adapter(base)
        opened.append(helper)
        return helper

    yield base, open_adapter
    for helper in opened:
        helper.manager.shutdown()
    shutil.rmtree(base, ignore_errors=True)


@pytest.mark.unit
@pytest.mark.delta
def test_overlapping_writers_retry_conflict_and_keep_every_row(adapters, monkeypatch, caplog):
    base, open_adapter = adapters
    seed = open_adapter()
    assert seed.write(TABLE, [_row("seed")], mode="append", async_write=False) is True

    delays: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda seconds: delays.append(seconds))
    overlap = _OverlappingWrites()
    monkeypatch.setattr("deltalake.write_deltalake", overlap)

    left, right = open_adapter(), open_adapter()
    results: list[bool | BaseException] = []

    def _write(helper: DeltaHelper, url_hash: str) -> None:
        try:
            results.append(helper.write(TABLE, [_row(url_hash)], mode="append", async_write=False))
        except Exception as exc:  # surface thread failures instead of hanging the join
            results.append(exc)

    workers = [
        threading.Thread(target=_write, args=(left, "writer-a")),
        threading.Thread(target=_write, args=(right, "writer-b")),
    ]
    with caplog.at_level("WARNING"):
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=30)
    assert not any(worker.is_alive() for worker in workers)
    assert results == [True, True], results
    assert overlap.conflicts == 1
    assert sorted(overlap.attempts.values()) == [1, 2]
    assert delays == [CONFLICT_BACKOFF]
    assert caplog.text.count(f"Commit conflict writing {TABLE}") == 1
    assert f"attempt 1/{CONFLICT_ATTEMPTS}" in caplog.text

    versions = _write_versions(seed)
    assert len(versions) == 3  # seed + two appends; the lost attempt is not a commit
    snapshots = [set(_hashes(seed, version)) for version in versions]
    assert [len(found) for found in snapshots] == [1, 2, 3]
    assert snapshots[0] == {"seed"}
    assert snapshots[0] < snapshots[1] < snapshots[2]
    assert snapshots[2] == {"seed", "writer-a", "writer-b"}
    latest = _hashes(seed)
    assert sorted(latest) == ["seed", "writer-a", "writer-b"]
    assert len(latest) == len(set(latest))
    assert not (base / "_write_spill").exists()
    _drop_table(base)


@pytest.mark.unit
@pytest.mark.delta
def test_exhausted_commit_conflicts_fail_fast_without_losing_rows(adapters, monkeypatch, caplog):
    base, open_adapter = adapters
    winner = open_adapter()
    assert winner.write(TABLE, [_row("seed")], mode="append", async_write=False) is True
    table_path = str(winner.manager.get_table_path(TABLE))
    # One pinned snapshot per attempt, all read before the winning commit.
    stale = [DeltaTable(table_path) for _ in range(CONFLICT_ATTEMPTS)]
    assert all(table.version() == 0 for table in stale)
    assert winner.write(TABLE, [_row("winner")], mode="append", async_write=False) is True
    version_after_winner = DeltaTable(table_path).version()
    committed = set(_hashes(winner))
    assert committed == {"seed", "winner"}

    delays: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda seconds: delays.append(seconds))
    hook = _StaleSnapshotConflicts(stale)
    monkeypatch.setattr("deltalake.write_deltalake", hook)

    loser = open_adapter()
    with caplog.at_level("WARNING"):
        wrote = loser.write(TABLE, [_row("loser")], mode="append", async_write=False)

    assert wrote is False
    assert hook.calls == CONFLICT_ATTEMPTS
    assert delays == [CONFLICT_BACKOFF * attempt for attempt in range(1, CONFLICT_ATTEMPTS)]
    assert caplog.text.count(f"Commit conflict writing {TABLE}") == CONFLICT_ATTEMPTS - 1
    assert DeltaTable(table_path).version() == version_after_winner
    assert set(_hashes(winner)) == committed
    assert "loser" not in _hashes(winner)
    assert len(_write_versions(winner)) == 2
    assert not (base / "_write_spill").exists()
    _drop_table(base)
