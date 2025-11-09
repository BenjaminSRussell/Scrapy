import pytest
from scrapy.crawler import CrawlerRunner
from twisted.internet import defer, reactor

from src.stage1.scout_spider import ScoutSpider

@pytest.mark.integration
@pytest.mark.slow
class TestEndToEndCrawl:

    @pytest.mark.skip(reason="Requires full Scrapy reactor setup")
    def test_scout_spider_full_crawl(self, delta_sandbox, redis_clean, http_server):
        host, port = http_server
        start_url = f"http://{host}:{port}/index.html"

        settings = {
            "CLOSESPIDER_TIMEOUT": 10,
            "DEPTH_LIMIT": 2,
        }

        runner = CrawlerRunner(settings=settings)

        @defer.inlineCallbacks
        def crawl():
            yield runner.crawl(ScoutSpider, start_urls=[start_url])
            reactor.stop()

        crawl()
        reactor.run()

        discovered = delta_sandbox.read("stage1_discovery")
        assert len(discovered) > 0

    @pytest.mark.skip(reason="Requires full Scrapy reactor setup")
    def test_deep_dive_spider_respects_depth_limit(self, delta_sandbox, redis_clean):

@pytest.mark.integration
class TestDeltaLakeUnderLoad:

    def test_concurrent_writes(self, delta_sandbox):
        import threading

        def write_batch(batch_id):
            data = [{"url": f"https://example.com/page{i}", "batch": batch_id} for i in range(10)]
            delta_sandbox.write("concurrent_test", data, mode="append")

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

        delta_sandbox.write("rw_test", [{"url": "initial"}], mode="overwrite")

        results = []

        def write_continuously():
            for i in range(10):
                delta_sandbox.write("rw_test", [{"url": f"write{i}"}], mode="append")
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

    @pytest.mark.skip(reason="Requires Postgres connection")
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

        delta_sandbox.write("js_spider_queue", js_items, mode="overwrite")

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

    @pytest.mark.skip(reason="Requires Docker Compose running")
    def test_postgres_db_name(self):
        import psycopg2

        conn = psycopg2.connect(
            host="localhost",
            port=5432,
            user="postgres",
            password="postgres",
            database="scraping_pipeline",
        )

        cur = conn.cursor()
        cur.execute("SELECT current_database()")
        db_name = cur.fetchone()[0]

        assert db_name == "scraping_pipeline"

        conn.close()

    @pytest.mark.skip(reason="Requires Docker Compose running")
    def test_delta_volume_shared(self):
        pass

@pytest.mark.integration
class TestGracefulShutdown:

    def test_closespider_timeout_configured(self):
        from src import settings

        assert hasattr(settings, "CLOSESPIDER_TIMEOUT")
        assert settings.CLOSESPIDER_TIMEOUT > 0
        assert settings.CLOSESPIDER_TIMEOUT == 600

    @pytest.mark.skip(reason="Requires spider run")
    def test_spider_closes_gracefully_on_timeout(self):
        pass
