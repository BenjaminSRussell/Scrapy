"""Optional egress-proxy rotation for Stage 1 downloads (#198).

Off unless a proxy list is configured. Operators enable it without code edits:

* environment: ``PROXY_LIST=http://a:8080,http://b:8080`` (and optionally
  ``PROXY_ROTATION_ENABLED=0`` to force it off)
* ``config.yml`` ``stage1.proxy.urls`` / ``stage1.proxy.enabled``
* or an explicit ``scrapy.proxy_list`` / ``scrapy.proxy_rotation_enabled``
  (the ``scrapy:`` section wins over ``stage1.proxy``; env wins over both)

An omitted enable flag turns rotation on when the list is non-empty. An
explicit false flag is the kill switch. An empty list never sends traffic
through a proxy.

The middleware runs at priority 745, just before Scrapy's
``HttpProxyMiddleware`` (750), and sets ``request.meta["proxy"]``. On HTTP
407, HTTP 429, or a download timeout the proxy that served the request is
quarantined for ``PROXY_QUARANTINE_SECONDS`` (default 300) and the request is
retried through the next healthy proxy, up to ``PROXY_MAX_ROTATIONS`` extra
attempts (default 3). If every proxy is quarantined the download is dropped
(``IgnoreRequest``) instead of falling back to the crawler's own IP.

Failures are counted in crawl stats ``proxy_rotation/failures`` and in
``scrapy_proxy_failures_total{spider,reason}``. ``scrapy_proxy_quarantined{spider}``
is how many proxies are in quarantine right now. Proxy userinfo is never
logged or used as a metric label.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from scrapy.exceptions import IgnoreRequest
from scrapy.http import Request, Response

from src.stage1.middlewares.fetch_policy_middleware import as_bool

logger = logging.getLogger(__name__)

DEFAULT_QUARANTINE_SECONDS = 300.0
DEFAULT_MAX_ROTATIONS = 3
PROXY_FAILURE_STATUSES = frozenset({407, 429})
MIDDLEWARE_PATH = "src.stage1.middlewares.proxy_rotation_middleware.ProxyRotationMiddleware"
# Just before HttpProxyMiddleware (750), so meta["proxy"] is set before it
# copies credentials into Proxy-Authorization.
MIDDLEWARE_PRIORITY = 745

try:
    from prometheus_client import Counter, Gauge

    PROXY_FAILURES: Any = Counter(
        "scrapy_proxy_failures_total",
        "Stage 1 downloads that failed through an egress proxy (407, 429, or timeout).",
        ["spider", "reason"],
    )
    PROXY_QUARANTINED: Any = Gauge(
        "scrapy_proxy_quarantined",
        "Egress proxies currently quarantined after a failure.",
        ["spider"],
    )
except Exception:  # prometheus_client missing or metric already registered
    PROXY_FAILURES = PROXY_QUARANTINED = None


def redact_proxy(url: str) -> str:
    """``http://user:pass@host:port`` -> ``http://***@host:port``."""
    text = (url or "").strip()
    parts = urlsplit(text if "://" in text else f"http://{text}")
    host = parts.hostname
    if not host:
        return "<invalid-proxy>"
    if ":" in host:
        host = f"[{host}]"
    netloc = f"***@{host}" if parts.username is not None or parts.password is not None else host
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme or "http", netloc, "", "", ""))


def normalize_proxy(value: str) -> str | None:
    """Accept ``http(s)://host:port`` or bare ``host:port``. Drop anything else."""
    text = value.strip()
    if not text:
        return None
    candidate = text if "://" in text else f"http://{text}"
    parts = urlsplit(candidate)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        logger.warning("[proxy_rotation] ignoring unsupported proxy %s", redact_proxy(candidate))
        return None
    return candidate


def parse_proxy_list(value: Any) -> list[str]:
    """Comma-separated string or list. Order is kept; blanks and dupes are dropped."""
    if value is None:
        return []
    if isinstance(value, str):
        items: list[Any] = value.split(",")
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        logger.warning("[proxy_rotation] proxy list must be a string or list, not %s", type(value).__name__)
        return []
    proxies: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = normalize_proxy(str(item))
        if normalized and normalized not in seen:
            seen.add(normalized)
            proxies.append(normalized)
    return proxies


def resolve_proxy_rotation_enabled(flag: Any, proxies: list[str]) -> bool:
    """On when ``proxies`` is non-empty, unless ``flag`` explicitly disables it.

    ``flag is None`` means the operator did not set the switch, so the list
    itself enables rotation. No list means off, even if the flag is true.
    """
    if not proxies:
        return False
    if flag is None:
        return True
    return as_bool(flag, default=False)


