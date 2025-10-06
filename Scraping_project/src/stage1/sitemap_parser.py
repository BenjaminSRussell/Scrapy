"""Advanced Sitemap Parser - Recursively discovers nested sitemaps."""

import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)


class SitemapParser:
    """Intelligent sitemap parser with recursive discovery."""

    # XML namespaces commonly used in sitemaps
    NAMESPACES = {
        'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9',
        'news': 'http://www.google.com/schemas/sitemap-news/0.9',
        'image': 'http://www.google.com/schemas/sitemap-image/1.1',
        'video': 'http://www.google.com/schemas/sitemap-video/1.1',
    }

    def __init__(self, base_url: str, timeout: int = 30, max_depth: int = 5):
        """Initialize sitemap parser.

        Args:
            base_url: Base URL of the site
            timeout: HTTP request timeout
            max_depth: Maximum recursion depth for nested sitemaps
        """
        self.base_url = base_url
        self.timeout = timeout
        self.max_depth = max_depth
        self.visited_sitemaps: set[str] = set()
        self.discovered_urls: set[str] = set()

    async def discover_all_urls(self) -> list[str]:
        """Discover all URLs from sitemaps (including nested).

        Returns:
            List of discovered URLs
        """
        parsed = urlparse(self.base_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        # Try common sitemap locations
        sitemap_urls = [
            urljoin(base, '/sitemap.xml'),
            urljoin(base, '/sitemap_index.xml'),
            urljoin(base, '/sitemap-index.xml'),
            urljoin(base, '/sitemaps/sitemap.xml'),
            urljoin(base, '/sitemap/sitemap.xml'),
        ]

        # Also check robots.txt for sitemap references
        robots_sitemaps = await self._get_sitemaps_from_robots(base)
        sitemap_urls.extend(robots_sitemaps)

        # Process all sitemaps
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for sitemap_url in sitemap_urls:
                await self._parse_sitemap_recursive(
                    client, sitemap_url, depth=0
                )

        return list(self.discovered_urls)

    async def _get_sitemaps_from_robots(self, base_url: str) -> list[str]:
        """Extract sitemap URLs from robots.txt.

        Args:
            base_url: Base URL of site

        Returns:
            List of sitemap URLs
        """
        robots_url = urljoin(base_url, '/robots.txt')
        sitemaps = []

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(robots_url)
                if response.status_code == 200:
                    for line in response.text.split('\n'):
                        line = line.strip()
                        if line.lower().startswith('sitemap:'):
                            sitemap_url = line.split(':', 1)[1].strip()
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
        """Recursively parse sitemap and nested sitemaps.

        Args:
            client: HTTP client
            sitemap_url: URL of sitemap to parse
            depth: Current recursion depth
        """
        # Check depth limit
        if depth > self.max_depth:
            logger.warning(f"Max sitemap depth reached: {sitemap_url}")
            return

        # Check if already visited
        if sitemap_url in self.visited_sitemaps:
            return

        self.visited_sitemaps.add(sitemap_url)

        try:
            logger.info(f"Parsing sitemap (depth={depth}): {sitemap_url}")
            response = await client.get(sitemap_url)

            if response.status_code != 200:
                logger.warning(f"Sitemap returned {response.status_code}: {sitemap_url}")
                return

            # Parse XML
            root = ET.fromstring(response.content)

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
            logger.warning(f"Failed to parse sitemap XML: {sitemap_url} - {e}")
        except Exception as e:
            logger.warning(f"Error processing sitemap: {sitemap_url} - {e}")

    def _is_sitemap_index(self, root: ET.Element) -> bool:
        """Check if XML is a sitemap index (contains nested sitemaps).

        Args:
            root: XML root element

        Returns:
            True if sitemap index
        """
        # Check for <sitemapindex> tag
        if root.tag.endswith('sitemapindex'):
            return True

        # Check for <sitemap> children (indicates index)
        for ns in ['', '{http://www.sitemaps.org/schemas/sitemap/0.9}']:
            if root.find(f'{ns}sitemap') is not None:
                return True

        return False

    def _extract_nested_sitemaps(self, root: ET.Element) -> list[str]:
        """Extract nested sitemap URLs from sitemap index.

        Args:
            root: XML root element

        Returns:
            List of nested sitemap URLs
        """
        sitemaps = []

        # Try with and without namespace
        for ns in ['', '{http://www.sitemaps.org/schemas/sitemap/0.9}']:
            for sitemap in root.findall(f'{ns}sitemap'):
                loc = sitemap.find(f'{ns}loc')
                if loc is not None and loc.text:
                    sitemap_url = loc.text.strip()
                    sitemaps.append(sitemap_url)

                    # Log lastmod if available
                    lastmod = sitemap.find(f'{ns}lastmod')
                    if lastmod is not None and lastmod.text:
                        logger.debug(f"Sitemap {sitemap_url} last modified: {lastmod.text}")

        return sitemaps

    def _extract_urls_from_sitemap(self, root: ET.Element) -> set[str]:
        """Extract URLs from regular sitemap.

        Args:
            root: XML root element

        Returns:
            Set of URLs
        """
        urls = set()

        # Try with and without namespace
        for ns in ['', '{http://www.sitemaps.org/schemas/sitemap/0.9}']:
            for url_elem in root.findall(f'{ns}url'):
                loc = url_elem.find(f'{ns}loc')
                if loc is not None and loc.text:
                    url = loc.text.strip()
                    urls.add(url)

                    # Extract metadata (optional)
                    lastmod = url_elem.find(f'{ns}lastmod')
                    priority = url_elem.find(f'{ns}priority')
                    changefreq = url_elem.find(f'{ns}changefreq')

                    if lastmod is not None or priority is not None:
                        metadata = {
                            'url': url,
                            'lastmod': lastmod.text if lastmod is not None else None,
                            'priority': float(priority.text) if priority is not None else None,
                            'changefreq': changefreq.text if changefreq is not None else None,
                        }
                        logger.debug(f"URL metadata: {metadata}")

        return urls

    def _extract_from_plain_text(self, text: str) -> set[str]:
        """Extract URLs from plain text sitemap (non-XML).

        Some sites serve plain text sitemaps with one URL per line.

        Args:
            text: Plain text content

        Returns:
            Set of URLs
        """
        urls = set()

        for line in text.split('\n'):
            line = line.strip()
            if line and (line.startswith('http://') or line.startswith('https://')):
                urls.add(line)

        return urls


class SitemapIntegration:
    """Integrate sitemap discovery with Scrapy spider."""

    def __init__(self, spider):
        """Initialize with Scrapy spider instance.

        Args:
            spider: Scrapy spider instance
        """
        self.spider = spider
        self.parser = SitemapParser(spider.start_urls[0] if spider.start_urls else "")

    async def discover_sitemap_urls(self) -> list[str]:
        """Discover all URLs from sitemaps.

        Returns:
            List of URLs to crawl
        """
        try:
            urls = await self.parser.discover_all_urls()
            logger.info(f"Sitemap discovery: found {len(urls)} URLs")
            return urls
        except Exception as e:
            logger.error(f"Sitemap discovery failed: {e}")
            return []

    def generate_scrapy_requests(self, urls: list[str]) -> Iterator:
        """Generate Scrapy requests from sitemap URLs.

        Args:
            urls: List of URLs from sitemap

        Yields:
            Scrapy Request objects
        """
        import scrapy

        for url in urls:
            yield scrapy.Request(
                url,
                callback=self.spider.parse,
                errback=self.spider.handle_error,
                meta={'depth': 0, 'source': 'sitemap'},
                priority=5,  # Give sitemap URLs higher priority
                dont_filter=True,
            )


# Synchronous wrapper for use in Scrapy
def discover_sitemaps_sync(base_url: str, timeout: int = 30) -> list[str]:
    """Synchronous wrapper for sitemap discovery.

    Args:
        base_url: Base URL of site
        timeout: HTTP timeout

    Returns:
        List of discovered URLs
    """
    import asyncio

    parser = SitemapParser(base_url, timeout=timeout)

    try:
        # Run async function in new event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        urls = loop.run_until_complete(parser.discover_all_urls())
        loop.close()
        return urls
    except Exception as e:
        logger.error(f"Sitemap discovery failed: {e}")
        return []
