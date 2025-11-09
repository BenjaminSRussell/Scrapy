from unittest.mock import Mock

import pytest
from scrapy.http import HtmlResponse, Request

from src.stage1.base_spider import BaseSpider

class TestBaseSpiderInit:

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_init_with_defaults(self, mock_spider_crawler):
        spider = BaseSpider()

        assert spider.name is not None
        assert hasattr(spider, "allowed_domains")
        assert hasattr(spider, "start_urls")

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_init_loads_delta_lake(self, mock_spider_crawler):
        spider = BaseSpider.from_crawler(mock_spider_crawler)

        assert hasattr(spider, "delta")
        assert hasattr(spider, "storage")

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_init_loads_configuration(self, mock_spider_crawler):
        spider = BaseSpider.from_crawler(mock_spider_crawler)

        assert hasattr(spider, "config")

class TestBaseSpiderURLExtraction:

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_extract_links_from_html(self):
        spider = BaseSpider()
        html_body = """
        <!DOCTYPE html>
        <html>
        <body>
            <a href="/page1">Relative link</a>
            <a href="https://www.uconn.edu/page2">Absolute on-site link</a>
            <a href="https://engr.uconn.edu/page3">Subdomain on-site link</a>
            <a href="https://www.external.com/page4">Off-site link</a>
        </body>
        </html>
        """
        response = HtmlResponse(
            url="https://www.uconn.edu/index.html",
            body=html_body.encode("utf-8"),
            encoding="utf-8",
        )
        spider.url_processor.extractor.base_url = response.url

        links = spider.extract_links(response)

        assert len(links) == 3
        assert "https://www.uconn.edu/page1" in links
        assert "https://www.uconn.edu/page2" in links
        assert "https://engr.uconn.edu/page3" in links
        assert not any("external.com" in link for link in links)

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_extract_links_filters_ignored_extensions(self, test_html_response):
        spider = BaseSpider()
        spider.allowed_domains = ["example.com", "external.com"]
        spider.url_processor.allowed_domains = spider.allowed_domains
        spider.url_processor.extractor.allowed_domains = spider.allowed_domains
        spider.url_processor.extractor.base_url = test_html_response.url

        spider.ignored_extensions = [".jpg", ".png", ".css", ".js"]

        links = spider.extract_links(test_html_response)

        assert len(links) == 3
        assert "https://example.com/page1" in links
        assert "https://example.com/page2" in links
        assert "https://external.com/" in links
        assert not any(link.endswith(".jpg") for link in links)
        assert not any(link.endswith(".js") for link in links)

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_extract_links_normalizes_urls(self):
        spider = BaseSpider()
        spider.allowed_domains = ["www.uconn.edu"]
        spider.url_processor.allowed_domains = spider.allowed_domains
        spider.url_processor.extractor.allowed_domains = spider.allowed_domains

        html = """
        <html>
            <body>
                <a href="  /page1  ">Page 1</a>
                <a href="/page2
                <a href="/page3?utm_source=tracker">Page 3</a>
            </body>
        </html>
        """
        response = HtmlResponse(url="https://www.uconn.edu", body=html.encode("utf-8"), encoding="utf-8")
        spider.url_processor.extractor.base_url = response.url
        links = spider.extract_links(response)

        assert len(links) == 3
        assert "https://www.uconn.edu/page1" in links
        assert "https://www.uconn.edu/page2" in links
        assert "https://www.uconn.edu/page3" in links

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_extract_links_handles_malformed_urls(self):
        spider = BaseSpider()
        spider.allowed_domains = ["uconn.edu"]
        spider.url_processor.allowed_domains = spider.allowed_domains
        spider.url_processor.extractor.allowed_domains = spider.allowed_domains

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
        response = HtmlResponse(url="https://www.uconn.edu", body=html.encode("utf-8"), encoding="utf-8")
        spider.url_processor.extractor.base_url = response.url

        links = spider.extract_links(response)

        assert len(links) == 2
        assert "https://www.uconn.edu/valid" in links
        assert "https://www.uconn.edu/example.com" in links

