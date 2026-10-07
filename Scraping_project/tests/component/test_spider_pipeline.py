import pytest
from scrapy.http import Request

from src.stage1.experimental.deep_dive_spider import DeepDiveSpider
from src.stage1.scout_spider import ScoutSpider

@pytest.mark.component
class TestScoutSpiderComponents:

    def test_scout_spider_initialization(self, delta_with_seed_urls, redis_clean):
        spider = ScoutSpider()

        assert spider.name == "scout"
        assert hasattr(spider, "redis_client")
        assert hasattr(spider, "delta")
        assert hasattr(spider, "skip_counters")

    def test_scout_spider_loads_seeds(self, delta_with_seed_urls, redis_clean):
        spider = ScoutSpider()

        assert len(spider.start_urls) > 0

    def test_scout_spider_parse_html(self, test_html_response, redis_clean):
        spider = ScoutSpider()
        # ScoutSpider defaults to allowed_domains=["uconn.edu"]; match it to
        # test_html_response's example.com URLs so parse() has in-domain
        # links to discover.
        spider.allowed_domains = ["example.com"]
        spider.url_processor.allowed_domains = spider.allowed_domains
        spider.url_processor.extractor.allowed_domains = spider.allowed_domains
        spider.redis_client = redis_clean

        results = list(spider.parse(test_html_response))

        discovery_items = [r for r in results if isinstance(r, dict)]
        requests = [r for r in results if isinstance(r, Request)]

        assert len(discovery_items) > 0
        assert len(requests) >= 0

@pytest.mark.component
class TestDeepDiveSpiderComponents:

    def test_deep_dive_spider_initialization(self):
        spider = DeepDiveSpider()

        assert spider.name == "deep_dive"
        assert hasattr(spider, "allowed_domains")

    def test_deep_dive_spider_enforces_depth_limit(self, mock_scrapy_settings):
        spider = DeepDiveSpider()

        assert "DEPTH_LIMIT" in spider.custom_settings
        assert spider.custom_settings["DEPTH_LIMIT"] > 0

    def test_deep_dive_spider_has_depth_middleware(self):
        spider = DeepDiveSpider()

        assert "SPIDER_MIDDLEWARES" in spider.custom_settings
        assert "scrapy.spidermiddlewares.depth.DepthMiddleware" in spider.custom_settings["SPIDER_MIDDLEWARES"]

@pytest.mark.component
class TestJSSpiderComponents:

    def test_js_spider_initialization(self):
        # scrapy_playwright is only referenced by dotted-path string in
        # DOWNLOAD_HANDLERS (resolved lazily by Scrapy on first real
        # request), so constructing the spider never needs it installed.
        from src.stage1.experimental.js_spider import JavaScriptSpider

        spider = JavaScriptSpider()

        assert spider.name == "javascript"
        assert "DOWNLOAD_HANDLERS" in spider.custom_settings
        assert "scrapy_playwright" in str(spider.custom_settings["DOWNLOAD_HANDLERS"])

    def test_js_spider_resource_blocking_configured(self):
        from src.stage1.experimental.js_spider import JavaScriptSpider

        spider = JavaScriptSpider()

        assert hasattr(spider, "BLOCKED_RESOURCE_TYPES")
        assert "image" in spider.BLOCKED_RESOURCE_TYPES
        assert "stylesheet" in spider.BLOCKED_RESOURCE_TYPES

@pytest.mark.component
class TestDeltaLakeIntegration:

    def test_delta_write_and_read(self, delta_sandbox):
        test_data = [
            {"url": "https://example.com/1", "depth": 0},
            {"url": "https://example.com/2", "depth": 1},
        ]

        delta_sandbox.write("test_table", test_data, mode="overwrite", async_write=False)

        read_data = delta_sandbox.read("test_table")

        assert len(read_data) == 2
        assert read_data[0]["url"] == "https://example.com/1"

    def test_delta_append_mode(self, delta_sandbox):
        initial_data = [{"url": "https://example.com/1", "depth": 0}]
        additional_data = [{"url": "https://example.com/2", "depth": 1}]

        delta_sandbox.write("test_table", initial_data, mode="overwrite", async_write=False)
        delta_sandbox.write("test_table", additional_data, mode="append", async_write=False)

        read_data = delta_sandbox.read("test_table")
        assert len(read_data) == 2

@pytest.mark.component
class TestRedisQueueIntegration:

    def test_redis_deduplication(self, redis_clean):
        from src.stage1.base_spider import BaseSpider

        spider = BaseSpider()
        spider.redis_client = redis_clean

        url_hash = spider._hash_url("https://example.com/test")

        redis_clean.sadd(spider.url_hashes_key, url_hash)

        is_duplicate = redis_clean.sismember(spider.url_hashes_key, url_hash)
        assert is_duplicate
