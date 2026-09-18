"""Tests for lazy imports and unified exception handling in DeltaLakeManager."""

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

from src.common.delta_lake import DeltaLakeManager

@pytest.fixture
def delta_manager(tmp_path):
    if "deltalake" in sys.modules:
        del sys.modules["deltalake"]
    if "pyarrow" in sys.modules:
        del sys.modules["pyarrow"]

    manager = DeltaLakeManager(base_path=str(tmp_path), start_workers=False)
    yield manager
    manager.shutdown()

def test_lazy_imports_on_first_write(delta_manager, caplog):
    assert "deltalake" not in sys.modules, "deltalake should not be imported before first write"
    assert "pyarrow" not in sys.modules, "pyarrow should not be imported before first write"

    sample_data = [{"id": 1, "value": "a"}]
    with caplog.at_level(logging.INFO):
        delta_manager.write("stage1_errors", sample_data, async_write=False)

    assert "deltalake" in sys.modules, "deltalake should be imported after first write"
    assert "pyarrow" in sys.modules, "pyarrow should be imported after first write"

    assert "✅ Wrote 1 records to stage1_errors" in caplog.text
    assert delta_manager.count("stage1_errors") == 1

@patch("deltalake.write_deltalake")
def test_unified_exception_handling_on_write(mock_write_deltalake, delta_manager, caplog):
    mock_write_deltalake.side_effect = RuntimeError("Disk is full")

    delta_manager._handle_writer_exception = MagicMock(wraps=delta_manager._handle_writer_exception)

    sample_data = [{"id": 2, "value": "b"}]
    with caplog.at_level(logging.ERROR):
        delta_manager.write("stage1_errors", sample_data, async_write=False)

    delta_manager._handle_writer_exception.assert_called_once()

    assert "Write failed for stage1_errors" in caplog.text
    assert "Disk is full" in caplog.text

    assert delta_manager.count("stage1_errors") == 0
