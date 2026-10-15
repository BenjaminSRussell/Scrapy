"""Parse sitemaps (including nested and gzipped variants) to collect URLs."""

import asyncio
import gzip
import logging
import threading
import time
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_fromstring

from src.stage1.discovery_handoff import DiscoveryHandoff
from src.utils.discovery_allowlist import (
    allowed_domains_from_config,
    effective_sitemap_allowlist,
    record_cross_domain_drop,
    url_host_allowed,
)

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter

    SITEMAP_LIMIT_HITS: Any = Counter(
        "sitemap_limit_hits_total",
        "Sitemap walks cut short by a configured limit (#206)",
        ["limit"],  # depth | urls | sitemaps | bytes
    )
    SITEMAP_URLS_DISCOVERED: Any = Counter(
        "sitemap_urls_discovered_total", "URLs collected from sitemaps"
    )
    SITEMAP_FETCHES: Any = Counter(
        "sitemap_fetches_total", "Sitemap documents fetched", ["kind"]  # index | urlset | error
    )
    SITEMAP_UNCHANGED_SKIPS: Any = Counter(
        "sitemap_unchanged_skipped_total",
        "Sitemap entries skipped because <lastmod> is not newer than the watermark (#394)",
        ["kind"],  # url | sitemap
    )
except (ImportError, ValueError):  # no prometheus_client, or already registered
    SITEMAP_LIMIT_HITS = SITEMAP_URLS_DISCOVERED = SITEMAP_FETCHES = SITEMAP_UNCHANGED_SKIPS = None

# Defaults follow the sitemaps.org protocol: <= 50,000 URLs and <= 50 MiB
# (uncompressed) per sitemap file. A whole-site walk is capped so a huge or
# hostile sitemap tree (or an index pointing at itself) cannot exhaust memory.
DEFAULT_SITEMAP_LIMITS = {
    "max_depth": 5,
    "max_urls": 50_000,
    "max_sitemaps": 500,
    "max_bytes": 50 * 1024 * 1024,
}

# Cross-domain redirects and loops are traps, not another host to walk (#940).
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_SITEMAP_REDIRECTS = 5


def sitemap_limits(config: Any = None) -> dict[str, int]:
    """Walk limits from ``stage1.sitemap.*`` (or ``stages.stage1.sitemap.*``)."""
    limits = dict(DEFAULT_SITEMAP_LIMITS)
    if config is None:
        try:
            from src.core.config import get_config

            config = get_config()
        except Exception:  # config is optional for this module
            return limits
    for key in limits:
        for prefix in ("stage1.sitemap", "stages.stage1.sitemap"):
            try:
                value = config.get(f"{prefix}.{key}")
            except Exception:
                value = None
            if value is None:
                continue
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                logger.warning(f"Ignoring invalid {prefix}.{key}={value!r}")
                continue
            if parsed >= 0:
                limits[key] = parsed
                break
    return limits


def _count(metric: Any, amount: int = 1, **labels: str) -> None:
    if metric is None or amount <= 0:
        return
    (metric.labels(**labels) if labels else metric).inc(amount)


class SitemapTooLarge(ValueError):
    """Decompressed sitemap exceeded ``max_bytes``."""


def bounded_gunzip(content: bytes, max_bytes: int) -> bytes:
    """gunzip that refuses to inflate past ``max_bytes`` (gzip-bomb guard)."""
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = inflater.decompress(content, max_bytes + 1)
    if len(out) > max_bytes or inflater.unconsumed_tail:
        raise SitemapTooLarge(f"decompressed sitemap exceeds {max_bytes} bytes")
    return out


DEFAULT_WATERMARK_MAX_AGE_DAYS = 30


def parse_lastmod(value: str | None) -> float | None:
    """Sitemap ``<lastmod>`` (W3C datetime) -> epoch seconds; None if absent or bad.

    Accepts ``YYYY-MM-DD``, ``YYYY-MM-DDThh:mm[:ss[.f]]`` with ``Z`` or an
    offset. A naive timestamp is read as UTC.
    """
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


