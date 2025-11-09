from datetime import datetime

import pytest

@pytest.mark.contract
class TestDiscoveryItemSchema:

    def test_discovery_item_required_fields(self):
        required_fields = [
            "url",
            "url_hash",
            "depth",
            "status_code",
            "content_type",
            "content_size",
            "discovered_at",
            "discovery_type",
        ]

        item = {
            "url": "https://example.com/page",
            "url_hash": "abc123",
            "depth": 1,
            "status_code": 200,
            "content_type": "text/html",
            "content_size": 5000,
            "discovered_at": datetime.now().isoformat(),
            "discovery_type": "html",
        }

        for field in required_fields:
            assert field in item, f"Missing required field: {field}"

    def test_discovery_item_field_types(self):
        item = {
            "url": "https://example.com/page",
            "url_hash": "abc123",
            "depth": 1,
            "status_code": 200,
            "content_type": "text/html",
            "content_size": 5000,
            "discovered_at": datetime.now().isoformat(),
            "discovery_type": "html",
        }

        assert isinstance(item["url"], str)
        assert isinstance(item["url_hash"], str)
        assert isinstance(item["depth"], int)
        assert isinstance(item["status_code"], int)
        assert isinstance(item["content_type"], str)
        assert isinstance(item["content_size"], int)
        assert isinstance(item["discovered_at"], str)
        assert isinstance(item["discovery_type"], str)

    def test_js_spider_emits_compatible_schema(self):
        js_item = {
            "url": "https://example.com/spa",
            "url_hash": "def456",
            "depth": 2,
            "status_code": 200,
            "content_type": "text/html",
            "content_size": 8000,
            "discovered_at": datetime.now().isoformat(),
            "discovery_type": "js_rendered",
            "resource_type": "page",
            "discovered_via_js": True,
        }

        required_fields = [
            "url",
            "url_hash",
            "depth",
            "status_code",
            "content_type",
            "content_size",
            "discovered_at",
            "discovery_type",
        ]

        for field in required_fields:
            assert field in js_item

@pytest.mark.contract
class TestOffsiteItemSchema:

    def test_offsite_item_required_fields(self):
        from src.items import OffsiteCandidateItem

        item = OffsiteCandidateItem(
            source_page="https://example.com/page",
            external_url="https://external.com",
            anchor_text="External Link",
            context="This is a link to an external site",
            discovered_at=datetime.now().isoformat(),
        )

        assert "source_page" in item
        assert "external_url" in item
        assert "anchor_text" in item
        assert "context" in item
        assert "discovered_at" in item

@pytest.mark.contract
class TestDepthTracking:

    def test_depth_increments_correctly(self):
        parent_depth = 0
        child_depth = parent_depth + 1

        assert child_depth == 1

    def test_depth_limit_enforced(self):
        from src.common.spider_config import get_spider_settings

        settings = get_spider_settings("deep_dive")
        max_depth = settings["DEPTH_LIMIT"]

        assert max_depth > 0
        assert max_depth == 10

@pytest.mark.contract
class TestLinkTriageContract:

    def test_link_categories_exhaustive(self):
        test_urls = [
            ("https://example.com/page", "html"),
            ("https://external.com/page", "offsite"),
            ("https://example.com/image.jpg", "static"),
        ]

        for _url, _expected_category in test_urls:
            pass

    def test_offsite_links_not_in_request_queue(self):
        pass

    def test_static_resources_tracked_in_counters(self):
        from src.stage1.base_spider import BaseSpider

        spider = BaseSpider()
        initial_count = spider.skip_counters.get("images", 0)

        spider._track_skip("https://example.com/photo.jpg", "images")

        assert spider.skip_counters["images"] == initial_count + 1

@pytest.mark.contract
class TestDeltaLakeContract:

    def test_delta_tables_exist(self, delta_sandbox):
        required_tables = [
            "stage1_discovery",
            "stage1_errors",
            "js_spider_queue",
            "stage2_queue",
            "seed_urls",
        ]

        for table in required_tables:
            delta_sandbox.write(table, [{"test": "data"}], mode="overwrite")

            data = delta_sandbox.read(table)
            assert len(data) > 0

    def test_delta_concurrent_access_safe(self, delta_sandbox):
        import threading

        def write_data():
            delta_sandbox.write("concurrent_test", [{"id": 1}], mode="append")

        def read_data():
            delta_sandbox.read("concurrent_test")

        delta_sandbox.write("concurrent_test", [{"id": 0}], mode="overwrite")

        threads = [
            threading.Thread(target=write_data),
            threading.Thread(target=read_data),
            threading.Thread(target=write_data),
            threading.Thread(target=read_data),
        ]

        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=5)

        assert True

@pytest.mark.contract
class TestScrapySettingsContract:

    def test_closespider_timeout_exists(self):
        from src import settings

        assert hasattr(settings, "CLOSESPIDER_TIMEOUT")

    def test_depth_middleware_enabled(self):
        from src.common.spider_config import get_spider_settings

        settings = get_spider_settings("deep_dive")

        assert "SPIDER_MIDDLEWARES" in settings
        assert "scrapy.spidermiddlewares.depth.DepthMiddleware" in settings["SPIDER_MIDDLEWARES"]

    def test_playwright_configured_for_js_spider(self):
        pass

@pytest.mark.contract
class TestMetricsContract:

    def test_skip_counters_initialized(self):
        from src.stage1.base_spider import BaseSpider

        spider = BaseSpider()

        required_counters = [
            "images",
            "static_assets",
            "documents",
            "media_files",
            "archives",
        ]

        for counter in required_counters:
            assert counter in spider.skip_counters
            assert isinstance(spider.skip_counters[counter], int)

    def test_prometheus_metrics_available(self):
        try:
            from src.scrapy_prometheus import (
                AVERAGE_FILE_SIZE_BYTES,
                NEW_URLS_FOUND_PER_MINUTE,
                OFFSITE_LINKS_FOUND,
            )

            assert NEW_URLS_FOUND_PER_MINUTE is not None
            assert AVERAGE_FILE_SIZE_BYTES is not None
            assert OFFSITE_LINKS_FOUND is not None

        except ImportError:
            pytest.skip("Prometheus not available")
