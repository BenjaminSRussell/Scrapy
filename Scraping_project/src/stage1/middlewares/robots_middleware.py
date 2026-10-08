"""robots.txt compliance for Stage 1 (#186) plus Crawl-delay (#188).

Scrapy's ``RobotsTxtMiddleware`` already fetches, caches (per netloc) and
enforces ``Disallow`` when ``ROBOTSTXT_OBEY`` is on, but it ignores
``Crawl-delay`` and only counts refusals in crawl stats. This subclass:

* counts refusals in ``scrapy_robots_forbidden_total{spider}`` and logs the
  first refusal per host at INFO (the rest at DEBUG);
* honours ``Crawl-delay`` for our user agent by raising the host's download
  slot delay to it, capped at ``ROBOTS_MAX_CRAWL_DELAY`` (60s). It is
  re-applied before every request because AutoThrottle may lower the slot
  delay again after a fast response.

A missing robots.txt (404) or a fetch error allows everything, as in Scrapy.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from scrapy.downloadermiddlewares.robotstxt import RobotsTxtMiddleware
from scrapy.exceptions import IgnoreRequest
from scrapy.utils.httpobj import urlparse_cached

logger = logging.getLogger(__name__)

DEFAULT_MAX_CRAWL_DELAY = 60.0

try:
    from prometheus_client import Counter

    ROBOTS_FORBIDDEN: Any = Counter(
        "scrapy_robots_forbidden_total",
        "Requests skipped because robots.txt disallows them.",
        ["spider"],
    )
    ROBOTS_CRAWL_DELAY_HOSTS: Any = Counter(
        "scrapy_robots_crawl_delay_hosts_total",
        "Hosts whose robots.txt Crawl-delay raised the download delay.",
        ["spider"],
    )
except Exception:  # prometheus_client missing or metric already registered
    ROBOTS_FORBIDDEN = ROBOTS_CRAWL_DELAY_HOSTS = None


def robots_crawl_delay(parser: Any, useragent: str | bytes) -> Optional[float]:
    """Crawl-delay for ``useragent`` from a Scrapy RobotParser, if it exposes one."""
    ua = useragent.decode("utf-8", "ignore") if isinstance(useragent, bytes) else str(useragent)
    for candidate in (getattr(parser, "rp", None), parser):  # ProtegoRobotParser wraps .rp
        fn = getattr(candidate, "crawl_delay", None)
        if callable(fn):
            try:
                value = fn(ua)
            except Exception:  # parser-specific failures: treat as no delay
                return None
            if value is None:
                return None
            try:
                delay = float(value)
            except (TypeError, ValueError):
                return None
            return delay if delay > 0 else None
    return None


class PoliteRobotsTxtMiddleware(RobotsTxtMiddleware):
    """RobotsTxtMiddleware + metrics + Crawl-delay (replaces Scrapy's at priority 100)."""

    def __init__(self, crawler: Any):
        super().__init__(crawler)
        self.max_crawl_delay = crawler.settings.getfloat("ROBOTS_MAX_CRAWL_DELAY", DEFAULT_MAX_CRAWL_DELAY)
        self._forbidden_hosts: set[str] = set()
        self._delay_hosts: dict[str, float] = {}

    def _useragent(self, request: Any) -> str | bytes:
        ua = self._robotstxt_useragent or request.headers.get(b"User-Agent", self._default_useragent)
        return ua or ""

    def process_request_2(self, rp: Any, request: Any, spider: Any) -> None:
        try:
            super().process_request_2(rp, request, spider)
        except IgnoreRequest:
            host = urlparse_cached(request).netloc
            spider_name = getattr(spider, "name", "unknown")
            if ROBOTS_FORBIDDEN is not None:
                ROBOTS_FORBIDDEN.labels(spider=spider_name).inc()
            if host not in self._forbidden_hosts:
                self._forbidden_hosts.add(host)
                logger.info(f"[robots] {host}: skipping {request.url[:100]} (Disallow); further refusals at DEBUG")
            raise
        if rp is not None:
            self._apply_crawl_delay(rp, request, spider)

    def _apply_crawl_delay(self, rp: Any, request: Any, spider: Any) -> None:
        delay = robots_crawl_delay(rp, self._useragent(request))
        if delay is None:
            return
        delay = min(delay, self.max_crawl_delay)
        downloader = getattr(getattr(self.crawler, "engine", None), "downloader", None)
        if downloader is None:
            return
        key = downloader.get_slot_key(request)
        slot = downloader.slots.get(key)
        if slot is not None and slot.delay < delay:
            slot.delay = delay
        # Slots are garbage-collected when idle; per-slot settings (DOWNLOAD_SLOTS)
        # make a re-created slot start at the Crawl-delay too.
        per_slot = getattr(downloader, "per_slot_settings", None)
        if isinstance(per_slot, dict):
            entry = per_slot.setdefault(key, {})
            if float(entry.get("delay", 0) or 0) < delay:
                entry["delay"] = delay
        if key not in self._delay_hosts:
            self._delay_hosts[key] = delay
            if ROBOTS_CRAWL_DELAY_HOSTS is not None:
                ROBOTS_CRAWL_DELAY_HOSTS.labels(spider=getattr(spider, "name", "unknown")).inc()
            logger.info(f"[robots] {key}: honouring Crawl-delay {delay:g}s (cap {self.max_crawl_delay:g}s)")
