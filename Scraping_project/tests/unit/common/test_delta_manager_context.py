"""Unit tests for the context manager protocol of DeltaLakeManager."""

import unittest
from unittest.mock import patch, MagicMock

import pytest

from src.common.delta_lake import DeltaLakeManager, InMemoryDeltaManager


class TestDeltaManagerContext(unittest.TestCase):
    """Verify context manager behavior for DeltaLakeManager."""

    @patch("src.common.delta_lake.DeltaLakeManager.shutdown")
    def test_delta_manager_context_calls_shutdown_when_started(self, mock_shutdown: MagicMock):
        """Verify __exit__ calls shutdown() when workers were started."""
        # Arrange: Instantiate with workers enabled
        # We need to disable the signal handler registration for this test
        with patch("signal.signal"):
            manager = DeltaLakeManager(start_workers=True)
            # Force-stop the threads immediately to avoid them running during the test
            manager.shutdown_event.set()
            manager.worker_thread.join()
            manager.maintenance_worker_thread.join()

        # Act: Use the manager as a context
        with manager as mgr:
            self.assertIs(mgr, manager)  # __enter__ should return self

        # Assert: shutdown was called
        mock_shutdown.assert_called_once_with(timeout=5)

    @patch("src.common.delta_lake.DeltaLakeManager.shutdown")
    def test_delta_manager_context_no_shutdown_when_not_started(self, mock_shutdown: MagicMock):
        """Verify __exit__ does NOT call shutdown() when workers were not started."""
        # Arrange: Instantiate with workers disabled
        manager = DeltaLakeManager(start_workers=False)

        # Act
        with manager as mgr:
            self.assertIs(mgr, manager)

        # Assert: shutdown was NOT called
        mock_shutdown.assert_not_called()

    def test_delta_manager_enter_returns_self(self):
        """Verify __enter__ returns the manager instance itself."""
        manager = DeltaLakeManager(start_workers=False)
        with manager as mgr:
            self.assertIs(mgr, manager)

    def test_delta_manager_context_propagates_exceptions(self):
        """Verify exceptions raised within the 'with' block are propagated."""
        class TestException(Exception):
            pass

        # Arrange
        manager = DeltaLakeManager(start_workers=False)

        # Act & Assert
        with self.assertRaises(TestException):
            with manager:
                raise TestException("This should be re-raised")


@patch("src.common.delta_lake.DELTA_MANAGER_SHUTDOWN_DURATION_SECONDS")
@patch("src.common.delta_lake.DELTA_MANAGER_SHUTDOWN_TOTAL")
@patch("src.common.delta_lake.DELTA_MANAGER_CONTEXT_EXIT_TOTAL")
@patch("src.common.delta_lake.DELTA_MANAGER_CONTEXT_ENTER_TOTAL")
class TestDeltaManagerObservability(unittest.TestCase):
    """Verify that context manager usage increments observability metrics."""

    def test_context_usage_increments_counters(
        self,
        mock_enter_total: MagicMock,
        mock_exit_total: MagicMock,
        mock_shutdown_total: MagicMock,
        mock_shutdown_duration: MagicMock,
    ):
        """Metrics should be incremented on context enter, exit, and shutdown."""
        # Arrange
        with patch("signal.signal"):
            manager = DeltaLakeManager(start_workers=True)

        # Act
        with manager:
            pass  # This will call __enter__ and then __exit__, which calls shutdown.

        # Assert
        mock_enter_total.inc.assert_called_once()
        mock_exit_total.inc.assert_called_once()
        mock_shutdown_total.inc.assert_called_once()

    def test_shutdown_observes_duration(
        self,
        mock_enter_total: MagicMock,
        mock_exit_total: MagicMock,
        mock_shutdown_total: MagicMock,
        mock_shutdown_duration: MagicMock,
    ):
        """Verify that the shutdown duration is recorded."""
        # Arrange
        with patch("signal.signal"):
            manager = DeltaLakeManager(start_workers=True)

        # Act
        with manager:
            pass

        # Assert
        mock_shutdown_duration.observe.assert_called_once()
        # Check that the observed value is a non-negative float
        args, _ = mock_shutdown_duration.observe.call_args
        self.assertIsInstance(args[0], float)
        self.assertGreaterEqual(args[0], 0)


class TestInMemoryDeltaManagerContext(unittest.TestCase):
    """Verify context manager behavior for InMemoryDeltaManager."""

    def test_inmemory_manager_context_is_noop(self):
        """Verify InMemoryDeltaManager works as a context manager (no-op)."""
        manager = InMemoryDeltaManager()
        try:
            with manager as mgr:
                self.assertIs(mgr, manager)
        except Exception:
            self.fail("InMemoryDeltaManager context manager raised an unexpected exception.")

    def test_inmemory_manager_context_propagates_exceptions(self):
        """Verify exceptions are propagated correctly from InMemoryDeltaManager context."""
        class TestException(Exception):
            pass

        # Arrange
        manager = InMemoryDeltaManager()

        # Act & Assert
        with self.assertRaises(TestException):
            with manager as mgr:
                self.assertIs(mgr, manager)
                raise TestException("This should be re-raised")