"""Scout Spider - High-speed reconnaissance and URL discovery.

REFACTORED BEHAVIOR:
- Extends BaseSpider for core functionality
- Overrides parse() for dual-queueing strategy:
  1. Queue HTML pages → JavaScriptSpider (for rendering)
  2. Queue ALL pages → Stage 2 (for analysis)
- Discard: Static assets only (images, CSS, JS files)
- Offsite: Log but don't follow
- Domain Filter: Only uconn.edu domains (from config.yml)

Performance Target: >100 URLs/sec discovery rate
"""

import logging
from datetime import datetime
from typing import Iterator

import scrapy
from scrapy.http import Response

from src.common.spider_config import get_spider_settings
from src.stage1.base_spider import BaseSpider

logger = logging.getLogger(__name__)


class ScoutSpider(BaseSpider):
    """High-speed reconnaissance spider - optimized for URL discovery.

    Inherits From:
        BaseSpider - Core spider functionality (URL extraction, Redis dedup, etc.)

    Overrides:
        parse() - Implements dual-queueing strategy
    """

    name = "scout"

    # Load custom settings from config.yml
    custom_settings = get_spider_settings("scout")

    # Static assets to discard (never queue)
    STATIC_EXTENSIONS = {
        # Images
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp', '.ico', '.tiff',
        # Stylesheets and scripts (files, not pages)
        '.css', '.js', '.map',
        # Media
        '.mp3', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4a', '.wav',
        # Archives
        '.zip', '.rar', '.7z', '.tar', '.gz', '.bz2',
        # Fonts
        '.woff', '.woff2', '.ttf', '.eot', '.otf',
        # Executables
        '.exe', '.dmg', '.pkg', '.deb', '.rpm',
    }

    def __init__(self, *args, **kwargs):
        """Initialize scout spider with aggressive settings."""
        super().__init__(*args, **kwargs)

        # Performance tracking specific to scout
        self.scout_stats = {
            'html_queued_js': 0,
            'pages_queued_stage2': 0,
            'static_discarded': 0,
        }

        logger.info(f"[SCOUT] Initialized with allowed_domains={self.allowed_domains}")

    def parse(self, response: Response) -> Iterator:
        """OVERRIDDEN: Dual-queueing strategy for scout spider.

        Flow:
        1. Check if content is HTML
        2. Extract all URLs (using BaseSpider._extract_urls)
        3. Categorize URLs:
           - External → Log via BaseSpider._create_offsite_item
           - Static assets → Discard
           - HTML pages → Queue for JS + Stage 2
           - Other pages → Queue for Stage 2 only
        4. Follow HTML links for continued discovery
        """
        # Fast content-type check
        content_type = response.headers.get('Content-Type', b'').decode('utf-8', errors='ignore').lower()

        # Only process HTML pages
        if 'text/html' not in content_type:
            logger.debug(f"[SCOUT] Non-HTML, skipping: {content_type} for {response.url[:80]}")
            return

        # Extract all URLs using BaseSpider method
        discovered_urls = self._extract_urls(response)

        # Process each discovered URL
        for url in discovered_urls:
            # Check if already processed (uses BaseSpider Redis dedup)
            url_hash = self._hash_url(url)

            # Use BaseSpider pipeline for batch Redis operations
            # But for scout, we need immediate dedup checks
            if self._is_duplicate_scout(url_hash):
                continue

            # Mark as seen
            self._mark_seen_scout(url_hash)

            # Categorize and route
            if self._is_external_url(url):
                # Offsite link - use BaseSpider method
                yield self._create_offsite_item(response, url)

                # Track metric
                if hasattr(self, 'skip_counters'):
                    self.skip_counters['offsite'] = self.skip_counters.get('offsite', 0) + 1

            elif self._is_static_asset(url):
                # Static asset - discard
                self.scout_stats['static_discarded'] += 1

                # Track in BaseSpider counters
                skip_reason = self._categorize_skip_reason(url)
                self._track_skip(url, skip_reason)

            else:
                # Page content - determine if HTML or other
                content_hint = self._guess_content_type(url)

                if content_hint == 'html':
                    # HTML page - queue for BOTH JavaScriptSpider AND Stage 2
                    yield self._queue_for_javascript_spider(url, response.url)
                    yield self._queue_for_stage2(url, response.url, content_hint)

                    self.scout_stats['html_queued_js'] += 1
                    self.scout_stats['pages_queued_stage2'] += 1

                else:
                    # Non-HTML page (PDF, DOC, etc.) - queue for Stage 2 only
                    yield self._queue_for_stage2(url, response.url, content_hint)
                    self.scout_stats['pages_queued_stage2'] += 1

                # Follow HTML links to continue discovery
                if content_hint == 'html':
                    depth = response.meta.get('depth', 0)
                    yield scrapy.Request(
                        url,
                        callback=self.parse,
                        errback=self.handle_error,
                        meta={'depth': depth + 1},
                        priority=0,
                        dont_filter=False,
                    )

        # Log progress periodically
        total_discovered = sum(self.scout_stats.values())
        if total_discovered % 100 == 0:
            self._log_scout_stats()

    def _is_duplicate_scout(self, url_hash: str) -> bool:
        """Check if URL has been seen before (immediate check for scout)."""
        return bool(self.redis_client.sismember(self.url_hashes_key, url_hash))

    def _mark_seen_scout(self, url_hash: str):
        """Mark URL as seen in Redis (immediate write for scout)."""
        self.redis_client.sadd(self.url_hashes_key, url_hash)

    def _is_static_asset(self, url: str) -> bool:
        """Check if URL is a static asset to discard."""
        url_lower = url.lower()
        return any(url_lower.endswith(ext) for ext in self.STATIC_EXTENSIONS)

    def _guess_content_type(self, url: str) -> str:
        """Guess content type from URL for routing decisions.

        Returns:
            'html': HTML page (default)
            'pdf': PDF document
            'doc': Office document
            'media': Media file (video/audio)
        """
        url_lower = url.lower()

        if '.pdf' in url_lower:
            return 'pdf'
        elif any(ext in url_lower for ext in ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']):
            return 'doc'
        elif any(ext in url_lower for ext in ['.mp4', '.avi', '.mov', '.mp3', '.wav']):
            return 'media'
        else:
            return 'html'  # Default assumption

    def _queue_for_javascript_spider(self, url: str, parent_url: str) -> dict:
        """Create queue item for JavaScriptSpider."""
        return {
            'url': url,
            'parent_url': parent_url,
            'priority': 1,
            'status': 'pending',
            'queued_at': datetime.now().isoformat(),
            'queued_by': 'scout',
            'target_spider': 'javascript',
        }

    def _queue_for_stage2(self, url: str, parent_url: str, content_hint: str) -> dict:
        """Create queue item for Stage 2 processing."""
        return {
            'url': url,
            'parent_url': parent_url,
            'content_hint': content_hint,
            'priority': 2 if content_hint == 'html' else 1,
            'status': 'pending',
            'queued_at': datetime.now().isoformat(),
            'queued_by': 'scout',
            'target_stage': 'stage2',
        }

    def _log_scout_stats(self):
        """Log scout-specific statistics."""
        logger.info(
            f"[SCOUT STATS] "
            f"HTML→JS: {self.scout_stats['html_queued_js']} | "
            f"Pages→Stage2: {self.scout_stats['pages_queued_stage2']} | "
            f"Static discarded: {self.scout_stats['static_discarded']}"
        )

    def closed(self, reason):
        """Called when spider closes - log final stats."""
        self._log_scout_stats()
        logger.info(f"[SCOUT] Spider closing: {reason}")

        # Call parent's closed method
        super().closed(reason)
