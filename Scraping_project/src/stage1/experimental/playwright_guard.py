"""Guaranteed release of Playwright pages for the JS spider (#452).

scrapy-playwright hands the callback a live page when
``playwright_include_page`` is set; the spider owns closing it on *every*
path (success, exception mid-render, and the errback). Unclosed pages keep
their browser context alive, so leaks accumulate Chromium renderer processes
until the node runs out of RAM or file descriptors.

``PageLedger`` tracks pages handed to the spider, closes each exactly once,
and exports ``scrapy_playwright_open_pages`` so a leak is visible.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:  # pragma: no cover - metrics optional
    from prometheus_client import Counter, Gauge

    OPEN_PAGES: Optional[Gauge] = Gauge(
        "scrapy_playwright_open_pages",
        "Playwright pages handed to the JS spider and not yet closed (#452).",
    )
    PAGES_CLOSED: Optional[Counter] = Counter(
        "scrapy_playwright_pages_closed_total",
        "Playwright pages closed by the JS spider, by path (parse/errback) and outcome (#452).",
        ["path", "outcome"],
    )
except Exception:  # pragma: no cover
    OPEN_PAGES = PAGES_CLOSED = None


class PageLedger:
    def __init__(self) -> None:
        self._open: dict[int, Any] = {}

    @property
    def open_count(self) -> int:
        return len(self._open)

    def _publish(self) -> None:
        if OPEN_PAGES is not None:
            OPEN_PAGES.set(len(self._open))

    def acquired(self, page: Any) -> None:
        if page is None:
            return
        self._open[id(page)] = page
        self._publish()

    async def release(self, page: Any, path: str) -> None:
        """Close ``page`` once; never raises (a failed close must not mask the
        original error or skip the rest of cleanup)."""
        if page is None:
            return
        self._open.pop(id(page), None)
        self._publish()
        try:
            is_closed = getattr(page, "is_closed", None)
            if callable(is_closed) and is_closed():
                outcome = "already_closed"
            else:
                await page.close()
                outcome = "closed"
        except Exception as e:
            outcome = "close_error"
            logger.warning(f"[JAVASCRIPT] Closing Playwright page failed ({path}): {e}")
        if PAGES_CLOSED is not None:
            PAGES_CLOSED.labels(path=path, outcome=outcome).inc()

    async def release_from_failure(self, failure: Any) -> None:
        request = getattr(failure, "request", None)
        meta = getattr(request, "meta", None) or {}
        await self.release(meta.get("playwright_page"), "errback")
