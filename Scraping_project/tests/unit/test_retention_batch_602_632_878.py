"""Vacuum preview (#602), retention tiers + DLQ cleanup (#632), offsite promotion state (#878)."""

import time
from datetime import UTC, datetime, timedelta

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from src.lakehouse.lakehouse_manager import LakehouseManager
from src.lakehouse.offsite_review import OffsiteReview

NOW = datetime(2026, 10, 8, 12, 0, 0)


@pytest.fixture
def manager(tmp_path):
    return LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)


# ---------------------------------------------------------------- #602 / #632
def _table_with_garbage(path):
    write_deltalake(str(path), pa.table({"a": [1, 2]}))
    write_deltalake(str(path), pa.table({"a": [3]}), mode="overwrite")  # first file is now unreferenced


def test_vacuum_preview_lists_files_and_bytes_without_deleting(manager):
    path = manager.tables["stage2_page_analysis"]
    _table_with_garbage(path)
    before = sorted(p.name for p in path.glob("*.parquet"))

    # delta-rs ages files by the log's remove timestamp, so only 0h sees them now.
    assert manager.vacuum_preview("stage2_page_analysis", 168)["files"] == []
    preview = manager.vacuum_preview("stage2_page_analysis", 0)

    assert preview["retention_hours"] == 0
    assert len(preview["files"]) == 1
    assert preview["bytes"] > 0
    assert sorted(p.name for p in path.glob("*.parquet")) == before  # nothing deleted


def test_vacuum_preview_of_missing_table_is_empty(manager):
    assert manager.vacuum_preview("stage3_analytics") == {
        "table": "stage3_analytics", "retention_hours": 168, "files": [], "bytes": 0,
    }


def test_retention_tiers_from_config(manager):
    assert manager.retention_hours_for("stage1_errors") == 720
    assert manager.retention_hours_for("stage2_errors") == 720
    assert manager.retention_hours_for("stage2_page_analysis") == 168
    manager.table_retention_hours = {"default": 200, "stage1_errors": 48}
    assert manager.retention_hours_for("stage1_errors") == 48
    assert manager.retention_hours_for("anything") == 200


def test_vacuum_all_tables_uses_tiers(manager, monkeypatch):
    calls = []
    monkeypatch.setattr(
        manager, "_vacuum_table", lambda t, h, enforce_retention_duration=True: calls.append((t, h, enforce_retention_duration))
    )
    manager.table_retention_hours = {"default": 168, "stage1_errors": 720, "stage2_errors": 72}
    manager.vacuum_all_tables()
    got = {t: (h, e) for t, h, e in calls}
    assert got["stage1_errors"] == (720, True)
    assert got["stage2_errors"] == (72, False)  # below 168h: safety check off, on purpose
    assert got["seed_urls"] == (168, True)
    calls.clear()
    manager.vacuum_all_tables(retention_hours=500)  # explicit override for every table
    assert {h for _, h, _ in calls} == {500}


def test_vacuum_all_tables_dry_run_returns_previews(manager, monkeypatch):
    monkeypatch.setattr(manager, "_vacuum_table", lambda *a, **k: pytest.fail("dry run must not vacuum"))
    previews = manager.vacuum_all_tables(dry_run=True)
    assert {p["table"] for p in previews} == set(manager.tables)


def test_file_dlq_cleanup_runs_with_configured_days(manager, monkeypatch):
    seen = {}

    class FakeDLQ:
        def cleanup_old(self, days):
            seen["days"] = days
            return 3

    import src.utils.dead_letter_queue as dlq

    monkeypatch.setattr(dlq, "DeadLetterQueue", FakeDLQ)
    manager.dlq_retention_days = 14
    assert manager._cleanup_file_dlq() == 3
    assert seen["days"] == 14
    manager.dlq_retention_days = 0
    assert manager._cleanup_file_dlq() == 0


def test_idle_maintenance_runs_dlq_and_offsite_gc(manager, monkeypatch):
    ran = []
    monkeypatch.setattr(manager, "gc_all_queues", lambda *a, **k: ran.append("queues"))
    monkeypatch.setattr(manager, "_cleanup_file_dlq", lambda: ran.append("dlq"))
    monkeypatch.setattr(manager, "gc_quarantine_tables", lambda **k: ran.append("errors"))
    import src.lakehouse.offsite_review as orv

    monkeypatch.setattr(orv.OffsiteReview, "gc", lambda self, **k: ran.append("offsite"))
    # monotonic() starts at boot; a literal 0.0 is still inside the interval
    # on a runner with under a minute of uptime (same fix as #754).
    manager.queue_retention_hours = 168
    manager.queue_gc_interval_s = 60
    manager._last_queue_gc = time.monotonic() - manager.queue_gc_interval_s - 1
    manager._maybe_gc_queues()
    assert ran == ["queues", "dlq", "errors", "offsite"]


