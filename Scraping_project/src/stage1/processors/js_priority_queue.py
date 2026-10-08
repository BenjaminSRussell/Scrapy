"""Async priority queue system for JavaScript spider using Redis sorted sets."""

import json
import logging
import time
from datetime import datetime
from typing import Any, cast

import redis

logger = logging.getLogger(__name__)

# #377: bounds for long crawls. 0 disables a bound.
DEFAULT_MAX_SIZE = 100_000
DEFAULT_TTL_SECONDS = 86_400.0

try:
    from prometheus_client import Counter, Gauge

    JS_QUEUE_SIZE: Any = Gauge("js_priority_queue_size", "Members in the JS render priority queue.", ["queue"])
    JS_QUEUE_EVICTIONS: Any = Counter(
        "js_priority_queue_evictions_total",
        "JS render candidates dropped from the priority queue, by reason (overflow, ttl).",
        ["queue", "reason"],
    )
except Exception:  # prometheus_client missing or metric already registered
    JS_QUEUE_SIZE = JS_QUEUE_EVICTIONS = None


def _config_value(key: str, default: Any) -> Any:
    try:
        from src.core.config import get_config

        value = get_config().get(key, default)
        return default if value is None else value
    except Exception:
        return default


def _decode(member: Any) -> str:
    return member.decode() if isinstance(member, bytes) else str(member)


