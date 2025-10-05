"""
Ultra-aggressive discovery spider with dynamic scaling and zero data loss.
"""

import signal
from datetime import datetime
import hashlib
import logging
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


class DiscoverySpider(scrapy.Spider):
    """Ultra-aggressive URL discovery with dynamic depth and graceful shutdown."""

    name = "discovery"

    # Dynamic settings
    # Aggressive settings
    custom_settings = {
        "CONCURRENT_REQUESTS": 128,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 64,
        "DOWNLOAD_DELAY": 0,
        "DOWNLOAD_TIMEOUT": 30,
        "ROBOTSTXT_OBEY": False,
        "COOKIES_ENABLED": False,
        "HTTPCACHE_ENABLED": False,
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 2,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 0,
        "AUTOTHROTTLE_MAX_DELAY": 1,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 128,
    }

    def __init__(self, seed_file=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seed_file = seed_file or str(Path(__file__).parent.parent.parent / "data" / "raw" / "uconn_urls.csv")

        # URL tracking with hash-based deduplication
        self.url_hashes: Set[str] = set()
        self.discovered_records = []
        self.error_records = []

        # Delta Lake manager
        self.delta = get_delta_manager()

        # Load existing URL hashes to prevent duplicates
        self._load_existing_urls()

        # Setup graceful shutdown
        signal.signal(signal.SIGINT, self._graceful_shutdown)
        signal.signal(signal.SIGTERM, self._graceful_shutdown)

        # Load seeds
        self.start_urls = self._load_seed_urls()
        logger.info(f"Loaded {len(self.start_urls)} seeds, {len(self.url_hashes)} existing URLs in Delta Lake")

    def _load_existing_urls(self):
        """Load existing URL hashes from Delta Lake to prevent duplicates."""
        try:
            existing = self.delta.read('stage1_discovery')
            for record in existing:
                url_hash = self._hash_url(record.get('url', ''))
                self.url_hashes.add(url_hash)
            logger.info(f"Loaded {len(self.url_hashes)} existing URL hashes")
        except Exception as e:
            logger.warning(f"No existing URLs: {e}")

    def _hash_url(self, url: str) -> str:
        """Create hash of URL for deduplication."""
        normalized = url.lower().rstrip('/')
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def _load_seed_urls(self):
        """Load seed URLs."""
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
                        # Don't add to url_hashes yet - will be added when parsed
        return urls

    def parse(self, response: Response):
        """Extract ALL URLs using ultra discovery."""
        depth = response.meta.get('depth', 0)
        url_hash = self._hash_url(response.url)

        # Ultra discovery
        ultra = UltraDiscovery(response)
        discovered_urls = list(ultra.discover_all())

        # Analyze page density
        content_size = len(response.body)
        is_heavy = content_size > 1_000_000  # 1MB+

        # Create discovery record
        record = {
            'url': response.url,
            'url_hash': url_hash,
            'depth': depth,
            'status_code': response.status,
            'content_type': response.headers.get('Content-Type', b'').decode('utf-8', errors='ignore'),
            'content_size': content_size,
            'is_heavy': is_heavy,
            'discovered_count': len(discovered_urls),
            'timestamp': str(datetime.now())
        }

        self.discovered_records.append(record)

        # Batch save every 100 records
        if len(self.discovered_records) >= 100:
            self._save_batch()

        logger.info(f"[D{depth}] {response.url[:80]} -> {len(discovered_urls)} URLs (size={content_size}, heavy={is_heavy})")

        # Follow discovered URLs with dynamic depth
        for new_url in discovered_urls:
            url_hash = self._hash_url(new_url)
            if url_hash not in self.url_hashes:
                self.url_hashes.add(url_hash)

                # Calculate next depth dynamically
                next_depth = depth + 1

                # Higher priority for same-domain URLs
                parsed_current = urlparse(response.url)
                parsed_new = urlparse(new_url)
                priority = 0 if parsed_current.netloc == parsed_new.netloc else -1

                yield scrapy.Request(
                    new_url,
                    callback=self.parse,
                    errback=self.handle_error,
                    meta={'depth': next_depth, 'is_heavy': is_heavy},
                    priority=priority,
                    dont_filter=True  # We handle dedup manually
                )

    def handle_error(self, failure):
        """Handle errors gracefully."""
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

        logger.debug(f"Error {error_type} ({error_code}): {url[:80]}")

    def _save_batch(self):
        """Save batch of discovered URLs to Delta Lake."""
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
                logger.info(f"Saved {len(self.error_records)} errors to Delta Lake")
                self.error_records = []
            except Exception as e:
                logger.error(f"Failed to save errors: {e}")

    def closed(self, reason):
        """Save remaining data on spider close."""
        logger.info(f"Spider closing: {reason}")
        logger.info(f"Total unique URLs discovered: {len(self.url_hashes)}")

        # Save remaining batches
        self._save_batch()

        # Checkpoint Delta Lake
        self.delta.checkpoint()

        logger.info("All data saved successfully")

    def _graceful_shutdown(self, signum, frame):
        """Handle SIGINT/SIGTERM gracefully."""
        logger.warning(f"Received signal {signum}, saving data...")
        self._save_batch()
        self.delta.checkpoint()
        logger.info("Data saved, exiting")
