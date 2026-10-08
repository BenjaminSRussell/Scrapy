"""Scout spider focused on fast URL discovery."""

import logging
from collections.abc import Iterable, Iterator
from datetime import datetime
from urllib.parse import urlparse

import scrapy
from scrapy.http import HtmlResponse, Response

from src.stage1.content_policy import classify_response, count_skipped
from src.stage1.middlewares.spider_config import get_spider_settings
from src.utils.delta import get_delta
from src.stage1.processors.url_extractor import URLExtractor
from src.stage1.processors.url_processor import should_follow_url
from src.lakehouse import SeedManager
from src.stage1.base_spider import BaseSpider
from src.stage1.sitemap_parser import discover_sitemaps_sync
from src.stage1.section_noise import SectionNoiseTracker, section_of

try:
    from src.scrapy_prometheus import URLS_SKIPPED
except Exception:  # prometheus_client missing
    URLS_SKIPPED = None

def get_delta_manager(*args, **kwargs):
    return get_delta()

_core_get_delta_manager = get_delta_manager

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
        decision = classify_response(response)  # #662: no binary into HTML parsing
        if not decision.parse_html and decision.reason == "empty_body":
            # Empty bodies keep the #199 accounting (skip_counters + urls_skipped_total).
            self._skip_response(response, "empty_body")
            return
        if not decision.parse_html:
            count_skipped("scout", decision.reason)
            logger.debug(f"[SCOUT] Not parsing ({decision.reason}) {response.url[:80]}")
            return

        empty_reason = self._empty_body_reason(response)
        if empty_reason:
            # Zero-byte bodies and blank shells (no text, no links/assets) carry
            # no URLs worth queueing; don't let them feed Stage 2 (#199).
            self._skip_response(response, empty_reason)
            return

        discovered_urls = self._extract_urls(response)

        url_hash = self._hash_url(response.url)
        depth = response.meta.get("depth", 0)
        self._record_discovery(
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

        # Noisy-section adaptation (#26): judged on earlier pages of this section.
        noise = self._section_noise()
        section = section_of(response.url)
        noisy = noise.is_noisy(section)
        html_links = low_links = followed = 0

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
                    low_value = noise.enabled and noise.is_low_value(url)
                    html_links += 1
                    low_links += low_value
                    if not noise.should_follow(section, url, followed, low_value):
                        continue
                    followed += 1

                    yield self._queue_for_javascript_spider(url, response.url)
                    yield self._queue_for_stage2(url, response.url, content_hint)

                    self.scout_stats["html_queued_js"] += 1
                    self.scout_stats["pages_queued_stage2"] += 1

                    yield scrapy.Request(
                        url,
                        callback=self.parse,
                        errback=self.handle_error,
                        meta={"depth": depth + 1},
                        priority=-1 if noisy else 0,
                        dont_filter=False,
                    )

                    urls_to_add_to_seeds.append(url)

                else:
                    yield self._queue_for_stage2(url, response.url, content_hint)
                    self.scout_stats["pages_queued_stage2"] += 1

                    urls_to_add_to_seeds.append(url)

        noise.record_page(section, html_links, low_links)

        if urls_to_add_to_seeds and self.expand_seeds:
            self._add_urls_to_seeds(urls_to_add_to_seeds, response.url)
            self.scout_stats["urls_added_to_seeds"] += len(urls_to_add_to_seeds)

        total_discovered = sum(self.scout_stats.values())
        if total_discovered % 100 == 0:
            self._log_scout_stats()

    def _section_noise(self) -> SectionNoiseTracker:
        tracker: SectionNoiseTracker | None = getattr(self, "_noise_tracker", None)
        if tracker is None:
            from src.core.config import get_config

            try:
                tracker = SectionNoiseTracker.from_config(get_config())
            except Exception as e:
                logger.warning(f"[SCOUT] noisy_sections config unreadable, using defaults: {e}")
                tracker = SectionNoiseTracker()
            self._noise_tracker = tracker
        return tracker

    @staticmethod
    def _empty_body_reason(response: Response) -> str | None:
        """``"empty_body"`` for a zero-byte/whitespace body or an HTML shell with
        no visible text and no ``href``/``src`` references; otherwise None (#199).

        JS app shells (``<div id=app></div><script src=app.js>``) are not empty:
        the ``src`` keeps them on the JS-detection path.
        """
        if not response.body or not response.body.strip():
            return "empty_body"
        if not isinstance(response, HtmlResponse):
            # Only HTML has a meaningful "blank shell"; a non-empty text/XML/JSON
            # body may carry bare URLs and is left to the extractors.
            return None
        try:
            has_text = bool(response.xpath("//body//text()[normalize-space()]").get())
            has_refs = bool(response.css("[href], [src]").get())
        except (ValueError, AttributeError):  # undecodable/non-text body
            return None
        if not has_text and not has_refs:
            return "empty_body"
        return None

    def _skip_response(self, response: Response, reason: str) -> None:
        self._track_skip(response.url, reason)
        if URLS_SKIPPED is not None:
            URLS_SKIPPED.labels(spider=self.name, skip_reason=reason).inc()
        logger.info(f"[SCOUT] Skipping {reason} response ({len(response.body)} bytes): {response.url[:80]}")

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
                enqueue_stage2=False,
            )

            logger.info(
                f"[SCOUT] SeedManager results: seeds={result['seed_inserted']}, "
                f"domain={result.get('domain_inserted', result.get('uconn_inserted', 0))}"
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
