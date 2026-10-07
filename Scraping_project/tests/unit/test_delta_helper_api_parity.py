"""Unit tests for DeltaHelper LakehouseManager API parity (#609)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.stage2.stage2_worker import Stage2Worker
from src.utils.delta import DeltaHelper, get_delta, reset_delta


@pytest.fixture
def delta_helper(tmp_path):
    """DeltaHelper with a mocked LakehouseManager (no real lake I/O)."""
    reset_delta()
    helper = DeltaHelper(base_path=tmp_path / "delta_lake")
    mock_manager = MagicMock()
    mock_manager.read_table.return_value = []
    mock_manager.read.return_value = []
    mock_manager.write.return_value = None
    mock_manager.get_table_path.side_effect = lambda name: helper.base_path / name
    helper._manager = mock_manager
    yield helper, mock_manager
    reset_delta()


def test_read_table_proxies_manager(delta_helper):
    helper, mock_manager = delta_helper
    mock_manager.read_table.return_value = [{"url": "https://example.com", "status": "pending"}]

    rows = helper.read_table("stage2_queue")

    assert rows == [{"url": "https://example.com", "status": "pending"}]
    mock_manager.read_table.assert_called_once_with("stage2_queue")


def test_write_passes_async_write(delta_helper):
    helper, mock_manager = delta_helper
    rows = [{"url": "https://example.com/a"}]

    ok = helper.write("stage2_page_analysis", rows, mode="append", async_write=False)

    assert ok is True
    mock_manager.write.assert_called_once_with(
        "stage2_page_analysis",
        rows,
        mode="append",
        async_write=False,
    )


def test_write_async_write_true(delta_helper):
    helper, mock_manager = delta_helper
    rows = [{"url": "https://example.com/b"}]

    ok = helper.write("stage4_large_docs", rows, mode="append", async_write=True)

    assert ok is True
    mock_manager.write.assert_called_once_with(
        "stage4_large_docs",
        rows,
        mode="append",
        async_write=True,
    )


def test_get_table_path_still_works(delta_helper):
    helper, mock_manager = delta_helper

    path = helper.get_table_path("stage2_queue")

    assert path == helper.base_path / "stage2_queue"
    mock_manager.get_table_path.assert_called_once_with("stage2_queue")


def test_get_delta_read_table_and_write_signatures(tmp_path):
    """Acceptance: get_delta().read_table / write(..., async_write=False) work."""
    reset_delta()
    try:
        delta = get_delta(base_path=tmp_path / "delta_lake")
        mock_manager = MagicMock()
        mock_manager.read_table.return_value = []
        mock_manager.write.return_value = None
        delta._manager = mock_manager

        assert get_delta().read_table("stage2_queue") == []
        assert get_delta().write(
            "stage2_page_analysis",
            [{"url": "https://x"}],
            mode="append",
            async_write=False,
        ) is True

        mock_manager.read_table.assert_called_with("stage2_queue")
        mock_manager.write.assert_called_with(
            "stage2_page_analysis",
            [{"url": "https://x"}],
            mode="append",
            async_write=False,
        )
    finally:
        reset_delta()


@pytest.mark.asyncio
async def test_stage2_run_empty_queue_returns_cleanly(delta_helper):
    """Stage2Worker.run must not TypeError on empty/missing table path."""
    helper, mock_manager = delta_helper
    mock_manager.read_table.return_value = []

    worker = Stage2Worker()
    worker.delta = helper

    await worker.run()  # returns cleanly; no TypeError

    mock_manager.read_table.assert_called_with("stage2_queue")


@pytest.mark.asyncio
async def test_stage2_run_missing_table_returns_cleanly(delta_helper):
    helper, mock_manager = delta_helper
    mock_manager.read_table.side_effect = ValueError("Unknown table: stage2_queue")

    # DeltaHelper.read_table swallows errors and returns []; Stage2 exits on empty
    worker = Stage2Worker()
    worker.delta = helper

    await worker.run()
