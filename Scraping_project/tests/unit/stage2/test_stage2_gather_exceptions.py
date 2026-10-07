"""#214: exceptions escaping _analyze_url must not be silently dropped."""

import asyncio

from src.core.constants import TABLE_STAGE2_ERRORS
from src.stage2 import stage2_worker as s2


class _FakeDelta:
    def __init__(self, queue):
        self.queue = queue
        self.writes = []

    def read_table(self, name, **kwargs):
        return list(self.queue) if name == "stage2_queue" else []

    def write(self, table, rows, mode="append", async_write=True):
        self.writes.append((table, list(rows)))
        return True

    def merge_into(self, table, rows, merge_key, update_columns):  # analysis upsert (#311)
        self.writes.append((table, list(rows)))
        return len(rows)


def _worker(queue):
    w = s2.Stage2Worker.__new__(s2.Stage2Worker)
    w.max_concurrent, w.batch_size = 2, 10
    w.semaphore = asyncio.Semaphore(2)
    w.delta = _FakeDelta(queue)
    w.postgres = None
    w.max_retries = 3
    w._dlq = None
    return w


def test_gather_exception_is_quarantined_counted_and_left_pending(monkeypatch):
    queue = [{"url": u, "url_hash": f"h-{u}", "status": "pending"} for u in ("ok", "boom")]
    worker = _worker(queue)
    updated = []

    async def analyze(record):
        if record["url"] == "boom":
            raise KeyError("missing field")
        return {"url": record["url"], "url_hash": record["url_hash"], "has_error": False, "is_low_quality": False}

    async def update(urls, table_name="stage2_queue", status="completed"):
        updated.extend((u, status) for u in urls)

    monkeypatch.setattr(worker, "_analyze_url", analyze)
    monkeypatch.setattr(worker, "_update_queue_status", update)
    counter = s2.STAGE2_GATHER_EXCEPTIONS.labels(exception="KeyError") if s2.STAGE2_GATHER_EXCEPTIONS else None
    before = counter._value.get() if counter else None

    counts = asyncio.run(worker._run_traced())

    errors = [r for t, rows in worker.delta.writes if t == TABLE_STAGE2_ERRORS for r in rows]
    assert [(r["url"], r["url_hash"]) for r in errors] == [("boom", "h-boom")]
    assert errors[0]["error_message"].startswith("exception: KeyError")
    assert counts["analyzed"] == 2 and counts["errors"] == 1
    assert updated == [("ok", "completed")]  # boom stays pending for retry (#160)
    if counter is not None:
        assert counter._value.get() == before + 1


def test_normalize_keeps_order_and_handles_non_exception_junk():
    worker = _worker([])
    batch = [{"url": "a", "url_hash": "ha"}, {"url": "b", "url_hash": "hb"}, {"url": "c", "url_hash": "hc"}]
    rows = worker._normalize_gather_results(batch, [{"url": "a"}, ValueError("x"), None])
    assert [r["url"] for r in rows] == ["a", "b", "c"]
    assert rows[1]["has_error"] and rows[1]["error_message"] == "exception: ValueError: x"
    assert rows[2]["has_error"] and rows[2]["error_message"] == "unexpected_result: NoneType"
