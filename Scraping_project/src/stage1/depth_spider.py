"""Depth Spider - Intensive crawling mode when regular queues are idle.

This spider activates when the priority queue drops below a threshold,
enabling deep exploration of the site to discover URLs that were missed
during initial scouting.

Features:
- Deeper crawl depth (up to 50 levels)
- Re-scrapes existing URLs to find new links
- Follows more aggressive link discovery patterns
- Respects the same domain restrictions as scout spider
"""

import logging
from collections.abc import Iterator
from datetime import datetime, timedelta

import scrapy
from scrapy.http import Response

from src.common.config_manager import ConfigManager
from src.common.spider_config import get_spider_settings
from src.common.storage_manager import get_delta, get_redis
from src.common.url_processor import should_follow_url
from src.lakehouse import SeedManager
from src.stage1.base_spider import BaseSpider

logger = logging.getLogger(__name__)


class DepthSpider(BaseSpider):
    """Deep crawling spider for comprehensive URL discovery."""

    name = "depth"

    # Load custom settings from config.yml
    custom_settings = get_spider_settings("deep_dive")

    def __init__(self, *args, **kwargs):
        """Initialize depth spider with intensive settings."""
        super().__init__(*args, **kwargs)

        # Load configuration
        config = ConfigManager.get_instance().config
        depth_config = config.get("stage1", {}).get("depth_spider", {})

        # Depth spider configuration
        self.max_depth = depth_config.get("max_depth", 50)
        self.rescrape_interval_hours = depth_config.get("rescrape_interval_hours", 24)
        self.enabled = depth_config.get("enabled", True)

        # Get Redis and Delta Lake managers
        self.redis_client = get_redis()
        self.delta = get_delta()

        # Initialize SeedManager for centralized seeding operations
        self.seed_manager = SeedManager(self.delta)

        # Tracking
        self.depth_stats = {
            "urls_rescraped": 0,
            "new_urls_found": 0,
            "urls_added_to_seeds": 0,
        }

        logger.info(f"[DEPTH] Initialized with max_depth={self.max_depth}")
        logger.info(f"[DEPTH] Re-scrape interval: {self.rescrape_interval_hours} hours")
        logger.info(f"[DEPTH] Enabled: {self.enabled}")

    def start_requests(self) -> Iterator[scrapy.Request]:
        """Generate initial requests for depth crawling.

        Priority order:
        1. URLs from seed_urls that haven't been crawled recently
        2. Existing URLs that haven't been re-scraped in X hours
        3. URLs from sitemap that were skipped
        """
        if not self.enabled:
            logger.warning("[DEPTH] Depth spider is disabled in config")
            return

        # Get URLs to re-scrape
        urls_to_crawl = self._get_rescrape_candidates()

        if not urls_to_crawl:
            logger.warning("[DEPTH] No URLs to re-scrape. Check seed_urls and Redis.")
            return

        logger.info(f"[DEPTH] Starting depth crawl with {len(urls_to_crawl)} URLs")

        for url in urls_to_crawl:
            yield scrapy.Request(
                url,
                callback=self.parse,
                errback=self.handle_error,
                meta={"depth": 0, "rescrape": True},
                priority=0,
                dont_filter=True,  # Allow re-scraping
            )

    def _get_rescrape_candidates(self) -> list[str]:
        """
        Get URLs that should be re-scraped.

        Strategy:
        1. Check Redis for URLs last crawled > X hours ago
        2. Get seed URLs that haven't been fully explored
        3. Prioritize high-value URLs (faculty, research, etc.)
        """
        try:
            # Get seed URLs from Delta Lake
            seed_records = self.delta.read("seed_urls")
            all_seed_urls = [record["url"] for record in seed_records]

            logger.info(f"[DEPTH] Found {len(all_seed_urls)} seed URLs")

            # Check which URLs need re-scraping based on last crawl time
            urls_to_crawl = []
            current_time = datetime.now()
            rescrape_threshold = current_time - timedelta(hours=self.rescrape_interval_hours)

            for url in all_seed_urls[:5000]:  # Limit to first 5000 to avoid overload
                # Check Redis for last crawl time
                last_crawl_key = f"depth:last_crawl:{self._hash_url(url)}"
                last_crawl_str = self.redis_client.get(last_crawl_key)

                if last_crawl_str:
                    # Parse timestamp
                    try:
                        last_crawl = datetime.fromisoformat(last_crawl_str.decode("utf-8"))
                        if last_crawl > rescrape_threshold:
                            # Recently crawled, skip
                            continue
                    except Exception:
                        pass  # Invalid timestamp, re-scrape anyway

                # URL needs re-scraping
                urls_to_crawl.append(url)

                # Limit batch size
                if len(urls_to_crawl) >= 1000:
                    break

            logger.info(f"[DEPTH] Selected {len(urls_to_crawl)} URLs for re-scraping")
            return urls_to_crawl

        except Exception as e:
            logger.error(f"[DEPTH] Failed to get rescrape candidates: {e}", exc_info=True)
            return []

    def parse(self, response: Response) -> Iterator:
        """Parse response and extract all possible URLs."""
        # Fast content-type check
        content_type = response.headers.get("Content-Type", b"").decode("utf-8", errors="ignore").lower()

        if "text/html" not in content_type:
            logger.debug(f"[DEPTH] Non-HTML, skipping: {content_type}")
            return

        # Update last crawl time in Redis
        url_hash = self._hash_url(response.url)
        last_crawl_key = f"depth:last_crawl:{url_hash}"
        self.redis_client.set(last_crawl_key, datetime.now().isoformat(), ex=86400 * 7)  # 7 day TTL

        # Track this as a re-scrape
        if response.meta.get("rescrape"):
            self.depth_stats["urls_rescraped"] += 1

        # Extract all URLs
        discovered_urls = self._extract_urls(response)

        if not discovered_urls:
            return

        # Record successful page
        depth = response.meta.get("depth", 0)
        self._record_successful_page(
            response=response,
            url_hash=url_hash,
            depth=depth,
            content_size=len(response.body),
            url_count=len(discovered_urls),
            is_heavy=len(response.body) > 100000,
            requires_js=False,
        )

        # Deduplicate URLs
        new_urls, _ = self._deduplicate_urls(discovered_urls)

        if new_urls:
            logger.info(f"[DEPTH] Found {len(new_urls)} new URLs at depth {depth} from {response.url[:80]}")
            self.depth_stats["new_urls_found"] += len(new_urls)

        # Add ALL new URLs to seed_urls for future crawling
        urls_to_add_to_seeds = []

        for url in new_urls:
            # Skip external URLs (offsite)
            if self._is_external_url(url):
                # Yield offsite item
                yield self._create_offsite_item(response, url)
                continue

            # Skip static assets (but still add to seeds)
            # Using centralized url_processor filtering logic
            if not should_follow_url(url):
                skip_reason = self._categorize_skip_reason(url)
                self._track_skip(url, skip_reason)
                urls_to_add_to_seeds.append(url)
                continue

            # HTML page - queue for Stage 2 and follow
            content_hint = self._guess_content_type(url)

            if content_hint == "html":
                # Follow the link if within depth limit
                if depth < self.max_depth:
                    yield scrapy.Request(
                        url,
                        callback=self.parse,
                        errback=self.handle_error,
                        meta={"depth": depth + 1, "rescrape": False},
                        priority=0,
                        dont_filter=False,
                    )

            # Add to seeds
            urls_to_add_to_seeds.append(url)

        # Batch add URLs to seed_urls
        if urls_to_add_to_seeds:
            self._add_urls_to_seeds(urls_to_add_to_seeds, response.url)
            self.depth_stats["urls_added_to_seeds"] += len(urls_to_add_to_seeds)

        # Log progress
        if self.depth_stats["urls_rescraped"] % 100 == 0:
            self._log_depth_stats()

    def _guess_content_type(self, url: str) -> str:
        """Guess content type from URL."""
        url_lower = url.lower()
        if ".pdf" in url_lower:
            return "pdf"
        elif any(ext in url_lower for ext in [".doc", ".docx", ".xls", ".xlsx"]):
            return "doc"
        else:
            return "html"

    def _add_urls_to_seeds(self, urls: list[str], source_url: str) -> None:
        """
        Add discovered URLs to seed_urls table.

        This method now delegates to SeedManager for centralized, idempotent seeding.
        """
        if not urls:
            return

        try:
            # Use SeedManager for centralized seeding logic
            result = self.seed_manager.add_urls_to_seeds(
                urls=urls,
                source_url=source_url,
                source_spider=self.name,
                write_uconn_urls=True,  # Depth spider writes to uconn_urls
                enqueue_stage2=False,  # Depth spider doesn't enqueue for Stage 2
            )

            logger.info(
                f"[DEPTH] SeedManager results: seeds={result['seed_inserted']}, uconn={result['uconn_inserted']}"
            )

        except Exception as e:
            logger.error(f"[DEPTH] Failed to add URLs via SeedManager: {e}", exc_info=True)

    def _log_depth_stats(self):
        """Log depth spider statistics."""
        logger.info(
            f"[DEPTH STATS] "
            f"Re-scraped: {self.depth_stats['urls_rescraped']} | "
            f"New URLs: {self.depth_stats['new_urls_found']} | "
            f"Added to seeds: {self.depth_stats['urls_added_to_seeds']}"
        )

    def closed(self, reason):
        """Called when spider closes."""
        self._log_depth_stats()
        logger.info(f"[DEPTH] Spider closing: {reason}")
        super().closed(reason)
