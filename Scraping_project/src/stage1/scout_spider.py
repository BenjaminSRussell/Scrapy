"""Scout spider focused on fast URL discovery."""

import logging
from collections.abc import Iterable, Iterator
from datetime import datetime
from urllib.parse import urlparse

import scrapy
from scrapy.http import Response

from src.common.delta_lake import get_delta_manager as _core_get_delta_manager
from src.common.postgres_manager import (
    get_postgres_manager as _core_get_postgres_manager,
)
from src.common.spider_config import get_spider_settings
from src.common.url_extractor import URLExtractor
from src.stage1.base_spider import BaseSpider

logger = logging.getLogger(__name__)


def get_delta_manager(*args, **kwargs):
    """Proxy to the shared delta manager factory (patched in tests)."""

    return _core_get_delta_manager(*args, **kwargs)


def get_postgres_manager(*args, **kwargs):
    """Proxy to the shared postgres manager factory (patched in tests)."""

    return _core_get_postgres_manager(*args, **kwargs)


class ScoutSpider(BaseSpider):
    """High-speed reconnaissance spider."""

    name = "scout"

    # Load custom settings from config.yml
    custom_settings = get_spider_settings("scout")

    # Static assets to discard (never queue)
    STATIC_EXTENSIONS = {
        # Images
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".svg",
        ".webp",
        ".ico",
        ".tiff",
        # Stylesheets and scripts (files, not pages)
        ".css",
        ".js",
        ".map",
        # Media
        ".mp3",
        ".mp4",
        ".avi",
        ".mov",
        ".wmv",
        ".flv",
        ".webm",
        ".m4a",
        ".wav",
        # Archives
        ".zip",
        ".rar",
        ".7z",
        ".tar",
        ".gz",
        ".bz2",
        # Fonts
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".otf",
        # Executables
        ".exe",
        ".dmg",
        ".pkg",
        ".deb",
        ".rpm",
    }

    def __init__(self, *args, **kwargs):
        """Initialize scout spider with aggressive settings."""
        super().__init__(*args, **kwargs)

        # Performance tracking specific to scout
        self.scout_stats = {
            "html_queued_js": 0,
            "pages_queued_stage2": 0,
            "static_discarded": 0,
        }

        self._discovery_response: Response | None = None
        self._url_extractor: URLExtractor | None = None

        logger.info(f"[SCOUT] Initialized with allowed_domains={self.allowed_domains}")

    def parse(self, response: Response) -> Iterator:
        """Dual-queue discovery handler using batch Redis operations."""
        # Fast content-type check
        content_type = (
            response.headers.get("Content-Type", b"")
            .decode("utf-8", errors="ignore")
            .lower()
        )

        # Only process HTML pages
        if "text/html" not in content_type:
            logger.debug(
                f"[SCOUT] Non-HTML, skipping: {content_type} for {response.url[:80]}"
            )
            return

        # Extract all URLs using BaseSpider method
        discovered_urls = self._extract_urls(response)

        if not discovered_urls:
            return

        # Check all URLs at once in Redis
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

        # Process new URLs only
        depth = response.meta.get("depth", 0)

        for url in new_urls:
            # Categorize and route
            if self._is_external_url(url):
                # Offsite link - use BaseSpider method
                yield self._create_offsite_item(response, url)

                # Track metric
                if hasattr(self, "skip_counters"):
                    self.skip_counters["offsite"] = (
                        self.skip_counters.get("offsite", 0) + 1
                    )

            elif self._is_static_asset(url):
                # Static asset - discard
                self.scout_stats["static_discarded"] += 1

                # Track in BaseSpider counters
                skip_reason = self._categorize_skip_reason(url)
                self._track_skip(url, skip_reason)

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

                else:
                    # Non-HTML page (PDF, DOC, etc.) - queue for Stage 2 only
                    yield self._queue_for_stage2(url, response.url, content_hint)
                    self.scout_stats["pages_queued_stage2"] += 1

        # Log progress periodically
        total_discovered = sum(self.scout_stats.values())
        if total_discovered % 100 == 0:
            self._log_scout_stats()

    def _initialize_discovery(self, response: Response) -> None:
        """Prepare discovery helpers for a given response."""

        self._discovery_response = response
        self._url_extractor = URLExtractor(
            base_url=response.url, allowed_domains=self.allowed_domains
        )

    def discover_all_urls(self) -> Iterable[str]:
        """Return all URLs discovered from the initialized response."""

        if self._discovery_response is None or self._url_extractor is None:
            raise RuntimeError("Call _initialize_discovery before discovering URLs")

        discovered = set(
            self._url_extractor.discover_all_urls(self._discovery_response)
        )
        discovered.update(self._extract_sitemap_urls())
        return sorted(discovered)

    def _extract_sitemap_urls(self) -> set[str]:
        """Synthesize sitemap and robots URLs for the current domain."""

        if self._discovery_response is None:
            return set()

        parsed = urlparse(self._discovery_response.url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        return {
            f"{base}/robots.txt",
            f"{base}/sitemap.xml",
            f"{base}/sitemap_index.xml",
            f"{base}/sitemap-index.xml",
        }

    def _has_ignored_extension(self, url: str) -> bool:
        """Return True when the URL corresponds to a static asset."""

        lowered = url.lower()
        return any(lowered.endswith(ext) for ext in self.STATIC_EXTENSIONS)

    def _detect_js_requirement(self, response: Response) -> bool:  # type: ignore[override]
        """Wrapper returning boolean JS requirement (tests expect bool)."""

        requires_js, _ = super()._detect_js_requirement(response)
        return requires_js

    def _is_static_asset(self, url: str) -> bool:
        """Check if URL is a static asset to discard."""
        url_lower = url.lower()
        return any(url_lower.endswith(ext) for ext in self.STATIC_EXTENSIONS)

    def _guess_content_type(self, url: str) -> str:
        """Guess content type from URL for routing decisions."""
        url_lower = url.lower()

        if ".pdf" in url_lower:
            return "pdf"
        elif any(
            ext in url_lower
            for ext in [".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"]
        ):
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
