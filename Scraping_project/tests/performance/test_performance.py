"""Performance tests for pipeline components.

K6: Benchmarks to ensure system meets performance targets.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.mark.performance
@pytest.mark.slow
class TestHashingPerformance:
    """Test URL hashing performance."""

    def test_hash_url_performance(self, performance_timer):
        """Test hashing 10,000 URLs completes in < 1 second."""
        from src.stage1.base_spider import BaseSpider

        spider = BaseSpider()
        urls = [f"https://example.com/page{i}" for i in range(10000)]

        with performance_timer as timer:
            for url in urls:
                spider._hash_url(url)

        assert timer.elapsed < 1.0, f"Hashing took {timer.elapsed:.2f}s (expected < 1s)"
        print(
            f"\n✓ Hashed 10,000 URLs in {timer.elapsed:.3f}s ({10000 / timer.elapsed:.0f} URLs/sec)"
        )


@pytest.mark.performance
@pytest.mark.slow
class TestRedisPerformance:
    """Test Redis operations performance."""

    def test_redis_batch_operations_performance(self, redis_clean, performance_timer):
        """Test Redis batch operations are faster than individual ops."""
        urls = [f"https://example.com/page{i}" for i in range(1000)]

        # Individual operations (slow)
        with performance_timer as timer_individual:
            for url in urls:
                redis_clean.sadd("test_individual", url)

        # Batch operations with pipeline (fast)
        redis_clean.delete("test_batch")

        with performance_timer as timer_batch:
            pipeline = redis_clean.pipeline()
            for url in urls:
                pipeline.sadd("test_batch", url)
            pipeline.execute()

        # Batch should be significantly faster
        assert (
            timer_batch.elapsed < timer_individual.elapsed * 0.5
        ), f"Batch ({timer_batch.elapsed:.2f}s) not faster than individual ({timer_individual.elapsed:.2f}s)"

        print(
            f"\n✓ Batch operations {timer_individual.elapsed / timer_batch.elapsed:.1f}x faster"
        )


@pytest.mark.performance
class TestDeltaLakePerformance:
    """K1: Test Delta Lake performance under load."""

    def test_delta_write_throughput(self, delta_sandbox, performance_timer):
        """Test Delta Lake write throughput."""
        # Generate 1000 records
        records = [
            {
                "url": f"https://example.com/page{i}",
                "url_hash": f"hash{i}",
                "depth": i % 10,
                "status_code": 200,
                "discovered_at": "2024-01-01T00:00:00",
            }
            for i in range(1000)
        ]

        with performance_timer as timer:
            delta_sandbox.write("perf_test", records, mode="overwrite")

        throughput = len(records) / timer.elapsed
        assert (
            throughput > 100
        ), f"Write throughput {throughput:.0f} records/sec (expected > 100)"

        print(f"\n✓ Delta Lake write: {throughput:.0f} records/sec")

    def test_delta_read_throughput(self, delta_sandbox, performance_timer):
        """Test Delta Lake read throughput."""
        # Write 1000 records
        records = [{"url": f"https://example.com/page{i}"} for i in range(1000)]
        delta_sandbox.write("perf_test", records, mode="overwrite")

        # Measure read performance
        with performance_timer as timer:
            for _ in range(10):
                delta_sandbox.read("perf_test")

        avg_time = timer.elapsed / 10
        assert avg_time < 1.0, f"Read took {avg_time:.2f}s (expected < 1s)"

        print(f"\n✓ Delta Lake read: {avg_time:.3f}s per read")

    def test_delta_concurrent_write_performance(self, delta_sandbox, performance_timer):
        """K1: Test concurrent writes complete without deadlock."""

        def write_batch(batch_id):
            records = [
                {
                    "url": f"https://example.com/batch{batch_id}/page{i}",
                    "batch": batch_id,
                }
                for i in range(100)
            ]
            delta_sandbox.write("concurrent_perf", records, mode="append")

        # Initialize table
        delta_sandbox.write("concurrent_perf", [{"url": "init"}], mode="overwrite")

        with performance_timer as timer:
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(write_batch, i) for i in range(10)]
                for future in futures:
                    future.result(timeout=10)

        # Should complete in reasonable time without deadlock
        assert (
            timer.elapsed < 10.0
        ), f"Concurrent writes took {timer.elapsed:.2f}s (expected < 10s)"

        # Verify all writes succeeded
        results = delta_sandbox.read("concurrent_perf")
        assert len(results) > 1000  # 10 batches * 100 records + 1 init

        print(f"\n✓ 10 concurrent writes completed in {timer.elapsed:.2f}s")


@pytest.mark.performance
class TestSpiderParsingPerformance:
    """Test spider parsing performance."""

    def test_parse_response_performance(self, test_html_response, performance_timer):
        """Test parsing HTML responses is fast."""
        from src.stage1.base_spider import BaseSpider

        spider = BaseSpider()
        spider.redis_client = MockRedis()

        # Parse 100 responses
        with performance_timer as timer:
            for _ in range(100):
                list(spider.parse(test_html_response))

        avg_time = timer.elapsed / 100
        assert (
            avg_time < 0.1
        ), f"Parse took {avg_time:.3f}s per response (expected < 0.1s)"

        print(
            f"\n✓ Parsed 100 responses in {timer.elapsed:.2f}s ({avg_time * 1000:.1f}ms per response)"
        )


@pytest.mark.performance
class TestLinkTriagePerformance:
    """K3: Test link triage performance."""

    def test_categorize_urls_performance(self, performance_timer):
        """K3: Test URL categorization is fast."""
        from src.stage1.base_spider import BaseSpider

        spider = BaseSpider()
        spider.allowed_domains = ["example.com"]

        # Mix of URL types
        test_urls = []
        for i in range(1000):
            test_urls.append(f"https://example.com/page{i}")  # HTML
            test_urls.append(f"https://external.com/page{i}")  # Offsite
            test_urls.append(f"https://example.com/image{i}.jpg")  # Static

        external_count = 0
        static_count = 0

        with performance_timer as timer:
            for url in test_urls:
                if spider._is_external_url(url):
                    external_count += 1
                if any(url.lower().endswith(ext) for ext in spider.IGNORED_EXTENSIONS):
                    static_count += 1

        assert external_count == 1000
        assert static_count == 1000

        throughput = len(test_urls) / timer.elapsed
        assert (
            throughput > 1000
        ), f"Categorization throughput {throughput:.0f} URLs/sec (expected > 1000)"

        print(
            f"\n✓ Categorized {len(test_urls)} URLs in {timer.elapsed:.2f}s ({throughput:.0f} URLs/sec)"
        )


@pytest.mark.performance
@pytest.mark.slow
class TestMemoryUsage:
    """Test memory usage remains within limits."""

    def test_spider_memory_footprint(self):
        """Test spider memory usage is reasonable."""
        import os

        import psutil

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        # Create spider and process data
        from src.stage1.base_spider import BaseSpider

        spider = BaseSpider()
        spider.redis_client = MockRedis()

        # Simulate processing 1000 pages
        for i in range(1000):
            spider.discovered_records.append(
                {
                    "url": f"https://example.com/page{i}",
                    "url_hash": f"hash{i}",
                    "depth": 1,
                    "discovered_at": "2024-01-01T00:00:00",
                }
            )

        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = final_memory - initial_memory

        # Should not use more than 100MB for 1000 records
        assert (
            memory_increase < 100
        ), f"Memory increase {memory_increase:.1f}MB (expected < 100MB)"

        print(f"\n✓ Memory increase: {memory_increase:.1f}MB for 1000 records")


@pytest.mark.performance
class TestBatchingPerformance:
    """Test batching efficiency."""

    def test_batch_size_impact(self, delta_sandbox, performance_timer):
        """Test that larger batches are more efficient."""
        records = [{"url": f"https://example.com/page{i}"} for i in range(1000)]

        # Small batches (10 records)
        with performance_timer as timer_small:
            for i in range(0, len(records), 10):
                batch = records[i : i + 10]
                delta_sandbox.write(
                    "batch_test_small", batch, mode="append" if i > 0 else "overwrite"
                )

        # Large batches (100 records)
        with performance_timer as timer_large:
            for i in range(0, len(records), 100):
                batch = records[i : i + 100]
                delta_sandbox.write(
                    "batch_test_large", batch, mode="append" if i > 0 else "overwrite"
                )

        # Larger batches should be faster
        assert (
            timer_large.elapsed < timer_small.elapsed
        ), f"Large batches ({timer_large.elapsed:.2f}s) slower than small ({timer_small.elapsed:.2f}s)"

        speedup = timer_small.elapsed / timer_large.elapsed
        print(f"\n✓ Large batches {speedup:.1f}x faster than small batches")


# ============================================================================
# Mock helpers
# ============================================================================


class MockRedis:
    """Mock Redis client for testing."""

    def __init__(self):
        self.data = {}

    def pipeline(self):
        return self

    def sismember(self, key, value):
        return self

    def sadd(self, key, value):
        if key not in self.data:
            self.data[key] = set()
        self.data[key].add(value)
        return self

    def execute(self):
        return [False] * 10

    def scard(self, key):
        return len(self.data.get(key, set()))
