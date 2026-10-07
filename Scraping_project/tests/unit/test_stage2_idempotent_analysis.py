"""#311: analysis is upserted by url_hash and only acked once durable."""

import pytest
from deltalake import DeltaTable

from src.lakehouse.lakehouse_manager import LakehouseManager
from src.stage2 import stage2_worker as sw
from src.stage2.stage2_worker import Stage2Worker, ensure_url_hash

URLS = ["https://a.example.edu/1", "https://b.example.edu/2"]


@pytest.fixture
def worker(tmp_path):
    lake = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    lake._write_sync(
        "stage2_queue",
        [{"url": u, "url_hash": f"h{i}", "status": "pending"} for i, u in enumerate(URLS)],
        "append",
    )
    w = Stage2Worker()
    w.delta = lake
    w.batch_size = 10
    calls = {"n": 0}

    async def fake_analyze(record):
        calls["n"] += 1
        return {
            "url": record["url"],
            "url_hash": record["url_hash"],
            "status_code": 200,
            "word_count": 100 + calls["n"],  # differs per run, so we can see the upsert
            "has_error": False,
            "is_low_quality": False,
            "is_massive_doc": False,
        }

    w._analyze_url = fake_analyze
    yield w, lake
    lake.shutdown_event.set()


def _analysis(lake):
    path = lake.get_table_path("stage2_page_analysis")
    return DeltaTable(str(path)).to_pyarrow_table().to_pylist()


def _queue_status(lake):
    return {r["url"]: r["status"] for r in lake.read("stage2_queue")}


@pytest.mark.asyncio
async def test_crash_between_write_and_ack_does_not_duplicate(worker, monkeypatch):
    w, lake = worker
    real_update = w._update_queue_status

    async def crash(*a, **k):
        raise RuntimeError("worker killed before queue ack")

    monkeypatch.setattr(w, "_update_queue_status", crash)
    with pytest.raises(RuntimeError):
        await w.run()
    first = _analysis(lake)
    assert len(first) == 2
    assert set(_queue_status(lake).values()) == {"pending"}  # ack lost

    monkeypatch.setattr(w, "_update_queue_status", real_update)
    await w.run()  # re-processes the same URLs

    rows = _analysis(lake)
    assert len(rows) == 2, "re-processing must upsert, not append duplicates"
    assert sorted(r["url_hash"] for r in rows) == ["h0", "h1"]
    assert all(r["word_count"] > 102 for r in rows)  # second run's values replaced the first
    assert all(r["domain"] for r in rows)  # partition key still populated on the merge path
    assert set(_queue_status(lake).values()) == {"completed"}


@pytest.mark.asyncio
async def test_failed_analysis_write_leaves_queue_pending(worker, monkeypatch):
    w, lake = worker
    monkeypatch.setattr(lake, "merge_into", lambda *a, **k: -1)
    acked = []

    async def record_update(urls, *a, **k):
        acked.extend(urls)
        return True

    monkeypatch.setattr(w, "_update_queue_status", record_update)
    before = sw.STAGE2_ANALYSIS_WRITE_FAILURES._value.get() if sw.STAGE2_ANALYSIS_WRITE_FAILURES else None

    await w.run()

    assert acked == []  # nothing acked whose analysis was not durable
    assert set(_queue_status(lake).values()) == {"pending"}
    if before is not None:
        assert sw.STAGE2_ANALYSIS_WRITE_FAILURES._value.get() == before + 1


def test_rows_without_hash_get_seed_hash():
    from src.lakehouse.seed_manager import default_url_hasher

    rows = ensure_url_hash([{"url": "https://x.edu/a"}, {"url": "https://x.edu/b", "url_hash": "keep"}])
    assert rows[0]["url_hash"] == default_url_hasher("https://x.edu/a")
    assert rows[1]["url_hash"] == "keep"
