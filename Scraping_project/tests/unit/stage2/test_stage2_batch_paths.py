"""#218: Stage2Worker batch loop: success, empty/unreadable queue, nothing pending,
multi-batch splitting, and the no-ack-on-failed-upsert path. Offline (fake Delta)."""
from __future__ import annotations

import asyncio

from src.core.constants import TABLE_STAGE2_ERRORS
from src.stage2 import stage2_worker as s2


class _FakeDelta:
    def __init__(self, queue, *, read_error=None, merge_ok=True):
        self.queue = queue
        self.read_error = read_error
        self.merge_ok = merge_ok
        self.writes: list[tuple[str, list]] = []
        self.merges: list[tuple[str, list]] = []

    def read_table(self, name, **kwargs):
        if name == "stage2_queue":
            if self.read_error:
                raise self.read_error
            return list(self.queue)
        return []

    def write(self, table, rows, mode="append", async_write=True):
        self.writes.append((table, list(rows)))
        return True

    def merge_into(self, table, rows, merge_key, update_columns):
        if not self.merge_ok:
            raise RuntimeError("commit conflict")
        self.merges.append((table, list(rows)))
        return len(rows)


def _worker(delta, batch_size=10):
    w = s2.Stage2Worker.__new__(s2.Stage2Worker)
    w.max_concurrent, w.batch_size = 2, batch_size
    w.semaphore = asyncio.Semaphore(2)
    w.delta = delta
    w.postgres = None
    w.max_retries = 3
    w._dlq = None
    return w


def _queue(*urls, status="pending"):
    return [{"url": u, "url_hash": f"h-{u}", "status": status} for u in urls]


def _wire(monkeypatch, worker, results):
    calls, acks = [], []

    async def analyze(record):
        calls.append(record["url"])
        return {"url": record["url"], "url_hash": record["url_hash"], **results[record["url"]]}

    async def update(urls, table_name="stage2_queue", status="completed"):
        acks.extend((u, status) for u in urls)

    monkeypatch.setattr(worker, "_analyze_url", analyze)
    monkeypatch.setattr(worker, "_update_queue_status", update)
    return calls, acks


ZERO = {"analyzed": 0, "quality_docs": 0, "massive_docs": 0, "errors": 0}


def test_success_path_counts_upserts_and_acks(monkeypatch):
    delta = _FakeDelta(_queue("good", "thin", "huge"))
    worker = _worker(delta)
    _, acks = _wire(monkeypatch, worker, {
        "good": {"has_error": False, "is_low_quality": False},
        "thin": {"has_error": False, "is_low_quality": True},
        "huge": {"has_error": False, "is_low_quality": False, "is_massive_doc": True},
    })

    counts = asyncio.run(worker._run_traced())

    assert counts == {"analyzed": 3, "quality_docs": 1, "massive_docs": 1, "errors": 0}
    upserted = sorted(r["url"] for _, rows in delta.merges for r in rows)
    assert upserted == ["good", "huge", "thin"]
    assert sorted(acks) == [("good", "completed"), ("huge", "completed"), ("thin", "completed")]
    assert not [t for t, _ in delta.writes if t == TABLE_STAGE2_ERRORS]


def test_empty_queue_is_a_noop(monkeypatch):
    worker = _worker(_FakeDelta([]))
    calls, acks = _wire(monkeypatch, worker, {})
    assert asyncio.run(worker._run_traced()) == ZERO
    assert calls == [] and acks == []


def test_unreadable_queue_returns_zero_counts(monkeypatch):
    worker = _worker(_FakeDelta([], read_error=FileNotFoundError("no table")))
    calls, _ = _wire(monkeypatch, worker, {})
    assert asyncio.run(worker._run_traced()) == ZERO
    assert calls == []


def test_only_pending_rows_are_analyzed(monkeypatch):
    queue = _queue("done", status="completed") + _queue("failed", status="failed") + _queue("todo")
    worker = _worker(_FakeDelta(queue))
    calls, _ = _wire(monkeypatch, worker, {"todo": {"has_error": False, "is_low_quality": False}})
    counts = asyncio.run(worker._run_traced())
    assert calls == ["todo"] and counts["analyzed"] == 1


def test_nothing_pending_skips_analysis(monkeypatch):
    worker = _worker(_FakeDelta(_queue("a", "b", status="completed")))
    calls, _ = _wire(monkeypatch, worker, {})
    assert asyncio.run(worker._run_traced()) == ZERO
    assert calls == []


def test_pending_rows_are_split_into_batches(monkeypatch):
    urls = [f"u{i}" for i in range(5)]
    delta = _FakeDelta(_queue(*urls))
    worker = _worker(delta, batch_size=2)
    _wire(monkeypatch, worker, {u: {"has_error": False, "is_low_quality": False} for u in urls})
    counts = asyncio.run(worker._run_traced())
    assert counts["analyzed"] == 5
    assert [len(rows) for _, rows in delta.merges] == [2, 2, 1]


def test_failed_upsert_acks_nothing(monkeypatch):
    delta = _FakeDelta(_queue("a", "b"), merge_ok=False)
    worker = _worker(delta)
    _, acks = _wire(monkeypatch, worker, {
        "a": {"has_error": False, "is_low_quality": False},
        "b": {"has_error": False, "is_low_quality": False},
    })
    asyncio.run(worker._run_traced())
    assert acks == [], "URLs must stay pending when the analysis write did not land (#311)"
