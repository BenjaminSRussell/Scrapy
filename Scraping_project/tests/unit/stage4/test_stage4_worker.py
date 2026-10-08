"""Stage4Worker orchestration (#228): call order, dependency failures, no model download.

Queue selection/marking is covered in tests/unit/test_stage4_queue_input.py and
the PDF quarantine path in tests/unit/test_stage4_pdf_sandbox.py; this suite
pins how the worker wires fetch -> summarize -> write -> ack together.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.stage4 import stage4_worker as mod
from src.stage4.stage4_worker import QUEUE_TABLE, SUMMARY_TABLE, Stage4Worker
from src.utils import graceful_shutdown as gs

pytestmark = [pytest.mark.unit, pytest.mark.stage4]


class Recorder:
    """Shared call log so the test can assert cross-dependency ordering."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []


class FakeProcessor:
    def __init__(self, rec: Recorder, texts: dict[str, str], summaries: dict[str, str],
                 summarize_raises: set[str] | None = None):
        self.rec, self.texts, self.summaries = rec, texts, summaries
        self.summarize_raises = summarize_raises or set()

    def _fetch_content(self, url: str, is_pdf: bool = False):
        self.rec.calls.append(("fetch", url))
        return self.texts.get(url, ""), ("pdf" if is_pdf else "html")

    def process_large_document(self, url: str, text: str) -> str:
        self.rec.calls.append(("summarize", url))
        if url in self.summarize_raises:
            raise RuntimeError("model crashed")
        return self.summaries.get(url, "")


class FakeDelta:
    def __init__(self, rec: Recorder, queue: list[dict], *, write_ok: bool = True, merge_result: int = 1):
        self.rec = rec
        self.tables: dict[str, list[dict]] = {QUEUE_TABLE: queue, SUMMARY_TABLE: []}
        self.write_ok = write_ok
        self.merge_result = merge_result

    def read(self, table: str, columns: list[str] | None = None, **_kw):
        rows = self.tables.get(table)
        if rows is None:
            raise FileNotFoundError(table)
        return [dict(r) for r in rows]

    def write(self, table: str, rows: list[dict], **kw):
        self.rec.calls.append(("write", table))
        if not self.write_ok:
            raise OSError("disk full")
        self.tables.setdefault(table, []).extend(rows)
        return True

    def merge_into(self, table: str, rows: list[dict], key: str, columns: list[str]) -> int:
        self.rec.calls.append(("merge", tuple(sorted((r["url"], r["status"]) for r in rows))))
        if self.merge_result >= 0:
            by_url = {r["url"]: r for r in rows}
            for row in self.tables[table]:
                if row["url"] in by_url:
                    row["status"] = by_url[row["url"]]["status"]
        return self.merge_result


def _row(url: str, **extra: Any) -> dict[str, Any]:
    return {"url": url, "url_hash": f"h-{url}", "status": "pending",
            "queued_at": datetime.now().isoformat(), **extra}


def _worker(rec: Recorder, processor: FakeProcessor, delta: FakeDelta) -> Stage4Worker:
    w = Stage4Worker.__new__(Stage4Worker)
    w.delta = delta
    w.processor = processor
    w.analysis_fallback = False
    return w


@pytest.fixture(autouse=True)
def _clean_shutdown():
    gs.reset_for_tests()
    yield
    gs.reset_for_tests()


def test_happy_path_order_and_row_shape():
    rec = Recorder()
    proc = FakeProcessor(rec, texts={"https://a/doc": "x" * 1000}, summaries={"https://a/doc": "s" * 50})
    delta = FakeDelta(rec, [_row("https://a/doc")])
    w = _worker(rec, proc, delta)

    assert asyncio.run(w._run_traced()) == 1

    kinds = [c[0] for c in rec.calls]
    assert kinds == ["fetch", "summarize", "write", "merge"]  # ack only after the durable write
    (row,) = delta.tables[SUMMARY_TABLE]
    assert row["url"] == "https://a/doc" and row["url_hash"] == "h-https://a/doc"
    assert row["original_size"] == 1000 and row["summary_size"] == 50
    assert row["compression_ratio"] == 0.05
    assert row["is_pdf"] is False and row["content_type"] == "html"
    assert delta.tables[QUEUE_TABLE][0]["status"] == "completed"


