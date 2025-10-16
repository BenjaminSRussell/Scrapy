import unittest
from unittest.mock import MagicMock

from src.common.retry_middleware import IntelligentRetryMiddleware


class TestIntelligentRetryMiddleware(unittest.TestCase):
    def test_calculate_backoff_delay_exceeds_max_with_jitter(self):
        """
        Verify that the backoff delay calculation with jitter does not exceed the max_backoff value.
        """
        settings = MagicMock()
        settings.getint.side_effect = lambda key, default=None: (default if default is not None else 2)
        middleware = IntelligentRetryMiddleware(settings=settings)

        # Simulate a high attempt number that would cause the delay to exceed max_backoff
        attempt = 10
        base = 2.0
        max_backoff = 100.0
        jitter = 10.0

        delay = middleware._calculate_backoff_delay(attempt, base, max_backoff, jitter)

        # The delay should be capped at max_backoff, regardless of jitter
        self.assertLessEqual(delay, max_backoff)


if __name__ == "__main__":
    unittest.main()