class LastmodWatermarks:
    """Per-site ``<lastmod>`` watermarks for incremental sitemap reads (#394).

    One Redis hash per site, ``sitemap:lastmod:<host>``. Field = page URL or
    nested sitemap URL; value = ``"<lastmod epoch>|<recorded epoch>"``.

    An entry whose sitemap ``<lastmod>`` is not newer than the stored one is
    *unchanged* and is skipped, but only while the stored entry is younger than
    ``max_age_seconds``: every URL is re-enqueued at least that often, so a
    URL that was discovered but never fetched successfully is not lost forever.
    Entries without a ``<lastmod>`` are never skipped.

    Without a Redis client the store is in-memory (one process, tests).
    """

    KEY_PREFIX = "sitemap:lastmod:"

    def __init__(
        self,
        site: str,
        redis_client: Any = None,
        max_age_seconds: float = DEFAULT_WATERMARK_MAX_AGE_DAYS * 86400,
        clock: Any = time.time,
    ):
        self.site = site.lower()
        self.key = f"{self.KEY_PREFIX}{self.site}"
        self.redis = redis_client
        self.max_age_seconds = float(max_age_seconds)
        self.clock = clock
        self._stored: dict[str, tuple[float, float]] | None = None
        self._pending: dict[str, str] = {}

    def _load(self) -> dict[str, tuple[float, float]]:
        if self._stored is not None:
            return self._stored
        stored: dict[str, tuple[float, float]] = {}
        if self.redis is not None:
            try:
                raw = self.redis.hgetall(self.key) or {}
            except Exception as e:  # Redis down: behave like a full (non-incremental) read
                logger.warning(f"Sitemap watermarks unavailable for {self.site}: {e}")
                raw = {}
            for field, value in raw.items():
                field = field.decode() if isinstance(field, bytes) else str(field)
                value = value.decode() if isinstance(value, bytes) else str(value)
                parts = value.split("|")
                try:
                    stored[field] = (float(parts[0]), float(parts[1]) if len(parts) > 1 else 0.0)
                except (ValueError, IndexError):
                    continue
        self._stored = stored
        return stored

    def is_unchanged(self, url: str, lastmod: float | None) -> bool:
        if lastmod is None:
            return False
        entry = self._load().get(url)
        if entry is None:
            return False
        stored_lastmod, recorded_at = entry
        if self.clock() - recorded_at > self.max_age_seconds:
            return False
        return lastmod <= stored_lastmod

    def record(self, url: str, lastmod: float | None) -> None:
        if lastmod is None:
            return
        now = self.clock()
        self._load()[url] = (lastmod, now)
        self._pending[url] = f"{lastmod}|{now}"

    def flush(self) -> int:
        """Persist recorded watermarks; returns how many were written."""
        pending, self._pending = self._pending, {}
        if not pending or self.redis is None:
            return len(pending)
        try:
            items = list(pending.items())
            for start in range(0, len(items), 1000):
                self.redis.hset(self.key, mapping=dict(items[start : start + 1000]))
            # Whole-hash expiry only cleans up abandoned sites; per-entry age
            # (max_age_seconds) is what forces periodic re-enqueue.
            self.redis.expire(self.key, int(self.max_age_seconds * 2) or 1)
        except Exception as e:
            logger.warning(f"Could not persist sitemap watermarks for {self.site}: {e}")
            return 0
        return len(pending)


def sitemap_incremental_settings(config: Any = None) -> tuple[bool, float]:
    """``(incremental, max_age_seconds)`` from ``stage1.sitemap.*``."""
    if config is None:
        try:
            from src.core.config import get_config

            config = get_config()
        except Exception:
            return False, DEFAULT_WATERMARK_MAX_AGE_DAYS * 86400.0
    enabled = config.get("stage1.sitemap.incremental")
    days = config.get("stage1.sitemap.watermark_max_age_days")
    try:
        max_age = float(days) * 86400 if days is not None else DEFAULT_WATERMARK_MAX_AGE_DAYS * 86400.0
    except (TypeError, ValueError):
        max_age = DEFAULT_WATERMARK_MAX_AGE_DAYS * 86400.0
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() in {"1", "true", "yes", "on"}
    return bool(enabled), max_age


