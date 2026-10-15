"""TableWatcher: live polling of one Delta table (spec: Live reader)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pytest

deltalake = pytest.importorskip("deltalake")
from deltalake import write_deltalake  # noqa: E402

from ccfeed.delta import TableWatcher  # noqa: E402
from ccfeed.stages import parse_ts  # noqa: E402

pytestmark = pytest.mark.delta

BASE = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
WANTED = ("url", "domain", "depth", "status_code", "parent_url", "_ingestion_time")


def iso(i: int, form: str = "+00:00") -> str:
    t = BASE + timedelta(seconds=i)
    if form == "naive":
        return t.replace(tzinfo=None).isoformat()
    if form == "Z":
        return t.replace(tzinfo=None).isoformat() + "Z"
    return t.isoformat()


def rows(start: int, n: int, form: str = "+00:00") -> pa.Table:
    """Rows shaped like the real stage1_discovery table (ISO string times, extra columns)."""
    return pa.table({
        "url": [f"https://example.edu/p{i}" for i in range(start, start + n)],
        "value": pa.array([None] * n, type=pa.int64()),
        "_ingestion_time": [iso(i, form) for i in range(start, start + n)],
        "_stage": ["stage1_discovery"] * n,
        "domain": ["example.edu" if i % 2 else "news.example.edu" for i in range(start, start + n)],
        "depth": pa.array([i % 4 for i in range(start, start + n)], type=pa.int64()),
        "status_code": pa.array([200] * n, type=pa.int64()),
    })


def append(path: Path, table: pa.Table, mode: str = "append") -> None:
    write_deltalake(str(path), table, mode=mode, partition_by=["domain"])


def test_missing_path_reports_missing(tmp_path):
    r = TableWatcher(tmp_path / "nope", WANTED).poll()
    assert r.status == "missing" and r.count is None and r.new_rows == []


def test_first_poll_counts_without_events_and_seeds_recent(tmp_path):
    path = tmp_path / "t"
    append(path, rows(0, 60))
    w = TableWatcher(path, WANTED)
    r = w.poll()
    assert r.status == "ok"
    assert r.count == 60
    assert r.version == 0
    assert r.new_rows == []
    assert r.last_write == pytest.approx(parse_ts(iso(59)))
    assert len(w.recent_rows) == 50
    assert w.recent_rows[0]["url"] == "https://example.edu/p59"  # newest first


def test_append_yields_only_new_rows_with_projected_fields(tmp_path):
    path = tmp_path / "t"
    append(path, rows(0, 10))
    w = TableWatcher(path, WANTED)
    w.poll()
    append(path, rows(10, 3))
    r = w.poll()
    assert [x["url"] for x in r.new_rows] == [f"https://example.edu/p{i}" for i in (10, 11, 12)]
    assert set(r.new_rows[0]) <= set(WANTED) | {"_t"}
    assert r.new_rows[0]["domain"] in ("example.edu", "news.example.edu")  # partition value restored
    assert r.new_rows[0]["_t"] == pytest.approx(parse_ts(iso(10)))
    assert "parent_url" not in r.new_rows[0]  # wanted but absent on disk
    assert r.count == 13
    assert w.poll().new_rows == []  # nothing new, nothing emitted


def test_rewrite_of_old_rows_emits_nothing(tmp_path):
    path = tmp_path / "t"
    append(path, rows(0, 20))
    w = TableWatcher(path, WANTED)
    w.poll()
    deltalake.DeltaTable(str(path)).optimize.compact()  # same rows, new files
    append(path, rows(0, 20), mode="overwrite")         # and again via overwrite
    r = w.poll()
    assert r.new_rows == []
    assert r.count == 20


def test_file_removed_after_listing_is_skipped(tmp_path, monkeypatch):
    path = tmp_path / "t"
    append(path, rows(0, 2))
    w = TableWatcher(path, WANTED)
    w.poll()
    append(path, rows(100, 4))
    real_read = w._read_file
    calls = []

    def flaky(file_path, columns):
        calls.append(file_path)
        if len(calls) == 1:
            raise FileNotFoundError(file_path)  # vacuumed between listing and reading
        return real_read(file_path, columns)

    monkeypatch.setattr(w, "_read_file", flaky)
    r = w.poll()
    assert r.status == "ok"
    assert 0 < len(r.new_rows) < 4  # the readable partition still came through


def test_per_poll_cap_keeps_the_newest_500(tmp_path):
    path = tmp_path / "t"
    append(path, rows(0, 1))
    w = TableWatcher(path, WANTED)
    w.poll()
    append(path, rows(1, 700))
    r = w.poll()
    assert len(r.new_rows) == 500
    assert r.new_rows[-1]["url"] == "https://example.edu/p700"
    assert r.new_rows[0]["url"] == "https://example.edu/p201"


def test_corrupt_log_reports_the_error_type(tmp_path):
    path = tmp_path / "t"
    append(path, rows(0, 3))
    log = next((path / "_delta_log").glob("*.json"))
    log.write_text("{not json")
    r = TableWatcher(path, WANTED).poll()
    assert r.status == "error"
    assert r.error and r.error.split(":")[0].endswith(("Error", "Exception"))


def test_table_created_after_start_streams_its_rows(tmp_path):
    path = tmp_path / "t"
    w = TableWatcher(path, WANTED)
    assert w.poll().status == "missing"
    append(path, rows(0, 5))  # written while we watched, so these are live events
    r = w.poll()
    assert r.status == "ok" and r.count == 5
    assert [x["url"] for x in r.new_rows] == [f"https://example.edu/p{i}" for i in range(5)]
    assert w.poll().new_rows == []


@pytest.mark.parametrize("form", ["naive", "Z", "+00:00"])
def test_every_timestamp_form_orders_and_dedupes(tmp_path, form):
    path = tmp_path / "t"
    append(path, rows(0, 5, form))
    w = TableWatcher(path, WANTED)
    assert w.poll().last_write == pytest.approx(parse_ts(iso(4)))
    append(path, rows(5, 2, form))
    assert [x["url"] for x in w.poll().new_rows] == ["https://example.edu/p5", "https://example.edu/p6"]


def test_table_read_for_queries_includes_partition_columns(tmp_path):
    path = tmp_path / "t"
    append(path, rows(0, 6))
    w = TableWatcher(path, WANTED)
    w.poll()
    t = w.table(["url", "domain", "nonexistent"])
    assert t.num_rows == 6
    assert set(t.column_names) == {"url", "domain"}
    hit = w.table(["url", "depth"], url="https://example.edu/p3")
    assert hit.to_pylist() == [{"url": "https://example.edu/p3", "depth": 3}]


def test_corrupt_new_data_file_marks_the_table_unreadable_but_keeps_readable_rows(tmp_path):
    path = tmp_path / "t"
    append(path, rows(0, 10))
    w = TableWatcher(path, WANTED)
    w.poll()
    before = {p for p in path.rglob("*.parquet")}
    append(path, rows(100, 4))  # two partitions, two new files
    new_files = sorted({p for p in path.rglob("*.parquet")} - before)
    assert len(new_files) == 2
    new_files[0].write_bytes(b"PAR1 this is not parquet PAR1")
    r = w.poll()
    assert r.status == "error"
    assert r.error and r.error.split(":")[0].endswith(("Error", "Invalid", "Exception")), r.error
    assert len(r.new_rows) == 2  # the readable partition still streams
    assert r.count == 14          # counts come from stats, not from reading
    again = w.poll()
    assert again.status == "error" and again.new_rows == []  # still broken, retried, nothing replayed


def test_file_already_corrupt_at_startup_stays_reported(tmp_path):
    path = tmp_path / "t"
    append(path, rows(0, 6))
    next(path.rglob("*.parquet")).write_bytes(b"PAR1 garbage PAR1")
    w = TableWatcher(path, WANTED)
    assert w.poll().status == "error"
    assert w.poll().status == "error"  # not silently "ok" on the next poll
    assert w.poll().count == 6
