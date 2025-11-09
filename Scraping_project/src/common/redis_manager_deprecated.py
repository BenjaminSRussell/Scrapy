import hashlib
import json
import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import redis  # type: ignore[import]
from redis.exceptions import RedisError  # type: ignore[import]

logger = logging.getLogger(__name__)

class RedisManager:

    PREFIX_URL_HASH = "url:hash:"
    PREFIX_URL_QUEUE = "url:queue:"
    PREFIX_CIRCUIT_BREAKER = "circuit:breaker:"
    PREFIX_MESSAGE_QUEUE = "queue:"
    PREFIX_PRIORITY_SCORE = "priority:score:"

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        max_connections: int = 50,
    ):
        """Initialize Redis connection pool.

        Args:
            host: Redis server host
            port: Redis server port
            db: Redis database number
            password: Redis password (if required)
            max_connections: Maximum connections in pool
        """
        socket_opts = {
            "socket_keepalive": True,
            "retry_on_timeout": True,
            "socket_connect_timeout": 5.0,
            "socket_timeout": 5.0,
        }
        self.pool = redis.ConnectionPool(
            host=host,
            port=port,
            db=db,
            password=password,
            max_connections=max_connections,
            decode_responses=True,
            **socket_opts,
        )
        self.redis = redis.Redis(connection_pool=self.pool)
        self._test_connection()

    def _test_connection(self):
        try:
            self.redis.ping()
            logger.info("Redis connection established successfully")
        except RedisError as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise

    def close(self):
        try:
            self.pool.disconnect()
            logger.info("Redis connection pool closed")
        except Exception as e:
            logger.warning(f"Error closing Redis pool: {e}")

    # ============================================
    # ============================================

    def hash_url(self, url: str) -> str:
        normalized = url.lower().rstrip("/")
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def url_exists(self, url: str) -> bool:
        url_hash = self.hash_url(url)
        key = f"{self.PREFIX_URL_HASH}{url_hash}"
        return self.redis.exists(key) > 0

    def add_url(self, url: str, metadata: dict[str, Any] | None = None) -> bool:
        url_hash = self.hash_url(url)
        key = f"{self.PREFIX_URL_HASH}{url_hash}"

        if self.redis.exists(key):
            return False

        data = {
            "url": url,
            "url_hash": url_hash,
            "timestamp": str(datetime.now()),
        }
        if metadata:
            data.update(metadata)

        self.redis.hset(key, mapping=data)
        return True

    def get_url_metadata(self, url: str) -> dict[str, str] | None:
        url_hash = self.hash_url(url)
        key = f"{self.PREFIX_URL_HASH}{url_hash}"
        data = self.redis.hgetall(key)
        return data if data else None

    def bulk_add_urls(self, urls: list[str]) -> int:
        pipeline = self.redis.pipeline()
        new_count = 0

        for url in urls:
            url_hash = self.hash_url(url)
            key = f"{self.PREFIX_URL_HASH}{url_hash}"

            pipeline.hsetnx(key, "url", url)

        results = pipeline.execute()
        new_count = sum(1 for r in results if r)
        return new_count

    # ============================================
    # ============================================

    def add_to_priority_queue(
        self,
        url: str,
        priority: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Add URL to priority queue with scoring.

        Priority scoring:
        - Higher scores = crawled first
        - Faculty/research pages: +10
        - Same domain: +5
        - Login/calendar pages: -10

        Args:
            url: URL to enqueue
            priority: Base priority score
            metadata: Optional metadata

        Returns:
            True if added successfully
        """
        if self.url_exists(url):
            return False

        score = self._calculate_priority(url, priority)

        queue_key = f"{self.PREFIX_URL_QUEUE}pending"
        url_hash = self.hash_url(url)

        data_key = f"{self.PREFIX_URL_HASH}{url_hash}"
        url_data = {
            "url": url,
            "url_hash": url_hash,
            "priority": str(score),
            "timestamp": str(datetime.now()),
        }
        if metadata:
            url_data.update(metadata)

        pipeline = self.redis.pipeline()
        pipeline.hset(data_key, mapping=url_data)
        pipeline.zadd(queue_key, {url_hash: score})
        pipeline.execute()

        return True

    def _calculate_priority(self, url: str, base_priority: float) -> float:
        score = base_priority
        parsed = urlparse(url)
        path = parsed.path.lower()

        high_value_keywords = [
            "research",
            "faculty",
            "publication",
            "people",
            "directory",
            "staff",
        ]
        if any(kw in path for kw in high_value_keywords):
            score += 10

        low_value_keywords = ["login", "calendar", "event", "news", "archive"]
        if any(kw in path for kw in low_value_keywords):
            score -= 10

        # Boost shorter paths (usually more important)
        path_depth = path.count("/")
        score -= path_depth * 0.5

        return score

    def get_next_urls(self, batch_size: int = 10) -> list[dict[str, Any]]:
        queue_key = f"{self.PREFIX_URL_QUEUE}pending"

        url_hashes = self.redis.zrevrange(queue_key, 0, batch_size - 1)

        if not url_hashes:
            return []

        pipeline = self.redis.pipeline()
        for url_hash in url_hashes:
            pipeline.zrem(queue_key, url_hash)
            data_key = f"{self.PREFIX_URL_HASH}{url_hash}"
            pipeline.hgetall(data_key)

        results = pipeline.execute()

        urls = []
        for i in range(len(url_hashes)):
            data = results[i * 2 + 1]
            if data:
                urls.append(data)

        return urls

    def get_queue_size(self) -> int:
        queue_key = f"{self.PREFIX_URL_QUEUE}pending"
        return self.redis.zcard(queue_key)

    # ============================================
    # ============================================

    def open_circuit(
        self,
        domain: str,
        duration_seconds: int = 900,
        reason: str = "high_error_rate",
    ):
        """Open circuit breaker for a domain (temporarily block crawling).

        Args:
            domain: Domain to block
            duration_seconds: How long to block (default 15 min)
            reason: Reason for opening circuit
        """
        key = f"{self.PREFIX_CIRCUIT_BREAKER}{domain}"
        data = {
            "domain": domain,
            "opened_at": str(datetime.now()),
            "reason": reason,
            "expires_in": str(duration_seconds),
        }

        self.redis.hset(key, mapping=data)
        self.redis.expire(key, duration_seconds)

        logger.warning(f"Circuit breaker OPENED for {domain} ({reason}) - blocked for {duration_seconds}s")

    def is_circuit_open(self, domain: str) -> bool:
        key = f"{self.PREFIX_CIRCUIT_BREAKER}{domain}"
        return self.redis.exists(key) > 0

    def close_circuit(self, domain: str):
        key = f"{self.PREFIX_CIRCUIT_BREAKER}{domain}"
        self.redis.delete(key)
        logger.info(f"Circuit breaker CLOSED for {domain}")

    def get_open_circuits(self) -> list[dict[str, str]]:
        pattern = f"{self.PREFIX_CIRCUIT_BREAKER}*"
        keys = self.redis.keys(pattern)

        circuits = []
        for key in keys:
            data = self.redis.hgetall(key)
            ttl = self.redis.ttl(key)
            data["ttl_seconds"] = str(ttl)
            circuits.append(data)

        return circuits

    # ============================================
    # ============================================

    def push_to_queue(self, queue_name: str, data: dict[str, Any]):
        key = f"{self.PREFIX_MESSAGE_QUEUE}{queue_name}"

        if "timestamp" not in data:
            data["timestamp"] = str(datetime.now())

        self.redis.lpush(key, json.dumps(data))

    def pop_from_queue(self, queue_name: str, timeout: int = 0) -> dict[str, Any] | None:
        key = f"{self.PREFIX_MESSAGE_QUEUE}{queue_name}"

        if timeout > 0:
            result = self.redis.brpop(key, timeout=timeout)
        else:
            result = self.redis.rpop(key)

        if result:
            if isinstance(result, tuple):
                return json.loads(result[1])
            else:
                return json.loads(result)

        return None

    def get_queue_length(self, queue_name: str) -> int:
        key = f"{self.PREFIX_MESSAGE_QUEUE}{queue_name}"
        return self.redis.llen(key)

    def clear_queue(self, queue_name: str) -> int:
        key = f"{self.PREFIX_MESSAGE_QUEUE}{queue_name}"
        length = self.redis.llen(key)
        self.redis.delete(key)
        logger.info(f"Cleared queue '{queue_name}': {length} items removed")
        return length

    def get_all_queue_stats(self) -> dict[str, int]:
        pattern = f"{self.PREFIX_MESSAGE_QUEUE}*"
        keys = self.redis.keys(pattern)

        stats = {}
        for key in keys:
            queue_name = key.replace(self.PREFIX_MESSAGE_QUEUE, "")
            stats[queue_name] = self.redis.llen(key)

        return stats

    # ============================================
    # ============================================

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_urls": len(self.redis.keys(f"{self.PREFIX_URL_HASH}*")),
            "queue_size": self.get_queue_size(),
            "open_circuits": len(self.get_open_circuits()),
            "message_queues": self.get_all_queue_stats(),
        }

    def clear_all_urls(self):
        pattern = f"{self.PREFIX_URL_HASH}*"
        keys = self.redis.keys(pattern)
        if keys:
            count = self.redis.delete(*keys)
            logger.warning(f"Cleared {count} URL hashes")

    def clear_priority_queue(self):
        queue_key = f"{self.PREFIX_URL_QUEUE}pending"
        self.redis.delete(queue_key)
        logger.warning("Priority queue cleared")

_redis_manager: RedisManager | None = None

def get_redis_manager(
    host: str = "localhost",
    port: int = 6379,
    db: int = 0,
    password: str | None = None,
) -> RedisManager:
    """Get or create global Redis manager instance.

    Args:
        host: Redis host
        port: Redis port
        db: Redis database
        password: Redis password

    Returns:
        RedisManager instance
    """
    global _redis_manager

    if _redis_manager is None:
        _redis_manager = RedisManager(
            host=host,
            port=port,
            db=db,
            password=password,
        )

    return _redis_manager
