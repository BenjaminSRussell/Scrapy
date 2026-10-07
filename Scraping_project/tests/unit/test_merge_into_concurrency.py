"""#169: merge_into is a real Delta MERGE; concurrent merges never lose rows."""

import threading

import pytest
from deltalake import DeltaTable

from src.lakehouse import lakehouse_manager as lm
from src.lakehouse.lakehouse_manager import LakehouseManager

TABLE = "seed_urls"


@pytest.fixture
def base(tmp_path):
    return str(tmp_path / "lake")


def _mgr(base):
    return LakehouseManager(base_path=base, start_workers=False)


def _rows(prefix, n, **extra):
    return [{"url_hash": f"{prefix}{i}", "url": f"https://e.com/{prefix}{i}", **extra} for i in range(n)]


def _snapshot(base):
    t = DeltaTable(f"{base}/{TABLE}").to_pyarrow_table().to_pylist()
    return {r["url_hash"]: r for r in t}


def test_upsert_updates_matched_and_inserts_new(base):
    m = _mgr(base)
    assert m.merge_into(TABLE, _rows("a", 3, source_spider="s1"), "url_hash", ["url", "source_spider"]) == 3
    changed = [{"url_hash": "a0", "url": "https://e.com/new", "source_spider": "s2"}] + _rows("b", 2, source_spider="s2")
    assert m.merge_into(TABLE, changed, "url_hash", ["url"]) == 3

    snap = _snapshot(base)
    assert set(snap) == {"a0", "a1", "a2", "b0", "b1"}
    assert snap["a0"]["url"] == "https://e.com/new"
    assert snap["a0"]["source_spider"] == "s1"  # not in update_columns
    assert snap["b0"]["source_spider"] == "s2"


def test_duplicate_keys_in_batch_last_wins(base):
    m = _mgr(base)
    m.merge_into(TABLE, _rows("a", 1), "url_hash", ["url"])
    batch = [{"url_hash": "a0", "url": "first"}, {"url_hash": "a0", "url": "last"}]
    assert m.merge_into(TABLE, batch, "url_hash", ["url"]) == 1
    assert _snapshot(base)["a0"]["url"] == "last"


def test_concurrent_merges_from_separate_writers_lose_nothing(base):
    _mgr(base).merge_into(TABLE, _rows("seed", 5), "url_hash", ["url"])
    n_writers, per_writer = 4, 15
    managers = [_mgr(base) for _ in range(n_writers)]  # separate instances = no shared lock
    barrier = threading.Barrier(n_writers)
    results: list[int] = []

    def work(i):
        barrier.wait()
        # disjoint inserts + every writer also updates the shared seed rows
        rows = _rows(f"w{i}_", per_writer) + [
            {"url_hash": f"seed{j}", "url": f"https://e.com/seed{j}?by={i}"} for j in range(5)
        ]
        results.append(managers[i].merge_into(TABLE, rows, "url_hash", ["url"]))

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n_writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(r == per_writer + 5 for r in results), results
    snap = _snapshot(base)
    assert len(snap) == 5 + n_writers * per_writer  # no lost inserts, no duplicate keys
    for i in range(n_writers):
        assert all(f"w{i}_{k}" in snap for k in range(per_writer))
    assert len(DeltaTable(f"{base}/{TABLE}").to_pyarrow_table()) == len(snap)


def test_failure_commits_nothing_and_never_overwrites(base, monkeypatch):
    m = _mgr(base)
    m.merge_into(TABLE, _rows("a", 3), "url_hash", ["url"])
    version = DeltaTable(f"{base}/{TABLE}").version()

    def boom(*a, **k):
        raise RuntimeError("merge exploded")

    monkeypatch.setattr(m, "_merge_rows", boom)
    monkeypatch.setattr(m, "_write_sync", lambda *a, **k: pytest.fail("no overwrite/append fallback"))
    before = lm.DELTA_MERGE_FAILURES.labels(table=TABLE)._value.get() if lm.DELTA_MERGE_FAILURES else None

    assert m.merge_into(TABLE, _rows("b", 2), "url_hash", ["url"]) == -1
    assert DeltaTable(f"{base}/{TABLE}").version() == version
    assert set(_snapshot(base)) == {"a0", "a1", "a2"}
    if before is not None:
        assert lm.DELTA_MERGE_FAILURES.labels(table=TABLE)._value.get() == before + 1


def test_unregistered_table_is_created(base):
    m = _mgr(base)
    assert m.merge_into("brand_new_table", _rows("x", 2), "url_hash", ["url"]) == 2
    assert len(DeltaTable(f"{base}/brand_new_table").to_pyarrow_table()) == 2
