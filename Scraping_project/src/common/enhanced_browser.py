"""
Enhanced headless browser with advanced JavaScript discovery capabilities.
Includes network interception, auto-click, SPA support, infinite scroll, and resource management.
"""

import logging
import asyncio
import re
from typing import Dict, Any, List, Optional, Set
from pathlib import Path
from urllib.parse import urlparse, urljoin
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)


class BrowserDiscoveryError(Exception):
    """Raised when browser discovery operations fail"""
    pass


class EnhancedBrowserDiscovery:
    """
    Enhanced browser for discovering URLs from JavaScript-heavy pages.
    Supports network interception, auto-click, infinite scroll, and SPA navigation.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize enhanced browser discovery

        Args:
            config: Configuration with keys:
                - browser_type: 'chromium', 'firefox', 'webkit'
                - headless: bool
                - timeout: int (milliseconds)
                - concurrent_limit: int (max concurrent browsers)
                - enable_network_intercept: bool
                - enable_auto_click: bool
                - enable_infinite_scroll: bool
                - max_scroll_attempts: int
                - scroll_pause_ms: int
        """
        self.config = config
        self.browser_type = config.get('browser_type', 'chromium')
        self.headless = config.get('headless', True)
        self.timeout = config.get('timeout', 30000)
        self.concurrent_limit = config.get('concurrent_limit', 2)

        # Feature flags
        self.enable_network_intercept = config.get('enable_network_intercept', True)
        self.enable_auto_click = config.get('enable_auto_click', True)
        self.enable_infinite_scroll = config.get('enable_infinite_scroll', True)

        # Scroll configuration
        self.max_scroll_attempts = config.get('max_scroll_attempts', 10)
        self.scroll_pause_ms = config.get('scroll_pause_ms', 500)

        # Network tracking
        self.intercepted_requests: Set[str] = set()
        self.intercepted_responses: List[Dict[str, Any]] = []

        # Resource management
        self.active_browsers = 0
        self.total_pages_processed = 0

        # Discovery statistics
        self.stats = {
            'network_requests_captured': 0,
            'buttons_clicked': 0,
            'scroll_actions': 0,
            'urls_discovered': 0,
            'errors': 0
        }

        self._playwright = None
        self._browser_pool: List[Any] = []

        logger.info(f"EnhancedBrowserDiscovery initialized: {self.browser_type}, concurrent_limit={self.concurrent_limit}")

    async def start(self):
        """Start the browser pool"""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise BrowserDiscoveryError(
                "Playwright not installed. Install with: pip install playwright && playwright install"
            )

        self._playwright = await async_playwright().start()

        # Pre-warm browser pool
        for _ in range(min(self.concurrent_limit, 1)):  # Start with 1, create more as needed
            await self._create_browser()

        logger.info(f"Browser pool started with {len(self._browser_pool)} browsers")

    async def stop(self):
        """Stop all browsers in the pool"""
        for browser in self._browser_pool:
            try:
                await browser.close()
            except Exception as e:
                logger.warning(f"Error closing browser: {e}")

        if self._playwright:
            await self._playwright.stop()

        logger.info(f"Browser pool stopped. Stats: {self.stats}")

    async def _create_browser(self):
        """Create a new browser instance"""
        if self.browser_type == 'chromium':
            browser_launcher = self._playwright.chromium
        elif self.browser_type == 'firefox':
            browser_launcher = self._playwright.firefox
        elif self.browser_type == 'webkit':
            browser_launcher = self._playwright.webkit
        else:
            raise BrowserDiscoveryError(f"Unsupported browser type: {self.browser_type}")

        browser = await browser_launcher.launch(
            headless=self.headless,
            args=['--disable-dev-shm-usage', '--no-sandbox'] if self.browser_type == 'chromium' else None
        )

        self._browser_pool.append(browser)
        return browser

    async def _get_browser(self):
        """Get an available browser from the pool or create one"""
        if len(self._browser_pool) < self.concurrent_limit:
            return await self._create_browser()
        return self._browser_pool[self.active_browsers % len(self._browser_pool)]

    async def discover_urls(self, url: str, base_domain: str) -> Dict[str, Any]:
        """
        Discover URLs from a JavaScript-heavy page using all available techniques.

        Args:
            url: URL to analyze
            base_domain: Base domain to filter URLs (e.g., 'uconn.edu')

        Returns:
            Dict with discovered URLs and metadata
        """
        self.active_browsers += 1
        self.total_pages_processed += 1

        try:
            browser = await self._get_browser()
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
            )

            page = await context.new_page()
            page.set_default_timeout(self.timeout)

            discovered_urls = set()
            network_urls = []

            # Set up network interception if enabled
            if self.enable_network_intercept:
                network_urls = await self._setup_network_interception(page, base_domain)

            try:
                # Navigate to page
                logger.info(f"Navigating to {url}")
                response = await page.goto(url, wait_until='networkidle', timeout=self.timeout)

                # Wait for initial content
                await page.wait_for_load_state('domcontentloaded')

                # Extract static URLs from HTML
                static_urls = await self._extract_static_urls(page, url)
                discovered_urls.update(static_urls)

                # Auto-click "Load More" buttons if enabled
                if self.enable_auto_click:
                    click_urls = await self._auto_click_load_more(page, url)
                    discovered_urls.update(click_urls)

                # Handle infinite scroll if enabled
                if self.enable_infinite_scroll:
                    scroll_urls = await self._handle_infinite_scroll(page, url)
                    discovered_urls.update(scroll_urls)

                # Detect and handle SPA navigation
                spa_urls = await self._detect_spa_navigation(page, url)
                discovered_urls.update(spa_urls)

                # Get HTML content
                html_content = await page.content()
                final_url = page.url

                result = {
                    'original_url': url,
                    'final_url': final_url,
                    'discovered_urls': list(discovered_urls),
                    'network_urls': network_urls,
                    'html_content': html_content,
                    'status_code': response.status if response else 0,
                    'discovery_methods': {
                        'static_html': len(static_urls),
                        'auto_click': self.stats['buttons_clicked'],
                        'infinite_scroll': self.stats['scroll_actions'],
                        'network_intercept': len(network_urls)
                    },
                    'timestamp': datetime.now().isoformat()
                }

                self.stats['urls_discovered'] += len(discovered_urls)

                return result

            finally:
                await context.close()

        except Exception as e:
            logger.error(f"Browser discovery failed for {url}: {e}")
            self.stats['errors'] += 1
            raise BrowserDiscoveryError(f"Failed to discover URLs: {e}")

        finally:
            self.active_browsers -= 1

    async def _setup_network_interception(self, page, base_domain: str) -> List[str]:
        """Set up network request/response interception"""
        captured_urls = []

        async def handle_request(request):
            """Capture outgoing requests"""
            url = request.url
            resource_type = request.resource_type

            # Filter for interesting request types
            if resource_type in ['xhr', 'fetch', 'document']:
                if base_domain in url:
                    captured_urls.append(url)
                    self.intercepted_requests.add(url)
                    self.stats['network_requests_captured'] += 1
                    logger.debug(f"Intercepted {resource_type} request: {url}")

        async def handle_response(response):
            """Capture responses"""
            if response.request.resource_type in ['xhr', 'fetch']:
                self.intercepted_responses.append({
                    'url': response.url,
                    'status': response.status,
                    'content_type': response.headers.get('content-type', ''),
                    'timestamp': datetime.now().isoformat()
                })

        page.on('request', handle_request)
        page.on('response', handle_response)

        return captured_urls

    async def _extract_static_urls(self, page, base_url: str) -> Set[str]:
        """Extract URLs from static HTML content"""
        urls = set()

        try:
            # Extract from anchor tags
            links = await page.evaluate('''() => {
                const anchors = Array.from(document.querySelectorAll('a[href]'));
                return anchors.map(a => a.href);
            }''')

            for link in links:
                absolute_url = urljoin(base_url, link)
                urls.add(absolute_url)

        except Exception as e:
            logger.debug(f"Failed to extract static URLs: {e}")

        return urls

    async def _auto_click_load_more(self, page, base_url: str) -> Set[str]:
        """
        Automatically detect and click "Load More" buttons.

        Looks for buttons/links with common patterns:
        - Text containing: "load more", "show more", "view more", "see more"
        - Classes/IDs containing: "load-more", "show-more", "pagination"
        - Data attributes suggesting loading: data-load, data-page
        """
        urls = set()
        max_clicks = 5  # Prevent infinite loops

        load_more_selectors = [
            'button:has-text("Load More")',
            'button:has-text("Show More")',
            'a:has-text("Load More")',
            'a:has-text("Show More")',
            'button:has-text("View More")',
            '[class*="load-more"]',
            '[class*="show-more"]',
            '[id*="load-more"]',
            '[data-load]',
            '.pagination-next',
            '.load-more-btn'
        ]

        for selector in load_more_selectors:
            try:
                # Check if element exists
                element = await page.query_selector(selector)
                if not element:
                    continue

                for click_count in range(max_clicks):
                    try:
                        # Check if still visible and enabled
                        is_visible = await element.is_visible()
                        is_enabled = await element.is_enabled()

                        if not (is_visible and is_enabled):
                            break

                        # Get current URL count
                        before_count = len(await self._extract_static_urls(page, base_url))

                        # Click the button
                        await element.click()
                        self.stats['buttons_clicked'] += 1
                        logger.info(f"Clicked '{selector}' button (attempt {click_count + 1})")

                        # Wait for network activity to settle
                        await page.wait_for_load_state('networkidle', timeout=5000)
                        await asyncio.sleep(0.5)

                        # Check if new content loaded
                        after_count = len(await self._extract_static_urls(page, base_url))

                        if after_count == before_count:
                            # No new content, stop clicking
                            break

                        # Extract newly discovered URLs
                        new_urls = await self._extract_static_urls(page, base_url)
                        urls.update(new_urls)

                    except Exception as e:
                        logger.debug(f"Click attempt failed: {e}")
                        break

            except Exception as e:
                logger.debug(f"Load more selector '{selector}' not found: {e}")

        return urls

    async def _handle_infinite_scroll(self, page, base_url: str) -> Set[str]:
        """
        Handle infinite scroll by scrolling down and detecting new content.
        """
        urls = set()
        previous_height = 0
        no_change_count = 0

        for scroll_attempt in range(self.max_scroll_attempts):
            try:
                # Get current scroll height
                current_height = await page.evaluate('document.body.scrollHeight')

                # If height hasn't changed, we might be at the bottom
                if current_height == previous_height:
                    no_change_count += 1
                    if no_change_count >= 2:
                        logger.debug("Reached bottom of infinite scroll")
                        break
                else:
                    no_change_count = 0

                # Scroll to bottom
                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                self.stats['scroll_actions'] += 1

                # Wait for new content to load
                await asyncio.sleep(self.scroll_pause_ms / 1000)
                await page.wait_for_load_state('networkidle', timeout=3000)

                # Extract URLs from newly loaded content
                new_urls = await self._extract_static_urls(page, base_url)
                urls.update(new_urls)

                previous_height = current_height

            except Exception as e:
                logger.debug(f"Scroll attempt {scroll_attempt} failed: {e}")
                break

        return urls

    async def _detect_spa_navigation(self, page, base_url: str) -> Set[str]:
        """
        Detect and handle Single Page Application (SPA) navigation.

        SPAs often use pushState/replaceState for navigation without full page reloads.
        This method extracts URLs from SPA routing.
        """
        urls = set()

        try:
            # Look for common SPA framework indicators
            spa_indicators = await page.evaluate('''() => {
                const indicators = {
                    hasReact: !!window.React || !!document.querySelector('[data-reactroot]'),
                    hasVue: !!window.Vue || !!document.querySelector('[data-v-]'),
                    hasAngular: !!window.angular || !!document.querySelector('[ng-app]'),
                    hasRouter: !!window.history && !!window.history.pushState
                };
                return indicators;
            }''')

            if not any(spa_indicators.values()):
                return urls

            logger.debug(f"SPA detected: {spa_indicators}")

            # Extract URLs from router config (common patterns)
            router_urls = await page.evaluate('''() => {
                const urls = new Set();

                // React Router
                if (window.__REACT_ROUTER__) {
                    // Try to extract routes from router config
                }

                // Vue Router
                if (window.$router && window.$router.options.routes) {
                    window.$router.options.routes.forEach(route => {
                        if (route.path) urls.add(route.path);
                    });
                }

                // Angular Router
                if (window.getAllAngularRootElements) {
                    // Angular route extraction
                }

                // Generic: Look for links that don't cause full page reload
                const internalLinks = Array.from(document.querySelectorAll('a[href^="/"], a[href^="#"]'));
                internalLinks.forEach(link => {
                    if (link.href) urls.add(link.href);
                });

                return Array.from(urls);
            }''')

            for router_url in router_urls:
                absolute_url = urljoin(base_url, router_url)
                urls.add(absolute_url)

        except Exception as e:
            logger.debug(f"SPA detection failed: {e}")

        return urls

    def get_statistics(self) -> Dict[str, Any]:
        """Get discovery statistics"""
        return {
            **self.stats,
            'total_pages_processed': self.total_pages_processed,
            'active_browsers': self.active_browsers,
            'pool_size': len(self._browser_pool)
        }
