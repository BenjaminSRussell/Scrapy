"""#611: Stage 4 consumes the stage4_large_docs queue and marks rows done."""

import asyncio
from datetime import datetime

import pytest

from src.stage4.stage4_worker import QUEUE_TABLE, SUMMARY_TABLE, Stage4Worker
from src.utils.delta import DeltaHelper


class FakeProcessor:
    def __init__(self, texts=None, summaries=None, raise_for=()):
        self.texts = texts or {}
        self.summaries = summaries or {}
        self.raise_for = set(raise_for)
        self.fetched: list[tuple[str, bool]] = []

    def _fetch_content(self, url, is_pdf=False):
        self.fetched.append((url, is_pdf))
        if url in self.raise_for:
            raise ConnectionError("boom")
        return self.texts.get(url, ""), ("pdf" if is_pdf else "html")

    def process_large_document(self, url, text):
        return self.summaries.get(url, "")


def _worker(tmp_path, processor):
    w = Stage4Worker.__new__(Stage4Worker)
    w.delta = DeltaHelper(base_path=tmp_path / "lake")
    w.processor = processor
    return w


def _queue(delta, url, **extra):
    row = {
        "url": url, "url_hash": f"h-{url}", "word_count": 60000, "content_length": 1,
        "status": "pending", "queued_at": datetime.now().isoformat(), **extra,
    }
    delta.write(QUEUE_TABLE, [row], mode="append", async_write=False)


def _statuses(delta):
    return {r["url"]: r["status"] for r in delta.read(QUEUE_TABLE)}


def test_queue_is_primary_input_and_rows_are_marked(tmp_path):
    proc = FakeProcessor(
        texts={"a": "x" * 100, "pdf": "y" * 100, "empty": "", "nosum": "z" * 50},
        summaries={"a": "sum-a", "pdf": "sum-pdf"},
    )
    w = _worker(tmp_path, proc)
    _queue(w.delta, "a")
    _queue(w.delta, "pdf", is_pdf=True)
    _queue(w.delta, "empty")
    _queue(w.delta, "nosum")

    assert asyncio.run(w._run_traced()) == 2

    assert ("pdf", True) in proc.fetched and ("a", False) in proc.fetched
    assert _statuses(w.delta) == {
        "a": "completed", "pdf": "completed",
        "empty": "skipped:no_text", "nosum": "skipped:no_summary",
    }
    summaries = {r["url"]: r for r in w.delta.read(SUMMARY_TABLE)}
    assert set(summaries) == {"a", "pdf"}
    assert summaries["pdf"]["is_pdf"] is True

    # Second run: nothing pending, nothing re-fetched.
    proc.fetched.clear()
    assert asyncio.run(w._run_traced()) == 0
    assert proc.fetched == []


def test_transient_failure_stays_pending_and_retries(tmp_path):
    proc = FakeProcessor(texts={"a": "x" * 10}, summaries={"a": "s"}, raise_for={"a"})
    w = _worker(tmp_path, proc)
    _queue(w.delta, "a")
    assert asyncio.run(w._run_traced()) == 0
    assert _statuses(w.delta) == {"a": "pending"}

    proc.raise_for.clear()
    assert asyncio.run(w._run_traced()) == 1
    assert _statuses(w.delta) == {"a": "completed"}


def test_duplicate_queue_rows_processed_once(tmp_path):
    proc = FakeProcessor(texts={"a": "x" * 10}, summaries={"a": "s"})
    w = _worker(tmp_path, proc)
    _queue(w.delta, "a")
    _queue(w.delta, "a")
    assert asyncio.run(w._run_traced()) == 1
    assert proc.fetched == [("a", False)]
    assert set(_statuses(w.delta).values()) == {"completed"}


def test_analysis_table_is_fallback_for_unqueued_docs_only(tmp_path):
    proc = FakeProcessor(texts={"q": "x", "legacy.pdf": "y"}, summaries={"q": "s", "legacy.pdf": "t"})
    w = _worker(tmp_path, proc)
    _queue(w.delta, "q")
    w.delta.write(
        "stage2_page_analysis",
        [
            {"url": "q", "is_massive_doc": True, "has_error": False},
            {"url": "legacy.pdf", "is_massive_doc": True, "has_error": False},
            {"url": "small", "is_massive_doc": False, "has_error": False},
            {"url": "broken", "is_massive_doc": True, "has_error": True},
        ],
        mode="append", async_write=False,
    )
    assert asyncio.run(w._run_traced()) == 2
    assert sorted(proc.fetched) == [("legacy.pdf", True), ("q", False)]
    assert _statuses(w.delta) == {"q": "completed"}  # fallback docs never enter the queue

    proc.fetched.clear()
    assert asyncio.run(w._run_traced()) == 0  # legacy deduped via summaries table
    assert proc.fetched == []


def test_summary_write_failure_leaves_rows_pending(tmp_path, monkeypatch):
    proc = FakeProcessor(texts={"a": "x"}, summaries={"a": "s"})
    w = _worker(tmp_path, proc)
    _queue(w.delta, "a")
    real_write = w.delta.write

    def failing_write(table, *a, **k):
        if table == SUMMARY_TABLE:
            raise OSError("disk full")
        return real_write(table, *a, **k)

    monkeypatch.setattr(w.delta, "write", failing_write)
    assert asyncio.run(w._run_traced()) == 0
    assert _statuses(w.delta) == {"a": "pending"}


@pytest.mark.parametrize("status", ["completed", "skipped:no_text"])
def test_settled_rows_are_not_reprocessed(tmp_path, status):
    proc = FakeProcessor(texts={"a": "x"}, summaries={"a": "s"})
    w = _worker(tmp_path, proc)
    _queue(w.delta, "a", status=status)
    assert asyncio.run(w._run_traced()) == 0
    assert proc.fetched == []
