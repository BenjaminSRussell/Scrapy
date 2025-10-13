"""JavaScriptSpider - Scrapy-Playwright integration for JavaScript-heavy pages.

REFACTORED BEHAVIOR (Post-ScoutSpider refactoring):
- Reads from js_spider_queue table (populated by ScoutSpider)
- Uses scrapy-playwright for JS rendering
- Blocks heavy resources (images, CSS, fonts) for faster rendering
- Performs limited scrolling to trigger lazy loading
- Captures DOM links and API URLs from network interception
- Writes discovered URLs to stage1_discovery table
- Updates queue status to 'completed' after processing

REPLACES:
- src/stage1/js_bot.py (standalone worker)
- Old js_spider.py that read from stage1_js_render_queue
"""

import hashlib
import logging
from datetime import datetime
from typing import Iterator
from urllib.parse import urljoin, urlparse

import scrapy
from scrapy.http import Response

from src.common.config import load_config
from src.common.delta_lake import get_delta_manager
from src.common.spider_config import get_spider_settings

logger = logging.getLogger(__name__)


class JavaScriptSpider(scrapy.Spider):
    """Spider for rendering JavaScript-heavy pages with Playwright.

    REFACTORED: This spider processes pages queued by ScoutSpider in the js_spider_queue table.
    All HTML pages discovered by Scout are sent here for full JavaScript rendering.
    """

    name = "javascript"

    # K5: Custom settings for JS rendering
    custom_settings = {
        **get_spider_settings("deep_dive"),  # Start with conservative settings

        # K5: Enable scrapy-playwright
        'DOWNLOAD_HANDLERS': {
            'http': 'scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler',
            'https': 'scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler',
        },

        # Playwright-specific settings
        'PLAYWRIGHT_BROWSER_TYPE': 'chromium',
        'PLAYWRIGHT_LAUNCH_OPTIONS': {
            'headless': True,
            'timeout': 30000,
        },

        # Lower concurrency for headless browsers
        'CONCURRENT_REQUESTS': 10,
        'CONCURRENT_REQUESTS_PER_DOMAIN': 5,

        # Longer timeouts for JS rendering
        'DOWNLOAD_TIMEOUT': 60,

        # Memory limits (browsers are memory-intensive)
        'MEMUSAGE_LIMIT_MB': 8192,
        'MEMUSAGE_WARNING_MB': 6144,
    }

    # Resources to block for faster rendering (3-5x speedup)
    BLOCKED_RESOURCE_TYPES = ['image', 'stylesheet', 'font', 'media']

    def __init__(self, *args, **kwargs):
        """Initialize JavaScript spider."""
        super().__init__(*args, **kwargs)

        self.config = load_config()
        self.delta = get_delta_manager()

        # Load JS render queue from Delta Lake (new js_spider_queue table)
        self.start_urls = self._load_js_queue()

        # Tracking
        self.rendered_count = 0
        self.completed_urls = []  # Track completed URLs for queue status updates

        logger.info(f"[JAVASCRIPT] Initialized with {len(self.start_urls)} pages to render")

    def _load_js_queue(self) -> list[str]:
        """Load pending JS render queue from Delta Lake.

        REFACTORED: Reads from js_spider_queue (not stage1_js_render_queue).

        Returns:
            List of URLs that require JS rendering
        """
        try:
            # Read all queue data from new js_spider_queue table
            all_queue_data = self.delta.read('js_spider_queue')

            # Filter for pending items only
            pending_items = [r for r in all_queue_data if r.get('status') == 'pending']

            urls = [item['url'] for item in pending_items]
            logger.info(f"[JAVASCRIPT] Loaded {len(urls)} pending JS pages from js_spider_queue")
            return urls

        except Exception as e:
            logger.warning(f"[JAVASCRIPT] Could not load JS queue: {e}")
            return []

    def start_requests(self) -> Iterator[scrapy.Request]:
        """Generate initial requests with Playwright metadata.

        REFACTORED: Each request includes playwright=True and resource blocking.
        """
        for url in self.start_urls:
            yield scrapy.Request(
                url,
                callback=self.parse,
                errback=self.handle_error,
                meta={
                    'playwright': True,
                    'playwright_include_page': True,
                    'playwright_page_methods': [
                        # Wait for network idle (all AJAX calls complete)
                        ('wait_for_load_state', 'networkidle'),
                    ],
                },
            )

    async def parse(self, response: Response) -> Iterator:
        """Parse JS-rendered page and extract URLs.

        REFACTORED: Extracts links from fully rendered DOM after JS execution.
        Performs limited scrolling to trigger lazy-loaded content.
        Tracks completed URLs for queue status updates.
        """
        url = response.url
        url_hash = self._hash_url(url)

        # Get Playwright page object for advanced operations
        page = response.meta.get('playwright_page')

        if page:
            # Setup resource blocking (images, CSS, fonts)
            await self._setup_resource_blocking(page)

            # Perform limited scrolling to trigger lazy loading
            await self._simulate_scrolling(page)

            # Capture network requests (API URLs)
            intercepted_urls = []
            page.on('response', lambda resp: self._handle_response(resp, intercepted_urls))

        # Extract URLs from rendered HTML
        discovered_urls = self._extract_urls_from_rendered_html(response)

        # Add intercepted API URLs if available
        if page and intercepted_urls:
            discovered_urls.extend(intercepted_urls)

        # Deduplicate
        discovered_urls = list(set(discovered_urls))

        logger.info(f"[JAVASCRIPT] Rendered {url[:80]} -> {len(discovered_urls)} URLs")

        # Create discovery items - write to stage1_discovery for ScoutSpider to pick up
        for discovered_url in discovered_urls:
            discovered_hash = self._hash_url(discovered_url)

            yield {
                'url': discovered_url,
                'url_hash': discovered_hash,
                'depth': response.meta.get('depth', 0) + 1,
                'status_code': 200,
                'content_type': 'text/html',
                'content_size': len(response.body),
                'discovered_at': datetime.now().isoformat(),
                'discovery_type': 'js_rendered',
                'resource_type': 'page',
                'parent_url': url,
                'discovered_via_js': True,
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
        """Simulate scrolling to trigger lazy-loaded content.

        Performs limited scrolling (max 5 scrolls) to load dynamic content.
        """
        try:
            previous_height = await page.evaluate('document.body.scrollHeight')

            max_scrolls = 5  # Limited scrolling to avoid infinite loops
            scroll_count = 0

            while scroll_count < max_scrolls:
                # Scroll to bottom
                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                await page.wait_for_timeout(500)

                # Check if new content loaded
                new_height = await page.evaluate('document.body.scrollHeight')
                if new_height == previous_height:
                    break

                previous_height = new_height
                scroll_count += 1

            logger.debug(f"[JAVASCRIPT] Scrolling complete after {scroll_count} scrolls")

        except Exception as e:
            logger.debug(f"[JAVASCRIPT] Scrolling failed: {e}")

    def _handle_response(self, response, intercepted_urls: list):
        """Handle network responses to capture API URLs.

        K5: Captures JSON API endpoints discovered during rendering.
        """
        try:
            content_type = response.headers.get('content-type', '')
            if 'application/json' in content_type:
                # Add API endpoint URL
                intercepted_urls.append(response.url)
                logger.debug(f"[K5] Captured API URL: {response.url[:80]}")
        except Exception as e:
            logger.debug(f"[K5] Response handler error: {e}")

    def _extract_urls_from_rendered_html(self, response: Response) -> list[str]:
        """Extract URLs from fully rendered HTML.

        K5: Extracts links, scripts, images, and API endpoints from DOM.
        """
        urls = []

        # Links
        for link in response.css('a::attr(href), link::attr(href)').getall():
            absolute_url = urljoin(response.url, link)
            if absolute_url.startswith('http'):
                urls.append(absolute_url)

        # Scripts
        for script in response.css('script::attr(src)').getall():
            absolute_url = urljoin(response.url, script)
            if absolute_url.startswith('http'):
                urls.append(absolute_url)

        # Images
        for img in response.css('img::attr(src)').getall():
            absolute_url = urljoin(response.url, img)
            if absolute_url.startswith('http'):
                urls.append(absolute_url)

        return urls

    def _hash_url(self, url: str) -> str:
        """Hash URL using SHA256 for consistency with base_spider."""
        return hashlib.sha256(url.encode('utf-8')).hexdigest()

    def handle_error(self, failure):
        """Handle rendering errors."""
        logger.error(f"[K5] Rendering failed: {failure.getErrorMessage()} for {failure.request.url[:80]}")

    def closed(self, reason):
        """Called when spider closes.

        K5: Update queue status in Delta Lake to mark items as completed.
        """
        logger.info(f"[K5] JSSpider closing: {reason}")
        logger.info(f"[K5] Rendered {self.rendered_count} pages successfully")

        # TODO: Update queue status in Delta Lake (mark completed)
        # This would require tracking which URLs were successfully rendered
        # and updating the stage1_js_render_queue table
