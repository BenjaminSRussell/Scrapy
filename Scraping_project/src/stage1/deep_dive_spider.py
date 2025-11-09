"""Enhanced depth spider for discovering hidden and embedded URLs."""

import logging
from collections.abc import Iterator
from typing import Any

from scrapy.http import Response

from src.common.hidden_url_extractor import HiddenURLExtractor
from src.common.spider_config import get_spider_settings
from src.stage1.base_spider import BaseSpider

logger = logging.getLogger(__name__)

class DeepDiveSpider(BaseSpider):

    name = "deep_dive"

    custom_settings = get_spider_settings("deep_dive")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        configured_domains = self.config_manager.stage1.allowed_domains

        if configured_domains:
            self.allowed_domains = configured_domains
            logger.info(f"[DEPTH] Domain allowlist from config: {configured_domains}")
        else:
            logger.info(f"[DEPTH] Using dynamic domains: {self.allowed_domains}")

        self.depth_stats = {
            "hidden_urls_found": 0,
            "data_attributes": 0,
            "json_ld": 0,
            "javascript": 0,
            "iframes": 0,
            "api_endpoints": 0,
            "high_value_urls": 0,
            "routed_to_js": 0,
        }

        logger.info("[DEPTH] Enhanced depth spider initialized with hidden URL extraction")

    def parse(self, response: Response) -> Iterator:
        yield from super().parse(response)

        hidden_extractor = HiddenURLExtractor(base_url=response.url)
        hidden_urls = hidden_extractor.extract_all_hidden_urls(response)

        for category, urls in hidden_urls.items():
            self.depth_stats[category] = self.depth_stats.get(category, 0) + len(urls)

        all_hidden_urls = []
        for urls in hidden_urls.values():
            all_hidden_urls.extend(urls)

        all_hidden_urls = list(set(all_hidden_urls))

        if all_hidden_urls:
            logger.info(f"[DEPTH] Found {len(all_hidden_urls)} hidden URLs from {response.url[:80]}")

            depth = response.meta.get("depth", 0)

            for url in all_hidden_urls:
                url_hash = self._hash_url(url)
                if self.redis_client.sismember(self.url_hashes_key, url_hash):
                    continue

                assessment = self.url_processor.assessor.assess_url(
                    url=url,
                    parent_url=response.url,
                    depth=depth + 1,
                    js_confidence=0.0,
                )

                if assessment.value_score < 30:
                    logger.debug(f"[DEPTH] Skipping low-value URL: {url[:80]} (score={assessment.value_score})")
                    continue

                if assessment.value_score >= 70:
                    self.depth_stats["high_value_urls"] += 1

                self.redis_client.sadd(self.url_hashes_key, url_hash)

                if assessment.recommended_spider == "js":
                    yield self._queue_for_js_spider(url, response.url, assessment)
                    self.depth_stats["routed_to_js"] += 1
                else:
                    yield self._queue_for_depth_crawl(url, response.url, assessment, depth)

        total_hidden = sum(self.depth_stats.values())
        if total_hidden > 0 and total_hidden % 50 == 0:
            self._log_depth_stats()

    def _queue_for_js_spider(self, url: str, parent_url: str, assessment: Any) -> dict:
        return {
            "url": url,
            "parent_url": parent_url,
            "priority": min(assessment.value_score, 100),
            "value_score": assessment.value_score,
            "discovery_source": "depth_spider",
            "target_spider": "javascript",
        }

    def _queue_for_depth_crawl(self, url: str, parent_url: str, assessment: Any, depth: int) -> dict:
        import scrapy

        return scrapy.Request(
            url,
            callback=self.parse,
            errback=self.handle_error,
            meta={
                "depth": depth + 1,
                "parent_url": parent_url,
                "value_score": assessment.value_score,
            },
            priority=min(assessment.value_score // 10, 10),
            dont_filter=False,
        )

    def _log_depth_stats(self):
        logger.info(
            f"[DEPTH STATS] "
            f"Hidden URLs: {self.depth_stats.get('hidden_urls_found', 0)} | "
            f"Data attrs: {self.depth_stats.get('data_attributes', 0)} | "
            f"JSON-LD: {self.depth_stats.get('json_ld', 0)} | "
            f"JavaScript: {self.depth_stats.get('javascript', 0)} | "
            f"Iframes: {self.depth_stats.get('iframes', 0)} | "
            f"APIs: {self.depth_stats.get('api_endpoints', 0)} | "
            f"High-value: {self.depth_stats.get('high_value_urls', 0)} | "
            f"Routed to JS: {self.depth_stats.get('routed_to_js', 0)}"
        )

    def closed(self, reason):
        self._log_depth_stats()
        logger.info(f"[DEPTH] Spider closing: {reason}")

        super().closed(reason)
