"""#612/#316: Stage 3 owns stage3_summaries and still honours the legacy name."""

from __future__ import annotations

import asyncio

from src.core.constants import LEGACY_TABLE_STAGE3_SUMMARIES, TABLE_STAGE3_SUMMARIES
from src.core.schemas import SCHEMA_REGISTRY


class FakeDelta:
    def __init__(self, tables):
        self.tables = {k: list(v) for k, v in tables.items()}
        self.writes = []

    def read(self, table, **_):
        if table not in self.tables:
            raise FileNotFoundError(table)
        return self.tables[table]

    def write(self, table, rows, mode="append", async_write=True):
        self.writes.append(table)
        self.tables.setdefault(table, []).extend(rows)
        return True


def _worker(delta):
    from src.stage3.stage3_worker import Stage3Worker

    w = Stage3Worker.__new__(Stage3Worker)
    w.max_concurrent = 2
    w.batch_size = 10
    w.semaphore = asyncio.Semaphore(2)
    w.delta = delta
    w.postgres = None
    w.SIMILARITY_THRESHOLD = 0.3
    return w


def test_canonical_names():
    assert TABLE_STAGE3_SUMMARIES == "stage3_summaries"
    assert LEGACY_TABLE_STAGE3_SUMMARIES == "stage4_summaries"
    assert TABLE_STAGE3_SUMMARIES in SCHEMA_REGISTRY


def test_processed_hashes_reads_canonical_and_legacy_tables():
    delta = FakeDelta({
        TABLE_STAGE3_SUMMARIES: [{"url_hash": "a"}],
        LEGACY_TABLE_STAGE3_SUMMARIES: [{"url_hash": "b"}, {"url_hash": None}],
    })
    assert _worker(delta)._processed_hashes() == {"a", "b"}


def test_processed_hashes_tolerates_missing_tables():
    assert _worker(FakeDelta({}))._processed_hashes() == set()


def test_run_skips_legacy_rows_and_writes_only_the_stage3_table(monkeypatch):
    docs = [
        {"url_hash": h, "url": f"https://x/{h}", "text_content": "word " * 200,
         "is_low_quality": False}
        for h in ("old", "new")
    ]
    delta = FakeDelta({
        "stage2_page_analysis": docs,
        LEGACY_TABLE_STAGE3_SUMMARIES: [{"url_hash": "old"}],
    })
    w = _worker(delta)
    summarized = []

    async def fake_dedupe(batch):
        return batch

    async def fake_summarize(doc):
        summarized.append(doc["url_hash"])
        return {"url_hash": doc["url_hash"], "summary": "s"}

    monkeypatch.setattr(w, "_deduplicate_documents", fake_dedupe)
    monkeypatch.setattr(w, "_summarize_document", fake_summarize)
    asyncio.run(w._run_traced())

    assert summarized == ["new"], "a document summarized under the legacy name must not be redone"
    assert delta.writes == [TABLE_STAGE3_SUMMARIES]
