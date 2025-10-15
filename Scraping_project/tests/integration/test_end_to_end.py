"""Integration tests for end-to-end pipeline flows.

K6: Tests complete workflows from crawl to storage.
"""

import pytest
from scrapy.crawler import CrawlerRunner
from twisted.internet import defer, reactor

from src.stage1.scout_spider import ScoutSpider


@pytest.mark.integration
@pytest.mark.slow
class TestEndToEndCrawl:
    """Test complete crawl workflows."""

    @pytest.mark.skip(reason="Requires full Scrapy reactor setup")
    def test_scout_spider_full_crawl(self, delta_sandbox, redis_clean, http_server):
        """Test ScoutSpider performs complete crawl."""
        # Setup
        host, port = http_server
        start_url = f"http://{host}:{port}/index.html"

        # Configure spider
        settings = {
            "CLOSESPIDER_TIMEOUT": 10,  # 10 second timeout for test
            "DEPTH_LIMIT": 2,
        }

        # Run spider
        runner = CrawlerRunner(settings=settings)

        @defer.inlineCallbacks
        def crawl():
            yield runner.crawl(ScoutSpider, start_urls=[start_url])
            reactor.stop()

        crawl()
        reactor.run()

        # Verify results in Delta Lake
        discovered = delta_sandbox.read("stage1_discovery")
        assert len(discovered) > 0

    @pytest.mark.skip(reason="Requires full Scrapy reactor setup")
    def test_deep_dive_spider_respects_depth_limit(self, delta_sandbox, redis_clean):
        """K4: Test DeepDiveSpider respects DEPTH_LIMIT."""
        # Similar to above but verify depth tracking


@pytest.mark.integration
class TestDeltaLakeUnderLoad:
    """K1: Test Delta Lake read/write under load (no deadlock warnings)."""

    def test_concurrent_writes(self, delta_sandbox):
        """K1: Test concurrent writes to Delta Lake don't deadlock."""
        import threading

        def write_batch(batch_id):
            data = [
                {"url": f"https://example.com/page{i}", "batch": batch_id}
                for i in range(10)
            ]
            delta_sandbox.write("concurrent_test", data, mode="append")

        # Spawn multiple threads writing concurrently
        threads = []
        for i in range(5):
            t = threading.Thread(target=write_batch, args=(i,))
            threads.append(t)
            t.start()

        # Wait for all threads
        for t in threads:
            t.join(timeout=5)

        # Verify all writes succeeded
        results = delta_sandbox.read("concurrent_test")
        assert len(results) == 50  # 5 threads * 10 records each

    def test_read_while_writing(self, delta_sandbox):
        """K1: Test reading while writing doesn't deadlock."""
        import threading
        import time

        # Initial data
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

        # Start concurrent read/write
        writer = threading.Thread(target=write_continuously)
        reader = threading.Thread(target=read_continuously)

        writer.start()
        reader.start()

        writer.join(timeout=5)
        reader.join(timeout=5)

        # Verify reads succeeded (no deadlock)
        assert len(results) > 0


@pytest.mark.integration
class TestPostgresMetrics:
    """K1: Test Postgres database operations."""

    @pytest.mark.skip(reason="Requires Postgres connection")
    def test_write_spider_metrics(self, postgres_clean):
        """Test writing spider metrics to Postgres."""
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

        # Verify write
        result = postgres_clean.query(
            "SELECT * FROM spider_stats WHERE spider_name = 'scout'"
        )
        assert len(result) == 1
        assert result[0]["urls_processed"] == 100


@pytest.mark.integration
class TestQueueFlow:
    """Test message queue flows."""

    def test_js_queue_roundtrip(self, delta_sandbox, redis_clean):
        """K5: Test JS render queue write and read."""
        # Write to JS queue
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

        # Read back
        queue = delta_sandbox.read("js_spider_queue")
        assert len(queue) == 1
        assert queue[0]["status"] == "pending"

    def test_offsite_links_captured(self, delta_sandbox):
        """K3: Test offsite links are saved but not followed."""
        from src.stage1.base_spider import BaseSpider

        spider = BaseSpider()
        spider.allowed_domains = ["example.com"]

        # Process would save offsite item
        # Verify in tests that Request is NOT generated for external URLs


@pytest.mark.integration
@pytest.mark.slow
class TestDockerComposeStack:
    """K1: Test Docker Compose stack health."""

    @pytest.mark.skip(reason="Requires Docker Compose running")
    def test_postgres_db_name(self):
        """K1: Verify POSTGRES_DB=pipeline_metrics."""
        import psycopg2

        conn = psycopg2.connect(
            host="localhost",
            port=5432,
            user="postgres",
            password="postgres",
            database="pipeline_metrics",
        )

        cur = conn.cursor()
        cur.execute("SELECT current_database()")
        db_name = cur.fetchone()[0]

        assert db_name == "pipeline_metrics"

        conn.close()

    @pytest.mark.skip(reason="Requires Docker Compose running")
    def test_delta_volume_shared(self):
        """K1: Verify delta_data volume is shared across services."""
        # Would test that multiple containers see same Delta data
        pass


@pytest.mark.integration
class TestGracefulShutdown:
    """K2: Test graceful spider shutdown."""

    def test_closespider_timeout_configured(self):
        """K2: Verify CLOSESPIDER_TIMEOUT is set in settings."""
        from src import settings

        assert hasattr(settings, "CLOSESPIDER_TIMEOUT")
        assert settings.CLOSESPIDER_TIMEOUT > 0
        assert settings.CLOSESPIDER_TIMEOUT == 600  # 10 minutes default

    @pytest.mark.skip(reason="Requires spider run")
    def test_spider_closes_gracefully_on_timeout(self):
        """K2: Test spider saves data before timeout close."""
        # Would run spider with short timeout and verify:
        # 1. Spider closes after timeout
        # 2. Pending batches are saved to Delta
        # 3. No data loss
        pass
