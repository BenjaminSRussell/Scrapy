"""#600 list_tables counts partitioned files; #614 guarded, recoverable deletes."""

import json

import pytest
from deltalake import DeltaTable

from src.lakehouse.lakehouse_manager import DeltaLakeManager, InMemoryBackend
from src.utils.delta import DeltaHelper

PART = "stage1_discovery"  # partitioned by domain


@pytest.fixture
def manager(tmp_path):
    mgr = DeltaLakeManager(base_path=str(tmp_path / "lake"), start_workers=False)
    yield mgr
    mgr.shutdown()


def _info(mgr, name):
    return next(t for t in mgr.list_tables() if t["name"] == name)


# ---- #600 -------------------------------------------------------------------

def test_list_tables_counts_files_under_partition_dirs(manager):
    rows = [{"url": f"https://{d}.edu/{i}"} for d in ("uconn", "yale", "mit") for i in range(3)]
    assert manager.write(PART, rows, async_write=False)
    assert manager.write(PART, [{"url": "https://uconn.edu/x"}], async_write=False)
    path = manager.get_table_path(PART)
    assert not list(path.glob("*.parquet")), "data lives in domain=... subdirs"

    info = _info(manager, PART)
    live = len(DeltaTable(str(path)).file_uris())
    assert info["parquet_files"] == live >= 4
    assert info["partitions"] == 3
    assert info["row_count"] == 10


def test_list_tables_ignores_tombstoned_files(manager):
    manager.write("plain", [{"a": 1}], async_write=False)
    manager.write("plain", [{"a": 2}], mode="overwrite", async_write=False)
    info = _info(manager, "plain")
    assert info["parquet_files"] == 1 and info["partitions"] == 0 and info["row_count"] == 1


def test_list_tables_missing_table(manager):
    info = _info(manager, PART)
    assert info["exists"] is False and info["parquet_files"] == 0


# ---- #614 -------------------------------------------------------------------

def test_delete_is_denied_by_default(manager):
    manager.write("t", [{"a": 1}], async_write=False)
    with pytest.raises(PermissionError):
        manager.delete_table("t")
    assert manager.table_exists("t")


def test_soft_delete_moves_to_trash_and_restores_with_history(manager):
    manager.write("t", [{"a": 1}], async_write=False)
    manager.write("t", [{"a": 2}], async_write=False)
    trash = manager.delete_table("t", allow_destructive=True, reason="test")
    assert not manager.table_exists("t")
    assert trash is not None and (trash / "_delta_log").is_dir()

    manager.restore_table("t")
    path = str(manager.get_table_path("t"))
    assert DeltaTable(path).version() == 1
    assert DeltaTable(path, version=0).to_pyarrow_table().num_rows == 1  # time travel intact


def test_restore_refuses_to_overwrite_a_live_table(manager):
    manager.write("t", [{"a": 1}], async_write=False)
    manager.delete_table("t", allow_destructive=True)
    manager.write("t", [{"a": 9}], async_write=False)
    with pytest.raises(FileExistsError):
        manager.restore_table("t")


def test_hard_delete_needs_env_opt_in(manager, monkeypatch):
    manager.write("t", [{"a": 1}], async_write=False)
    monkeypatch.delenv("DELTA_ALLOW_HARD_DELETE", raising=False)
    with pytest.raises(PermissionError):
        manager.delete_table("t", allow_destructive=True, hard=True)
    assert manager.table_exists("t")

    monkeypatch.setenv("DELTA_ALLOW_HARD_DELETE", "1")
    assert manager.delete_table("t", allow_destructive=True, hard=True) is None
    assert not manager.get_table_path("t").exists()


def test_deletes_are_audited(manager, monkeypatch):
    manager.write("t", [{"a": 1}], async_write=False)
    manager.delete_table("t", allow_destructive=True, reason="cleanup")
    manager.restore_table("t")
    monkeypatch.setenv("DELTA_ALLOW_HARD_DELETE", "1")
    manager.delete_table("t", allow_destructive=True, hard=True)

    lines = (manager.base_path / "_audit" / "table_deletes.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines]
    assert [r["action"] for r in records] == ["soft", "restore", "hard"]
    assert records[0]["reason"] == "cleanup" and records[0]["trash"]


def test_truncate_keeps_schema_and_history(manager):
    manager.write("t", [{"a": 1, "b": "x"}], async_write=False)
    assert manager.truncate_table("t")
    dt = DeltaTable(str(manager.get_table_path("t")))
    assert dt.to_pyarrow_table().num_rows == 0
    assert {"a", "b"} <= set(dt.schema().to_arrow().names)
    assert DeltaTable(str(manager.get_table_path("t")), version=0).to_pyarrow_table().num_rows == 1


def test_delta_helper_clear_table_actually_clears(manager):
    helper = DeltaHelper(base_path=manager.base_path)
    helper._manager = manager
    manager.write("t", [{"a": 1}], async_write=False)
    assert helper.clear_table("t") is True
    assert manager.count("t") == 0


def test_in_memory_backend_matches_guard():
    backend = InMemoryBackend()
    backend.write("t", [{"a": 1}])
    with pytest.raises(PermissionError):
        backend.delete_table("t")
    backend.truncate_table("t")
    assert backend.count("t") == 0
    backend.delete_table("t", allow_destructive=True)
    assert not backend.table_exists("t")


def test_emptied_table_counts_lists_and_exports(manager, tmp_path):
    """A table with zero live files (after truncate) must not hit the delta-rs
    get_add_actions() panic in count()/list_tables(), and export still works."""
    manager.write(PART, [{"url": "https://uconn.edu/a"}], async_write=False)
    manager.truncate_table(PART)
    assert manager.count(PART) == 0
    info = _info(manager, PART)
    assert (info["parquet_files"], info["partitions"], info["row_count"]) == (0, 0, 0)
    assert "error" not in info
    result = manager.export(PART, str(tmp_path / "empty.csv"))
    assert result["rows"] == 0
    assert manager.write(PART, [{"url": "https://uconn.edu/b"}], async_write=False)
    assert manager.count(PART) == 1
