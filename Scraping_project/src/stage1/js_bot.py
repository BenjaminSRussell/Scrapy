"""
JavaScript Rendering Bot - Handles JS-heavy pages with Playwright.
"""

import asyncio
import logging
import hashlib
from datetime import datetime
from typing import Dict, Any

from playwright.async_api import async_playwright, Browser
from bs4 import BeautifulSoup

from src.common.delta_lake import get_delta_manager

logger = logging.getLogger(__name__)


class JSBot:
    """Async bot for rendering JavaScript-heavy pages."""

    def __init__(self, headless: bool = True, max_concurrent: int = 10):
        self.headless = headless
        self.max_concurrent = max_concurrent
        self.delta = get_delta_manager()
        self.semaphore = asyncio.Semaphore(max_concurrent)

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

    async def _render_page(self, browser: Browser, record: Dict[str, Any]) -> Dict[str, Any]:
        """Render single page with JavaScript and extract all discovered links."""
        url = record.get('url')
        url_hash = record.get('url_hash')
        depth = record.get('depth', 0)

        async with self.semaphore:
            try:
                page = await browser.new_page()

                # Navigate with timeout
                await page.goto(url, timeout=30000, wait_until='networkidle')

                # Wait for dynamic content
                await page.wait_for_timeout(2000)

                # Scroll to trigger lazy loading
                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                await page.wait_for_timeout(1000)

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


async def run_js_bot():
    """Run the JS rendering bot."""
    bot = JSBot(headless=True, max_concurrent=10)
    await bot.process_queue()


if __name__ == '__main__':
    asyncio.run(run_js_bot())