def test_pdf_by_extension_is_fetched_as_pdf():
    rec = Recorder()
    url = "https://a/report.PDF?download=1"
    proc = FakeProcessor(rec, texts={url: "y" * 200}, summaries={url: "sum"})
    delta = FakeDelta(rec, [_row(url)])
    assert asyncio.run(_worker(rec, proc, delta)._run_traced()) == 1
    assert delta.tables[SUMMARY_TABLE][0]["is_pdf"] is True


def test_summarizer_failure_leaves_row_pending_and_continues():
    rec = Recorder()
    proc = FakeProcessor(
        rec,
        texts={"https://bad/": "x" * 100, "https://good/": "y" * 100},
        summaries={"https://good/": "ok"},
        summarize_raises={"https://bad/"},
    )
    delta = FakeDelta(rec, [_row("https://bad/"), _row("https://good/")])
    assert asyncio.run(_worker(rec, proc, delta)._run_traced()) == 1
    statuses = {r["url"]: r["status"] for r in delta.tables[QUEUE_TABLE]}
    assert statuses == {"https://bad/": "pending", "https://good/": "completed"}


def test_write_failure_acks_nothing():
    rec = Recorder()
    proc = FakeProcessor(rec, texts={"https://a/": "x" * 100}, summaries={"https://a/": "s"})
    delta = FakeDelta(rec, [_row("https://a/")], write_ok=False)
    assert asyncio.run(_worker(rec, proc, delta)._run_traced()) == 0
    assert all(c[0] != "merge" for c in rec.calls)
    assert delta.tables[QUEUE_TABLE][0]["status"] == "pending"


def test_queue_merge_failure_is_logged_not_raised(caplog):
    rec = Recorder()
    proc = FakeProcessor(rec, texts={"https://a/": "x" * 100}, summaries={"https://a/": "s"})
    delta = FakeDelta(rec, [_row("https://a/")], merge_result=-1)
    assert asyncio.run(_worker(rec, proc, delta)._run_traced()) == 1
    assert "stay pending" in caplog.text


def test_skips_are_acked_with_reason():
    rec = Recorder()
    proc = FakeProcessor(rec, texts={"https://nosum/": "x" * 10}, summaries={})
    delta = FakeDelta(rec, [_row("https://empty/"), _row("https://nosum/")])
    assert asyncio.run(_worker(rec, proc, delta)._run_traced()) == 0
    merge = [c for c in rec.calls if c[0] == "merge"]
    assert merge == [("merge", (("https://empty/", "skipped:no_text"), ("https://nosum/", "skipped:no_summary")))]


def test_shutdown_stops_before_next_doc_but_saves_and_acks_finished_work():
    rec = Recorder()

    class StopAfterFirst(FakeProcessor):
        def process_large_document(self, url, text):
            out = super().process_large_document(url, text)
            gs.get_shutdown().request("test")  # SIGTERM lands while doc 1 is summarizing
            return out

    proc = StopAfterFirst(rec, texts={"https://1/": "a" * 10, "https://2/": "b" * 10},
                          summaries={"https://1/": "s1", "https://2/": "s2"})
    delta = FakeDelta(rec, [_row("https://1/"), _row("https://2/")])
    assert asyncio.run(_worker(rec, proc, delta)._run_traced()) == 1
    assert ("fetch", "https://2/") not in rec.calls
    statuses = {r["url"]: r["status"] for r in delta.tables[QUEUE_TABLE]}
    assert statuses == {"https://1/": "completed", "https://2/": "pending"}


def test_constructor_does_not_load_a_model(monkeypatch):
    """No GPU / model download in CI: the transformer is loaded lazily on first summarize."""
    monkeypatch.setattr(mod, "get_delta", lambda: MagicMock(name="delta"))
    monkeypatch.setattr("src.stage4.large_doc_processor.get_delta", lambda: MagicMock(name="delta"))
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: default
    monkeypatch.setattr(mod, "get_config", lambda: cfg)

    import src.stage4.large_doc_processor as ldp

    loaded: list[str] = []
    monkeypatch.setattr(ldp.LargeDocProcessor, "_load_model", lambda self: loaded.append(self.model_name))

    w = Stage4Worker(model_name="tiny/model")
    assert w.processor.model_name == "tiny/model"
    assert w.processor.summarizer is None
    assert loaded == []
    assert w.analysis_fallback is True