def _coerce_float(value: Any, default: float) -> float:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        logger.warning("[proxy_rotation] invalid quarantine %r; using %s", value, default)
        return default
    return max(0.0, parsed)


def _coerce_int(value: Any, default: int) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        logger.warning("[proxy_rotation] invalid max rotations %r; using %s", value, default)
        return default
    return max(0, parsed)


def proxy_settings_from_sources(
    scrapy_config: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve the ``PROXY_*`` settings ``src/settings.py`` publishes.

    ``env`` defaults to ``os.environ``. Pass ``{}`` in tests so the ambient
    environment cannot flip the result. Environment wins over the derived
    scrapy config (which already prefers ``scrapy.*`` over ``stage1.proxy.*``).
    """
    cfg = scrapy_config or {}
    source = os.environ if env is None else env
    raw_list = source["PROXY_LIST"] if "PROXY_LIST" in source else cfg.get("proxy_list")
    proxies = parse_proxy_list(raw_list)
    flag = source["PROXY_ROTATION_ENABLED"] if "PROXY_ROTATION_ENABLED" in source else cfg.get("proxy_rotation_enabled")
    quarantine_raw = (
        source["PROXY_QUARANTINE_SECONDS"]
        if "PROXY_QUARANTINE_SECONDS" in source
        else cfg.get("proxy_quarantine_seconds", DEFAULT_QUARANTINE_SECONDS)
    )
    rotations_raw = (
        source["PROXY_MAX_ROTATIONS"]
        if "PROXY_MAX_ROTATIONS" in source
        else cfg.get("proxy_max_rotations", DEFAULT_MAX_ROTATIONS)
    )
    return {
        "PROXY_LIST": proxies,
        "PROXY_ROTATION_ENABLED": resolve_proxy_rotation_enabled(flag, proxies),
        "PROXY_QUARANTINE_SECONDS": _coerce_float(quarantine_raw, DEFAULT_QUARANTINE_SECONDS),
        "PROXY_MAX_ROTATIONS": _coerce_int(rotations_raw, DEFAULT_MAX_ROTATIONS),
    }


def is_proxy_timeout(exception: BaseException) -> bool:
    """Download timeouts only. Connection refusals and DNS errors are not."""
    if isinstance(exception, TimeoutError):
        return True
    name = type(exception).__name__.lower()
    return "timeout" in name or "timedout" in name


class ProxyPool:
    """Round-robin proxy list with a per-proxy quarantine clock."""

    def __init__(self, proxies: list[str], quarantine_seconds: float, clock: Callable[[], float]):
        self.proxies = list(proxies)
        self.quarantine_seconds = max(0.0, float(quarantine_seconds))
        self.clock = clock
        self._until: dict[str, float] = {}
        self._cursor = 0

    def is_quarantined(self, proxy: str) -> bool:
        return self._until.get(proxy, 0.0) > self.clock()

    def quarantined_count(self) -> int:
        return sum(1 for proxy in self.proxies if self.is_quarantined(proxy))

    def quarantine(self, proxy: str) -> None:
        if proxy not in self.proxies:
            return
        self._until[proxy] = self.clock() + self.quarantine_seconds

    def has_healthy(self) -> bool:
        return any(not self.is_quarantined(proxy) for proxy in self.proxies)

    def next_proxy(self) -> str | None:
        count = len(self.proxies)
        if count == 0:
            return None
        for offset in range(count):
            index = (self._cursor + offset) % count
            proxy = self.proxies[index]
            if not self.is_quarantined(proxy):
                self._cursor = (index + 1) % count
                return proxy
        return None


class ProxyRotationMiddleware:
    def __init__(
        self,
        proxies: list[str] | None = None,
        *,
        enabled: bool = False,
        quarantine_seconds: float = DEFAULT_QUARANTINE_SECONDS,
        max_rotations: int = DEFAULT_MAX_ROTATIONS,
        clock: Callable[[], float] | None = None,
        stats: Any = None,
    ):
        self.enabled = bool(enabled) and bool(proxies)
        self.max_rotations = max(0, int(max_rotations))
        self.stats = stats
        self.pool = ProxyPool(list(proxies or []), quarantine_seconds, clock or time.monotonic)
        self._logged_exhausted = False

    @classmethod
    def from_crawler(cls, crawler: Any) -> ProxyRotationMiddleware:
        settings = crawler.settings
        proxies = parse_proxy_list(settings.get("PROXY_LIST"))
        enabled = as_bool(settings.get("PROXY_ROTATION_ENABLED"), default=False) and bool(proxies)
        return cls(
            proxies,
            enabled=enabled,
            quarantine_seconds=settings.getfloat("PROXY_QUARANTINE_SECONDS", DEFAULT_QUARANTINE_SECONDS),
            max_rotations=settings.getint("PROXY_MAX_ROTATIONS", DEFAULT_MAX_ROTATIONS),
            stats=getattr(crawler, "stats", None),
        )

    def _spider_name(self, spider: Any) -> str:
        name = getattr(spider, "name", None)
        return str(name or "unknown")

    def _inc(self, key: str) -> None:
        if self.stats is not None:
            self.stats.inc_value(key)

    def _sync_quarantine(self, spider: Any) -> None:
        count = self.pool.quarantined_count()
        if self.stats is not None and hasattr(self.stats, "set_value"):
            self.stats.set_value("proxy_rotation/quarantined", count)
        if PROXY_QUARANTINED is not None:
            try:
                PROXY_QUARANTINED.labels(spider=self._spider_name(spider)).set(count)
            except Exception:
                logger.debug("[proxy_rotation] quarantine gauge update failed", exc_info=True)

    def _record_failure(self, spider: Any, reason: str) -> None:
        self._inc("proxy_rotation/failures")
        self._inc(f"proxy_rotation/failures/{reason}")
        self._sync_quarantine(spider)
        if PROXY_FAILURES is None:
            return
        try:
            PROXY_FAILURES.labels(spider=self._spider_name(spider), reason=reason).inc()
        except Exception:
            logger.debug("[proxy_rotation] failure counter update failed", exc_info=True)

    def process_request(self, request: Request, spider: Any = None) -> None:
        if not self.enabled:
            return None
        # A caller-supplied proxy is not part of the pool and is left alone.
        if request.meta.get("proxy") and not request.meta.get("_proxy_rotation_managed"):
            return None
        proxy = self.pool.next_proxy()
        if proxy is None:
            self._inc("proxy_rotation/no_healthy_proxy")
            self._sync_quarantine(spider)
            if not self._logged_exhausted:
                logger.warning("[proxy_rotation] no healthy proxy; not falling back to the crawler IP")
                self._logged_exhausted = True
            raise IgnoreRequest("proxy_rotation: no healthy proxy")
        if self._logged_exhausted:
            logger.info("[proxy_rotation] a proxy left quarantine; downloads resume")
            self._logged_exhausted = False
        # Drop auth for the previous proxy before HttpProxyMiddleware sets the new one.
        request.headers.pop("Proxy-Authorization", None)
        request.meta["proxy"] = proxy
        request.meta["_proxy_assigned"] = proxy
        request.meta["_proxy_rotation_managed"] = True
        self._inc("proxy_rotation/assigned")
        self._sync_quarantine(spider)
        return None

    def _rotate(self, request: Request, spider: Any, reason: str) -> Request | None:
        proxy = request.meta.get("_proxy_assigned") or request.meta.get("proxy")
        if isinstance(proxy, str) and proxy:
            self.pool.quarantine(proxy)
            logger.warning(
                "[proxy_rotation] quarantined %s for %.0fs after %s",
                redact_proxy(proxy),
                self.pool.quarantine_seconds,
                reason,
            )
        self._record_failure(spider, reason)
        rotations = int(request.meta.get("_proxy_rotations", 0)) + 1
        if rotations > self.max_rotations or not self.pool.has_healthy():
            # RetryMiddleware must not send this same failure out again.
            request.meta["dont_retry"] = True
            return None
        retry = request.copy()
        retry.dont_filter = True
        retry.meta["_proxy_rotations"] = rotations
        retry.meta.pop("proxy", None)
        retry.meta.pop("_proxy_assigned", None)
        retry.headers.pop("Proxy-Authorization", None)
        self._inc("proxy_rotation/rotations")
        return retry

    def process_response(self, request: Request, response: Response, spider: Any = None) -> Request | Response:
        if not self.enabled or not request.meta.get("_proxy_rotation_managed"):
            return response
        if response.status not in PROXY_FAILURE_STATUSES:
            return response
        rotated = self._rotate(request, spider, str(response.status))
        return response if rotated is None else rotated

    def process_exception(self, request: Request, exception: Exception, spider: Any = None) -> Request | None:
        if not self.enabled or not request.meta.get("_proxy_rotation_managed"):
            return None
        if not is_proxy_timeout(exception):
            return None
        return self._rotate(request, spider, "timeout")
