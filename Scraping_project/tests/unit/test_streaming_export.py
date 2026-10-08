"""Streaming, partition-scoped, size-capped Delta export (#373)."""

import json

import pyarrow as pa
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq
import pytest
from deltalake import DeltaTable, write_deltalake

import src.lakehouse.lakehouse_manager as lm
from src.lakehouse.lakehouse_manager import DeltaLakeManager

TABLE = "stage1_discovery"


def _rows(start, n, day):
    return pa.table(
        {
            "url": [f"https://uconn.edu/p/{i}" for i in range(start, start + n)],
            "n": list(range(start, start + n)),
            "crawl_date": [day] * n,
        }
    )


@pytest.fixture
def manager(tmp_path):
    mgr = DeltaLakeManager(base_path=str(tmp_path / "lake"), start_workers=False)
    yield mgr
    mgr.shutdown()


@pytest.fixture
def filled(manager):
    path = str(manager.get_table_path(TABLE))
    write_deltalake(path, _rows(0, 1500, "2026-10-06"), partition_by=["crawl_date"])
    write_deltalake(path, _rows(1500, 1000, "2026-10-07"), mode="append", partition_by=["crawl_date"])
    return manager


def test_csv_roundtrip(filled, tmp_path):
    out = tmp_path / "out" / "t.csv"
    result = filled.export(TABLE, str(out), batch_size=300)
    back = pa_csv.read_csv(out)
    assert result["rows"] == back.num_rows == 2500
    assert result["files"] == [str(out)] and result["output"] == str(out)
    assert sorted(back.column("n").to_pylist()) == list(range(2500))
    assert result["columns"] == 3


def test_json_lines_roundtrip(filled, tmp_path):
    out = tmp_path / "t.json"
    result = filled.export(TABLE, str(out), format="json", batch_size=400)
    lines = out.read_text().splitlines()
    assert result["rows"] == len(lines) == 2500
    assert sorted(json.loads(line)["n"] for line in lines) == list(range(2500))


def test_parquet_roundtrip(filled, tmp_path):
    out = tmp_path / "t.parquet"
    result = filled.export(TABLE, str(out), format="parquet", batch_size=512)
    assert result["rows"] == pq.read_table(out).num_rows == 2500


def test_partition_scoped_export(filled, tmp_path):
    out = tmp_path / "day.csv"
    result = filled.export(TABLE, str(out), filters=[("crawl_date", "=", "2026-10-07")], columns=["url", "n"])
    back = pa_csv.read_csv(out)
    assert result["rows"] == back.num_rows == 1000
    assert back.column_names == ["url", "n"]
    assert min(back.column("n").to_pylist()) == 1500


def test_max_rows_per_file_rolls_over(filled, tmp_path):
    out = tmp_path / "t.csv"
    result = filled.export(TABLE, str(out), batch_size=700, max_rows_per_file=1000)
    names = [p.split("/")[-1] for p in result["files"]]
    assert names == ["t.part-00000.csv", "t.part-00001.csv", "t.part-00002.csv"]
    counts = [pa_csv.read_csv(p).num_rows for p in result["files"]]
    assert counts == [1000, 1000, 500] and result["rows"] == 2500
    assert not out.exists()


def test_max_bytes_per_file_rolls_over(filled, tmp_path):
    out = tmp_path / "t.parquet"
    result = filled.export(TABLE, str(out), format="parquet", batch_size=200, max_bytes_per_file=4096)
    assert len(result["files"]) > 1
    assert sum(pq.read_table(p).num_rows for p in result["files"]) == 2500


def test_config_limits_apply(filled, tmp_path, monkeypatch):
    cfg = {"export.batch_size": 250, "export.max_rows_per_file": 2000}

    class _Cfg:
        def get(self, key, default=None):
            return cfg.get(key, default)

    monkeypatch.setattr(lm.Config, "get_instance", classmethod(lambda cls: _Cfg()))
    result = filled.export(TABLE, str(tmp_path / "t.csv"))
    assert [pa_csv.read_csv(p).num_rows for p in result["files"]] == [2000, 500]


def test_empty_table_exports_empty_file(manager, tmp_path):
    out = tmp_path / "empty.csv"
    result = manager.export(TABLE, str(out))
    assert out.exists() and result["rows"] == 0 and result["columns"] == 0


def test_unsupported_format_writes_nothing(filled, tmp_path):
    out = tmp_path / "t.xml"
    with pytest.raises(ValueError):
        filled.export(TABLE, str(out), format="xml")
    assert not out.exists()


def test_export_streams_without_materializing(manager, tmp_path, monkeypatch):
    path = str(manager.get_table_path(TABLE))
    for i in range(20):
        write_deltalake(path, _rows(i * 10_000, 10_000, "2026-10-07"), mode="append")
    full_bytes = DeltaTable(path).to_pyarrow_table().nbytes

    def _boom(*args, **kwargs):
        raise AssertionError("export must not materialize the whole table")

    monkeypatch.setattr(DeltaTable, "to_pyarrow_table", _boom)
    monkeypatch.setattr(DeltaTable, "to_pandas", _boom)

    seen = []
    baseline = pa.total_allocated_bytes()
    original = lm._ExportSink.write

    def _spy(self, batch):
        seen.append((batch.num_rows, pa.total_allocated_bytes() - baseline))
        original(self, batch)

    monkeypatch.setattr(lm._ExportSink, "write", _spy)
    result = manager.export(TABLE, str(tmp_path / "big.csv"), batch_size=2_000)

    assert result["rows"] == 200_000
    assert len(seen) >= 100
    assert max(rows for rows, _ in seen) <= 2_000
    assert max(alloc for _, alloc in seen) < full_bytes / 2  # materializing would be >= 1x
