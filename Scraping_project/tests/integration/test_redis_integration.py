"""Integration tests for Redis functionality.

Tests real Redis operations with actual Redis instance.
"""

import time

import pytest

from src.common.redis_manager import RedisManager


@pytest.mark.integration
@pytest.mark.redis
class TestRedisIntegration:
    """Integration tests for Redis operations."""

    def test_queue_operations_end_to_end(self, redis_clean):
        """Test complete queue workflow."""
        manager = RedisManager(
            host='localhost',
            port=6379,
            db=redis_clean.connection_pool.connection_kwargs['db']
        )

        # Create a pipeline of items
        test_items = [
            {'url': f'https://example.com/{i}', 'priority': i}
            for i in range(10)
        ]

        # Push all items
        for item in test_items:
            manager.push_to_queue('test_pipeline', item)

        # Verify queue length
        assert manager.queue_length('test_pipeline') == 10

        # Pop and process items
        processed = []
        while manager.queue_length('test_pipeline') > 0:
            item = manager.pop_from_queue('test_pipeline')
            if item:
                processed.append(item)

        # Verify all items processed
        assert len(processed) == 10
        assert processed == test_items

    def test_cache_expiration_timing(self, redis_clean):
        """Test cache TTL expiration with real timing."""
        manager = RedisManager(
            host='localhost',
            port=6379,
            db=redis_clean.connection_pool.connection_kwargs['db']
        )

        # Set cache with 2 second TTL
        manager.set_cache('expiring_key', {'data': 'value'}, ttl=2)

        # Should exist immediately
        assert manager.cache_exists('expiring_key')
        assert manager.get_cache('expiring_key') == {'data': 'value'}

        # Should still exist after 1 second
        time.sleep(1)
        assert manager.cache_exists('expiring_key')

        # Should be expired after 3 seconds total
        time.sleep(2)
        assert not manager.cache_exists('expiring_key')
        assert manager.get_cache('expiring_key') is None

    @pytest.mark.slow
    def test_rate_limiting_enforcement(self, redis_clean):
        """Test rate limiting prevents excess requests."""
        manager = RedisManager(
            host='localhost',
            port=6379,
            db=redis_clean.connection_pool.connection_kwargs['db']
        )

        domain = 'example.com'
        limit = 5
        window = 2  # 2 second window

        # Make requests up to limit
        allowed_count = 0
        blocked_count = 0

        for i in range(10):
            if manager.check_rate_limit(domain, limit, window):
                allowed_count += 1
            else:
                blocked_count += 1

        # Should allow first 5, block next 5
        assert allowed_count == 5
        assert blocked_count == 5

        # Wait for window to expire
        time.sleep(3)

        # Should allow requests again
        assert manager.check_rate_limit(domain, limit, window)

    def test_concurrent_queue_access(self, redis_clean):
        """Test multiple processes accessing queue concurrently."""
        import threading

        manager = RedisManager(
            host='localhost',
            port=6379,
            db=redis_clean.connection_pool.connection_kwargs['db']
        )

        results = []
        lock = threading.Lock()

        def producer(n):
            """Producer thread."""
            for i in range(n):
                manager.push_to_queue('concurrent_queue', {'id': i, 'thread': threading.current_thread().name})

        def consumer(n):
            """Consumer thread."""
            consumed = []
            for _ in range(n):
                item = manager.pop_from_queue('concurrent_queue')
                if item:
                    consumed.append(item)
            with lock:
                results.extend(consumed)

        # Start multiple producers and consumers
        producers = [threading.Thread(target=producer, args=(5,)) for _ in range(3)]
        consumers = [threading.Thread(target=consumer, args=(5,)) for _ in range(3)]

        # Start all threads
        for t in producers + consumers:
            t.start()

        # Wait for completion
        for t in producers + consumers:
            t.join()

        # Verify all items processed (15 total)
        assert len(results) == 15

    def test_large_data_serialization(self, redis_clean):
        """Test handling of large data structures."""
        manager = RedisManager(
            host='localhost',
            port=6379,
            db=redis_clean.connection_pool.connection_kwargs['db']
        )

        # Create large nested structure
        large_data = {
            'urls': [f'https://example.com/page{i}' for i in range(1000)],
            'metadata': {
                'timestamp': '2024-01-01T00:00:00',
                'nested': {
                    'data': [{'id': i, 'value': f'item_{i}'} for i in range(100)]
                }
            }
        }

        # Store and retrieve
        manager.set_cache('large_data', large_data)
        retrieved = manager.get_cache('large_data')

        # Verify integrity
        assert retrieved == large_data
        assert len(retrieved['urls']) == 1000
        assert len(retrieved['metadata']['nested']['data']) == 100

    def test_connection_pool_reuse(self, redis_clean):
        """Test connection pooling across multiple instances."""
        # Create multiple managers
        managers = [
            RedisManager(
                host='localhost',
                port=6379,
                db=redis_clean.connection_pool.connection_kwargs['db']
            )
            for _ in range(5)
        ]

        # All should work independently
        for i, manager in enumerate(managers):
            manager.set_cache(f'key_{i}', f'value_{i}')

        # Verify all values
        for i, manager in enumerate(managers):
            assert manager.get_cache(f'key_{i}') == f'value_{i}'

    def test_pipeline_with_error_recovery(self, redis_clean):
        """Test error recovery in pipeline operations."""
        manager = RedisManager(
            host='localhost',
            port=6379,
            db=redis_clean.connection_pool.connection_kwargs['db']
        )

        # Push valid and invalid data
        valid_items = [{'url': f'https://example.com/{i}'} for i in range(5)]
        for item in valid_items:
            manager.push_to_queue('error_queue', item)

        # Simulate processing with some failures
        processed = []
        failed = []

        while manager.queue_length('error_queue') > 0:
            item = manager.pop_from_queue('error_queue')
            if item:
                try:
                    # Simulate processing
                    if int(item['url'][-1]) % 2 == 0:
                        processed.append(item)
                    else:
                        # Put back in queue for retry
                        manager.push_to_queue('error_queue_retry', item)
                        failed.append(item)
                except Exception:
                    failed.append(item)

        # Verify processing results
        assert len(processed) > 0
        assert manager.queue_length('error_queue_retry') == len(failed)
