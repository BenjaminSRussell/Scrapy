"""Honour ``Retry-After`` on 429/503 responses in Stage 1 (#188).

Scrapy's RetryMiddleware retries 429/503 immediately (subject only to the
slot delay) and ignores ``Retry-After``. This middleware runs just before it
on the response path (priority 560 > RetryMiddleware 550 > SoftBanMiddleware
540) and raises the host's download-slot delay to the advertised wait, capped
at ``RETRY_AFTER_MAX_DELAY`` (120s), so the retry and every other request to
that host wait at least that long. The previous delay is restored once the
window has passed. It never drops or alters the response itself.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_RETRY_AFTER_MAX_DELAY = 120.0
RETRY_AFTER_STATUSES = frozenset({429, 503})

try:
    from prometheus_client import Counter

    RETRY_AFTER_WAITS: Any = Counter(
        "scrapy_retry_after_waits_total",
        "429/503 responses whose Retry-After raised a host's download delay.",
        ["spider"],
    )
    RETRY_AFTER_SECONDS: Any = Counter(
        "scrapy_retry_after_seconds_total",
        "Seconds of Retry-After wait applied (after the cap).",
        ["spider"],
    )
except Exception:  # prometheus_client missing or metric already registered
    RETRY_AFTER_WAITS = RETRY_AFTER_SECONDS = None


def parse_retry_after(value: Any, now: Optional[datetime] = None) -> Optional[float]:
    """Seconds to wait from a Retry-After header (delta-seconds or HTTP-date)."""
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("latin-1", "ignore")
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return max(0.0, (when - current).total_seconds())


class RetryAfterMiddleware:
    def __init__(self, crawler: Any = None, max_delay: float = DEFAULT_RETRY_AFTER_MAX_DELAY,
                 clock: Callable[[], float] = time.monotonic):
        self.crawler = crawler
        self.max_delay = max_delay
        self.clock = clock
        self._restore: dict[str, tuple[float, float]] = {}  # slot key -> (old delay, restore at)

    @classmethod
    def from_crawler(cls, crawler: Any) -> "RetryAfterMiddleware":
        return cls(crawler=crawler,
                   max_delay=crawler.settings.getfloat("RETRY_AFTER_MAX_DELAY", DEFAULT_RETRY_AFTER_MAX_DELAY))

    def _slot(self, request: Any) -> tuple[Optional[str], Any]:
        downloader = getattr(getattr(self.crawler, "engine", None), "downloader", None)
        if downloader is None:
            return None, None
        key = downloader.get_slot_key(request)
        return key, downloader.slots.get(key)

    def process_request(self, request: Any, spider: Any = None) -> None:
        if not self._restore:
            return None
        key, slot = self._slot(request)
        if key in self._restore:
            old_delay, until = self._restore[key]
            if self.clock() >= until:
                del self._restore[key]
                if slot is not None:
                    slot.delay = old_delay
                logger.info(f"[retry_after] {key}: wait over; download delay back to {old_delay}s")
        return None

    def process_response(self, request: Any, response: Any, spider: Any = None) -> Any:
        if response.status not in RETRY_AFTER_STATUSES:
            return response
        wait = parse_retry_after(response.headers.get(b"Retry-After"))
        if not wait:
            return response
        wait = min(wait, self.max_delay)
        key, slot = self._slot(request)
        if key is None or slot is None:
            return response
        if key not in self._restore:
            self._restore[key] = (slot.delay, 0.0)
        old_delay, until = self._restore[key]
        self._restore[key] = (old_delay, max(until, self.clock() + wait))
        slot.delay = max(slot.delay, wait)
        spider_name = getattr(spider, "name", None) or getattr(getattr(self.crawler, "spider", None), "name", "unknown")
        if RETRY_AFTER_WAITS is not None:
            RETRY_AFTER_WAITS.labels(spider=spider_name).inc()
            RETRY_AFTER_SECONDS.labels(spider=spider_name).inc(wait)
        stats = getattr(self.crawler, "stats", None)
        if stats is not None:
            stats.inc_value("retry_after/count")
        logger.warning(f"[retry_after] HTTP {response.status} from {key}: waiting {wait:g}s before the next request")
        return response
