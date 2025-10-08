"""Ultimate Scout Spider - Most advanced URL discovery system.
Detects JS-heavy pages and routes to specialized renderer.
"""

import base64
import hashlib
import json
import logging
import re
import signal
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
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

logger = logging.getLogger(__name__)


class ScoutSpider(scrapy.Spider):
    """Ultimate URL discovery scout with JS detection and intelligent routing."""

    name = "scout"

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
        # Maximum concurrency - push computational limits
        'CONCURRENT_REQUESTS': 256,
        'CONCURRENT_REQUESTS_PER_DOMAIN': 128,
        'DOWNLOAD_DELAY': 0.1,
        'DOWNLOAD_TIMEOUT': 30,
        'ROBOTSTXT_OBEY': False,
        'COOKIES_ENABLED': False,
        'HTTPCACHE_ENABLED': False,
        'RETRY_ENABLED': True,
        'RETRY_TIMES': 2,

        # Autothrottle - aggressive settings
        'AUTOTHROTTLE_ENABLED': True,
        'AUTOTHROTTLE_START_DELAY': 1,
        'AUTOTHROTTLE_MAX_DELAY': 5,
        'AUTOTHROTTLE_TARGET_CONCURRENCY': 256,

        # Reactor settings for high performance
        'REACTOR_THREADPOOL_MAXSIZE': 64,
        'DNS_TIMEOUT': 10,

        # Memory optimizations
        'MEMUSAGE_ENABLED': True,
        'MEMUSAGE_LIMIT_MB': 4096,
        'MEMUSAGE_WARNING_MB': 3072,
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

    def __init__(self, seed_file=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seed_file = seed_file or str(Path(__file__).parent.parent.parent / "data" / "raw" / "uconn_urls.csv")

        self.url_hashes: set[str] = set()
        self.discovered_records = []
        self.error_records = []
        self.js_render_queue = []
        self.sitemaps_parsed: set[str] = set()

        self.delta = get_delta_manager()
        self.postgres = get_postgres_manager()
        self._load_existing_urls()

        self.perf_start_time = datetime.now()
        self.perf_urls_processed = 0
        self.perf_last_log = datetime.now()

        signal.signal(signal.SIGINT, self._graceful_shutdown)
        signal.signal(signal.SIGTERM, self._graceful_shutdown)

        self.start_urls = self._load_seed_urls()
        logger.info(f"Scout loaded {len(self.start_urls)} seeds, {len(self.url_hashes)} existing URLs")

    def _initialize_discovery(self, response: Response):
        """Initialize response-specific variables for discovery."""
        self.response = response
        self.base_url = response.url
        self.discovered_urls = set()

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
        """Create a consistent, normalized hash for a URL."""
        # Normalize: lowercase, remove trailing slash, remove www.
        normalized = url.lower().rstrip('/')
        if urlparse(normalized).netloc.startswith('www.'):
            normalized = normalized.replace('www.', '', 1)
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def _has_ignored_extension(self, url: str) -> bool:
        """Check if URL has an extension that should be ignored."""
        parsed = urlparse(url)
        path = parsed.path.lower()
        return any(path.endswith(ext) for ext in self.IGNORED_EXTENSIONS)

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
        self._initialize_discovery(response)
        depth = response.meta.get('depth', 0)
        url_hash = self._hash_url(response.url)

        content_type = response.headers.get('Content-Type', b'').decode('utf-8', errors='ignore').lower()
        if 'text/html' not in content_type and 'application/xhtml' not in content_type:
            logger.debug(f"Skipping non-HTML content: {content_type} for {response.url[:80]}")
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

        if len(self.discovered_records) >= 50:
            self._save_batch()

        logger.info(f"[D{depth}] {response.url[:80]} -> {len(discovered_urls)} URLs")

        for new_url in discovered_urls:
            if self._has_ignored_extension(new_url):
                logger.debug(f"Skipping static/media file: {new_url[:80]}")
                continue

            new_url_hash = self._hash_url(new_url)
            if new_url_hash not in self.url_hashes:
                self.url_hashes.add(new_url_hash)
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
                url = part.strip().split()[0]
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
        """Handle errors."""
        url = failure.request.url
        if failure.check(HttpError):
            error_type, error_code, error_message = 'HttpError', failure.value.response.status, f"HTTP {failure.value.response.status}"
        elif failure.check(DNSLookupError):
            error_type, error_code, error_message = 'DNSLookupError', 0, "DNS lookup failed"
        elif failure.check(TimeoutError, TCPTimedOutError):
            error_type, error_code, error_message = 'TimeoutError', 0, "Request timeout"
        else:
            error_type, error_code, error_message = failure.type.__name__, 0, str(failure.value)

        error_record = {'url': url, 'url_hash': self._hash_url(url), 'error_type': error_type, 'error_code': error_code, 'depth': failure.request.meta.get('depth', 0), 'timestamp': str(datetime.now())}
        self.error_records.append(error_record)

        if self.postgres:
            try:
                self.postgres.log_error(stage='stage1', url=url, error_type=error_type, error_message=error_message, stack_trace=failure.getTraceback(), http_status_code=error_code if error_code > 0 else None)
            except Exception as e:
                logger.debug(f"Failed to log error to PostgreSQL: {e}")

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

    def closed(self, reason):
        """Save remaining data on close."""
        logger.info(f"Scout closing: {reason}. Total unique URLs: {len(self.url_hashes)}")
        self._save_batch()
        self._save_js_queue()
        self.delta.checkpoint()
        logger.info("All data saved successfully.")

    def _graceful_shutdown(self, signum, frame):
        """Handle SIGINT/SIGTERM for graceful shutdown."""
        logger.warning(f"Received signal {signum}, saving data...")
        self._save_batch()
        self._save_js_queue()
        self.delta.checkpoint()
        logger.info("Data saved, exiting.")

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