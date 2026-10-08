"""Unit suite for Stage3Worker (#223).

Offline: no models, no network. The summarizer is the extractive sentence-cut
already in ``_summarize_document``; Postgres/OTEL are stubbed.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.core.constants import (
    LEGACY_TABLE_STAGE3_SUMMARIES,
    SUMMARY_LIMITS,
    TABLE_STAGE3_SUMMARIES,
)
from src.stage3.stage3_worker import Stage3Worker
from src.utils import graceful_shutdown as gs

pytestmark = [pytest.mark.unit, pytest.mark.stage3]


def _doc(url: str, text: str, *, url_hash: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "url": url,
        "url_hash": url_hash or f"h-{url}",
        "text_content": text,
        "is_low_quality": False,
        "is_massive_doc": False,
        "has_error": False,
        "keywords": extra.pop("keywords", ["research"]),
        "quality_score": extra.pop("quality_score", 80),
        **extra,
    }


class FakeDelta:
    def __init__(self, tables: dict[str, list] | None = None):
        self.tables: dict[str, list] = {k: list(v) for k, v in (tables or {}).items()}
        self.writes: list[tuple[str, list, dict]] = []

    def read(self, table: str, **_kw):
        if table not in self.tables:
            raise FileNotFoundError(table)
        return list(self.tables[table])

    def write(self, table: str, rows: list, **kw):
        self.writes.append((table, list(rows), kw))
        self.tables.setdefault(table, []).extend(rows)
        return True


@pytest.fixture
def worker(monkeypatch):
    w = Stage3Worker(max_concurrent=2, batch_size=2)
    w.delta = FakeDelta()
    w.postgres = MagicMock(name="postgres")
    monkeypatch.setattr("src.stage3.stage3_worker.init_tracing", lambda **k: None)
    monkeypatch.setattr("src.stage3.stage3_worker.ensure_crawl_job_id", lambda: "job-test")
    # start_span is a context manager; a no-op MagicMock works.
    monkeypatch.setattr("src.stage3.stage3_worker.start_span", MagicMock())
    monkeypatch.setattr("src.stage3.stage3_worker.record_performance", MagicMock())
    monkeypatch.setattr("src.stage3.stage3_worker.record_error", MagicMock())
    gs.reset_for_tests()
    yield w
    gs.reset_for_tests()


_TOPICS = ["chemistry", "basketball", "admissions", "library", "housing", "parking",
           "engineering", "nursing", "music", "athletics", "dining", "finance"]
# Distinct vocabularies so MinHash dedup keeps every one of them.
_TEXTS = [
    " ".join(f"{t}{j}" for j in range(60)) + f". {t.title()} second sentence. Third about {t}."
    for t in _TOPICS
]


def test_empty_stage2_returns_zero(worker):
    worker.delta.tables["stage2_page_analysis"] = []
    assert asyncio.run(worker.run()) == 0
    assert worker.delta.writes == []


def test_filters_low_quality_massive_and_errors(worker):
    worker.delta.tables["stage2_page_analysis"] = [
        _doc("https://ok/", "One. Two. Three. Four. Five. Six."),
        _doc("https://lq/", "x", **{"is_low_quality": True}),
        _doc("https://huge/", "x", **{"is_massive_doc": True}),
        _doc("https://err/", "x", **{"has_error": True}),
        _doc("https://blank/", ""),  # no text_content
    ]
    worker.delta.tables[TABLE_STAGE3_SUMMARIES] = []
    n = asyncio.run(worker.run())
    assert n == 1
    assert worker.delta.writes[0][0] == TABLE_STAGE3_SUMMARIES
    assert worker.delta.writes[0][1][0]["url"] == "https://ok/"


def test_extractive_summary_respects_sentence_limit(worker):
    sentences = [f"Sentence {i}" for i in range(20)]
    text = ". ".join(sentences) + "."
    out = asyncio.run(worker._summarize_document(_doc("https://x/", text)))
    assert out is not None
    limit = SUMMARY_LIMITS["extractive_max_sentences"]
    # summary ends with a period and has at most `limit` sentences.
    body = out["summary"].rstrip(".")
    assert len([s for s in body.split(".") if s.strip()]) <= limit
    assert out["word_count"] == len(text.split())


def test_already_processed_hashes_are_skipped(worker):
    worker.delta.tables["stage2_page_analysis"] = [_doc("https://a/", "Alpha. Beta. Gamma.")]
    worker.delta.tables[TABLE_STAGE3_SUMMARIES] = [{"url_hash": "h-https://a/"}]
    assert asyncio.run(worker.run()) == 0
    assert worker.delta.writes == []


def test_legacy_table_also_counts_as_processed(worker):
    worker.delta.tables["stage2_page_analysis"] = [_doc("https://a/", "Alpha. Beta.")]
    worker.delta.tables[LEGACY_TABLE_STAGE3_SUMMARIES] = [{"url_hash": "h-https://a/"}]
    assert asyncio.run(worker.run()) == 0


def test_summarize_failure_records_error_and_returns_none(worker, monkeypatch):
    from src.stage3 import stage3_worker as mod

    recorded: list[dict] = []
    monkeypatch.setattr(mod, "record_error", lambda postgres, **kw: recorded.append(kw))

    bad = _doc("https://bad/", "unused")
    bad["text_content"] = None  # None.split → AttributeError inside the try block
    assert asyncio.run(worker._summarize_document(bad)) is None
    assert recorded == [
        {"stage": "stage3", "url": "https://bad/", "error_type": "AttributeError",
         "error_message": recorded[0]["error_message"]}
    ]


def test_failed_documents_are_not_written_and_stay_pending(worker, monkeypatch):
    docs = [_doc("https://a/", _TEXTS[0]), _doc("https://b/", _TEXTS[1])]
    worker.delta.tables["stage2_page_analysis"] = docs
    worker.delta.tables[TABLE_STAGE3_SUMMARIES] = []
    real = Stage3Worker._summarize_document

    async def flaky(self, doc):
        if doc["url"] == "https://b/":
            return None  # what the except-branch returns
        return await real(self, doc)

    monkeypatch.setattr(Stage3Worker, "_summarize_document", flaky)
    assert asyncio.run(worker.run()) == 1
    assert [r["url"] for r in worker.delta.tables[TABLE_STAGE3_SUMMARIES]] == ["https://a/"]
    # b has no summary row, so the next run picks it up again.
    assert "h-https://b/" not in worker._processed_hashes()


def test_summary_without_sentence_breaks_is_capped(worker):
    from src.stage3.stage3_worker import MAX_SUMMARY_CHARS

    text = "word " * 3000  # no periods: the whole text is one "sentence"
    out = asyncio.run(worker._summarize_document(_doc("https://x/", text)))
    assert out is not None
    assert 0 < len(out["summary"]) <= MAX_SUMMARY_CHARS


@pytest.mark.parametrize(
    "configured, expected",
    [(0.5, 0.5), (1.0, 1.0), (None, 0.3), (0, 0.3), (1.5, 0.3), (-0.2, 0.3), ("abc", 0.3)],
)
def test_similarity_threshold_comes_from_config(monkeypatch, configured, expected):
    from src.stage3 import stage3_worker as mod

    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: default if configured is None else configured
    monkeypatch.setattr(mod, "get_config", lambda: cfg)
    assert mod._similarity_threshold() == expected


def test_batching_writes_once_per_batch(worker):
    docs = [_doc(f"https://{i}/", _TEXTS[i]) for i in range(5)]
    worker.batch_size = 2
    worker.delta.tables["stage2_page_analysis"] = docs
    worker.delta.tables[TABLE_STAGE3_SUMMARIES] = []
    n = asyncio.run(worker.run())
    assert n == 5
    # 5 docs / batch_size 2 → 3 writes
    assert len(worker.delta.writes) == 3
    assert all(w[0] == TABLE_STAGE3_SUMMARIES for w in worker.delta.writes)


def test_deduplicate_drops_near_duplicates(worker):
    text = "research lab findings " * 40
    unique = asyncio.run(
        worker._deduplicate_documents(
            [
                _doc("https://a/", text, url_hash="a"),
                _doc("https://b/", text, url_hash="b"),  # same text → near-dup
                _doc("https://c/", "completely different content about sports " * 40, url_hash="c"),
            ]
        )
    )
    hashes = {d["url_hash"] for d in unique}
    assert "a" in hashes and "c" in hashes
    assert "b" not in hashes


def test_drain_stops_after_current_batch(worker, monkeypatch):
    docs = [_doc(f"https://{i}/", _TEXTS[i]) for i in range(6)]
    worker.batch_size = 2
    worker.delta.tables["stage2_page_analysis"] = docs
    worker.delta.tables[TABLE_STAGE3_SUMMARIES] = []
    # SIGTERM arrives during the first batch: that batch is saved, the rest wait.
    monkeypatch.setattr("src.stage3.stage3_worker._drain_requested", lambda done, total: done >= 2)
    assert asyncio.run(worker.run()) == 2
    assert len(worker.delta.writes) == 1


def test_real_shutdown_flag_stops_the_run_after_one_batch(worker):
    from src.stage3.stage3_worker import _drain_requested

    assert _drain_requested(0, 2) is False
    gs.get_shutdown().request("test")  # flag only; no signal, no force-exit timer
    assert _drain_requested(1, 2) is True

    worker.batch_size = 2
    worker.delta.tables["stage2_page_analysis"] = [_doc(f"https://{i}/", _TEXTS[i]) for i in range(4)]
    worker.delta.tables[TABLE_STAGE3_SUMMARIES] = []
    assert asyncio.run(worker.run()) == 2


def test_fallback_summary_truncates(worker):
    long = "A" * 2000
    out = worker._fallback_summary(long, max_chars=100)
    assert len(out) <= 100


def test_extract_key_facts_prefers_keyword_sentences(worker):
    text = "Intro sentence. The research lab published results. Closing remarks."
    facts = worker._extract_key_facts(text, ["research"])
    assert any("research" in f.lower() for f in facts)
