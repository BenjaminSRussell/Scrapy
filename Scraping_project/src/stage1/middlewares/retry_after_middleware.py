"""Honour ``Retry-After`` on 429/503 responses in Stage 1 (#188).

Scrapy's RetryMiddleware retries 429/503 immediately (subject only to the
slot delay) and ignores ``Retry-After``. This middleware runs just before it
on the response path (priority 560 > RetryMiddleware 550 > SoftBanMiddleware
540) and raises the host's download-slot delay to the advertised wait, capped
at ``RETRY_AFTER_MAX_DELAY`` (120s), so the retry and every other request to
that host wait at least that long. The previous delay is restored once the
window has passed. It never drops or alters the response itself.

A 429/503 *without* a usable Retry-After backs the host off exponentially
(#194): the slot delay doubles (at least ``RATE_LIMIT_BACKOFF_MIN``, 1s) on
each consecutive one, up to ``RATE_LIMIT_BACKOFF_MAX`` (default: the larger
of ``AUTOTHROTTLE_MAX_DELAY`` and 30s), and is held for
``RATE_LIMIT_COOLDOWN_FACTOR`` (4) times that delay before being restored.

While a wait is active the enforced delay is re-applied on every request and
response for that host. AutoThrottle recomputes ``slot.delay`` on each 200
response and clamps it to ``AUTOTHROTTLE_MAX_DELAY``, so without this a
single successful in-flight response would cancel the wait.

Scrapy measures the slot delay from when the *previous request was sent*
(``slot.lastseen``), not from when its response arrived. A slow 429 would
therefore eat into the wait (a 150 ms round trip turns Retry-After: 1 into a
0.85 s gap). The wait is anchored to the moment the 429/503 is received by
moving ``slot.lastseen`` forward to "now".

Download-delay jitter is switched off for the host while a wait is enforced.
With Scrapy's default ``RANDOMIZE_DOWNLOAD_DELAY=True`` the slot sleeps
``uniform(0.5, 1.5) * delay``, so a Retry-After of N seconds would only be
honoured for 0.5*N. The slot's own ``randomize_delay`` is restored together
with the old delay once the wait is over.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_RETRY_AFTER_MAX_DELAY = 120.0
DEFAULT_BACKOFF_MIN = 1.0
DEFAULT_BACKOFF_FLOOR_MAX = 30.0
DEFAULT_COOLDOWN_FACTOR = 4.0
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
    RATE_LIMIT_BACKOFFS: Any = Counter(
        "scrapy_rate_limit_backoffs_total",
        "429/503 responses without Retry-After that doubled a host's download delay (#194).",
        ["spider"],
    )
except Exception:  # prometheus_client missing or metric already registered
    RETRY_AFTER_WAITS = RETRY_AFTER_SECONDS = RATE_LIMIT_BACKOFFS = None


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
                 clock: Callable[[], float] = time.monotonic,
                 backoff_min: float = DEFAULT_BACKOFF_MIN,
                 backoff_max: float = DEFAULT_BACKOFF_FLOOR_MAX,
                 cooldown_factor: float = DEFAULT_COOLDOWN_FACTOR,
                 wall_clock: Callable[[], float] = time.time):
        self.crawler = crawler
        self.wall_clock = wall_clock  # same clock as Scrapy's Slot.lastseen
        self.max_delay = max_delay
        self.clock = clock
        self.backoff_min = max(0.0, backoff_min)
        self.backoff_max = max(self.backoff_min, backoff_max)
        self.cooldown_factor = max(1.0, cooldown_factor)
        # slot key -> (delay before the wait, restore at, delay enforced until then,
        #              slot.randomize_delay before the wait)
        self._restore: dict[str, tuple[float, float, float, bool]] = {}

    @classmethod
    def from_crawler(cls, crawler: Any) -> "RetryAfterMiddleware":
        s = crawler.settings
        autothrottle_max = s.getfloat("AUTOTHROTTLE_MAX_DELAY", 60.0)
        return cls(
            crawler=crawler,
            max_delay=s.getfloat("RETRY_AFTER_MAX_DELAY", DEFAULT_RETRY_AFTER_MAX_DELAY),
            backoff_min=s.getfloat("RATE_LIMIT_BACKOFF_MIN", DEFAULT_BACKOFF_MIN),
            backoff_max=s.getfloat("RATE_LIMIT_BACKOFF_MAX", max(autothrottle_max, DEFAULT_BACKOFF_FLOOR_MAX)),
            cooldown_factor=s.getfloat("RATE_LIMIT_COOLDOWN_FACTOR", DEFAULT_COOLDOWN_FACTOR),
        )

    def _slot(self, request: Any) -> tuple[Optional[str], Any]:
        downloader = getattr(getattr(self.crawler, "engine", None), "downloader", None)
        if downloader is None:
            return None, None
        key = downloader.get_slot_key(request)
        return key, downloader.slots.get(key)

    def _enforce(self, key: Optional[str], slot: Any) -> None:
        """Re-apply an active wait, or restore the old delay once it is over."""
        if key is None or key not in self._restore:
            return
        old_delay, until, enforced, old_randomize = self._restore[key]
        if self.clock() >= until:
            del self._restore[key]
            if slot is not None:
                slot.delay = old_delay
                if hasattr(slot, "randomize_delay"):
                    slot.randomize_delay = old_randomize
            logger.info(f"[retry_after] {key}: wait over; download delay back to {old_delay}s")
        elif slot is not None:
            if slot.delay < enforced:
                slot.delay = enforced  # undo AutoThrottle lowering it mid-wait
            if getattr(slot, "randomize_delay", False):
                slot.randomize_delay = False

    def process_request(self, request: Any, spider: Any = None) -> None:
        if not self._restore:
            return None
        self._enforce(*self._slot(request))
        return None

    def _hold(self, key: str, slot: Any, delay: float, duration: float) -> None:
        default = (slot.delay, 0.0, 0.0, bool(getattr(slot, "randomize_delay", False)))
        old_delay, until, enforced, old_randomize = self._restore.get(key, default)
        enforced = max(enforced, delay)
        self._restore[key] = (old_delay, max(until, self.clock() + duration), enforced, old_randomize)
        slot.delay = max(slot.delay, enforced)
        if hasattr(slot, "lastseen"):
            # Count the wait from this response, not from when the request left.
            slot.lastseen = max(slot.lastseen, self.wall_clock())
        if hasattr(slot, "randomize_delay"):
            # Jitter would shorten the wait to as little as 0.5 x delay.
            slot.randomize_delay = False

    def _spider_name(self, spider: Any) -> str:
        name = getattr(spider, "name", None) or getattr(getattr(self.crawler, "spider", None), "name", None)
        return str(name or "unknown")

    def process_response(self, request: Any, response: Any, spider: Any = None) -> Any:
        if response.status not in RETRY_AFTER_STATUSES:
            if self._restore:
                self._enforce(*self._slot(request))
            return response
        key, slot = self._slot(request)
        if key is None or slot is None:
            return response
        wait = parse_retry_after(response.headers.get(b"Retry-After"))
        stats = getattr(self.crawler, "stats", None)
        if wait:
            wait = min(wait, self.max_delay)
            self._hold(key, slot, wait, wait)
            if RETRY_AFTER_WAITS is not None:
                RETRY_AFTER_WAITS.labels(spider=self._spider_name(spider)).inc()
                RETRY_AFTER_SECONDS.labels(spider=self._spider_name(spider)).inc(wait)
            if stats is not None:
                stats.inc_value("retry_after/count")
            logger.warning(
                f"[retry_after] HTTP {response.status} from {key}: waiting {wait:g}s before the next request"
            )
            return response
        # No (usable) Retry-After: exponential backoff on the host (#194).
        current = self._restore[key][2] if key in self._restore else slot.delay
        delay = min(max(current * 2, self.backoff_min), self.backoff_max)
        self._hold(key, slot, delay, delay * self.cooldown_factor)
        if RATE_LIMIT_BACKOFFS is not None:
            RATE_LIMIT_BACKOFFS.labels(spider=self._spider_name(spider)).inc()
        if stats is not None:
            stats.inc_value("rate_limit/backoff_count")
            stats.max_value("rate_limit/max_delay", delay)
        logger.warning(
            f"[retry_after] HTTP {response.status} from {key} without Retry-After: "
            f"download delay now {delay:g}s"
        )
        return response