class SitemapParser:

    NAMESPACES = {
        "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
        "news": "http://www.google.com/schemas/sitemap-news/0.9",
        "image": "http://www.google.com/schemas/sitemap-image/1.1",
        "video": "http://www.google.com/schemas/sitemap-video/1.1",
    }

    def __init__(
        self,
        base_url: str,
        timeout: int = 30,
        max_depth: int | None = None,
        max_urls: int | None = None,
        max_sitemaps: int | None = None,
        max_bytes: int | None = None,
        watermarks: LastmodWatermarks | None = None,
        *,
        allowed_domains: Iterable[str] | None = None,
    ):
        """Walk limits default to config ``stage1.sitemap.*`` (see sitemap_limits).

        ``watermarks`` turns on incremental reads (#394): URLs and nested
        sitemaps whose ``<lastmod>`` is not newer than the stored watermark are
        skipped. Watermarks are written only at the end of a walk.

        ``allowed_domains`` is the shared ``stage1.allowed_domains`` list
        (#940). Nested sitemap locs, page locs, and redirects whose host is
        outside it are dropped and counted on ``cross_domain_sitemap_dropped``.
        Omit it to read that list from config. A seed host that is not on the
        list keeps the walk on the seed host only.
        """
        limits = sitemap_limits()
        self.base_url = base_url
        self.timeout = timeout
        configured = allowed_domains_from_config() if allowed_domains is None else allowed_domains
        self.sitemap_allowlist = effective_sitemap_allowlist(base_url, configured)
        self.max_depth = limits["max_depth"] if max_depth is None else max_depth
        self.max_urls = limits["max_urls"] if max_urls is None else max_urls
        self.max_sitemaps = limits["max_sitemaps"] if max_sitemaps is None else max_sitemaps
        self.max_bytes = limits["max_bytes"] if max_bytes is None else max_bytes
        self.limits_hit: set[str] = set()
        self.stats: dict[str, int] = {
            "indexes": 0,
            "urlsets": 0,
            "skipped_depth": 0,
            "skipped_cap": 0,
            "skipped_unchanged_urls": 0,
            "skipped_unchanged_sitemaps": 0,
            "dropped_cross_domain": 0,
        }
        self.visited_sitemaps: set[str] = set()
        self.discovered_urls: set[str] = set()
        self._dropped_cross_domain: set[str] = set()
        self.watermarks = watermarks
        self._lastmods: dict[str, float | None] = {}
        self._limit_events = 0
        self._hard_errors: list[str] = []
        self._any_success = False

    async def discover_all_urls(self) -> list[str]:
        parsed = urlparse(self.base_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        sitemap_urls = [
            urljoin(base, "/sitemap.xml"),
            urljoin(base, "/sitemap.xml.gz"),
            urljoin(base, "/sitemap_index.xml"),
            urljoin(base, "/sitemap_index.xml.gz"),
            urljoin(base, "/sitemap-index.xml"),
            urljoin(base, "/sitemap-index.xml.gz"),
            urljoin(base, "/sitemaps/sitemap.xml"),
            urljoin(base, "/sitemap/sitemap.xml"),
        ]

        headers = {"User-Agent": "SitemapParser/1.0 (compatible; web crawler)"}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            for sitemap_url in sitemap_urls:
                await self._parse_sitemap_recursive(client, sitemap_url, depth=0)

        if self.watermarks is not None:
            self.watermarks.flush()
        return list(self.discovered_urls)

    async def discover_with_handoff(
        self,
        *,
        discovery_source: str = "sitemap_parser",
        job_id: str | None = None,
    ) -> DiscoveryHandoff:
        """Discover sitemap URLs and return success, empty, or failed (#647).

        ``discover_all_urls`` stays the walk (caps, gzip, lastmod watermarks).
        A walk that never received HTTP 200 and recorded transport errors is
        failed, not an empty site.
        """
        site = urlparse(self.base_url).netloc.lower() or None
        try:
            urls = await self.discover_all_urls()
        except Exception as e:
            logger.error(f"Sitemap discovery hard failure: {e}")
            return DiscoveryHandoff.failed(
                str(e),
                discovery_source=discovery_source,
                job_id=job_id,
                site=site,
            )
        if urls:
            return DiscoveryHandoff.success(
                urls,
                discovery_source=discovery_source,
                job_id=job_id,
                site=site,
            )
        if self._hard_errors and not self._any_success:
            return DiscoveryHandoff.failed(
                "; ".join(self._hard_errors[:5]),
                discovery_source=discovery_source,
                job_id=job_id,
                site=site,
            )
        return DiscoveryHandoff.empty(
            discovery_source=discovery_source,
            job_id=job_id,
            site=site,
        )

    async def _parse_sitemap_recursive(
        self,
        client: httpx.AsyncClient,
        sitemap_url: str,
        depth: int = 0,
    ) -> bool:
        """Walk a sitemap (and any nested indexes) while honoring depth and caps.

        Returns True only when the sitemap (and everything under it) was read
        completely, so its ``<lastmod>`` watermark may be advanced.
        """
        if depth > self.max_depth:
            self.stats["skipped_depth"] += 1
            self._limit_hit("depth", f"Max sitemap depth {self.max_depth} reached: {sitemap_url}")
            return False

        if sitemap_url in self.visited_sitemaps:
            return False

        if not url_host_allowed(sitemap_url, self.sitemap_allowlist):
            self._note_cross_domain(sitemap_url, "nested sitemap")
            return False

        if self._urls_full():
            return False

        if len(self.visited_sitemaps) >= self.max_sitemaps:
            self.stats["skipped_cap"] += 1
            self._limit_hit("sitemaps", f"Sitemap fetch cap {self.max_sitemaps} reached; skipping {sitemap_url}")
            return False

        self.visited_sitemaps.add(sitemap_url)

        try:
            logger.info(f"Parsing sitemap (depth={depth}): {sitemap_url}")
            fetched = await self._fetch(client, sitemap_url)
            if fetched is None:
                return False
            response, content = fetched

            if response.status_code != 200:
                logger.warning(f"Sitemap returned {response.status_code}: {sitemap_url}")
                return False

            self._any_success = True
            if content is None:
                self._limit_hit("bytes", f"Sitemap larger than {self.max_bytes} bytes skipped: {sitemap_url}")
                return False
            if content[:2] == b"\x1f\x8b":
                # Still gzipped (a .gz file, or httpx left it encoded).
                try:
                    content = bounded_gunzip(content, self.max_bytes)
                    logger.debug(f"Decompressed gzipped sitemap: {sitemap_url}")
                except SitemapTooLarge:
                    self._limit_hit("bytes", f"Sitemap inflates past {self.max_bytes} bytes; skipped: {sitemap_url}")
                    return False
                except (zlib.error, gzip.BadGzipFile, EOFError) as e:
                    logger.warning(f"Failed to decompress sitemap: {sitemap_url} - {e}")
                    return False

            try:
                # defusedxml: sitemaps are untrusted remote XML (entity
                # expansion / XXE). Returns a stdlib Element.
                root = safe_fromstring(content)

                if self._is_sitemap_index(root):
                    logger.info(f"Found sitemap index: {sitemap_url}")
                    self.stats["indexes"] += 1
                    _count(SITEMAP_FETCHES, kind="index")
                    nested_sitemaps = self._extract_nested_sitemaps(root)

                    complete = True
                    for nested_url in nested_sitemaps:
                        if self._urls_full():
                            complete = False
                            break
                        nested_abs = urljoin(sitemap_url, nested_url)
                        lastmod = self._lastmods.get(nested_url)
                        if self.watermarks is not None and self.watermarks.is_unchanged(nested_abs, lastmod):
                            # Nothing in this child changed since the last full read (#394).
                            self.stats["skipped_unchanged_sitemaps"] += 1
                            _count(SITEMAP_UNCHANGED_SKIPS, kind="sitemap")
                            continue
                        events_before = self._limit_events
                        ok = await self._parse_sitemap_recursive(client, nested_abs, depth + 1)
                        if ok and self._limit_events == events_before:
                            if self.watermarks is not None:
                                self.watermarks.record(nested_abs, lastmod)
                        else:
                            complete = False
                    return complete

                else:
                    self.stats["urlsets"] += 1
                    _count(SITEMAP_FETCHES, kind="urlset")
                    urls = self._extract_urls_from_sitemap(root)
                    fresh = self._drop_unchanged(urls)
                    events_before = self._limit_events
                    added = self._add_urls(fresh)
                    logger.info(
                        f"Extracted {len(urls)} URLs from {sitemap_url} ({added} new, "
                        f"{len(urls) - len(fresh)} unchanged since last read)"
                    )
                    return self._limit_events == events_before

            except DefusedXmlException as e:
                logger.warning(f"Rejected unsafe XML in sitemap {sitemap_url}: {e}")
                return False
            except ET.ParseError as e:
                content_type = response.headers.get("content-type", "").lower()
                if "text/plain" in content_type or "text/html" in content_type:
                    logger.info(f"XML parsing failed, trying plain-text format: {sitemap_url}")
                    try:
                        text_content = content.decode("utf-8")
                        urls = self._extract_from_plain_text(text_content)
                        self._add_urls(urls)
                        logger.info(f"Extracted {len(urls)} URLs from plain-text sitemap: {sitemap_url}")
                        return True
                    except Exception as text_error:
                        logger.warning(f"Plain-text parsing also failed for {sitemap_url}: {text_error}")
                else:
                    logger.warning(f"Failed to parse sitemap XML: {sitemap_url} - {e}")

        except Exception as e:
            _count(SITEMAP_FETCHES, kind="error")
            logger.warning(f"Error processing sitemap: {sitemap_url} - {e}")
            self._hard_errors.append(f"{sitemap_url}: {e}")
        return False

    def _note_cross_domain(self, url: str, reason: str) -> None:
        if url in self._dropped_cross_domain:
            return
        self._dropped_cross_domain.add(url)
        self.stats["dropped_cross_domain"] += 1
        record_cross_domain_drop(1)
        logger.warning("Dropping cross-domain sitemap trap (%s): %s", reason, url)

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> tuple[httpx.Response, bytes | None] | None:
        """GET ``url``, reading at most ``max_bytes`` of (transport-decoded) body.

        Streaming keeps a huge body, or a Content-Encoding: gzip bomb that
        httpx would inflate, from being buffered whole. Returns
        ``(response, None)`` when the body exceeds the cap.

        Redirects are followed only while the next host stays on the shared
        allowlist, and only up to ``_MAX_SITEMAP_REDIRECTS``. A cross-domain
        target or a loop returns None (the document is dropped, not parsed).
        """
        current = url
        seen: set[str] = set()
        for _hop in range(_MAX_SITEMAP_REDIRECTS + 1):
            if current in seen:
                self._note_cross_domain(current, "redirect loop")
                return None
            seen.add(current)
            if not url_host_allowed(current, self.sitemap_allowlist):
                self._note_cross_domain(current, "redirect left allowlist")
                return None
            async with client.stream("GET", current, follow_redirects=False) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        return response, b""
                    current = urljoin(str(response.url), location.strip())
                    continue
                if response.status_code != 200:
                    return response, b""
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self.max_bytes:
                        return response, None
                    chunks.append(chunk)
                return response, b"".join(chunks)
        self._note_cross_domain(current, "redirect loop")
        return None

    def _urls_full(self) -> bool:
        return len(self.discovered_urls) >= self.max_urls

    def _drop_unchanged(self, urls: list[str]) -> list[str]:
        """Filter out URLs whose ``<lastmod>`` is not newer than the watermark (#394)."""
        if self.watermarks is None:
            return urls
        fresh = [u for u in urls if not self.watermarks.is_unchanged(u, self._lastmods.get(u))]
        skipped = len(urls) - len(fresh)
        if skipped:
            self.stats["skipped_unchanged_urls"] += skipped
            _count(SITEMAP_UNCHANGED_SKIPS, skipped, kind="url")
        return fresh

    def _add_urls(self, urls: Any) -> int:
        """Add URLs in document order up to ``max_urls``; returns how many were new."""
        before = len(self.discovered_urls)
        for url in urls:
            if not url_host_allowed(url, self.sitemap_allowlist):
                self._note_cross_domain(url, "sitemap loc")
                continue
            if len(self.discovered_urls) >= self.max_urls:
                self._limit_hit("urls", f"Sitemap URL cap {self.max_urls} reached; remaining URLs dropped")
                break
            self.discovered_urls.add(url)
            if self.watermarks is not None:
                # Only URLs actually handed on get a watermark; cap-dropped ones
                # stay "new" for the next read.
                self.watermarks.record(url, self._lastmods.get(url))
        added = len(self.discovered_urls) - before
        _count(SITEMAP_URLS_DISCOVERED, added)
        return added

    def _limit_hit(self, limit: str, message: str) -> None:
        self._limit_events += 1
        _count(SITEMAP_LIMIT_HITS, limit=limit)
        if limit not in self.limits_hit:
            self.limits_hit.add(limit)
            logger.warning(message)
        else:
            logger.debug(message)

    def _is_sitemap_index(self, root: ET.Element) -> bool:
        if root.tag.endswith("sitemapindex"):
            return True

        for ns in ["", "{http://www.sitemaps.org/schemas/sitemap/0.9}"]:
            if root.find(f"{ns}sitemap") is not None:
                return True

        return False

    def _extract_nested_sitemaps(self, root: ET.Element) -> list[str]:
        sitemaps = []

        for ns in ["", "{http://www.sitemaps.org/schemas/sitemap/0.9}"]:
            for sitemap in root.findall(f"{ns}sitemap"):
                loc = sitemap.find(f"{ns}loc")
                if loc is not None and loc.text:
                    sitemap_url = loc.text.strip()
                    sitemaps.append(sitemap_url)

                    lastmod = sitemap.find(f"{ns}lastmod")
                    self._lastmods[sitemap_url] = parse_lastmod(lastmod.text if lastmod is not None else None)
                    if lastmod is not None and lastmod.text:
                        logger.debug(f"Sitemap {sitemap_url} last modified: {lastmod.text}")

        return sitemaps

    def _extract_urls_from_sitemap(self, root: ET.Element) -> list[str]:
        """URLs in document order (so the ``max_urls`` cap keeps the first ones)."""
        urls: dict[str, None] = {}

        for ns in ["", "{http://www.sitemaps.org/schemas/sitemap/0.9}"]:
            for url_elem in root.findall(f"{ns}url"):
                loc = url_elem.find(f"{ns}loc")
                if loc is not None and loc.text:
                    url = loc.text.strip()
                    urls[url] = None

                    lastmod = url_elem.find(f"{ns}lastmod")
                    self._lastmods[url] = parse_lastmod(lastmod.text if lastmod is not None else None)
                    priority = url_elem.find(f"{ns}priority")
                    changefreq = url_elem.find(f"{ns}changefreq")

                    if lastmod is not None or priority is not None:
                        # #234: a junk <priority> must not discard the whole urlset.
                        priority_value = self._parse_priority(priority.text if priority is not None else None)
                        metadata = {
                            "url": url,
                            "lastmod": lastmod.text if lastmod is not None else None,
                            "priority": priority_value,
                            "changefreq": (changefreq.text if changefreq is not None else None),
                        }
                        logger.debug(f"URL metadata: {metadata}")

        return list(urls)

    def _extract_from_plain_text(self, text: str) -> list[str]:
        urls: dict[str, None] = {}

        for line in text.split("\n"):
            line = line.strip()
            if line and (line.startswith("http://") or line.startswith("https://")):
                urls[line] = None

        return list(urls)

    @staticmethod
    def _parse_priority(text: str | None) -> float | None:
        """Sitemap <priority> as a float in [0, 1], or None when absent/invalid."""
        if text is None:
            return None
        try:
            value = float(text.strip())
        except ValueError:
            return None
        return value if 0.0 <= value <= 1.0 else None

class SitemapIntegration:

    def __init__(self, spider):
        self.spider = spider
        self.parser = SitemapParser(spider.start_urls[0] if spider.start_urls else "")

    async def discover_sitemap_urls(self) -> list[str]:
        handoff = await self.discover_sitemap_handoff()
        return list(handoff.discovered)

    async def discover_sitemap_handoff(self) -> DiscoveryHandoff:
        try:
            handoff = await self.parser.discover_with_handoff(discovery_source="sitemap_parser")
            logger.info(
                f"Sitemap discovery handoff: status={handoff.status.value} "
                f"discovered={len(handoff.discovered)}"
            )
            return handoff
        except Exception as e:
            logger.error(f"Sitemap discovery failed: {e}")
            return DiscoveryHandoff.failed(str(e), discovery_source="sitemap_parser")

    def generate_scrapy_requests(self, urls: list[str]) -> Iterator:
        import scrapy

        for url in urls:
            yield scrapy.Request(
                url,
                callback=self.spider.parse,
                errback=self.spider.handle_error,
                meta={"depth": 0, "source": "sitemap", "discovery_source": "sitemap_parser"},
                priority=5,
                dont_filter=True,
            )

def default_watermarks(base_url: str, config: Any = None) -> LastmodWatermarks | None:
    """Redis-backed watermarks when ``stage1.sitemap.incremental`` is on (#394)."""
    enabled, max_age = sitemap_incremental_settings(config)
    if not enabled:
        return None
    site = urlparse(base_url).netloc.lower()
    if not site:
        return None
    try:
        from src.utils.redis import get_redis

        client = get_redis().client
    except Exception as e:
        logger.warning(f"Incremental sitemap reads disabled (no Redis): {e}")
        return None
    return LastmodWatermarks(site, client, max_age_seconds=max_age)


_DEFAULT = object()


def discover_sitemaps_sync(base_url: str, timeout: int = 30, watermarks: Any = _DEFAULT) -> list[str]:
    """Walk ``base_url``'s sitemaps. Incremental (#394) unless ``watermarks=None``."""
    return list(discover_sitemaps_handoff_sync(base_url, timeout=timeout, watermarks=watermarks).discovered)


def discover_sitemaps_handoff_sync(
    base_url: str,
    timeout: int = 30,
    watermarks: Any = _DEFAULT,
    *,
    discovery_source: str = "sitemap_parser",
    job_id: str | None = None,
) -> DiscoveryHandoff:
    """Sync walk returning success|empty|failed (#647).

    A running loop (Scrapy's reactor during spider setup) is driven on a
    worker thread, same as before. Exceptions and timeouts yield failed, not
    empty, and the list helper still returns ``[]`` for those.
    """
    if watermarks is _DEFAULT:
        watermarks = default_watermarks(base_url)
    parser = SitemapParser(base_url, timeout=timeout, watermarks=watermarks)
    site = urlparse(base_url).netloc.lower() or None
    found: list[str] = []
    error: BaseException | None = None

    def _run() -> None:
        nonlocal found, error
        try:
            found = asyncio.run(parser.discover_all_urls())
        except Exception as e:
            error = e
            logger.error(f"Sitemap discovery failed: {e}")

    def _classify(timed_out: bool = False) -> DiscoveryHandoff:
        if timed_out:
            return DiscoveryHandoff.failed(
                "sitemap discovery timed out",
                discovery_source=discovery_source,
                job_id=job_id,
                site=site,
            )
        if error is not None:
            return DiscoveryHandoff.failed(
                str(error),
                discovery_source=discovery_source,
                job_id=job_id,
                site=site,
            )
        if found:
            return DiscoveryHandoff.success(
                found,
                discovery_source=discovery_source,
                job_id=job_id,
                site=site,
            )
        if parser._hard_errors and not parser._any_success:
            return DiscoveryHandoff.failed(
                "; ".join(parser._hard_errors[:5]),
                discovery_source=discovery_source,
                job_id=job_id,
                site=site,
            )
        return DiscoveryHandoff.empty(
            discovery_source=discovery_source,
            job_id=job_id,
            site=site,
        )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _run()
        return _classify()

    thread = threading.Thread(target=_run)
    thread.start()
    thread.join(timeout=timeout + 5)
    if thread.is_alive():
        logger.error(f"Sitemap discovery timed out for {base_url}")
        return _classify(timed_out=True)
    return _classify()
