"""Shared HTTP identity and egress policy for the aiohttp stages (#1629).

Stage 1 reads these values from the Scrapy settings. Stage 2 and the Stage 4
fetcher read the same keys through :class:`HttpPolicy`, so one policy applies
to every stage that talks to the web:

* ``USER_AGENT`` (``scrapy.user_agent``): sent on every request.
* ``ROBOTSTXT_OBEY`` (``scrapy.robotstxt_obey``): gates robots.txt in Stage 2.
* ``ROBOTS_MAX_CRAWL_DELAY``: cap on a Crawl-delay the robots file asks for.
* ``PROXY_*`` (``stage1.proxy.*``): the same egress pool, with the same quarantine clock.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from src.stage1.middlewares.proxy_rotation_middleware import ProxyPool, proxy_settings_from_sources

DEFAULT_USER_AGENT = "UConn-Discovery-Crawler/1.0"
DEFAULT_ROBOTS_MAX_CRAWL_DELAY = 60.0


class NoHealthyProxy(Exception):
    """Every egress proxy is quarantined. Callers defer the URL; they never go out from the crawler's own IP."""


def _settings_overlay() -> dict[str, Any]:
    # Lazy: src.settings wires the whole Scrapy middleware stack at import time.
    from src.settings import derive_scrapy_config

    return derive_scrapy_config()


@dataclass(frozen=True)
class HttpPolicy:
    user_agent: str
    robotstxt_obey: bool
    robots_max_crawl_delay: float
    proxy_list: tuple[str, ...]
    proxy_enabled: bool
    proxy_quarantine_seconds: float

    @classmethod
    def from_settings(
        cls, scrapy_config: Mapping[str, Any] | None = None, env: Mapping[str, str] | None = None
    ) -> HttpPolicy:
        """Resolve the policy the way ``src/settings.py`` does. Pass ``{}`` as ``env`` in tests."""
        cfg = _settings_overlay() if scrapy_config is None else scrapy_config
        source = os.environ if env is None else env
        proxies = proxy_settings_from_sources(cfg, env=env)
        obey_raw = source.get("ROBOTSTXT_OBEY", cfg.get("robotstxt_obey", True))
        delay_raw = source.get("ROBOTS_MAX_CRAWL_DELAY", cfg.get("robots_max_crawl_delay", DEFAULT_ROBOTS_MAX_CRAWL_DELAY))
        return cls(
            user_agent=str(cfg.get("user_agent") or DEFAULT_USER_AGENT),
            robotstxt_obey=str(obey_raw).strip().lower() not in {"0", "false", "no", "off"},
            robots_max_crawl_delay=float(delay_raw),
            proxy_list=tuple(proxies["PROXY_LIST"]),
            proxy_enabled=bool(proxies["PROXY_ROTATION_ENABLED"]),
            proxy_quarantine_seconds=float(proxies["PROXY_QUARANTINE_SECONDS"]),
        )

    def headers(self) -> dict[str, str]:
        return {"User-Agent": self.user_agent}


class EgressProxies:
    """Proxy pool for Stage 2 downloads. Disabled unless the Stage 1 ``PROXY_*`` policy enables it."""

    def __init__(self, policy: HttpPolicy, clock: Callable[[], float] = time.monotonic):
        self.enabled = policy.proxy_enabled and bool(policy.proxy_list)
        self._pool = ProxyPool(list(policy.proxy_list), policy.proxy_quarantine_seconds, clock)

    def pick(self) -> str | None:
        """Next healthy proxy, None when disabled. Raises NoHealthyProxy when all are quarantined."""
        if not self.enabled:
            return None
        proxy = self._pool.next_proxy()
        if proxy is None:
            raise NoHealthyProxy("every egress proxy is quarantined")
        return proxy

    def quarantine(self, proxy: str | None) -> None:
        if proxy:
            self._pool.quarantine(proxy)
