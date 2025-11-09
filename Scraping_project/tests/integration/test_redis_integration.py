import time

import pytest

from src.common.redis_manager import RedisManager

@pytest.mark.integration
@pytest.mark.redis
class TestRedisIntegration:

    def test_queue_operations_end_to_end(self, redis_clean):
        manager = RedisManager(
            host="127.0.0.1",
            port=6379,
            db=redis_clean.connection_pool.connection_kwargs["db"],
        )

        test_items = [{"url": f"https://example.com/{i}", "priority": i} for i in range(10)]

        for item in test_items:
            manager.push_to_queue("test_pipeline", item)

        assert manager.get_queue_length("test_pipeline") == 10

        processed = []
        while manager.get_queue_length("test_pipeline") > 0:
            item = manager.pop_from_queue("test_pipeline")
            if item:
                processed.append(item)

        assert len(processed) == 10
        assert processed == test_items

    @pytest.mark.skip(reason="Cache methods not yet implemented in RedisManager")
    def test_cache_expiration_timing(self, redis_clean):
        manager = RedisManager(
            host="127.0.0.1",
            port=6379,
            db=redis_clean.connection_pool.connection_kwargs["db"],
        )

        manager.set_cache("expiring_key", {"data": "value"}, ttl=2)

        assert manager.cache_exists("expiring_key")
        assert manager.get_cache("expiring_key") == {"data": "value"}

        time.sleep(1)
        assert manager.cache_exists("expiring_key")

        time.sleep(2)
        assert not manager.cache_exists("expiring_key")
        assert manager.get_cache("expiring_key") is None

    @pytest.mark.slow
    @pytest.mark.skip(reason="Rate limiting methods not yet implemented in RedisManager")
    def test_rate_limiting_enforcement(self, redis_clean):
        manager = RedisManager(
            host="127.0.0.1",
            port=6379,
            db=redis_clean.connection_pool.connection_kwargs["db"],
        )

        domain = "example.com"
        limit = 5
        window = 2

        allowed_count = 0
        blocked_count = 0

        for _ in range(10):
            if manager.check_rate_limit(domain, limit, window):
                allowed_count += 1
            else:
                blocked_count += 1

        assert allowed_count == 5
        assert blocked_count == 5

        time.sleep(3)

        assert manager.check_rate_limit(domain, limit, window)

    def test_concurrent_queue_access(self, redis_clean):
        import threading

        manager = RedisManager(
            host="127.0.0.1",
            port=6379,
            db=redis_clean.connection_pool.connection_kwargs["db"],
        )

        results = []
        lock = threading.Lock()

        def producer(n):
            for i in range(n):
                manager.push_to_queue(
                    "concurrent_queue",
                    {"id": i, "thread": threading.current_thread().name},
                )

        def consumer(n):
            consumed = []
            for _ in range(n):
                item = manager.pop_from_queue("concurrent_queue")
                if item:
                    consumed.append(item)
            with lock:
                results.extend(consumed)

        producers = [threading.Thread(target=producer, args=(5,)) for _ in range(3)]
        consumers = [threading.Thread(target=consumer, args=(5,)) for _ in range(3)]

        for t in producers + consumers:
            t.start()

        for t in producers + consumers:
            t.join()

        assert len(results) == 15

    @pytest.mark.skip(reason="Cache methods not yet implemented in RedisManager")
    def test_large_data_serialization(self, redis_clean):
        manager = RedisManager(
            host="127.0.0.1",
            port=6379,
            db=redis_clean.connection_pool.connection_kwargs["db"],
        )

        large_data = {
            "urls": [f"https://example.com/page{i}" for i in range(1000)],
            "metadata": {
                "timestamp": "2024-01-01T00:00:00",
                "nested": {"data": [{"id": i, "value": f"item_{i}"} for i in range(100)]},
            },
        }

        manager.set_cache("large_data", large_data)
        retrieved = manager.get_cache("large_data")

        assert retrieved == large_data
        assert len(retrieved["urls"]) == 1000
        assert len(retrieved["metadata"]["nested"]["data"]) == 100

    @pytest.mark.skip(reason="Cache methods not yet implemented in RedisManager")
    def test_connection_pool_reuse(self, redis_clean):
        managers = [
            RedisManager(
                host="127.0.0.1",
                port=6379,
                db=redis_clean.connection_pool.connection_kwargs["db"],
            )
            for _ in range(5)
        ]

        for i, manager in enumerate(managers):
            manager.set_cache(f"key_{i}", f"value_{i}")

        for i, manager in enumerate(managers):
            assert manager.get_cache(f"key_{i}") == f"value_{i}"

    def test_pipeline_with_error_recovery(self, redis_clean):
        manager = RedisManager(
            host="127.0.0.1",
            port=6379,
            db=redis_clean.connection_pool.connection_kwargs["db"],
        )

        valid_items = [{"url": f"https://example.com/{i}"} for i in range(5)]
        for item in valid_items:
            manager.push_to_queue("error_queue", item)

        processed = []
        failed = []

        while manager.get_queue_length("error_queue") > 0:
            item = manager.pop_from_queue("error_queue")
            if item:
                try:
                    if int(item["url"][-1]) % 2 == 0:
                        processed.append(item)
                    else:
                        manager.push_to_queue("error_queue_retry", item)
                        failed.append(item)
                except Exception:
                    failed.append(item)

        assert len(processed) > 0
        assert manager.get_queue_length("error_queue_retry") == len(failed)
