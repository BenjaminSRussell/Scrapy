import unittest
from unittest.mock import MagicMock, patch

import pytest
from scrapy.http import HtmlResponse, Response
from scrapy.spidermiddlewares.httperror import HttpError
from twisted.internet.error import DNSLookupError, TCPTimedOutError, TimeoutError

from src.stage1.scout_spider import ScoutSpider


class TestScoutSpider(unittest.TestCase):
    def setUp(self):
        # Patch dependencies for initialization
        with patch('src.stage1.scout_spider.get_delta_manager'):
            with patch('src.stage1.scout_spider.get_postgres_manager'):
                self.spider = ScoutSpider()

    # ============================================================================
    # URL Hashing and Filtering Tests
    # ============================================================================

    def test_hash_url(self):
        """Test URL hashing for consistency and normalization."""
        spider = self.spider

        # Basic case
        url1 = "https://example.com/page"
        hash1 = spider._hash_url(url1)

        # Normalization (trailing slash)
        url2 = "https://example.com/page/"
        hash2 = spider._hash_url(url2)

        # Normalization (case)
        url3 = "https://EXAMPLE.com/Page"
        hash3 = spider._hash_url(url3)

        # All hashes should be identical
        self.assertEqual(hash1, hash2)
        self.assertEqual(hash1, hash3)

        # Verify hash format
        self.assertEqual(len(hash1), 16)
        self.assertTrue(all(c in '0123456789abcdef' for c in hash1))

    def test_has_ignored_extension(self):
        """Test filtering of URLs with ignored extensions."""
        spider = self.spider

        # Ignored extensions
        self.assertTrue(spider._has_ignored_extension("https://example.com/image.jpg"))
        self.assertTrue(spider._has_ignored_extension("https://example.com/document.PDF"))
        self.assertTrue(spider._has_ignored_extension("https://example.com/archive.zip"))

        # Non-ignored extensions
        self.assertFalse(spider._has_ignored_extension("https://example.com/page.html"))
        self.assertFalse(spider._has_ignored_extension("https://example.com/document.php"))
        self.assertFalse(spider._has_ignored_extension("https://example.com/no_extension"))

    # ============================================================================
    # JS Detection Tests
    # ============================================================================

    def test_detect_js_requirement(self):
        """Test detection of JS-heavy pages."""
        spider = self.spider

        # Helper to create a Scrapy HtmlResponse
        def create_response(body: str) -> HtmlResponse:
            return HtmlResponse(url='https://js-test.com', body=body, encoding='utf-8')

        # Case 1: SPA indicators (React, Vue)
        spa_body = "<html><body><div id='app-root'></div><script src='app.js'></script></body></html>"
        self.assertTrue(spider._detect_js_requirement(create_response(spa_body)))

        # Case 2: Heavy async loading
        async_body = "<html><script>fetch('/api/1'); fetch('/api/2'); axios.get('/data'); ajax(); ajax(); ajax();</script></html>"
        self.assertTrue(spider._detect_js_requirement(create_response(async_body)))

        # Case 3: Minimal content with script tags
        minimal_body = "<html><body><script src='loader.js'></script></body></html>"
        self.assertTrue(spider._detect_js_requirement(create_response(minimal_body)))

        # Case 4: Standard HTML (should not require JS)
        standard_body = """
        <html>
            <body>
                <h1>Welcome</h1>
                <p>This is a standard HTML page with plenty of text content.
                   It does not use any modern SPA frameworks and should be
                   perfectly parseable without JavaScript rendering.
                   The content is static and delivered directly from the server.
                </p>
            </body>
        </html>
        """
        self.assertFalse(spider._detect_js_requirement(create_response(standard_body)))

    # ============================================================================
    # URL Extraction Tests
    # ============================================================================

    def test_extract_api_endpoints(self):
        """Test extraction of API endpoints from script tags."""
        spider = self.spider
        body = """
        <html><script>
            const endpoint = "/api/v1/data";
            let baseURL = "https://api.example.com/v2/";
            fetch('/graphql');
        </script></html>
        """
        response = HtmlResponse(url='https://example.com', body=body, encoding='utf-8')
        urls = spider._extract_api_endpoints(response)

        self.assertIn("https://example.com/api/v1/data", urls)
        self.assertIn("https://api.example.com/v2/", urls)
        self.assertIn("https://example.com/graphql", urls)

    def test_extract_sitemap_urls(self):
        """Test generation of sitemap and robots.txt URLs."""
        spider = self.spider
        response = HtmlResponse(url='https://sub.example.com/path/page', body=b'')
        urls = spider._extract_sitemap_urls(response)

        self.assertIn("https://sub.example.com/robots.txt", urls)
        self.assertIn("https://sub.example.com/sitemap.xml", urls)
        self.assertIn("https://sub.example.com/sitemap_index.xml", urls)

    def test_extract_from_comments(self):
        """Test extraction of URLs from HTML comments."""
        spider = self.spider
        body = """
        <!--
            Check this out: https://comment.com/page1
            Another one: www.comment2.com
        -->
        """
        response = HtmlResponse(url='https://example.com', body=body, encoding='utf-8')
        urls = spider._extract_from_comments(response)

        self.assertIn("https://comment.com/page1", urls)
        self.assertIn("http://www.comment2.com", urls)

    def test_extract_from_headers(self):
        """Test extraction of URLs from HTTP headers."""
        spider = self.spider
        headers = {
            b'Link': b'<https://example.com/next>; rel="next"',
            b'Location': b'/redirected'
        }
        response = Response(url='https://example.com', headers=headers)
        urls = spider._extract_from_headers(response)

        self.assertIn("https://example.com/next", urls)
        self.assertIn("https://example.com/redirected", urls)

    # ============================================================================
    # Error Handling Tests
    # ============================================================================

    def test_handle_error_conditions(self):
        """Test different error handling scenarios."""
        spider = self.spider

        # --- Test HttpError ---
        spider.error_records = []
        request_http = MagicMock()
        request_http.url = 'https://http-error.com'
        request_http.meta = {'depth': 1}
        response_http = MagicMock()
        response_http.status = 404
        failure_http = MagicMock()
        failure_http.request = request_http
        failure_http.value = HttpError(response_http)
        failure_http.value.response = response_http
        failure_http.check.side_effect = lambda *a: HttpError in a

        spider.handle_error(failure_http)
        self.assertEqual(len(spider.error_records), 1)
        record = spider.error_records[0]
        self.assertEqual(record['error_type'], 'HttpError')
        self.assertEqual(record['error_code'], 404)
        self.assertEqual(record['url'], 'https://http-error.com')

        # --- Test DNSLookupError ---
        spider.error_records = []
        request_dns = MagicMock()
        request_dns.url = 'https://dns-error.com'
        request_dns.meta = {'depth': 2}
        failure_dns = MagicMock()
        failure_dns.request = request_dns
        failure_dns.value = DNSLookupError()
        failure_dns.check.side_effect = lambda *a: DNSLookupError in a

        spider.handle_error(failure_dns)
        self.assertEqual(len(spider.error_records), 1)
        record = spider.error_records[0]
        self.assertEqual(record['error_type'], 'DNSLookupError')
        self.assertEqual(record['error_code'], 0)

        # --- Test TimeoutError ---
        spider.error_records = []
        request_timeout = MagicMock()
        request_timeout.url = 'https://timeout-error.com'
        request_timeout.meta = {'depth': 3}
        failure_timeout = MagicMock()
        failure_timeout.request = request_timeout
        failure_timeout.value = TimeoutError()
        failure_timeout.check.side_effect = lambda *a: any(x in a for x in [TimeoutError, TCPTimedOutError])

        spider.handle_error(failure_timeout)
        self.assertEqual(len(spider.error_records), 1)
        record = spider.error_records[0]
        self.assertEqual(record['error_type'], 'TimeoutError')


if __name__ == "__main__":
    unittest.main()
