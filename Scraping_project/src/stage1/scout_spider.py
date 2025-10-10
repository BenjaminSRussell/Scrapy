"""Ultimate Scout Spider - Most advanced URL discovery system.
Detects JS-heavy pages and routes to specialized renderer.
"""

import base64
import hashlib
import json
import logging
import re
import time
from collections.abc import Iterator
from datetime import datetime
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import scrapy
from scrapy.http import Response
from scrapy.spidermiddlewares.httperror import HttpError
from twisted.internet.error import (
    DNSLookupError,
    TCPTimedOutError,
    TimeoutError,
)

from src.common.delta_lake import get_delta_manager
from src.common.postgres_manager import get_postgres_manager
from src.items import OffsiteCandidateItem

# Import Prometheus metrics for dashboard updates
try:
    from src.scrapy_prometheus import (
        AVERAGE_FILE_SIZE_BYTES,
        NEW_URLS_FOUND_PER_MINUTE,
        OFFSITE_LINKS_FOUND,
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    NEW_URLS_FOUND_PER_MINUTE = None
    AVERAGE_FILE_SIZE_BYTES = None
    OFFSITE_LINKS_FOUND = None
    PROMETHEUS_AVAILABLE = False

logger = logging.getLogger(__name__)


class ScoutSpider(scrapy.Spider):
    """Ultimate URL discovery scout with JS detection and intelligent routing."""

    name = "scout"

    # Restrict crawling to uconn.edu domain only
    allowed_domains = ["uconn.edu"]

    # Handle redirects automatically to uncover more content
    handle_httpstatus_list = [301, 302, 303, 307, 308]

    # Extensions to ignore when following links (static assets, media, documents)
    IGNORED_EXTENSIONS = [
        # Images
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp', '.ico', '.tiff',
        # Stylesheets and scripts (already loaded by browser, not useful for crawling)
        '.css', '.js', '.map',
        # Archives
        '.zip', '.rar', '.7z', '.tar', '.gz', '.bz2',
        # Documents (will be discovered and queued for later processing, not crawled)
        '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
        # Media files
        '.mp3', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4a', '.wav',
        # Fonts
        '.woff', '.woff2', '.ttf', '.eot', '.otf',
        # Other binary formats
        '.exe', '.dmg', '.pkg', '.deb', '.rpm',
    ]

    custom_settings = {
        # EXTREME concurrency - maximum resource utilization
        'CONCURRENT_REQUESTS': 1024,  # Increased from 1024
        'CONCURRENT_REQUESTS_PER_DOMAIN': 512,  # Increased from 512
        # Remove CONCURRENT_REQUESTS_PER_IP to avoid conflicts with priority queue
        'DOWNLOAD_DELAY': 0.01,  # Reduced from 0.01 for even faster speed
        'DOWNLOAD_TIMEOUT': 15,  # Reduced from 20 to fail faster
        'ROBOTSTXT_OBEY': False,
        'COOKIES_ENABLED': False,
        'HTTPCACHE_ENABLED': False,
        'RETRY_ENABLED': True,
        'RETRY_TIMES': 2,  # Reduced from 3 to fail faster

        # Autothrottle - extremely aggressive settings
        'AUTOTHROTTLE_ENABLED': True,
        'AUTOTHROTTLE_START_DELAY': 0.005,  # Reduced from 0.01
        'AUTOTHROTTLE_MAX_DELAY': 1.5,  # Reduced from 2
        'AUTOTHROTTLE_TARGET_CONCURRENCY': 2048,  # Increased from 1024

        # Reactor settings for extreme performance
        'REACTOR_THREADPOOL_MAXSIZE': 128,  # Increased from 128
        'DNS_TIMEOUT': 8,  # Reduced from 10

        # Memory optimizations - increase limits
        'MEMUSAGE_ENABLED': True,
        'MEMUSAGE_LIMIT_MB': 10240,  # Increased to 10GB
        'MEMUSAGE_WARNING_MB': 8192,  # Increased to 8GB

        # Scheduler configuration for high-throughput
        'SCHEDULER_DISK_QUEUE': 'scrapy.squeues.PickleFifoDiskQueue',
        'SCHEDULER_PRIORITY_QUEUE': 'scrapy.pqueues.ScrapyPriorityQueue',

        # Depth limit to prevent going too deep
        'DEPTH_LIMIT': 10,  # Limit crawl depth to 5 levels
        'DEPTH_PRIORITY': 1,  # Enable depth-based priority

        # Connection pool settings for better performance
        'DOWNLOAD_MAXSIZE': 10485760,  # 10MB max download size
        'DOWNLOAD_WARNSIZE': 5242880,  # 5MB warning size
    }

    # Comprehensive URL pattern
    URL_REGEX = re.compile(
        r'(?:(?:https?|ftp):)?//[\w\-\.]+(?::\d+)?(?:/[\w\-\./?%&=]*)?'
        r'|(?:www\.)?[\w\-]+\.(?:edu|com|org|net|gov|io|co)(?:/[\w\-\./?%&=]*)?',
        re.IGNORECASE
    )

    # Patterns for encoded/obfuscated content
    ENCODED_URL_PATTERNS = [
        re.compile(r'atob\(["\']([^"\"]+)["\"]\)'),
        re.compile(r'decodeURIComponent\(["\']([^"\"]+)["\"]\)'),
        re.compile(r'unescape\(["\']([^"\"]+)["\"]\)'),
    ]

    # JS variables that often contain URLs
    JS_VAR_PATTERNS = [
        r'(?:var|let|const)\s+(\w+)\s*=\s*["\']([^"\"]*(?:https?://|/)[^"\"]+)["\"]',
        r'(\w+)\s*:\s*["\']([^"\"]*(?:https?://|/)[^"\"]+)["\"]',
        r'(?:url|href|src|endpoint|api|link)\s*[:=]\s*["\']([^"\"]+)["\"]',
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.url_hashes: set[str] = set()
        self.discovered_records = []
        self.error_records = []
        self.js_render_queue = []
        self.sitemaps_parsed: set[str] = set()

        # Skip tracking counters for live metrics
        self.skip_counters = {
            'images': 0,
            'static_assets': 0,
            'documents': 0,
            'media_files': 0,
            'archives': 0,
            'duplicates': 0,
            'invalid_urls': 0,
        }

        self.delta = get_delta_manager()
        self.postgres = get_postgres_manager()
        self._load_existing_urls()

        self.perf_start_time = datetime.now()
        self.perf_urls_processed = 0
        self.perf_last_log = datetime.now()

        # New dashboard metrics tracking with sliding window
        self.start_time = time.time()
        self.new_urls_count = 0
        self.total_file_size = 0

        # Sliding window for real-time metrics (last 60 seconds)
        from collections import deque
        self.url_discovery_window = deque(maxlen=60)  # Store (timestamp, count) tuples
        self.file_size_window = deque(maxlen=100)  # Store recent file sizes
        self.last_metric_update = time.time()

        # NOTE: Don't register signal handlers here - let Scrapy/Twisted handle graceful shutdown
        # The closed() method will be called automatically on shutdown
        # signal.signal(signal.SIGINT, self._graceful_shutdown)
        # signal.signal(signal.SIGTERM, self._graceful_shutdown)

        self.start_urls = self._load_seed_urls()
        logger.info(f"Scout loaded {len(self.start_urls)} seeds, {len(self.url_hashes)} existing URLs")

    def _hash_url(self, url: str) -> str:
        """Hashes a URL using SHA256 for efficient storage and lookup."""
        return hashlib.sha256(url.encode('utf-8')).hexdigest()

    def _categorize_skip_reason(self, url: str) -> str:
        """Categorize the reason a URL was skipped for metrics tracking."""
        url_lower = url.lower()

        # Check for images
        if any(url_lower.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp', '.ico', '.tiff']):
            return 'images'

        # Check for static assets (CSS, JS)
        if any(url_lower.endswith(ext) for ext in ['.css', '.js', '.map']):
            return 'static_assets'

        # Check for documents
        if any(url_lower.endswith(ext) for ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']):
            return 'documents'

        # Check for media files
        if any(url_lower.endswith(ext) for ext in ['.mp3', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4a', '.wav']):
            return 'media_files'

        # Check for archives
        if any(url_lower.endswith(ext) for ext in ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2']):
            return 'archives'

        # Check for fonts
        if any(url_lower.endswith(ext) for ext in ['.woff', '.woff2', '.ttf', '.eot', '.otf']):
            return 'static_assets'

        # Default to static assets
        return 'static_assets'

    def _track_skip(self, url: str, reason: str = None):
        """Track a skipped URL for metrics."""
        if reason is None:
            reason = self._categorize_skip_reason(url)

        self.skip_counters[reason] = self.skip_counters.get(reason, 0) + 1

        # Log progress every 500 skips
        total_skips = sum(self.skip_counters.values())
        if total_skips % 500 == 0:
            skip_summary = ', '.join([f"{k}: {v}" for k, v in sorted(self.skip_counters.items(), key=lambda x: -x[1])])
            logger.info(f"⏭️  SKIP STATS - Total: {total_skips} | {skip_summary}")

    def _initialize_discovery(self, response: Response):
        """Initializes the discovery process for a given response."""
        self.response = response
        self.base_url = response.url
        self.discovered_urls: set[str] = set()

    def _load_existing_urls(self):
        """Load already discovered URLs from Delta Lake to prevent re-crawling."""
        # Disabled to always start from the beginning of the seed list
        pass

    def _load_seed_urls(self):
        """Load seed URLs from the 'seed_urls' Delta Lake table."""
        try:
            seed_records = self.delta.read('seed_urls')
            urls = [record['url'] for record in seed_records]
            
            # Deduplicate against already scraped URLs
            new_urls = []
            for url in urls:
                url_hash = self._hash_url(url)
                if url_hash not in self.url_hashes:
                    new_urls.append(url)
            
            return new_urls
        except Exception as e:
            logger.error(f"Could not load seed URLs from Delta Lake: {e}")
            return []

    def parse(self, response: Response):
        """Parse response with JS detection and advanced URL extraction."""
        self._initialize_discovery(response)
        depth = response.meta.get('depth', 0)
        url_hash = self._hash_url(response.url)

        content_type = response.headers.get('Content-Type', b'').decode('utf-8', errors='ignore').lower()

        # Yield item for ALL discovered resources (PDFs, images, documents, etc.)
        discovered_item = {
            'url': response.url,
            'url_hash': url_hash,
            'depth': depth,
            'status_code': response.status,
            'content_type': content_type,
            'content_size': len(response.body),
            'discovered_at': datetime.now().isoformat(),
            'discovery_type': 'html' if ('text/html' in content_type or 'application/xhtml' in content_type) else 'resource',
        }

        # Check for document types (PDFs, Word docs, etc.)
        if any(ext in response.url.lower() for ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']):
            discovered_item['resource_type'] = 'document'
            discovered_item['file_extension'] = response.url.lower().split('.')[-1]
        elif any(ext in response.url.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp']):
            discovered_item['resource_type'] = 'image'
        elif any(ext in response.url.lower() for ext in ['.js', '.css']):
            discovered_item['resource_type'] = 'asset'
        else:
            discovered_item['resource_type'] = 'page'

        # Yield item to Kafka for ALL resources
        yield discovered_item

        if 'text/html' not in content_type and 'application/xhtml' not in content_type:
            logger.debug(f"Non-HTML content discovered: {content_type} for {response.url[:80]}")
            self._record_non_html(response, url_hash, depth, content_type)
            return

        requires_js = self._detect_js_requirement(response)
        discovered_urls = list(self.discover_all_urls())

        content_size = len(response.body)
        is_heavy = content_size > 1_000_000

        self._record_discovery(response, url_hash, depth, content_size, is_heavy, len(discovered_urls), requires_js)

        if requires_js:
            self._queue_for_js_rendering(response.url, url_hash, depth)

        self.perf_urls_processed += 1
        self._maybe_log_performance()

        # Update dashboard metrics
        self.new_urls_count += len(discovered_urls)
        self.total_file_size += content_size
        self._current_content_size = content_size  # Store for sliding window
        self._update_dashboard_metrics()

        if len(self.discovered_records) >= 50:
            self._save_batch()

        logger.info(f"[D{depth}] {response.url[:80]} -> {len(discovered_urls)} URLs")

        for new_url in discovered_urls:
            new_url_hash = self._hash_url(new_url)
            if new_url_hash not in self.url_hashes:
                self.url_hashes.add(new_url_hash)

                # Check if URL is external (not uconn.edu)
                is_external = self._is_external_url(new_url)

                if is_external:
                    # Two-Pipeline System: External URLs -> OffsiteCandidateItem
                    # Extract anchor text and context
                    anchor_text, context = self._extract_context(response, new_url)

                    # Create OffsiteCandidateItem instead of crawling
                    offsite_item = OffsiteCandidateItem(
                        source_page=response.url,
                        external_url=new_url,
                        anchor_text=anchor_text,
                        context=context,
                        discovered_at=datetime.now().isoformat()
                    )
                    yield offsite_item

                    # Increment Prometheus metric
                    if PROMETHEUS_AVAILABLE and OFFSITE_LINKS_FOUND:
                        OFFSITE_LINKS_FOUND.labels(spider=self.name).inc()

                    logger.debug(f"External link found: {new_url[:80]}")
                else:
                    # Internal URL (uconn.edu) - proceed with existing logic
                    # Check if it's a static file - track it but don't crawl deeply
                    is_static = any(new_url.lower().endswith(ext) for ext in self.IGNORED_EXTENSIONS)

                    if is_static:
                        # Track skip reason for metrics
                        skip_reason = self._categorize_skip_reason(new_url)
                        self._track_skip(new_url, skip_reason)

                        # Yield metadata about static files without crawling them
                        static_item = {
                            'url': new_url,
                            'url_hash': new_url_hash,
                            'depth': depth + 1,
                            'discovery_type': 'static_resource',
                            'resource_type': 'static',
                            'file_extension': new_url.lower().split('.')[-1] if '.' in new_url else 'unknown',
                            'discovered_at': datetime.now().isoformat(),
                            'parent_url': response.url,
                            'skip_reason': skip_reason,
                        }
                        yield static_item
                        logger.debug(f"Skipped {skip_reason}: {new_url[:80]}")
                    else:
                        # Crawl HTML and API endpoints
                        priority = 0 if urlparse(response.url).netloc == urlparse(new_url).netloc else -1
                        yield scrapy.Request(
                            new_url,
                            callback=self.parse,
                            errback=self.handle_error,
                            meta={'depth': depth + 1},
                            priority=priority,
                            dont_filter=False  # Rely on Scrapy's duplicate filter
                        )

    def discover_all_urls(self) -> Iterator[str]:
        """Extract URLs from ALL possible sources"""
        yield from self._extract_from_standard_tags()
        yield from self._extract_from_inline_scripts()
        yield from self._extract_from_event_handlers()
        yield from self._extract_from_css()
        yield from self._extract_from_style_tags()
        yield from self._extract_from_meta_tags()
        yield from self._extract_from_iframes()
        yield from self._extract_from_data_uris()
        yield from self._extract_from_json_ld()
        yield from self._extract_from_microdata()
        yield from self._extract_from_srcset()
        yield from self._extract_from_headers()
        yield from self._extract_from_encoded()
        yield from self._extract_from_query_params()
        yield from self._extract_sitemap_urls()
        yield from self._extract_from_raw_regex()  # New: regex search on raw HTML
        yield from self.discovered_urls

    def _add_url(self, url: str):
        """Normalize and add URL to discovered set"""
        if not url:
            return

        url = url.strip().strip('"\'')
        if url.startswith(('data:', 'javascript:', 'mailto:', 'tel:', '#')):
            return

        try:
            url = urljoin(self.base_url, url)
        except ValueError:
            logger.debug(f"Could not join base URL with: {url}")
            return

        url = url.split('#')[0]
        if len(url) > 2000:
            return

        # Filter out obviously invalid URLs (common false positives)
        invalid_patterns = [
            '/schema.org', '/w.org', '/text/javascript', '/application/json',
            'uconn.edu', 'e.co'  # These are being extracted incorrectly
        ]
        if any(url.endswith(pattern) or pattern in url.split('/')[-1] for pattern in invalid_patterns):
            logger.debug(f"Skipping invalid URL pattern: {url}")
            return

        self.discovered_urls.add(url)

    def _detect_js_requirement(self, response: Response) -> bool:
        """Detect if page requires JavaScript rendering."""
        html_lower = response.text.lower()
        spa_indicators = ['react', 'vue', 'angular', 'svelte', 'next.js', 'nuxt', '__next', 'spa-root', 'app-root']
        if any(indicator in html_lower for indicator in spa_indicators):
            return True
        if html_lower.count('fetch(') + html_lower.count('axios') + html_lower.count('ajax') > 3:
            return True
        text_content = ''.join(response.css('body ::text').getall()).strip()
        if len(text_content) < 150 and '<script' in html_lower:
            return True
        return False

    # ... (All the _extract_* methods from UltraDiscovery go here) ...
    def _extract_from_standard_tags(self) -> Iterator[str]:
        """Extract from standard HTML tags"""
        for href in self.response.css('a::attr(href), area::attr(href)').getall():
            self._add_url(href)
        for src in self.response.css('img::attr(src), source::attr(src), track::attr(src), script::attr(src), link[rel="stylesheet"]::attr(href), video::attr(src), audio::attr(src), object::attr(data), embed::attr(src)').getall():
            self._add_url(src)

        # Enhanced: Extract from <link> tags (all types, not just stylesheets)
        for link_href in self.response.css('link::attr(href)').getall():
            self._add_url(link_href)

        # Enhanced: Extract from all <script> src attributes
        for script_src in self.response.css('script::attr(src)').getall():
            self._add_url(script_src)

        # Enhanced: Extract from data-* attributes (data-href, data-url, data-src)
        for data_attr in self.response.xpath('//@data-href | //@data-url | //@data-src | //@data-link').getall():
            self._add_url(data_attr)

        return iter(())

    def _extract_from_inline_scripts(self) -> Iterator[str]:
        """Extract URLs from inline <script> tags"""
        scripts = self.response.css('script:not([src])::text').getall()
        for script in scripts:
            for match in self.URL_REGEX.finditer(script):
                self._add_url(match.group(0))
            for pattern_str in self.JS_VAR_PATTERNS:
                pattern = re.compile(pattern_str, re.IGNORECASE)
                for match in pattern.finditer(script):
                    self._add_url(match.groups()[-1])
            ajax_patterns = [r'\.fetch\(["\']([^"\"]+)["\"]\)', r'\.ajax\(\{[^}]*url:\s*["\']([^"\"]+)["\"]', r'XMLHttpRequest\.open\([^,]+,\s*["\']([^"\"]+)["\"]', r'axios\.(?:get|post|put|delete)\(["\']([^"\"]+)["\"]']
            for pattern_str in ajax_patterns:
                pattern = re.compile(pattern_str, re.IGNORECASE)
                for match in pattern.finditer(script):
                    self._add_url(match.group(1))
        return iter(())

    def _extract_from_event_handlers(self) -> Iterator[str]:
        """Extract URLs from onclick, onload, etc."""
        event_attrs = ['onclick', 'onload', 'onmouseover', 'onchange', 'onsubmit', 'onerror', 'onfocus', 'onblur']
        for attr in event_attrs:
            handlers = self.response.xpath(f'//@{attr}').getall()
            for handler in handlers:
                location_patterns = [r'(?:window|document)\.location(?:\.href)?\s*=\s*["\']([^"\"]+)["\"]', r'window\.open\(["\']([^"\"]+)["\"]']
                for pattern_str in location_patterns:
                    pattern = re.compile(pattern_str)
                    for match in pattern.finditer(handler):
                        self._add_url(match.group(1))
        return iter(())

    def _extract_from_css(self) -> Iterator[str]:
        """Extract URLs from CSS (inline styles)"""
        styles = self.response.css('[style]::attr(style)').getall()
        for style in styles:
            for match in re.finditer(r'url\(["\']?([^)\"\"]+)["\"]?\)', style):
                self._add_url(match.group(1))
        return iter(())

    def _extract_from_style_tags(self) -> Iterator[str]:
        """Extract URLs from <style> tags"""
        style_contents = self.response.css('style::text').getall()
        for content in style_contents:
            for match in re.finditer(r'url\(["\']?([^)\"\"]+)["\"]?\)', content):
                self._add_url(match.group(1))
            for match in re.finditer(r'@import\s+["\']([^"\"]+)["\"]', content):
                self._add_url(match.group(1))
        return iter(())

    def _extract_from_meta_tags(self) -> Iterator[str]:
        """Extract URLs from meta tags"""
        meta_refresh = self.response.css('meta[http-equiv="refresh"]::attr(content)').getall()
        for content in meta_refresh:
            match = re.search(r'url=([^;]+)', content, re.IGNORECASE)
            if match:
                self._add_url(match.group(1))
        meta_url_attrs = ['og:url', 'og:image', 'twitter:url', 'twitter:image', 'msapplication-TileImage', 'apple-touch-icon']
        for prop in meta_url_attrs:
            urls = self.response.css(f'meta[property="{prop}"]::attr(content), meta[name="{prop}"]::attr(content)').getall()
            for url in urls:
                self._add_url(url)
        canonical = self.response.css('link[rel="canonical"]::attr(href)').get()
        if canonical:
            self._add_url(canonical)
        return iter(())

    def _extract_from_iframes(self) -> Iterator[str]:
        """Extract URLs from iframes and frame elements"""
        for src in self.response.css('iframe::attr(src), frame::attr(src)').getall():
            self._add_url(src)
        for data_src in self.response.css('iframe::attr(data-src), iframe::attr(data-lazy-src)').getall():
            self._add_url(data_src)
        return iter(())

    def _extract_from_data_uris(self) -> Iterator[str]:
        """Decode data URIs that might contain URLs"""
        data_uris = self.response.xpath('//@*[starts-with(., "data:")]').getall()
        for data_uri in data_uris:
            if ';base64,' in data_uri:
                try:
                    _, encoded = data_uri.split(';base64,', 1)
                    decoded = base64.b64decode(encoded).decode('utf-8', errors='ignore')
                    for match in self.URL_REGEX.finditer(decoded):
                        self._add_url(match.group(0))
                except Exception:
                    pass
        return iter(())

    def _extract_from_json_ld(self) -> Iterator[str]:
        """Extract URLs from JSON-LD structured data"""
        json_ld_scripts = self.response.css('script[type="application/ld+json"]::text').getall()
        for script in json_ld_scripts:
            try:
                data = json.loads(script)
                urls = self._extract_urls_from_json(data)
                for url in urls:
                    self._add_url(url)
            except json.JSONDecodeError:
                pass
        return iter(())

    def _extract_urls_from_json(self, obj, depth=0) -> Iterator[str]:
        """Recursively extract URLs from JSON object"""
        if depth > 10:
            return
        if isinstance(obj, dict):
            for key, value in obj.items():
                if any(url_key in key.lower() for url_key in ['url', 'href', 'link', 'src', 'image']):
                    if isinstance(value, str):
                        yield value
                if isinstance(value, (dict, list)):
                    yield from self._extract_urls_from_json(value, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                yield from self._extract_urls_from_json(item, depth + 1)
        elif isinstance(obj, str):
            if obj.startswith(('http://', 'https://', '/', 'www.')):
                yield obj
        return iter(())

    def _extract_from_microdata(self) -> Iterator[str]:
        """Extract URLs from microdata (itemprop)"""
        urls = self.response.css('[itemprop*="url"]::attr(href), [itemprop*="image"]::attr(src)').getall()
        for url in urls:
            self._add_url(url)
        return iter(())

    def _extract_from_srcset(self) -> Iterator[str]:
        """Extract URLs from srcset attributes (responsive images)"""
        srcsets = self.response.css('img::attr(srcset), source::attr(srcset)').getall()
        for srcset in srcsets:
            for part in srcset.split(','):
                part = part.strip()
                if part:  # Check if part is not empty
                    parts = part.split()
                    if parts:  # Check if split result is not empty
                        url = parts[0]
                        self._add_url(url)
        return iter(())

    def _extract_from_headers(self) -> Iterator[str]:
        """Extract URLs from HTTP Link headers"""
        link_headers = self.response.headers.getlist('Link')
        for header in link_headers:
            matches = re.finditer(r'<([^>]+)>', header.decode('utf-8', errors='ignore'))
            for match in matches:
                self._add_url(match.group(1))
        location = self.response.headers.get('Location', b'').decode('utf-8', errors='ignore')
        if location:
            self._add_url(location)
        return iter(())

    def _extract_from_encoded(self) -> Iterator[str]:
        """Extract URLs from encoded/obfuscated content"""
        html = self.response.text
        for pattern in self.ENCODED_URL_PATTERNS:
            for match in pattern.finditer(html):
                encoded = match.group(1)
                try:
                    if pattern.pattern.startswith('atob'):
                        decoded = base64.b64decode(encoded).decode('utf-8', errors='ignore')
                    elif 'decodeURIComponent' in pattern.pattern:
                        decoded = unquote(encoded)
                    else:
                        decoded = encoded
                    for url_match in self.URL_REGEX.finditer(decoded):
                        self._add_url(url_match.group(0))
                except Exception:
                    pass
        return iter(())

    def _extract_from_query_params(self) -> Iterator[str]:
        """Extract URLs from query parameters (nested URLs)"""
        parsed = urlparse(self.base_url)
        params = parse_qs(parsed.query)
        for _key, values in params.items():
            for value in values:
                if value.startswith(('http://', 'https://', 'www.', '/')):
                    self._add_url(value)
                try:
                    decoded = unquote(value)
                    if decoded.startswith(('http://', 'https://')):
                        self._add_url(decoded)
                except Exception:
                    pass
        return iter(())

    def _is_valid_url(self, url: str) -> bool:
        """Validate that a URL is legitimate and not from example/commented code.

        Filters out:
        - Example domains (example.com, localhost, etc.)
        - Invalid schemes
        - Obviously malformed URLs
        """
        url_lower = url.lower()

        # Filter example/placeholder domains
        invalid_domains = [
            'example.com', 'example.org', 'example.net',
            'localhost', '127.0.0.1', '0.0.0.0',
            'test.com', 'dummy.com', 'placeholder.com',
            'your-domain.com', 'yourdomain.com'
        ]

        for domain in invalid_domains:
            if domain in url_lower:
                return False

        # Filter invalid schemes
        if url_lower.startswith(('javascript:', 'data:', 'mailto:', 'tel:', 'ftp:')):
            return False

        # Must be HTTP(S) or start with /
        if not (url.startswith(('http://', 'https://', '/'))):
            return False

        # Filter common false positives from code examples
        code_patterns = [
            'api.example', 'https://example', 'http://localhost',
            'http://test', 'https://test', '//example',
            'schema.org', 'w3.org/2000', 'w3.org/1999'
        ]

        for pattern in code_patterns:
            if pattern in url_lower:
                return False

        return True

    def _is_external_url(self, url: str) -> bool:
        """Check if a URL is external (not uconn.edu).

        Args:
            url: The URL to check

        Returns:
            True if the URL is external to uconn.edu, False otherwise
        """
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()

            # Remove port if present
            if ':' in domain:
                domain = domain.split(':')[0]

            # Check if it's a uconn.edu domain
            return not (domain.endswith('uconn.edu') or domain == 'uconn.edu')
        except Exception:
            return False

    def _extract_context(self, response: Response, url: str) -> tuple[str, str]:
        """Extract anchor text and surrounding context for a URL.

        Args:
            response: The response object containing the URL
            url: The URL to extract context for

        Returns:
            Tuple of (anchor_text, context)
        """
        anchor_text = ""
        context = ""

        try:
            # Try to find the anchor element containing this URL
            anchors = response.css(f'a[href="{url}"]')
            if anchors:
                # Get the text content of the anchor
                anchor_text = anchors[0].css('::text').get() or ""
                anchor_text = anchor_text.strip()

                # Try to get the parent paragraph for context
                parent_p = anchors[0].xpath('./ancestor::p[1]//text()').getall()
                if parent_p:
                    context = ' '.join([t.strip() for t in parent_p if t.strip()])
                    # Limit context to 500 characters
                    if len(context) > 500:
                        context = context[:500] + "..."
        except Exception as e:
            logger.debug(f"Failed to extract context for {url}: {e}")

        return anchor_text, context

    def _extract_from_raw_regex(self) -> Iterator[str]:
        """Extract URLs from raw HTML using regex search with validation.
        This catches URLs embedded in JavaScript code, HTML comments, and other places
        that CSS selectors might miss. Now includes validation to avoid false positives.
        """
        try:
            raw_html = self.response.text
            discovered_urls = set()

            # Search for URLs in the raw HTML (including JS code and comments)
            for match in self.URL_REGEX.finditer(raw_html):
                url = match.group(0)
                if self._is_valid_url(url):
                    discovered_urls.add(url)

            # Also search HTML comments specifically
            comment_pattern = re.compile(r'<!--(.*?)-->', re.DOTALL)
            for comment_match in comment_pattern.finditer(raw_html):
                comment_text = comment_match.group(1)
                # Skip comments that look like documentation or examples
                if any(marker in comment_text.lower() for marker in ['example', 'todo', 'fixme', 'sample']):
                    continue

                for url_match in self.URL_REGEX.finditer(comment_text):
                    url = url_match.group(0)
                    if self._is_valid_url(url):
                        discovered_urls.add(url)

            # Add validated URLs
            for url in discovered_urls:
                self._add_url(url)

        except Exception as e:
            logger.debug(f"Regex extraction failed: {e}")

        return iter(())

    def _extract_sitemap_urls(self) -> list:
        """Extract from robots.txt and sitemaps."""
        urls = []
        parsed = urlparse(self.response.url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        urls.append(robots_url)
        sitemap_urls = ['/sitemap.xml', '/sitemap_index.xml', '/sitemap-index.xml']
        for sitemap in sitemap_urls:
            urls.append(urljoin(self.response.url, sitemap))
        return urls

    def handle_error(self, failure):
        """Handle errors and send to Kafka for monitoring."""
        # Enhanced: Capture request URL and failure value for better diagnostics
        url = failure.request.url if hasattr(failure, 'request') else 'unknown'
        failure_value = str(failure.value) if hasattr(failure, 'value') else 'unknown error'

        if failure.check(HttpError):
            error_type, error_code, error_message = 'HttpError', failure.value.response.status, f"HTTP {failure.value.response.status}"
        elif failure.check(DNSLookupError):
            error_type, error_code, error_message = 'DNSLookupError', 0, f"DNS lookup failed: {failure_value}"
        elif failure.check(TimeoutError, TCPTimedOutError):
            error_type, error_code, error_message = 'TimeoutError', 0, f"Request timeout: {failure_value}"
        else:
            error_type, error_code, error_message = failure.type.__name__, 0, failure_value

        # Enhanced logging with request URL and failure value
        logger.error(f"Request failed: {url} | Error: {error_type} | Message: {error_message}")

        timestamp = datetime.now().isoformat()
        error_record = {
            'url': url,
            'url_hash': self._hash_url(url),
            'error_type': error_type,
            'error_code': error_code,
            'depth': failure.request.meta.get('depth', 0),
            'timestamp': timestamp,
            'error_message': error_message,
        }

        # Add error record for batch persistence
        self.error_records.append(error_record)

        if self.postgres:
            try:
                self.postgres.log_error(
                    stage='stage1',
                    url=url,
                    error_type=error_type,
                    error_message=error_message,
                    stack_trace=failure.getTraceback(),
                    http_status_code=error_code if error_code > 0 else None,
                )
            except Exception as e:
                logger.debug(f"Failed to log error to PostgreSQL: {e}")

        # Create error item for Kafka monitoring
        error_item = {
            **error_record,
            'discovery_type': 'error',
            'is_functional': False,
        }

        # Yield error to Kafka for real-time monitoring
        return error_item

    def _save_batch(self):
        """Save batches to Delta Lake."""
        if self.discovered_records:
            try:
                self.delta.write('stage1_discovery', self.discovered_records, mode='append')
                logger.info(f"Saved {len(self.discovered_records)} URLs to Delta Lake")
                self.discovered_records = []
            except Exception as e:
                logger.error(f"Failed to save batch: {e}")
        if self.error_records:
            try:
                self.delta.write('stage1_errors', self.error_records, mode='append')
                logger.info(f"Saved {len(self.error_records)} errors")
                self.error_records = []
            except Exception as e:
                logger.error(f"Failed to save errors: {e}")

    def _save_js_queue(self):
        """Save JS render queue."""
        if self.js_render_queue:
            try:
                for record in self.js_render_queue:
                    record['status'] = 'pending'
                self.delta.write('stage1_js_render_queue', self.js_render_queue, mode='append')
                logger.info(f"Saved {len(self.js_render_queue)} JS pages to render queue")
                self.js_render_queue = []
            except Exception as e:
                logger.error(f"Failed to save JS queue: {e}")

    def _maybe_log_performance(self):
        """Log performance metrics periodically."""
        now = datetime.now()
        if (now - self.perf_last_log).total_seconds() >= 5.0 and self.perf_urls_processed > 0:
            elapsed = (now - self.perf_last_log).total_seconds()
            if self.postgres and elapsed > 0:
                try:
                    self.postgres.log_performance_metric(stage='stage1', urls_processed=self.perf_urls_processed, processing_time_seconds=elapsed)
                    logger.debug(f"Stage 1 performance: {self.perf_urls_processed / elapsed:.2f} URLs/sec")
                except Exception as e:
                    logger.debug(f"Failed to log performance to PostgreSQL: {e}")
            self.perf_last_log = now
            self.perf_urls_processed = 0

    def _update_dashboard_metrics(self):
        """Update Prometheus dashboard metrics using sliding window for real-time tracking.

        Uses a 60-second sliding window instead of lifetime average to show
        real-time performance changes and bursts.
        """
        if not PROMETHEUS_AVAILABLE:
            return

        try:
            current_time = time.time()

            # Add current discovery count to sliding window
            self.url_discovery_window.append((current_time, self.new_urls_count))

            # Add current file size to sliding window
            if hasattr(self, '_current_content_size'):
                self.file_size_window.append(self._current_content_size)

            # Only update metrics every second to avoid overhead
            if current_time - self.last_metric_update < 1.0:
                return

            self.last_metric_update = current_time

            # Calculate URLs per minute using sliding window (last 60 seconds)
            cutoff_time = current_time - 60.0
            recent_discoveries = [count for ts, count in self.url_discovery_window if ts >= cutoff_time]

            if recent_discoveries:
                # Sum URLs discovered in last 60 seconds
                urls_in_window = sum(recent_discoveries)
                # Calculate rate per minute based on actual window size
                window_size_minutes = min(60.0, current_time - self.start_time) / 60.0
                if window_size_minutes > 0:
                    urls_per_minute = urls_in_window / window_size_minutes
                    NEW_URLS_FOUND_PER_MINUTE.labels(spider=self.name).set(urls_per_minute)

            # Calculate average file size from recent samples
            if self.file_size_window:
                avg_file_size = sum(self.file_size_window) / len(self.file_size_window)
                AVERAGE_FILE_SIZE_BYTES.labels(spider=self.name).set(avg_file_size)

        except Exception as e:
            logger.debug(f"Failed to update dashboard metrics: {e}")

    def closed(self, reason):
        """Save remaining data on close.

        This method is called by Scrapy when the spider is shutting down,
        including when Ctrl+C is pressed. Scrapy ensures all pipelines have
        finished processing before calling this method.
        """
        logger.info(f"Scout closing: {reason}. Total unique URLs: {len(self.url_hashes)}")

        # Log final skip statistics
        total_skips = sum(self.skip_counters.values())
        if total_skips > 0:
            logger.info(f"📊 FINAL SKIP STATISTICS - Total Skipped: {total_skips}")
            for skip_type, count in sorted(self.skip_counters.items(), key=lambda x: -x[1]):
                if count > 0:
                    percentage = (count / total_skips) * 100
                    logger.info(f"   • {skip_type}: {count:,} ({percentage:.1f}%)")

        logger.info("Saving remaining batches to Delta Lake...")
        self._save_batch()
        self._save_js_queue()
        self.delta.checkpoint()
        logger.info("✅ All data saved successfully. Spider shutdown complete.")

    def _record_non_html(self, response, url_hash, depth, content_type):
        record = {'url': response.url, 'url_hash': url_hash, 'depth': depth, 'status_code': response.status, 'content_type': content_type, 'content_size': len(response.body), 'is_heavy': False, 'discovered_count': 0, 'requires_js': False, 'is_non_html': True, 'timestamp': str(datetime.now())}
        self.discovered_records.append(record)
        if len(self.discovered_records) >= 50:
            self._save_batch()

    def _record_discovery(self, response, url_hash, depth, content_size, is_heavy, discovered_count, requires_js):
        record = {'url': response.url, 'url_hash': url_hash, 'depth': depth, 'status_code': response.status, 'content_type': response.headers.get('Content-Type', b'').decode('utf-8', errors='ignore'), 'content_size': content_size, 'is_heavy': is_heavy, 'discovered_count': discovered_count, 'requires_js': requires_js, 'timestamp': str(datetime.now())}
        self.discovered_records.append(record)

    def _queue_for_js_rendering(self, url, url_hash, depth):
        js_record = {'url': url, 'url_hash': url_hash, 'depth': depth, 'reason': 'js_required', 'timestamp': str(datetime.now())}
        self.js_render_queue.append(js_record)
        logger.info(f"[D{depth}] JS-required, queued for render: {url[:80]}")
        if len(self.js_render_queue) >= 20:
            self._save_js_queue()
