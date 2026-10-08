"""#655 / #663: Stage 2 when its backing store drops mid-run, and replay idempotency.

#655 asked for "Redis drops mid Stage 2 batch". Stage 2 has no Redis dependency
today: its queue is the Delta ``stage2_queue`` table, and results are upserted to
``stage2_page_analysis`` (see ``src/stage2/stage2_worker.py``). These tests apply the
same intent (store outage mid-batch: no hang, no silent success, no double-processing)
to the stores Stage 2 actually uses. Everything is offline, using a real Delta lake under
``tmp_path`` with ``_analyze_url`` stubbed (no HTTP).

#663: replaying identical input (operator re-run, partial first run, a re-crawl that
re-enqueues the same URLs) yields one analysis row per ``url_hash``.
"""
from __future__ import annotations

import asyncio
import logging

import pytest
from deltalake import DeltaTable

from src.lakehouse.lakehouse_manager import LakehouseManager
from src.stage2 import stage2_worker as sw
from src.stage2.stage2_worker import Stage2Worker

pytestmark = pytest.mark.stage2

URLS = [f"https://www.uconn.edu/page/{i}" for i in range(4)]
RUN_TIMEOUT = 20  # seconds; a hang fails the test instead of the suite


def _queue_rows(urls, start=0):
    return [{"url": u, "url_hash": f"h{start + i}", "status": "pending"} for i, u in enumerate(urls)]


@pytest.fixture
def stage2(tmp_path, monkeypatch):
    monkeypatch.setenv("STAGE2_MERGE_RETRIES", "1")
    lake = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    assert lake._write_sync("stage2_queue", _queue_rows(URLS), "append")
    w = Stage2Worker()
    w.delta = lake
    w.batch_size = 2
    w.postgres = None
    calls: list[str] = []

    async def fake_analyze(record):
        calls.append(record["url"])
        return {
            "url": record["url"],
            "url_hash": record["url_hash"],
            "status_code": 200,
            "word_count": 100 + len(calls),  # changes per call, so an upsert is visible
            "has_error": False,
            "is_low_quality": False,
            "is_massive_doc": False,
        }

    w._analyze_url = fake_analyze
    yield w, lake, calls
    lake.shutdown_event.set()


def _analysis(lake):
    path = lake.get_table_path("stage2_page_analysis")
    if not (path / "_delta_log").exists():
        return []
    return DeltaTable(str(path)).to_pyarrow_table().to_pylist()


def _status(lake):
    out: dict[str, list[str]] = {}
    for r in lake.read("stage2_queue"):
        out.setdefault(r["url"], []).append(r["status"])
    return out


async def _run(w):
    return await asyncio.wait_for(w._run_traced(), timeout=RUN_TIMEOUT)


@pytest.mark.asyncio
async def test_analysis_store_drops_mid_run_second_batch_stays_pending(stage2, monkeypatch, caplog):
    w, lake, calls = stage2
    real_merge = lake.merge_into
    seen = {"n": 0}

    def drop_on_second_batch(*a, **k):
        seen["n"] += 1
        if seen["n"] == 2:
            raise ConnectionError("store connection reset mid-batch")
        return real_merge(*a, **k)

    monkeypatch.setattr(lake, "merge_into", drop_on_second_batch)
    before = sw.STAGE2_ANALYSIS_WRITE_FAILURES._value.get() if sw.STAGE2_ANALYSIS_WRITE_FAILURES else None
    with caplog.at_level(logging.ERROR):
        counts = await _run(w)  # returns: no hang, no crash

    status = _status(lake)
    assert [status[u] for u in URLS[:2]] == [["completed"], ["completed"]]
    assert [status[u] for u in URLS[2:]] == [["pending"], ["pending"]], "unwritten batch was acked"
    assert {r["url"] for r in _analysis(lake)} == set(URLS[:2])
    assert "Analysis upsert raised" in caplog.text and "leaving 2 URLs pending" in caplog.text
    if before is not None:
        assert sw.STAGE2_ANALYSIS_WRITE_FAILURES._value.get() == before + 1
    assert counts["analyzed"] == 4  # attempted, but only durable rows were acked

    # Store back: the replay processes only what is still pending, once.
    monkeypatch.setattr(lake, "merge_into", real_merge)
    calls.clear()
    await _run(w)
    assert sorted(calls) == sorted(URLS[2:])
    assert all(s == ["completed"] for s in _status(lake).values())
    rows = _analysis(lake)
    assert len(rows) == 4 and len({r["url_hash"] for r in rows}) == 4


