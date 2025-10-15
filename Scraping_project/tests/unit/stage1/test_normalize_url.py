"""Unit tests for the centralized URL normalization function."""

import unittest

from src.stage1.base_spider import BaseSpider


class TestURLNormalization(unittest.TestCase):
    """Test suite for URL normalization."""

    def test_normalization_logic(self):
        """Verify that various forms of messy URLs are cleaned as expected."""
        test_cases = {
            # Basic fragment removal
            "https://www.uconn.edu/index.html#section1": "https://www.uconn.edu/index.html",
            # Case normalization
            "HTTPS://WWW.UCONN.EDU/": "https://www.uconn.edu/",
            # Tracking parameter removal
            "https://www.uconn.edu/?utm_source=test&variable=1": "https://www.uconn.edu/?variable=1",
            # Multiple tracking parameters
            "https://www.uconn.edu/?utm_source=test&utm_medium=cpc&variable=1": "https://www.uconn.edu/?variable=1",
            # Fragment and tracker
            "https://www.uconn.edu/page?utm_source=x#abc": "https://www.uconn.edu/page",
            # No changes needed
            "https://www.uconn.edu/clean/path": "https://www.uconn.edu/clean/path",
            # External URL with trackers
            "https://www.external.com/page?utm_campaign=spring&id=123": "https://www.external.com/page?id=123",
            # Empty query after stripping
            "https://www.uconn.edu/path?utm_source=news": "https://www.uconn.edu/path",
            # Malformed URL (should pass through)
            "htp:/invalid-url": "htp:/invalid-url",
        }

        for messy, expected in test_cases.items():
            with self.subTest(messy=messy):
                self.assertEqual(BaseSpider.normalize_url(messy), expected)


if __name__ == "__main__":
    unittest.main()
