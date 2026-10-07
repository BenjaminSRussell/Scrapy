"""#274: honor delta_lake.checkpoint_interval and create real Delta log checkpoints."""

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from src.lakehouse import lakehouse_manager as lm
from src.lakehouse.lakehouse_manager import LakehouseManager, last_checkpoint_version

TABLE = "stage2_queue"


@pytest.fixture
def mgr(tmp_path):
    m = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    m.checkpoint_interval = 3
    yield m
    m.shutdown_event.set()


def _checkpoint_files(path):
    return sorted(p.name for p in (path / "_delta_log").glob("*.checkpoint.parquet"))


def _write(m, i):
    assert m._write_sync(TABLE, [{"url": f"https://e.com/{i}", "status": "pending"}], "append") is True


def test_new_table_gets_configured_interval_and_auto_checkpoints(mgr):
    path = mgr.get_table_path(TABLE)
    for i in range(4):  # versions 0..3; interval 3 => checkpoint at version 2
        _write(mgr, i)

    dt = DeltaTable(str(path))
    assert dt.metadata().configuration[lm.CHECKPOINT_INTERVAL_PROPERTY] == "3"
    assert "00000000000000000002.checkpoint.parquet" in _checkpoint_files(path)
    assert len(dt.to_pyarrow_table()) == 4


def test_existing_table_without_property_is_synced(mgr):
    path = mgr.get_table_path(TABLE)
    write_deltalake(str(path), pa.table({"url": ["https://e.com/old"], "status": ["pending"]}))
    assert lm.CHECKPOINT_INTERVAL_PROPERTY not in DeltaTable(str(path)).metadata().configuration

    _write(mgr, 1)

    conf = DeltaTable(str(path)).metadata().configuration
    assert conf[lm.CHECKPOINT_INTERVAL_PROPERTY] == "3"


def test_checkpoint_writes_real_checkpoint_and_is_idempotent(mgr):
    mgr.checkpoint_interval = 100  # rule out the automatic one
    path = mgr.get_table_path(TABLE)
    for i in range(2):
        _write(mgr, i)
    version = DeltaTable(str(path)).version()
    assert _checkpoint_files(path) == []

    before = (
        lm.DELTA_CHECKPOINTS.labels(table=TABLE, outcome="created")._value.get()
        if lm.DELTA_CHECKPOINTS is not None
        else None
    )
    mgr.checkpoint(timeout=1)  # what shutdown() calls

    assert f"{version:020d}.checkpoint.parquet" in _checkpoint_files(path)
    assert last_checkpoint_version(path) == version
    if before is not None:
        assert lm.DELTA_CHECKPOINTS.labels(table=TABLE, outcome="created")._value.get() == before + 1
    # Table still loads from the checkpoint with all rows.
    assert len(DeltaTable(str(path)).to_pyarrow_table()) == 2
    # Nothing new committed => no second checkpoint.
    assert mgr.create_checkpoints() == {}


def test_last_checkpoint_version_handles_missing_and_garbage(tmp_path):
    assert last_checkpoint_version(tmp_path) is None
    (tmp_path / "_delta_log").mkdir()
    (tmp_path / "_delta_log" / "_last_checkpoint").write_text("not json")
    assert last_checkpoint_version(tmp_path) is None
