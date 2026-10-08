"""get_delta() and the LakehouseManager singleton are one writer (#359)."""

from __future__ import annotations

import logging

import pytest

from src.lakehouse import lakehouse_manager as lm
from src.lakehouse.lakehouse_manager import LakehouseManager, get_lakehouse_manager, lakehouse_session
from src.utils import delta as delta_mod
from src.utils.delta import DeltaHelper, get_delta, reset_delta

TABLE = "errors_t"


@pytest.fixture
def lake(tmp_path, monkeypatch):
    path = tmp_path / "lake"
    monkeypatch.setenv("DELTA_LAKE_PATH", str(path))
    monkeypatch.delenv("DELTA_BACKEND", raising=False)
    LakehouseManager.reset_instance()
    reset_delta()
    yield path
    reset_delta()
    LakehouseManager.reset_instance()


def _has_rows(mgr) -> bool:
    return (mgr.base_path / TABLE / "_delta_log").exists()


def _rows(n, start=0):
    return [{"url": f"https://uconn.edu/{i}", "error": "x", "url_hash": f"h{i}"} for i in range(start, start + n)]


def test_get_delta_and_lakehouse_manager_are_the_same_writer(lake):
    helper = get_delta()
    assert helper.shared is True
    assert helper.manager is LakehouseManager.get_instance()
    assert helper.manager is get_lakehouse_manager()
    # Same write queue, writer thread and schema cache, not a second copy.
    assert helper.manager.write_queue is get_lakehouse_manager().write_queue


def test_sync_write_is_committed_on_return(lake):
    helper = get_delta()
    assert helper.write(TABLE, _rows(3), mode="append", async_write=False) is True
    assert len(get_lakehouse_manager().read(TABLE)) == 3


def test_async_write_is_queued_then_committed(lake):
    helper = get_delta()
    assert helper.write(TABLE, _rows(2), mode="append", async_write=True) is True
    mgr = get_lakehouse_manager()
    mgr.write_queue.join()  # the shared writer thread drains it
    assert len(mgr.read(TABLE)) == 2


def test_unsupported_kwargs_fail_loudly(lake):
    with pytest.raises(TypeError):
        get_delta().write(TABLE, _rows(1), mode="append", schema_overwrite=True)


def test_helper_reattaches_after_the_singleton_is_reset(lake):
    helper = get_delta()
    first = helper.manager
    with lakehouse_session() as mgr:  # resets (and shuts down) the singleton on exit
        assert mgr is first
    assert LakehouseManager._instance is None
    second = helper.manager
    assert second is not first and second is LakehouseManager._instance
    assert helper.write(TABLE, _rows(1), mode="append", async_write=False) is True


def test_private_helper_keeps_its_own_manager(lake, tmp_path):
    shared = get_delta().manager
    private = DeltaHelper(tmp_path / "other_lake")
    assert private.shared is False
    assert private.manager is not shared
    assert private.write(TABLE, _rows(1), mode="append", async_write=False) is True
    assert not _has_rows(get_lakehouse_manager())  # nothing leaked into the shared lake
    private.manager.shutdown()


def test_singleton_on_another_lake_is_not_hijacked(lake, tmp_path, caplog):
    other = LakehouseManager.get_instance(base_path=str(tmp_path / "elsewhere"))
    with caplog.at_level(logging.WARNING):
        helper = get_delta()
        assert helper.manager is not other
    assert "separate manager" in caplog.text
    assert helper.write(TABLE, _rows(1), mode="append", async_write=False) is True
    assert not _has_rows(other)
    helper.manager.shutdown()


def test_get_instance_warns_when_asked_for_a_different_path(lake, tmp_path, caplog):
    first = LakehouseManager.get_instance(base_path=str(lake))
    with caplog.at_level(logging.WARNING, logger=lm.logger.name):
        assert LakehouseManager.get_instance(base_path=str(tmp_path / "nope")) is first
    assert "ignored" in caplog.text


def test_stage2_style_call_sites_go_through_the_shared_writer(lake):
    """Stage 2 calls get_delta().write(..., async_write=...) - both paths must work."""
    helper = get_delta()
    assert helper.write("stage4_large_docs", [{"url": "https://uconn.edu/big", "url_hash": "b"}],
                        mode="append", async_write=True) is True
    helper.manager.write_queue.join()
    assert helper.manager is LakehouseManager.get_instance()
    assert delta_mod._delta_helper is helper
