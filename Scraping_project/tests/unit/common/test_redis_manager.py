"""Unit tests for RedisManager.

Tests Redis connection management and queue operations.
"""

import json
from unittest.mock import patch

import pytest
import redis

from src.common.redis_manager import RedisManager


class TestRedisManagerInit:
    """Test RedisManager initialization."""

    @pytest.mark.unit
    def test_init_with_defaults(self):
        """Test initialization with default parameters."""
        with patch('redis.ConnectionPool') as mock_pool:
            manager = RedisManager()
            mock_pool.assert_called_with(
                host='localhost',
                port=6379,
                db=0,
                password=None,
                max_connections=50,
                decode_responses=True,
                socket_keepalive=True,
                retry_on_timeout=True,
                socket_connect_timeout=5.0,
                socket_timeout=5.0,
            )

    @pytest.mark.unit
    def test_connection_failure(self):
        """Test handling of Redis connection failure."""
        with patch('redis.Redis') as mock_redis:
            mock_redis.return_value.ping.side_effect = redis.exceptions.ConnectionError
            with pytest.raises(redis.exceptions.RedisError):
                RedisManager()


class TestRedisManagerQueue:
    """Test Redis queue operations."""

    @pytest.mark.unit
    @pytest.mark.redis
    def test_push_to_queue(self, redis_clean):
        """Test pushing items to Redis queue."""
        manager = RedisManager(host='127.0.0.1', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        test_item = {'url': 'https://example.com', 'depth': 1}
        manager.push_to_queue('test_queue', test_item)

        # Verify item was pushed
        result = redis_clean.lrange('queue:test_queue', 0, -1)
        assert len(result) == 1
        pushed_item = json.loads(result[0])
        assert pushed_item['url'] == test_item['url']
        assert pushed_item['depth'] == test_item['depth']

    @pytest.mark.unit
    @pytest.mark.redis
    def test_pop_from_queue(self, redis_clean):
        """Test popping items from Redis queue."""
        manager = RedisManager(host='127.0.0.1', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        # Push test item
        test_item = {'url': 'https://example.com', 'depth': 1}
        manager.push_to_queue('test_queue', test_item)

        # Pop and verify
        result = manager.pop_from_queue('test_queue')
        assert result['url'] == test_item['url']
        assert result['depth'] == test_item['depth']

    @pytest.mark.unit
    @pytest.mark.redis
    def test_pop_from_empty_queue(self, redis_clean):
        """Test popping from empty queue returns None."""
        manager = RedisManager(host='127.0.0.1', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        result = manager.pop_from_queue('empty_queue')
        assert result is None

    @pytest.mark.unit
    @pytest.mark.redis
    def test_get_queue_length(self, redis_clean):
        """Test getting queue length."""
        manager = RedisManager(host='127.0.0.1', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        # Push multiple items
        for i in range(5):
            manager.push_to_queue('test_queue', {'id': i})

        length = manager.get_queue_length('test_queue')
        assert length == 5

    @pytest.mark.unit
    @pytest.mark.redis
    def test_clear_queue(self, redis_clean):
        """Test clearing a queue."""
        manager = RedisManager(host='127.0.0.1', port=6379, db=redis_clean.connection_pool.connection_kwargs['db'])

        # Push items
        for i in range(5):
            manager.push_to_queue('test_queue', {'id': i})

        # Clear queue
        manager.clear_queue('test_queue')

        # Verify empty
        assert manager.get_queue_length('test_queue') == 0
