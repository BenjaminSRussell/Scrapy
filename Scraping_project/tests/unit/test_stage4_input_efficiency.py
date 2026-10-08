"""#318: the Stage 4 fallback never materializes all of stage2_page_analysis. #320: Stage 4 reuses Stage 2 text instead of re-fetching."""

import asyncio

import pytest
from prometheus_client import REGISTRY

from src.stage4 import stage4_worker as s4
from src.stage4.stage4_worker import Stage4Worker
from src.utils.delta import DeltaHelper


class FakeProcessor:
    def __init__(self):
        self.fetched = []

    def _fetch_content(self, url, is_pdf=False):
        self.fetched.append(url)
        return "text", "html"

    def process_large_document(self, url, text):
        return f"summary of {url}"


def _worker(tmp_path, analysis_fallback=True):
    w = Stage4Worker.__new__(Stage4Worker)
    w.delta = DeltaHelper(base_path=tmp_path / "lake")
    w.processor = FakeProcessor()
    w.analysis_fallback = analysis_fallback
    return w


def _analysis(delta, n_small=500):
    rows = [{"url": f"https://ex.com/small-{i}", "is_massive_doc": False, "has_error": False, "content": "x" * 50} for i in range(n_small)]
    rows += [
        {"url": "https://ex.com/big-1", "is_massive_doc": True, "has_error": False, "content": "y"},
        {"url": "https://ex.com/big-err", "is_massive_doc": True, "has_error": True, "content": "z"},
    ]
    delta.write(s4.ANALYSIS_TABLE, rows, mode="append", async_write=False)


def _rows_read(mode):
    return REGISTRY.get_sample_value("stage4_analysis_rows_read_total", {"mode": mode}) or 0.0


def _selected(source):
    return REGISTRY.get_sample_value("stage4_docs_selected_total", {"source": source}) or 0.0


def test_fallback_reads_only_massive_rows(tmp_path, monkeypatch):
    w = _worker(tmp_path)
    _analysis(w.delta)
    seen_filters = []
    real_read = w.delta.manager.read

    def spy(table, filters=None, columns=None, version=None):
        rows = real_read(table, filters=filters, columns=columns, version=version)
        if table == s4.ANALYSIS_TABLE:
            seen_filters.append(filters)
            assert len(rows) == 2, "full Stage 2 table materialized"
        return rows

    monkeypatch.setattr(w.delta.manager, "read", spy)
    before_f, before_full = _rows_read("filtered"), _rows_read("full_scan")
    before_sel = _selected("analysis_fallback")
    assert asyncio.run(w._run_traced()) == 1
    assert w.processor.fetched == ["https://ex.com/big-1"]
    assert seen_filters == [s4.MASSIVE_DOC_FILTER]
    assert _rows_read("filtered") == before_f + 2
    assert _rows_read("full_scan") == before_full
    assert _selected("analysis_fallback") == before_sel + 1


def test_unfilterable_table_falls_back_to_full_scan(tmp_path):
    w = _worker(tmp_path)
    # Old-shape table without is_massive_doc: the pushed-down filter can't bind.
    w.delta.write(s4.ANALYSIS_TABLE, [{"url": "https://ex.com/legacy", "has_error": False}], mode="append", async_write=False)
    before = _rows_read("full_scan")
    assert w._fallback_from_analysis(set()) == []
    assert _rows_read("full_scan") == before + 1


def test_fallback_can_be_disabled(tmp_path, monkeypatch):
    w = _worker(tmp_path, analysis_fallback=False)
    _analysis(w.delta, n_small=3)
    monkeypatch.setattr(w.delta.manager, "read", lambda *a, **k: pytest.fail("Stage 2 read with fallback disabled"))
    assert w._fallback_from_analysis(set()) == []


def test_summary_dedup_reads_only_url_column(tmp_path, monkeypatch):
    w = _worker(tmp_path)
    w.delta.write(s4.SUMMARY_TABLE, [{"url": "https://ex.com/big-1", "summary": "s" * 1000}], mode="append", async_write=False)
    _analysis(w.delta, n_small=0)
    calls = []
    real = w.delta.read

    def spy(table, filters=None, columns=None):
        calls.append((table, columns))
        return real(table, filters=filters, columns=columns)

    monkeypatch.setattr(w.delta, "read", spy)
    assert asyncio.run(w._run_traced()) == 0  # big-1 already summarized
    assert (s4.SUMMARY_TABLE, ["url"]) in calls


