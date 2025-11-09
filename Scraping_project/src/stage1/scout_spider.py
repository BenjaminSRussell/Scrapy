"""Scout spider focused on fast URL discovery."""

import logging
from collections.abc import Iterable, Iterator
from datetime import datetime
from urllib.parse import urlparse

import scrapy
from scrapy.http import Response

from src.common.spider_config import get_spider_settings
from src.common.storage_manager import get_delta, get_postgres
from src.common.url_extractor import URLExtractor
from src.common.url_processor import should_follow_url
from src.lakehouse import SeedManager
from src.stage1.base_spider import BaseSpider
from src.stage1.sitemap_parser import discover_sitemaps_sync


# Backward compatibility aliases
def get_delta_manager(*args, **kwargs):
    """Proxy to StorageManager delta backend (patched in tests)."""
    return get_delta()


def get_postgres_manager(*args, **kwargs):
    """Proxy to StorageManager postgres backend (patched in tests)."""
    return get_postgres()


# Legacy proxies removed - use StorageManager directly
_core_get_delta_manager = get_delta_manager
_core_get_postgres_manager = get_postgres_manager

logger = logging.getLogger(__name__)


class ScoutSpider(BaseSpider):
    """High-speed reconnaissance spider."""

    name = "scout"

    # Load custom settings from config.yml
    custom_settings = get_spider_settings("scout")

    # NOTE: Static asset filtering is now handled by src.common.url_processor.should_follow_url()
    # This centralizes URL filtering logic and removes duplication

    def __init__(self, *args, **kwargs):
        """Initialize scout spider with aggressive settings."""
        super().__init__(*args, **kwargs)

        # Performance tracking specific to scout
        self.scout_stats = {
            "html_queued_js": 0,
            "pages_queued_stage2": 0,
            "static_discarded": 0,
            "urls_added_to_seeds": 0,  # NEW: Track seed expansion
        }

        self._discovery_response: Response | None = None
        self._url_extractor: URLExtractor | None = None

        # Load configuration for seed expansion settings
        from src.common.config_manager import ConfigManager

        config = ConfigManager.get_instance().config

        # Access Pydantic model attributes (not .get() method!)
        # Enable aggressive seed URL expansion from config
        self.expand_seeds = getattr(config.stage1, "expand_seeds", True)
        self.parse_sitemaps = getattr(config.stage1, "parse_sitemaps", True)
        self.aggressive_collection = getattr(config.stage1, "aggressive_collection", True)

        # Initialize SeedManager for centralized seeding operations
        # This is the ONLY way spiders should add URLs to seeds or queues
        self.seed_manager = SeedManager(self.delta)

        logger.info(f"[SCOUT] Initialized with allowed_domains={self.allowed_domains}")
        logger.info(f"[SCOUT] Seed expansion enabled: {self.expand_seeds}")
        logger.info(f"[SCOUT] Sitemap parsing enabled: {self.parse_sitemaps}")
        logger.info(f"[SCOUT] Aggressive collection mode: {self.aggressive_collection}")

        # NEW: Auto-discover sitemaps and add to seeds on initialization
        if self.parse_sitemaps and hasattr(self, "start_urls") and self.start_urls:
            self._discover_and_add_sitemap_urls()

    def parse(self, response: Response) -> Iterator:
        """Dual-queue discovery handler using batch Redis operations."""
        # Fast content-type check
        content_type = response.headers.get("Content-Type", b"").decode("utf-8", errors="ignore").lower()

        # Only process HTML pages
        if "text/html" not in content_type:
            logger.debug(f"[SCOUT] Non-HTML, skipping: {content_type} for {response.url[:80]}")
            return

        # Extract all URLs using BaseSpider method
        discovered_urls = self._extract_urls(response)

        # Record this page discovery for Stage 1 metrics
        url_hash = self._hash_url(response.url)
        depth = response.meta.get("depth", 0)
        self._record_successful_page(
            response=response,
            url_hash=url_hash,
            depth=depth,
            content_size=len(response.body),
            url_count=len(discovered_urls),
            is_heavy=len(response.body) > 100000,  # >100KB
            requires_js=False,  # Scout doesn't detect JS yet
        )

        if not discovered_urls:
            return

        # Check all URLs at once in Redis via BaseSpider helper
        new_urls, _ = self._deduplicate_urls(discovered_urls)

        # Process new URLs only
        depth = response.meta.get("depth", 0)

        # NEW: Collect ALL URLs for seed expansion (even external/static)
        urls_to_add_to_seeds = []

        for url in new_urls:
            # Categorize and route
            if self._is_external_url(url):
                # Offsite link - use BaseSpider method
                yield self._create_offsite_item(response, url)

                # Track metric
                if hasattr(self, "skip_counters"):
                    self.skip_counters["offsite"] = self.skip_counters.get("offsite", 0) + 1

                # NEW: Still add to seeds for potential future cross-domain crawling
                urls_to_add_to_seeds.append(url)

            elif not should_follow_url(url):
                # Static asset - discard from immediate crawl but add to seeds
                # Using centralized url_processor filtering logic
                self.scout_stats["static_discarded"] += 1

                # Track in BaseSpider counters
                skip_reason = self._categorize_skip_reason(url)
                self._track_skip(url, skip_reason)

                # NEW: Even static assets go to seeds (may have updated content later)
                urls_to_add_to_seeds.append(url)

            else:
                # Page content - determine if HTML or other
                content_hint = self._guess_content_type(url)

                if content_hint == "html":
                    # HTML page - queue for JavaScriptSpider and Stage 2
                    yield self._queue_for_javascript_spider(url, response.url)
                    yield self._queue_for_stage2(url, response.url, content_hint)

                    self.scout_stats["html_queued_js"] += 1
                    self.scout_stats["pages_queued_stage2"] += 1

                    # Follow HTML links to continue discovery
                    yield scrapy.Request(
                        url,
                        callback=self.parse,
                        errback=self.handle_error,
                        meta={"depth": depth + 1},
                        priority=0,
                        dont_filter=False,
                    )

                    # NEW: Add to seeds for future crawling
                    urls_to_add_to_seeds.append(url)

                else:
                    # Non-HTML page (PDF, DOC, etc.) - queue for Stage 2 only
                    yield self._queue_for_stage2(url, response.url, content_hint)
                    self.scout_stats["pages_queued_stage2"] += 1

                    # NEW: Add to seeds
                    urls_to_add_to_seeds.append(url)

        # NEW: Batch add all discovered URLs to seed_urls for continuous expansion
        if urls_to_add_to_seeds and self.expand_seeds:
            self._add_urls_to_seeds(urls_to_add_to_seeds, response.url)
            self.scout_stats["urls_added_to_seeds"] += len(urls_to_add_to_seeds)

        # Log progress periodically
        total_discovered = sum(self.scout_stats.values())
        if total_discovered % 100 == 0:
            self._log_scout_stats()

    def _initialize_discovery(self, response: Response) -> None:
        """Prepare discovery helpers for a given response."""

        self._discovery_response = response
        self._url_extractor = URLExtractor(base_url=response.url, allowed_domains=self.allowed_domains)

    def discover_all_urls(self) -> Iterable[str]:
        """Return all URLs discovered from the initialized response."""

        if self._discovery_response is None or self._url_extractor is None:
            raise RuntimeError("Call _initialize_discovery before discovering URLs")

        discovered = set(self._url_extractor.discover_all_urls(self._discovery_response))
        discovered.update(self._extract_sitemap_urls())
        return sorted(discovered)

    def _extract_sitemap_urls(self) -> set[str]:
        """Synthesize sitemap and robots URLs for the current domain."""

        if self._discovery_response is None:
            return set()

        parsed = urlparse(self._discovery_response.url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        return {
            f"{base}/sitemap.xml",
            f"{base}/sitemap_index.xml",
            f"{base}/sitemap-index.xml",
        }

    def _detect_js_requirement(self, response: Response) -> bool:  # type: ignore[override]
        """Wrapper returning boolean JS requirement (tests expect bool)."""

        requires_js, _ = super()._detect_js_requirement(response)
        return requires_js

    def _guess_content_type(self, url: str) -> str:
        """Guess content type from URL for routing decisions."""
        url_lower = url.lower()

        if ".pdf" in url_lower:
            return "pdf"
        elif any(ext in url_lower for ext in [".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"]):
            return "doc"
        elif any(ext in url_lower for ext in [".mp4", ".avi", ".mov", ".mp3", ".wav"]):
            return "media"
        else:
            return "html"  # Default assumption

    def _queue_for_javascript_spider(self, url: str, parent_url: str) -> dict:
        """Create queue item for JavaScriptSpider."""
        return {
            "url": url,
            "parent_url": parent_url,
            "priority": 1,
            "status": "pending",
            "queued_at": datetime.now().isoformat(),
            "queued_by": "scout",
            "target_spider": "javascript",
        }

    def _queue_for_stage2(self, url: str, parent_url: str, content_hint: str) -> dict:
        """Create queue item for Stage 2 processing."""
        return {
            "url": url,
            "parent_url": parent_url,
            "content_hint": content_hint,
            "priority": 2 if content_hint == "html" else 1,
            "status": "pending",
            "queued_at": datetime.now().isoformat(),
            "queued_by": "scout",
            "target_stage": "stage2",
        }

    def _discover_and_add_sitemap_urls(self) -> None:
        """
        Discover sitemap URLs and add them to seed_urls for comprehensive crawling.

        This runs at spider initialization to bootstrap seed URL expansion from sitemaps.
        Sitemaps often contain hundreds or thousands of URLs that we'd otherwise miss.
        """
        if not self.start_urls:
            return

        try:
            base_url = self.start_urls[0]
            logger.info(f"[SCOUT] Discovering sitemap URLs from {base_url}")

            # Use synchronous sitemap parser
            sitemap_urls = discover_sitemaps_sync(base_url, timeout=30)

            if sitemap_urls:
                logger.info(f"[SCOUT] Found {len(sitemap_urls)} URLs from sitemaps")
                # Add all sitemap URLs to seeds
                self._add_urls_to_seeds(sitemap_urls, source_url=f"{base_url}/sitemap.xml")
            else:
                logger.info(f"[SCOUT] No sitemap URLs discovered for {base_url}")

        except Exception as e:
            logger.warning(f"[SCOUT] Sitemap discovery failed: {e}")

    def _add_urls_to_seeds(self, urls: list[str], source_url: str) -> None:
        """
        Add discovered URLs to seed_urls AND uconn_urls tables for continuous expansion.

        This method now delegates to SeedManager for centralized, idempotent seeding.

        Args:
            urls: List of URLs to add to seed_urls
            source_url: The parent URL where these were discovered
        """
        if not urls:
            return

        try:
            # Use SeedManager for centralized seeding logic
            result = self.seed_manager.add_urls_to_seeds(
                urls=urls,
                source_url=source_url,
                source_spider=self.name,
                write_uconn_urls=True,  # Scout always writes to uconn_urls
                enqueue_stage2=False,  # Scout doesn't enqueue for Stage 2 here (done separately)
            )

            logger.info(
                f"[SCOUT] SeedManager results: seeds={result['seed_inserted']}, uconn={result['uconn_inserted']}"
            )

        except Exception as e:
            logger.error(f"[SCOUT] Failed to add URLs via SeedManager: {e}", exc_info=True)

    def _log_scout_stats(self):
        """Log scout-specific statistics."""
        logger.info(
            f"[SCOUT STATS] "
            f"HTML→JS: {self.scout_stats['html_queued_js']} | "
            f"Pages→Stage2: {self.scout_stats['pages_queued_stage2']} | "
            f"Static discarded: {self.scout_stats['static_discarded']} | "
            f"Seeds added: {self.scout_stats['urls_added_to_seeds']}"
        )

    def closed(self, reason):
        """Called when spider closes - log final stats."""
        self._log_scout_stats()
        logger.info(f"[SCOUT] Spider closing: {reason}")

        # Call parent's closed method
        super().closed(reason)
