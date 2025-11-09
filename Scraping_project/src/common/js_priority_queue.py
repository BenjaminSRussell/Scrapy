"""Async priority queue system for JavaScript spider using Redis sorted sets."""

import json
import logging
from datetime import datetime
from typing import Any

import redis

logger = logging.getLogger(__name__)

class JSPriorityQueue:

    def __init__(self, redis_client: redis.Redis, queue_key: str = "js_spider:priority_queue"):
        self.redis = redis_client
        self.queue_key = queue_key
        self.hash_key = f"{queue_key}:hashes"
        self.metadata_key = f"{queue_key}:metadata"

        logger.info(f"[JS_QUEUE] Initialized priority queue: {queue_key}")

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
            if self.redis.sismember(self.hash_key, url):
                logger.debug(f"[JS_QUEUE] URL already queued: {url[:80]}")
                return False

            self.redis.sadd(self.hash_key, url)

            priority_score = -priority

            self.redis.zadd(self.queue_key, {url: priority_score})

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

            logger.debug(f"[JS_QUEUE] Enqueued URL (priority={priority}): {url[:80]}")
            return True

        except Exception as e:
            logger.error(f"[JS_QUEUE] Failed to enqueue URL {url[:80]}: {e}")
            return False

    def enqueue_batch(self, urls: list[tuple[str, int, dict[str, Any] | None]]) -> int:
        if not urls:
            return 0

        try:
            pipeline = self.redis.pipeline()
            enqueued_count = 0

            for url, priority, metadata in urls:
                if not self.redis.sismember(self.hash_key, url):
                    pipeline.sadd(self.hash_key, url)
                    pipeline.zadd(self.queue_key, {url: -priority})

                    if metadata:
                        metadata["queued_at"] = datetime.now().isoformat()
                        pipeline.hset(
                            self.metadata_key,
                            url,
                            json.dumps(metadata),
                        )

                    enqueued_count += 1

            pipeline.execute()
            logger.info(f"[JS_QUEUE] Batch enqueued {enqueued_count}/{len(urls)} URLs")
            return enqueued_count

        except Exception as e:
            logger.error(f"[JS_QUEUE] Batch enqueue failed: {e}")
            return 0

    def dequeue(self, count: int = 1) -> list[dict[str, Any]]:
        try:
            urls = self.redis.zrange(self.queue_key, 0, count - 1)

            if not urls:
                return []

            pipeline = self.redis.pipeline()

            for url in urls:
                pipeline.zrem(self.queue_key, url)
                pipeline.hget(self.metadata_key, url)
                pipeline.hdel(self.metadata_key, url)

            results = pipeline.execute()

            url_dicts = []
            for i, url in enumerate(urls):
                metadata_json = results[i * 3 + 1]
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
            results = self.redis.zrange(self.queue_key, 0, count - 1, withscores=True)

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
            return self.redis.zcard(self.queue_key)
        except Exception as e:
            logger.error(f"[JS_QUEUE] Size check failed: {e}")
            return 0

    def clear(self) -> None:
        try:
            pipeline = self.redis.pipeline()
            pipeline.delete(self.queue_key)
            pipeline.delete(self.hash_key)
            pipeline.delete(self.metadata_key)
            pipeline.execute()

            logger.info("[JS_QUEUE] Queue cleared")

        except Exception as e:
            logger.error(f"[JS_QUEUE] Clear failed: {e}")

    def get_stats(self) -> dict[str, Any]:
        try:
            total_size = self.size()

            all_scores = self.redis.zrange(self.queue_key, 0, -1, withscores=True)

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
