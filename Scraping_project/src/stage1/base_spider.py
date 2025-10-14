"""Shared crawling logic for Stage 1 spiders."""

import hashlib
import logging
import os
import time
from collections import deque
from collections.abc import Iterator
from datetime import datetime
from urllib.parse import ParseResult, parse_qs, urlencode, urlparse

import redis
import scrapy
from scrapy.http import Response
from scrapy.spidermiddlewares.httperror import HttpError
from twisted.internet.error import DNSLookupError, TCPTimedOutError, TimeoutError

from src.common.config import load_config
from src.common.delta_lake import get_delta_manager
from src.common.postgres_manager import get_postgres_manager
from src.common.url_extractor import URLExtractor
from src.items import OffsiteCandidateItem
from src.stage1.js_detection import JSDetector

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


class BaseSpider(scrapy.Spider):
    """Base spider with shared crawling logic.

    Subclasses should define:
    - name: Spider name
    - custom_settings: Spider-specific settings dict
    """

    # Follow standard redirect status codes
    handle_httpstatus_list = [301, 302, 303, 307, 308]

    def __init__(self, *args, **kwargs):
        """Initialize base spider with shared resources."""
        super().__init__(*args, **kwargs)

        # Limit crawl to uconn.edu domains (including subdomains)
        self.allowed_domains = ['uconn.edu']

        # Grab IGNORED_EXTENSIONS from settings when available
        self.IGNORED_EXTENSIONS = getattr(self, 'settings', {}).get('IGNORED_EXTENSIONS', []) if hasattr(self, 'settings') else []

        # Load configuration
        self.config = load_config()

        # Shared Redis client for URL de-duplication
        redis_config = self.config.get('redis', {})
        redis_host = os.getenv('REDIS_HOST', redis_config.get('host', 'localhost'))
        redis_port = int(os.getenv('REDIS_PORT', redis_config.get('port', 6379)))

        self.redis_client = redis.Redis(
            host=redis_host,
            port=redis_port,
            db=redis_config.get('db', 0),
            decode_responses=False  # Work with bytes for efficiency
        )

        # Spider-specific Redis key storing URL hashes
        self.url_hashes_key = f'{self.name}:url_hashes'

        # Initialize data structures
        self.discovered_records = []
        self.error_records = []
        self.sitemaps_parsed: set[str] = set()

        # Track skipped URLs by type for metrics
        self.skip_counters = {
            'images': 0,
            'static_assets': 0,
            'documents': 0,
            'media_files': 0,
            'archives': 0,
            'duplicates': 0,
            'invalid_urls': 0,
        }

        # Initialize managers
        self.delta = get_delta_manager()
        self.postgres = get_postgres_manager()

        # Performance metrics
        self.perf_start_time = datetime.now()
        self.perf_urls_processed = 0
        self.perf_last_log = datetime.now()

        # Track per-page metrics for dashboard updates
        self.start_time = time.time()
        self.total_urls_discovered = 0
        self.total_file_size = 0

        # Sliding windows for rate calculations
        self.url_discovery_window = deque(maxlen=60)
        self.file_size_window = deque(maxlen=100)
        self.last_metric_update = time.time()

        # Minimum JS detector confidence before flagging a page
        self.js_confidence_threshold = self.config.get('stage1', {}).get('js_confidence_threshold', 0.7)

        # Batch size falls back to 50 if settings unavailable
        self.batch_size = self.settings.getint('DELTA_BATCH_SIZE', 50) if hasattr(self, 'settings') and self.settings else 50

        # Ensure we have start URLs loaded from Delta
        if not hasattr(self, 'start_urls') or not self.start_urls:
            self.start_urls = self._load_seed_urls()

        url_count = self.redis_client.scard(self.url_hashes_key)
        logger.info(f"{self.name} loaded {len(self.start_urls)} seeds, {url_count} existing URLs in Redis")

    async def start(self):
        """Emit initial requests for the dynamically loaded start URLs."""
        logger.warning(f"🚀 [{self.name}] start() CALLED!")  # DEBUG

        if not self.start_urls:
            logger.error(f"{self.name}: No start URLs to crawl! Check Delta Lake seed_urls table.")
            return

        logger.warning(f"🚀 [{self.name}]: Starting crawl with {len(self.start_urls)} seed URLs")

        request_count = 0
        for url in self.start_urls:
            request_count += 1
            if request_count <= 5:
                logger.warning(f"🚀 [{self.name}]: Yielding request #{request_count}: {url[:80]}")
            yield scrapy.Request(
                url,
                callback=self.parse,
                errback=self.handle_error,
                meta={'depth': 0},
                priority=1,
                dont_filter=False
            )

        logger.warning(f"🚀 [{self.name}]: Yielded {request_count} total requests from start()")

    def _hash_url(self, url: str) -> str:
        """Return the full SHA256 hash of a URL for deduplication."""
        return hashlib.sha256(url.encode('utf-8')).hexdigest()

    @staticmethod
    def normalize_url(url: str) -> str:
        """Normalize a URL for consistency.

        - Lowercase scheme and hostname
        - Remove fragment
        - Remove common tracking parameters
        """
        try:
            parsed = urlparse(url)

            # Reconstruct query string without tracking parameters
            query_params = parse_qs(parsed.query)

            # List of trackers to remove (case-insensitive)
            trackers_to_remove = [
                'utm_source', 'utm_medium', 'utm_campaign',
                'utm_term', 'utm_content', 'gclid', 'fbclid'
            ]

            # Filter out tracking parameters
            filtered_params = {
                k: v for k, v in query_params.items()
                if k.lower() not in trackers_to_remove
            }

            # Rebuild the URL
            new_query = urlencode(filtered_params, doseq=True)

            # Create a new ParseResult tuple
            normalized = ParseResult(
                scheme=parsed.scheme.lower(),
                netloc=parsed.netloc.lower(),
                path=parsed.path,
                params=parsed.params,
                query=new_query,
                fragment=""  # Remove fragment
            )

            return normalized.geturl()

        except Exception:
            # If parsing fails, return the original URL
            return url

    def _load_seed_urls(self):
        """Return new seed URLs from Delta Lake that are not already in Redis."""
        try:
            seed_records = self.delta.read('seed_urls')
            urls = [record['url'] for record in seed_records]

            logger.info(f"Loaded {len(urls)} seed URLs from Delta Lake, checking against Redis...")

            # Batch deduplicate using a Redis pipeline
            new_urls = []
            if urls:
                pipeline = self.redis_client.pipeline()
                url_hashes = []

                # Queue all existence checks
                for url in urls:
                    url_hash = self._hash_url(url)
                    url_hashes.append(url_hash)
                    pipeline.sismember(self.url_hashes_key, url_hash)

                # Execute batch check
                exists_results = pipeline.execute()

                # Filter out existing URLs
                for url, exists in zip(urls, exists_results, strict=False):
                    if not exists:
                        new_urls.append(url)

                logger.info(f"After deduplication: {len(new_urls)} new URLs (filtered {len(urls) - len(new_urls)} existing)")

            return new_urls
        except Exception as e:
            logger.error(f"Could not load seed URLs from Delta Lake: {e}")
            return []

    def _load_existing_urls(self):
        """Load already discovered URLs from Delta Lake to prevent re-crawling."""
        # Disabled to always start from the beginning
        pass

    def parse(self, response: Response):
        """Handle each response, record metadata, and queue new work."""
        depth = response.meta.get('depth', 0)
        url_hash = self._hash_url(response.url)
        content_type = response.headers.get('Content-Type', b'').decode('utf-8', errors='ignore').lower()

        # Yield item for ALL discovered resources
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

        # Categorize resource type
        discovered_item['resource_type'] = self._categorize_resource(response.url, content_type)

        # Yield item to pipeline
        yield discovered_item

        # Only process HTML pages
        if 'text/html' not in content_type and 'application/xhtml' not in content_type:
            logger.debug(f"Non-HTML content discovered: {content_type} for {response.url[:80]}")
            self._record_non_html(response, url_hash, depth, content_type)
            return

        # Check whether the page needs JavaScript rendering
        requires_js, confidence = self._detect_js_requirement(response)

        # Extract in-scope URLs
        discovered_urls = self._extract_urls(response)

        content_size = len(response.body)
        is_heavy = content_size > 1_000_000

        # Record discovery details
        self._record_discovery(response, url_hash, depth, content_size, is_heavy, len(discovered_urls), requires_js)

        # Update performance tracking
        self.perf_urls_processed += 1
        self._maybe_log_performance()

        # Track this page in rolling windows
        page_url_count = len(discovered_urls)
        current_time = time.time()

        # Add to sliding window (timestamp, count)
        self.url_discovery_window.append((current_time, page_url_count))
        self.file_size_window.append(content_size)

        # Update totals
        self.total_urls_discovered += page_url_count
        self.total_file_size += content_size

        # Update dashboard metrics
        self._update_dashboard_metrics()

        # Flush batch when we hit the configured size
        if len(self.discovered_records) >= self.batch_size:
            self._save_batch()

        logger.info(f"[D{depth}] {response.url[:80]} -> {page_url_count} URLs (JS: {requires_js}, conf: {confidence:.2f})")

        # Process discovered URLs with batch Redis operations
        yield from self._process_discovered_urls(response, discovered_urls, depth)

    def _categorize_resource(self, url: str, content_type: str) -> str:
        """Categorize the resource type based on URL and content type."""
        url_lower = url.lower()

        if any(ext in url_lower for ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']):
            return 'document'
        elif any(ext in url_lower for ext in ['.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp']):
            return 'image'
        elif any(ext in url_lower for ext in ['.js', '.css']):
            return 'asset'
        else:
            return 'page'

    def _detect_js_requirement(self, response: Response) -> tuple[bool, float]:
        """Detect if page requires JavaScript rendering using JSDetector.

        Returns:
            Tuple of (requires_js: bool, confidence: float)
        """
        detector = JSDetector(response)
        result = detector.requires_js_rendering()
        return result['requires_js'], result['confidence']

    def _extract_urls(self, response: Response) -> list[str]:
        """Extract URLs using centralized URLExtractor.

        Args:
            response: Scrapy Response object

        Returns:
            List of discovered URLs
        """
        extractor = URLExtractor(
            base_url=response.url,
            allowed_domains=self.allowed_domains
        )
        discovered_urls = extractor.discover_all_urls(response)
        return [self.normalize_url(url) for url in discovered_urls]

    def _process_discovered_urls(self, response: Response, discovered_urls: list[str], depth: int) -> Iterator:
        """Deduplicate discovered URLs, track skips, and queue new crawl requests."""
        if not discovered_urls:
            return

        # Batch Redis operations using pipeline
        pipeline = self.redis_client.pipeline()
        url_hash_map = {}

        # First pass: hash all URLs and check existence in batch
        for url in discovered_urls:
            url_hash = self._hash_url(url)
            url_hash_map[url] = url_hash
            pipeline.sismember(self.url_hashes_key, url_hash)

        # Execute batch check
        existence_results = pipeline.execute()

        # Second pass: add new URLs in batch
        new_urls = []
        for url, exists in zip(discovered_urls, existence_results, strict=False):
            if not exists:
                url_hash = url_hash_map[url]
                pipeline.sadd(self.url_hashes_key, url_hash)
                new_urls.append(url)

        # Execute batch add
        if new_urls:
            pipeline.execute()

        # Categorize URLs for downstream handling
        html_candidates = []
        offsite_urls = []
        static_urls = []

        for url in new_urls:
            url_hash = url_hash_map[url]
            is_external = self._is_external_url(url)

            if is_external:
                # Record offsite links but do not follow them
                offsite_urls.append(url)
            else:
                # Internal URLs rely on the configured ignore list
                is_static = any(url.lower().endswith(ext) for ext in self.IGNORED_EXTENSIONS)

                if is_static:
                    # Static resource: publish metadata only
                    static_urls.append((url, url_hash))
                else:
                    # HTML candidate: queue for crawling
                    html_candidates.append((url, url_hash))

        # Emit offsite link items
        for url in offsite_urls:
            yield self._create_offsite_item(response, url)

            if PROMETHEUS_AVAILABLE and OFFSITE_LINKS_FOUND:
                OFFSITE_LINKS_FOUND.labels(spider=self.name).inc()

            logger.debug(f"[K3 OFFSITE] External link found (not following): {url[:80]}")

        # Track static resources
        for url, url_hash in static_urls:
            skip_reason = self._categorize_skip_reason(url)
            self._track_skip(url, skip_reason)
            yield self._create_static_item(url, url_hash, depth, response.url, skip_reason)
            logger.debug(f"[K3 STATIC] Skipped {skip_reason}: {url[:80]}")

        # Queue HTML candidates for crawling
        for url, url_hash in html_candidates:
            priority = 0 if urlparse(response.url).netloc == urlparse(url).netloc else -1
            yield scrapy.Request(
                url,
                callback=self.parse,
                errback=self.handle_error,
                meta={'depth': depth + 1},
                priority=priority,
                dont_filter=False
            )
            logger.debug(f"[K3 HTML] Queued HTML candidate: {url[:80]}")

    def _create_offsite_item(self, response: Response, url: str) -> OffsiteCandidateItem:
        """Create an offsite candidate item with context."""
        anchor_text, context = self._extract_context(response, url)
        return OffsiteCandidateItem(
            source_page=response.url,
            external_url=url,
            anchor_text=anchor_text,
            context=context,
            discovered_at=datetime.now().isoformat()
        )

    def _create_static_item(self, url: str, url_hash: str, depth: int, parent_url: str, skip_reason: str) -> dict:
        """Create metadata item for static resources."""
        return {
            'url': url,
            'url_hash': url_hash,
            'depth': depth + 1,
            'discovery_type': 'static_resource',
            'resource_type': 'static',
            'file_extension': url.lower().split('.')[-1] if '.' in url else 'unknown',
            'discovered_at': datetime.now().isoformat(),
            'parent_url': parent_url,
            'skip_reason': skip_reason,
        }

    def _is_external_url(self, url: str) -> bool:
        """Check if URL is external to allowed domains."""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()

            # Remove port if present
            if ':' in domain:
                domain = domain.split(':')[0]

            # Check against allowed domains
            for allowed in self.allowed_domains:
                if domain.endswith(allowed) or domain == allowed:
                    return False

            return True
        except Exception:
            return False

    def _extract_context(self, response: Response, url: str) -> tuple[str, str]:
        """Extract anchor text and surrounding context for a URL."""
        anchor_text = ""
        context = ""

        try:
            anchors = response.css(f'a[href="{url}"]')
            if anchors:
                anchor_text = anchors[0].css('::text').get() or ""
                anchor_text = anchor_text.strip()

                parent_p = anchors[0].xpath('./ancestor::p[1]//text()').getall()
                if parent_p:
                    context = ' '.join([t.strip() for t in parent_p if t.strip()])
                    if len(context) > 500:
                        context = context[:500] + "..."
        except Exception as e:
            logger.debug(f"Failed to extract context for {url}: {e}")

        return anchor_text, context

    def _categorize_skip_reason(self, url: str) -> str:
        """Categorize the reason a URL was skipped for metrics tracking."""
        url_lower = url.lower()

        if any(url_lower.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp', '.ico', '.tiff']):
            return 'images'
        elif any(url_lower.endswith(ext) for ext in ['.css', '.js', '.map']):
            return 'static_assets'
        elif any(url_lower.endswith(ext) for ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']):
            return 'documents'
        elif any(url_lower.endswith(ext) for ext in ['.mp3', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4a', '.wav']):
            return 'media_files'
        elif any(url_lower.endswith(ext) for ext in ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2']):
            return 'archives'
        elif any(url_lower.endswith(ext) for ext in ['.woff', '.woff2', '.ttf', '.eot', '.otf']):
            return 'static_assets'
        else:
            return 'static_assets'

    def _track_skip(self, url: str, reason: str = None):
        """Track a skipped URL for metrics."""
        if reason is None:
            reason = self._categorize_skip_reason(url)

        self.skip_counters[reason] = self.skip_counters.get(reason, 0) + 1

        total_skips = sum(self.skip_counters.values())
        if total_skips % 500 == 0:
            skip_summary = ', '.join([f"{k}: {v}" for k, v in sorted(self.skip_counters.items(), key=lambda x: -x[1])])
            logger.info(f"⏭️  SKIP STATS - Total: {total_skips} | {skip_summary}")

    def _update_dashboard_metrics(self):
        """Update Prometheus gauges using the sliding windows."""
        if not PROMETHEUS_AVAILABLE:
            return

        current_time = time.time()

        # Calculate rate from sliding window (URLs discovered in last 60 seconds)
        cutoff_time = current_time - 60
        recent_urls = sum(count for timestamp, count in self.url_discovery_window if timestamp >= cutoff_time)
        urls_per_minute = recent_urls  # Already per minute due to 60-second window

        # Calculate average file size from window
        if self.file_size_window:
            avg_file_size = sum(self.file_size_window) / len(self.file_size_window)
        else:
            avg_file_size = 0

        # Update Prometheus metrics
        if NEW_URLS_FOUND_PER_MINUTE:
            NEW_URLS_FOUND_PER_MINUTE.labels(spider=self.name).set(urls_per_minute)

        if AVERAGE_FILE_SIZE_BYTES:
            AVERAGE_FILE_SIZE_BYTES.labels(spider=self.name).set(avg_file_size)

        self.last_metric_update = current_time

    def _record_discovery(self, response: Response, url_hash: str, depth: int,
                         content_size: int, is_heavy: bool, url_count: int, requires_js: bool):
        """Record successful page discovery."""
        record = {
            'url': response.url,
            'url_hash': url_hash,
            'depth': depth,
            'status_code': response.status,
            'content_size': content_size,
            'is_heavy': is_heavy,
            'urls_found': url_count,
            'requires_js': requires_js,
            'discovered_at': datetime.now().isoformat(),
        }
        self.discovered_records.append(record)

    def _record_non_html(self, response: Response, url_hash: str, depth: int, content_type: str):
        """Record non-HTML resource discovery."""
        record = {
            'url': response.url,
            'url_hash': url_hash,
            'depth': depth,
            'content_type': content_type,
            'content_size': len(response.body),
            'discovered_at': datetime.now().isoformat(),
        }
        self.discovered_records.append(record)


    def _save_batch(self):
        """Save batched records to Delta Lake."""
        if self.discovered_records:
            try:
                self.delta.write('stage1_discovery', self.discovered_records)
                logger.info(f"💾 Saved {len(self.discovered_records)} discovery records")
                self.discovered_records = []
            except Exception as e:
                logger.error(f"Failed to save discovery records: {e}")

    def _maybe_log_performance(self):
        """Log performance metrics periodically."""
        now = datetime.now()
        if (now - self.perf_last_log).seconds >= 10:
            elapsed = (now - self.perf_start_time).seconds or 1
            rate = self.perf_urls_processed / elapsed
            logger.info(f"⚡ Performance: {self.perf_urls_processed} URLs @ {rate:.2f} URLs/sec")
            self.perf_last_log = now

    def handle_error(self, failure):
        """Log crawl errors with severity-aware messages."""
        request = failure.request

        # Categorize error
        if failure.check(HttpError):
            response = failure.value.response
            status = response.status

            # Log 404s at INFO level (expected)
            if status == 404:
                logger.info(f"Not found (404): {request.url[:80]}")
            # Log other HTTP errors at WARNING
            else:
                logger.warning(f"HTTP {status} error: {request.url[:80]}")

        elif failure.check(DNSLookupError):
            logger.error(f"DNS lookup failed: {request.url[:80]}")
        elif failure.check(TimeoutError, TCPTimedOutError):
            # Log timeouts at DEBUG on first attempt, ERROR after final retry
            retry_count = request.meta.get('retry_times', 0)
            max_retries = self.settings.get('RETRY_TIMES', 3)

            if retry_count >= max_retries:
                logger.error(f"Timeout after {retry_count} retries: {request.url[:80]}")
            else:
                logger.debug(f"Timeout (retry {retry_count}/{max_retries}): {request.url[:80]}")
        else:
            logger.error(f"Unknown error: {failure.getErrorMessage()} for {request.url[:80]}")

        # Record error for analysis
        error_record = {
            'url': request.url,
            'url_hash': self._hash_url(request.url),
            'error_type': failure.type.__name__,
            'error_message': str(failure.value),
            'depth': request.meta.get('depth', 0),
            'timestamp': datetime.now().isoformat(),
        }
        self.error_records.append(error_record)

        # Save error batch if needed
        if len(self.error_records) >= 50:
            self._save_error_batch()

    def _save_error_batch(self):
        """Save error records to Delta Lake."""
        if self.error_records:
            try:
                self.delta.write('stage1_errors', self.error_records)
                logger.info(f"💾 Saved {len(self.error_records)} error records")
                self.error_records = []
            except Exception as e:
                logger.error(f"Failed to save error records: {e}")

    def closed(self, reason):
        """Called when spider closes - save remaining data."""
        logger.info(f"Spider closing: {reason}")

        # Save any remaining records
        self._save_batch()
        self._save_error_batch()

        # Log final stats
        logger.info(f"✅ Final stats: {self.perf_urls_processed} URLs processed")
        logger.info(f"✅ Total URLs discovered: {self.total_urls_discovered}")

        skip_summary = ', '.join([f"{k}: {v}" for k, v in sorted(self.skip_counters.items(), key=lambda x: -x[1])])
        logger.info(f"⏭️  Final skip stats: {skip_summary}")
