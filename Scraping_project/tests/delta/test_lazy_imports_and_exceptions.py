"""Tests for lazy imports and unified exception handling in DeltaLakeManager."""

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

# Test subject
from src.common.delta_lake import DeltaLakeManager


@pytest.fixture
def delta_manager(tmp_path):
    """Fixture for a DeltaLakeManager instance with a temporary base path."""
    # Ensure heavy libraries are not imported before test
    if "deltalake" in sys.modules:
        del sys.modules["deltalake"]
    if "pyarrow" in sys.modules:
        del sys.modules["pyarrow"]

    manager = DeltaLakeManager(base_path=str(tmp_path), start_workers=False)
    yield manager
    manager.shutdown()


def test_lazy_imports_on_first_write(delta_manager, caplog):
    """Verify that heavy libraries (pyarrow, deltalake) are loaded only on first write."""
    # 1. Assert libs are NOT in sys.modules before first write
    assert (
        "deltalake" not in sys.modules
    ), "deltalake should not be imported before first write"
    assert (
        "pyarrow" not in sys.modules
    ), "pyarrow should not be imported before first write"

    # 2. Perform a write operation to a table without special partitioning
    sample_data = [{"id": 1, "value": "a"}]
    with caplog.at_level(logging.INFO):
        delta_manager.write("stage1_errors", sample_data, async_write=False)

    # 3. Assert libs ARE in sys.modules after first write
    assert "deltalake" in sys.modules, "deltalake should be imported after first write"
    assert "pyarrow" in sys.modules, "pyarrow should be imported after first write"

    # 4. Assert write was successful
    assert "✅ Wrote 1 records to stage1_errors" in caplog.text
    assert delta_manager.count("stage1_errors") == 1


@patch("deltalake.write_deltalake")
def test_unified_exception_handling_on_write(
    mock_write_deltalake, delta_manager, caplog
):
    """Assert that _handle_writer_exception is called on write failure."""
    # 1. Configure mock to raise a specific exception
    mock_write_deltalake.side_effect = RuntimeError("Disk is full")

    # 2. Mock the unified exception handler to track its call
    delta_manager._handle_writer_exception = MagicMock(
        wraps=delta_manager._handle_writer_exception
    )

    # 3. Perform a write that is expected to fail
    sample_data = [{"id": 2, "value": "b"}]
    with caplog.at_level(logging.ERROR):
        delta_manager.write("stage1_errors", sample_data, async_write=False)

    # 4. Assert that the unified exception handler was called exactly once
    delta_manager._handle_writer_exception.assert_called_once()

    # 5. Assert that the correct error message was logged
    assert "Write failed for stage1_errors" in caplog.text
    assert "Disk is full" in caplog.text

    # 6. Assert that the exception handler returned the expected outcome (e.g., no re-raise)
    # (This is implicit if the test continues without an unhandled exception)
    assert delta_manager.count("stage1_errors") == 0