class JSPriorityQueue:
    """Redis sorted-set queue of JS render candidates (score = -priority).

    Bounded (#377): at most ``max_size`` members, so on overflow the lowest-priority members
    are evicted first. Members older than ``ttl_seconds`` (by enqueue time, kept in
    ``<queue>:enqueued_at``) are pruned on every enqueue and dequeue. An evicted URL stays
    in the ``<queue>:hashes`` claim set, so it isn't re-queued by the same crawl.
    """

    # Class-level defaults keep ``__new__``-built instances (tests, legacy callers) unbounded.
    max_size: int = 0
    ttl_seconds: float = 0.0

    def __init__(
        self,
        redis_client: redis.Redis,
        queue_key: str = "js_spider:priority_queue",
        max_size: int | None = None,
        ttl_seconds: float | None = None,
    ):
        self.redis = redis_client
        self.queue_key = queue_key
        self.hash_key = f"{queue_key}:hashes"
        self.metadata_key = f"{queue_key}:metadata"
        self.max_size = max(0, int(max_size if max_size is not None else _config_value("stage1.js_queue_max_size", DEFAULT_MAX_SIZE)))
        self.ttl_seconds = max(
            0.0,
            float(ttl_seconds if ttl_seconds is not None else _config_value("stage1.js_queue_ttl_seconds", DEFAULT_TTL_SECONDS)),
        )

        logger.info(
            f"[JS_QUEUE] Initialized priority queue: {queue_key} "
            f"(max_size={self.max_size or 'unbounded'}, ttl_seconds={self.ttl_seconds or 'none'})"
        )

    @property
    def enqueued_key(self) -> str:
        return f"{self.queue_key}:enqueued_at"

    def _drop(self, members: list[Any], reason: str) -> list[str]:
        """Remove members from the queue, the enqueue-time index and the metadata hash."""
        if not members:
            return []
        pipe = self.redis.pipeline()
        pipe.zrem(self.queue_key, *members)
        pipe.zrem(self.enqueued_key, *members)
        pipe.hdel(self.metadata_key, *members)
        pipe.execute()
        if JS_QUEUE_EVICTIONS is not None:
            JS_QUEUE_EVICTIONS.labels(queue=self.queue_key, reason=reason).inc(len(members))
        logger.info(f"[JS_QUEUE] Evicted {len(members)} URL(s) from {self.queue_key} ({reason})")
        return [_decode(m) for m in members]

    def prune_stale(self, now: float | None = None) -> list[str]:
        """Drop members enqueued more than ``ttl_seconds`` ago. Returns the dropped URLs."""
        if self.ttl_seconds <= 0:
            return []
        cutoff = (time.time() if now is None else now) - self.ttl_seconds
        stale = cast(list[Any], self.redis.zrangebyscore(self.enqueued_key, "-inf", f"({cutoff}"))
        return self._drop(stale, "ttl")

    def _evict_overflow(self) -> list[str]:
        if self.max_size <= 0:
            return []
        over = cast(int, self.redis.zcard(self.queue_key)) - self.max_size
        if over <= 0:
            return []
        # Highest score == lowest priority (score is -priority); ZPOPMAX is atomic.
        popped = cast(list[tuple[Any, float]], self.redis.zpopmax(self.queue_key, over))
        return self._drop([m for m, _ in popped], "overflow")

    def enforce_bounds(self, now: float | None = None) -> set[str]:
        """Apply TTL then max-size. Returns every URL evicted by this call."""
        evicted: set[str] = set()
        try:
            evicted.update(self.prune_stale(now))
            evicted.update(self._evict_overflow())
        except Exception as e:
            logger.error(f"[JS_QUEUE] Bound enforcement failed for {self.queue_key}: {e}")
        if JS_QUEUE_SIZE is not None:
            JS_QUEUE_SIZE.labels(queue=self.queue_key).set(self.size())
        return evicted

    def enqueue(
        self,
        url: str,
        priority: int = 0,
        metadata: dict[str, Any] | None = None,
        parent_url: str | None = None,
        js_confidence: float = 0.0,
    ) -> bool:
        """Add URL to priority queue if not already present.

        Args:
            url: URL to enqueue
            priority: Priority score (higher = processed first)
                     - 100: Critical (detected SPA/React/Vue)
                     - 50: High (high JS confidence)
                     - 25: Medium (moderate JS signals)
                     - 10: Low (minimal JS)
            metadata: Optional metadata dictionary
            parent_url: URL that discovered this URL
            js_confidence: JS detection confidence (0.0-1.0)

        Returns:
            True if URL was added, False if already in queue
        """
        try:
            # SADD is the atomic "first to queue" claim (#159).
            if not self.redis.sadd(self.hash_key, url):
                logger.debug(f"[JS_QUEUE] URL already queued: {url[:80]}")
                return False

            priority_score = -priority

            self.redis.zadd(self.queue_key, {url: priority_score})
            self.redis.zadd(self.enqueued_key, {url: time.time()})

            if metadata or parent_url or js_confidence:
                url_metadata = metadata or {}
                url_metadata.update(
                    {
                        "queued_at": datetime.now().isoformat(),
                        "parent_url": parent_url,
                        "js_confidence": js_confidence,
                        "priority": priority,
                    }
                )

                self.redis.hset(
                    self.metadata_key,
                    url,
                    json.dumps(url_metadata),
                )

            if url in self.enforce_bounds():
                logger.debug(f"[JS_QUEUE] URL evicted on arrival (queue full of higher priority): {url[:80]}")
                return False

            logger.debug(f"[JS_QUEUE] Enqueued URL (priority={priority}): {url[:80]}")
            return True

        except Exception as e:
            logger.error(f"[JS_QUEUE] Failed to enqueue URL {url[:80]}: {e}")
            return False

    def enqueue_batch(self, urls: list[tuple[str, int, dict[str, Any] | None]]) -> int:
        if not urls:
            return 0

        try:
            # Phase 1: atomic per-URL claims (#159); phase 2: queue only the winners.
            claim = self.redis.pipeline()
            for url, _priority, _metadata in urls:
                claim.sadd(self.hash_key, url)
            claimed = claim.execute()

            pipeline = self.redis.pipeline()
            enqueued_count = 0
            now = time.time()

            for (url, priority, metadata), added in zip(urls, claimed, strict=False):
                if int(added or 0) == 1:
                    pipeline.zadd(self.queue_key, {url: -priority})
                    pipeline.zadd(self.enqueued_key, {url: now})

                    if metadata:
                        metadata["queued_at"] = datetime.now().isoformat()
                        pipeline.hset(
                            self.metadata_key,
                            url,
                            json.dumps(metadata),
                        )

                    enqueued_count += 1

            pipeline.execute()
            if enqueued_count:
                evicted = self.enforce_bounds()
                enqueued_count -= sum(1 for (url, _p, _m), added in zip(urls, claimed, strict=False)
                                      if int(added or 0) == 1 and url in evicted)
            logger.info(f"[JS_QUEUE] Batch enqueued {enqueued_count}/{len(urls)} URLs")
            return enqueued_count

        except Exception as e:
            logger.error(f"[JS_QUEUE] Batch enqueue failed: {e}")
            return 0

    def dequeue(self, count: int = 1) -> list[dict[str, Any]]:
        try:
            self.prune_stale()  # never hand out a candidate older than the TTL
            urls = cast(list[Any], self.redis.zrange(self.queue_key, 0, count - 1))

            if not urls:
                return []

            pipeline = self.redis.pipeline()

            for url in urls:
                pipeline.zrem(self.queue_key, url)
                pipeline.hget(self.metadata_key, url)
                pipeline.hdel(self.metadata_key, url)
                pipeline.zrem(self.enqueued_key, url)

            results = pipeline.execute()

            url_dicts = []
            for i, url in enumerate(urls):
                metadata_json = results[i * 4 + 1]
                metadata = json.loads(metadata_json) if metadata_json else {}

                url_dicts.append(
                    {
                        "url": url.decode() if isinstance(url, bytes) else url,
                        "metadata": metadata,
                        "dequeued_at": datetime.now().isoformat(),
                    }
                )

            logger.debug(f"[JS_QUEUE] Dequeued {len(url_dicts)} URLs")
            return url_dicts

        except Exception as e:
            logger.error(f"[JS_QUEUE] Dequeue failed: {e}")
            return []

    def peek(self, count: int = 10) -> list[tuple[str, int]]:
        try:
            results = cast(
                list[tuple[Any, float]],
                self.redis.zrange(self.queue_key, 0, count - 1, withscores=True),
            )

            return [
                (
                    url.decode() if isinstance(url, bytes) else url,
                    -int(score),
                )
                for url, score in results
            ]

        except Exception as e:
            logger.error(f"[JS_QUEUE] Peek failed: {e}")
            return []

    def size(self) -> int:
        try:
            return cast(int, self.redis.zcard(self.queue_key))
        except Exception as e:
            logger.error(f"[JS_QUEUE] Size check failed: {e}")
            return 0

    def clear(self) -> None:
        try:
            pipeline = self.redis.pipeline()
            pipeline.delete(self.queue_key)
            pipeline.delete(self.hash_key)
            pipeline.delete(self.metadata_key)
            pipeline.delete(self.enqueued_key)
            pipeline.execute()

            logger.info("[JS_QUEUE] Queue cleared")

        except Exception as e:
            logger.error(f"[JS_QUEUE] Clear failed: {e}")

    def get_stats(self) -> dict[str, Any]:
        try:
            total_size = self.size()

            all_scores = cast(
                list[tuple[Any, float]], self.redis.zrange(self.queue_key, 0, -1, withscores=True)
            )

            priority_dist = {
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
            }

            for _, score in all_scores:
                priority = -int(score)
                if priority >= 100:
                    priority_dist["critical"] += 1
                elif priority >= 50:
                    priority_dist["high"] += 1
                elif priority >= 25:
                    priority_dist["medium"] += 1
                else:
                    priority_dist["low"] += 1

            return {
                "total_size": total_size,
                "priority_distribution": priority_dist,
                "queue_key": self.queue_key,
                "max_size": self.max_size,
                "ttl_seconds": self.ttl_seconds,
            }

        except Exception as e:
            logger.error(f"[JS_QUEUE] Stats failed: {e}")
            return {}

