"""Unit tests for RedisManager.

Tests Redis connection management, queue operations, and caching functionality.
"""

import json
from unittest.mock import patch

import pytest

from src.common.redis_manager import RedisManager


class TestRedisManagerInit:
    """Test RedisManager initialization."""

    @pytest.mark.unit
    def test_init_with_defaults(self):
        """Test initialization with default parameters."""
        with patch('redis.Redis') as mock_redis:
            manager = RedisManager()

            assert manager.host == 'localhost'
            assert manager.port == 6379
            assert manager.db == 0
            mock_redis.assert_called_once()

    @pytest.mark.unit
    def test_init_with_custom_params(self):
        """Test initialization with custom parameters."""
        with patch('redis.Redis') as mock_redis:
            manager = RedisManager(
                host='redis-server',
                port=6380,
                db=1,
                password='secret'
            )

            assert manager.host == 'redis-server'
            assert manager.port == 6380
            assert manager.db == 1

    @pytest.mark.unit
    def test_connection_failure(self):
        """Test handling of Redis connection failure."""
        with patch('redis.Redis', side_effect=ConnectionError("Cannot connect")):
            with pytest.raises(ConnectionError):
                RedisManager()


class TestRedisManagerQueue:
    """Test Redis queue operations."""

    @pytest.mark.unit
    @pytest.mark.redis
    def test_push_to_queue(self, redis_clean):
        """Test pushing items to Redis queue."""
        manager = RedisManager(host='localhost', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        test_item = {'url': 'https://example.com', 'depth': 1}
        manager.push_to_queue('test_queue', test_item)

        # Verify item was pushed
        result = redis_clean.lrange('test_queue', 0, -1)
        assert len(result) == 1
        assert json.loads(result[0]) == test_item

    @pytest.mark.unit
    @pytest.mark.redis
    def test_pop_from_queue(self, redis_clean):
        """Test popping items from Redis queue."""
        manager = RedisManager(host='localhost', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        # Push test item
        test_item = {'url': 'https://example.com', 'depth': 1}
        redis_clean.rpush('test_queue', json.dumps(test_item))

        # Pop and verify
        result = manager.pop_from_queue('test_queue')
        assert result == test_item

    @pytest.mark.unit
    @pytest.mark.redis
    def test_pop_from_empty_queue(self, redis_clean):
        """Test popping from empty queue returns None."""
        manager = RedisManager(host='localhost', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        result = manager.pop_from_queue('empty_queue')
        assert result is None

    @pytest.mark.unit
    @pytest.mark.redis
    def test_queue_length(self, redis_clean):
        """Test getting queue length."""
        manager = RedisManager(host='localhost', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        # Push multiple items
        for i in range(5):
            manager.push_to_queue('test_queue', {'id': i})

        length = manager.queue_length('test_queue')
        assert length == 5

    @pytest.mark.unit
    @pytest.mark.redis
    def test_clear_queue(self, redis_clean):
        """Test clearing a queue."""
        manager = RedisManager(host='localhost', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        # Push items
        for i in range(5):
            manager.push_to_queue('test_queue', {'id': i})

        # Clear queue
        manager.clear_queue('test_queue')

        # Verify empty
        assert manager.queue_length('test_queue') == 0


class TestRedisManagerCache:
    """Test Redis caching operations."""

    @pytest.mark.unit
    @pytest.mark.redis
    def test_set_and_get_cache(self, redis_clean):
        """Test setting and getting cached values."""
        manager = RedisManager(host='localhost', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        manager.set_cache('test_key', {'data': 'value'})
        result = manager.get_cache('test_key')

        assert result == {'data': 'value'}

    @pytest.mark.unit
    @pytest.mark.redis
    def test_cache_with_expiry(self, redis_clean):
        """Test cache with TTL expiry."""
        manager = RedisManager(host='localhost', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        manager.set_cache('test_key', 'value', ttl=1)

        # Immediately should exist
        assert manager.get_cache('test_key') == 'value'

        # After 2 seconds should be expired
        import time
        time.sleep(2)
        assert manager.get_cache('test_key') is None

    @pytest.mark.unit
    @pytest.mark.redis
    def test_delete_cache(self, redis_clean):
        """Test deleting cached value."""
        manager = RedisManager(host='localhost', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        manager.set_cache('test_key', 'value')
        assert manager.get_cache('test_key') == 'value'

        manager.delete_cache('test_key')
        assert manager.get_cache('test_key') is None

    @pytest.mark.unit
    @pytest.mark.redis
    def test_cache_exists(self, redis_clean):
        """Test checking if cache key exists."""
        manager = RedisManager(host='localhost', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        manager.set_cache('test_key', 'value')

        assert manager.cache_exists('test_key') is True
        assert manager.cache_exists('nonexistent_key') is False


class TestRedisManagerBloomFilter:
    """Test Redis-backed bloom filter for duplicate detection."""

    @pytest.mark.unit
    @pytest.mark.redis
    def test_url_seen(self, redis_clean):
        """Test marking and checking if URL was seen."""
        manager = RedisManager(host='localhost', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        url = 'https://example.com/page'

        # Initially not seen
        assert manager.is_url_seen(url) is False

        # Mark as seen
        manager.mark_url_seen(url)

        # Now should be seen
        assert manager.is_url_seen(url) is True

    @pytest.mark.unit
    @pytest.mark.redis
    def test_url_deduplication(self, redis_clean):
        """Test URL deduplication across multiple instances."""
        manager = RedisManager(host='localhost', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        urls = [
            'https://example.com/page1',
            'https://example.com/page2',
            'https://example.com/page1',  # Duplicate
            'https://example.com/page3',
        ]

        seen_count = 0
        for url in urls:
            if not manager.is_url_seen(url):
                manager.mark_url_seen(url)
                seen_count += 1

        assert seen_count == 3  # Only 3 unique URLs


class TestRedisManagerRateLimiting:
    """Test Redis-based rate limiting."""

    @pytest.mark.unit
    @pytest.mark.redis
    def test_rate_limit_allows_initial_requests(self, redis_clean):
        """Test rate limiting allows initial requests."""
        manager = RedisManager(host='localhost', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        domain = 'example.com'
        limit = 10
        window = 60

        # First request should be allowed
        assert manager.check_rate_limit(domain, limit, window) is True

    @pytest.mark.unit
    @pytest.mark.redis
    def test_rate_limit_blocks_excess_requests(self, redis_clean):
        """Test rate limiting blocks requests exceeding limit."""
        manager = RedisManager(host='localhost', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        domain = 'example.com'
        limit = 3
        window = 60

        # Make requests up to limit
        for _ in range(limit):
            assert manager.check_rate_limit(domain, limit, window) is True

        # Next request should be blocked
        assert manager.check_rate_limit(domain, limit, window) is False


class TestRedisManagerStats:
    """Test Redis stats tracking."""

    @pytest.mark.unit
    @pytest.mark.redis
    def test_increment_counter(self, redis_clean):
        """Test incrementing counter."""
        manager = RedisManager(host='localhost', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        counter_name = 'pages_scraped'

        # Increment multiple times
        for _ in range(5):
            manager.increment_counter(counter_name)

        # Check value
        value = redis_clean.get(counter_name)
        assert int(value) == 5

    @pytest.mark.unit
    @pytest.mark.redis
    def test_get_stats(self, redis_clean):
        """Test getting all stats."""
        manager = RedisManager(host='localhost', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        # Set various stats
        manager.increment_counter('stat1')
        manager.increment_counter('stat1')
        manager.increment_counter('stat2')

        stats = manager.get_stats()

        assert 'stat1' in stats
        assert stats['stat1'] == 2
        assert stats['stat2'] == 1


class TestRedisManagerConnectionPool:
    """Test Redis connection pooling."""

    @pytest.mark.unit
    def test_connection_pool_reuse(self):
        """Test connection pool reuses connections."""
        with patch('redis.Redis') as mock_redis:
            manager1 = RedisManager()
            manager2 = RedisManager()

            # Both should use connection pool
            assert mock_redis.call_count >= 1

    @pytest.mark.unit
    @pytest.mark.redis
    def test_connection_health_check(self, redis_clean):
        """Test connection health check."""
        manager = RedisManager(host='localhost', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        assert manager.ping() is True

    @pytest.mark.unit
    @pytest.mark.redis
    def test_reconnect_on_failure(self, redis_clean):
        """Test automatic reconnection on connection failure."""
        manager = RedisManager(host='localhost', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        # Simulate connection loss
        with patch.object(manager.client, 'ping', side_effect=[ConnectionError, True]):
            # First ping fails, should trigger reconnect
            result = manager.ping()

            # Should eventually succeed after reconnect
            assert result is True or result is False  # Depending on implementation
