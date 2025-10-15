"""Render JavaScript-heavy pages queued by the crawler."""

import hashlib
import logging
from collections.abc import AsyncGenerator, Iterator
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import scrapy
from scrapy.http import Response

from src.common.config import Config
from src.common.delta_lake import get_delta_manager
from src.common.spider_config import get_spider_settings
from src.stage1.base_spider import BaseSpider

logger = logging.getLogger(__name__)


class JavaScriptSpider(scrapy.Spider):
    """Render queued pages with Playwright before handing data downstream."""

    name = "javascript"

    # Custom settings tuned for Playwright rendering
    custom_settings = {
        **get_spider_settings("deep_dive"),
        # Enable scrapy-playwright
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        # Playwright-specific settings
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
        "PLAYWRIGHT_LAUNCH_OPTIONS": {
            "headless": True,
            "timeout": 30000,
        },
        # Lower concurrency for headless browsers
        "CONCURRENT_REQUESTS": 10,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 5,
        # Longer timeouts for JS rendering
        "DOWNLOAD_TIMEOUT": 60,
        # Memory limits (browsers are memory-intensive)
        "MEMUSAGE_LIMIT_MB": 8192,
        "MEMUSAGE_WARNING_MB": 6144,
    }

    # Block non-essential assets to keep render fast
    BLOCKED_RESOURCE_TYPES = ["image", "stylesheet", "font", "media"]

    def __init__(self, *args, **kwargs):
        """Initialize JavaScript spider."""
        super().__init__(*args, **kwargs)

        self.config = Config.get_instance()
        self.delta = get_delta_manager()

        # Load pending work
        self.start_urls = self._load_js_queue()

        # Tracking
        self.rendered_count = 0
        self.completed_urls = []

        logger.info(
            f"[JAVASCRIPT] Initialized with {len(self.start_urls)} pages to render"
        )

    def _load_js_queue(self) -> list[str]:
        """Return pending URLs from the js_spider_queue table."""
        try:
            all_queue_data = self.delta.read("js_spider_queue")

            pending_items = [r for r in all_queue_data if r.get("status") == "pending"]

            urls = [item["url"] for item in pending_items]
            logger.info(
                f"[JAVASCRIPT] Loaded {len(urls)} pending JS pages from js_spider_queue"
            )
            return urls

        except Exception as e:
            logger.warning(f"[JAVASCRIPT] Could not load JS queue: {e}")
            return []

    def start_requests(self) -> Iterator[scrapy.Request]:
        """Yield Playwright-enabled requests for queued URLs."""
        for url in self.start_urls:
            yield scrapy.Request(
                url,
                callback=self.parse,
                errback=self.handle_error,
                meta={
                    "playwright": True,
                    "playwright_include_page": True,
                    "playwright_page_methods": [
                        # Wait until async work drains
                        ("wait_for_load_state", "networkidle"),
                    ],
                },
            )

    async def parse(self, response: Response) -> AsyncGenerator[dict[str, Any], None]:
        """Render the page, collect links, and mark queue entries complete."""
        url = response.url

        page = response.meta.get("playwright_page")
        intercepted_urls: list[str] = []

        if page:
            await self._setup_resource_blocking(page)

            await self._simulate_scrolling(page)

            page.on(
                "response", lambda resp: self._handle_response(resp, intercepted_urls)
            )

        discovered_urls = self._extract_urls_from_rendered_html(response)

        if page and intercepted_urls:
            discovered_urls.extend(intercepted_urls)

        discovered_urls = list(set(discovered_urls))

        logger.info(f"[JAVASCRIPT] Rendered {url[:80]} -> {len(discovered_urls)} URLs")

        # Create discovery items - write to stage1_discovery for ScoutSpider to pick up
        for discovered_url in discovered_urls:
            discovered_hash = self._hash_url(discovered_url)

            yield {
                "url": discovered_url,
                "url_hash": discovered_hash,
                "depth": response.meta.get("depth", 0) + 1,
                "status_code": 200,
                "content_type": "text/html",
                "content_size": len(response.body),
                "discovered_at": datetime.now().isoformat(),
                "discovery_type": "js_rendered",
                "resource_type": "page",
                "parent_url": url,
                "discovered_via_js": True,
            }

        # Track completion for queue status update
        self.rendered_count += 1
        self.completed_urls.append(url)

        # Close page to free memory
        if page:
            await page.close()

    async def _setup_resource_blocking(self, page):
        """Block unnecessary resource types for faster rendering.

        Blocks images, CSS, fonts - reduces load time by 3-5x.
        """

        async def handle_route(route):
            if route.request.resource_type in self.BLOCKED_RESOURCE_TYPES:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", handle_route)

    async def _simulate_scrolling(self, page):
        """Scroll a few times to nudge lazy loaders."""
        try:
            previous_height = await page.evaluate("document.body.scrollHeight")

            max_scrolls = 5
            scroll_count = 0

            while scroll_count < max_scrolls:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(500)

                new_height = await page.evaluate("document.body.scrollHeight")
                if new_height == previous_height:
                    break

                previous_height = new_height
                scroll_count += 1

            logger.debug(
                f"[JAVASCRIPT] Scrolling complete after {scroll_count} scrolls"
            )

        except Exception as e:
            logger.debug(f"[JAVASCRIPT] Scrolling failed: {e}")

    def _handle_response(self, response, intercepted_urls: list):
        """Capture JSON endpoints exposed during rendering."""
        try:
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                intercepted_urls.append(response.url)
                logger.debug(f"[JAVASCRIPT] Captured API URL: {response.url[:80]}")
        except Exception as e:
            logger.debug(f"[JAVASCRIPT] Response handler error: {e}")

    def _extract_urls_from_rendered_html(self, response: Response) -> list[str]:
        """Collect links, script sources, and images from the rendered DOM."""
        urls = []

        for link in response.css("a::attr(href), link::attr(href)").getall():
            absolute_url = urljoin(response.url, link)
            if absolute_url.startswith("http"):
                urls.append(absolute_url)

        for script in response.css("script::attr(src)").getall():
            absolute_url = urljoin(response.url, script)
            if absolute_url.startswith("http"):
                urls.append(absolute_url)

        for img in response.css("img::attr(src)").getall():
            absolute_url = urljoin(response.url, img)
            if absolute_url.startswith("http"):
                urls.append(absolute_url)

        return [BaseSpider.normalize_url(url) for url in urls]

    def _hash_url(self, url: str) -> str:
        """Hash URL using SHA256 for consistency with base_spider."""
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def handle_error(self, failure):
        """Handle rendering errors."""
        logger.error(
            f"[JAVASCRIPT] Rendering failed: {failure.getErrorMessage()} for {failure.request.url[:80]}"
        )

    def closed(self, reason):
        """Persist completion markers for the processed queue items."""
        logger.info(f"[JAVASCRIPT] Spider closing: {reason}")
        logger.info(f"[JAVASCRIPT] Rendered {self.rendered_count} pages successfully")

        # Update queue status for completed URLs
        if self.completed_urls:
            try:
                # Read all queue data
                all_queue_data = self.delta.read("js_spider_queue")

                # Create set of completed URLs for faster lookup
                completed_set = set(self.completed_urls)

                # Update status for completed items
                for record in all_queue_data:
                    if record.get("url") in completed_set:
                        record["status"] = "completed"
                        record["completed_at"] = datetime.now().isoformat()

                # Write back to Delta Lake
                self.delta.write(
                    "js_spider_queue",
                    all_queue_data,
                    mode="overwrite",
                    async_write=False,
                )
                logger.info(
                    f"[JAVASCRIPT] Marked {len(self.completed_urls)} items as completed in queue"
                )

            except Exception as e:
                logger.error(f"[JAVASCRIPT] Failed to update queue status: {e}")