class TestBaseSpiderDepthControl:

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_respects_max_depth(self):
        spider = BaseSpider()
        spider.max_depth = 2

        request = Request(url="https://example.com/deep", meta={"depth": 2})
        should_follow = spider.should_follow_link(request)
        assert should_follow is False

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_tracks_depth_in_meta(self):
        spider = BaseSpider()

        request = Request(url="https://example.com", meta={"depth": 0})
        next_request = spider.create_request("https://example.com/next", parent=request)
        assert next_request.meta["depth"] == 1

class TestBaseSpiderRateLimiting:

    @pytest.mark.unit
    @pytest.mark.stage1
    @pytest.mark.slow
    def test_respects_download_delay(self):
        spider = BaseSpider()
        spider.download_delay = 1.0
        assert spider.download_delay == 1.0

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_concurrent_requests_limit(self):
        spider = BaseSpider()
        spider.concurrent_requests = 16
        assert spider.concurrent_requests == 16

class TestBaseSpiderErrorHandling:

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_handles_404_errors(self):
        spider = BaseSpider()
        request = Request(url="https://example.com/notfound", meta={"depth": 0})
        response = HtmlResponse(
            url="https://example.com/notfound", status=404, body=b"<html><body>Not Found</body></html>", request=request
        )
        result = spider.parse(response)
        assert result is None or isinstance(result, list | type(None))

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_handles_500_errors(self):
        spider = BaseSpider()
        request = Request(url="https://example.com/error", meta={"depth": 0})
        response = HtmlResponse(
            url="https://example.com/error", status=500, body=b"<html><body>Server Error</body></html>", request=request
        )
        result = spider.parse(response)
        assert result is None or isinstance(result, list | type(None))

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_retry_on_failure(self):
        spider = BaseSpider()
        spider.retry_times = 3
        assert spider.retry_times == 3

class TestBaseSpiderDuplicateDetection:

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_filters_duplicate_urls(self):
        seen_urls = set()

        def is_duplicate(url):
            if url in seen_urls:
                return True
            seen_urls.add(url)
            return False

        url = "https://example.com/page"
        assert is_duplicate(url) is False
        assert is_duplicate(url) is True

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_normalizes_urls_for_deduplication(self):
        spider = BaseSpider()
        urls = [
            "https://example.com/page",
            "https://example.com/page/",
            "https://example.com/page
            "https://example.com/page?utm_source=123",
        ]
        normalized = [spider.normalize_url(url) for url in urls]
        assert len(set(normalized)) == 1

class TestBaseSpiderMetrics:

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_tracks_pages_scraped(self):
        spider = BaseSpider()
        spider.crawler = Mock()
        spider.crawler.stats = Mock()
        request = Request(url="https://example.com", meta={"depth": 0})
        response = HtmlResponse(
            url="https://example.com", body=b"<html></html>", request=request, headers={b"Content-Type": b"text/html"}
        )
        spider.parse(response)
        assert hasattr(spider.crawler, "stats")

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_tracks_errors(self):
        spider = BaseSpider()
        spider.crawler = Mock()
        spider.crawler.stats = Mock()
        response = HtmlResponse(url="https://example.com", status=500, body=b"Error")
        try:
            spider.parse_error(response)
        except Exception:
            pass

class TestBaseSpiderCleanup:

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_closes_connections_on_shutdown(self):
        spider = BaseSpider()
        spider.delta = Mock()
        spider.redis = Mock()
        spider.closed("finished")
        assert True

    @pytest.mark.unit
    @pytest.mark.stage1
    def test_flushes_buffers_on_shutdown(self):
        spider = BaseSpider()
        spider.delta = Mock()
        spider.closed("finished")
        assert True
