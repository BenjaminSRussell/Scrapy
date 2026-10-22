"""robots.txt gate for Stage 2 (#1629).

Same policy as Stage 1 (``src/stage1/middlewares/robots_middleware.py``):
``Disallow`` is enforced, Crawl-delay spaces requests to a host (capped at
``ROBOTS_MAX_CRAWL_DELAY``), and a missing or unreadable robots.txt allows
everything. Protego is the parser Scrapy uses by default, so both stages read
the rules the same way.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Optional
from urllib.parse import urlsplit

from protego import Protego

FetchRobots = Callable[[str], Awaitable[Optional[bytes]]]


class RobotsGate:
    """Per-origin robots.txt cache plus a per-host Crawl-delay clock.

    ``fetch`` takes a robots.txt URL and returns its body, or None when the file
    is missing or unreadable. It may raise; the error reaches the caller and the
    origin is not cached, so the next URL on that origin tries again.
    """

    def __init__(
        self,
        user_agent: str,
        fetch: FetchRobots,
        *,
        max_crawl_delay: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.user_agent = user_agent
        self.max_crawl_delay = max(0.0, float(max_crawl_delay))
        self._fetch = fetch
        self._clock = clock
        self._parsers: dict[str, Protego] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._next_start: dict[str, float] = {}

    async def check(self, url: str) -> tuple[bool, float]:
        """(allowed, Crawl-delay in seconds for our user agent, capped). The delay is 0.0 when unset."""
        parts = urlsplit(url)
        parser = await self._parser(f"{parts.scheme}://{parts.netloc}")
        if not parser.can_fetch(url, self.user_agent):
            return False, 0.0
        delay = parser.crawl_delay(self.user_agent)
        if not delay or delay <= 0:
            return True, 0.0
        return True, min(float(delay), self.max_crawl_delay)

    async def _parser(self, origin: str) -> Protego:
        parser = self._parsers.get(origin)
        if parser is not None:
            return parser
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:  # one robots.txt fetch per origin, even with concurrent URLs
            parser = self._parsers.get(origin)
            if parser is None:
                body = await self._fetch(f"{origin}/robots.txt")
                parser = Protego.parse((body or b"").decode("utf-8", "replace"))
                self._parsers[origin] = parser
        return parser

    def reserve(self, host: str, delay: float) -> float:
        """Seconds to wait before a request to ``host`` may start. Books the next start ``delay`` later."""
        now = self._clock()
        start = max(now, self._next_start.get(host, now))
        self._next_start[host] = start + delay
        return start - now
