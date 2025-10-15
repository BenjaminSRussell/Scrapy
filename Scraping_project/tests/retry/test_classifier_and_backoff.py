"""Tests for the retry classifier and backoff logic."""

import unittest

from scrapy.settings import Settings

from src.common.retry_middleware import IntelligentRetryMiddleware


class TestClassifierAndBackoff(unittest.TestCase):
    """Test cases for retry classifier and backoff logic."""

    def setUp(self):
        """Set up the test case."""
        self.settings = Settings(
            {
                "RETRY_BACKOFF_BASE": 2,
                "RETRY_BACKOFF_MAX": 300,
            }
        )
        self.middleware = IntelligentRetryMiddleware(self.settings)

    def test_status_classification_snapshot(self):
        """
        Snapshot test to verify the classification of various HTTP status codes.
        This test captures the current baseline behavior.
        """
        # Statuses to test and their expected classification (retry or fail)
        status_tests = {
            # Transient/Retryable statuses
            408: "retry",
            429: "retry",
            500: "retry",
            502: "retry",
            503: "retry",
            504: "retry",
            # Permanent/Non-retryable statuses
            400: "fail",
            401: "fail",
            403: "fail",
            404: "fail",
            410: "fail",
            # Success status
            200: "pass",
        }

        for status, expected_action in status_tests.items():
            with self.subTest(status=status, expected_action=expected_action):
                action = self.middleware._classify_status(status)
                self.assertEqual(action, expected_action)

    def test_backoff_schedule_snapshot(self):
        """
        Snapshot test for the backoff delay calculation.
        Verifies the exponential backoff schedule with jitter.
        """
        retry_attempts = 10
        backoff_base = self.settings.getint("RETRY_BACKOFF_BASE")
        backoff_max = self.settings.getint("RETRY_BACKOFF_MAX")

        for i in range(1, retry_attempts + 1):
            with self.subTest(attempt=i):
                delay = self.middleware._compute_backoff(i)

                # Base delay without jitter
                expected_base_delay = backoff_base**i
                # Max delay with jitter (10%)
                expected_max_jitter_delay = expected_base_delay * 1.1

                # The final delay should be capped by backoff_max
                final_expected_max = min(expected_max_jitter_delay, backoff_max)
                # The minimum delay can't be higher than max
                final_expected_min = min(expected_base_delay, backoff_max)

                # Assert that the delay is within the expected range (base <= delay < base * 1.1)
                self.assertGreaterEqual(delay, final_expected_min)
                # Allow for a tiny floating point tolerance
                self.assertLessEqual(delay, final_expected_max + 0.0001)


if __name__ == "__main__":
    unittest.main()
