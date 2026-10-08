"""Enhanced JavaScript spider with priority queue and aggressive async processing."""

import logging
import os
from collections.abc import AsyncGenerator, Iterator
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import scrapy
from scrapy.http import Response

from src.core.config import get_config
from src.stage1.experimental.playwright_guard import PageLedger
from src.stage1.experimental.gate import ExperimentalSpiderMixin
from src.utils.url_canon import url_hash
from src.stage1.processors.js_priority_queue import JSPriorityQueue
from src.stage1.processors.url_processor import URLProcessor
from src.stage1.middlewares.spider_config import get_spider_settings
from src.utils.delta import get_delta
from src.stage1.experimental.playwright_blocking import DEFAULT_BLOCKED_TYPES
from src.stage1.experimental.playwright_blocking import get_policy as get_blocking_policy
from src.lakehouse import SeedManager

logger = logging.getLogger(__name__)


class JavaScriptSpider(ExperimentalSpiderMixin, scrapy.Spider):

    name = "javascript"

    custom_settings = {
        **get_spider_settings("deep_dive"),
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
        # #390: configurable blocking, applied from the first subresource (not only after load).
        "PLAYWRIGHT_ABORT_REQUEST": "src.stage1.experimental.playwright_blocking.should_abort_request",
        "PLAYWRIGHT_LAUNCH_OPTIONS": {
            "headless": True,
            "timeout": 30000,
        },
        # #452: hard caps enforced by scrapy-playwright (semaphores). When
        # saturated, new renders wait instead of spawning more Chromium state.
        "PLAYWRIGHT_MAX_CONTEXTS": int(os.getenv("PLAYWRIGHT_MAX_CONTEXTS", "4")),
        "PLAYWRIGHT_MAX_PAGES_PER_CONTEXT": int(os.getenv("PLAYWRIGHT_MAX_PAGES_PER_CONTEXT", "8")),
        "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": 30000,
        "CONCURRENT_REQUESTS": 20,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 10,
        "DOWNLOAD_TIMEOUT": 60,
        "MEMUSAGE_LIMIT_MB": 12288,
        "MEMUSAGE_WARNING_MB": 10240,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 0.5,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 15.0,
    }

    # Default only; the live list is stage1.js_blocked_resource_types (+ per-domain overrides, #390).
    BLOCKED_RESOURCE_TYPES = list(DEFAULT_BLOCKED_TYPES)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.config = get_config()
        self.delta = get_delta()

        self.seed_manager = SeedManager(self.delta)

        from src.utils.redis import get_redis

        redis_client = get_redis().client

        self.priority_queue = JSPriorityQueue(redis_client, queue_key="js_spider:priority_queue")

        self.start_urls = self._load_js_queue()

        self.rendered_count = 0
        self.page_ledger = PageLedger()
        self.completed_urls = []
        self.priority_stats = {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
        }

        queue_stats = self.priority_queue.get_stats()
        logger.info(
            f"[JS_SPIDER] Initialized | "
            f"Delta queue: {len(self.start_urls)} | "
            f"Priority queue: {queue_stats.get('total_size', 0)} | "
            f"Total pages to render: {len(self.start_urls) + queue_stats.get('total_size', 0)}"
        )

    def _load_js_queue(self) -> list[str]:
        try:
            all_queue_data = self.delta.read("js_spider_queue")

            pending_items = [r for r in all_queue_data if r.get("status") == "pending"]

            urls = [item["url"] for item in pending_items]
            logger.info(f"[JAVASCRIPT] Loaded {len(urls)} pending JS pages from js_spider_queue")
            return urls

        except Exception as e:
            logger.warning(f"[JAVASCRIPT] Could not load JS queue: {e}")
            return []

    def start_requests(self) -> Iterator[scrapy.Request]:
        logger.info("[JS_SPIDER] Processing priority queue...")

        batch_size = 50
        while True:
            url_batch = self.priority_queue.dequeue(count=batch_size)
            if not url_batch:
                break

            for url_data in url_batch:
                url = url_data["url"]
                metadata = url_data.get("metadata", {})
                priority = metadata.get("priority", 0)

                if priority >= 100:
                    self.priority_stats["critical"] += 1
                elif priority >= 50:
                    self.priority_stats["high"] += 1
                elif priority >= 25:
                    self.priority_stats["medium"] += 1
                else:
                    self.priority_stats["low"] += 1

                yield scrapy.Request(
                    url,
                    callback=self.parse,
                    errback=self.handle_error,
                    meta={
                        "playwright": True,
                        "playwright_include_page": True,
                        "playwright_page_methods": [
                            ("wait_for_load_state", "networkidle"),
                        ],
                        "priority": priority,
                        "metadata": metadata,
                    },
                    priority=priority,
                )

        logger.info(
            f"[JS_SPIDER] Priority queue processed | "
            f"Critical: {self.priority_stats['critical']} | "
            f"High: {self.priority_stats['high']} | "
            f"Medium: {self.priority_stats['medium']} | "
            f"Low: {self.priority_stats['low']}"
        )

        logger.info("[JS_SPIDER] Processing Delta Lake queue...")
        for url in self.start_urls:
            yield scrapy.Request(
                url,
                callback=self.parse,
                errback=self.handle_error,
                meta={
                    "playwright": True,
                    "playwright_include_page": True,
                    "playwright_page_methods": [
                        ("wait_for_load_state", "networkidle"),
                    ],
                },
            )

    def _ledger(self) -> PageLedger:
        if getattr(self, "page_ledger", None) is None:
            self.page_ledger = PageLedger()
        return self.page_ledger

    async def parse(self, response: Response) -> AsyncGenerator[dict[str, Any], None]:
        url = response.url

        page = response.meta.get("playwright_page")
        ledger = self._ledger()
        ledger.acquired(page)
        intercepted_urls: list[str] = []

        # #452: all page work happens inside try/finally and the page is closed
        # *before* the first yield, so neither an exception mid-render nor an
        # abandoned generator can leak it.
        try:
            if page:
                # Listen before scrolling so XHRs triggered by scrolling are captured.
                page.on("response", lambda resp: self._handle_response(resp, intercepted_urls))

                await self._setup_resource_blocking(page)

                await self._simulate_scrolling(page)

            discovered_urls = self._extract_urls_from_rendered_html(response)

            if page and intercepted_urls:
                discovered_urls.extend(intercepted_urls)
        finally:
            await ledger.release(page, "parse")

        discovered_urls = list(set(discovered_urls))

        logger.info(f"[JAVASCRIPT] Rendered {url[:80]} -> {len(discovered_urls)} URLs")

        if discovered_urls:
            self._add_urls_to_seeds(discovered_urls, url)

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

        self.rendered_count += 1
        self.completed_urls.append(url)

    async def _setup_resource_blocking(self, page):
        policy = get_blocking_policy()

        async def handle_route(route):
            if policy.should_block(route.request.resource_type, page.url):
                await route.abort()
            else:
                # fallback, not continue_: let scrapy-playwright's own route handler run too.
                await route.fallback()

        await page.route("**/*", handle_route)

    async def _simulate_scrolling(self, page):
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

            logger.debug(f"[JAVASCRIPT] Scrolling complete after {scroll_count} scrolls")

        except Exception as e:
            logger.debug(f"[JAVASCRIPT] Scrolling failed: {e}")

    def _handle_response(self, response, intercepted_urls: list):
        try:
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                intercepted_urls.append(response.url)
                logger.debug(f"[JAVASCRIPT] Captured API URL: {response.url[:80]}")
        except Exception as e:
            logger.debug(f"[JAVASCRIPT] Response handler error: {e}")

    def _extract_urls_from_rendered_html(self, response: Response) -> list[str]:
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

        # Was `BaseSpider.normalize_url(url)`: an unbound method call on a
        # class this spider does not inherit from (TypeError: missing `url`).
        return [self._normalize_url(url) for url in urls]

    def _normalize_url(self, url: str) -> str:
        processor = getattr(self, "_url_processor", None)
        if processor is None:
            processor = URLProcessor(
                base_url="",
                allowed_domains=list(getattr(self, "allowed_domains", None) or []),
                use_historical_data=False,
            )
            self._url_processor = processor
        return processor.normalize_url(url) or url

    def _hash_url(self, url: str) -> str:
        # Same canonical sha256[:16] as scout/Stage 2 (#728); was a full raw-URL digest.
        return url_hash(url)

    def _add_urls_to_seeds(self, urls: list[str], source_url: str) -> None:
        if not urls:
            return

        try:
            result = self.seed_manager.add_urls_to_seeds(
                urls=urls,
                source_url=source_url,
                source_spider=self.name,
                enqueue_stage2=False,
            )

            logger.info(
                f"[JS_SPIDER] SeedManager results: seeds={result['seed_inserted']}, domain={result.get('domain_inserted', result.get('uconn_inserted', 0))}"
            )

        except Exception as e:
            logger.error(f"[JS_SPIDER] Failed to add URLs via SeedManager: {e}", exc_info=True)

    async def handle_error(self, failure):
        logger.error(f"[JAVASCRIPT] Rendering failed: {failure.getErrorMessage()} for {failure.request.url[:80]}")
        # #452: a failed request may still carry an open page.
        await self._ledger().release_from_failure(failure)

    def closed(self, reason):
        logger.info(f"[JAVASCRIPT] Spider closing: {reason}")
        logger.info(f"[JAVASCRIPT] Rendered {self.rendered_count} pages successfully")
        leaked = self._ledger().open_count
        if leaked:
            logger.warning(f"[JAVASCRIPT] {leaked} Playwright pages still open at close (leak)")

        if self.completed_urls:
            try:
                all_queue_data = self.delta.read("js_spider_queue")

                completed_set = set(self.completed_urls)

                for record in all_queue_data:
                    if record.get("url") in completed_set:
                        record["status"] = "completed"
                        record["completed_at"] = datetime.now().isoformat()

                self.delta.write(
                    "js_spider_queue",
                    all_queue_data,
                    mode="overwrite",
                    async_write=False,
                )
                logger.info(f"[JAVASCRIPT] Marked {len(self.completed_urls)} items as completed in queue")

            except Exception as e:
                logger.error(f"[JAVASCRIPT] Failed to update queue status: {e}")
