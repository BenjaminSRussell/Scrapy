"""
Ultimate Scout Spider - Most advanced URL discovery system.
Detects JS-heavy pages and routes to specialized renderer.
"""

import signal
import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urljoin
from typing import Set, Dict, Any

import scrapy
from scrapy.http import Response
from scrapy.spidermiddlewares.httperror import HttpError
from twisted.internet.error import DNSLookupError, TimeoutError, TCPTimedOutError

from src.common.delta_lake import get_delta_manager
from src.stage1.ultra_discovery import UltraDiscovery

logger = logging.getLogger(__name__)


class ScoutSpider(scrapy.Spider):
    """Ultimate URL discovery scout with JS detection and intelligent routing."""

    name = "scout"

    custom_settings = {
        'CONCURRENT_REQUESTS': 256,
        'CONCURRENT_REQUESTS_PER_DOMAIN': 128,
        'DOWNLOAD_DELAY': 0,
        'DOWNLOAD_TIMEOUT': 30,
        'ROBOTSTXT_OBEY': False,
        'COOKIES_ENABLED': False,
        'HTTPCACHE_ENABLED': False,
        'RETRY_ENABLED': True,
        'RETRY_TIMES': 2,
        'AUTOTHROTTLE_ENABLED': True,
        'AUTOTHROTTLE_START_DELAY': 0,
        'AUTOTHROTTLE_MAX_DELAY': 0.5,
        'AUTOTHROTTLE_TARGET_CONCURRENCY': 256,
    }

    def __init__(self, seed_file=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seed_file = seed_file or str(Path(__file__).parent.parent.parent / "data" / "raw" / "uconn_urls.csv")
        
        self.url_hashes: Set[str] = set()
        self.discovered_records = []
        self.error_records = []
        self.js_render_queue = []
        
        self.delta = get_delta_manager()
        self._load_existing_urls()
        
        signal.signal(signal.SIGINT, self._graceful_shutdown)
        signal.signal(signal.SIGTERM, self._graceful_shutdown)
        
        self.start_urls = self._load_seed_urls()
        logger.info(f"Scout loaded {len(self.start_urls)} seeds, {len(self.url_hashes)} existing URLs")

    def _load_existing_urls(self):
        """Load existing URL hashes from Delta Lake for deduplication."""
        try:
            existing = self.delta.read('stage1_discovery')
            for record in existing:
                url_hash = self._hash_url(record.get('url', ''))
                self.url_hashes.add(url_hash)
            logger.info(f"Loaded {len(self.url_hashes)} existing URL hashes")
        except Exception as e:
            logger.warning(f"No existing URLs: {e}")

    def _hash_url(self, url: str) -> str:
        """Create normalized hash for deduplication."""
        normalized = url.lower().rstrip('/')
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def _load_seed_urls(self):
        """Load seed URLs without pre-adding to hash set."""
        seed_path = Path(self.seed_file)
        if not seed_path.exists():
            logger.error(f"Seed file not found: {self.seed_file}")
            return []

        urls = []
        with open(seed_path) as f:
            for line in f:
                line = line.strip()
                if line and line.startswith('http'):
                    url_hash = self._hash_url(line)
                    if url_hash not in self.url_hashes:
                        urls.append(line)
        return urls

    def parse(self, response: Response):
        """Parse response with JS detection and advanced URL extraction."""
        depth = response.meta.get('depth', 0)
        url_hash = self._hash_url(response.url)

        # Detect if page requires JavaScript rendering
        requires_js = self._detect_js_requirement(response)

        if requires_js:
            # Route to JS render queue
            js_record = {
                'url': response.url,
                'url_hash': url_hash,
                'depth': depth,
                'reason': 'js_required',
                'timestamp': str(datetime.now())
            }
            self.js_render_queue.append(js_record)
            logger.info(f"[D{depth}] JS-required: {response.url[:80]}")
            
            if len(self.js_render_queue) >= 50:
                self._save_js_queue()
            return

        # Ultra discovery for standard HTML
        ultra = UltraDiscovery(response)
        discovered_urls = list(ultra.discover_all())

        # Advanced techniques
        discovered_urls.extend(self._extract_api_endpoints(response))
        discovered_urls.extend(self._extract_sitemap_urls(response))
        discovered_urls.extend(self._extract_from_comments(response))
        discovered_urls.extend(self._extract_from_headers(response))

        # Deduplicate
        discovered_urls = list(set(discovered_urls))

        content_size = len(response.body)
        is_heavy = content_size > 1_000_000

        record = {
            'url': response.url,
            'url_hash': url_hash,
            'depth': depth,
            'status_code': response.status,
            'content_type': response.headers.get('Content-Type', b'').decode('utf-8', errors='ignore'),
            'content_size': content_size,
            'is_heavy': is_heavy,
            'discovered_count': len(discovered_urls),
            'requires_js': False,
            'timestamp': str(datetime.now())
        }

        self.discovered_records.append(record)

        if len(self.discovered_records) >= 100:
            self._save_batch()

        logger.info(f"[D{depth}] {response.url[:80]} -> {len(discovered_urls)} URLs")

        # Follow discovered URLs
        for new_url in discovered_urls:
            url_hash = self._hash_url(new_url)
            if url_hash not in self.url_hashes:
                self.url_hashes.add(url_hash)

                parsed_current = urlparse(response.url)
                parsed_new = urlparse(new_url)
                priority = 0 if parsed_current.netloc == parsed_new.netloc else -1

                yield scrapy.Request(
                    new_url,
                    callback=self.parse,
                    errback=self.handle_error,
                    meta={'depth': depth + 1, 'is_heavy': is_heavy},
                    priority=priority,
                    dont_filter=True
                )

    def _detect_js_requirement(self, response: Response) -> bool:
        """Detect if page requires JavaScript rendering."""
        # Check for SPA frameworks
        html_lower = response.text.lower()
        
        spa_indicators = [
            'react', 'vue', 'angular', 'svelte',
            'next.js', 'nuxt', '__next',
            'window.__initial', 'window.__data',
            'spa-root', 'app-root', 'root-app'
        ]
        
        if any(indicator in html_lower for indicator in spa_indicators):
            return True

        # Check for heavy async loading
        if html_lower.count('fetch(') + html_lower.count('axios') + html_lower.count('ajax') > 5:
            return True

        # Check for minimal initial content
        text_content = response.css('body ::text').getall()
        total_text = ''.join(text_content).strip()
        
        if len(total_text) < 200 and '<script' in html_lower:
            return True

        return False

    def _extract_api_endpoints(self, response: Response) -> list:
        """Extract API endpoints from JavaScript."""
        urls = []
        scripts = response.css('script::text').getall()
        
        api_patterns = [
            r'["\']/(api|graphql|rest|v\d+)/[^"\']+["\']',
            r'endpoint\s*[:=]\s*["\']([^"\']+)["\']',
            r'baseURL\s*[:=]\s*["\']([^"\']+)["\']',
        ]
        
        for script in scripts:
            for pattern in api_patterns:
                matches = re.findall(pattern, script)
                for match in matches:
                    if isinstance(match, tuple):
                        match = match[0]
                    full_url = urljoin(response.url, match)
                    urls.append(full_url)
        
        return urls

    def _extract_sitemap_urls(self, response: Response) -> list:
        """Extract from robots.txt and sitemaps."""
        urls = []
        
        # Check robots.txt reference
        parsed = urlparse(response.url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        urls.append(robots_url)
        
        # Check common sitemap locations
        sitemap_urls = [
            '/sitemap.xml',
            '/sitemap_index.xml',
            '/sitemap-index.xml',
        ]
        
        for sitemap in sitemap_urls:
            urls.append(urljoin(response.url, sitemap))
        
        return urls

    def _extract_from_comments(self, response: Response) -> list:
        """Extract URLs from HTML comments."""
        urls = []
        
        # Find all HTML comments
        comments = re.findall(r'<!--(.*?)-->', response.text, re.DOTALL)
        
        url_pattern = r'https?://[^\s<>"]+|www\.[^\s<>"]+'
        
        for comment in comments:
            found_urls = re.findall(url_pattern, comment)
            for url in found_urls:
                if not url.startswith('http'):
                    url = 'http://' + url
                urls.append(url)
        
        return urls

    def _extract_from_headers(self, response: Response) -> list:
        """Extract URLs from HTTP headers."""
        urls = []
        
        # Link header
        link_header = response.headers.get('Link', b'').decode('utf-8', errors='ignore')
        if link_header:
            link_urls = re.findall(r'<([^>]+)>', link_header)
            urls.extend([urljoin(response.url, u) for u in link_urls])
        
        # Location header (redirects)
        location = response.headers.get('Location', b'').decode('utf-8', errors='ignore')
        if location:
            urls.append(urljoin(response.url, location))
        
        return urls

    def handle_error(self, failure):
        """Handle errors."""
        url = failure.request.url

        if failure.check(HttpError):
            response = failure.value.response
            error_type = 'http_error'
            error_code = response.status
        elif failure.check(DNSLookupError):
            error_type = 'dns_error'
            error_code = 0
        elif failure.check(TimeoutError, TCPTimedOutError):
            error_type = 'timeout'
            error_code = 0
        else:
            error_type = 'unknown'
            error_code = 0

        error_record = {
            'url': url,
            'url_hash': self._hash_url(url),
            'error_type': error_type,
            'error_code': error_code,
            'depth': failure.request.meta.get('depth', 0),
            'timestamp': str(datetime.now())
        }

        self.error_records.append(error_record)

    def _save_batch(self):
        """Save batches to Delta Lake."""
        if self.discovered_records:
            try:
                self.delta.write('stage1_discovery', self.discovered_records, mode='append', async_write=False)
                logger.info(f"Saved {len(self.discovered_records)} URLs to Delta Lake")
                self.discovered_records = []
            except Exception as e:
                logger.error(f"Failed to save batch: {e}")

        if self.error_records:
            try:
                self.delta.write('stage1_errors', self.error_records, mode='append', async_write=False)
                logger.info(f"Saved {len(self.error_records)} errors")
                self.error_records = []
            except Exception as e:
                logger.error(f"Failed to save errors: {e}")

    def _save_js_queue(self):
        """Save JS render queue with 'pending' status for worker pickup."""
        if self.js_render_queue:
            try:
                # Add 'pending' status for JS bot to pick up
                for record in self.js_render_queue:
                    record['status'] = 'pending'

                self.delta.write('stage1_js_render_queue', self.js_render_queue, mode='append', async_write=False)
                logger.info(f"Saved {len(self.js_render_queue)} JS pages to render queue")
                self.js_render_queue = []
            except Exception as e:
                logger.error(f"Failed to save JS queue: {e}")

    def closed(self, reason):
        """Save remaining data on close."""
        logger.info(f"Scout closing: {reason}")
        logger.info(f"Total unique URLs discovered: {len(self.url_hashes)}")

        self._save_batch()
        self._save_js_queue()
        self.delta.checkpoint()

        logger.info("All data saved successfully")

    def _graceful_shutdown(self, signum, frame):
        """Handle SIGINT/SIGTERM."""
        logger.warning(f"Received signal {signum}, saving data...")
        self._save_batch()
        self._save_js_queue()
        self.delta.checkpoint()
        logger.info("Data saved, exiting")
