"""Parse sitemaps (including nested and gzipped variants) to collect URLs."""

import asyncio
import gzip
import logging
import threading
import xml.etree.ElementTree as ET
import zlib
from typing import Any

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_fromstring
from collections.abc import Iterator
from urllib.parse import urljoin, urlparse

import httpx

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
except (ImportError, ValueError):  # no prometheus_client, or already registered
    SITEMAP_LIMIT_HITS = SITEMAP_URLS_DISCOVERED = SITEMAP_FETCHES = None

# Defaults follow the sitemaps.org protocol: <= 50,000 URLs and <= 50 MiB
# (uncompressed) per sitemap file. A whole-site walk is capped so a huge or
# hostile sitemap tree (or an index pointing at itself) cannot exhaust memory.
DEFAULT_SITEMAP_LIMITS = {
    "max_depth": 5,
    "max_urls": 50_000,
    "max_sitemaps": 500,
    "max_bytes": 50 * 1024 * 1024,
}


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
    ):
        """Walk limits default to config ``stage1.sitemap.*`` (see sitemap_limits)."""
        limits = sitemap_limits()
        self.base_url = base_url
        self.timeout = timeout
        self.max_depth = limits["max_depth"] if max_depth is None else max_depth
        self.max_urls = limits["max_urls"] if max_urls is None else max_urls
        self.max_sitemaps = limits["max_sitemaps"] if max_sitemaps is None else max_sitemaps
        self.max_bytes = limits["max_bytes"] if max_bytes is None else max_bytes
        self.limits_hit: set[str] = set()
        self.stats: dict[str, int] = {"indexes": 0, "urlsets": 0, "skipped_depth": 0, "skipped_cap": 0}
        self.visited_sitemaps: set[str] = set()
        self.discovered_urls: set[str] = set()

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

        return list(self.discovered_urls)

    async def _parse_sitemap_recursive(
        self,
        client: httpx.AsyncClient,
        sitemap_url: str,
        depth: int = 0,
    ):
        """Walk a sitemap (and any nested indexes) while honoring depth and caps."""
        if depth > self.max_depth:
            self.stats["skipped_depth"] += 1
            self._limit_hit("depth", f"Max sitemap depth {self.max_depth} reached: {sitemap_url}")
            return

        if sitemap_url in self.visited_sitemaps:
            return

        if self._urls_full():
            return

        if len(self.visited_sitemaps) >= self.max_sitemaps:
            self.stats["skipped_cap"] += 1
            self._limit_hit("sitemaps", f"Sitemap fetch cap {self.max_sitemaps} reached; skipping {sitemap_url}")
            return

        self.visited_sitemaps.add(sitemap_url)

        try:
            logger.info(f"Parsing sitemap (depth={depth}): {sitemap_url}")
            response, content = await self._fetch(client, sitemap_url)

            if response.status_code != 200:
                logger.warning(f"Sitemap returned {response.status_code}: {sitemap_url}")
                return

            if content is None:
                self._limit_hit("bytes", f"Sitemap larger than {self.max_bytes} bytes skipped: {sitemap_url}")
                return
            if content[:2] == b"\x1f\x8b":
                # Still gzipped (a .gz file, or httpx left it encoded).
                try:
                    content = bounded_gunzip(content, self.max_bytes)
                    logger.debug(f"Decompressed gzipped sitemap: {sitemap_url}")
                except SitemapTooLarge:
                    self._limit_hit("bytes", f"Sitemap inflates past {self.max_bytes} bytes; skipped: {sitemap_url}")
                    return
                except (zlib.error, gzip.BadGzipFile, EOFError) as e:
                    logger.warning(f"Failed to decompress sitemap: {sitemap_url} - {e}")
                    return

            try:
                # defusedxml: sitemaps are untrusted remote XML (entity
                # expansion / XXE). Returns a stdlib Element.
                root = safe_fromstring(content)

                if self._is_sitemap_index(root):
                    logger.info(f"Found sitemap index: {sitemap_url}")
                    self.stats["indexes"] += 1
                    _count(SITEMAP_FETCHES, kind="index")
                    nested_sitemaps = self._extract_nested_sitemaps(root)

                    for nested_url in nested_sitemaps:
                        if self._urls_full():
                            break
                        await self._parse_sitemap_recursive(
                            client, urljoin(sitemap_url, nested_url), depth + 1
                        )

                else:
                    self.stats["urlsets"] += 1
                    _count(SITEMAP_FETCHES, kind="urlset")
                    urls = self._extract_urls_from_sitemap(root)
                    added = self._add_urls(urls)
                    logger.info(f"Extracted {len(urls)} URLs from {sitemap_url} ({added} new)")

            except DefusedXmlException as e:
                logger.warning(f"Rejected unsafe XML in sitemap {sitemap_url}: {e}")
                return
            except ET.ParseError as e:
                content_type = response.headers.get("content-type", "").lower()
                if "text/plain" in content_type or "text/html" in content_type:
                    logger.info(f"XML parsing failed, trying plain-text format: {sitemap_url}")
                    try:
                        text_content = content.decode("utf-8")
                        urls = self._extract_from_plain_text(text_content)
                        self._add_urls(urls)
                        logger.info(f"Extracted {len(urls)} URLs from plain-text sitemap: {sitemap_url}")
                    except Exception as text_error:
                        logger.warning(f"Plain-text parsing also failed for {sitemap_url}: {text_error}")
                else:
                    logger.warning(f"Failed to parse sitemap XML: {sitemap_url} - {e}")

        except Exception as e:
            _count(SITEMAP_FETCHES, kind="error")
            logger.warning(f"Error processing sitemap: {sitemap_url} - {e}")

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> tuple[httpx.Response, bytes | None]:
        """GET ``url``, reading at most ``max_bytes`` of (transport-decoded) body.

        Streaming keeps a huge body, or a Content-Encoding: gzip bomb that
        httpx would inflate, from being buffered whole. Returns
        ``(response, None)`` when the body exceeds the cap.
        """
        async with client.stream("GET", url) as response:
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

    def _urls_full(self) -> bool:
        return len(self.discovered_urls) >= self.max_urls

    def _add_urls(self, urls: Any) -> int:
        """Add URLs in document order up to ``max_urls``; returns how many were new."""
        before = len(self.discovered_urls)
        for url in urls:
            if len(self.discovered_urls) >= self.max_urls:
                self._limit_hit("urls", f"Sitemap URL cap {self.max_urls} reached; remaining URLs dropped")
                break
            self.discovered_urls.add(url)
        added = len(self.discovered_urls) - before
        _count(SITEMAP_URLS_DISCOVERED, added)
        return added

    def _limit_hit(self, limit: str, message: str) -> None:
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
                    priority = url_elem.find(f"{ns}priority")
                    changefreq = url_elem.find(f"{ns}changefreq")

                    if lastmod is not None or priority is not None:
                        priority_value = (
                            float(priority.text) if priority is not None and priority.text is not None else None
                        )
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