@pytest.mark.asyncio
async def test_queue_ack_store_drops_mid_run_then_replay_does_not_duplicate(stage2, monkeypatch, caplog):
    """Analysis written, queue ack lost (store down): replay upserts, never appends."""
    w, lake, calls = stage2
    real = w._merge_queue_status
    seen = {"n": 0}

    def drop_second_ack(urls, table_name, status):
        seen["n"] += 1
        if seen["n"] == 2:
            raise ConnectionError("store connection reset during queue MERGE")
        return real(urls, table_name, status)

    monkeypatch.setattr(w, "_merge_queue_status", drop_second_ack)
    with caplog.at_level(logging.ERROR):
        await _run(w)
    assert [_status(lake)[u] for u in URLS[2:]] == [["pending"], ["pending"]]
    assert "Rows stay pending and will be retried next run" in caplog.text
    assert len(_analysis(lake)) == 4
    original = {r["url_hash"]: r["word_count"] for r in _analysis(lake)}

    monkeypatch.setattr(w, "_merge_queue_status", real)
    calls.clear()
    await _run(w)
    assert sorted(calls) == sorted(URLS[2:])          # only un-acked URLs re-analysed
    rows = _analysis(lake)
    assert len(rows) == 4, "replay duplicated analysis rows"
    by_hash = {r["url_hash"]: r for r in rows}
    assert len(by_hash) == 4
    # Replayed rows were replaced by the newer analysis (upsert), not kept twice;
    # rows acked in the first run are untouched.
    assert all(by_hash[h]["word_count"] != original[h] for h in ("h2", "h3"))
    assert all(by_hash[h]["word_count"] == original[h] for h in ("h0", "h1"))


@pytest.mark.asyncio
async def test_full_rerun_of_completed_queue_is_a_no_op(stage2):
    w, lake, calls = stage2
    await _run(w)
    first = sorted((r["url_hash"], r["word_count"]) for r in _analysis(lake))
    calls.clear()
    counts = await _run(w)
    assert calls == [] and counts["analyzed"] == 0
    assert sorted((r["url_hash"], r["word_count"]) for r in _analysis(lake)) == first


@pytest.mark.asyncio
async def test_recrawl_reenqueue_yields_one_analysis_row_per_url_hash(stage2):
    """Queue writes are appends, so a re-crawl can enqueue a URL twice (same batch or not)."""
    w, lake, calls = stage2
    assert lake._write_sync("stage2_queue", _queue_rows(URLS[:2]), "append")  # same url/url_hash again
    w.batch_size = 10  # duplicates land in one batch: MERGE source must be de-duplicated
    await _run(w)
    rows = _analysis(lake)
    assert sorted(r["url_hash"] for r in rows) == ["h0", "h1", "h2", "h3"]
    assert all(set(s) == {"completed"} for s in _status(lake).values())


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason=(
    "Known gap: Stage2Worker ignores delta.write()'s False return for stage2_errors and still logs "
    "'Quarantined N failed URLs'; the retry counter (read from stage2_errors) then never advances. "
    "Fix deferred until open PRs touching stage2_worker.py land (see #655 PR)."
))
async def test_failed_error_quarantine_write_is_not_reported_as_success(stage2, monkeypatch, caplog):
    w, lake, calls = stage2

    async def failing(record):
        return w._error_record(record["url"], record["url_hash"], 503, "upstream unavailable")

    w._analyze_url = failing
    real_write = lake.write

    def errors_table_down(table, data, *a, **k):
        if table == sw.TABLE_STAGE2_ERRORS:
            return False
        return real_write(table, data, *a, **k)

    monkeypatch.setattr(lake, "write", errors_table_down)
    with caplog.at_level(logging.INFO):
        await _run(w)
    assert all(s == ["pending"] for s in _status(lake).values())  # errors are never acked
    assert "Quarantined" not in caplog.text, "a failed quarantine write was logged as success"
