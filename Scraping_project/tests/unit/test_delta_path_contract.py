"""#613: DELTA_LAKE_PATH is a single contract for DeltaHelper and LakehouseManager."""

from src.lakehouse.lakehouse_manager import LakehouseManager
from src.utils.delta import DeltaHelper


def test_env_path_shared_by_helper_and_manager(tmp_path, monkeypatch):
    lake = tmp_path / "delta"
    monkeypatch.setenv("DELTA_LAKE_PATH", str(lake))
    helper = DeltaHelper()
    manager = LakehouseManager(start_workers=False)
    try:
        assert helper.base_path == lake
        assert manager.base_path == lake
        assert manager.tables["stage2_page_analysis"].parent == lake
    finally:
        manager.shutdown()


def test_explicit_base_path_still_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "env"))
    manager = LakehouseManager(base_path=str(tmp_path / "explicit"), start_workers=False)
    try:
        assert manager.base_path == tmp_path / "explicit"
    finally:
        manager.shutdown()
    assert DeltaHelper(tmp_path / "x").base_path == tmp_path / "x"


def test_without_env_both_use_config_default(tmp_path, monkeypatch):
    monkeypatch.delenv("DELTA_LAKE_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    helper = DeltaHelper()
    manager = LakehouseManager(start_workers=False)
    try:
        assert helper.base_path == manager.base_path
    finally:
        manager.shutdown()


def test_helper_and_manager_resolve_same_table_paths(tmp_path, monkeypatch):
    """A Stage2 write via get_delta() lands where a fresh manager (e.g. the exporter) reads."""
    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "lake"))
    helper = DeltaHelper()
    reader = LakehouseManager(start_workers=False)
    try:
        for table in ("stage2_queue", "stage2_page_analysis", "stage3_summaries"):
            if table in reader.tables:
                assert reader.tables[table] == helper.base_path / table
    finally:
        reader.shutdown()
