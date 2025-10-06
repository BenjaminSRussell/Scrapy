"""
JavaScript Rendering Bot - Handles JS-heavy pages with Playwright.
"""

import asyncio
import logging
import hashlib
from datetime import datetime
from typing import List, Dict, Any

from playwright.async_api import async_playwright, Page, Browser
from bs4 import BeautifulSoup

from src.common.delta_lake import get_delta_manager
from src.stage1.ultra_discovery import UltraDiscovery

logger = logging.getLogger(__name__)


class JSBot:
    """Async bot for rendering JavaScript-heavy pages."""

    def __init__(self, headless: bool = True, max_concurrent: int = 10):
        self.headless = headless
        self.max_concurrent = max_concurrent
        self.delta = get_delta_manager()
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def process_queue(self):
        """Process all pages in JS render queue."""
        try:
            queue_data = self.delta.read('stage1_js_render_queue')
            logger.info(f"Processing {len(queue_data)} JS pages")
        except Exception as e:
            logger.warning(f"No JS queue data: {e}")
            return

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            
            tasks = []
            for record in queue_data:
                task = self._render_page(browser, record)
                tasks.append(task)

            results = await asyncio.gather(*tasks, return_exceptions=True)

            await browser.close()

        # Save results
        successful = [r for r in results if r and not isinstance(r, Exception)]
        if successful:
            self.delta.write('stage1_discovery', successful, mode='append', async_write=False)
            logger.info(f"Saved {len(successful)} rendered pages")

        # Clear processed items from queue
        self.delta.write('stage1_js_render_queue', [], mode='overwrite', async_write=False)

    async def _render_page(self, browser: Browser, record: Dict[str, Any]) -> Dict[str, Any]:
        """Render single page with JavaScript."""
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
                
                # Extract URLs from rendered content
                urls = []
                for link in soup.find_all(['a', 'link'], href=True):
                    urls.append(link['href'])
                
                for script in soup.find_all('script', src=True):
                    urls.append(script['src'])

                # Deduplicate
                urls = list(set(urls))

                return {
                    'url': url,
                    'url_hash': url_hash,
                    'depth': depth,
                    'status_code': 200,
                    'content_type': 'text/html',
                    'content_size': len(html),
                    'is_heavy': len(html) > 1_000_000,
                    'discovered_count': len(urls),
                    'requires_js': True,
                    'js_rendered': True,
                    'timestamp': str(datetime.now())
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
