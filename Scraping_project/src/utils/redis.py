"""
Global Redis utilities.

Centralizes all Redis operations to eliminate duplicate code across the pipeline.
Replaces src/common/redis_manager.py with a simpler, more consistent API.
"""

from src.utils.url_canon import canonical_or_raw
from typing import Any, Optional, Set, List, cast
import json
import os
import redis
import logging
from functools import wraps
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Seen-URL store policy (#159, #163)
#
# Keys: ``{key_prefix}:urls`` (a Redis SET). Namespace a site or tenant by
# putting it in the prefix, e.g. ``key_prefix=f"{site_id}:scout"`` gives
# ``uconn:scout:urls``. Never share a prefix between sites.
#
# Ownership: ``claim_url`` / ``claim_urls`` use one ``SADD`` per URL, which
# returns 1 only for the caller that inserted it. That single atomic op
# decides first-seen ownership; a separate SISMEMBER-then-SADD can't, because
# two workers can both see "absent" before either adds.
#
# Failure policy: when Redis errors, the seen helpers fail CLOSED by default.
# They raise ``SeenStoreUnavailable`` rather than reporting a URL as unseen
# (which would cause duplicate crawls) or silently not recording it (which
# would cause endless re-crawls). Callers should pause enqueueing and retry
# later. Set ``REDIS_SEEN_FAIL_MODE=open`` to restore the old behaviour
# (treat as unseen, log) for local debugging. Every error increments
# ``redis_seen_check_errors_total{op=...}`` either way.
# ---------------------------------------------------------------------------

SEEN_FAIL_MODE_ENV = "REDIS_SEEN_FAIL_MODE"

# ---------------------------------------------------------------------------
# Connection pool backpressure (#533)
#
# Each RedisHelper owns a bounded ``redis.BlockingConnectionPool``. A caller
# that finds every connection busy waits up to REDIS_POOL_TIMEOUT seconds,
# then gets ``redis.ConnectionError("No connection available")``. On the
# seen/claim paths that error goes through the fail-closed policy above:
# ``SeenStoreUnavailable`` is raised (admission pauses) and
# ``redis_pool_exhausted_total{op}`` is incremented. It is never treated as
# "unseen", so a burst can't turn pool exhaustion into a duplicate-crawl storm.
# Before this, the pool was redis-py's default, which is unbounded with no
# backpressure, so a burst opened connections until Redis hit maxclients.
# Sizing: see "Redis connection pool sizing" in the README.
# ---------------------------------------------------------------------------
MAX_CONNECTIONS_ENV = "REDIS_MAX_CONNECTIONS"
POOL_TIMEOUT_ENV = "REDIS_POOL_TIMEOUT"
DEFAULT_MAX_CONNECTIONS = 50
DEFAULT_POOL_TIMEOUT = 2.0
_POOL_EXHAUSTED_MARKER = "No connection available"


def _env_positive(name: str, default: float, cast_to: type) -> Any:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return cast_to(default)
    try:
        value = cast_to(raw)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
        return cast_to(default)
    return value if value > 0 else cast_to(default)


def is_pool_exhausted(error: BaseException) -> bool:
    """True for redis-py's BlockingConnectionPool timeout error."""
    return isinstance(error, redis.ConnectionError) and _POOL_EXHAUSTED_MARKER in str(error)


class SeenStoreUnavailable(RuntimeError):
    """Redis seen-URL store is unreachable; admission must pause (fail-closed)."""


try:
    from prometheus_client import Counter as _PromCounter

    REDIS_SEEN_ERRORS: Any = _PromCounter(
        "redis_seen_check_errors_total",
        "Redis seen-URL store errors by operation (check/mark/claim).",
        ["op"],
    )
    REDIS_POOL_EXHAUSTED: Any = _PromCounter(
        "redis_pool_exhausted_total",
        "Redis operations that found every pooled connection busy for REDIS_POOL_TIMEOUT (#533).",
        ["op"],
    )
except Exception:  # prometheus_client missing or metric already registered
    REDIS_SEEN_ERRORS = None
    REDIS_POOL_EXHAUSTED = None


def seen_fail_mode() -> str:
    mode = os.getenv(SEEN_FAIL_MODE_ENV, "closed").strip().lower()
    return "open" if mode == "open" else "closed"


# #161: seen:* sets and queues have no TTL. Under an allkeys-* policy Redis
# evicts them silently at maxmemory, the seen set vanishes and the crawl starts
# over. Compose/Helm run volatile-lru (only TTL keys are evictable); this check
# makes a misconfigured server loud.
UNSAFE_EVICTION_POLICIES = ("allkeys-lru", "allkeys-lfu", "allkeys-random")


def check_eviction_policy(client: Any) -> Optional[str]:
    """Log an ERROR if the server may evict TTL-less keys. Returns the policy.

    Returns None when CONFIG is unavailable (e.g. disabled on managed Redis).
    """
    try:
        raw = (client.config_get("maxmemory-policy") or {}).get("maxmemory-policy")
    except Exception as exc:  # noqa: BLE001 - CONFIG may be renamed/disabled
        logger.debug(f"Could not read Redis maxmemory-policy: {exc}")
        return None
    policy = str(raw) if raw is not None else None
    if policy in UNSAFE_EVICTION_POLICIES:
        logger.error(
            f"Redis maxmemory-policy is {policy}: seen-URL sets and queues (no TTL) can be "
            "evicted silently under memory pressure, causing recrawl storms. "
            "Use volatile-lru or noeviction (#161)."
        )
    return policy


class RedisHelper:
    """Centralized Redis operations."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        db: int = 0,
        password: Optional[str] = None,
        max_connections: Optional[int] = None,
        pool_timeout: Optional[float] = None,
    ):
        """
        Initialize Redis helper.

        Args:
            host: Redis host address (default: REDIS_HOST env var, then localhost)
            port: Redis port number (default: REDIS_PORT env var, then 6379)
            db: Redis database number
            password: Optional Redis AUTH password
        """
        # #511: REDIS_HOST/REDIS_PORT/REDIS_PASSWORD/REDIS_DB (REDIS_URL as fallback),
        # resolved in one place; explicit arguments still win.
        from src.utils.redis_env import redis_settings

        env = redis_settings()
        self.host = host or env.host
        self.port = port or env.port
        self.db = db or env.db
        # #184: AUTH password comes from the caller, else REDIS_PASSWORD (Helm
        # secret / compose .env). Empty means no AUTH (local dev only).
        self.password = password or env.password
        self._client: Optional[redis.Redis] = None
        # #533: bounded pool; see the module comment for the policy.
        self.max_connections = int(max_connections) if max_connections else _env_positive(
            MAX_CONNECTIONS_ENV, DEFAULT_MAX_CONNECTIONS, int)
        self.pool_timeout = float(pool_timeout) if pool_timeout else _env_positive(
            POOL_TIMEOUT_ENV, DEFAULT_POOL_TIMEOUT, float)

    @property
    def client(self) -> redis.Redis:
        """Lazy connection to Redis."""
        if self._client is None:
            try:
                conn_kwargs: dict[str, Any] = dict(
                    host=self.host,
                    port=self.port,
                    db=self.db,
                    password=self.password,
                    decode_responses=True,
                    socket_timeout=5,
                    socket_connect_timeout=5,
                )
                pool = redis.BlockingConnectionPool(
                    max_connections=self.max_connections,
                    timeout=self.pool_timeout,
                    **conn_kwargs,
                )
                # Connection kwargs are passed too (redis-py ignores them when
                # a pool is given) so construction stays inspectable.
                self._client = redis.Redis(connection_pool=pool, **conn_kwargs)
                self._client.ping()
                logger.info(f"Connected to Redis at {self.host}:{self.port}")
                check_eviction_policy(self._client)
                if not self.password:
                    logger.warning(
                        "Redis AUTH is not configured (REDIS_PASSWORD unset). "
                        "Acceptable for local dev only; set REDIS_PASSWORD in any shared environment."
                    )
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
            return bool(self.client.sismember(key, canonical_or_raw(url)))  # #728
        except Exception as e:
            return bool(self._seen_store_error("check", e, fallback=False))

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
            self.client.sadd(key, canonical_or_raw(url))  # #728
            return True
        except Exception as e:
            return bool(self._seen_store_error("mark", e, fallback=False))

    def claim_url(self, url: str, key_prefix: str = "seen") -> bool:
        """Atomically claim first-seen ownership of ``url`` (#159).

        Returns True for exactly one caller across all processes (the one whose
        SADD inserted the member); False if it was already seen. Raises
        ``SeenStoreUnavailable`` on Redis errors unless fail mode is ``open``.
        """
        try:
            return int(cast(int, self.client.sadd(f"{key_prefix}:urls", canonical_or_raw(url)))) == 1
        except Exception as e:
            # Fail-open treats the URL as unseen, i.e. claimed (old behaviour).
            return bool(self._seen_store_error("claim", e, fallback=True))

    def claim_urls(self, urls: list[str], key_prefix: str = "seen") -> list[str]:
        """Claim many URLs in one round trip; returns those this caller now owns."""
        if not urls:
            return []
        try:
            pipe = self.client.pipeline(transaction=False)
            for url in urls:
                pipe.sadd(f"{key_prefix}:urls", canonical_or_raw(url))  # #728: variants collide
            results = pipe.execute()
            return [u for u, added in zip(urls, results, strict=False) if int(added) == 1]
        except Exception as e:
            self._seen_store_error("claim", e, fallback=None)
            return list(urls)

    def _seen_store_error(self, op: str, error: Exception, fallback: Any) -> Any:
        """Apply the seen-store failure policy: count, then raise or fall back."""
        if REDIS_SEEN_ERRORS is not None:
            REDIS_SEEN_ERRORS.labels(op=op).inc()
        if is_pool_exhausted(error):
            if REDIS_POOL_EXHAUSTED is not None:
                REDIS_POOL_EXHAUSTED.labels(op=op).inc()
            logger.warning(
                "Redis pool exhausted during %s (%d connections busy for %.2fs)",
                op, self.max_connections, self.pool_timeout,
            )
        if seen_fail_mode() == "open":
            logger.error(f"Redis seen-store {op} failed; failing OPEN ({SEEN_FAIL_MODE_ENV}=open): {error}")
            return fallback
        logger.error(f"Redis seen-store {op} failed; failing CLOSED, pausing admission: {error}")
        raise SeenStoreUnavailable(f"Redis seen-URL store unavailable during {op}: {error}") from error

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

    def clear_all(self, confirm: bool = False) -> bool:
        """
        Clear all keys from current database.

        WARNING: This deletes ALL data in the current Redis database -- seen-URL
        sets, queues and counters shared by every worker (#203/#381).

        Refuses unless the caller passes ``confirm=True`` or the process runs
        with ``ALLOW_REDIS_FLUSH=1``, so a stray call cannot wipe the shared
        data plane.

        Returns:
            True if successful, False otherwise (including when refused)
        """
        if not confirm and os.getenv("ALLOW_REDIS_FLUSH") != "1":
            logger.error(
                "Refusing Redis flushdb: pass confirm=True or set ALLOW_REDIS_FLUSH=1"
            )
            return False
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