def _dates(path):
    return DeltaTable(str(path)).to_pyarrow_table(columns=["date"])["date"].to_pylist()


def test_quarantine_gc_drops_old_error_days_and_leaves_silver(manager, monkeypatch):
    write_deltalake(str(manager.tables["stage1_errors"]), pa.table({
        "url": ["old", "boundary", "fresh", "undated"],
        "date": pa.array(["2026-01-01", "2026-09-08", "2026-10-01", None], type=pa.string()),
    }))
    write_deltalake(str(manager.tables["stage2_errors"]), pa.table({
        "url": ["old2", "fresh2"],
        "date": pa.array(["2026-10-01", "2026-10-07"], type=pa.string()),
    }))
    write_deltalake(str(manager.tables["stage2_page_analysis"]), pa.table({
        "url": ["silver"],
        "date": pa.array(["2020-01-01"], type=pa.string()),
    }))
    vacuumed = []
    monkeypatch.setattr(
        manager, "_vacuum_table",
        lambda table, hours, enforce_retention_duration=True: vacuumed.append(
            (table, hours, enforce_retention_duration)
        ),
    )
    manager.table_retention_hours = {"default": 168, "stage1_errors": 720, "stage2_errors": 72}
    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)

    preview = {r["table"]: r for r in manager.gc_quarantine_tables(dry_run=True, now=now)}
    assert preview["stage1_errors"]["matched"] == 1 and preview["stage1_errors"]["deleted"] == 0
    assert preview["stage1_errors"]["vacuumed"] is False
    assert preview["stage2_errors"]["matched"] == 1
    assert _dates(manager.tables["stage1_errors"]) == ["2026-01-01", "2026-09-08", "2026-10-01", None]

    applied = {r["table"]: r for r in manager.gc_quarantine_tables(now=now)}
    assert applied["stage1_errors"]["deleted"] == 1
    assert applied["stage1_errors"]["vacuumed"] is True
    assert _dates(manager.tables["stage1_errors"]) == ["2026-09-08", "2026-10-01", None]
    # 72h before 2026-10-08 is 2026-10-05, so 2026-10-01 goes and 2026-10-07 stays.
    assert _dates(manager.tables["stage2_errors"]) == ["2026-10-07"]
    assert _dates(manager.tables["stage2_page_analysis"]) == ["2020-01-01"]
    assert ("stage2_page_analysis", 168, True) not in vacuumed
    assert ("stage1_errors", 720, True) in vacuumed
    assert ("stage2_errors", 72, False) in vacuumed  # under 168h: safety check off


def test_quarantine_gc_hold_and_missing_date_do_not_delete_rows(manager, monkeypatch):
    write_deltalake(str(manager.tables["stage1_errors"]), pa.table({
        "url": ["keep"], "date": pa.array(["2020-01-01"], type=pa.string()),
    }))
    write_deltalake(str(manager.tables["stage2_errors"]), pa.table({"url": ["nodate"], "error": ["x"]}))
    vacuumed = []
    monkeypatch.setattr(
        manager, "_vacuum_table",
        lambda table, hours, enforce_retention_duration=True: vacuumed.append(table),
    )
    manager.table_retention_hours = {"default": 168, "stage1_errors": 0, "stage2_errors": 720}
    now = datetime(2026, 10, 8, tzinfo=UTC)
    by_table = {r["table"]: r for r in manager.gc_quarantine_tables(now=now)}
    assert by_table["stage1_errors"]["skipped"] == "retention disabled"
    assert by_table["stage1_errors"]["vacuumed"] is False
    assert _dates(manager.tables["stage1_errors"]) == ["2020-01-01"]
    assert by_table["stage2_errors"]["skipped"] == "no date column"
    assert by_table["stage2_errors"]["deleted"] == 0
    assert by_table["stage2_errors"]["vacuumed"] is True
    assert vacuumed == ["stage2_errors"]
    rows = DeltaTable(str(manager.tables["stage2_errors"])).to_pyarrow_table().to_pylist()
    assert rows == [{"url": "nodate", "error": "x"}]


def test_quarantine_gc_isolates_one_table_failure(manager, monkeypatch):
    def boom(table, hours, *, dry_run, now):
        if table == "stage1_errors":
            raise RuntimeError("disk on fire")
        return {"table": table, "retention_hours": hours, "matched": 0, "deleted": 0,
                "vacuumed": False, "skipped": "ok"}

    monkeypatch.setattr(manager, "_gc_one_quarantine_table", boom)
    manager.table_retention_hours = {"default": 168, "stage1_errors": 720, "stage2_errors": 720}
    results = manager.gc_quarantine_tables()
    assert results[0]["skipped"].startswith("error: disk on fire")
    assert results[1]["table"] == "stage2_errors" and results[1]["skipped"] == "ok"


