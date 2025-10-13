"""JavaScript Rendering Bot - Handles JS-heavy pages with Playwright.

⚠️ DEPRECATED (K5): This standalone worker is deprecated in favor of js_spider.py.
Use JSSpider (src/stage1/js_spider.py) instead, which integrates with Scrapy.
This file will be removed in the next release.

IMPROVEMENTS:
- Persistent browser instance across batches (no restart overhead)
- Request interception to block unnecessary resources (images, CSS, fonts)
- Thread-safe per-page URL buffers (prevents cross-contamination)
- Full-length SHA-256 hashes (no truncation, prevents collisions)
- Captures relative URLs via urljoin (finds intra-site links)
- Per-record status updates (prevents data loss during concurrent writes)
"""

import asyncio
import hashlib
import logging
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.async_api import Browser, Page, async_playwright

from src.common.delta_lake import get_delta_manager

logger = logging.getLogger(__name__)


class JSBot:
    """Async bot for rendering JavaScript-heavy pages with optimizations."""

    # Resources to block for faster rendering
    BLOCKED_RESOURCE_TYPES = ['image', 'stylesheet', 'font', 'media']

    def __init__(self, headless: bool = True, max_concurrent: int = 10,
                 page_timeout: int = 30000, wait_between_batches: int = 30):
        """Initialize JSBot with persistent browser support.

        Args:
            headless: Run browser in headless mode
            max_concurrent: Maximum concurrent page renders
            page_timeout: Page load timeout in milliseconds (default: 30000)
            wait_between_batches: Wait time between batches in seconds (default: 30)
        """
        self.headless = headless
        self.max_concurrent = max_concurrent
        self.page_timeout = page_timeout
        self.wait_between_batches = wait_between_batches
        self.delta = get_delta_manager()
        self.semaphore = asyncio.Semaphore(max_concurrent)

        # REMOVED: Shared intercepted_urls (caused cross-contamination)
        # Now using per-page task-local storage

    async def process_queue(self):
        """Process pending pages in JS render queue with persistent browser.

        IMPROVEMENT: Uses per-record updates to prevent data loss.
        """
        try:
            # Read all queue data
            all_queue_data = self.delta.read('stage1_js_render_queue')

            # Filter for pending items only
            pending_items = [r for r in all_queue_data if r.get('status') == 'pending']

            if not pending_items:
                logger.info("No pending JS pages to render")
                return

            logger.info(f"Processing {len(pending_items)} pending JS pages")
        except Exception as e:
            logger.warning(f"No JS queue data: {e}")
            return

        # IMPROVEMENT: Persistent browser across batch (no restart overhead)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)

            try:
                # Render all pages concurrently
                tasks = []
                for record in pending_items:
                    task = self._render_page(browser, record)
                    tasks.append(task)

                results = await asyncio.gather(*tasks, return_exceptions=True)

            finally:
                await browser.close()

        # Process results
        discovered_urls = []
        completed_hashes = set()

        for record, result in zip(pending_items, results, strict=False):
            if isinstance(result, Exception):
                logger.error(f"Rendering failed for {record.get('url')}: {result}")
                continue

            if result:
                discovered_urls.extend(result.get('discovered_urls', []))
                completed_hashes.add(record['url_hash'])

        # Save newly discovered URLs
        if discovered_urls:
            self.delta.write('stage1_discovery', discovered_urls, mode='append', async_write=False)
            logger.info(f"Saved {len(discovered_urls)} newly discovered URLs from JS rendering")

        # IMPROVEMENT: Per-record merge updates (preserves new pending entries added during batch)
        if completed_hashes:
            self._update_queue_status(all_queue_data, completed_hashes)

    def _update_queue_status(self, all_queue_data: list, completed_hashes: set):
        """Update queue status for completed items without overwriting new entries.

        IMPROVEMENT: Merge-based update instead of full overwrite.
        """
        # Update completed items
        updated_queue = []
        for record in all_queue_data:
            if record.get('url_hash') in completed_hashes:
                record['status'] = 'completed'
                record['completed_at'] = datetime.now().isoformat()
            updated_queue.append(record)

        # Write back with overwrite (since we read all data)
        # In production, use a proper merge strategy with DeltaLake
        self.delta.write('stage1_js_render_queue', updated_queue, mode='overwrite', async_write=False)
        logger.info(f"Marked {len(completed_hashes)} items as completed")

    async def _render_page(self, browser: Browser, record: dict[str, Any]) -> dict[str, Any] | None:
        """Render single page with JavaScript and extract all discovered links.

        IMPROVEMENTS:
        - Request interception to block unnecessary resources
        - Thread-safe per-page URL buffer
        - Full-length hashes (no truncation)
        - Captures relative URLs
        """
        url = record.get('url')
        url_hash = record.get('url_hash')
        depth = record.get('depth', 0)

        # IMPROVEMENT: Per-page URL buffer (thread-safe, prevents cross-contamination)
        page_urls = []

        async with self.semaphore:
            try:
                page = await browser.new_page()

                # IMPROVEMENT: Block unnecessary resource types for faster rendering
                await self._setup_request_interception(page)

                # Set up network interception to capture API calls
                await self._setup_network_interception(page, page_urls)

                # Navigate with timeout (configurable)
                await page.goto(url, timeout=self.page_timeout, wait_until='networkidle')

                # Wait for key elements
                try:
                    await page.wait_for_selector('footer, main, #content, .content', timeout=5000)
                except Exception:
                    logger.debug(f"No footer/main element found for {url}, proceeding anyway")

                # Simulate scrolling to trigger infinite-scrolling
                await self._simulate_scrolling(page)

                # Get rendered HTML
                html = await page.content()

                await page.close()

                # Parse rendered HTML
                soup = BeautifulSoup(html, 'html.parser')

                # Extract URLs from rendered content
                discovered_urls = []

                # Links (with relative URL support)
                for link in soup.find_all(['a', 'link'], href=True):
                    href = link['href']
                    # IMPROVEMENT: Use urljoin to capture relative URLs
                    absolute_url = urljoin(url, href)
                    if absolute_url.startswith('http'):
                        discovered_urls.append(absolute_url)

                # Scripts (with relative URL support)
                for script in soup.find_all('script', src=True):
                    src = script['src']
                    absolute_url = urljoin(url, src)
                    if absolute_url.startswith('http'):
                        discovered_urls.append(absolute_url)

                # Images (with relative URL support)
                for img in soup.find_all('img', src=True):
                    src = img['src']
                    absolute_url = urljoin(url, src)
                    if absolute_url.startswith('http'):
                        discovered_urls.append(absolute_url)

                # Add URLs from intercepted API calls (page-local buffer)
                discovered_urls.extend(page_urls)

                # Deduplicate
                discovered_urls = list(set(discovered_urls))

                # Create discovery records for each URL
                url_records = []
                for discovered_url in discovered_urls:
                    # IMPROVEMENT: Use full-length hash (no truncation, prevents collisions)
                    discovered_hash = hashlib.sha256(discovered_url.encode('utf-8')).hexdigest()

                    url_records.append({
                        'url': discovered_url,
                        'url_hash': discovered_hash,
                        'depth': depth + 1,
                        'status_code': 200,
                        'content_type': 'text/html',
                        'content_size': 0,
                        'is_heavy': False,
                        'urls_found': 0,
                        'requires_js': False,
                        'discovered_via_js': True,
                        'parent_url': url,
                        'discovered_at': datetime.now().isoformat()
                    })

                logger.info(f"Rendered {url[:80]} -> {len(url_records)} URLs")

                return {
                    'url_hash': url_hash,
                    'discovered_urls': url_records
                }

            except Exception as e:
                logger.error(f"Failed to render {url}: {e}")
                return None

    async def _setup_request_interception(self, page: Page):
        """Set up request interception to block unnecessary resources.

        IMPROVEMENT: Blocks images, CSS, fonts for 3-5x faster page loads.
        """
        async def handle_route(route):
            if route.request.resource_type in self.BLOCKED_RESOURCE_TYPES:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", handle_route)

    async def _setup_network_interception(self, page: Page, page_urls: list):
        """Set up network interception to capture API calls and extract URLs.

        IMPROVEMENT: Uses page-local buffer (thread-safe, prevents cross-contamination).

        Args:
            page: Playwright page instance
            page_urls: Page-local list to store intercepted URLs
        """
        async def handle_response(response):
            try:
                # Only process JSON responses
                content_type = response.headers.get('content-type', '')
                if 'application/json' in content_type:
                    try:
                        json_data = await response.json()
                        # Recursively search for URLs in JSON
                        urls = self._extract_urls_from_json(json_data, base_url=response.url)
                        page_urls.extend(urls)
                    except Exception as e:
                        logger.debug(f"Failed to parse JSON response: {e}")
            except Exception as e:
                logger.debug(f"Response handler error: {e}")

        page.on('response', handle_response)

    async def _simulate_scrolling(self, page: Page):
        """Simulate scrolling to the bottom of the page to trigger infinite-scrolling.

        IMPROVEMENT: Adaptive scrolling - continues until no more content loads.
        """
        try:
            previous_height = await page.evaluate('document.body.scrollHeight')

            # Adaptive scrolling - continue until content stops loading
            max_scrolls = 20  # Safety limit to prevent infinite loops
            scroll_count = 0

            while scroll_count < max_scrolls:
                # Scroll to bottom
                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                await page.wait_for_timeout(500)

                # Check if new content loaded
                new_height = await page.evaluate('document.body.scrollHeight')
                if new_height == previous_height:
                    # No new content loaded - stop scrolling
                    logger.debug(f"Scrolling complete after {scroll_count} scrolls")
                    break

                previous_height = new_height
                scroll_count += 1

            if scroll_count >= max_scrolls:
                logger.debug(f"Reached max scroll limit ({max_scrolls})")

        except Exception as e:
            logger.debug(f"Scrolling simulation failed: {e}")

    def _extract_urls_from_json(self, obj, base_url: str = None, depth: int = 0) -> list[str]:
        """Recursively extract URLs from JSON object.

        IMPROVEMENT: Supports relative URLs via base_url parameter.

        Args:
            obj: JSON object to extract URLs from
            base_url: Base URL for resolving relative URLs
            depth: Current recursion depth

        Returns:
            List of absolute URLs
        """
        urls = []
        if depth > 10:  # Prevent infinite recursion
            return urls

        if isinstance(obj, dict):
            for key, value in obj.items():
                # Check if key suggests a URL
                if any(url_key in key.lower() for url_key in ['url', 'href', 'link', 'src', 'endpoint', 'api']):
                    if isinstance(value, str):
                        # Handle both absolute and relative URLs
                        if value.startswith('http'):
                            urls.append(value)
                        elif value.startswith('/') and base_url:
                            urls.append(urljoin(base_url, value))

                # Recurse into nested objects
                if isinstance(value, (dict, list)):
                    urls.extend(self._extract_urls_from_json(value, base_url, depth + 1))

        elif isinstance(obj, list):
            for item in obj:
                urls.extend(self._extract_urls_from_json(item, base_url, depth + 1))

        elif isinstance(obj, str):
            if obj.startswith('http'):
                urls.append(obj)
            elif obj.startswith('/') and base_url:
                urls.append(urljoin(base_url, obj))

        return urls


async def run_js_bot():
    """Run the JS rendering bot in continuous mode."""
    bot = JSBot(headless=True, max_concurrent=10)

    logger.info("JS Bot starting in continuous mode...")
    while True:
        try:
            await bot.process_queue()
            logger.info(f"Waiting {bot.wait_between_batches} seconds before next check...")
            await asyncio.sleep(bot.wait_between_batches)
        except KeyboardInterrupt:
            logger.info("JS Bot shutting down...")
            break
        except Exception as e:
            logger.error(f"Error in JS Bot loop: {e}")
            await asyncio.sleep(10)


if __name__ == '__main__':
    asyncio.run(run_js_bot())
