import hashlib
import unittest
from unittest.mock import MagicMock, patch

from scrapy.http import HtmlResponse, Response, Request

from src.stage1.scout_spider import ScoutSpider


class TestScoutSpider(unittest.TestCase):

    def setUp(self):
        self.spider = ScoutSpider()

    def test_hash_url(self):
        url = "http://example.com"
        expected_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        self.assertEqual(self.spider._hash_url(url), expected_hash)

        url_with_slash = "http://example.com/"
        self.assertEqual(self.spider._hash_url(url_with_slash), expected_hash)

        url_https = "https://example.com"
        expected_hash_https = hashlib.sha256(url_https.encode()).hexdigest()[:16]
        self.assertEqual(self.spider._hash_url(url_https), expected_hash_https)

    def test_has_ignored_extension(self):
        self.assertTrue(self.spider._has_ignored_extension("http://example.com/a.jpg"))
        self.assertTrue(self.spider._has_ignored_extension("http://example.com/a.css"))
        self.assertFalse(self.spider._has_ignored_extension("http://example.com/a.html"))
        self.assertFalse(self.spider._has_ignored_extension("http://example.com/a.php"))
        self.assertTrue(self.spider._has_ignored_extension("http://example.com/path/to/file.pdf"))
        self.assertFalse(self.spider._has_ignored_extension("http://example.com/path/to/page"))

    def test_detect_js_requirement(self):
        html_with_react = b"<html><body><div id='root'></div><script src='react.js'></script></body></html>"
        response_react = HtmlResponse(url="http://example.com", body=html_with_react, encoding='utf-8')
        self.assertTrue(self.spider._detect_js_requirement(response_react))

        html_with_vue = b"<html><body><div id='app'></div><script src='vue.js'></script></body></html>"
        response_vue = HtmlResponse(url="http://example.com", body=html_with_vue, encoding='utf-8')
        self.assertTrue(self.spider._detect_js_requirement(response_vue))

        html_with_angular = b"<html><body><app-root></app-root><script src='angular.js'></script></body></html>"
        response_angular = HtmlResponse(url="http://example.com", body=html_with_angular, encoding='utf-8')
        self.assertTrue(self.spider._detect_js_requirement(response_angular))

        html_with_fetch = b"<html><body><p>" + b"some text " * 50 + b"</p><script>fetch('/api/data');</script></body></html>"
        response_fetch = HtmlResponse(url="http://example.com", body=html_with_fetch, encoding='utf-8')
        self.assertFalse(self.spider._detect_js_requirement(response_fetch))

        html_with_many_fetches = b"<html><body><script>fetch('/api/data');fetch('/api/data');fetch('/api/data');fetch('/api/data');fetch('/api/data');fetch('/api/data');</script></body></html>"
        response_many_fetches = HtmlResponse(url="http://example.com", body=html_with_many_fetches, encoding='utf-8')
        self.assertTrue(self.spider._detect_js_requirement(response_many_fetches))

        html_minimal_content = b"<html><body><script>var x=1;</script></body></html>"
        response_minimal = HtmlResponse(url="http://example.com", body=html_minimal_content, encoding='utf-8')
        self.assertTrue(self.spider._detect_js_requirement(response_minimal))

        html_no_js = b"<html><body><p>Hello, world!</p></body></html>"
        response_no_js = HtmlResponse(url="http://example.com", body=html_no_js, encoding='utf-8')
        self.assertFalse(self.spider._detect_js_requirement(response_no_js))

    def test_extract_api_endpoints(self):
        script_with_endpoints = b"<script>const api = '/api/v1/users'; const base = 'https://api.example.com';</script>"
        response = HtmlResponse(url="http://example.com", body=script_with_endpoints, encoding='utf-8')
        urls = self.spider._extract_api_endpoints(response)
        self.assertIn("http://example.com/api/v1/users", urls)

    def test_extract_sitemap_urls(self):
        response = HtmlResponse(url="http://example.com", body=b"<html></html>", encoding='utf-8')
        urls = self.spider._extract_sitemap_urls(response)
        self.assertIn("http://example.com/robots.txt", urls)
        self.assertIn("http://example.com/sitemap.xml", urls)

    def test_extract_from_comments(self):
        html_with_comments = b"<!-- Link: http://example.com/comment -->"
        response = HtmlResponse(url="http://example.com", body=html_with_comments, encoding='utf-8')
        urls = self.spider._extract_from_comments(response)
        self.assertIn("http://example.com/comment", urls)

    def test_extract_from_headers(self):
        headers = {
            b"Link": b"<http://example.com/link-header>; rel='stylesheet'",
            b"Location": b"http://example.com/redirect"
        }
        response = HtmlResponse(url="http://example.com", body=b"", headers=headers)
        urls = self.spider._extract_from_headers(response)
        self.assertIn("http://example.com/link-header", urls)
        self.assertIn("http://example.com/redirect", urls)

    def test_handle_error(self):
        request = Request("http://example.com")
        failure = MagicMock()
        failure.request = request
        failure.check.return_value = False
        class CustomError:
            __name__ = "CustomError"
        failure.type = CustomError
        failure.value = "Custom error message"

        self.spider.handle_error(failure)
        self.assertEqual(len(self.spider.error_records), 1)
        self.assertEqual(self.spider.error_records[0]['error_type'], 'CustomError')
        self.assertEqual(self.spider.error_records[0]['url'], 'http://example.com')

if __name__ == '__main__':
    unittest.main()
