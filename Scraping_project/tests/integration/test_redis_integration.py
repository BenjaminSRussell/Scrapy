"""Integration tests for RedisHelper.

The old RedisManager's generic named-queue API (push_to_queue/
pop_from_queue/get_queue_length/clear_queue) has no callers anywhere in
src/ - queueing moved to Delta Lake tables (stage2_queue,
js_spider_queue) and the Redis-backed JSPriorityQueue (sorted sets) for
the one case that still needs Redis-speed ordering. RedisHelper's own
surface (URL dedup sets, counters, circuit breaker) is what's actually
live, so that's what these integration tests exercise.
"""

import threading

import pytest

from src.utils.redis import RedisHelper

@pytest.mark.integration
@pytest.mark.redis
class TestRedisIntegration:

    def _helper(self, redis_clean) -> RedisHelper:
        helper = RedisHelper(
            host="127.0.0.1",
            port=6379,
            db=redis_clean.connection_pool.connection_kwargs["db"],
        )
        helper._client = redis_clean
        return helper

    def test_url_dedup_end_to_end(self, redis_clean):
        helper = self._helper(redis_clean)

        urls = [f"https://example.com/{i}" for i in range(10)]
        for url in urls:
            assert not helper.check_url_seen(url, "scout")
            helper.mark_url_seen(url, "scout")

        for url in urls:
            assert helper.check_url_seen(url, "scout")

        assert helper.get_set_size("scout:urls") == 10

    def test_circuit_breaker_end_to_end(self, redis_clean):
        helper = self._helper(redis_clean)

        assert not helper.is_circuit_open("flaky.example.com")

        helper.open_circuit("flaky.example.com", duration_seconds=60, reason="high_error_rate")

        assert helper.is_circuit_open("flaky.example.com")
        assert "flaky.example.com" in helper.get_open_circuits()
        assert not helper.is_circuit_open("healthy.example.com")

    def test_concurrent_set_access(self, redis_clean):
        helper = self._helper(redis_clean)
        results = []
        lock = threading.Lock()

        def worker(offset: int):
            added = []
            for i in range(5):
                url = f"https://example.com/{offset}-{i}"
                helper.mark_url_seen(url, "concurrent")
                added.append(url)
            with lock:
                results.extend(added)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 15
        assert helper.get_set_size("concurrent:urls") == 15

    def test_counter_increment_and_read(self, redis_clean):
        helper = self._helper(redis_clean)

        assert helper.get_counter("pages_scraped") == 0

        for _ in range(5):
            helper.increment_counter("pages_scraped")

        assert helper.get_counter("pages_scraped") == 5

    def test_delete_key_and_clear_all(self, redis_clean):
        helper = self._helper(redis_clean)

        helper.mark_url_seen("https://example.com/x", "cleanup")
        assert helper.get_key_count() > 0

        helper.delete_key("cleanup:urls")
        assert not helper.check_url_seen("https://example.com/x", "cleanup")

        helper.increment_counter("some_counter")
        helper.clear_all()
        assert helper.get_key_count() == 0
