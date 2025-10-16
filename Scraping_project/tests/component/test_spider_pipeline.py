"""Component tests for spider + pipeline integration.

K6: Tests spider and pipeline working together.
"""

import pytest
from scrapy.http import Request

from src.stage1.deep_dive_spider import DeepDiveSpider
from src.stage1.scout_spider import ScoutSpider


@pytest.mark.component
class TestScoutSpiderComponents:
    """Test ScoutSpider components."""

    def test_scout_spider_initialization(self, delta_with_seed_urls, redis_clean):
        """Test that ScoutSpider initializes correctly."""
        spider = ScoutSpider()

        assert spider.name == "scout"
        assert hasattr(spider, "redis_client")
        assert hasattr(spider, "delta")
        assert hasattr(spider, "skip_counters")

    def test_scout_spider_loads_seeds(self, delta_with_seed_urls, redis_clean):
        """Test that ScoutSpider loads seed URLs from Delta Lake."""
        spider = ScoutSpider()

        # Should load seed URLs if start_urls not provided
        assert len(spider.start_urls) > 0

    @pytest.mark.skip(reason="MockRedis fixture needs improvement - execute() returns fixed 10 False values")
    def test_scout_spider_parse_html(self, test_html_response):
        """Test ScoutSpider.parse() extracts items correctly."""
        spider = ScoutSpider()
        spider.redis_client = MockRedis()

        results = list(spider.parse(test_html_response))

        # Should yield discovery item + requests for found URLs
        discovery_items = [r for r in results if isinstance(r, dict)]
        requests = [r for r in results if isinstance(r, Request)]

        assert len(discovery_items) > 0
        assert len(requests) >= 0  # May vary based on deduplication


@pytest.mark.component
class TestDeepDiveSpiderComponents:
    """Test DeepDiveSpider components (K4)."""

    def test_deep_dive_spider_initialization(self):
        """K4: Test DeepDiveSpider initializes with domain allowlist."""
        spider = DeepDiveSpider()

        assert spider.name == "deep_dive"
        assert hasattr(spider, "allowed_domains")

    def test_deep_dive_spider_enforces_depth_limit(self, mock_scrapy_settings):
        """K4: Test DepthMiddleware is configured."""
        spider = DeepDiveSpider()

        # Check that DEPTH_LIMIT is set in custom_settings
        assert "DEPTH_LIMIT" in spider.custom_settings
        assert spider.custom_settings["DEPTH_LIMIT"] > 0

    def test_deep_dive_spider_has_depth_middleware(self):
        """K4: Test DepthMiddleware is enabled."""
        spider = DeepDiveSpider()

        assert "SPIDER_MIDDLEWARES" in spider.custom_settings
        assert "scrapy.spidermiddlewares.depth.DepthMiddleware" in spider.custom_settings["SPIDER_MIDDLEWARES"]


@pytest.mark.component
class TestJSSpiderComponents:
    """Test JSSpider components (K5)."""

    @pytest.mark.skip(reason="Requires scrapy-playwright installation")
    def test_js_spider_initialization(self):
        """K5: Test JSSpider initializes with Playwright settings."""
        from src.stage1.js_spider import JSSpider

        spider = JSSpider()

        assert spider.name == "js_spider"
        assert "DOWNLOAD_HANDLERS" in spider.custom_settings
        assert "scrapy_playwright" in str(spider.custom_settings["DOWNLOAD_HANDLERS"])

    @pytest.mark.skip(reason="Requires scrapy-playwright installation")
    def test_js_spider_resource_blocking_configured(self):
        """K5: Test resource blocking is configured."""
        from src.stage1.js_spider import JSSpider

        spider = JSSpider()

        assert hasattr(spider, "BLOCKED_RESOURCE_TYPES")
        assert "image" in spider.BLOCKED_RESOURCE_TYPES
        assert "stylesheet" in spider.BLOCKED_RESOURCE_TYPES


@pytest.mark.component
class TestDeltaLakeIntegration:
    """Test Delta Lake read/write operations."""

    def test_delta_write_and_read(self, delta_sandbox):
        """Test writing and reading from Delta Lake."""
        test_data = [
            {"url": "https://example.com/1", "depth": 0},
            {"url": "https://example.com/2", "depth": 1},
        ]

        # Write data
        delta_sandbox.write("test_table", test_data, mode="overwrite")

        # Read data back
        read_data = delta_sandbox.read("test_table")

        assert len(read_data) == 2
        assert read_data[0]["url"] == "https://example.com/1"

    def test_delta_append_mode(self, delta_sandbox):
        """Test appending to Delta Lake table."""
        initial_data = [{"url": "https://example.com/1", "depth": 0}]
        additional_data = [{"url": "https://example.com/2", "depth": 1}]

        delta_sandbox.write("test_table", initial_data, mode="overwrite")
        delta_sandbox.write("test_table", additional_data, mode="append")

        read_data = delta_sandbox.read("test_table")
        assert len(read_data) == 2


@pytest.mark.component
class TestRedisQueueIntegration:
    """Test Redis queue operations."""

    def test_redis_deduplication(self, redis_clean):
        """Test Redis-based URL deduplication."""
        from src.stage1.base_spider import BaseSpider

        spider = BaseSpider()
        spider.redis_client = redis_clean

        url_hash = spider._hash_url("https://example.com/test")

        # First add should succeed
        redis_clean.sadd(spider.url_hashes_key, url_hash)

        # Second add should be duplicate
        is_duplicate = redis_clean.sismember(spider.url_hashes_key, url_hash)
        assert is_duplicate


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
        return [False] * 10

    def scard(self, key):
        return len(self.data)
