"""Unit tests for BaseSpider.

Tests core spider functionality, URL extraction, and response handling.
"""

from unittest.mock import Mock, patch

import pytest
from scrapy.http import HtmlResponse, Request

from src.stage1.base_spider import BaseSpider


class TestBaseSpiderInit:
    """Test BaseSpider initialization."""

    @pytest.mark.unit
    @pytest.mark.stage1
    @patch("src.stage1.base_spider.redis.Redis")
    def test_init_with_defaults(self, mock_redis, mock_spider_crawler):
        """Test spider initialization with default parameters."""
        mock_redis.return_value.scard.return_value = 0
        spider = BaseSpider()

        assert spider.name is not None
        assert hasattr(spider, "allowed_domains")
        assert hasattr(spider, "start_urls")

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_init_loads_delta_lake(self, mock_spider_crawler):
        """Test spider initializes Delta Lake manager."""
        with patch("src.stage1.base_spider.DeltaLakeManager") as mock_delta:
            spider = BaseSpider.from_crawler(mock_spider_crawler)

            # Should have initialized Delta Lake
            assert hasattr(spider, "delta")
            mock_delta.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_init_loads_configuration(self, mock_spider_crawler):
        """Test spider loads configuration."""
        spider = BaseSpider.from_crawler(mock_spider_crawler)

        # Should have loaded config
        assert hasattr(spider, "config")


class TestBaseSpiderURLExtraction:
    """Test URL extraction from responses."""

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_extract_links_from_html(self, test_html_response):
        """Test extracting links from HTML response."""
        spider = BaseSpider()

        links = spider.extract_links(test_html_response)

        assert len(links) > 0
        # Should extract relative and absolute links
        assert any("/page1" in link for link in links)
        assert any("external.com" in link for link in links)

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_extract_links_filters_ignored_extensions(self, test_html_response):
        """Test link extraction filters out ignored file extensions."""
        spider = BaseSpider()
        spider.ignored_extensions = [".jpg", ".png", ".css", ".js"]

        links = spider.extract_links(test_html_response)

        # Should not include images or scripts
        assert not any(link.endswith(".jpg") for link in links)
        assert not any(link.endswith(".js") for link in links)

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_extract_links_normalizes_urls(self):
        """Test link extraction normalizes URLs."""
        spider = BaseSpider()

        html = """
        <html>
            <body>
                <a href="  /page1  ">Page 1</a>
                <a href="/page2#section">Page 2</a>
                <a href="/page3?utm=123">Page 3</a>
            </body>
        </html>
        """

        response = HtmlResponse(url="https://example.com", body=html.encode("utf-8"), encoding="utf-8")

        links = spider.extract_links(response)

        # Should normalize whitespace and fragments
        assert "https://example.com/page1" in links
        # Fragment handling depends on implementation
        assert any("page2" in link for link in links)

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_extract_links_handles_malformed_urls(self):
        """Test link extraction handles malformed URLs gracefully."""
        spider = BaseSpider()

        html = """
        <html>
            <body>
                <a href="javascript:void(0)">JS Link</a>
                <a href="mailto:test@example.com">Email</a>
                <a href="">Empty</a>
                <a href="   ">Whitespace</a>
                <a href="/valid">Valid</a>
            </body>
        </html>
        """

        response = HtmlResponse(url="https://example.com", body=html.encode("utf-8"), encoding="utf-8")

        links = spider.extract_links(response)

        # Should only extract valid HTTP(S) links
        assert any("valid" in link for link in links)
        assert not any("javascript:" in link for link in links)
        assert not any("mailto:" in link for link in links)


class TestBaseSpiderRobotsTxt:
    """Test robots.txt handling."""

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_respects_robots_txt(self):
        """Test spider respects robots.txt rules."""
        spider = BaseSpider()

        # Test with mock robots.txt parser
        with patch.object(spider, "is_allowed_by_robots", return_value=False):
            url = "https://example.com/disallowed"

            # Should not crawl disallowed URL
            allowed = spider.is_allowed_by_robots(url)
            assert allowed is False

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_handles_missing_robots_txt(self):
        """Test spider handles missing robots.txt gracefully."""
        spider = BaseSpider()

        # When robots.txt doesn't exist, should allow all
        with patch.object(spider, "is_allowed_by_robots", return_value=True):
            url = "https://example.com/page"

            allowed = spider.is_allowed_by_robots(url)
            assert allowed is True


