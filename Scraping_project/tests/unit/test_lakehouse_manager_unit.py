"""Unit suite for ``src/lakehouse/lakehouse_manager.py`` (#254).

Offline: every test gets its own ``temp_dir`` lake and a manager with
``start_workers=False`` (no background threads), writing synchronously.
Covers write/read/count/list, idempotent upserts and table lifecycle.
"""
from __future__ import annotations

import pytest

from src.lakehouse.lakehouse_manager import LakehouseManager

pytestmark = pytest.mark.delta


@pytest.fixture
def lake(temp_dir):
    mgr = LakehouseManager(base_path=str(temp_dir / "lake"), start_workers=False)
    yield mgr
    mgr.shutdown(timeout=1)


def _rows(n, **overrides):
    return [{"url": f"https://example.com/{i}", "url_hash": f"h{i:04d}", "status": "pending", **overrides}
            for i in range(n)]


def test_lake_lives_under_temp_dir(lake, temp_dir):
    assert str(lake.get_table_path("stage2_queue")).startswith(str(temp_dir))


def test_missing_table_reads_empty(lake):
    assert lake.read("stage2_queue") == []
    assert lake.count("stage2_queue") == 0
    assert lake.table_exists("stage2_queue") is False


def test_sync_write_then_read_and_count(lake):
    assert lake.write("stage2_queue", _rows(3), async_write=False) is not False
    rows = lake.read("stage2_queue")
    assert sorted(r["url_hash"] for r in rows) == ["h0000", "h0001", "h0002"]
    assert lake.count("stage2_queue") == 3
    assert lake.table_exists("stage2_queue") is True


def test_append_is_not_idempotent_but_merge_is(lake):
    rows = _rows(2)
    lake.write("stage2_queue", rows, async_write=False)
    lake.write("stage2_queue", rows, async_write=False)
    assert lake.count("stage2_queue") == 4  # plain append duplicates (documented)

    lake.truncate_table("stage2_queue")
    assert lake.count("stage2_queue") == 0
    first = lake.merge_into("stage2_queue", rows, "url_hash", ["url", "status"])
    again = lake.merge_into("stage2_queue", rows, "url_hash", ["url", "status"])
    assert first == 2 and again >= 0
    assert lake.count("stage2_queue") == 2  # re-applying the same upsert adds nothing


def test_merge_updates_matching_rows_and_inserts_new(lake):
    lake.merge_into("stage2_queue", _rows(2), "url_hash", ["url", "status"])
    changed = [{"url": "https://example.com/0", "url_hash": "h0000", "status": "completed"},
               {"url": "https://example.com/9", "url_hash": "h0009", "status": "pending"}]
    lake.merge_into("stage2_queue", changed, "url_hash", ["status"])
    by_hash = {r["url_hash"]: r["status"] for r in lake.read("stage2_queue")}
    assert by_hash == {"h0000": "completed", "h0001": "pending", "h0009": "pending"}


def test_merge_empty_is_noop(lake):
    assert lake.merge_into("stage2_queue", [], "url_hash", ["status"]) == 0
    assert lake.table_exists("stage2_queue") is False


def test_overwrite_replaces_rows(lake):
    lake.write("stage2_queue", _rows(3), async_write=False)
    lake.write("stage2_queue", _rows(1, status="done"), mode="overwrite", async_write=False)
    rows = lake.read("stage2_queue")
    assert len(rows) == 1 and rows[0]["status"] == "done"


def test_read_with_filters_and_columns(lake):
    lake.write("stage2_queue", _rows(3), async_write=False)
    lake.merge_into("stage2_queue", [{"url": "https://example.com/1", "url_hash": "h0001", "status": "done"}],
                    "url_hash", ["status"])
    done = lake.read("stage2_queue", filters=[("status", "=", "done")], columns=["url_hash"])
    assert done == [{"url_hash": "h0001"}]


def test_schema_shaped_records_round_trip(lake):
    from datetime import datetime

    from src.core.schemas import get_schema

    names = get_schema("stage3_summaries").names
    row = {"url": "https://example.com/a", "url_hash": "a", "summary": "s", "word_count": 3,
           "keywords": ["k"], "quality_score": 0.5, "timestamp": datetime(2026, 1, 1)}
    assert set(row) == set(names)
    lake.write("stage3_summaries", [row, {**row, "url_hash": "b"}], async_write=False)
    assert lake.count("stage3_summaries") == 2
    assert lake.read("stage3_summaries", columns=["keywords"])[0]["keywords"] == ["k"]


def test_list_tables_reports_existing_tables(lake):
    lake.write("stage2_queue", _rows(2), async_write=False)
    info = {t["name"]: t for t in lake.list_tables()}
    assert info["stage2_queue"]["exists"] is True
    assert info["stage2_queue"]["row_count"] == 2
    assert info["stage2_queue"]["parquet_files"] >= 1


def test_truncate_keeps_table_and_history(lake):
    lake.write("stage2_queue", _rows(2), async_write=False)
    assert lake.truncate_table("stage2_queue") is True
    assert lake.count("stage2_queue") == 0
    assert lake.table_exists("stage2_queue") is True
    assert len(lake.get_table_history("stage2_queue")) >= 2
    assert lake.truncate_table("stage3_summaries") is True  # known but never written: no-op
    with pytest.raises(ValueError, match="Unknown table"):
        lake.truncate_table("never_created")  # typos fail loudly instead of making a table
