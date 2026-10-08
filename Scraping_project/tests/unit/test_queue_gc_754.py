"""Retention GC for completed/failed stage queue rows (#754)."""

from datetime import datetime, timedelta

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from src.lakehouse.lakehouse_manager import QUEUE_TABLES, LakehouseManager

NOW = datetime(2026, 10, 8, 12, 0, 0)
OLD = NOW - timedelta(days=10)
RECENT = NOW - timedelta(hours=2)


def _rows():
    return {
        "url": ["old-done", "new-done", "old-failed", "pending", "processing", "done-no-ts"],
        "status": ["completed", "completed", "failed", "pending", "processing", "completed"],
        "completed_at": [OLD, RECENT, OLD, None, OLD, None],
    }


def _write(path, kind):
    rows = _rows()
    if kind == "timestamp":
        col = pa.array(rows["completed_at"], type=pa.timestamp("ms"))
    else:
        col = pa.array([v.isoformat() if v else None for v in rows["completed_at"]])
    write_deltalake(
        str(path),
        pa.table({"url": rows["url"], "status": rows["status"], "completed_at": col}),
    )


def _urls(path):
    return sorted(DeltaTable(str(path)).to_pyarrow_table(columns=["url"])["url"].to_pylist())


@pytest.fixture
def manager(tmp_path):
    return LakehouseManager(base_path=str(tmp_path), start_workers=False)


@pytest.mark.parametrize(
    "table,kind",
    [("stage2_queue", "timestamp"), ("js_spider_queue", "string"), ("stage4_large_docs", "string")],
)
def test_gc_deletes_only_expired_terminal_rows(manager, table, kind):
    _write(manager.tables[table], kind)

    res = manager.gc_queue_table(table, 168, now=NOW)

    assert res["matched"] == res["deleted"] == res["archived"] == 2
    assert _urls(manager.tables[table]) == ["done-no-ts", "new-done", "pending", "processing"]
    history = manager.base_path / f"{table}_history"
    assert _urls(history) == ["old-done", "old-failed"]


def test_dry_run_changes_nothing(manager):
    _write(manager.tables["stage2_queue"], "timestamp")

    res = manager.gc_queue_table("stage2_queue", 168, dry_run=True, now=NOW)

    assert res["matched"] == 2 and res["deleted"] == 0
    assert len(_urls(manager.tables["stage2_queue"])) == 6
    assert not (manager.base_path / "stage2_queue_history").exists()


def test_no_archive_skips_history(manager):
    _write(manager.tables["js_spider_queue"], "string")

    res = manager.gc_queue_table("js_spider_queue", 168, archive=False, now=NOW)

    assert res["deleted"] == 2 and res["archived"] == 0
    assert not (manager.base_path / "js_spider_queue_history").exists()


def test_short_retention_also_collects_recent_rows(manager):
    _write(manager.tables["stage2_queue"], "timestamp")

    res = manager.gc_queue_table("stage2_queue", 1, now=NOW)

    assert res["deleted"] == 3
    assert _urls(manager.tables["stage2_queue"]) == ["done-no-ts", "pending", "processing"]


def test_missing_table_and_columns_are_skipped(manager):
    assert manager.gc_queue_table("stage2_queue", 168)["skipped"] == "table does not exist"
    write_deltalake(str(manager.tables["stage4_large_docs"]), pa.table({"url": ["a"]}))
    res = manager.gc_queue_table("stage4_large_docs", 168)
    assert res["skipped"] == "no status/completed_at column"


def test_invalid_retention_rejected(manager):
    with pytest.raises(ValueError):
        manager.gc_queue_table("stage2_queue", 0)


def test_gc_all_queues_reports_each_table_and_counts(manager, monkeypatch):
    _write(manager.tables["stage2_queue"], "timestamp")
    _write(manager.tables["stage4_large_docs"], "string")
    vacuumed = []
    monkeypatch.setattr(manager, "_vacuum_table", lambda t, h: vacuumed.append(t))
    real = manager.gc_queue_table
    monkeypatch.setattr(manager, "gc_queue_table", lambda t, h, **kw: real(t, h, now=NOW, **kw))

    results = manager.gc_all_queues(168)

    by_table = {r["table"]: r for r in results}
    assert set(by_table) == set(QUEUE_TABLES)
    assert by_table["stage2_queue"]["deleted"] == 2
    assert by_table["stage4_large_docs"]["deleted"] == 2
    assert by_table["js_spider_queue"]["skipped"] == "table does not exist"
    assert sorted(vacuumed) == ["stage2_queue", "stage4_large_docs"]
    counts = manager.queue_row_counts()
    assert counts["stage2_queue"]["completed"] == 2
    assert counts["stage2_queue"]["failed"] == 0
    assert counts["stage2_queue"]["pending"] == 1


def test_gc_all_queues_isolates_failures(manager, monkeypatch):
    def boom(table, hours, **kw):
        if table == "stage2_queue":
            raise RuntimeError("disk on fire")
        return {"table": table, "matched": 0, "archived": 0, "deleted": 0, "skipped": None}

    monkeypatch.setattr(manager, "gc_queue_table", boom)
    results = manager.gc_all_queues(168)
    assert results[0]["skipped"].startswith("error: disk on fire")
    assert [r["table"] for r in results] == list(QUEUE_TABLES)


def test_maintenance_trigger_respects_interval_and_disable(manager, monkeypatch):
    calls = []
    monkeypatch.setattr(manager, "gc_all_queues", lambda *a, **kw: calls.append((a, kw)))
    manager.queue_retention_hours = 168
    manager.queue_gc_interval_s = 3600

    manager._last_queue_gc = 0.0
    manager._maybe_gc_queues()
    manager._maybe_gc_queues()  # within the interval: no second run
    assert len(calls) == 1

    manager._last_queue_gc = 0.0
    manager.queue_retention_hours = 0
    manager._maybe_gc_queues()
    assert len(calls) == 1


def test_config_defaults():
    from src.core.config import Config

    cfg = Config.get_instance()
    assert cfg.get("delta_lake.queue_retention_hours") == 168
    assert cfg.get("delta_lake.queue_gc_interval_minutes") == 60
    assert cfg.get("delta_lake.queue_gc_archive") is True


def test_cli_queue_gc_dry_run(tmp_path, monkeypatch, capsys):
    import cli

    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path))
    _write(tmp_path / "stage2_queue", "timestamp")
    parser_args = type("A", (), {"retention_hours": 1.0, "dry_run": True, "no_archive": False})()
    cli.cmd_queue_gc(parser_args)
    out = capsys.readouterr().out
    assert "stage2_queue: would delete" in out
    assert len(_urls(tmp_path / "stage2_queue")) == 6
