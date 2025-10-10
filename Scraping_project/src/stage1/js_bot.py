"""JavaScript Rendering Bot - Handles JS-heavy pages with Playwright.
"""

import asyncio
import hashlib
import logging
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup
from playwright.async_api import Browser, async_playwright

from src.common.delta_lake import get_delta_manager

logger = logging.getLogger(__name__)


class JSBot:
    """Async bot for rendering JavaScript-heavy pages."""

    def __init__(self, headless: bool = True, max_concurrent: int = 10):
        self.headless = headless
        self.max_concurrent = max_concurrent
        self.delta = get_delta_manager()
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.intercepted_urls = []  # Store URLs from API calls

    async def process_queue(self):
        """Process pending pages in JS render queue."""
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

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)

            tasks = []
            for record in pending_items:
                task = self._render_page(browser, record)
                tasks.append(task)

            results = await asyncio.gather(*tasks, return_exceptions=True)

            await browser.close()

        # Save newly discovered URLs to main discovery table
        discovered_urls = []
        for result in results:
            if result and not isinstance(result, Exception):
                discovered_urls.extend(result.get('discovered_urls', []))

        if discovered_urls:
            self.delta.write('stage1_discovery', discovered_urls, mode='append', async_write=False)
            logger.info(f"Saved {len(discovered_urls)} newly discovered URLs from JS rendering")

        # Update queue items to 'completed' status
        completed_hashes = {r['url_hash'] for r in pending_items}
        updated_queue = []
        for record in all_queue_data:
            if record.get('url_hash') in completed_hashes:
                record['status'] = 'completed'
                record['completed_at'] = str(datetime.now())
            updated_queue.append(record)

        self.delta.write('stage1_js_render_queue', updated_queue, mode='overwrite', async_write=False)
        logger.info(f"Marked {len(completed_hashes)} items as completed")

    async def _render_page(self, browser: Browser, record: dict[str, Any]) -> dict[str, Any]:
        """Render single page with JavaScript and extract all discovered links."""
        url = record.get('url')
        url_hash = record.get('url_hash')
        depth = record.get('depth', 0)

        async with self.semaphore:
            try:
                page = await browser.new_page()

                # Enhanced: Set up network interception to capture API calls
                await self._setup_network_interception(page)

                # Navigate with timeout
                await page.goto(url, timeout=30000, wait_until='networkidle')

                # Enhanced: Use explicit wait for key element instead of fixed timeout
                try:
                    # Wait for either footer or main content to be visible
                    await page.wait_for_selector('footer, main, #content, .content', timeout=5000)
                except Exception:
                    logger.debug(f"No footer/main element found for {url}, proceeding anyway")

                # Enhanced: Simulate scrolling to trigger infinite-scrolling mechanisms
                await self._simulate_scrolling(page)

                # Get rendered HTML
                html = await page.content()

                await page.close()

                # Parse rendered HTML
                soup = BeautifulSoup(html, 'html.parser')

                # Extract URLs from rendered content - comprehensive approach
                discovered_urls = []

                # Links
                for link in soup.find_all(['a', 'link'], href=True):
                    href = link['href']
                    if href.startswith('http'):
                        discovered_urls.append(href)

                # Scripts
                for script in soup.find_all('script', src=True):
                    src = script['src']
                    if src.startswith('http'):
                        discovered_urls.append(src)

                # Images
                for img in soup.find_all('img', src=True):
                    src = img['src']
                    if src.startswith('http'):
                        discovered_urls.append(src)

                # Enhanced: Add URLs from intercepted API calls
                discovered_urls.extend(self.intercepted_urls)
                # Clear intercepted URLs for next page
                self.intercepted_urls = []

                # Deduplicate
                discovered_urls = list(set(discovered_urls))

                # Create discovery records for each URL
                url_records = []
                for discovered_url in discovered_urls:
                    discovered_hash = hashlib.sha256(discovered_url.lower().rstrip('/').encode()).hexdigest()[:16]
                    url_records.append({
                        'url': discovered_url,
                        'url_hash': discovered_hash,
                        'depth': depth + 1,
                        'status_code': 200,
                        'content_type': 'text/html',
                        'content_size': 0,
                        'is_heavy': False,
                        'discovered_count': 0,
                        'requires_js': False,
                        'discovered_via_js': True,
                        'parent_url': url,
                        'timestamp': str(datetime.now())
                    })

                logger.info(f"Rendered {url[:80]} -> {len(url_records)} URLs")

                return {
                    'url_hash': url_hash,
                    'discovered_urls': url_records
                }

            except Exception as e:
                logger.error(f"Failed to render {url}: {e}")
                return None

    async def _simulate_scrolling(self, page):
        """Simulate scrolling to the bottom of the page to trigger infinite-scrolling."""
        try:
            # Get initial height
            previous_height = await page.evaluate('document.body.scrollHeight')

            # Scroll multiple times to trigger lazy loading
            for _ in range(5):
                # Scroll to bottom
                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')

                # Wait for content to load
                await page.wait_for_timeout(500)

                # Check if new content was loaded
                new_height = await page.evaluate('document.body.scrollHeight')
                if new_height == previous_height:
                    # No new content, stop scrolling
                    break
                previous_height = new_height

        except Exception as e:
            logger.debug(f"Scrolling simulation failed: {e}")

    async def _setup_network_interception(self, page):
        """Set up network interception to capture API calls and extract URLs from JSON responses."""
        async def handle_response(response):
            try:
                # Only process JSON responses
                content_type = response.headers.get('content-type', '')
                if 'application/json' in content_type:
                    try:
                        json_data = await response.json()
                        # Recursively search for URLs in JSON
                        urls = self._extract_urls_from_json(json_data)
                        self.intercepted_urls.extend(urls)
                    except Exception as e:
                        logger.debug(f"Failed to parse JSON response: {e}")
            except Exception as e:
                logger.debug(f"Response handler error: {e}")

        # Listen to all responses
        page.on('response', handle_response)

    def _extract_urls_from_json(self, obj, depth=0):
        """Recursively extract URLs from JSON object."""
        urls = []
        if depth > 10:  # Prevent infinite recursion
            return urls

        if isinstance(obj, dict):
            for key, value in obj.items():
                # Check if key suggests a URL
                if any(url_key in key.lower() for url_key in ['url', 'href', 'link', 'src', 'endpoint', 'api']):
                    if isinstance(value, str) and (value.startswith('http') or value.startswith('/')):
                        urls.append(value)
                # Recurse into nested objects
                if isinstance(value, (dict, list)):
                    urls.extend(self._extract_urls_from_json(value, depth + 1))
        elif isinstance(obj, list):
            for item in obj:
                urls.extend(self._extract_urls_from_json(item, depth + 1))
        elif isinstance(obj, str):
            if obj.startswith(('http://', 'https://', '/')):
                urls.append(obj)

        return urls


async def run_js_bot():
    """Run the JS rendering bot."""
    bot = JSBot(headless=True, max_concurrent=10)
    await bot.process_queue()


if __name__ == '__main__':
    asyncio.run(run_js_bot())
