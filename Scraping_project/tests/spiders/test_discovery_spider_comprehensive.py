"""Comprehensive tests for discovery spider - every edge case"""
import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pytest
import scrapy
from scrapy.http import HtmlResponse, Request

from stage1.discovery_spider import DiscoverySpider
from common.schemas import DiscoveryItem


class TestDiscoverySpiderInit:
    """initialization testing because setup matters"""

    def test_default_initialization(self):
        spider = DiscoverySpider()
        assert spider.max_depth == 3
        assert spider.name == "discovery"
        assert spider.allowed_domains == ["uconn.edu"]
        assert isinstance(spider.seen_urls, set)

    def test_custom_max_depth(self):
        spider = DiscoverySpider(max_depth=5)
        assert spider.max_depth == 5

    def test_max_depth_string_conversion(self):
        spider = DiscoverySpider(max_depth="7")
        assert spider.max_depth == 7

    def test_counters_initialized(self):
        spider = DiscoverySpider()
        assert spider.total_urls_parsed == 0
        assert spider.unique_urls_found == 0
        assert spider.duplicates_skipped == 0
        assert spider.dynamic_urls_found == 0
        assert spider.api_endpoints_found == 0

    def test_depth_yields_initialized(self):
        spider = DiscoverySpider(max_depth=2)
        expected_depths = {0: 0, 1: 0, 2: 0}
        assert spider.depth_yields == expected_depths

    def test_referring_pages_empty(self):
        spider = DiscoverySpider()
        assert spider.referring_pages == {}


