"""Unit tests for the context manager protocol of DeltaLakeManager."""

import unittest
from unittest.mock import patch, MagicMock

import pytest

from src.common.delta_lake import DeltaLakeManager, InMemoryDeltaManager

class TestDeltaManagerContext(unittest.TestCase):

    @patch("src.common.delta_lake.DeltaLakeManager.shutdown")
    def test_delta_manager_context_calls_shutdown_when_started(self, mock_shutdown: MagicMock):
        with patch("signal.signal"):
            manager = DeltaLakeManager(start_workers=True)
            manager.shutdown_event.set()
            manager.worker_thread.join()
            manager.maintenance_worker_thread.join()

        with manager as mgr:
            self.assertIs(mgr, manager)

        mock_shutdown.assert_called_once_with(timeout=5)

    @patch("src.common.delta_lake.DeltaLakeManager.shutdown")
    def test_delta_manager_context_no_shutdown_when_not_started(self, mock_shutdown: MagicMock):
        manager = DeltaLakeManager(start_workers=False)

        with manager as mgr:
            self.assertIs(mgr, manager)

        mock_shutdown.assert_not_called()

    def test_delta_manager_enter_returns_self(self):
        manager = DeltaLakeManager(start_workers=False)
        with manager as mgr:
            self.assertIs(mgr, manager)

    def test_delta_manager_context_propagates_exceptions(self):
        class TestException(Exception):
            pass

        manager = DeltaLakeManager(start_workers=False)

        with self.assertRaises(TestException):
            with manager:
                raise TestException("This should be re-raised")

@patch("src.common.delta_lake.DELTA_MANAGER_SHUTDOWN_DURATION_SECONDS")
@patch("src.common.delta_lake.DELTA_MANAGER_SHUTDOWN_TOTAL")
@patch("src.common.delta_lake.DELTA_MANAGER_CONTEXT_EXIT_TOTAL")
@patch("src.common.delta_lake.DELTA_MANAGER_CONTEXT_ENTER_TOTAL")
class TestDeltaManagerObservability(unittest.TestCase):

    def test_context_usage_increments_counters(
        self,
        mock_enter_total: MagicMock,
        mock_exit_total: MagicMock,
        mock_shutdown_total: MagicMock,
        mock_shutdown_duration: MagicMock,
    ):
        """Metrics should be incremented on context enter, exit, and shutdown."""
        with patch("signal.signal"):
            manager = DeltaLakeManager(start_workers=True)

        with manager:
            pass

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
        with patch("signal.signal"):
            manager = DeltaLakeManager(start_workers=True)

        with manager:
            pass

        mock_shutdown_duration.observe.assert_called_once()
        args, _ = mock_shutdown_duration.observe.call_args
        self.assertIsInstance(args[0], float)
        self.assertGreaterEqual(args[0], 0)

class TestInMemoryDeltaManagerContext(unittest.TestCase):

    def test_inmemory_manager_context_is_noop(self):
        manager = InMemoryDeltaManager()
        try:
            with manager as mgr:
                self.assertIs(mgr, manager)
        except Exception:
            self.fail("InMemoryDeltaManager context manager raised an unexpected exception.")

    def test_inmemory_manager_context_propagates_exceptions(self):
        class TestException(Exception):
            pass

        manager = InMemoryDeltaManager()

        with self.assertRaises(TestException):
            with manager as mgr:
                self.assertIs(mgr, manager)
                raise TestException("This should be re-raised")
