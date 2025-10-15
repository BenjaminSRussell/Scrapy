import unittest
from unittest.mock import MagicMock, patch

from src.common.retry_middleware import IntelligentRetryMiddleware


class TestIntelligentRetryMiddleware(unittest.TestCase):
    def test_no_random_import_in_backoff_calculation(self):
        """
        Verify that 'import random' is not called inside _calculate_backoff_delay.
        """
        settings = MagicMock()
        settings.getint.side_effect = lambda key, default=None: (
            default if default is not None else 2
        )
        middleware = IntelligentRetryMiddleware(settings=settings)

        with patch("builtins.__import__") as mock_import:
            middleware._calculate_backoff_delay(1)
            for call in mock_import.call_args_list:
                self.assertNotEqual(
                    call.args[0],
                    "random",
                    "Found 'import random' inside _calculate_backoff_delay",
                )


if __name__ == "__main__":
    unittest.main()
