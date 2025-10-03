"""
Headless browser integration for JavaScript-rendered content and AJAX endpoints.
Supports both Playwright and Selenium with configurable options.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class HeadlessBrowserError(Exception):
    """Raised when headless browser operations fail"""
    pass


class HeadlessBrowser:
    """Headless browser wrapper supporting Playwright and Selenium"""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize headless browser with configuration

        Args:
            config: Browser configuration with keys:
                - engine: 'playwright' or 'selenium'
                - browser_type: 'chromium', 'firefox', 'webkit'
                - headless: bool
                - timeout: int (milliseconds)
                - user_agent: str
                - viewport: dict with width/height
                - screenshot_on_error: bool
        """
        self.config = config
        self.engine = config.get('engine', 'playwright').lower()
        self.browser_type = config.get('browser_type', 'chromium').lower()
        self.headless = config.get('headless', True)
        self.timeout = config.get('timeout', 30000)
        self.user_agent = config.get('user_agent')
        self.viewport = config.get('viewport', {'width': 1920, 'height': 1080})
        self.screenshot_on_error = config.get('screenshot_on_error', False)
        self.wait_for_selector = config.get('wait_for_selector')

        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None

        logger.info(f"Initialized HeadlessBrowser with engine={self.engine}, browser={self.browser_type}")

    async def start(self):
        """Start the browser instance"""
        if self.engine == 'playwright':
            await self._start_playwright()
        elif self.engine == 'selenium':
            await self._start_selenium()
        else:
            raise HeadlessBrowserError(f"Unsupported browser engine: {self.engine}")

    async def stop(self):
        """Stop the browser instance"""
        try:
            if self.engine == 'playwright':
                await self._stop_playwright()
            elif self.engine == 'selenium':
                await self._stop_selenium()
        except Exception as e:
            logger.warning(f"Error stopping browser: {e}")

    async def _start_playwright(self):
        """Start Playwright browser"""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise HeadlessBrowserError(
                "Playwright not installed. Install with: pip install playwright && playwright install"
            ) from None

        self._playwright = await async_playwright().start()

        # Select browser type
        if self.browser_type == 'chromium':
            browser_launcher = self._playwright.chromium
        elif self.browser_type == 'firefox':
            browser_launcher = self._playwright.firefox
        elif self.browser_type == 'webkit':
            browser_launcher = self._playwright.webkit
        else:
            raise HeadlessBrowserError(f"Unsupported browser type: {self.browser_type}")

        # Launch browser
        self._browser = await browser_launcher.launch(headless=self.headless)

        # Create context with user agent and viewport
        context_options = {
            'viewport': self.viewport,
        }
        if self.user_agent:
            context_options['user_agent'] = self.user_agent

        self._context = await self._browser.new_context(**context_options)
        self._page = await self._context.new_page()

        # Set default timeout
        self._page.set_default_timeout(self.timeout)

        logger.info(f"Playwright {self.browser_type} browser started")

    async def _stop_playwright(self):
        """Stop Playwright browser"""
        if self._page:
            await self._page.close()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

        logger.info("Playwright browser stopped")

    async def _start_selenium(self):
        """Start Selenium browser"""
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options as ChromeOptions
            from selenium.webdriver.firefox.options import Options as FirefoxOptions
        except ImportError:
            raise HeadlessBrowserError(
                "Selenium not installed. Install with: pip install selenium"
            ) from None

        # Configure browser options
        if self.browser_type == 'chromium' or self.browser_type == 'chrome':
            options = ChromeOptions()
            if self.headless:
                options.add_argument('--headless')
            if self.user_agent:
                options.add_argument(f'user-agent={self.user_agent}')
            options.add_argument(f'--window-size={self.viewport["width"]},{self.viewport["height"]}')
            self._browser = webdriver.Chrome(options=options)
        elif self.browser_type == 'firefox':
            options = FirefoxOptions()
            if self.headless:
                options.add_argument('--headless')
            if self.user_agent:
                options.set_preference("general.useragent.override", self.user_agent)
            self._browser = webdriver.Firefox(options=options)
            self._browser.set_window_size(self.viewport["width"], self.viewport["height"])
        else:
            raise HeadlessBrowserError(f"Selenium does not support browser type: {self.browser_type}")

        # Set page load timeout
        self._browser.set_page_load_timeout(self.timeout / 1000)

        logger.info(f"Selenium {self.browser_type} browser started")

    async def _stop_selenium(self):
        """Stop Selenium browser"""
        if self._browser:
            self._browser.quit()
        logger.info("Selenium browser stopped")

    async def fetch_page(self, url: str) -> dict[str, Any]:
        """
        Fetch a page with JavaScript execution

        Args:
            url: URL to fetch

        Returns:
            Dict with keys: html, url, status, screenshots
        """
        if self.engine == 'playwright':
            return await self._fetch_playwright(url)
        elif self.engine == 'selenium':
            return await self._fetch_selenium(url)
        else:
            raise HeadlessBrowserError(f"Unsupported browser engine: {self.engine}")

    async def _fetch_playwright(self, url: str) -> dict[str, Any]:
        """Fetch page using Playwright"""
        try:
            response = await self._page.goto(url, wait_until='networkidle')

            # Wait for specific selector if configured
            if self.wait_for_selector:
                try:
                    await self._page.wait_for_selector(self.wait_for_selector, timeout=5000)
                except Exception as e:
                    logger.debug(f"Selector {self.wait_for_selector} not found: {e}")

            # Get page content
            html = await self._page.content()
            final_url = self._page.url
            status = response.status if response else 0

            # Extract dynamic URLs from network activity
            dynamic_urls = await self._extract_network_urls_playwright()

            return {
                'html': html,
                'url': final_url,
                'status': status,
                'screenshots': [],
                'dynamic_urls': dynamic_urls
            }

        except Exception as e:
            logger.error(f"Playwright fetch failed for {url}: {e}")

            # Take screenshot on error if enabled
            screenshot_path = None
            if self.screenshot_on_error and self._page:
                try:
                    screenshot_dir = Path("data/logs/screenshots")
                    screenshot_dir.mkdir(parents=True, exist_ok=True)
                    screenshot_path = screenshot_dir / f"{urlparse(url).netloc}_{urlparse(url).path.replace('/', '_')}.png"
                    await self._page.screenshot(path=str(screenshot_path))
                    logger.info(f"Screenshot saved: {screenshot_path}")
                except Exception as screenshot_error:
                    logger.warning(f"Failed to save screenshot: {screenshot_error}")

            raise HeadlessBrowserError(f"Failed to fetch {url}: {e}") from e

    async def _extract_network_urls_playwright(self) -> list[str]:
        """Extract URLs from network activity in Playwright"""
        # This would require setting up request/response listeners
        # For now, return empty list as placeholder
        return []

    async def _fetch_selenium(self, url: str) -> dict[str, Any]:
        """Fetch page using Selenium (runs in executor since it's sync)"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._fetch_selenium_sync, url)

    def _fetch_selenium_sync(self, url: str) -> dict[str, Any]:
        """Synchronous Selenium fetch"""
        try:
            self._browser.get(url)

            # Wait for specific selector if configured
            if self.wait_for_selector:
                try:
                    from selenium.webdriver.common.by import By
                    from selenium.webdriver.support import expected_conditions as EC
                    from selenium.webdriver.support.ui import WebDriverWait

                    WebDriverWait(self._browser, 5).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, self.wait_for_selector))
                    )
                except Exception as e:
                    logger.debug(f"Selector {self.wait_for_selector} not found: {e}")

            # Get page source
            html = self._browser.page_source
            final_url = self._browser.current_url

            return {
                'html': html,
                'url': final_url,
                'status': 200,  # Selenium doesn't expose HTTP status
                'screenshots': [],
                'dynamic_urls': []
            }

        except Exception as e:
            logger.error(f"Selenium fetch failed for {url}: {e}")

            # Take screenshot on error if enabled
            if self.screenshot_on_error and self._browser:
                try:
                    screenshot_dir = Path("data/logs/screenshots")
                    screenshot_dir.mkdir(parents=True, exist_ok=True)
                    screenshot_path = screenshot_dir / f"{urlparse(url).netloc}_{urlparse(url).path.replace('/', '_')}.png"
                    self._browser.save_screenshot(str(screenshot_path))
                    logger.info(f"Screenshot saved: {screenshot_path}")
                except Exception as screenshot_error:
                    logger.warning(f"Failed to save screenshot: {screenshot_error}")

            raise HeadlessBrowserError(f"Failed to fetch {url}: {e}") from e

    async def extract_ajax_endpoints(self, url: str) -> list[str]:
        """
        Extract AJAX endpoints from a page by monitoring network activity

        Args:
            url: URL to analyze

        Returns:
            List of discovered AJAX endpoint URLs
        """
        if self.engine != 'playwright':
            logger.warning("AJAX endpoint extraction only supported with Playwright")
            return []

        endpoints = []

        async def handle_request(request):
            # Filter for XHR/Fetch requests
            if request.resource_type in ['xhr', 'fetch']:
                endpoints.append(request.url)

        # Set up request listener
        self._page.on('request', handle_request)

        try:
            # Navigate and wait for network activity
            await self._page.goto(url, wait_until='networkidle')

            # Additional wait for lazy-loaded content
            await asyncio.sleep(2)

            # Scroll to trigger lazy loading
            await self._page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            await asyncio.sleep(1)

        finally:
            # Remove listener
            self._page.remove_listener('request', handle_request)

        logger.info(f"Extracted {len(endpoints)} AJAX endpoints from {url}")
        return endpoints


class HeadlessBrowserPool:
    """Pool of headless browser instances for concurrent processing"""

    def __init__(self, config: dict[str, Any], pool_size: int = 5):
        self.config = config
        self.pool_size = pool_size
        self.browsers: list[HeadlessBrowser] = []
        self._lock = asyncio.Lock()

    async def start(self):
        """Start browser pool"""
        logger.info(f"Starting browser pool with {self.pool_size} instances")
        for _ in range(self.pool_size):
            browser = HeadlessBrowser(self.config)
            await browser.start()
            self.browsers.append(browser)
        logger.info(f"Browser pool started with {len(self.browsers)} instances")

    async def stop(self):
        """Stop all browsers in pool"""
        logger.info("Stopping browser pool")
        for browser in self.browsers:
            await browser.stop()
        self.browsers.clear()
        logger.info("Browser pool stopped")

    async def fetch_page(self, url: str) -> dict[str, Any]:
        """Fetch page using an available browser from the pool"""
        async with self._lock:
            if not self.browsers:
                raise HeadlessBrowserError("Browser pool is empty")
            browser = self.browsers[0]  # Simple round-robin (could be improved)

        return await browser.fetch_page(url)
