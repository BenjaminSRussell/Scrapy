"""Pipeline Delta batches stay bounded (#424) and flush on a timer (#425)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import tracemalloc
from pathlib import Path
from types import SimpleNamespace

import pytest
from prometheus_client import REGISTRY

from src import pipelines as p
from src.items import OffsiteCandidateItem

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FakeDelta:
    def __init__(self, result=True):
        self.result = result
        self.writes: list[tuple[str, list]] = []

    def write(self, table, rows, mode="append", async_write=True):
        self.writes.append((table, list(rows)))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def rows(self, table=None):
        return [r for t, batch in self.writes if table in (None, t) for r in batch]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _metric(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


# --- BufferedDeltaBatch ------------------------------------------------------


def test_flushes_at_row_limit():
    delta = FakeDelta()
    b = p.BufferedDeltaBatch(delta, "t_rows", max_rows=3, max_age=0)
    assert [b.add({"i": i}) for i in range(3)] == [False, False, True]
    assert delta.rows() == [{"i": 0}, {"i": 1}, {"i": 2}] and len(b) == 0


def test_flushes_at_byte_limit():
    delta = FakeDelta()
    b = p.BufferedDeltaBatch(delta, "t_bytes", max_rows=1000, max_bytes=1000, max_age=0)
    big = {"context": "x" * 600}
    assert b.add(big) is False
    assert b.add(big) is True  # ~1.2 KB > 1000 bytes
    assert len(delta.writes) == 1 and len(b) == 0


def test_flushes_by_age_on_add_and_on_timer():
    delta, clock = FakeDelta(), Clock()
    b = p.BufferedDeltaBatch(delta, "t_age", max_rows=1000, max_age=5, clock=clock)
    b.add({"i": 1})
    clock.now += 4
    assert b.flush_if_due() is False
    clock.now += 1
    before = _metric("pipeline_batch_flushes_total", table="t_age", trigger="timer", outcome="ok")
    assert b.flush_if_due() is True
    assert _metric("pipeline_batch_flushes_total", table="t_age", trigger="timer", outcome="ok") == before + 1
    # Age is measured from the oldest unflushed row.
    b.add({"i": 2})
    clock.now += 5
    assert b.add({"i": 3}) is True
    assert delta.rows() == [{"i": 1}, {"i": 2}, {"i": 3}]


@pytest.mark.parametrize("result", [False, RuntimeError("lake down")])
def test_rejected_write_clears_batch_and_counts_rows(result):
    delta = FakeDelta(result=result)
    b = p.BufferedDeltaBatch(delta, "t_fail", max_rows=2, max_age=0)
    before = _metric("pipeline_batch_rows_unwritten_total", table="t_fail")
    b.add({"i": 1})
    assert b.add({"i": 2}) is False
    assert len(b) == 0 and b.rows_unwritten == 2 and b.rows_written == 0
    assert _metric("pipeline_batch_rows_unwritten_total", table="t_fail") == before + 2


def test_list_compatibility():
    b = p.BufferedDeltaBatch(FakeDelta(), "t_compat", max_rows=10)
    assert b == [] and not b
    b.add({"a": 1})
    assert b == [{"a": 1}] and list(b) == [{"a": 1}] and len(b) == 1
    b.clear()
    assert b == []


# --- OffsiteCandidatePipeline (#424) ----------------------------------------


def _offsite(i, context="ctx"):
    return OffsiteCandidateItem(
        source_page=f"https://uconn.edu/p{i % 50}",
        external_url=f"https://example{i}.org/x",
        anchor_text=f"link {i}",
        context=context,
        discovered_at="2026-10-08T00:00:00Z",
    )


def _offsite_pipeline(delta, **opts):
    pipe = p.OffsiteCandidatePipeline(opts or None)
    pipe.delta = delta
    return pipe


def test_100k_offsite_candidates_stay_bounded_and_lose_nothing():
    delta = FakeDelta()
    pipe = _offsite_pipeline(delta)
    spider = SimpleNamespace(name="scout")
    for i in range(100_000):
        pipe.process_item(_offsite(i), spider)
    pipe.spider_closed(spider)

    assert pipe.batch.peak_rows <= p.OffsiteCandidatePipeline.BATCH_SIZE
    written = delta.rows("stage1_offsite_candidates")
    # Same rows, same order as one big flush at close would have written.
    assert written == [dict(_offsite(i)) for i in range(100_000)]


class RejectingDelta:
    """Lake that accepts nothing and retains nothing (so only the pipeline can leak)."""

    def write(self, table, rows, mode="append", async_write=True):
        return False


def test_failing_lake_does_not_grow_memory(monkeypatch):
    # Keep log records out of the measurement (pytest's log capture keeps them).
    monkeypatch.setattr(p.logger, "disabled", True)
    pipe = _offsite_pipeline(RejectingDelta())
    spider = SimpleNamespace(name="scout")
    noisy = "y" * 2000  # noisy page: large context strings
    tracemalloc.start()
    try:
        for i in range(2_000):
            pipe.process_item(_offsite(i, noisy), spider)
        baseline = tracemalloc.get_traced_memory()[0]
        for i in range(2_000, 20_000):
            pipe.process_item(_offsite(i, noisy), spider)
        current = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()
    assert pipe.batch.peak_rows <= 100
    # 18k more noisy rows (~36 MB if retained) add well under 2 MB.
    assert current - baseline < 2 * 1024 * 1024
    assert pipe.batch.rows_unwritten >= 19_900


def test_noisy_rows_flush_on_bytes():
    delta = FakeDelta()
    pipe = _offsite_pipeline(delta, max_rows=100, max_bytes=50_000, max_age=0)
    spider = SimpleNamespace(name="scout")
    for i in range(30):
        pipe.process_item(_offsite(i, "z" * 10_000), spider)
    assert pipe.batch.peak_rows <= 6
    pipe.spider_closed(spider)
    assert len(delta.rows()) == 30


def test_saved_metric_counts_only_accepted_rows():
    spider = SimpleNamespace(name="offsite_metric_test")
    labels = {"spider": "offsite_metric_test"}
    before = _metric("scrapy_offsite_candidates_saved_total", **labels)
    ok = _offsite_pipeline(FakeDelta(), max_rows=10, max_age=0)
    for i in range(25):
        ok.process_item(_offsite(i), spider)
    ok.spider_closed(spider)
    assert _metric("scrapy_offsite_candidates_saved_total", **labels) == before + 25

    bad = _offsite_pipeline(FakeDelta(result=False), max_rows=10, max_age=0)
    for i in range(25):
        bad.process_item(_offsite(i), spider)
    bad.spider_closed(spider)
    assert _metric("scrapy_offsite_candidates_saved_total", **labels) == before + 25


def test_settings_wired_through_from_crawler():
    from scrapy.settings import Settings

    crawler = SimpleNamespace(
        settings=Settings(
            {
                "OFFSITE_BATCH_SIZE": 7,
                "OFFSITE_BATCH_MAX_BYTES": 1234,
                "OFFSITE_FLUSH_INTERVAL": 2.5,
                "QUEUE_BATCH_SIZE": 9,
                "QUEUE_FLUSH_INTERVAL": 0,
            }
        ),
        signals=SimpleNamespace(connect=lambda *a, **k: None),
    )
    off = p.OffsiteCandidatePipeline.from_crawler(crawler)
    assert (off.batch.max_rows, off.batch.max_bytes, off.batch.max_age) == (7, 1234, 2.5)
    q = p.QueueItemPipeline.from_crawler(crawler)
    assert q.stage2_queue_batch.max_rows == 9 and q.js_queue_batch.max_age == 0


# --- QueueItemPipeline (#425) -----------------------------------------------


def test_queue_pipeline_timer_flushes_idle_batches():
    delta, clock = FakeDelta(), Clock()
    q = p.QueueItemPipeline({"max_rows": 100, "max_age": 10})
    q.delta = delta
    for b in (q.js_queue_batch, q.stage2_queue_batch):
        b.clock = clock
    spider = SimpleNamespace(name="scout")
    q.process_item({"url": "https://uconn.edu/a", "target_stage": "stage2"}, spider)
    q.process_item({"url": "https://uconn.edu/b", "target_spider": "javascript"}, spider)
    q._flush_due()
    assert delta.writes == []
    clock.now += 10
    q._flush_due()
    assert {t for t, _ in delta.writes} == {"stage2_queue", "js_spider_queue"}
    assert q.stage2_queue_batch == [] and q.js_queue_batch == []


def test_swapping_delta_after_construction_reaches_batches():
    q = p.QueueItemPipeline()
    new = FakeDelta()
    q.delta = new
    assert q.js_queue_batch.delta is new and q.stage2_queue_batch.delta is new


KILL_SCRIPT = textwrap.dedent(
    """
    import os, signal, sys
    from twisted.internet import reactor
    from src import pipelines as p
    from src.utils.delta import DeltaHelper

    interval = float(sys.argv[1])
    q = p.QueueItemPipeline({"max_rows": 100, "max_age": interval})
    q.delta = DeltaHelper(sys.argv[2])
    spider = type("S", (), {"name": "scout"})()
    q.spider_opened(spider)

    def enqueue():
        for i in range(5):
            q.process_item({"url": f"https://uconn.edu/k{i}", "url_hash": f"k{i}", "target_stage": "stage2"}, spider)

    reactor.callWhenRunning(enqueue)
    # Hard kill (OOM / SIGKILL): no spider_closed, no atexit, no flush.
    reactor.callLater(float(sys.argv[3]), os.kill, os.getpid(), signal.SIGKILL)
    reactor.run()
    """
)


def _killed_rows(tmp_path, interval, kill_after):
    lake = tmp_path / f"lake_{interval}"
    env = {**os.environ, "DELTA_LAKE_PATH": str(lake)}
    out = subprocess.run(
        [sys.executable, "-c", KILL_SCRIPT, str(interval), str(lake), str(kill_after)],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == -9, out.stderr[-2000:]
    table = lake / "stage2_queue"
    if not (table / "_delta_log").exists():
        return []
    from deltalake import DeltaTable

    # Rows are identified by url: QueueItemPipeline re-derives url_hash from the canonical url (#728).
    return sorted(DeltaTable(str(table)).to_pyarrow_table(columns=["url"]).column("url").to_pylist())


def test_kill_9_after_timer_flush_keeps_rows(tmp_path):
    assert _killed_rows(tmp_path, interval=0.3, kill_after=4.0) == [f"https://uconn.edu/k{i}" for i in range(5)]


def test_kill_9_without_timer_loses_the_batch(tmp_path):
    """Control: the pre-#425 behaviour (flush only at 100 rows / close)."""
    assert _killed_rows(tmp_path, interval=0, kill_after=2.0) == []
