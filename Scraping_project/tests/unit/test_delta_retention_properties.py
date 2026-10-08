"""#493: Delta retention table properties come from config and drive vacuum."""

import logging

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from src.lakehouse import lakehouse_manager as lm
from src.lakehouse.lakehouse_manager import LakehouseManager
from src.lakehouse.table_properties import (
    DELETED_FILE_RETENTION_PROPERTY,
    LOG_RETENTION_PROPERTY,
    interval_hours,
    normalize_interval,
    retention_properties,
)

TABLE = "stage2_queue"


class FakeConfig:
    def __init__(self, data):
        self.data = data

    def get(self, key, default=None):
        return self.data.get(key, default)


@pytest.fixture
def mgr(tmp_path):
    m = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    m._retention_props = retention_properties(
        FakeConfig({"delta_lake.retention": {"log_retention": "14 days", "deleted_file_retention": "interval 72 hours"}})
    )
    yield m
    m.shutdown_event.set()


def _write(m, i, mode="append"):
    assert m._write_sync(TABLE, [{"url": f"https://e.com/{i}", "status": "pending"}], mode) is True


# ------------------------------------------------------------------ parsing
@pytest.mark.parametrize(
    "raw,want,hours",
    [
        ("interval 30 days", "interval 30 days", 720),
        ("7 days", "interval 7 days", 168),
        ("interval 1 week", "interval 1 week", 168),
        ("72 hours", "interval 72 hours", 72),
        (48, "interval 48 hours", 48),
        ("INTERVAL 2 Days", "interval 2 days", 48),
    ],
)
def test_normalize_interval(raw, want, hours):
    assert normalize_interval(raw) == want
    assert interval_hours(raw) == hours


@pytest.mark.parametrize("bad", ["forever", "7", "interval days", "-1 days", True, "7 fortnights"])
def test_bad_intervals_rejected(bad):
    with pytest.raises(ValueError):
        normalize_interval(bad)


def test_defaults_match_delta_defaults():
    assert retention_properties(FakeConfig({})) == {
        LOG_RETENTION_PROPERTY: "interval 30 days",
        DELETED_FILE_RETENTION_PROPERTY: "interval 7 days",
    }


def test_checkpoint_retention_refused_with_clear_error():
    # delta-kernel rejects every value of delta.checkpointRetentionDuration.
    with pytest.raises(ValueError, match="not supported by delta-rs"):
        retention_properties(FakeConfig({"delta_lake.retention": {"checkpoint_retention": "2 days"}}))


def test_unknown_key_refused():
    with pytest.raises(ValueError, match="Unknown"):
        retention_properties(FakeConfig({"delta_lake.retention": {"log_retension": "2 days"}}))


def test_repo_config_values_are_valid():
    from src.core.config import get_config

    props = retention_properties(get_config())
    assert interval_hours(props[DELETED_FILE_RETENTION_PROPERTY]) >= 168  # vacuum stays >= 7 days
    assert interval_hours(props[LOG_RETENTION_PROPERTY]) >= interval_hours(props[DELETED_FILE_RETENTION_PROPERTY])


# ------------------------------------------------------------------ tables
def test_new_table_created_with_retention_properties(mgr):
    _write(mgr, 0)
    conf = DeltaTable(str(mgr.get_table_path(TABLE))).metadata().configuration
    assert conf[LOG_RETENTION_PROPERTY] == "interval 14 days"
    assert conf[DELETED_FILE_RETENTION_PROPERTY] == "interval 72 hours"
    assert conf[lm.CHECKPOINT_INTERVAL_PROPERTY] == str(mgr.checkpoint_interval)


def test_existing_table_gets_retention_synced(mgr):
    path = mgr.get_table_path(TABLE)
    write_deltalake(str(path), pa.table({"url": ["https://e.com/old"], "status": ["pending"]}))
    assert LOG_RETENTION_PROPERTY not in DeltaTable(str(path)).metadata().configuration
    _write(mgr, 1)
    conf = DeltaTable(str(path)).metadata().configuration
    assert conf[LOG_RETENTION_PROPERTY] == "interval 14 days"
    assert conf[DELETED_FILE_RETENTION_PROPERTY] == "interval 72 hours"


def test_vacuum_default_uses_table_retention(mgr, monkeypatch):
    _write(mgr, 0)
    _write(mgr, 1, mode="overwrite")
    calls = []
    real_vacuum = DeltaTable.vacuum

    def spy(self, *args, **kwargs):
        calls.append(kwargs.get("retention_hours", "unset"))
        return real_vacuum(self, *args, **kwargs)

    monkeypatch.setattr(DeltaTable, "vacuum", spy)
    mgr._vacuum_table(TABLE)
    assert calls == [None]  # delta-rs then applies delta.deletedFileRetentionDuration


def test_vacuum_shorter_than_table_retention_is_refused(mgr, caplog):
    _write(mgr, 0)
    _write(mgr, 1, mode="overwrite")
    with caplog.at_level(logging.WARNING):
        mgr._vacuum_table(TABLE, retention_hours=1)
    assert "72 hours" in caplog.text  # enforced from the table property, not a hard-coded 168


def test_bad_config_falls_back_to_defaults_without_breaking_writes(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(
        "src.core.config.get_config", lambda: FakeConfig({"delta_lake.retention": {"log_retention": "forever"}})
    )
    m = LakehouseManager(base_path=str(tmp_path / "lake2"), start_workers=False)
    try:
        with caplog.at_level(logging.ERROR):
            _write(m, 0)
        conf = DeltaTable(str(m.get_table_path(TABLE))).metadata().configuration
        assert conf[LOG_RETENTION_PROPERTY] == "interval 30 days"
        assert "Ignoring delta_lake.retention" in caplog.text
    finally:
        m.shutdown_event.set()


def test_vacuum_script_defaults_to_table_retention():
    import pathlib
    import re

    src = (pathlib.Path(__file__).resolve().parents[2] / "scripts" / "vacuum_delta_tables.py").read_text()
    block = re.search(r'"--retention-hours",(.*?)\)', src, re.S).group(1)
    assert "default=None" in block
