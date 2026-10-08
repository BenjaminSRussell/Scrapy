"""Config-driven Delta partitioning (#261 #236 #268) and declared tables (#433)."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake
from prometheus_client import REGISTRY

from src.lakehouse import lakehouse_manager as lm
from src.lakehouse.lakehouse_manager import (
    DEFAULT_PARTITIONS,
    LakehouseManager,
    _iso_day,
    partition_date,
    partition_settings,
    reset_partition_settings,
)


class _Cfg:
    def __init__(self, delta_lake: dict):
        self.delta_lake = delta_lake

    def get(self, key, default=None):
        node = {"delta_lake": self.delta_lake}
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


@pytest.fixture(autouse=True)
def _fresh_settings():
    reset_partition_settings()
    yield
    reset_partition_settings()


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.delenv("DELTA_UNDECLARED_TABLES", raising=False)
    m = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    yield m
    m.shutdown()


def partitions_of(path) -> list[str]:
    return list(DeltaTable(str(path)).metadata().partition_columns)


# --- settings ---------------------------------------------------------------


def test_repo_config_declares_partitions_for_append_only_tables():
    partitions, sources = partition_settings()
    assert partitions["stage1_discovery"] == ["domain"]
    assert partitions["stage2_page_analysis"] == ["domain"]
    for table in ("stage1_errors", "stage2_errors", "stage3_summaries", "stage4_summaries",
                  "stage4_large_doc_summaries", "metadata_queue"):
        assert partitions[table] == ["date"], table
    for queue in ("stage2_queue", "js_spider_queue", "seed_urls", "stage4_large_docs"):
        assert queue not in partitions
    assert sources[0] == "scraped_at_utc" and "_ingestion_time" in sources


def test_defaults_when_config_has_no_partitions_key():
    partitions, sources = partition_settings(_Cfg({}))
    assert partitions == DEFAULT_PARTITIONS
    assert sources == lm.DEFAULT_DATE_SOURCE_FIELDS


def test_config_replaces_defaults_and_normalises():
    partitions, _ = partition_settings(
        _Cfg({"partitions": {"a": "date", "b": [" domain ", "date"], "c": [], "d": None}})
    )
    assert partitions == {"a": ["date"], "b": ["domain", "date"]}


@pytest.mark.parametrize(
    "raw", [["stage1_discovery"], {"t": [1]}, {"t": ["date", "date"]}, {"t": [""]}, {"t": {"x": 1}}]
)
def test_malformed_partition_config_fails_fast(raw):
    with pytest.raises(ValueError):
        partition_settings(_Cfg({"partitions": raw}))


# --- date derivation --------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-10-07T23:59:59Z", "2026-10-07"),
        ("2026-10-07", "2026-10-07"),
        (" 2026-02-28 10:00 ", "2026-02-28"),
        (datetime(2026, 1, 2, 3, tzinfo=UTC), "2026-01-02"),
        (date(2026, 3, 4), "2026-03-04"),
        (1767225600, "2026-01-01"),          # epoch seconds
        (1767225600000, "2026-01-01"),       # epoch millis
        ("2026-13-40", None),
        ("yesterday", None),
        ("", None),
        (None, None),
        (True, None),
    ],
)
def test_iso_day(value, expected):
    assert _iso_day(value) == expected


def test_partition_date_prefers_event_time_then_falls_back():
    assert partition_date({"scraped_at_utc": "2026-05-01T00:00:00Z", "_ingestion_time": "2026-10-08"}) == "2026-05-01"
    assert partition_date({"scraped_at_utc": "garbage", "processed_at": "2026-04-30T12:00:00"}) == "2026-04-30"
    assert partition_date({}) == datetime.now(UTC).date().isoformat()


# --- write / merge paths -----------------------------------------------------


def test_new_date_partitioned_table_is_created_partitioned(manager):
    rows = [
        {"url": "https://a.uconn.edu/x", "error": "boom", "scraped_at_utc": "2026-10-01T10:00:00Z"},
        {"url": "https://a.uconn.edu/y", "error": "bang", "scraped_at_utc": "2026-10-02T10:00:00Z"},
    ]
    assert manager.write("stage1_errors", rows, async_write=False) is not False
    path = manager.get_table_path("stage1_errors")
    assert partitions_of(path) == ["date"]
    days = sorted(DeltaTable(str(path)).to_pyarrow_table().column("date").to_pylist())
    assert days == ["2026-10-01", "2026-10-02"]
    assert sorted(p.name for p in path.iterdir() if p.name.startswith("date=")) == ["date=2026-10-01", "date=2026-10-02"]


def test_existing_date_value_is_kept_and_garbage_replaced(manager):
    rows = [
        {"url": "https://a.uconn.edu/1", "date": "2026-09-09", "scraped_at_utc": "2026-10-01"},
        {"url": "https://a.uconn.edu/2", "date": "not-a-date", "scraped_at_utc": "2026-10-01"},
    ]
    manager.write("stage3_summaries", rows, async_write=False)
    table = DeltaTable(str(manager.get_table_path("stage3_summaries"))).to_pyarrow_table()
    got = dict(zip(table.column("url").to_pylist(), table.column("date").to_pylist(), strict=True))
    assert got == {"https://a.uconn.edu/1": "2026-09-09", "https://a.uconn.edu/2": "2026-10-01"}


def test_domain_partitioned_tables_unchanged(manager):
    manager.write("stage1_discovery", [{"url": "https://www.uconn.edu/a", "url_hash": "h1"}], async_write=False)
    path = manager.get_table_path("stage1_discovery")
    assert partitions_of(path) == ["domain"]
    assert "date" not in DeltaTable(str(path)).to_pyarrow_table().column_names


def test_queue_tables_stay_unpartitioned(manager):
    n = manager.merge_into("stage2_queue", [{"url": "https://a.uconn.edu/q", "url_hash": "q1", "status": "pending"}],
                           "url_hash", ["status"])
    assert n == 1
    assert partitions_of(manager.get_table_path("stage2_queue")) == []


def test_merge_into_creates_date_partitioned_table(manager):
    rows = [{"url_hash": "m1", "url": "https://a.uconn.edu/m", "processed_at": "2026-08-08T08:00:00"}]
    assert manager.merge_into("stage4_large_doc_summaries", rows, "url_hash", ["url"]) == 1
    path = manager.get_table_path("stage4_large_doc_summaries")
    assert partitions_of(path) == ["date"]
    # Second merge (update) keeps working against the partitioned table.
    rows[0]["url"] = "https://a.uconn.edu/m2"
    assert manager.merge_into("stage4_large_doc_summaries", rows, "url_hash", ["url"]) >= 1
    assert DeltaTable(str(path)).to_pyarrow_table().column("url").to_pylist() == ["https://a.uconn.edu/m2"]


def test_existing_unpartitioned_table_keeps_layout_and_writes_succeed(manager, caplog):
    """Regression (#261): a config/partition mismatch made every write fail."""
    path = manager.get_table_path("stage2_errors")
    write_deltalake(str(path), pa.table({"url": ["https://old/1"], "error": ["e"]}))  # legacy: no partitions
    with caplog.at_level(logging.WARNING):
        ok = manager.write("stage2_errors", [{"url": "https://new/2", "error": "e2"}], async_write=False)
        ok2 = manager.write("stage2_errors", [{"url": "https://new/3", "error": "e3"}], async_write=False)
    assert ok is not False and ok2 is not False
    assert partitions_of(path) == []
    assert DeltaTable(str(path)).to_pyarrow_table().num_rows == 3
    drift_logs = [r for r in caplog.records if "keeping the existing layout" in r.getMessage()]
    assert len(drift_logs) == 1  # logged once (cached)
    assert REGISTRY.get_sample_value("delta_partition_config_drift", {"table": "stage2_errors"}) == 1
    assert manager.partition_report()["stage2_errors"] == {"configured": ["date"], "existing": [], "drift": True}


def test_existing_partitioned_table_not_in_config_keeps_partitions(manager):
    path = manager.get_table_path("stage2_queue")
    write_deltalake(str(path), pa.table({"url_hash": ["a"], "status": ["pending"]}), partition_by=["status"])
    assert manager.merge_into("stage2_queue", [{"url_hash": "b", "status": "done"}], "url_hash", ["status"]) == 1
    assert partitions_of(path) == ["status"]


def test_partition_report_for_fresh_lake(manager):
    manager.write("stage1_errors", [{"url": "https://a/1", "error": "x"}], async_write=False)
    report = manager.partition_report()
    assert report["stage1_errors"] == {"configured": ["date"], "existing": ["date"], "drift": False}
    assert report["stage2_errors"]["existing"] is None and report["stage2_errors"]["drift"] is False


# --- declared tables (#433) ---------------------------------------------------


def test_metadata_queue_is_declared_partitioned_and_vacuumed(manager):
    assert "metadata_queue" in manager.tables  # included in vacuum_all_tables / list_tables
    assert "metadata_queue" in manager.declared_tables
    manager.write("metadata_queue", [{"url": "https://a/1", "title": "t"}], async_write=False)
    assert partitions_of(manager.get_table_path("metadata_queue")) == ["date"]


def test_undeclared_table_warns_once_but_writes(manager, caplog):
    with caplog.at_level(logging.WARNING):
        assert manager.write("shadow_tbl", [{"a": 1}], async_write=False) is not False
        assert manager.write("shadow_tbl", [{"a": 2}], async_write=False) is not False
    assert sum("not declared" in r.getMessage() for r in caplog.records) == 1
    assert REGISTRY.get_sample_value(
        "delta_undeclared_table_writes_total", {"table": "shadow_tbl", "action": "warned"}
    ) == 2


def test_undeclared_table_rejected_in_reject_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_UNDECLARED_TABLES", "reject")
    m = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    assert m.write("rogue_tbl", [{"a": 1}], async_write=False) is False
    assert m.merge_into("rogue_tbl", [{"k": 1}], "k", []) == -1
    assert not (tmp_path / "lake" / "rogue_tbl" / "_delta_log").exists()
    # Declared tables (built-in, config `tables`, `extra_tables`, quarantine) still work.
    assert m.write("stage1_errors", [{"url": "https://a/1", "error": "x"}], async_write=False) is not False
    assert m.write("entity_summaries", [{"entity": "x"}], async_write=False) is not False
    assert "cast_quarantine" in m.declared_tables and "domain_quarantine" in m.declared_tables


def test_invalid_undeclared_mode_fails_fast(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_UNDECLARED_TABLES", "explode")
    with pytest.raises(ValueError, match="undeclared_tables"):
        LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