def test_config_flag_is_read(monkeypatch):
    class _Cfg:
        def get(self, key, default=None):
            return False if key == "stage4.analysis_fallback" else default

    monkeypatch.setattr(s4, "get_config", lambda: _Cfg())
    monkeypatch.setattr(s4, "get_delta", lambda: None)
    monkeypatch.setattr(s4, "LargeDocProcessor", lambda model_name: None)
    assert Stage4Worker().analysis_fallback is False


# --- #320: reuse Stage 2 text instead of re-fetching ------------------------


def _source(source):
    return REGISTRY.get_sample_value("stage4_content_source_total", {"source": source}) or 0.0


def _queue_row(delta, url, **extra):
    row = {"url": url, "url_hash": f"h-{url}", "word_count": 1, "content_length": 0, "status": "pending", **extra}
    delta.write(s4.QUEUE_TABLE, [row], mode="append", async_write=False)


def test_html_doc_with_complete_stage2_text_is_not_refetched(tmp_path):
    w = _worker(tmp_path, analysis_fallback=False)
    body = "word " * 5000
    _queue_row(w.delta, "https://uconn.edu/big", text_content=body, content_length=len(body), content_type="html")
    before_reuse, before_fetch = _source("stage2_text"), _source("fetch")
    assert asyncio.run(w._run_traced()) == 1
    assert w.processor.fetched == []  # no network
    summary = w.delta.read(s4.SUMMARY_TABLE)[0]
    assert summary["original_size"] == len(body)
    assert _source("stage2_text") == before_reuse + 1 and _source("fetch") == before_fetch


def test_truncated_stage2_text_is_refetched(tmp_path):
    w = _worker(tmp_path, analysis_fallback=False)
    _queue_row(w.delta, "https://uconn.edu/cut", text_content="x" * 10000, content_length=250000)
    before = _source("fetch")
    asyncio.run(w._run_traced())
    assert w.processor.fetched == ["https://uconn.edu/cut"]
    assert _source("fetch") == before + 1


def test_pdf_still_fetches_even_with_text(tmp_path):
    w = _worker(tmp_path, analysis_fallback=False)
    _queue_row(w.delta, "https://uconn.edu/a.pdf", is_pdf=True, text_content="stale", content_length=5)
    asyncio.run(w._run_traced())
    assert w.processor.fetched == ["https://uconn.edu/a.pdf"]


def test_legacy_queue_row_without_text_fetches(tmp_path):
    w = _worker(tmp_path, analysis_fallback=False)
    _queue_row(w.delta, "https://uconn.edu/old")
    asyncio.run(w._run_traced())
    assert w.processor.fetched == ["https://uconn.edu/old"]


def _stage2():
    from src.stage2.stage2_worker import Stage2Worker

    w = Stage2Worker.__new__(Stage2Worker)
    written = []

    class _Delta:
        def write(self, table, rows, mode="append", async_write=True):
            written.append((table, rows))
            return True

    w.delta = _Delta()
    return w, written


def test_stage2_routes_full_text_to_stage4_queue(monkeypatch):
    monkeypatch.delenv("STAGE4_INLINE_TEXT_MAX_CHARS", raising=False)
    w, written = _stage2()
    text = "t" * 60000
    asyncio.run(w._route_to_stage4("https://uconn.edu/big", "h", text, 1, len(text)))
    (table, rows), = written
    assert table == "stage4_large_docs"
    assert rows[0]["text_content"] == text and rows[0]["content_type"] == "html"


def test_stage2_inline_text_cap(monkeypatch):
    monkeypatch.setenv("STAGE4_INLINE_TEXT_MAX_CHARS", "1000")
    w, written = _stage2()
    asyncio.run(w._route_to_stage4("https://uconn.edu/huge", "h", "t" * 5000, 1, 5000))
    assert written[0][1][0]["text_content"] == ""  # Stage 4 will fetch
