import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))


from scrapy.settings import Settings

from src.common.retry_middleware import IntelligentRetryMiddleware


class TestIntelligentRetryMiddleware(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            {
                "RETRY_BACKOFF_BASE": 2,
                "RETRY_BACKOFF_MAX": 10,
            }
        )
        self.middleware = IntelligentRetryMiddleware(self.settings)

    @patch("random.uniform")
    def test_calculate_backoff_delay_exceeds_max_with_jitter(self, mock_uniform):
        # Arrange
        retry_count = 4  # This will produce a delay of 2^4 = 16
        mock_uniform.return_value = (
            5  # A large jitter to make the test case more obvious
        )

        # Act
        delay = self.middleware._calculate_backoff_delay(retry_count)

        # Assert
        self.assertLessEqual(
            delay,
            self.settings.get("RETRY_BACKOFF_MAX"),
            "The calculated delay should not exceed the maximum backoff time, even with jitter.",
        )


if __name__ == "__main__":
    unittest.main()
