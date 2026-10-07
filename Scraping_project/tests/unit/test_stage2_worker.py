import pyarrow as pa
import pytest
from deltalake import DeltaTable

from src.lakehouse.lakehouse_manager import DeltaLakeManager
from src.stage2.stage2_worker import Stage2Worker

@pytest.fixture
def delta_manager(tmp_path):
    temp_data_path = tmp_path / "delta_lake"
    temp_data_path.mkdir()

    manager = DeltaLakeManager(base_path=str(temp_data_path), start_workers=False)
    return manager

@pytest.fixture
def stage2_queue_table(delta_manager):
    table_name = "test_stage2_queue"
    table_path = delta_manager.base_path / table_name

    schema = pa.schema(
        [
            pa.field("url", pa.string()),
            pa.field("url_hash", pa.string()),
            pa.field("status", pa.string()),
            pa.field("is_heavy", pa.bool_()),
            pa.field("completed_at", pa.timestamp("us")),
        ]
    )

    DeltaTable.create(table_path, schema, mode="overwrite")

    data = [
        {
            "url": "http://example.com/a",
            "url_hash": "hash_a",
            "status": "pending",
            "is_heavy": False,
            "completed_at": None,
        },
        {
            "url": "http://example.com/b",
            "url_hash": "hash_b",
            "status": "pending",
            "is_heavy": False,
            "completed_at": None,
        },
        {
            "url": "http://example.com/c",
            "url_hash": "hash_c",
            "status": "pending",
            "is_heavy": True,
            "completed_at": None,
        },
    ]

    delta_manager.write(table_name, data, mode="append", async_write=False)

    return table_name, delta_manager.read(table_name)

@pytest.mark.asyncio
async def test_update_queue_status_merge(delta_manager, stage2_queue_table):
    table_name, _ = stage2_queue_table
    worker = Stage2Worker()
    worker.delta = delta_manager

    completed_urls = ["http://example.com/a", "http://example.com/c"]

    await worker._update_queue_status(completed_urls, table_name=table_name)

    results = delta_manager.read(table_name)

    status_map = {row["url"]: row["status"] for row in results}

    assert status_map.get("http://example.com/a") == "completed"
    assert status_map.get("http://example.com/c") == "completed"

    assert status_map.get("http://example.com/b") == "pending"

    completed_at_map = {row["url"]: row["completed_at"] for row in results}
    assert completed_at_map.get("http://example.com/a") is not None
    assert completed_at_map.get("http://example.com/c") is not None
    assert completed_at_map.get("http://example.com/b") is None


# ---- #168: no full-table overwrite; MERGE retries; disjoint concurrent updates ----

@pytest.mark.asyncio
async def test_concurrent_workers_disjoint_updates_both_land(delta_manager):
    import asyncio as _asyncio

    table_name = "stage2_queue_168"
    schema = pa.schema([pa.field("url", pa.string()), pa.field("status", pa.string()),
                        pa.field("completed_at", pa.timestamp("us"))])
    DeltaTable.create(delta_manager.base_path / table_name, schema, mode="overwrite")
    urls = [f"http://example.com/{i}" for i in range(40)]
    delta_manager.write(table_name, [{"url": u, "status": "pending", "completed_at": None} for u in urls],
                        mode="append", async_write=False)

    w1, w2 = Stage2Worker(), Stage2Worker()
    w1.delta = w2.delta = delta_manager
    ok = await _asyncio.gather(
        w1._update_queue_status(urls[:20], table_name=table_name),
        w2._update_queue_status(urls[20:], table_name=table_name, status="failed"),
    )
    assert ok == [True, True]
    status = {r["url"]: r["status"] for r in delta_manager.read(table_name)}
    assert all(status[u] == "completed" for u in urls[:20])
    assert all(status[u] == "failed" for u in urls[20:])


@pytest.mark.asyncio
async def test_merge_failure_leaves_rows_pending_and_never_overwrites(delta_manager, stage2_queue_table, monkeypatch):
    import src.stage2.stage2_worker as s2

    table_name, _ = stage2_queue_table
    worker = Stage2Worker()
    worker.delta = delta_manager
    monkeypatch.setenv("STAGE2_MERGE_RETRIES", "2")
    monkeypatch.setattr(s2.asyncio, "sleep", _no_sleep)

    def broken_merge(*a, **k):
        raise RuntimeError("Commit failed: version conflict")

    writes = []
    monkeypatch.setattr(worker, "_merge_queue_status", broken_merge)
    monkeypatch.setattr(delta_manager, "write", lambda *a, **k: writes.append((a, k)))
    counter = s2.STAGE2_QUEUE_UPDATE_FAILURES.labels(status="completed") if s2.STAGE2_QUEUE_UPDATE_FAILURES else None
    before = counter._value.get() if counter else None

    assert await worker._update_queue_status(["http://example.com/a"], table_name=table_name) is False
    assert writes == []  # no full-table overwrite fallback
    assert {r["url"]: r["status"] for r in delta_manager.read(table_name)}["http://example.com/a"] == "pending"
    assert not hasattr(worker, "_update_queue_status_overwrite")
    if counter is not None:
        assert counter._value.get() == before + 1


async def _no_sleep(_seconds):
    return None
