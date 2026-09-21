import pytest
import pytest_twisted
from scrapy.crawler import CrawlerRunner

from src.stage1.scout_spider import ScoutSpider

@pytest.mark.integration
@pytest.mark.slow
class TestEndToEndCrawl:

    @pytest_twisted.inlineCallbacks
    def test_scout_spider_full_crawl(self, delta_sandbox, redis_clean, http_server, monkeypatch):
        import src.utils.delta as delta_module
        import src.utils.redis as redis_module
        from src.utils.delta import DeltaHelper
        from src.utils.redis import RedisHelper

        # Route the spider's global get_delta()/get_redis() singletons to this
        # test's sandboxed instances, so its writes land where the assertion
        # reads from (get_delta() otherwise defaults to the real ./data/delta_lake
        # path - a separate on-disk location from delta_sandbox's temp dir).
        delta_helper = DeltaHelper(base_path=delta_sandbox.base_path)
        delta_helper._manager = delta_sandbox
        original_write = delta_helper.write

        def sync_write(table_name, data, mode="append", async_write=True):
            # delta_sandbox is constructed with start_workers=False, so an
            # async (queued) write would never drain - force synchronous.
            return original_write(table_name, data, mode=mode, async_write=False)

        monkeypatch.setattr(delta_helper, "write", sync_write)
        monkeypatch.setattr(delta_module, "_delta_helper", delta_helper)

        redis_helper = RedisHelper()
        redis_helper._client = redis_clean
        monkeypatch.setattr(redis_module, "_redis_helper", redis_helper)

        host, port = http_server
        start_url = f"http://{host}:{port}/index.html"

        settings = {
            "CLOSESPIDER_TIMEOUT": 10,
            "DEPTH_LIMIT": 2,
            # pytest-twisted installs the plain SelectReactor by default (it
            # doesn't touch asyncio, so it doesn't fight pytest-asyncio's own
            # per-test event loop management elsewhere in the suite). Scrapy
            # normally insists on AsyncioSelectorReactor; tell it to accept
            # whatever reactor is already installed instead.
            "TWISTED_REACTOR": None,
        }

        runner = CrawlerRunner(settings=settings)
        yield runner.crawl(ScoutSpider, start_urls=[start_url], allowed_domains=["127.0.0.1"])

        discovered = delta_sandbox.read("stage1_discovery")
        assert len(discovered) > 0

    def test_deep_dive_spider_respects_depth_limit(self, delta_sandbox, redis_clean):
        pass


@pytest.mark.integration
class TestDeltaLakeUnderLoad:

    def test_concurrent_writes(self, delta_sandbox):
        import threading

        def write_batch(batch_id):
            data = [{"url": f"https://example.com/page{i}", "batch": batch_id} for i in range(10)]
            delta_sandbox.write("concurrent_test", data, mode="append", async_write=False)

        threads = []
        for i in range(5):
            t = threading.Thread(target=write_batch, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=5)

        results = delta_sandbox.read("concurrent_test")
        assert len(results) == 50

    def test_read_while_writing(self, delta_sandbox):
        import threading
        import time

        delta_sandbox.write("rw_test", [{"url": "initial"}], mode="overwrite", async_write=False)

        results = []

        def write_continuously():
            for i in range(10):
                delta_sandbox.write("rw_test", [{"url": f"write{i}"}], mode="append", async_write=False)
                time.sleep(0.1)

        def read_continuously():
            for _ in range(10):
                data = delta_sandbox.read("rw_test")
                results.append(len(data))
                time.sleep(0.1)

        writer = threading.Thread(target=write_continuously)
        reader = threading.Thread(target=read_continuously)

        writer.start()
        reader.start()

        writer.join(timeout=5)
        reader.join(timeout=5)

        assert len(results) > 0

@pytest.mark.integration
class TestPostgresMetrics:

    def test_write_spider_metrics(self, postgres_clean):
        metrics = {
            "spider_name": "scout",
            "urls_processed": 100,
            "errors": 5,
            "timestamp": "2024-01-01T00:00:00",
        }

        postgres_clean.execute(
            """
            INSERT INTO spider_stats (spider_name, urls_processed, errors, timestamp)
            VALUES (%(spider_name)s, %(urls_processed)s, %(errors)s, %(timestamp)s)
            """,
            metrics,
        )

        result = postgres_clean.query("SELECT * FROM spider_stats WHERE spider_name = 'scout'")
        assert len(result) == 1
        assert result[0]["urls_processed"] == 100

@pytest.mark.integration
class TestQueueFlow:

    def test_js_queue_roundtrip(self, delta_sandbox, redis_clean):
        js_items = [
            {
                "url": "https://example.com/spa",
                "url_hash": "abc123",
                "depth": 1,
                "confidence": 0.85,
                "status": "pending",
                "queued_at": "2024-01-01T00:00:00",
            }
        ]

        delta_sandbox.write("js_spider_queue", js_items, mode="overwrite", async_write=False)

        queue = delta_sandbox.read("js_spider_queue")
        assert len(queue) == 1
        assert queue[0]["status"] == "pending"

    def test_offsite_links_captured(self, delta_sandbox):
        from src.stage1.base_spider import BaseSpider

        spider = BaseSpider()
        spider.allowed_domains = ["example.com"]

@pytest.mark.integration
@pytest.mark.slow
class TestDockerComposeStack:

    def test_postgres_db_name(self):
        import os

        import psycopg2

        expected_db_name = os.getenv("DB_NAME", "scraping_pipeline")

        try:
            conn = psycopg2.connect(
                host=os.getenv("DB_HOST", "localhost"),
                port=int(os.getenv("DB_PORT", "5432")),
                user=os.getenv("DB_USER", "postgres"),
                password=os.getenv("DB_PASSWORD", "postgres"),
                database=expected_db_name,
            )
        except psycopg2.OperationalError as exc:
            pytest.skip(f"Postgres not reachable: {exc}")

        cur = conn.cursor()
        cur.execute("SELECT current_database()")
        db_name = cur.fetchone()[0]

        assert db_name == expected_db_name

        conn.close()

    def test_delta_volume_shared(self):
        pass

@pytest.mark.integration
class TestGracefulShutdown:

    def test_closespider_timeout_configured(self):
        from src import settings

        assert hasattr(settings, "CLOSESPIDER_TIMEOUT")
        assert settings.CLOSESPIDER_TIMEOUT > 0
        assert settings.CLOSESPIDER_TIMEOUT == 600

    def test_spider_closes_gracefully_on_timeout(self):
        pass
