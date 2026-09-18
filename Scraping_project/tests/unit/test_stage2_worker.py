import pyarrow as pa
import pytest
from deltalake import DeltaTable

from src.common.delta_lake import DeltaLakeManager
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
