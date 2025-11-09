"""Tests for the retry classifier and backoff logic."""

import unittest

from scrapy.settings import Settings

from src.common.retry_middleware import IntelligentRetryMiddleware

class TestClassifierAndBackoff(unittest.TestCase):

    def setUp(self):
        self.settings = Settings(
            {
                "RETRY_BACKOFF_BASE": 2,
                "RETRY_BACKOFF_MAX": 300,
            }
        )
        self.middleware = IntelligentRetryMiddleware(self.settings)

    def test_status_classification_snapshot(self):
        status_tests = {
            408: "retry",
            429: "retry",
            500: "retry",
            502: "retry",
            503: "retry",
            504: "retry",
            400: "fail",
            401: "fail",
            403: "fail",
            404: "fail",
            410: "fail",
            200: "pass",
        }

        for status, expected_action in status_tests.items():
            with self.subTest(status=status, expected_action=expected_action):
                action = self.middleware._classify_status(status)
                self.assertEqual(action, expected_action)

    def test_backoff_schedule_snapshot(self):
        retry_attempts = 10
        backoff_base = self.settings.getint("RETRY_BACKOFF_BASE")
        backoff_max = self.settings.getint("RETRY_BACKOFF_MAX")

        for i in range(1, retry_attempts + 1):
            with self.subTest(attempt=i):
                delay = self.middleware._compute_backoff(i)

                expected_base_delay = backoff_base**i
                expected_max_jitter_delay = expected_base_delay * 1.1

                final_expected_max = min(expected_max_jitter_delay, backoff_max)
                final_expected_min = min(expected_base_delay, backoff_max)

                self.assertGreaterEqual(delay, final_expected_min)
                self.assertLessEqual(delay, final_expected_max + 0.0001)

if __name__ == "__main__":
    unittest.main()
