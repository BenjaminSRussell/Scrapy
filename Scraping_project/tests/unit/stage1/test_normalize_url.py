"""Unit tests for the centralized URL normalization function."""

import unittest

from src.stage1.base_spider import BaseSpider

class TestURLNormalization(unittest.TestCase):

    def setUp(self):
        self.spider = BaseSpider()

    def test_normalization_logic(self):
        test_cases = {
            "https://www.uconn.edu/index.html
            "HTTPS://WWW.UCONN.EDU/": "https://www.uconn.edu/",
            "https://www.uconn.edu/?utm_source=test&variable=1": "https://www.uconn.edu/?variable=1",
            "https://www.uconn.edu/?utm_source=test&utm_medium=cpc&variable=1": "https://www.uconn.edu/?variable=1",
            "https://www.uconn.edu/page?utm_source=x
            "https://www.uconn.edu/clean/path": "https://www.uconn.edu/clean/path",
            "https://www.external.com/page?utm_campaign=spring&id=123": "https://www.external.com/page?id=123",
            "https://www.uconn.edu/path?utm_source=news": "https://www.uconn.edu/path",
            "htp:/invalid-url": "htp:/invalid-url",
        }

        for messy, expected in test_cases.items():
            with self.subTest(messy=messy):
                self.assertEqual(self.spider.normalize_url(messy), expected)

if __name__ == "__main__":
    unittest.main()
