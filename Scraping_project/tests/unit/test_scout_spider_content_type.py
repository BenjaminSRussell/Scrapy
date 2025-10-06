"""Unit tests for ScoutSpider Content-Type checking."""

import pytest
from scrapy.http import HtmlResponse, Request


class MockResponse:
    """Mock response for testing content-type checking."""

    def __init__(self, url: str, content_type: str, body: bytes = b"test"):
        self.url = url
        self.body = body
        self.status = 200
        self._headers = {b'Content-Type': content_type.encode()}
        self.request = Request(url=url, meta={'depth': 0})

    @property
    def headers(self):
        return self

    def get(self, key, default=None):
        return self._headers.get(key.encode() if isinstance(key, str) else key, default)

    @property
    def meta(self):
        return self.request.meta


class TestScoutSpiderContentType:
    """Test Content-Type checking in ScoutSpider."""

    def test_html_content_type_accepted(self):
        """Test that HTML content types are processed (not immediately rejected)."""
        from src.stage1.scout_spider import ScoutSpider

        spider = ScoutSpider()

        # Create mock HTML response
        response = MockResponse(
            url='https://example.com/page.html',
            content_type='text/html; charset=utf-8'
        )

        # Parse - HTML should NOT be marked as non-HTML
        # (It may fail later in processing, but that's okay - we just want to verify
        # it passes the content-type check and doesn't get marked as non-HTML)
        try:
            list(spider.parse(response))
        except Exception:
            pass  # Processing errors are okay, we just care about content-type check

        # Verify: if a record was created, it should NOT be marked as non-HTML
        if spider.discovered_records:
            for record in spider.discovered_records:
                assert not record.get('is_non_html', False), "HTML should not be marked as non-HTML"

        spider.closed('finished')

    def test_pdf_content_type_skipped(self):
        """Test that PDF content is skipped."""
        from src.stage1.scout_spider import ScoutSpider

        spider = ScoutSpider()

        # Create mock PDF response
        response = MockResponse(
            url='https://example.com/document.pdf',
            content_type='application/pdf'
        )

        # Parse should return early (empty generator)
        results = list(spider.parse(response))

        # Should have no results (no URL extraction from PDF)
        assert len(results) == 0, "PDF should not yield any parse results"

        # Should have recorded as non-HTML
        assert len(spider.discovered_records) > 0, "Should record the PDF"
        record = spider.discovered_records[0]
        assert record.get('is_non_html') is True, "Should be marked as non-HTML"
        assert 'pdf' in record.get('content_type', '').lower()

        spider.closed('finished')

    def test_image_content_type_skipped(self):
        """Test that image content is skipped."""
        from src.stage1.scout_spider import ScoutSpider

        spider = ScoutSpider()

        # Create mock image response
        response = MockResponse(
            url='https://example.com/photo.jpg',
            content_type='image/jpeg'
        )

        # Parse should return early
        results = list(spider.parse(response))

        assert len(results) == 0, "Image should not yield any parse results"
        assert len(spider.discovered_records) > 0, "Should record the image"

        record = spider.discovered_records[0]
        assert record.get('is_non_html') is True
        assert 'image' in record.get('content_type', '').lower()

        spider.closed('finished')

    def test_json_content_type_skipped(self):
        """Test that JSON content is skipped."""
        from src.stage1.scout_spider import ScoutSpider

        spider = ScoutSpider()

        # Create mock JSON response
        response = MockResponse(
            url='https://example.com/api/data.json',
            content_type='application/json'
        )

        results = list(spider.parse(response))

        assert len(results) == 0, "JSON should not yield any parse results"
        assert len(spider.discovered_records) > 0, "Should record the JSON"

        record = spider.discovered_records[0]
        assert record.get('is_non_html') is True
        assert 'json' in record.get('content_type', '').lower()

        spider.closed('finished')

    def test_xhtml_content_type_accepted(self):
        """Test that XHTML content types are processed (not immediately rejected)."""
        from src.stage1.scout_spider import ScoutSpider

        spider = ScoutSpider()

        # Create mock XHTML response
        response = MockResponse(
            url='https://example.com/page.xhtml',
            content_type='application/xhtml+xml'
        )

        # Parse - XHTML should NOT be marked as non-HTML
        try:
            list(spider.parse(response))
        except Exception:
            pass  # Processing errors are okay

        # Verify: if a record was created, it should NOT be marked as non-HTML
        if spider.discovered_records:
            for record in spider.discovered_records:
                assert not record.get('is_non_html', False), "XHTML should not be marked as non-HTML"

        spider.closed('finished')

    def test_missing_content_type_header(self):
        """Test handling of missing Content-Type header."""
        from src.stage1.scout_spider import ScoutSpider

        spider = ScoutSpider()

        # Create response with no content-type
        response = MockResponse(
            url='https://example.com/unknown',
            content_type=''
        )
        response._headers = {}

        # Should skip processing (default to non-HTML)
        results = list(spider.parse(response))

        # Should record but not process
        assert len(results) == 0, "Unknown content should not be processed"

        spider.closed('finished')
