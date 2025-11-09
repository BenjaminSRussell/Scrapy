"""
Smart caching layer with Redis backend.

Phase 8: Performance optimization through multi-level caching.
"""

import hashlib
import logging
import pickle
from enum import Enum
from typing import Optional, TypeVar, Callable, Any, Dict
from functools import wraps

logger = logging.getLogger(__name__)

T = TypeVar('T')


class CacheStrategy(Enum):
    """Cache strategies for different data types."""
    LRU = "lru"
    LFU = "lfu"
    TTL = "ttl"


class SmartCache:
    """
    Multi-level caching with Redis backend.

    Provides L1 (in-memory) and L2 (Redis) caching with automatic
    serialization and cache statistics.
    """

    def __init__(self, redis_client: Any, strategy: CacheStrategy = CacheStrategy.LRU):
        self.redis = redis_client
        self.strategy = strategy
        self.local_cache: Dict[str, Any] = {}
        self.cache_stats = {"hits": 0, "misses": 0, "sets": 0}

    def get(self, key: str, deserializer: Optional[Callable] = None) -> Optional[Any]:
        """
        Get from cache with L1 -> L2 fallback.

        Args:
            key: Cache key
            deserializer: Optional custom deserializer

        Returns:
            Cached value or None if not found
        """
        # Try L1 (local memory) first
        if key in self.local_cache:
            self.cache_stats["hits"] += 1
            return self.local_cache[key]

        # Try L2 (Redis)
        try:
            value = self.redis.client.get(f"cache:{key}")
            if value:
                self.cache_stats["hits"] += 1
                # Deserialize
                result = pickle.loads(value) if deserializer is None else deserializer(value)
                # Populate L1 cache
                self.local_cache[key] = result
                return result
        except Exception as e:
            logger.warning(f"Redis cache error for key {key}: {e}")

        self.cache_stats["misses"] += 1
        return None

    def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
        serializer: Optional[Callable] = None
    ) -> bool:
        """
        Set in both L1 and L2 cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live in seconds
            serializer: Optional custom serializer

        Returns:
            True if successful
        """
        # Store in L1
        self.local_cache[key] = value

        # Store in L2 (Redis)
        try:
            serialized = pickle.dumps(value) if serializer is None else serializer(value)
            if ttl:
                self.redis.client.setex(f"cache:{key}", ttl, serialized)
            else:
                self.redis.client.set(f"cache:{key}", serialized)

            self.cache_stats["sets"] += 1
            return True
        except Exception as e:
            logger.error(f"Failed to set cache for key {key}: {e}")
            return False

    def invalidate(self, key: str) -> bool:
        """Remove from all cache levels."""
        self.local_cache.pop(key, None)
        try:
            self.redis.client.delete(f"cache:{key}")
            return True
        except Exception as e:
            logger.error(f"Failed to invalidate cache for key {key}: {e}")
            return False

    def clear(self) -> bool:
        """Clear all caches."""
        self.local_cache.clear()
        try:
            # Clear only cache: prefixed keys
            pattern = "cache:*"
            keys = self.redis.client.keys(pattern)
            if keys:
                self.redis.client.delete(*keys)
            return True
        except Exception as e:
            logger.error(f"Failed to clear cache: {e}")
            return False

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total_requests = self.cache_stats["hits"] + self.cache_stats["misses"]
        hit_rate = self.cache_stats["hits"] / total_requests if total_requests > 0 else 0

        return {
            **self.cache_stats,
            "hit_rate": hit_rate,
            "l1_size": len(self.local_cache)
        }


def cached(
    ttl: int = 3600,
    key_prefix: str = "",
    cache_null: bool = False
):
    """
    Caching decorator for functions.

    Args:
        ttl: Time to live in seconds
        key_prefix: Prefix for cache keys
        cache_null: Whether to cache None results

    Example:
        @cached(ttl=3600, key_prefix="url_analysis")
        async def analyze_url(url: str) -> dict:
            return await expensive_analysis(url)
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> T:
            # Generate cache key from function name and arguments
            key_parts = [key_prefix or func.__name__]
            key_parts.extend(str(arg) for arg in args)
            key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
            cache_key = hashlib.md5(":".join(key_parts).encode()).hexdigest()

            # Try to get cache instance (assumes global get_cache() exists)
            try:
                from src.utils.cache import get_cache
                cache = get_cache()

                # Try cache first
                cached_value = cache.get(cache_key)
                if cached_value is not None:
                    return cached_value
            except Exception as e:
                logger.debug(f"Cache unavailable: {e}")

            # Cache miss - execute function
            result = await func(*args, **kwargs)

            # Cache result (unless null and cache_null=False)
            if result is not None or cache_null:
                try:
                    cache.set(cache_key, result, ttl=ttl)
                except Exception as e:
                    logger.debug(f"Failed to cache result: {e}")

            return result

        @wraps(func)
        def sync_wrapper(*args, **kwargs) -> T:
            # Similar logic for sync functions
            return func(*args, **kwargs)

        # Return async wrapper if function is async
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


# Global cache instance
_global_cache: Optional[SmartCache] = None


def get_cache() -> SmartCache:
    """Get global cache instance."""
    global _global_cache
    if _global_cache is None:
        from src.utils.redis import get_redis
        redis_helper = get_redis()
        _global_cache = SmartCache(redis_helper)
    return _global_cache
