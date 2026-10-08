"""#372: row counts come from Delta log stats, never from materializing rows."""

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from src.lakehouse import lakehouse_manager as lm
from src.lakehouse.lakehouse_manager import LakehouseManager, metadata_row_count


@pytest.fixture
def mgr(tmp_path):
    m = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    yield m
    m.shutdown_event.set()


def _no_materialize(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("count() must not materialize table data")

    monkeypatch.setattr(DeltaTable, "to_pyarrow_table", boom)
    monkeypatch.setattr(DeltaTable, "to_pandas", boom)


def test_count_uses_log_stats_across_appends_and_partitions(mgr, monkeypatch):
    for i in range(3):
        rows = [{"url": f"https://d{j}.example.edu/{i}", "n": j} for j in range(4)]
        assert mgr._write_sync("stage2_page_analysis", rows, "append")  # partitioned by domain
    _no_materialize(monkeypatch)
    assert mgr.count("stage2_page_analysis") == 12


def test_count_reflects_overwrite_not_tombstoned_files(mgr, monkeypatch):
    mgr._write_sync("t372", [{"url": str(i)} for i in range(10)], "append")
    mgr._write_sync("t372", [{"url": "only"}], "overwrite")
    _no_materialize(monkeypatch)
    assert mgr.count("t372") == 1


def test_missing_table_counts_zero(mgr):
    assert mgr.count("stage2_queue") == 0


def test_falls_back_to_parquet_footers_when_stats_missing(mgr, monkeypatch):
    mgr._write_sync("t372b", [{"url": str(i)} for i in range(7)], "append")
    monkeypatch.setattr(lm, "metadata_row_count", lambda table: None)
    _no_materialize(monkeypatch)
    assert mgr.count("t372b") == 7


def test_metadata_row_count_none_when_a_file_lacks_stats(tmp_path):
    path = str(tmp_path / "t")
    write_deltalake(path, pa.table({"a": [1, 2]}))

    class Stub:
        def file_uris(self):
            return ["x", "y"]

        def get_add_actions(self, flatten=True):
            return pa.record_batch({"path": ["x", "y"], "num_records": pa.array([2, None], pa.int64())})

    assert metadata_row_count(DeltaTable(path)) == 2
    assert metadata_row_count(Stub()) is None


def test_delta_helper_get_row_count_goes_through_metadata(tmp_path, monkeypatch):
    from src.utils.delta import DeltaHelper

    helper = DeltaHelper(base_path=tmp_path / "lake")
    helper.manager._write_sync("t372c", [{"url": str(i)} for i in range(5)], "append")
    _no_materialize(monkeypatch)
    assert helper.get_row_count("t372c") == 5
    assert helper.count("missing_table") == 0
