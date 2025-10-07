import unittest
from src.stage1.scout_spider import ScoutSpider

class TestScoutSpider(unittest.TestCase):

    def setUp(self):
        # Initialize the spider with a dummy seed file
        # This is needed because the spider's __init__ requires it.
        self.spider = ScoutSpider(seed_file='dummy_seed.csv')

    def test_hash_url_normalization(self):
        """Test that _hash_url correctly normalizes URLs before hashing."""

        # URLs that should have the same hash after normalization
        #
        # Normalization should handle:
        # - Case-insensitivity in scheme and netloc
        # - Trailing slashes in the path
        #
        # It should NOT alter the query parameters.

        # Test Case 1: Trailing slash and case variation
        url1 = "http://example.com/path"
        url2 = "http://example.com/path/"
        url3 = "HTTP://EXAMPLE.COM/path"

        self.assertEqual(
            self.spider._hash_url(url1),
            self.spider._hash_url(url2),
            "Hashes should be equal for URLs with/without trailing slash"
        )
        self.assertEqual(
            self.spider._hash_url(url1),
            self.spider._hash_url(url3),
            "Hashes should be equal for URLs with different capitalization"
        )

        # Test Case 2: URLs with query parameters that should be different
        url_query1 = "http://example.com/path?a=1"
        url_query2 = "http://example.com/path?a=2"
        url_no_query = "http://example.com/path"

        self.assertNotEqual(
            self.spider._hash_url(url_query1),
            self.spider._hash_url(url_query2),
            "Hashes for URLs with different query parameters should be different"
        )
        self.assertNotEqual(
            self.spider._hash_url(url_no_query),
            self.spider._hash_url(url_query1),
            "Hashes for URLs with and without query parameters should be different"
        )

        # Test Case 3: Query parameter order should matter
        url_query_order1 = "http://example.com/path?a=1&b=2"
        url_query_order2 = "http://example.com/path?b=2&a=1"

        self.assertNotEqual(
            self.spider._hash_url(url_query_order1),
            self.spider._hash_url(url_query_order2),
            "Hashes should be different if query parameter order is different"
        )

if __name__ == '__main__':
    unittest.main()
