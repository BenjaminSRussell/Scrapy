"""Scout spider focused on fast URL discovery."""

import logging
from collections.abc import Iterable, Iterator
from datetime import datetime
from urllib.parse import urlparse

import scrapy
from scrapy.http import Response

from src.stage1.middlewares.spider_config import get_spider_settings
from src.utils.delta import get_delta
# get_postgres removed - implement in Phase 6
from src.stage1.processors.url_extractor import URLExtractor
from src.stage1.processors.url_processor import should_follow_url
from src.lakehouse import SeedManager
from src.stage1.base_spider import BaseSpider
from src.stage1.sitemap_parser import discover_sitemaps_sync

def get_delta_manager(*args, **kwargs):
    return get_delta()

def get_postgres_manager(*args, **kwargs):
    return get_postgres()

_core_get_delta_manager = get_delta_manager
_core_get_postgres_manager = get_postgres_manager

logger = logging.getLogger(__name__)

class ScoutSpider(BaseSpider):

    name = "scout"

    custom_settings = get_spider_settings("scout")

    # NOTE: Static asset filtering is now handled by src.common.url_processor.should_follow_url()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.scout_stats = {
            "html_queued_js": 0,
            "pages_queued_stage2": 0,
            "static_discarded": 0,
            "urls_added_to_seeds": 0,
        }

        self._discovery_response: Response | None = None
        self._url_extractor: URLExtractor | None = None

        from src.core.config import get_config

        config = get_config()

        self.expand_seeds = config.get("stages.stage1.expand_seeds", True)
        self.parse_sitemaps = config.get("stages.stage1.parse_sitemaps", True)
        self.aggressive_collection = config.get("stages.stage1.aggressive_collection", True)

        self.seed_manager = SeedManager(self.delta)

        logger.info(f"[SCOUT] Initialized with allowed_domains={self.allowed_domains}")
        logger.info(f"[SCOUT] Seed expansion enabled: {self.expand_seeds}")
        logger.info(f"[SCOUT] Sitemap parsing enabled: {self.parse_sitemaps}")
        logger.info(f"[SCOUT] Aggressive collection mode: {self.aggressive_collection}")

        if self.parse_sitemaps and hasattr(self, "start_urls") and self.start_urls:
            self._discover_and_add_sitemap_urls()

    def parse(self, response: Response) -> Iterator:
        content_type = response.headers.get("Content-Type", b"").decode("utf-8", errors="ignore").lower()

        if "text/html" not in content_type:
            logger.debug(f"[SCOUT] Non-HTML, skipping: {content_type} for {response.url[:80]}")
            return

        discovered_urls = self._extract_urls(response)

        url_hash = self._hash_url(response.url)
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

        if not discovered_urls:
            return

        new_urls, _ = self._deduplicate_urls(discovered_urls)

        depth = response.meta.get("depth", 0)

        urls_to_add_to_seeds = []

        for url in new_urls:
            if self._is_external_url(url):
                yield self._create_offsite_item(response, url)

                if hasattr(self, "skip_counters"):
                    self.skip_counters["offsite"] = self.skip_counters.get("offsite", 0) + 1

                urls_to_add_to_seeds.append(url)

            elif not should_follow_url(url):
                self.scout_stats["static_discarded"] += 1

                skip_reason = self._categorize_skip_reason(url)
                self._track_skip(url, skip_reason)

                urls_to_add_to_seeds.append(url)

            else:
                content_hint = self._guess_content_type(url)

                if content_hint == "html":
                    yield self._queue_for_javascript_spider(url, response.url)
                    yield self._queue_for_stage2(url, response.url, content_hint)

                    self.scout_stats["html_queued_js"] += 1
                    self.scout_stats["pages_queued_stage2"] += 1

                    yield scrapy.Request(
                        url,
                        callback=self.parse,
                        errback=self.handle_error,
                        meta={"depth": depth + 1},
                        priority=0,
                        dont_filter=False,
                    )

                    urls_to_add_to_seeds.append(url)

                else:
                    yield self._queue_for_stage2(url, response.url, content_hint)
                    self.scout_stats["pages_queued_stage2"] += 1

                    urls_to_add_to_seeds.append(url)

        if urls_to_add_to_seeds and self.expand_seeds:
            self._add_urls_to_seeds(urls_to_add_to_seeds, response.url)
            self.scout_stats["urls_added_to_seeds"] += len(urls_to_add_to_seeds)

        total_discovered = sum(self.scout_stats.values())
        if total_discovered % 100 == 0:
            self._log_scout_stats()

    def _initialize_discovery(self, response: Response) -> None:

        self._discovery_response = response
        self._url_extractor = URLExtractor(base_url=response.url, allowed_domains=self.allowed_domains)

    def discover_all_urls(self) -> Iterable[str]:

        if self._discovery_response is None or self._url_extractor is None:
            raise RuntimeError("Call _initialize_discovery before discovering URLs")

        discovered = set(self._url_extractor.discover_all_urls(self._discovery_response))
        discovered.update(self._extract_sitemap_urls())
        return sorted(discovered)

    def _extract_sitemap_urls(self) -> set[str]:

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

        requires_js, _ = super()._detect_js_requirement(response)
        return requires_js

    def _guess_content_type(self, url: str) -> str:
        url_lower = url.lower()

        if ".pdf" in url_lower:
            return "pdf"
        elif any(ext in url_lower for ext in [".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"]):
            return "doc"
        elif any(ext in url_lower for ext in [".mp4", ".avi", ".mov", ".mp3", ".wav"]):
            return "media"
        else:
            return "html"

    def _queue_for_javascript_spider(self, url: str, parent_url: str) -> dict:
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
        if not self.start_urls:
            return

        try:
            base_url = self.start_urls[0]
            logger.info(f"[SCOUT] Discovering sitemap URLs from {base_url}")

            sitemap_urls = discover_sitemaps_sync(base_url, timeout=30)

            if sitemap_urls:
                logger.info(f"[SCOUT] Found {len(sitemap_urls)} URLs from sitemaps")
                self._add_urls_to_seeds(sitemap_urls, source_url=f"{base_url}/sitemap.xml")
            else:
                logger.info(f"[SCOUT] No sitemap URLs discovered for {base_url}")

        except Exception as e:
            logger.warning(f"[SCOUT] Sitemap discovery failed: {e}")

    def _add_urls_to_seeds(self, urls: list[str], source_url: str) -> None:
        if not urls:
            return

        try:
            result = self.seed_manager.add_urls_to_seeds(
                urls=urls,
                source_url=source_url,
                source_spider=self.name,
                write_uconn_urls=True,
                enqueue_stage2=False,
            )

            logger.info(
                f"[SCOUT] SeedManager results: seeds={result['seed_inserted']}, uconn={result['uconn_inserted']}"
            )

        except Exception as e:
            logger.error(f"[SCOUT] Failed to add URLs via SeedManager: {e}", exc_info=True)

    def _log_scout_stats(self):
        logger.info(
            f"[SCOUT STATS] "
            f"HTML→JS: {self.scout_stats['html_queued_js']} | "
            f"Pages→Stage2: {self.scout_stats['pages_queued_stage2']} | "
            f"Static discarded: {self.scout_stats['static_discarded']} | "
            f"Seeds added: {self.scout_stats['urls_added_to_seeds']}"
        )

    def closed(self, reason):
        self._log_scout_stats()
        logger.info(f"[SCOUT] Spider closing: {reason}")

        super().closed(reason)
