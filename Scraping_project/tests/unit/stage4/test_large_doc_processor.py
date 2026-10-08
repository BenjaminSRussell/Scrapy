"""src/stage4/large_doc_processor.py: chunking, summarisation wiring and queue status (#224).

Offline: no BART, no HTTP, no Delta. The summarizer is a recording stub.
"""

from __future__ import annotations

import pytest

from src.stage4 import large_doc_processor as ldp
from src.stage4.large_doc_processor import LargeDocProcessor, chunk_spans

pytestmark = [pytest.mark.unit, pytest.mark.stage4]

SENT = "The quick brown fox jumps over the lazy dog near the river bank. "  # 64 chars


class StubSummarizer:
    def __init__(self, fail_on=None):
        self.calls: list[tuple[str, dict]] = []
        self.fail_on = fail_on

    def __call__(self, text, **kw):
        self.calls.append((text, kw))
        if self.fail_on is not None and self.fail_on(text):
            raise RuntimeError("model blew up")
        return [{"summary_text": f"S{len(self.calls)}"}]


class FakeLake:
    def __init__(self, docs=None):
        self.docs = docs or []
        self.writes: list[tuple[str, list, dict]] = []

    def read(self, table):
        assert table == "stage4_large_docs"
        return [dict(d) for d in self.docs]

    def write(self, table, rows, **kw):
        self.writes.append((table, rows, kw))


def proc(summarizer=None, lake=None) -> LargeDocProcessor:
    p = LargeDocProcessor.__new__(LargeDocProcessor)  # skip get_delta()/httpx/model load
    p.delta = lake or FakeLake()
    p.model_name = "stub-model"
    p.summarizer = summarizer or StubSummarizer()
    p.CHUNK_SIZE, p.OVERLAP = 5000, 500
    return p


# --- chunking: boundaries, overlap, short docs --------------------------------


@pytest.mark.parametrize("n", [0, 1, 99, 5000])
def test_short_doc_is_a_single_chunk(n):
    text = "x" * n
    assert proc()._split_into_chunks(text) == [text]


def test_one_char_over_the_limit_makes_two_overlapping_chunks():
    text = "y" * 5001
    chunks = proc()._split_into_chunks(text)
    assert [len(c) for c in chunks] == [5000, 501]  # second chunk re-reads the 500-char overlap
    assert chunks[0][-500:] == chunks[1][:500]


def test_overlap_is_exact_between_adjacent_chunks_and_covers_the_text():
    text = "".join(chr(65 + i % 26) for i in range(12_345))
    spans = chunk_spans(text, 5000, 500)
    assert spans[0][0] == 0 and spans[-1][1] == len(text)
    for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
        assert a1 - b0 == 500 and b1 > a1
    assert all(b - a <= 5000 for a, b in spans)


def test_chunks_prefer_sentence_ends():
    text = SENT * 200  # 12.8k chars, a period every 64
    chunks = proc()._split_into_chunks(text)
    assert all(c.endswith(".") for c in chunks[:-1])


def test_whitespace_only_chunks_are_dropped():
    text = "a" * 4600 + " " * 5000 + "b" * 100
    chunks = proc()._split_into_chunks(text)
    assert chunks and all(c.strip() for c in chunks)


# --- _summarize_chunk ------------------------------------------------------------


def test_tiny_or_empty_chunks_are_not_sent_to_the_model():
    s = StubSummarizer()
    p = proc(s)
    assert p._summarize_chunk("") is None and p._summarize_chunk("x" * 99) is None
    assert s.calls == []


def test_whole_chunk_reaches_the_model_with_tokenizer_truncation():
    """The old 1024-char cut summarised only ~20% of each 5000-char chunk."""
    s = StubSummarizer()
    chunk = SENT * 70  # 4480 chars
    assert proc(s)._summarize_chunk(chunk) == "S1"
    text, kw = s.calls[0]
    assert text == chunk
    assert kw == {"max_length": 150, "min_length": 30, "do_sample": False, "truncation": True}


def test_model_failure_falls_back_to_leading_sentences():
    p = proc(StubSummarizer(fail_on=lambda t: True))
    out = p._summarize_chunk("One two. Three four. Five six. Seven eight. " * 5)
    assert out == "One two.  Three four.  Five six."


# --- _process_document / process_large_document -------------------------------------


def test_process_document_happy_path(monkeypatch):
    s = StubSummarizer()
    p = proc(s)
    text = SENT * 200
    monkeypatch.setattr(p, "_fetch_content", lambda url, is_pdf=False: (text, "html"))
    row = p._process_document({"url": "https://u.edu/big", "is_pdf": False})
    n_chunks = len(p._split_into_chunks(text))
    assert len(s.calls) == n_chunks  # combined summary is short: no refine pass
    assert row["url"] == "https://u.edu/big" and row["chunk_count"] == n_chunks
    assert row["original_word_count"] == len(text.split())
    assert row["model_used"] == "stub-model"
    assert row["summary"] == " ".join(f"S{i}" for i in range(1, n_chunks + 1))