# ---------------------------------------------------------------------- #878
def _offsite(manager, rows):
    write_deltalake(str(manager.tables["stage1_offsite_candidates"]), pa.table({
        "source_page": [r[0] for r in rows],
        "external_url": [r[1] for r in rows],
        "anchor_text": ["x"] * len(rows),
        "context": ["c"] * len(rows),
        "discovered_at": [r[2] for r in rows],
    }), mode="append")


def _iso(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat()


class FakeSeeds:
    def __init__(self):
        self.calls = []

    def add_urls_to_seeds(self, urls, source_url, source_spider, **kw):
        self.calls.append(list(urls))
        return {"seed_inserted": len(urls)}


def test_summary_aggregates_sightings_and_defaults_to_pending(manager):
    _offsite(manager, [("p1", "https://a.org/", _iso(3)), ("p2", "https://a.org/", _iso(1)), ("p1", "https://b.org/", _iso(2))])
    review = OffsiteReview(manager)
    s = {e["external_url"]: e for e in review.summary()}
    assert s["https://a.org/"]["sightings"] == 2
    assert s["https://a.org/"]["status"] == "pending"
    assert s["https://a.org/"]["first_seen"] == _iso(3) and s["https://a.org/"]["last_seen"] == _iso(1)
    assert review.counts() == {"pending": 2, "accepted": 0, "rejected": 0}


def test_set_status_adds_columns_and_updates_every_sighting(manager):
    _offsite(manager, [("p1", "https://a.org/", _iso(3)), ("p2", "https://a.org/", _iso(1)), ("p1", "https://o'brien.org/", _iso(2))])
    review = OffsiteReview(manager)
    assert review.set_status(["https://a.org/", "https://o'brien.org/"], "accepted", now=NOW) == 3
    rows = DeltaTable(str(manager.tables["stage1_offsite_candidates"])).to_pyarrow_table().to_pylist()
    assert {r["status"] for r in rows} == {"accepted"}
    assert {r["reviewed_at"] for r in rows} == {NOW.isoformat()}
    with pytest.raises(ValueError):
        review.set_status(["https://a.org/"], "maybe")


def test_promote_accepted_is_idempotent(manager):
    _offsite(manager, [("p1", "https://a.org/", _iso(3)), ("p1", "https://b.org/", _iso(3))])
    review = OffsiteReview(manager)
    review.set_status(["https://a.org/"], "accepted", now=NOW)
    seeds = FakeSeeds()
    assert review.promote_accepted(seeds, now=NOW) == ["https://a.org/"]
    assert seeds.calls == [["https://a.org/"]]
    assert review.promote_accepted(seeds, now=NOW) == []  # already stamped promoted_at
    assert len(seeds.calls) == 1


def test_gc_drops_stale_pending_and_old_rejected_but_keeps_accepted(manager):
    _offsite(manager, [
        ("p", "https://old-pending.org/", _iso(40)),
        ("p", "https://new-pending.org/", _iso(2)),
        ("p", "https://rejected.org/", _iso(40)),
        ("p", "https://accepted.org/", _iso(40)),
    ])
    review = OffsiteReview(manager)
    review.set_status(["https://rejected.org/"], "rejected", now=NOW - timedelta(days=10))
    review.set_status(["https://accepted.org/"], "accepted", now=NOW - timedelta(days=10))

    assert review.gc(now=NOW, dry_run=True) == {"pending": 1, "rejected": 1}
    assert review.gc(now=NOW) == {"pending": 1, "rejected": 1}
    left = sorted(e["external_url"] for e in review.summary())
    assert left == ["https://accepted.org/", "https://new-pending.org/"]


def test_gc_before_any_review_has_no_status_column(manager):
    _offsite(manager, [("p", "https://old.org/", _iso(40)), ("p", "https://new.org/", _iso(1))])
    assert OffsiteReview(manager).gc(now=NOW) == {"pending": 1, "rejected": 0}
    assert [e["external_url"] for e in OffsiteReview(manager).summary()] == ["https://new.org/"]


def test_gc_without_table_is_noop(manager):
    assert OffsiteReview(manager).gc(now=NOW) == {"pending": 0, "rejected": 0}


def test_cli_lake_vacuum_preview_is_default(tmp_path, monkeypatch, capsys):
    import cli

    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "lake"))
    m = LakehouseManager(start_workers=False)
    _table_with_garbage(m.tables["stage2_page_analysis"])
    n_before = len(list(m.tables["stage2_page_analysis"].glob("*.parquet")))
    args = type("A", (), {"apply": False, "table": "stage2_page_analysis", "retention_hours": 0, "verbose": True})()
    cli.cmd_lake_vacuum(args)
    out = capsys.readouterr().out
    assert "stage2_page_analysis: 1 files" in out and "older than 0h" in out and "pass --apply" in out
    assert len(list(m.tables["stage2_page_analysis"].glob("*.parquet"))) == n_before
