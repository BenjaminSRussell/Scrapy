"""Partition prune and time-travel read smoke (#697).

A two-partition ``stage1_discovery`` table is written only under pytest's temp
lake. ``LakehouseManager.read`` must return one partition or one snapshot, and
it must forward those choices as Delta query options. A missing version and an
invalid partition predicate fail in a defined way instead of returning another
snapshot.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest
from deltalake import DeltaTable
from deltalake.exceptions import DeltaError

from src.lakehouse.lakehouse_manager import LakehouseManager, MissingColumnsError

pytestmark = [pytest.mark.delta, pytest.mark.unit]

TABLE = "stage1_discovery"
A = "a.edu"
B = "b.edu"
PARTITION = [("domain", "=", A)]


@pytest.fixture
def lake(tmp_path):
    mgr = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    assert mgr.write(
        TABLE,
        [
            {"url": "https://a.edu/1", "url_hash": "a1"},
            {"url": "https://b.edu/1", "url_hash": "b1"},
        ],
        async_write=False,
    )
    assert mgr.write(TABLE, [{"url": "https://a.edu/2", "url_hash": "a2"}], async_write=False)
    yield mgr
    mgr.shutdown()


def _urls(rows):
    return sorted(row["url"] for row in rows)


def _capture_read_options(monkeypatch):
    """Record version, filters, partitions, and columns passed to Delta."""
    calls = []
    real = DeltaTable.to_pyarrow_table

    def spy(self, *args, **kwargs):
        calls.append(
            {
                "version": self.version(),
                "filters": kwargs.get("filters"),
                "partitions": kwargs.get("partitions"),
                "columns": kwargs.get("columns"),
            }
        )
        return real(self, *args, **kwargs)

    monkeypatch.setattr(DeltaTable, "to_pyarrow_table", spy)
    return calls


def test_partition_filter_returns_only_that_partition(lake, monkeypatch):
    calls = _capture_read_options(monkeypatch)
    rows = lake.read(TABLE, filters=PARTITION, columns=["url", "domain"])
    assert _urls(rows) == ["https://a.edu/1", "https://a.edu/2"]
    assert {row["domain"] for row in rows} == {A}
    assert all(set(row) == {"url", "domain"} for row in rows)

    latest = DeltaTable(str(lake.get_table_path(TABLE))).version()
    assert calls == [
        {"version": latest, "filters": PARTITION, "partitions": None, "columns": ["url", "domain"]}
    ]

    kept = DeltaTable(str(lake.get_table_path(TABLE))).file_uris(PARTITION)
    everything = DeltaTable(str(lake.get_table_path(TABLE))).file_uris()
    assert kept and len(kept) < len(everything)
    assert all(f"/domain={A}/" in uri for uri in kept)
    assert all(f"/domain={B}/" not in uri for uri in kept)


def test_partition_filter_does_not_scan_other_partitions(lake):
    """A broken sibling partition is invisible to a pruned read and fatal to a full scan."""
    sibling = list(Path(lake.get_table_path(TABLE)).glob(f"domain={B}/*.parquet"))
    assert len(sibling) == 1
    sibling[0].write_bytes(b"not a parquet file")

    with pytest.raises(pa.ArrowInvalid, match=f"domain={B}"):
        lake.read(TABLE)
    assert _urls(lake.read(TABLE, filters=PARTITION, columns=["url"])) == [
        "https://a.edu/1",
        "https://a.edu/2",
    ]


def test_time_travel_returns_the_prior_snapshot(lake, monkeypatch):
    calls = _capture_read_options(monkeypatch)
    assert _urls(lake.read(TABLE, version=0)) == ["https://a.edu/1", "https://b.edu/1"]
    prior = lake.read(TABLE, version=0, filters=PARTITION, columns=["url"])
    assert prior == [{"url": "https://a.edu/1"}]
    assert calls == [
        {"version": 0, "filters": None, "partitions": None, "columns": None},
        {"version": 0, "filters": PARTITION, "partitions": None, "columns": ["url"]},
    ]
    assert _urls(lake.read(TABLE)) == ["https://a.edu/1", "https://a.edu/2", "https://b.edu/1"]


def test_missing_version_raises(lake):
    with pytest.raises(DeltaError, match="99"):
        lake.read(TABLE, version=99)
    assert _urls(lake.read(TABLE)) == ["https://a.edu/1", "https://a.edu/2", "https://b.edu/1"]


def test_unknown_partition_value_is_empty(lake):
    assert lake.read(TABLE, filters=[("domain", "=", "nope.edu")], columns=["url"]) == []


def test_unknown_partition_column_is_a_clear_error(lake):
    with pytest.raises(MissingColumnsError) as exc:
        lake.read(TABLE, filters=[("not_a_partition", "=", A)])
    err = exc.value
    assert err.missing == ["not_a_partition"]
    assert err.table_name == TABLE
    assert "domain" in err.available
    assert "at version" not in str(err)


def test_invalid_partition_operator_is_rejected(lake):
    with pytest.raises(ValueError, match="operator"):
        lake.read(TABLE, filters=[("domain", "like", "a%")])
