"""Downloader middleware: refuse SSRF-like targets before download (#682).

Runs in ``process_request``. Scrapy's RedirectMiddleware turns each redirect
into a new Request that goes back through the downloader middlewares, so every
redirect hop is checked too. Settings:

- ``SSRF_GUARD_ENABLED`` (default True)
- ``SSRF_RESOLVE_DNS`` (default False): also block hostnames whose DNS answers
  are non-global. Off by default: resolution here would block the reactor.
- ``SSRF_ALLOWED_HOSTS``: comma list of hosts/IPs/CIDRs; falls back to the env var.
"""

from __future__ import annotations

import logging
from typing import Any

from scrapy.exceptions import IgnoreRequest

from src.utils.ssrf import count_blocked, ssrf_block_reason

logger = logging.getLogger(__name__)


class SSRFGuardMiddleware:
    def __init__(self, enabled: bool = True, resolve: bool = False, allowed_hosts: str | None = None):
        self.enabled = enabled
        self.resolve = resolve
        self.allowed_hosts = allowed_hosts
        self.blocked = 0

    @classmethod
    def from_crawler(cls, crawler: Any) -> "SSRFGuardMiddleware":
        s = crawler.settings
        allowed = s.get("SSRF_ALLOWED_HOSTS")
        if isinstance(allowed, (list, tuple)):
            allowed = ",".join(allowed)
        return cls(
            enabled=s.getbool("SSRF_GUARD_ENABLED", True),
            resolve=s.getbool("SSRF_RESOLVE_DNS", False),
            allowed_hosts=allowed or None,
        )

    def process_request(self, request: Any, spider: Any = None) -> None:
        if not self.enabled:
            return None
        reason = ssrf_block_reason(request.url, resolve=self.resolve, allowed_hosts=self.allowed_hosts)
        if reason is None:
            return None
        self.blocked += 1
        count_blocked("stage1", reason)
        logger.warning("SSRF guard blocked %s (%s)", request.url, reason)
        raise IgnoreRequest(f"ssrf_blocked:{reason}")
