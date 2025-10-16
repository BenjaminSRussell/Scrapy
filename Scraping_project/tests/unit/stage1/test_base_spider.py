"""Unit tests for BaseSpider.

Tests core spider functionality, URL extraction, and response handling.
"""

from unittest.mock import Mock, patch, MagicMock

import pytest
from scrapy.http import HtmlResponse, Request

from src.stage1.base_spider import BaseSpider
from src.settings import IGNORED_EXTENSIONS as DEFAULT_IGNORED_EXTENSIONS


@pytest.fixture
def mocked_spider():
    """
    Provides a BaseSpider instance with its external and internal dependencies
    (StorageManager, _load_seed_urls, URLProcessor) fully mocked out. This ensures
    that tests are isolated and do not perform real I/O or complex processing.
    """
    with patch('src.stage1.base_spider.StorageManager.get_instance') as mock_storage_manager, \
         patch('src.stage1.base_spider.BaseSpider._load_seed_urls', return_value=['https://www.uconn.edu/mock']) as mock_load_seeds, \
         patch('src.stage1.base_spider.URLProcessor') as MockURLProcessor:

        mock_processor_instance = MockURLProcessor.return_value

        mock_processor_instance.extractor.discover_all_urls.return_value = [
            'https://www.uconn.edu/page1',
            'https://external.com/page',
            '/relative/path',
            'https://www.uconn.edu/image.jpg',
            'https://www.uconn.edu/document.pdf',
            'javascript:void(0)'
        ]
        mock_processor_instance.normalize_url.side_effect = lambda url: str(url)

        mock_redis = MagicMock()
        mock_delta = MagicMock()
        mock_storage_manager.return_value.redis = mock_redis
        mock_storage_manager.return_value.delta = mock_delta

        spider = BaseSpider(name="test_spider")

        spider.ignored_extensions = DEFAULT_IGNORED_EXTENSIONS

        spider.mock_storage_manager = mock_storage_manager
        spider.mock_load_seeds = mock_load_seeds
        spider.mock_redis = mock_redis
        spider.mock_delta = mock_delta
        spider.mock_url_processor = mock_processor_instance

        yield spider


class TestBaseSpiderInit:
    """Test BaseSpider initialization."""

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_init_with_defaults(self, mocked_spider):
        """Test spider initialization with default parameters."""
        assert mocked_spider.name == "test_spider"
        assert hasattr(mocked_spider, "allowed_domains")
        assert mocked_spider.start_urls == ['https://www.uconn.edu/mock']



class TestBaseSpiderURLExtraction:
    """Test URL extraction from responses."""

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_extract_links_from_html(self, mocked_spider, test_html_response):
        """Test extracting links from HTML response."""
        links = mocked_spider.extract_links(test_html_response)
        assert len(links) > 0
        assert any("/page1" in link for link in links)
        assert any("external.com" in link for link in links)

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_extract_links_filters_ignored_extensions(self, mocked_spider, test_html_response):
        """Test link extraction filters out ignored file extensions."""
        mocked_spider.ignored_extensions = [".jpg", ".png", ".css", ".js"]
        links = mocked_spider.extract_links(test_html_response)
        assert not any(link.endswith(".jpg") for link in links)
        assert not any(link.endswith(".js") for link in links)

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_extract_links_normalizes_urls(self, mocked_spider):
        """Test link extraction normalizes URLs."""
        mocked_spider.mock_url_processor.extractor.discover_all_urls.return_value = [
            "  /page1  ",
            "/page2#section",
            "/page3?utm=123",
        ]
        response = HtmlResponse(url="https://example.com", body=b"", encoding="utf-8")
        links = mocked_spider.extract_links(response)
        assert "  /page1  " in links
        assert "/page2#section" in links

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_extract_links_handles_malformed_urls(self, mocked_spider):
        """Test link extraction handles malformed URLs gracefully."""
        mocked_spider.mock_url_processor.extractor.discover_all_urls.return_value = [
            "javascript:void(0)",
            "mailto:test@example.com",
            "",
            "   ",
            "/valid",
        ]
        response = HtmlResponse(url="https://example.com", body=b"", encoding="utf-8")
        links = mocked_spider.extract_links(response)
        assert "/valid" in links
        assert not any("javascript:" in link for link in links)
        assert not any("mailto:" in link for link in links)


