"""#417: retracted URLs are soft-deleted in place by url_hash, with history kept.

``LakehouseManager.tombstone_urls`` sets ``is_deleted`` / ``deleted_at`` /
``deleted_reason`` on existing rows through an update-only MERGE. Offline: each
test gets its own lake under ``tmp_path``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from deltalake import DeltaTable, QueryBuilder
from pydantic import ValidationError

from src.lakehouse.lakehouse_manager import InMemoryBackend, LakehouseManager
from src.schemas import TombstoneRecord

pytestmark = pytest.mark.delta

TABLE = "stage2_queue"  # declared in config.yml, unpartitioned


def _hash(i: int) -> str:
    return f"{i:016x}"


def _rows(n: int) -> list[dict]:
    return [{"url_hash": _hash(i), "url": f"https://uconn.edu/{i}", "status": "pending"} for i in range(1, n + 1)]


@pytest.fixture
def lake(tmp_path):
    mgr = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    yield mgr
    mgr.shutdown(timeout=1)


def _by_hash(lake) -> dict[str, dict]:
    return {r["url_hash"]: r for r in lake.read(TABLE)}


def test_record_normalises_hash_and_requires_a_reason():
    rec = TombstoneRecord(url_hash="ABCDEF0123456789", reason="takedown")
    assert rec.url_hash == "abcdef0123456789"
    assert rec.deleted_at.utcoffset().total_seconds() == 0

    naive = TombstoneRecord(url_hash=_hash(1), reason="takedown", deleted_at=datetime(2026, 1, 2, 3))
    assert naive.deleted_at.utcoffset().total_seconds() == 0

    for bad in ({"url_hash": "not-hex-at-all!!"}, {"url_hash": "abc"}, {"reason": ""}):
        with pytest.raises(ValidationError):
            TombstoneRecord(**{"url_hash": _hash(1), "reason": "takedown", **bad})


def test_marks_matched_rows_and_keeps_pre_retraction_versions(lake):
    lake.merge_into(TABLE, _rows(3), "url_hash", ["url", "status"])
    table_path = str(lake.get_table_path(TABLE))
    before = DeltaTable(table_path).version()

    assert lake.tombstone_urls(TABLE, [TombstoneRecord(url_hash=_hash(2), reason="takedown")]) == 1

    live = _by_hash(lake)
    assert set(live) == {_hash(1), _hash(2), _hash(3)}  # soft delete: no rows removed
    assert live[_hash(2)]["is_deleted"] is True
    assert live[_hash(2)]["deleted_reason"] == "takedown"
    assert live[_hash(2)]["deleted_at"].endswith("+00:00")
    assert not live[_hash(1)].get("is_deleted") and not live[_hash(3)].get("is_deleted")

    # Time travel: the version before the retraction has no tombstone columns.
    old = DeltaTable(table_path, version=before).to_pyarrow_table().to_pylist()
    assert {r["url_hash"] for r in old} == set(live)
    assert all("is_deleted" not in r for r in old)


def test_repeat_retraction_keeps_the_first_deleted_at(lake):
    lake.merge_into(TABLE, _rows(2), "url_hash", ["url", "status"])
    first = TombstoneRecord(url_hash=_hash(1), reason="takedown", deleted_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert lake.tombstone_urls(TABLE, [first]) == 1
    later = TombstoneRecord(url_hash=_hash(1), reason="robots_disallowed", deleted_at=datetime(2026, 6, 1, tzinfo=UTC))
    assert lake.tombstone_urls(TABLE, [later]) == 0

    row = _by_hash(lake)[_hash(1)]
    assert row["deleted_at"] == first.deleted_at.isoformat()
    assert row["deleted_reason"] == "takedown"


def test_unknown_hash_inserts_nothing_and_a_missing_table_is_a_noop(lake):
    assert lake.tombstone_urls(TABLE, [{"url_hash": _hash(9), "reason": "takedown"}]) == 0
    assert lake.table_exists(TABLE) is False

    lake.merge_into(TABLE, _rows(2), "url_hash", ["url", "status"])
    assert lake.tombstone_urls(TABLE, [{"url_hash": _hash(9), "reason": "takedown"}]) == 0
    assert lake.count(TABLE) == 2
    assert not any(r.get("is_deleted") for r in lake.read(TABLE))


def test_plain_upsert_does_not_clear_the_mark(lake):
    lake.merge_into(TABLE, _rows(1), "url_hash", ["url", "status"])
    lake.tombstone_urls(TABLE, [TombstoneRecord(url_hash=_hash(1), reason="takedown")])
    done = [{"url_hash": _hash(1), "url": "https://uconn.edu/1", "status": "done"}]
    lake.merge_into(TABLE, done, "url_hash", ["status"])

    row = _by_hash(lake)[_hash(1)]
    assert row["status"] == "done"
    assert row["is_deleted"] is True


def test_failure_raises_instead_of_reporting_zero(lake, monkeypatch):
    lake.merge_into(TABLE, _rows(1), "url_hash", ["url", "status"])

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(lake, "_tombstone_rows", boom)
    with pytest.raises(RuntimeError, match="nothing committed"):
        lake.tombstone_urls(TABLE, [TombstoneRecord(url_hash=_hash(1), reason="takedown")])


def test_documented_consumer_filter_hides_retracted_rows(lake):
    lake.merge_into(TABLE, _rows(3), "url_hash", ["url", "status"])
    lake.tombstone_urls(TABLE, [TombstoneRecord(url_hash=_hash(1), reason="takedown")])
    # A row appended after the retraction has no is_deleted value (null): it is live.
    lake.write(TABLE, [{"url_hash": _hash(4), "url": "https://uconn.edu/4", "status": "pending"}], async_write=False)

    # The filter from docs/contracts/README.md, run as SQL against the Delta table.
    sql = f"SELECT url_hash FROM {TABLE} WHERE NOT COALESCE(is_deleted, FALSE) ORDER BY url_hash"
    delta = DeltaTable(str(lake.get_table_path(TABLE)))
    live = QueryBuilder().register(TABLE, delta).execute(sql).read_all()
    assert live.column("url_hash").to_pylist() == [_hash(2), _hash(3), _hash(4)]


def test_in_memory_backend_marks_rows_and_keeps_snapshots():
    mem = InMemoryBackend(history_depth=5)
    mem.write(TABLE, _rows(2), mode="append")

    assert mem.tombstone_urls(TABLE, [TombstoneRecord(url_hash=_hash(2), reason="takedown")]) == 1
    assert mem.tombstone_urls(TABLE, [TombstoneRecord(url_hash=_hash(2), reason="again")]) == 0
    current = {r["url_hash"]: r for r in mem.read(TABLE)}
    assert current[_hash(2)]["is_deleted"] is True
    assert current[_hash(2)]["deleted_reason"] == "takedown"

    previous = {r["url_hash"]: r for r in mem.read(TABLE, version=0)}
    assert "is_deleted" not in previous[_hash(2)]
    assert mem.tombstone_urls("no_such_table", [TombstoneRecord(url_hash=_hash(1), reason="x")]) == 0