def calculate_js_priority(
    js_confidence: float,
    url: str,
    framework_detected: str | None = None,
    is_spa: bool = False,
) -> int:
    """Calculate priority score for JavaScript rendering.

    DEPRECATED: This function is maintained for backward compatibility.
    New code should use URLValueAssessor.calculate_js_priority() instead,
    which includes historical data analysis.

    Priority levels:
    - 100: Critical (SPA, framework detected)
    - 50-75: High (high JS confidence, framework hints)
    - 25-49: Medium (moderate JS signals)
    - 0-24: Low (minimal JS)

    Args:
        js_confidence: JS detection confidence (0.0-1.0)
        url: URL to prioritize
        framework_detected: Detected framework name (React, Vue, Angular, etc.)
        is_spa: Whether page is detected as SPA

    Returns:
        Priority score (0-100)
    """
    try:
        from src.common.url_value_assessor import URLValueAssessor

        assessor = URLValueAssessor()
        return assessor.calculate_js_priority(js_confidence, url, framework_detected, is_spa)
    except Exception as e:
        logger.warning(f"[JS_QUEUE] Could not use URLValueAssessor, falling back: {e}")

        base_priority = int(js_confidence * 50)

        if framework_detected:
            framework_boost = {
                "react": 50,
                "vue": 50,
                "angular": 50,
                "next": 50,
                "nuxt": 50,
                "svelte": 40,
                "ember": 40,
            }.get(framework_detected.lower(), 30)

            base_priority += framework_boost

        if is_spa:
            base_priority += 50

        url_lower = url.lower()
        if any(hint in url_lower for hint in ["app", "dashboard", "portal", "console"]):
            base_priority += 10

        return min(base_priority, 100)
