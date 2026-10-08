"""Stage 1 soft-ban guard (#582).

Challenge/captcha responses are dropped (``IgnoreRequest``) instead of being
parsed, so their links are never followed or queued. A spike of soft bans on
one domain raises that domain's download-slot delay for the cooldown period
(the same lever AutoThrottle uses), then restores it.

Runs at priority 540, inside Scrapy's RetryMiddleware (550), so 429/503 are
retried first; only responses that survive retries are classified.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from scrapy.exceptions import IgnoreRequest
from scrapy.http import Response, TextResponse

from src.utils.soft_ban import DomainBackoff, SoftBanDetector, count_soft_ban, domain_of

logger = logging.getLogger(__name__)


class SoftBanMiddleware:
    def __init__(self, crawler: Any = None, detector: Optional[SoftBanDetector] = None,
                 backoff: Optional[DomainBackoff] = None, slot_delay: float = 30.0):
        self.crawler = crawler
        self.detector = detector or SoftBanDetector()
        self.backoff = backoff or DomainBackoff(stage="stage1")
        self.slot_delay = slot_delay
        self._restore: dict[str, tuple[float, float]] = {}  # slot key -> (old delay, restore at)

    @classmethod
    def from_crawler(cls, crawler: Any) -> "SoftBanMiddleware":
        return cls(crawler=crawler, slot_delay=crawler.settings.getfloat("SOFT_BAN_SLOT_DELAY", 30.0))

    def _slot(self, request: Any) -> tuple[Optional[str], Any]:
        try:
            downloader = self.crawler.engine.downloader
            key = downloader.get_slot_key(request)
            return key, downloader.slots.get(key)
        except Exception:
            return None, None

    def _maybe_restore(self, request: Any) -> None:
        key, slot = self._slot(request)
        if key is None or key not in self._restore:
            return
        old_delay, restore_at = self._restore[key]
        if self.backoff.clock() >= restore_at:
            if slot is not None:
                slot.delay = old_delay
            del self._restore[key]
            logger.info(f"[soft_ban] {key}: cooldown over; download delay restored to {old_delay}s")

    def _slow_down(self, request: Any) -> None:
        key, slot = self._slot(request)
        if key is None or slot is None:
            return
        if key not in self._restore:
            self._restore[key] = (slot.delay, self.backoff.clock() + self.backoff.cooldown)
        slot.delay = max(slot.delay, self.slot_delay)
        logger.warning(f"[soft_ban] {key}: download delay raised to {slot.delay}s for {self.backoff.cooldown:.0f}s")

    def process_request(self, request: Any, spider: Any = None) -> None:
        if self._restore:
            self._maybe_restore(request)
        return None

    def process_response(self, request: Any, response: Response, spider: Any = None) -> Response:
        body = response.text if isinstance(response, TextResponse) else ""
        headers = {
            k.decode("latin-1") if isinstance(k, bytes) else str(k):
            (v[0].decode("latin-1") if v and isinstance(v[0], bytes) else str(v[0]) if v else "")
            for k, v in response.headers.items()
        }
        sig = self.detector.detect(response.status, body, headers)
        if not sig:
            return response
        count_soft_ban("stage1", sig)
        stats = getattr(self.crawler, "stats", None)
        if stats is not None:
            stats.inc_value("soft_ban/count")
            stats.inc_value(f"soft_ban/{sig}")
        if self.backoff.record(domain_of(response.url)):
            self._slow_down(request)
        logger.warning(f"[soft_ban] {sig} (HTTP {response.status}) at {response.url[:80]}; dropped")
        raise IgnoreRequest(f"soft_ban:{sig}")
