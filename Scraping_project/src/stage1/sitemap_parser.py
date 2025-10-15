"""Parse sitemaps (including nested and gzipped variants) to collect URLs."""

import gzip
import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)


class SitemapParser:
    """Recursively walk sitemap indexes and return discovered URLs."""

    # XML namespaces commonly used in sitemaps
    NAMESPACES = {
        "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
        "news": "http://www.google.com/schemas/sitemap-news/0.9",
        "image": "http://www.google.com/schemas/sitemap-image/1.1",
        "video": "http://www.google.com/schemas/sitemap-video/1.1",
    }

    def __init__(self, base_url: str, timeout: int = 30, max_depth: int = 5):
        """Store crawl settings and per-run caches."""
        self.base_url = base_url
        self.timeout = timeout
        self.max_depth = max_depth
        self.visited_sitemaps: set[str] = set()
        self.discovered_urls: set[str] = set()

    async def discover_all_urls(self) -> list[str]:
        """Return every URL advertised by the site's sitemap footprint."""
        parsed = urlparse(self.base_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        robots_sitemaps = await self._get_sitemaps_from_robots(base)

        common_sitemap_urls = [
            urljoin(base, "/sitemap.xml"),
            urljoin(base, "/sitemap.xml.gz"),
            urljoin(base, "/sitemap_index.xml"),
            urljoin(base, "/sitemap_index.xml.gz"),
            urljoin(base, "/sitemap-index.xml"),
            urljoin(base, "/sitemap-index.xml.gz"),
            urljoin(base, "/sitemaps/sitemap.xml"),
            urljoin(base, "/sitemap/sitemap.xml"),
        ]

        sitemap_urls = robots_sitemaps + common_sitemap_urls

        headers = {"User-Agent": "SitemapParser/1.0 (compatible; web crawler)"}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            for sitemap_url in sitemap_urls:
                await self._parse_sitemap_recursive(client, sitemap_url, depth=0)

        return list(self.discovered_urls)

    async def _get_sitemaps_from_robots(self, base_url: str) -> list[str]:
        """Return sitemap URLs declared in robots.txt."""
        robots_url = urljoin(base_url, "/robots.txt")
        sitemaps = []

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(robots_url)
                if response.status_code == 200:
                    for line in response.text.split("\n"):
                        line = line.strip()
                        if line.lower().startswith("sitemap:"):
                            sitemap_url = line.split(":", 1)[1].strip()
                            sitemaps.append(sitemap_url)
                            logger.info(f"Found sitemap in robots.txt: {sitemap_url}")
        except Exception as e:
            logger.debug(f"Failed to fetch robots.txt: {e}")

        return sitemaps

    async def _parse_sitemap_recursive(
        self,
        client: httpx.AsyncClient,
        sitemap_url: str,
        depth: int = 0,
    ):
        """Walk a sitemap (and any nested indexes) while honoring depth."""
        if depth > self.max_depth:
            logger.warning(f"Max sitemap depth reached: {sitemap_url}")
            return

        if sitemap_url in self.visited_sitemaps:
            return

        self.visited_sitemaps.add(sitemap_url)

        try:
            logger.info(f"Parsing sitemap (depth={depth}): {sitemap_url}")
            response = await client.get(sitemap_url)

            if response.status_code != 200:
                logger.warning(
                    f"Sitemap returned {response.status_code}: {sitemap_url}"
                )
                return

            # Support gzipped sitemap payloads
            content = response.content
            if (
                sitemap_url.endswith(".gz")
                or response.headers.get("content-encoding") == "gzip"
            ):
                try:
                    content = gzip.decompress(content)
                    logger.debug(f"Decompressed gzipped sitemap: {sitemap_url}")
                except Exception as e:
                    logger.warning(f"Failed to decompress sitemap: {sitemap_url} - {e}")
                    return

            # Try to parse as XML first
            try:
                root = ET.fromstring(content)

                # Check if this is a sitemap index (contains nested sitemaps)
                if self._is_sitemap_index(root):
                    logger.info(f"Found sitemap index: {sitemap_url}")
                    nested_sitemaps = self._extract_nested_sitemaps(root)

                    # Recursively parse nested sitemaps
                    for nested_url in nested_sitemaps:
                        await self._parse_sitemap_recursive(
                            client, nested_url, depth + 1
                        )

                else:
                    # Regular sitemap with URLs
                    urls = self._extract_urls_from_sitemap(root)
                    self.discovered_urls.update(urls)
                    logger.info(f"Extracted {len(urls)} URLs from {sitemap_url}")

            except ET.ParseError as e:
                # Fall back to plain-text parsing if needed
                content_type = response.headers.get("content-type", "").lower()
                if "text/plain" in content_type or "text/html" in content_type:
                    logger.info(
                        f"XML parsing failed, trying plain-text format: {sitemap_url}"
                    )
                    try:
                        text_content = content.decode("utf-8")
                        urls = self._extract_from_plain_text(text_content)
                        self.discovered_urls.update(urls)
                        logger.info(
                            f"Extracted {len(urls)} URLs from plain-text sitemap: {sitemap_url}"
                        )
                    except Exception as text_error:
                        logger.warning(
                            f"Plain-text parsing also failed for {sitemap_url}: {text_error}"
                        )
                else:
                    logger.warning(f"Failed to parse sitemap XML: {sitemap_url} - {e}")

        except Exception as e:
            logger.warning(f"Error processing sitemap: {sitemap_url} - {e}")

    def _is_sitemap_index(self, root: ET.Element) -> bool:
        """Return True when the document describes nested sitemaps."""
        if root.tag.endswith("sitemapindex"):
            return True

        for ns in ["", "{http://www.sitemaps.org/schemas/sitemap/0.9}"]:
            if root.find(f"{ns}sitemap") is not None:
                return True

        return False

    def _extract_nested_sitemaps(self, root: ET.Element) -> list[str]:
        """Return nested sitemap URLs declared in an index file."""
        sitemaps = []

        for ns in ["", "{http://www.sitemaps.org/schemas/sitemap/0.9}"]:
            for sitemap in root.findall(f"{ns}sitemap"):
                loc = sitemap.find(f"{ns}loc")
                if loc is not None and loc.text:
                    sitemap_url = loc.text.strip()
                    sitemaps.append(sitemap_url)

                    lastmod = sitemap.find(f"{ns}lastmod")
                    if lastmod is not None and lastmod.text:
                        logger.debug(
                            f"Sitemap {sitemap_url} last modified: {lastmod.text}"
                        )

        return sitemaps

    def _extract_urls_from_sitemap(self, root: ET.Element) -> set[str]:
        """Return URL entries referenced by a standard sitemap file."""
        urls = set()

        for ns in ["", "{http://www.sitemaps.org/schemas/sitemap/0.9}"]:
            for url_elem in root.findall(f"{ns}url"):
                loc = url_elem.find(f"{ns}loc")
                if loc is not None and loc.text:
                    url = loc.text.strip()
                    urls.add(url)

                    lastmod = url_elem.find(f"{ns}lastmod")
                    priority = url_elem.find(f"{ns}priority")
                    changefreq = url_elem.find(f"{ns}changefreq")

                    if lastmod is not None or priority is not None:
                        priority_value = (
                            float(priority.text)
                            if priority is not None and priority.text is not None
                            else None
                        )
                        metadata = {
                            "url": url,
                            "lastmod": lastmod.text if lastmod is not None else None,
                            "priority": priority_value,
                            "changefreq": (
                                changefreq.text if changefreq is not None else None
                            ),
                        }
                        logger.debug(f"URL metadata: {metadata}")

        return urls

    def _extract_from_plain_text(self, text: str) -> set[str]:
        """Return URLs from a plain-text sitemap (one per line)."""
        urls = set()

        for line in text.split("\n"):
            line = line.strip()
            if line and (line.startswith("http://") or line.startswith("https://")):
                urls.add(line)

        return urls


class SitemapIntegration:
    """Integrate sitemap discovery with Scrapy spider."""

    def __init__(self, spider):
        """Bind a spider instance and prepare the parser helper."""
        self.spider = spider
        self.parser = SitemapParser(spider.start_urls[0] if spider.start_urls else "")

    async def discover_sitemap_urls(self) -> list[str]:
        """Return sitemap-derived URLs for the given spider."""
        try:
            urls = await self.parser.discover_all_urls()
            logger.info(f"Sitemap discovery: found {len(urls)} URLs")
            return urls
        except Exception as e:
            logger.error(f"Sitemap discovery failed: {e}")
            return []

    def generate_scrapy_requests(self, urls: list[str]) -> Iterator:
        """Yield Scrapy requests for each sitemap URL."""
        import scrapy

        for url in urls:
            yield scrapy.Request(
                url,
                callback=self.spider.parse,
                errback=self.spider.handle_error,
                meta={"depth": 0, "source": "sitemap"},
                priority=5,  # Give sitemap URLs higher priority
                dont_filter=True,
            )


# Synchronous wrapper for use in Scrapy
def discover_sitemaps_sync(base_url: str, timeout: int = 30) -> list[str]:
    """Run sitemap discovery in a temporary event loop."""
    import asyncio

    parser = SitemapParser(base_url, timeout=timeout)

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        urls = loop.run_until_complete(parser.discover_all_urls())
        loop.close()
        return urls
    except Exception as e:
        logger.error(f"Sitemap discovery failed: {e}")
        return []
