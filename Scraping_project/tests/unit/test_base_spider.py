"""Unit tests for BaseSpider.

K6: Tests core spider functionality in isolation.
"""

import pytest

from src.stage1.base_spider import BaseSpider


@pytest.mark.unit
class TestBaseSpiderURLHashing:
    """Test URL hashing functionality."""

    def test_hash_url_consistency(self):
        """Test that same URL produces same hash."""
        spider = BaseSpider()
        url = "https://example.com/page"

        hash1 = spider._hash_url(url)
        hash2 = spider._hash_url(url)

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 produces 64-char hex string

    def test_hash_url_uniqueness(self):
        """Test that different URLs produce different hashes."""
        spider = BaseSpider()

        hash1 = spider._hash_url("https://example.com/page1")
        hash2 = spider._hash_url("https://example.com/page2")

        assert hash1 != hash2

    def test_hash_url_no_truncation(self):
        """K3: Verify full-length hash (no truncation to prevent collisions)."""
        spider = BaseSpider()
        url_hash = spider._hash_url("https://example.com/test")

        # SHA256 produces 64 hex characters
        assert len(url_hash) == 64


@pytest.mark.unit
class TestBaseSpiderExternalURLDetection:
    """Test external URL detection (K3 link triage)."""

    def test_is_external_url_external(self):
        """Test that external URLs are correctly identified."""
        spider = BaseSpider()
        spider.allowed_domains = ['example.com']

        assert spider._is_external_url('https://external.com/page')
        assert spider._is_external_url('https://other.org/page')

    def test_is_external_url_internal(self):
        """Test that internal URLs are correctly identified."""
        spider = BaseSpider()
        spider.allowed_domains = ['example.com']

        assert not spider._is_external_url('https://example.com/page')
        assert not spider._is_external_url('https://www.example.com/page')

    def test_is_external_url_subdomain(self):
        """Test that subdomains are treated as internal."""
        spider = BaseSpider()
        spider.allowed_domains = ['example.com']

        assert not spider._is_external_url('https://sub.example.com/page')
        assert not spider._is_external_url('https://deep.sub.example.com/page')

    def test_is_external_url_with_port(self):
        """Test URL with port number."""
        spider = BaseSpider()
        spider.allowed_domains = ['example.com']

        assert not spider._is_external_url('https://example.com:8080/page')


@pytest.mark.unit
class TestBaseSpiderResourceCategorization:
    """Test resource type categorization."""

    def test_categorize_skip_reason_images(self):
        """Test image file categorization."""
        spider = BaseSpider()

        assert spider._categorize_skip_reason('https://example.com/photo.jpg') == 'images'
        assert spider._categorize_skip_reason('https://example.com/icon.png') == 'images'
        assert spider._categorize_skip_reason('https://example.com/logo.svg') == 'images'

    def test_categorize_skip_reason_static_assets(self):
        """Test static asset categorization."""
        spider = BaseSpider()

        assert spider._categorize_skip_reason('https://example.com/style.css') == 'static_assets'
        assert spider._categorize_skip_reason('https://example.com/app.js') == 'static_assets'
        assert spider._categorize_skip_reason('https://example.com/font.woff2') == 'static_assets'

    def test_categorize_skip_reason_documents(self):
        """Test document file categorization."""
        spider = BaseSpider()

        assert spider._categorize_skip_reason('https://example.com/report.pdf') == 'documents'
        assert spider._categorize_skip_reason('https://example.com/data.xlsx') == 'documents'
        assert spider._categorize_skip_reason('https://example.com/slide.pptx') == 'documents'

    def test_categorize_skip_reason_media(self):
        """Test media file categorization."""
        spider = BaseSpider()

        assert spider._categorize_skip_reason('https://example.com/video.mp4') == 'media_files'
        assert spider._categorize_skip_reason('https://example.com/audio.mp3') == 'media_files'

    def test_categorize_skip_reason_archives(self):
        """Test archive file categorization."""
        spider = BaseSpider()

        assert spider._categorize_skip_reason('https://example.com/data.zip') == 'archives'
        assert spider._categorize_skip_reason('https://example.com/backup.tar.gz') == 'archives'


@pytest.mark.unit
class TestBaseSpiderLinkTriage:
    """K3: Test link triage functionality."""

    def test_process_discovered_urls_categorization(self, test_html_response):
        """K3: Test that URLs are correctly categorized (HTML/offsite/static)."""
        spider = BaseSpider()
        spider.allowed_domains = ['example.com']
        spider.redis_client = MockRedis()

        discovered_urls = [
            'https://example.com/page1',  # HTML candidate
            'https://external.com/page',  # Offsite
            'https://example.com/image.jpg',  # Static
        ]

        results = list(spider._process_discovered_urls(test_html_response, discovered_urls, depth=0))

        # Should yield: 1 offsite item, 1 static item, 1 HTML request
        offsite_items = [r for r in results if isinstance(r, dict) and 'external_url' in r]
        static_items = [r for r in results if isinstance(r, dict) and 'skip_reason' in r]
        html_requests = [r for r in results if hasattr(r, 'url')]

        assert len(offsite_items) == 1
        assert len(static_items) == 1
        assert len(html_requests) == 1

    def test_offsite_links_not_followed(self, test_html_response):
        """K3: Verify offsite links are logged but not followed."""
        spider = BaseSpider()
        spider.allowed_domains = ['example.com']
        spider.redis_client = MockRedis()

        discovered_urls = ['https://external.com/page']

        results = list(spider._process_discovered_urls(test_html_response, discovered_urls, depth=0))

        # Should yield offsite item but no Request
        requests = [r for r in results if hasattr(r, 'url')]
        assert len(requests) == 0  # No follow requests for offsite URLs


@pytest.mark.unit
class TestBaseSpiderCounters:
    """Test skip counter tracking (K3)."""

    def test_track_skip_increments_counter(self):
        """Test that skip tracking increments correct counter."""
        spider = BaseSpider()

        spider._track_skip('https://example.com/image.jpg', 'images')
        assert spider.skip_counters['images'] == 1

        spider._track_skip('https://example.com/photo.png', 'images')
        assert spider.skip_counters['images'] == 2

    def test_track_skip_auto_categorization(self):
        """Test that track_skip auto-categorizes when reason not provided."""
        spider = BaseSpider()

        spider._track_skip('https://example.com/style.css')
        assert spider.skip_counters['static_assets'] > 0


# ============================================================================
# Mock helpers
# ============================================================================

class MockRedis:
    """Mock Redis client for testing."""

    def __init__(self):
        self.data = set()

    def pipeline(self):
        return self

    def sismember(self, key, value):
        return self

    def sadd(self, key, value):
        self.data.add(value)
        return self

    def execute(self):
        return [False] * 10  # Return False for all existence checks

    def scard(self, key):
        return len(self.data)
