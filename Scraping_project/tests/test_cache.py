"""
Unit tests for caching utilities.

Phase 9: Comprehensive test coverage for cache layer.
"""

import pytest
from unittest.mock import Mock, MagicMock
from src.utils.cache import SmartCache, CacheStrategy, cached, get_cache


class TestSmartCache:
    """Test SmartCache class."""

    @pytest.fixture
    def mock_redis(self):
        """Mock Redis client."""
        redis = Mock()
        redis.client = Mock()
        redis.client.get = Mock(return_value=None)
        redis.client.set = Mock(return_value=True)
        redis.client.setex = Mock(return_value=True)
        redis.client.delete = Mock(return_value=1)
        redis.client.keys = Mock(return_value=[])
        return redis

    @pytest.fixture
    def cache(self, mock_redis):
        """Create SmartCache instance."""
        return SmartCache(mock_redis, strategy=CacheStrategy.LRU)

    def test_cache_miss(self, cache, mock_redis):
        """Test cache miss."""
        result = cache.get("missing_key")
        assert result is None
        assert cache.cache_stats["misses"] == 1
        assert cache.cache_stats["hits"] == 0

    def test_l1_cache_hit(self, cache):
        """Test L1 (local) cache hit."""
        cache.set("test_key", "test_value")
        result = cache.get("test_key")
        
        assert result == "test_value"
        assert cache.cache_stats["hits"] == 1
        assert cache.cache_stats["sets"] == 1

    def test_cache_invalidation(self, cache, mock_redis):
        """Test cache invalidation."""
        cache.set("test_key", "test_value")
        cache.invalidate("test_key")
        
        assert "test_key" not in cache.local_cache
        mock_redis.client.delete.assert_called_with("cache:test_key")

    def test_cache_stats(self, cache):
        """Test cache statistics."""
        cache.set("key1", "value1")
        cache.get("key1")  # hit
        cache.get("key2")  # miss
        
        stats = cache.get_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["sets"] == 1
        assert stats["hit_rate"] == 0.5
        assert stats["l1_size"] == 1


@pytest.mark.asyncio
class TestCachedDecorator:
    """Test @cached decorator."""

    @pytest.fixture
    def mock_cache(self, monkeypatch):
        """Mock global cache."""
        cache = Mock()
        cache.get = Mock(return_value=None)
        cache.set = Mock(return_value=True)
        
        def mock_get_cache():
            return cache
        
        monkeypatch.setattr("src.utils.cache.get_cache", mock_get_cache)
        return cache

    async def test_cache_decorator_miss(self, mock_cache):
        """Test decorator on cache miss."""
        call_count = 0

        @cached(ttl=3600, key_prefix="test")
        async def expensive_function(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x * 2

        result = await expensive_function(5)
        assert result == 10
        assert call_count == 1
        mock_cache.set.assert_called_once()

    async def test_cache_decorator_hit(self, mock_cache):
        """Test decorator on cache hit."""
        mock_cache.get = Mock(return_value=42)
        call_count = 0

        @cached(ttl=3600)
        async def expensive_function(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x * 2

        result = await expensive_function(5)
        assert result == 42  # Cached value
        assert call_count == 0  # Function not called
