"""Apply rustmapper-discovered robots Crawl-Delay before Stage 1 downloads (#939).

The discovery handoff envelope may include ``crawl_delay_seconds`` (host to
seconds). This middleware raises that host's download-slot delay to
``max(configured DOWNLOAD_DELAY, discovered)``, capped at
``ROBOTS_MAX_CRAWL_DELAY``. A host with no positive discovered delay is left
on the configured download delay.

It runs before ``PoliteRobotsTxtMiddleware`` so the robots.txt fetch itself
is already paced, and it re-applies the floor on every request so
AutoThrottle cannot drop the slot below it. It never lowers a higher delay
set by Retry-After, soft-ban, or a live Crawl-delay.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from src.stage1.discovery_handoff import (
    load_discovered_crawl_delays,
    merge_crawl_delay_maps,
    normalize_host_key,
)
from src.stage1.middlewares.robots_middleware import DEFAULT_MAX_CRAWL_DELAY

logger = logging.getLogger(__name__)

MIDDLEWARE_PATH = "src.stage1.middlewares.host_politeness_middleware.DiscoveredCrawlDelayMiddleware"
# After SSRF (50), before robots.txt (100): the robots fetch must already wait.
MIDDLEWARE_PRIORITY = 90


def inherited_download_delay(
    configured: float,
    discovered: float | None,
    *,
    cap: float,
) -> float | None:
    """Slot floor for one host, or None to keep the configured download delay.

    ``discovered`` missing, non-numeric, or ``<= 0`` means the config default.
    Otherwise the floor is ``max(configured, min(discovered, cap))``, so the
    cap never pulls the slot below the configured delay.
    """
    try:
        floor = float(configured)
    except (TypeError, ValueError):
        floor = 0.0
    if discovered is None or isinstance(discovered, bool):
        return None
    try:
        extra = float(discovered)
    except (TypeError, ValueError):
        return None
    if extra != extra or extra in (float("inf"), float("-inf")) or extra <= 0:
        return None
    try:
        limit = float(cap)
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_CRAWL_DELAY
    if limit <= 0:
        limit = DEFAULT_MAX_CRAWL_DELAY
    return max(floor, min(extra, limit))


class DiscoveredCrawlDelayMiddleware:
    """Raise per-host slot delay from the discovery handoff (#939)."""

    def __init__(
        self,
        crawler: Any = None,
        delays: dict[str, float] | None = None,
        configured_delay: float = 0.0,
        max_crawl_delay: float = DEFAULT_MAX_CRAWL_DELAY,
    ):
        self.crawler = crawler
        self.delays = merge_crawl_delay_maps(delays)
        self.configured_delay = configured_delay
        self.max_crawl_delay = max_crawl_delay
        self._spider: Any = None
        self._logged: set[str] = set()

    @classmethod
    def from_crawler(cls, crawler: Any) -> "DiscoveredCrawlDelayMiddleware":
        settings = crawler.settings
        raw = settings.get("DISCOVERED_CRAWL_DELAYS")
        delays = merge_crawl_delay_maps(
            raw,
            load_discovered_crawl_delays(
                handoff_path=settings.get("DISCOVERY_HANDOFF_PATH") or None,
                jsonl_path=settings.get("DISCOVERY_JSONL_PATH") or None,
            ),
        )
        return cls(
            crawler=crawler,
            delays=delays,
            configured_delay=settings.getfloat("DOWNLOAD_DELAY", 0.0),
            max_crawl_delay=settings.getfloat("ROBOTS_MAX_CRAWL_DELAY", DEFAULT_MAX_CRAWL_DELAY),
        )

    def _configured(self, spider: Any) -> float:
        if spider is not None and getattr(spider, "download_delay", None) is not None:
            try:
                return float(spider.download_delay)
            except (TypeError, ValueError):
                pass
        return self.configured_delay

    def _sync_spider(self, spider: Any) -> None:
        if spider is None or spider is self._spider:
            return
        self._spider = spider
        handoff = getattr(spider, "_last_discovery_handoff", None)
        self.delays = merge_crawl_delay_maps(
            self.delays,
            getattr(spider, "host_crawl_delays", None),
            getattr(handoff, "crawl_delay_seconds", None) if handoff is not None else None,
        )

    def _host_keys(self, request: Any, slot_key: str | None) -> list[str]:
        keys: list[str] = []
        if slot_key:
            keys.append(slot_key)
        url = getattr(request, "url", "") or ""
        host = urlparse(url).hostname if url else None
        if host:
            keys.append(host)
        return keys

    def _discovered_for(self, request: Any, slot_key: str | None) -> tuple[str | None, float | None]:
        for key in self._host_keys(request, slot_key):
            host = normalize_host_key(key)
            if host and host in self.delays:
                return host, self.delays[host]
        return None, None

    def _apply(self, request: Any, spider: Any) -> None:
        downloader = getattr(getattr(self.crawler, "engine", None), "downloader", None)
        if downloader is None:
            return
        slot_key = None
        get_key = getattr(downloader, "get_slot_key", None)
        if callable(get_key):
            try:
                slot_key = get_key(request)
            except (TypeError, ValueError, AttributeError):
                slot_key = None
        host, discovered = self._discovered_for(request, slot_key)
        floor = inherited_download_delay(
            self._configured(spider),
            discovered,
            cap=self.max_crawl_delay,
        )
        if floor is None or host is None:
            return
        key = slot_key or host
        slots = getattr(downloader, "slots", None)
        slot = slots.get(key) if isinstance(slots, dict) else None
        if slot is not None and float(getattr(slot, "delay", 0) or 0) < floor:
            slot.delay = floor
        per_slot = getattr(downloader, "per_slot_settings", None)
        if isinstance(per_slot, dict):
            entry = per_slot.setdefault(key, {})
            if float(entry.get("delay", 0) or 0) < floor:
                entry["delay"] = floor
        if host not in self._logged:
            self._logged.add(host)
            logger.info(
                f"[politeness] {host}: inherited Crawl-delay {floor:g}s "
                f"(configured {self._configured(spider):g}s, cap {self.max_crawl_delay:g}s)"
            )
            stats = getattr(self.crawler, "stats", None)
            if stats is not None:
                stats.inc_value("politeness/discovered_crawl_delay_hosts")

    def process_request(self, request: Any, spider: Any = None) -> None:
        self._sync_spider(spider)
        self._apply(request, spider)
        return None
