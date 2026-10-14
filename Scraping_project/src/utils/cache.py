"""
Smart caching layer with Redis backend.

Phase 8: Performance optimization through multi-level caching.
"""

import hashlib
import logging
import json
from enum import Enum
from typing import Optional, TypeVar, Callable, Any, Dict, cast
from functools import wraps

logger = logging.getLogger(__name__)

T = TypeVar('T')
# Signature-preserving decorator type (works for sync and async callables).
F = TypeVar('F', bound=Callable[..., Any])


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
                # JSON, not pickle: unpickling Redis data is code execution for
                # anyone who can write to Redis, and the client uses
                # decode_responses=True, so pickle bytes never round-tripped.
                result = json.loads(value) if deserializer is None else deserializer(value)
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
            serialized = json.dumps(value) if serializer is None else serializer(value)
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
    def decorator(func: F) -> F:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            # Generate cache key from function name and arguments
            key_parts = [key_prefix or func.__name__]
            key_parts.extend(str(arg) for arg in args)
            key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
            cache_key = hashlib.md5(":".join(key_parts).encode(), usedforsecurity=False).hexdigest()

            # Try to get cache instance (assumes global get_cache() exists)
            cache = None
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
            if cache is not None and (result is not None or cache_null):
                try:
                    cache.set(cache_key, result, ttl=ttl)
                except Exception as e:
                    logger.debug(f"Failed to cache result: {e}")

            return result

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            # Similar logic for sync functions
            return func(*args, **kwargs)

        # Return async wrapper if function is async
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return cast(F, async_wrapper)
        return cast(F, sync_wrapper)

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


# Stage 2 conditional reuse (#478). A body is cacheable only when the response
# carried an ETag or Last-Modified. The body key is url_hash + those validators.
# The index key remembers which validators were stored so a later call can
# decide "unchanged" without contacting the origin.
DEFAULT_STAGE2_CONTENT_TTL = 3600
_BAD_CACHE_CHARS = frozenset("\r\n\x00")


def _cache_token(value: Any) -> str:
    """Validator or hash text safe to mix into a key. Empty when unusable."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value).strip()
    if not text or any(ch in text for ch in _BAD_CACHE_CHARS):
        return ""
    return text


def stage2_content_cache_key(
    url_hash: str, etag: str | None = None, last_modified: str | None = None
) -> str | None:
    """Key for one validated body. None when url_hash or both validators are missing."""
    token = _cache_token(url_hash)
    etag_s = _cache_token(etag)
    lm_s = _cache_token(last_modified)
    if not token or not (etag_s or lm_s):
        return None
    identity = f"{token}\n{etag_s}\n{lm_s}".encode("utf-8", errors="replace")
    digest = hashlib.sha256(identity).hexdigest()
    return f"stage2:http:{token}:{digest}"


def stage2_content_index_key(url_hash: str) -> str | None:
    """Points a url_hash at the validators stored with its body."""
    token = _cache_token(url_hash)
    if not token:
        return None
    return f"stage2:http-index:{token}"


def stage2_unchanged_body(cache: Any, url_hash: str) -> str | None:
    """HTML the cache still holds for this url_hash's validators, else None.

    A return value means the body is unchanged, so the caller skips the network.
    """
    index_key = stage2_content_index_key(url_hash)
    if cache is None or index_key is None:
        return None
    meta = cache.get(index_key)
    if not isinstance(meta, dict):
        return None
    body_key = stage2_content_cache_key(url_hash, meta.get("etag"), meta.get("last_modified"))
    if body_key is None:
        return None
    body = cache.get(body_key)
    if not isinstance(body, str) or body == "":
        return None
    return body


def stage2_store_body(
    cache: Any,
    url_hash: str,
    body: str,
    etag: str | None = None,
    last_modified: str | None = None,
    ttl: int = DEFAULT_STAGE2_CONTENT_TTL,
) -> bool:
    """Store HTML only when an ETag or Last-Modified was present. No validator, no write."""
    if cache is None or not isinstance(body, str) or body == "":
        return False
    etag_s = _cache_token(etag)
    lm_s = _cache_token(last_modified)
    body_key = stage2_content_cache_key(url_hash, etag_s, lm_s)
    index_key = stage2_content_index_key(url_hash)
    if body_key is None or index_key is None:
        return False
    stored = cache.set(body_key, body, ttl=ttl)
    indexed = cache.set(index_key, {"etag": etag_s, "last_modified": lm_s}, ttl=ttl)
    return bool(stored and indexed)
