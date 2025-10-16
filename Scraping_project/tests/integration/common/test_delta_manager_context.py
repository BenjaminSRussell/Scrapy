"""Integration tests for the DeltaLakeManager context manager."""

import tempfile
import shutil
import time
import pytest
from src.common.delta_lake import DeltaLakeManager

@pytest.mark.integration
def test_delta_manager_context_terminates_threads():
    """Verify that the 'with' statement properly shuts down background threads."""
    temp_dir = tempfile.mkdtemp(prefix="delta_integration_test_")

    try:
        # Arrange: Instantiate a real DeltaLakeManager with workers enabled
        # Using a 'with' statement on the manager itself is the test subject
        with DeltaLakeManager(base_path=temp_dir, start_workers=True) as manager:
            # Act: Let the manager run for a moment
            assert manager.worker_thread.is_alive()
            assert manager.maintenance_worker_thread.is_alive()

            # The __exit__ part of the context manager will be called here
            # which should trigger the shutdown.

        # Assert: After the 'with' block, the threads should be terminated
        # We might need to wait a very short moment for the threads to exit
        time.sleep(0.1) # Give a moment for threads to join
        assert not manager.worker_thread.is_alive(), "Worker thread should be terminated."
        assert not manager.maintenance_worker_thread.is_alive(), "Maintenance thread should be terminated."

    finally:
        # Cleanup
        shutil.rmtree(temp_dir)