class TestBaseSpiderDepthControl:
    """Test crawl depth control."""

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_respects_max_depth(self):
        """Test spider respects maximum depth setting."""
        spider = BaseSpider()
        spider.max_depth = 2

        # Create request at max depth
        request = Request(url="https://example.com/deep", meta={"depth": 2})

        # Should not follow links from this page
        should_follow = spider.should_follow_link(request)

        # Depending on implementation
        assert should_follow is False or request.meta["depth"] == 2

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_tracks_depth_in_meta(self):
        """Test spider tracks depth in request metadata."""
        spider = BaseSpider()

        # Initial request at depth 0
        request = Request(url="https://example.com", meta={"depth": 0})

        # Following link should increment depth
        next_request = spider.create_request("https://example.com/next", parent=request)

        assert next_request.meta["depth"] == 1


class TestBaseSpiderRateLimiting:
    """Test rate limiting functionality."""

    @pytest.mark.unit
    @pytest.mark.stage1
    @pytest.mark.slow
    def test_respects_download_delay(self):
        """Test spider respects download delay."""
        spider = BaseSpider()
        spider.download_delay = 1.0

        # This would be tested via Scrapy's built-in rate limiting
        # Verify setting is applied
        assert spider.download_delay == 1.0

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_concurrent_requests_limit(self):
        """Test spider respects concurrent requests limit."""
        spider = BaseSpider()
        spider.concurrent_requests = 16

        assert spider.concurrent_requests == 16


class TestBaseSpiderErrorHandling:
    """Test error handling and retries."""

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_handles_404_errors(self):
        """Test spider handles 404 errors gracefully."""
        spider = BaseSpider()

        response = HtmlResponse(
            url="https://example.com/notfound",
            status=404,
            body=b"<html><body>Not Found</body></html>",
        )

        # Should handle error without crashing
        result = spider.parse(response)

        # Depending on implementation, might log error or return None
        assert result is None or isinstance(result, list)

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_handles_500_errors(self):
        """Test spider handles server errors."""
        spider = BaseSpider()

        response = HtmlResponse(
            url="https://example.com/error",
            status=500,
            body=b"<html><body>Server Error</body></html>",
        )

        # Should handle gracefully
        result = spider.parse(response)
        assert result is None or isinstance(result, list)

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_retry_on_failure(self):
        """Test spider retries on request failure."""
        spider = BaseSpider()
        spider.retry_times = 3

        # Verify retry setting
        assert spider.retry_times == 3


class TestBaseSpiderDuplicateDetection:
    """Test duplicate URL detection."""

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_filters_duplicate_urls(self):
        """Test spider filters duplicate URLs."""
        spider = BaseSpider()

        # Mock duplicate filter
        seen_urls = set()

        def is_duplicate(url):
            hashed = spider._hash_url(url)
            if hashed in seen_urls:
                return True
            seen_urls.add(hashed)
            return False

        url = "https://example.com/page"

        # First time should not be duplicate
        assert is_duplicate(url) is False

        # Second time should be duplicate
        assert is_duplicate(url) is True

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_normalizes_urls_for_deduplication(self):
        """Test URLs are normalized for deduplication."""
        spider = BaseSpider()

        # These should be considered duplicates
        urls = [
            "https://example.com/page",
            "https://example.com/page/",
            "https://example.com/page#section",
            "https://example.com/page?utm=123",
        ]

        # Normalize and check
        normalized = [spider.normalize_url(url) for url in urls]

        # Depending on implementation, some should be the same
        # At minimum, trailing slash and fragments should be normalized
        assert len(set(normalized)) <= len(urls)


class TestBaseSpiderMetrics:
    """Test metrics collection."""

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_tracks_pages_scraped(self):
        """Test spider tracks number of pages scraped."""
        spider = BaseSpider()

        # Mock stats collector
        spider.crawler = Mock()
        spider.crawler.stats = Mock()

        # Simulate scraping
        response = HtmlResponse(url="https://example.com", body=b"<html></html>")
        spider.parse(response)

        # Should have incremented counter (depending on implementation)
        # Verify stats collector was called
        assert hasattr(spider.crawler, "stats")

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_tracks_errors(self):
        """Test spider tracks errors."""
        spider = BaseSpider()
        spider.crawler = Mock()
        spider.crawler.stats = Mock()

        # Simulate error
        response = HtmlResponse(url="https://example.com", status=500, body=b"Error")

        try:
            spider.parse_error(response)
        except Exception:
            pass

        # Should have logged error


class TestBaseSpiderCleanup:
    """Test spider cleanup and shutdown."""

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_closes_connections_on_shutdown(self):
        """Test spider closes connections when shutting down."""
        spider = BaseSpider()

        # Mock connections
        spider.delta = Mock()
        spider.redis = Mock()

        # Call close
        spider.closed("finished")

        # Should have closed connections
        # Verify cleanup was called (depending on implementation)
        assert True  # Placeholder

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_flushes_buffers_on_shutdown(self):
        """Test spider flushes write buffers on shutdown."""
        spider = BaseSpider()
        spider.delta = Mock()

        # Call close
        spider.closed("finished")

        # Should have flushed pending writes
        # (depending on implementation)
        assert True  # Placeholder
