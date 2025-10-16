import asyncio
import os
from datetime import datetime

import pyarrow as pa
import pytest
from deltalake import DeltaTable

from src.common.delta_lake import DeltaLakeManager
from src.stage2.stage2_worker import Stage2Worker


@pytest.fixture
def delta_manager(tmp_path):
    """Fixture to create a temporary Delta Lake manager for testing."""
    # Override the default data path to use a temporary directory
    temp_data_path = tmp_path / "delta_lake"
    temp_data_path.mkdir()
    
    # Create a mock Delta manager
    manager = DeltaLakeManager(base_path=str(temp_data_path), start_workers=False)
    return manager

@pytest.fixture
def stage2_queue_table(delta_manager):
    """Fixture to create a dummy stage2_queue table for testing."""
    table_name = "test_stage2_queue"
    table_path = delta_manager.base_path / table_name
    
    # Define schema
    schema = pa.schema([
        pa.field("url", pa.string()),
        pa.field("url_hash", pa.string()),
        pa.field("status", pa.string()),
        pa.field("is_heavy", pa.bool_()),
        pa.field("completed_at", pa.timestamp("us")),
    ])
    
    # Create the Delta table with the correct schema before writing the data
    DeltaTable.create(
        table_path,
        schema,
        mode="overwrite"
    )

    # Create initial data
    data = [
        {"url": "http://example.com/a", "url_hash": "hash_a", "status": "pending", "is_heavy": False, "completed_at": None},
        {"url": "http://example.com/b", "url_hash": "hash_b", "status": "pending", "is_heavy": False, "completed_at": None},
        {"url": "http://example.com/c", "url_hash": "hash_c", "status": "pending", "is_heavy": True, "completed_at": None},
    ]
    
    # Write initial data
    delta_manager.write(table_name, data, mode="append", async_write=False)
    
    return table_name, delta_manager.read(table_name)

@pytest.mark.asyncio
async def test_update_queue_status_merge(delta_manager, stage2_queue_table):
    """
    Test that the _update_queue_status function correctly updates the status
    of completed URLs using the MERGE operation.
    """
    table_name, _ = stage2_queue_table
    # 1. Initialize the worker
    worker = Stage2Worker()
    worker.delta = delta_manager  # Inject the mock manager

    # 2. Define completed URLs
    completed_urls = ["http://example.com/a", "http://example.com/c"]

    # 3. Call the function to be tested
    await worker._update_queue_status(completed_urls, table_name=table_name)

    # 4. Verify the results
    # Read the table again and check the status
    results = delta_manager.read(table_name)

    # Create a map of URL -> status for easy lookup
    status_map = {row["url"]: row["status"] for row in results}

    # Assert that the completed URLs are marked as 'completed'
    assert status_map.get("http://example.com/a") == "completed"
    assert status_map.get("http://example.com/c") == "completed"
    
    # Assert that the pending URL is still 'pending'
    assert status_map.get("http://example.com/b") == "pending"

    # Assert that completed_at is populated for completed items
    completed_at_map = {row["url"]: row["completed_at"] for row in results}
    assert completed_at_map.get("http://example.com/a") is not None
    assert completed_at_map.get("http://example.com/c") is not None
    assert completed_at_map.get("http://example.com/b") is None
