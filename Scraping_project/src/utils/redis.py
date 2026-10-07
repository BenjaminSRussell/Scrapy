"""
Global Redis utilities.

Centralizes all Redis operations to eliminate duplicate code across the pipeline.
Replaces src/common/redis_manager.py with a simpler, more consistent API.
"""

from typing import Any, Optional, Set, List, cast
import json
import os
import redis
import logging
from functools import wraps
import time

logger = logging.getLogger(__name__)


class RedisHelper:
    """Centralized Redis operations."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        db: int = 0,
        password: Optional[str] = None,
    ):
        """
        Initialize Redis helper.

        Args:
            host: Redis host address (default: REDIS_HOST env var, then localhost)
            port: Redis port number (default: REDIS_PORT env var, then 6379)
            db: Redis database number
            password: Optional Redis AUTH password
        """
        self.host = host or os.getenv("REDIS_HOST", "localhost")
        self.port = port or int(os.getenv("REDIS_PORT", "6379"))
        self.db = db
        self.password = password
        self._client: Optional[redis.Redis] = None

    @property
    def client(self) -> redis.Redis:
        """Lazy connection to Redis."""
        if self._client is None:
            try:
                self._client = redis.Redis(
                    host=self.host,
                    port=self.port,
                    db=self.db,
                    password=self.password,
                    decode_responses=True,
                    socket_timeout=5,
                    socket_connect_timeout=5
                )
                self._client.ping()
                logger.info(f"Connected to Redis at {self.host}:{self.port}")
            except redis.ConnectionError as e:
                logger.error(f"Failed to connect to Redis: {e}")
                raise
        return self._client

    def check_url_seen(self, url: str, key_prefix: str = "seen") -> bool:
        """
        Check if URL has been seen before.

        Args:
            url: URL to check
            key_prefix: Prefix for Redis key

        Returns:
            True if URL was seen before, False otherwise

        Example:
            redis = get_redis()
            if redis.check_url_seen("https://uconn.edu", "scout"):
                print("Already seen")
        """
        try:
            key = f"{key_prefix}:urls"
            return bool(self.client.sismember(key, url))
        except Exception as e:
            logger.error(f"Failed to check URL in Redis: {e}")
            return False

    def mark_url_seen(self, url: str, key_prefix: str = "seen") -> bool:
        """
        Mark URL as seen.

        Args:
            url: URL to mark
            key_prefix: Prefix for Redis key

        Returns:
            True if successful, False otherwise

        Example:
            redis = get_redis()
            redis.mark_url_seen("https://uconn.edu", "scout")
        """
        try:
            key = f"{key_prefix}:urls"
            self.client.sadd(key, url)
            return True
        except Exception as e:
            logger.error(f"Failed to mark URL in Redis: {e}")
            return False

    def add_to_set(self, key: str, *values: str) -> int:
        """
        Add values to Redis set.

        Args:
            key: Redis key
            values: Values to add

        Returns:
            Number of values added
        """
        try:
            return cast(int, self.client.sadd(key, *values))
        except Exception as e:
            logger.error(f"Failed to add to set {key}: {e}")
            return 0

    def get_set_members(self, key: str) -> Set[str]:
        """
        Get all members of a Redis set.

        Args:
            key: Redis key

        Returns:
            Set of members
        """
        try:
            return cast(Set[str], self.client.smembers(key))
        except Exception as e:
            logger.error(f"Failed to get set members from {key}: {e}")
            return set()

    def get_set_size(self, key: str) -> int:
        """
        Get size of Redis set.

        Args:
            key: Redis key

        Returns:
            Number of members in set
        """
        try:
            return cast(int, self.client.scard(key))
        except Exception as e:
            logger.error(f"Failed to get set size for {key}: {e}")
            return 0

    def increment_counter(self, key: str, amount: int = 1) -> int:
        """
        Increment counter.

        Args:
            key: Redis key
            amount: Amount to increment by

        Returns:
            New counter value
        """
        try:
            return cast(int, self.client.incrby(key, amount))
        except Exception as e:
            logger.error(f"Failed to increment counter {key}: {e}")
            return 0

    def get_counter(self, key: str) -> int:
        """
        Get counter value.

        Args:
            key: Redis key

        Returns:
            Counter value, or 0 if not set
        """
        try:
            value = cast(Optional[str], self.client.get(key))
            return int(value) if value else 0
        except Exception as e:
            logger.error(f"Failed to get counter {key}: {e}")
            return 0

    def get_memory_usage(self) -> int:
        """
        Get Redis memory usage in bytes.

        Returns:
            Memory usage in bytes
        """
        try:
            info = cast(dict[str, Any], self.client.info("memory"))
            return int(info.get("used_memory", 0))
        except Exception as e:
            logger.error(f"Failed to get memory usage: {e}")
            return 0

    def get_key_count(self) -> int:
        """
        Get total number of keys in Redis.

        Returns:
            Number of keys
        """
        try:
            return cast(int, self.client.dbsize())
        except Exception as e:
            logger.error(f"Failed to get key count: {e}")
            return 0

    def delete_key(self, key: str) -> bool:
        """
        Delete a key from Redis.

        Args:
            key: Redis key to delete

        Returns:
            True if successful, False otherwise
        """
        try:
            self.client.delete(key)
            return True
        except Exception as e:
            logger.error(f"Failed to delete key {key}: {e}")
            return False

    def clear_all(self) -> bool:
        """
        Clear all keys from current database.

        WARNING: This deletes ALL data in the current Redis database!

        Returns:
            True if successful, False otherwise
        """
        try:
            self.client.flushdb()
            logger.warning("Cleared all keys from Redis database")
            return True
        except Exception as e:
            logger.error(f"Failed to clear Redis: {e}")
            return False

    def ping(self) -> bool:
        """
        Check if Redis is responsive.

        Returns:
            True if Redis responds, False otherwise
        """
        try:
            return bool(self.client.ping())
        except Exception as e:
            logger.error(f"Redis ping failed: {e}")
            return False

    def open_circuit(self, domain: str, duration_seconds: int = 900, reason: Optional[str] = None) -> bool:
        """
        Open the circuit breaker for a domain, blocking requests to it for a
        limited time.

        Args:
            domain: Domain to block
            duration_seconds: How long the circuit stays open
            reason: Optional human-readable reason, stored alongside the key

        Returns:
            True if successful, False otherwise
        """
        try:
            key = f"circuit:{domain}"
            value = json.dumps({"reason": reason, "opened_at": time.time()})
            self.client.set(key, value, ex=duration_seconds)
            return True
        except Exception as e:
            logger.error(f"Failed to open circuit for {domain}: {e}")
            return False

    def is_circuit_open(self, domain: str) -> bool:
        """
        Check whether the circuit breaker for a domain is currently open.

        Args:
            domain: Domain to check

        Returns:
            True if the circuit is open (requests should be skipped)
        """
        try:
            return cast(int, self.client.exists(f"circuit:{domain}")) > 0
        except Exception as e:
            logger.error(f"Failed to check circuit for {domain}: {e}")
            return False

    def get_open_circuits(self) -> List[str]:
        """
        List domains whose circuit breaker is currently open.

        Returns:
            List of domain names with an open circuit
        """
        try:
            keys = cast(List[str], self.client.keys("circuit:*"))
            return [key.split("circuit:", 1)[1] for key in keys]
        except Exception as e:
            logger.error(f"Failed to list open circuits: {e}")
            return []


# Global instance
_redis_helper: Optional[RedisHelper] = None


def get_redis(
    host: Optional[str] = None, port: Optional[int] = None, db: int = 0, password: Optional[str] = None
) -> RedisHelper:
    """
    Get global Redis helper instance.

    This is the primary way to access Redis operations throughout the pipeline.

    Args:
        host: Redis host address (default: REDIS_HOST env var, then localhost)
        port: Redis port number (default: REDIS_PORT env var, then 6379)
        db: Redis database number
        password: Optional Redis AUTH password

    Returns:
        RedisHelper instance

    Example:
        from src.utils.redis import get_redis

        redis = get_redis()
        if not redis.check_url_seen(url, "scout"):
            redis.mark_url_seen(url, "scout")
            # Process URL...
    """
    global _redis_helper
    if _redis_helper is None:
        _redis_helper = RedisHelper(host, port, db, password)
    return _redis_helper


def reset_redis():
    """Reset global Redis helper instance (useful for testing)."""
    global _redis_helper
    if _redis_helper and _redis_helper._client:
        _redis_helper._client.close()
    _redis_helper = None


def redis_retry(max_attempts: int = 3, backoff_factor: float = 2.0):
    """
    Decorator for Redis operations with retry logic.

    Args:
        max_attempts: Maximum number of retry attempts
        backoff_factor: Exponential backoff factor

    Example:
        @redis_retry(max_attempts=3)
        def my_redis_operation():
            redis = get_redis()
            return redis.client.get("mykey")
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except redis.ConnectionError as e:
                    if attempt == max_attempts - 1:
                        logger.error(f"Redis operation failed after {max_attempts} attempts: {e}")
                        raise
                    wait_time = backoff_factor ** attempt
                    logger.warning(f"Redis connection error, retrying in {wait_time}s...")
                    time.sleep(wait_time)
        return wrapper
    return decorator
