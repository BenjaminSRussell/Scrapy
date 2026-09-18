import pytest

from src.stage1.base_spider import BaseSpider

@pytest.mark.unit
class TestBaseSpiderURLHashing:

    def test_hash_url_consistency(self):
        spider = BaseSpider()
        url = "https://example.com/page"

        hash1 = spider._hash_url(url)
        hash2 = spider._hash_url(url)

        assert hash1 == hash2
        assert len(hash1) == 16

    def test_hash_url_uniqueness(self):
        spider = BaseSpider()

        hash1 = spider._hash_url("https://example.com/page1")
        hash2 = spider._hash_url("https://example.com/page2")

        assert hash1 != hash2

    def test_hash_url_no_truncation(self):
        spider = BaseSpider()
        url_hash = spider._hash_url("https://example.com/test")

        assert len(url_hash) == 16

@pytest.mark.unit
class TestBaseSpiderExternalURLDetection:

    def test_is_external_url_external(self):
        spider = BaseSpider()
        spider.allowed_domains = ["example.com"]

        assert spider._is_external_url("https://external.com/page")
        assert spider._is_external_url("https://other.org/page")

    def test_is_external_url_internal(self):
        spider = BaseSpider()
        spider.allowed_domains = ["example.com"]

        assert not spider._is_external_url("https://example.com/page")
        assert not spider._is_external_url("https://www.example.com/page")

    def test_is_external_url_subdomain(self):
        spider = BaseSpider()
        spider.allowed_domains = ["example.com"]

        assert not spider._is_external_url("https://sub.example.com/page")
        assert not spider._is_external_url("https://deep.sub.example.com/page")

    def test_is_external_url_with_port(self):
        spider = BaseSpider()
        spider.allowed_domains = ["example.com"]

        assert not spider._is_external_url("https://example.com:8080/page")

@pytest.mark.unit
class TestBaseSpiderResourceCategorization:

    def test_categorize_skip_reason_images(self):
        spider = BaseSpider()

        assert spider._categorize_skip_reason("https://example.com/photo.jpg") == "images"
        assert spider._categorize_skip_reason("https://example.com/icon.png") == "images"
        assert spider._categorize_skip_reason("https://example.com/logo.svg") == "images"

    def test_categorize_skip_reason_static_assets(self):
        spider = BaseSpider()

        assert spider._categorize_skip_reason("https://example.com/style.css") == "static_assets"
        assert spider._categorize_skip_reason("https://example.com/app.js") == "static_assets"
        assert spider._categorize_skip_reason("https://example.com/font.woff2") == "static_assets"

    def test_categorize_skip_reason_documents(self):
        spider = BaseSpider()

        assert spider._categorize_skip_reason("https://example.com/report.pdf") == "documents"
        assert spider._categorize_skip_reason("https://example.com/data.xlsx") == "documents"
        assert spider._categorize_skip_reason("https://example.com/slide.pptx") == "documents"

    def test_categorize_skip_reason_media(self):
        spider = BaseSpider()

        assert spider._categorize_skip_reason("https://example.com/video.mp4") == "media_files"
        assert spider._categorize_skip_reason("https://example.com/audio.mp3") == "media_files"

    def test_categorize_skip_reason_archives(self):
        spider = BaseSpider()

        assert spider._categorize_skip_reason("https://example.com/data.zip") == "archives"
        assert spider._categorize_skip_reason("https://example.com/backup.tar.gz") == "archives"

@pytest.mark.unit
class TestBaseSpiderLinkTriage:

    def test_process_discovered_urls_categorization(self, test_html_response):
        spider = BaseSpider()
        spider.allowed_domains = ["example.com"]
        spider.redis_client = MockRedis()

        discovered_urls = [
            "https://example.com/page1",
            "https://external.com/page",
            "https://example.com/image.jpg",
        ]

        results = list(spider._process_discovered_urls(test_html_response, discovered_urls, depth=0))

        offsite_items = [r for r in results if isinstance(r, dict) and "external_url" in r]
        static_items = [r for r in results if isinstance(r, dict) and "skip_reason" in r]
        html_requests = [r for r in results if hasattr(r, "url")]

        assert len(offsite_items) == 1
        assert len(static_items) == 1
        assert len(html_requests) == 1

    def test_offsite_links_not_followed(self, test_html_response):
        spider = BaseSpider()
        spider.allowed_domains = ["example.com"]
        spider.redis_client = MockRedis()

        discovered_urls = ["https://external.com/page"]

        results = list(spider._process_discovered_urls(test_html_response, discovered_urls, depth=0))

        requests = [r for r in results if hasattr(r, "url")]
        assert len(requests) == 0

@pytest.mark.unit
class TestBaseSpiderCounters:

    def test_track_skip_increments_counter(self):
        spider = BaseSpider()

        spider._track_skip("https://example.com/image.jpg", "images")
        assert spider.skip_counters["images"] == 1

        spider._track_skip("https://example.com/photo.png", "images")
        assert spider.skip_counters["images"] == 2

    def test_track_skip_auto_categorization(self):
        spider = BaseSpider()

        spider._track_skip("https://example.com/style.css")
        assert spider.skip_counters["static_assets"] > 0

# ============================================================================
# ============================================================================

class MockRedisPipeline:

    def __init__(self, parent: "MockRedis"):
        self._parent = parent
        self._commands: list[tuple[str, str, str]] = []

    def sismember(self, key: str, value: str):
        self._commands.append(("sismember", key, value))
        return self

    def sadd(self, key: str, value: str):
        self._commands.append(("sadd", key, value))
        return self

    def execute(self):
        results: list[bool] = []

        for command, _key, value in self._commands:
            if command == "sismember":
                results.append(value in self._parent.data)
            elif command == "sadd":
                self._parent.data.add(value)

        self._commands.clear()
        return results

    def reset(self):
        self._commands.clear()
        return None

class MockRedis:

    def __init__(self):
        self.data: set[str] = set()

    def pipeline(self):
        return MockRedisPipeline(self)

    def scard(self, key):
        return len(self.data)
