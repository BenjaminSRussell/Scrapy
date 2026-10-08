"""Stage 1 enforcement of the crawl kill switch and budgets (#456).

Every outgoing request is checked against ``CrawlGuard``. When the switch is
engaged or a budget is spent, the request is dropped (``IgnoreRequest``) and
the spider is closed once with reason ``kill_switch`` or
``budget_exceeded:<cap>``, so a runaway crawl stops within the guard's check
interval. Responses are charged against the request and byte budgets.
"""

from __future__ import annotations

import logging
from typing import Any

from scrapy.exceptions import IgnoreRequest, NotConfigured

from src.utils.crawl_guard import CrawlGuard

logger = logging.getLogger(__name__)


class CrawlGuardMiddleware:
    def __init__(self, crawler: Any = None, guard: CrawlGuard | None = None):
        self.crawler = crawler
        self.guard = guard or CrawlGuard.from_config()
        self._closing = False

    @classmethod
    def from_crawler(cls, crawler):
        if not crawler.settings.getbool("CRAWL_GUARD_ENABLED", True):
            raise NotConfigured("CRAWL_GUARD_ENABLED is false")
        return cls(crawler=crawler)

    def _close(self, spider: Any, reason: str) -> None:
        if self._closing:
            return
        self._closing = True
        logger.critical(f"[CRAWL_GUARD] closing spider {getattr(spider, 'name', '?')}: {reason}")
        engine = getattr(self.crawler, "engine", None)
        if engine is not None:
            engine.close_spider(spider, reason)

    def process_request(self, request, spider=None):
        reason = self.guard.block_reason(stage="stage1")
        if reason:
            close_reason = "kill_switch" if reason == "kill_switch" else f"budget_exceeded:{reason.split(':', 1)[1]}"
            self._close(spider, close_reason)
            raise IgnoreRequest(f"crawl guard: {reason}")
        return None

    def process_response(self, request, response, spider=None):
        over = self.guard.charge(requests=1, nbytes=len(response.body or b""))
        if over:
            self._close(spider, f"budget_exceeded:{over}")
        return response