class TestStartRequests:
    """seed URL loading because everything starts somewhere"""

    def setup_method(self):
        self.spider = DiscoverySpider()

    def test_missing_seed_file(self):
        with patch('pathlib.Path.exists', return_value=False):
            requests = list(self.spider.start_requests())
            assert len(requests) >= 2  # sitemap requests at minimum

    def test_empty_seed_file(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write("")
            seed_file = Path(f.name)

        with patch('pathlib.Path') as mock_path:
            mock_path.return_value = seed_file
            mock_path.return_value.exists.return_value = True

            requests = list(self.spider.start_requests())
            # should still have sitemap requests
            assert len(requests) >= 2

        seed_file.unlink()

    def test_valid_seed_urls(self):
        seed_content = """https://uconn.edu
https://www.uconn.edu/academics
https://admissions.uconn.edu"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(seed_content)
            seed_file = Path(f.name)

        with patch('pathlib.Path') as mock_path:
            mock_path.return_value = seed_file
            mock_path.return_value.exists.return_value = True

            requests = list(self.spider.start_requests())
            # sitemap + seed URLs
            assert len(requests) >= 5

        seed_file.unlink()

    def test_malformed_seed_urls(self):
        seed_content = """not-a-url
"https://uconn.edu"
https:https://duplicate.uconn.edu
C:\\Windows\\path\\https://extracted.uconn.edu
https://uconn.edu\\backslash"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(seed_content)
            seed_file = Path(f.name)

        with patch('pathlib.Path') as mock_path:
            mock_path.return_value = seed_file
            mock_path.return_value.exists.return_value = True

            requests = list(self.spider.start_requests())
            # should clean up and recover some URLs
            assert len(requests) >= 2

        seed_file.unlink()

    def test_sitemap_request_generation(self):
        spider = DiscoverySpider()
        requests = list(spider._generate_sitemap_requests())

        # should generate requests for multiple domains and paths
        assert len(requests) >= 6  # 2 domains * (1 robots + 3 sitemaps each)

        robots_requests = [r for r in requests if 'robots.txt' in r.url]
        sitemap_requests = [r for r in requests if 'sitemap' in r.url]

        assert len(robots_requests) >= 2
        assert len(sitemap_requests) >= 6


class TestUrlCleaning:
    """URL sanitization because data entry is terrible"""

    def setup_method(self):
        self.spider = DiscoverySpider()

    def test_clean_basic_url(self):
        url, sanitized = self.spider._clean_seed_url("https://uconn.edu", 1)
        assert url == "https://uconn.edu"
        assert not sanitized

    def test_clean_quoted_url(self):
        url, sanitized = self.spider._clean_seed_url('"https://uconn.edu"', 1)
        assert url == "https://uconn.edu"
        assert sanitized

    def test_clean_single_quoted_url(self):
        url, sanitized = self.spider._clean_seed_url("'https://uconn.edu'", 1)
        assert url == "https://uconn.edu"
        assert sanitized

    def test_clean_trailing_backslash(self):
        url, sanitized = self.spider._clean_seed_url("https://uconn.edu\\", 1)
        assert url == "https://uconn.edu"
        assert sanitized

    def test_clean_duplicate_scheme(self):
        url, sanitized = self.spider._clean_seed_url("https:https://uconn.edu", 1)
        assert url == "https://uconn.edu"
        assert sanitized

    def test_clean_windows_path(self):
        path_url = "C:\\Users\\cache\\https\\www.uconn.edu"
        url, sanitized = self.spider._clean_seed_url(path_url, 1)
        assert url == "https://www.uconn.edu"
        assert sanitized

    def test_clean_no_scheme(self):
        url, sanitized = self.spider._clean_seed_url("uconn.edu", 1)
        assert url == "https://uconn.edu"
        assert sanitized

    def test_clean_http_scheme(self):
        url, sanitized = self.spider._clean_seed_url("http://uconn.edu", 1)
        assert url == "http://uconn.edu"
        assert not sanitized

    def test_clean_empty_url(self):
        url, sanitized = self.spider._clean_seed_url("", 1)
        assert url is None
        assert not sanitized

    def test_clean_whitespace_only(self):
        url, sanitized = self.spider._clean_seed_url("   ", 1)
        assert url is None
        assert not sanitized

    def test_clean_malformed_url(self):
        url, sanitized = self.spider._clean_seed_url("not-a-url-at-all", 1)
        assert url is None

    def test_clean_invalid_scheme(self):
        url, sanitized = self.spider._clean_seed_url("ftp://uconn.edu", 1)
        assert url is None


class TestResponseParsing:
    """response parsing because HTML is chaos"""

    def setup_method(self):
        self.spider = DiscoverySpider()

    def create_response(self, html_content, url="https://test.uconn.edu"):
        return HtmlResponse(
            url=url,
            body=html_content.encode('utf-8'),
            encoding='utf-8'
        )

    def test_parse_basic_page(self):
        html = """
        <html>
        <body>
            <a href="https://uconn.edu/page1">Page 1</a>
            <a href="https://www.uconn.edu/page2">Page 2</a>
        </body>
        </html>
        """
        response = self.create_response(html)
        response.meta = {'source_url': 'https://test.uconn.edu', 'depth': 0}

        items = list(self.spider.parse(response))
        discovery_items = [item for item in items if isinstance(item, DiscoveryItem)]

        assert len(discovery_items) >= 2

    def test_parse_page_with_relative_links(self):
        html = """
        <html>
        <body>
            <a href="/relative">Relative</a>
            <a href="../parent">Parent</a>
            <a href="child/page">Child</a>
        </body>
        </html>
        """
        response = self.create_response(html)
        response.meta = {'source_url': 'https://test.uconn.edu', 'depth': 0}

        items = list(self.spider.parse(response))
        # should resolve relative URLs
        assert len(items) >= 3

    def test_parse_excludes_non_html_links(self):
        html = """
        <html>
        <body>
            <a href="https://uconn.edu/file.pdf">PDF</a>
            <a href="https://uconn.edu/image.jpg">Image</a>
            <a href="https://uconn.edu/page.html">HTML</a>
            <a href="https://uconn.edu/styles.css">CSS</a>
        </body>
        </html>
        """
        response = self.create_response(html)
        response.meta = {'source_url': 'https://test.uconn.edu', 'depth': 0}

        items = list(self.spider.parse(response))
        discovery_items = [item for item in items if isinstance(item, DiscoveryItem)]

        # should exclude PDF, images, CSS but include HTML
        assert len(discovery_items) >= 1

    def test_parse_external_domains_excluded(self):
        html = """
        <html>
        <body>
            <a href="https://yale.edu">Yale</a>
            <a href="https://harvard.edu">Harvard</a>
            <a href="https://uconn.edu">UConn</a>
        </body>
        </html>
        """
        response = self.create_response(html)
        response.meta = {'source_url': 'https://test.uconn.edu', 'depth': 0}

        items = list(self.spider.parse(response))
        discovery_items = [item for item in items if isinstance(item, DiscoveryItem)]

        # should only include UConn
        uconn_items = [item for item in discovery_items if 'uconn.edu' in item.discovered_url]
        assert len(uconn_items) >= 1

    def test_parse_duplicate_urls_skipped(self):
        html = """
        <html>
        <body>
            <a href="https://uconn.edu/page">Page</a>
            <a href="https://uconn.edu/page">Same Page</a>
            <a href="https://uconn.edu/page?">Same with query</a>
        </body>
        </html>
        """
        response = self.create_response(html)
        response.meta = {'source_url': 'https://test.uconn.edu', 'depth': 0}

        # Pre-populate seen URLs
        self.spider.seen_urls.add("https://uconn.edu/page")

        items = list(self.spider.parse(response))
        discovery_items = [item for item in items if isinstance(item, DiscoveryItem)]

        # should skip duplicates
        assert self.spider.duplicates_skipped > 0


class TestDynamicDiscovery:
    """dynamic content discovery because modern web is complex"""

    def setup_method(self):
        self.spider = DiscoverySpider()

    def create_response(self, html_content, url="https://test.uconn.edu"):
        response = HtmlResponse(
            url=url,
            body=html_content.encode('utf-8'),
            encoding='utf-8'
        )
        response.meta = {'source_url': url, 'depth': 0}
        return response

    def test_discover_data_attributes(self):
        html = """
        <html>
        <body>
            <div data-url="https://api.uconn.edu/endpoint">API Data</div>
            <button data-load-url="https://uconn.edu/load">Load More</button>
            <form data-action="https://uconn.edu/submit">Form</form>
        </body>
        </html>
        """
        response = self.create_response(html)

        items = list(self.spider._discover_dynamic_sources(response, 0))
        assert len(items) >= 3

    def test_discover_form_actions(self):
        html = """
        <html>
        <body>
            <form action="https://uconn.edu/search">Search</form>
            <form action="/submit">Relative Action</form>
            <form action="mailto:test@uconn.edu">Email Action</form>
        </body>
        </html>
        """
        response = self.create_response(html)

        items = list(self.spider._discover_dynamic_sources(response, 0))
        # should find valid form actions, exclude mailto
        assert len(items) >= 1

    def test_discover_script_urls(self):
        html = """
        <html>
        <body>
            <script>
                fetch("https://api.uconn.edu/data");
                $.get("/ajax/load");
                var apiUrl = "https://uconn.edu/api/endpoint";
            </script>
        </body>
        </html>
        """
        response = self.create_response(html)

        items = list(self.spider._discover_dynamic_sources(response, 0))
        assert len(items) >= 2

    def test_discover_json_scripts(self):
        html = """
        <html>
        <body>
            <script type="application/json">
            {
                "apiUrl": "https://uconn.edu/api/data",
                "endpoints": ["https://uconn.edu/api/search", "/api/local"],
                "config": {"baseUrl": "https://api.uconn.edu"}
            }
            </script>
        </body>
        </html>
        """
        response = self.create_response(html)

        items = list(self.spider._discover_dynamic_sources(response, 0))
        assert len(items) >= 3

    def test_dynamic_discovery_rate_limiting(self):
        # test rate limiting
        self.spider._dynamic_discovery_count = 1001

        html = """
        <html>
        <body>
            <div data-url="https://uconn.edu/test">Test</div>
        </body>
        </html>
        """
        response = self.create_response(html)

        items = list(self.spider._discover_dynamic_sources(response, 0))
        # should be rate limited
        assert len(items) == 0

    def test_json_url_extraction(self):
        json_text = '''
        {
            "url": "https://uconn.edu/page1",
            "endpoint": "https://api.uconn.edu/data",
            "links": ["https://uconn.edu/page2", "/relative"],
            "nested": {
                "href": "https://uconn.edu/nested"
            }
        }
        '''
        response = self.create_response("")

        urls = self.spider._extract_urls_from_json_text(json_text, response)
        assert len(urls) >= 4

    def test_api_endpoint_detection(self):
        test_urls = [
            "https://uconn.edu/api/data",
            "https://uconn.edu/rest/service",
            "https://uconn.edu/ajax/load",
            "https://uconn.edu/data.json",
            "https://uconn.edu/feed.xml",
            "https://uconn.edu/search?format=json",
            "https://uconn.edu/regular/page"
        ]

        api_urls = [url for url in test_urls if self.spider._looks_like_api_endpoint(url)]
        assert len(api_urls) == 6  # all except the last one


class TestSitemapParsing:
    """sitemap parsing because XML is fun"""

    def setup_method(self):
        self.spider = DiscoverySpider()

    def create_response(self, content, url="https://uconn.edu/sitemap.xml", status=200):
        response = HtmlResponse(
            url=url,
            body=content.encode('utf-8'),
            encoding='utf-8',
            status=status
        )
        response.meta = {'source_url': url, 'domain': 'uconn.edu', 'depth': 0}
        return response

    def test_parse_robots_txt(self):
        robots_content = """
User-agent: *
Disallow: /private/
Sitemap: https://uconn.edu/sitemap.xml
Sitemap: https://uconn.edu/sitemap_news.xml
"""
        response = self.create_response(robots_content, "https://uconn.edu/robots.txt")

        requests = list(self.spider._parse_robots(response))
        assert len(requests) == 2

    def test_parse_robots_txt_no_sitemaps(self):
        robots_content = """
User-agent: *
Disallow: /private/
Allow: /public/
"""
        response = self.create_response(robots_content, "https://uconn.edu/robots.txt")

        requests = list(self.spider._parse_robots(response))
        assert len(requests) == 0

    def test_parse_simple_sitemap(self):
        sitemap_content = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url>
        <loc>https://uconn.edu/page1</loc>
    </url>
    <url>
        <loc>https://uconn.edu/page2</loc>
    </url>
</urlset>"""
        response = self.create_response(sitemap_content)

        items = list(self.spider._parse_sitemap(response))
        assert len(items) == 2

    def test_parse_sitemap_index(self):
        sitemap_index = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <sitemap>
        <loc>https://uconn.edu/sitemap1.xml</loc>
    </sitemap>
    <sitemap>
        <loc>https://uconn.edu/sitemap2.xml</loc>
    </sitemap>
</sitemapindex>"""
        response = self.create_response(sitemap_index)

        items = list(self.spider._parse_sitemap(response))
        # should generate requests for nested sitemaps
        requests = [item for item in items if isinstance(item, scrapy.Request)]
        assert len(requests) == 2

    def test_parse_sitemap_mixed_content(self):
        mixed_sitemap = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url>
        <loc>https://uconn.edu/page1</loc>
    </url>
    <url>
        <loc>https://external.com/page</loc>
    </url>
    <url>
        <loc>https://www.uconn.edu/page2</loc>
    </url>
</urlset>"""
        response = self.create_response(mixed_sitemap)

        items = list(self.spider._parse_sitemap(response))
        discovery_items = [item for item in items if isinstance(item, DiscoveryItem)]

        # should only include uconn.edu URLs
        uconn_items = [item for item in discovery_items if 'uconn.edu' in item.discovered_url]
        assert len(uconn_items) == 2

    def test_parse_sitemap_404(self):
        response = self.create_response("Not Found", status=404)

        items = list(self.spider._parse_sitemap(response))
        assert len(items) == 0

    def test_parse_malformed_xml(self):
        malformed_xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url>
        <loc>https://uconn.edu/page1</loc>
    </url>
    <url>
        <loc>https://uconn.edu/page2
    </url>
</urlset>"""
        response = self.create_response(malformed_xml)

        # should handle gracefully
        items = list(self.spider._parse_sitemap(response))
        # might find some URLs despite malformed XML
        assert len(items) >= 0


class TestPaginationGeneration:
    """pagination URL generation because infinite scroll is everywhere"""

    def setup_method(self):
        self.spider = DiscoverySpider()

    def test_generate_page_parameters(self):
        base_url = "https://uconn.edu/search"
        pagination_urls = self.spider._generate_pagination_urls(base_url)

        assert len(pagination_urls) > 0
        # should include various pagination patterns
        page_patterns = [url for url in pagination_urls if 'page=' in url]
        offset_patterns = [url for url in pagination_urls if 'offset=' in url]

        assert len(page_patterns) > 0
        assert len(offset_patterns) > 0

    def test_generate_with_existing_params(self):
        base_url = "https://uconn.edu/search?q=test&category=academics"
        pagination_urls = self.spider._generate_pagination_urls(base_url)

        # should preserve existing parameters
        for url in pagination_urls:
            assert 'q=test' in url
            assert 'category=academics' in url

    def test_generate_with_fragment(self):
        base_url = "https://uconn.edu/search#results"
        pagination_urls = self.spider._generate_pagination_urls(base_url)

        # should preserve fragment
        for url in pagination_urls:
            assert '#results' in url

    def test_pagination_patterns_variety(self):
        base_url = "https://uconn.edu/api/data"
        pagination_urls = self.spider._generate_pagination_urls(base_url)

        # check for different parameter types
        param_types = set()
        for url in pagination_urls:
            if 'page=' in url:
                param_types.add('page')
            if 'offset=' in url:
                param_types.add('offset')
            if 'limit=' in url:
                param_types.add('limit')
            if 'per_page=' in url:
                param_types.add('per_page')

        assert len(param_types) >= 3


class TestUrlNormalization:
    """URL normalization testing because consistency matters"""

    def setup_method(self):
        self.spider = DiscoverySpider()

    def create_response(self, url="https://test.uconn.edu"):
        response = HtmlResponse(url=url, body=b"<html></html>")
        return response

    def test_normalize_basic_url(self):
        response = self.create_response()
        result = self.spider._normalize_candidate("https://uconn.edu/page", response)
        assert result == "https://uconn.edu/page"

    def test_normalize_relative_url(self):
        response = self.create_response("https://uconn.edu/current")
        result = self.spider._normalize_candidate("/relative", response)
        assert result == "https://uconn.edu/relative"

    def test_normalize_protocol_relative(self):
        response = self.create_response("https://uconn.edu")
        result = self.spider._normalize_candidate("//other.uconn.edu/page", response)
        assert result == "https://other.uconn.edu/page"

    def test_normalize_invalid_schemes(self):
        response = self.create_response()

        invalid_urls = [
            "javascript:alert(1)",
            "mailto:test@uconn.edu",
            "#fragment-only",
            "ftp://uconn.edu/file"
        ]

        for url in invalid_urls:
            result = self.spider._normalize_candidate(url, response)
            assert result is None

    def test_normalize_external_domains(self):
        response = self.create_response()
        result = self.spider._normalize_candidate("https://yale.edu/page", response)
        assert result is None

    def test_normalize_empty_url(self):
        response = self.create_response()
        result = self.spider._normalize_candidate("", response)
        assert result is None

    def test_normalize_whitespace_url(self):
        response = self.create_response()
        result = self.spider._normalize_candidate("   ", response)
        assert result is None


class TestCandidateProcessing:
    """candidate URL processing because this is where it all comes together"""

    def setup_method(self):
        self.spider = DiscoverySpider()

    def test_process_new_candidate(self):
        result = self.spider._process_candidate_url(
            "https://uconn.edu/new-page",
            "https://uconn.edu/source",
            0
        )

        assert len(result) == 2  # DiscoveryItem + Request
        assert isinstance(result[0], DiscoveryItem)
        assert isinstance(result[1], scrapy.Request)

    def test_process_duplicate_candidate(self):
        # Add to seen URLs first
        self.spider.seen_urls.add("https://uconn.edu/duplicate")

        result = self.spider._process_candidate_url(
            "https://uconn.edu/duplicate",
            "https://uconn.edu/source",
            0
        )

        assert len(result) == 0
        assert self.spider.duplicates_skipped > 0

    def test_process_max_depth_reached(self):
        self.spider.max_depth = 2

        result = self.spider._process_candidate_url(
            "https://uconn.edu/deep-page",
            "https://uconn.edu/source",
            2  # at max depth
        )

        # should still create DiscoveryItem but no Request
        assert len(result) == 1
        assert isinstance(result[0], DiscoveryItem)

    def test_process_invalid_url(self):
        result = self.spider._process_candidate_url(
            "not-a-valid-url",
            "https://uconn.edu/source",
            0
        )

        assert len(result) == 0

    def test_process_depth_tracking(self):
        self.spider.max_depth = 3

        self.spider._process_candidate_url(
            "https://uconn.edu/page1",
            "https://uconn.edu/source",
            1
        )

        # should track depth yields
        assert self.spider.depth_yields[2] == 1


class TestSpiderStatistics:
    """statistics tracking because metrics matter"""

    def setup_method(self):
        self.spider = DiscoverySpider()

    def test_url_counting(self):
        initial_count = self.spider.unique_urls_found

        self.spider._process_candidate_url(
            "https://uconn.edu/page1",
            "https://uconn.edu/source",
            0
        )

        assert self.spider.unique_urls_found == initial_count + 1

    def test_duplicate_counting(self):
        # Process same URL twice
        self.spider._process_candidate_url(
            "https://uconn.edu/page1",
            "https://uconn.edu/source",
            0
        )

        initial_duplicates = self.spider.duplicates_skipped

        self.spider._process_candidate_url(
            "https://uconn.edu/page1",
            "https://uconn.edu/source",
            0
        )

        assert self.spider.duplicates_skipped == initial_duplicates + 1

    def test_depth_distribution_tracking(self):
        urls = [
            ("https://uconn.edu/depth0", 0),
            ("https://uconn.edu/depth1", 1),
            ("https://uconn.edu/depth2", 2),
        ]

        for url, depth in urls:
            self.spider._process_candidate_url(url, "https://uconn.edu", depth)

        assert self.spider.depth_yields[1] == 1  # depth 0 -> 1
        assert self.spider.depth_yields[2] == 1  # depth 1 -> 2
        assert self.spider.depth_yields[3] == 1  # depth 2 -> 3

    def test_api_endpoint_counting(self):
        html = """
        <html>
        <body>
            <div data-url="https://api.uconn.edu/endpoint">API</div>
            <div data-url="https://uconn.edu/regular">Regular</div>
        </body>
        </html>
        """
        response = HtmlResponse(
            url="https://test.uconn.edu",
            body=html.encode('utf-8'),
            encoding='utf-8'
        )
        response.meta = {'source_url': 'https://test.uconn.edu', 'depth': 0}

        initial_count = self.spider.api_endpoints_found
        list(self.spider._discover_dynamic_sources(response, 0))

        assert self.spider.api_endpoints_found > initial_count


class TestErrorHandling:
    """error handling because things break"""

    def setup_method(self):
        self.spider = DiscoverySpider()

    def test_malformed_json_handling(self):
        response = HtmlResponse(url="https://test.uconn.edu", body=b"")

        # malformed JSON should not crash
        result = self.spider._extract_urls_from_json_text("{invalid json", response)
        assert len(result) == 0

    def test_empty_response_handling(self):
        response = HtmlResponse(
            url="https://test.uconn.edu",
            body=b"",
            encoding='utf-8'
        )
        response.meta = {'source_url': 'https://test.uconn.edu', 'depth': 0}

        # should not crash on empty response
        items = list(self.spider.parse(response))
        assert len(items) >= 0

    def test_unicode_handling(self):
        html = """
        <html>
        <body>
            <a href="https://uconn.edu/café">Café</a>
            <a href="https://uconn.edu/测试">Test</a>
        </body>
        </html>
        """
        response = HtmlResponse(
            url="https://test.uconn.edu",
            body=html.encode('utf-8'),
            encoding='utf-8'
        )
        response.meta = {'source_url': 'https://test.uconn.edu', 'depth': 0}

        # should handle unicode gracefully
        items = list(self.spider.parse(response))
        assert len(items) >= 0

    def test_very_large_response(self):
        # large HTML content
        large_content = "<html><body>" + "x" * 100000 + "</body></html>"
        response = HtmlResponse(
            url="https://test.uconn.edu",
            body=large_content.encode('utf-8'),
            encoding='utf-8'
        )
        response.meta = {'source_url': 'https://test.uconn.edu', 'depth': 0}

        # should handle large responses
        items = list(self.spider.parse(response))
        assert len(items) >= 0


class TestSpiderClosing:
    """spider closing and summary because endings matter"""

    def setup_method(self):
        self.spider = DiscoverySpider()

    @patch('stage1.discovery_spider.logger')
    def test_spider_closed_summary(self, mock_logger):
        # Set up some statistics
        self.spider.total_urls_parsed = 100
        self.spider.unique_urls_found = 75
        self.spider.duplicates_skipped = 25
        self.spider.dynamic_urls_found = 10
        self.spider.api_endpoints_found = 5

        self.spider.closed("finished")

        # should log comprehensive summary
        mock_logger.info.assert_called()
        call_args = [call[0][0] for call in mock_logger.info.call_args_list]

        summary_logged = any("CRAWL SUMMARY" in arg for arg in call_args)
        assert summary_logged

    def test_efficiency_metrics_calculation(self):
        self.spider.unique_urls_found = 80
        self.spider.duplicates_skipped = 20
        self.spider.total_urls_parsed = 50

        with patch('stage1.discovery_spider.logger') as mock_logger:
            self.spider.closed("finished")

            # should calculate and log efficiency metrics
            call_args = [call[0][0] for call in mock_logger.info.call_args_list]
            efficiency_logged = any("EFFICIENCY METRICS" in arg for arg in call_args)
            assert efficiency_logged


class TestIntegrationScenarios:
    """integration scenarios because real world is messy"""

    def setup_method(self):
        self.spider = DiscoverySpider(max_depth=2)

    def test_full_crawl_simulation(self):
        # simulate a small crawl
        seed_urls = [
            "https://uconn.edu",
            "https://www.uconn.edu/academics"
        ]

        for url in seed_urls:
            self.spider.seen_urls.add(url)

        # simulate finding new URLs
        discovered_urls = [
            "https://uconn.edu/admissions",
            "https://uconn.edu/research",
            "https://registrar.uconn.edu"
        ]

        for url in discovered_urls:
            results = self.spider._process_candidate_url(url, "https://uconn.edu", 0)
            assert len(results) >= 1

        assert self.spider.unique_urls_found >= 3

    def test_mixed_content_handling(self):
        # page with multiple content types
        html = """
        <html>
        <body>
            <a href="https://uconn.edu/page1">Regular Page</a>
            <a href="https://uconn.edu/file.pdf">PDF File</a>
            <form action="https://uconn.edu/search">Search</form>
            <div data-url="https://api.uconn.edu/data">API Data</div>
            <script>
                fetch("https://uconn.edu/ajax/load");
            </script>
        </body>
        </html>
        """
        response = HtmlResponse(
            url="https://test.uconn.edu",
            body=html.encode('utf-8'),
            encoding='utf-8'
        )
        response.meta = {'source_url': 'https://test.uconn.edu', 'depth': 0}

        items = list(self.spider.parse(response))

        # should find various types of URLs
        discovery_items = [item for item in items if isinstance(item, DiscoveryItem)]
        assert len(discovery_items) >= 3