def test_long_combined_summary_gets_a_refine_pass(monkeypatch):
    class Wordy(StubSummarizer):
        def __call__(self, text, **kw):
            super().__call__(text, **kw)
            return [{"summary_text": "z" * 400}]

    s = Wordy()
    p = proc(s)
    monkeypatch.setattr(p, "_fetch_content", lambda url, is_pdf=False: (SENT * 200, "txt"))
    row = p._process_document({"url": "https://u.edu/big"})
    assert len(s.calls) == row["chunk_count"] + 1
    assert len(s.calls[-1][0]) <= 5000  # refine input is capped


@pytest.mark.parametrize("doc", [{}, {"url": None}, {"url": 42}])
def test_document_without_url_is_skipped(doc):
    assert proc()._process_document(doc) is None


def test_fetch_failure_or_empty_text_yields_none(monkeypatch):
    p = proc()

    def boom(url, is_pdf=False):
        raise OSError("down")

    monkeypatch.setattr(p, "_fetch_content", boom)
    assert p._process_document({"url": "https://u.edu/a"}) is None
    monkeypatch.setattr(p, "_fetch_content", lambda url, is_pdf=False: ("", "unknown"))
    assert p._process_document({"url": "https://u.edu/a"}) is None


def test_process_large_document_falls_back_to_a_prefix_when_nothing_summarises():
    p = proc(StubSummarizer())
    assert p.process_large_document("u", "short text") == "short text"  # every chunk < 100 chars
    long = "w" * 600
    p.summarizer = StubSummarizer(fail_on=lambda t: True)
    out = p.process_large_document("u", long)
    assert out  # fallback sentences, never an exception


# --- queue status ------------------------------------------------------------------


def test_process_queue_marks_only_summarised_docs_completed(monkeypatch):
    docs = [
        {"url": "https://u.edu/ok", "status": "pending"},
        {"url": "https://u.edu/broken", "status": "pending"},
        {"url": "https://u.edu/old", "status": "completed"},
    ]
    lake = FakeLake(docs)
    p = proc(lake=lake)
    monkeypatch.setattr(p, "_load_model", lambda: None)
    monkeypatch.setattr(
        p, "_process_document",
        lambda d: {"url": d["url"], "summary": "s"} if d["url"].endswith("ok") else None,
    )
    p.process_queue()
    (t1, summaries, kw1), (t2, queue, kw2) = lake.writes
    assert t1 == "stage4_summaries" and [r["url"] for r in summaries] == ["https://u.edu/ok"]
    assert kw1 == {"mode": "append", "async_write": False}
    assert t2 == "stage4_large_docs" and kw2 == {"mode": "overwrite", "async_write": False}
    status = {r["url"]: r["status"] for r in queue}
    assert status == {"https://u.edu/ok": "completed", "https://u.edu/broken": "failed", "https://u.edu/old": "completed"}
    assert all("completed_at" in r for r in queue if r["url"] != "https://u.edu/old")


def test_process_queue_exception_in_one_doc_does_not_stop_the_rest(monkeypatch):
    lake = FakeLake([{"url": "a", "status": "pending"}, {"url": "b", "status": "pending"}])
    p = proc(lake=lake)
    monkeypatch.setattr(p, "_load_model", lambda: None)

    def process(d):
        if d["url"] == "a":
            raise RuntimeError("bad doc")
        return {"url": "b", "summary": "s"}

    monkeypatch.setattr(p, "_process_document", process)
    p.process_queue()
    queue = lake.writes[-1][1]
    assert {r["url"]: r["status"] for r in queue} == {"a": "failed", "b": "completed"}


def test_empty_queue_loads_no_model(monkeypatch):
    p = proc(lake=FakeLake([{"url": "x", "status": "completed"}]))
    monkeypatch.setattr(p, "_load_model", lambda: pytest.fail("model must not load for an empty queue"))
    p.process_queue()
    assert p.delta.writes == []


def test_legacy_two_argument_update_still_works():
    lake = FakeLake()
    p = proc(lake=lake)
    p._update_queue_status([{"url": "a", "status": "pending"}], [{"url": "a"}])
    assert lake.writes[0][1][0]["status"] == "completed"


def test_html_extraction_strips_boilerplate():
    html = "<html><nav>menu</nav><script>x()</script><p>Hello   <b>world</b></p><footer>f</footer></html>"
    assert proc()._extract_html_text(html) == "Hello world"


def test_module_has_no_import_time_model_or_network():
    assert not hasattr(ldp, "pipeline")