class SitemapIntegration:

    def __init__(self, spider):
        self.spider = spider
        self.parser = SitemapParser(spider.start_urls[0] if spider.start_urls else "")

    async def discover_sitemap_urls(self) -> list[str]:
        try:
            urls = await self.parser.discover_all_urls()
            logger.info(f"Sitemap discovery: found {len(urls)} URLs")
            return urls
        except Exception as e:
            logger.error(f"Sitemap discovery failed: {e}")
            return []

    def generate_scrapy_requests(self, urls: list[str]) -> Iterator:
        import scrapy

        for url in urls:
            yield scrapy.Request(
                url,
                callback=self.spider.parse,
                errback=self.spider.handle_error,
                meta={"depth": 0, "source": "sitemap"},
                priority=5,
                dont_filter=True,
            )

def discover_sitemaps_sync(base_url: str, timeout: int = 30) -> list[str]:
    parser = SitemapParser(base_url, timeout=timeout)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No event loop running on this thread - safe to drive one directly.
        try:
            return asyncio.run(parser.discover_all_urls())
        except Exception as e:
            logger.error(f"Sitemap discovery failed: {e}")
            return []

    # A loop is already running on this thread (e.g. Scrapy's own Twisted
    # asyncio reactor calling this during spider construction) - asyncio
    # forbids nesting event loops, so run the coroutine on a dedicated
    # thread with its own loop instead.
    result: list[str] = []

    def _worker():
        nonlocal result
        try:
            result = asyncio.run(parser.discover_all_urls())
        except Exception as e:
            logger.error(f"Sitemap discovery failed: {e}")

    thread = threading.Thread(target=_worker)
    thread.start()
    thread.join(timeout=timeout + 5)
    return result
