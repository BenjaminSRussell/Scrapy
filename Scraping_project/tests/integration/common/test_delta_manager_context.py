"""Integration tests for the DeltaLakeManager context manager."""

import tempfile
import shutil
import time
import pytest
from src.common.delta_lake import DeltaLakeManager

@pytest.mark.integration
def test_delta_manager_context_terminates_threads():
    temp_dir = tempfile.mkdtemp(prefix="delta_integration_test_")

    try:
        with DeltaLakeManager(base_path=temp_dir, start_workers=True) as manager:
            assert manager.worker_thread.is_alive()
            assert manager.maintenance_worker_thread.is_alive()

        time.sleep(0.1)
        assert not manager.worker_thread.is_alive(), "Worker thread should be terminated."
        assert not manager.maintenance_worker_thread.is_alive(), "Maintenance thread should be terminated."

    finally:
        shutil.rmtree(temp_dir)
