"""#331: Stage 2 failures go to stage2_errors, never into stage2_page_analysis."""

import asyncio

from src.core.constants import TABLE_STAGE2_ERRORS
from src.core.schemas import SCHEMA_REGISTRY
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


def _row(url, has_error):
    return {"url": url, "url_hash": url, "has_error": has_error, "is_low_quality": False, "is_massive_doc": False}


def test_split_stage2_results():
    ok, bad = _row("a", False), _row("b", True)
    accepted, quarantined = s2.split_stage2_results([ok, bad, RuntimeError("x"), None])
    assert accepted == [ok]
    assert quarantined == [bad]


def test_worker_routes_errors_to_quarantine_table(monkeypatch):
    queue = [{"url": u, "status": "pending"} for u in ("ok1", "bad1", "ok2")]
    worker = s2.Stage2Worker.__new__(s2.Stage2Worker)
    worker.max_concurrent = 2
    worker.batch_size = 10
    worker.semaphore = asyncio.Semaphore(2)
    worker.delta = _FakeDelta(queue)
    worker.postgres = None
    updated = []

    async def fake_analyze(record):
        return _row(record["url"], record["url"].startswith("bad"))

    async def fake_update(urls, table_name="stage2_queue"):
        updated.extend(urls)

    monkeypatch.setattr(worker, "_analyze_url", fake_analyze)
    monkeypatch.setattr(worker, "_update_queue_status", fake_update)
    before_q = s2.STAGE2_ROWS.labels(outcome="quarantined")._value.get() if s2.STAGE2_ROWS else None

    counts = asyncio.run(worker._run_traced())

    tables = {t: [r["url"] for r in rows] for t, rows in worker.delta.writes}
    assert tables["stage2_page_analysis"] == ["ok1", "ok2"]
    assert tables[TABLE_STAGE2_ERRORS] == ["bad1"]
    assert all(not r["has_error"] for t, rows in worker.delta.writes if t == "stage2_page_analysis" for r in rows)
    # Failed URLs are still marked processed so they are not retried forever.
    assert sorted(updated) == ["bad1", "ok1", "ok2"]
    assert counts["analyzed"] == 3 and counts["errors"] == 1
    if before_q is not None:
        assert s2.STAGE2_ROWS.labels(outcome="quarantined")._value.get() == before_q + 1


def test_quarantine_table_is_registered():
    assert SCHEMA_REGISTRY[TABLE_STAGE2_ERRORS] is SCHEMA_REGISTRY["stage2_page_analysis"]