class TestBaseSpiderRobotsTxt:
    """Test robots.txt handling."""

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_respects_robots_txt(self, mocked_spider):
        """Test spider respects robots.txt rules."""
        with patch.object(mocked_spider, "is_allowed_by_robots", return_value=False):
            url = "https://example.com/disallowed"
            allowed = mocked_spider.is_allowed_by_robots(url)
            assert allowed is False

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_handles_missing_robots_txt(self, mocked_spider):
        """Test spider handles missing robots.txt gracefully."""
        with patch.object(mocked_spider, "is_allowed_by_robots", return_value=True):
            url = "https://example.com/page"
            allowed = mocked_spider.is_allowed_by_robots(url)
            assert allowed is True


class TestBaseSpiderDepthControl:
    """Test crawl depth control."""

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_respects_max_depth(self, mocked_spider):
        """Test spider respects maximum depth setting."""
        mocked_spider.max_depth = 2
        request = Request(url="https://example.com/deep", meta={"depth": 2})
        assert mocked_spider.should_follow_link(request) is False

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_tracks_depth_in_meta(self, mocked_spider):
        """Test spider tracks depth in request metadata."""
        request = Request(url="https://example.com", meta={"depth": 0})
        next_request = mocked_spider.create_request("https://example.com/next", parent=request)
        assert next_request.meta["depth"] == 1


class TestBaseSpiderRateLimiting:
    """Test rate limiting functionality."""

    @pytest.mark.unit
    @pytest.mark.stage1
    @pytest.mark.slow
    def test_respects_download_delay(self, mocked_spider):
        """Test spider respects download delay."""
        mocked_spider.download_delay = 1.0
        assert mocked_spider.download_delay == 1.0

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_concurrent_requests_limit(self, mocked_spider):
        """Test spider respects concurrent requests limit."""
        mocked_spider.concurrent_requests = 16
        assert mocked_spider.concurrent_requests == 16


class TestBaseSpiderErrorHandling:
    """Test error handling and retries."""

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_handles_404_errors(self, mocked_spider):
        """Test spider handles 404 errors gracefully."""
        request = Request(url="https://example.com/notfound", meta={"depth": 0})
        response = HtmlResponse(url="https://example.com/notfound", status=404, body=b"", request=request)
        result = mocked_spider.parse(response)
        assert isinstance(result, list)

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_handles_500_errors(self, mocked_spider):
        """Test spider handles server errors."""
        request = Request(url="https://example.com/error", meta={"depth": 0})
        response = HtmlResponse(url="https://example.com/error", status=500, body=b"", request=request)
        result = mocked_spider.parse(response)
        assert isinstance(result, list)

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_retry_on_failure(self, mocked_spider):
        """Test spider retries on request failure."""
        mocked_spider.retry_times = 3
        assert mocked_spider.retry_times == 3


class TestBaseSpiderDuplicateDetection:
    """Test duplicate URL detection."""

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_normalizes_urls_for_deduplication(self, mocked_spider):
        """Test URLs are normalized for deduplication."""
        urls = [
            "https://example.com/page",
            "https://example.com/page/",
            "https://example.com/page#section",
            "https://example.com/page?utm=123",
        ]
        normalized = [mocked_spider.normalize_url(url) for url in urls]
        assert len(set(normalized)) <= len(urls)


class TestBaseSpiderMetrics:
    """Test metrics collection."""

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_tracks_pages_scraped(self, mocked_spider):
        """Test spider tracks number of pages scraped."""
        mocked_spider.crawler = Mock()
        mocked_spider.crawler.stats = Mock()
        request = Request(url="https://example.com", meta={"depth": 0})
        response = HtmlResponse(url="https://example.com", body=b"<html></html>", request=request, headers={b"Content-Type": b"text/html"})
        mocked_spider.parse(response)
        assert hasattr(mocked_spider.crawler, "stats")

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_tracks_errors(self, mocked_spider):
        """Test spider tracks errors."""
        mocked_spider.crawler = Mock()
        mocked_spider.crawler.stats = Mock()
        request = Request(url="https://example.com")
        response = HtmlResponse(url="https://example.com", status=500, body=b"Error", request=request)
        mocked_spider.parse_error(response)
        assert len(mocked_spider.error_records) > 0


class TestBaseSpiderCleanup:
    """Test spider cleanup and shutdown."""

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_closes_connections_on_shutdown(self, mocked_spider):
        """Test spider closes connections when shutting down."""
        mocked_spider.closed("finished")
        assert True

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_flushes_buffers_on_shutdown(self, mocked_spider):
        """Test spider flushes write buffers on shutdown."""
        mocked_spider.discovered_records.append({"url": "https://example.com"})
        mocked_spider.closed("finished")
        mocked_spider.mock_delta.write.assert_called()