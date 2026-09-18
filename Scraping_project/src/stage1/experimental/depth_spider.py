import logging
from collections.abc import Iterator
from datetime import datetime, timedelta

import scrapy
from scrapy.http import Response

from src.core.config import get_config
from src.stage1.middlewares.spider_config import get_spider_settings
from src.utils.delta import get_delta
from src.utils.redis import get_redis
from src.stage1.processors.url_processor import should_follow_url
from src.lakehouse import SeedManager
from src.stage1.base_spider import BaseSpider

logger = logging.getLogger(__name__)

class DepthSpider(BaseSpider):

    name = "depth"

    custom_settings = get_spider_settings("deep_dive")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        config = ConfigManager.get_instance().config
        depth_config = config.get("stage1", {}).get("depth_spider", {})

        self.max_depth = depth_config.get("max_depth", 50)
        self.rescrape_interval_hours = depth_config.get("rescrape_interval_hours", 24)
        self.enabled = depth_config.get("enabled", True)

        self.redis_client = get_redis()
        self.delta = get_delta()

        self.seed_manager = SeedManager(self.delta)

        self.depth_stats = {
            "urls_rescraped": 0,
            "new_urls_found": 0,
            "urls_added_to_seeds": 0,
        }

        logger.info(f"[DEPTH] Initialized with max_depth={self.max_depth}")
        logger.info(f"[DEPTH] Re-scrape interval: {self.rescrape_interval_hours} hours")
        logger.info(f"[DEPTH] Enabled: {self.enabled}")

    def start_requests(self) -> Iterator[scrapy.Request]:
        if not self.enabled:
            logger.warning("[DEPTH] Depth spider is disabled in config")
            return

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
                dont_filter=True,
            )

    def _get_rescrape_candidates(self) -> list[str]:
        try:
            seed_records = self.delta.read("seed_urls")
            all_seed_urls = [record["url"] for record in seed_records]

            logger.info(f"[DEPTH] Found {len(all_seed_urls)} seed URLs")

            urls_to_crawl = []
            current_time = datetime.now()
            rescrape_threshold = current_time - timedelta(hours=self.rescrape_interval_hours)

            for url in all_seed_urls[:5000]:
                last_crawl_key = f"depth:last_crawl:{self._hash_url(url)}"
                last_crawl_str = self.redis_client.get(last_crawl_key)

                if last_crawl_str:
                    try:
                        last_crawl = datetime.fromisoformat(last_crawl_str.decode("utf-8"))
                        if last_crawl > rescrape_threshold:
                            continue
                    except Exception:
                        pass

                urls_to_crawl.append(url)

                if len(urls_to_crawl) >= 1000:
                    break

            logger.info(f"[DEPTH] Selected {len(urls_to_crawl)} URLs for re-scraping")
            return urls_to_crawl

        except Exception as e:
            logger.error(f"[DEPTH] Failed to get rescrape candidates: {e}", exc_info=True)
            return []

    def parse(self, response: Response) -> Iterator:
        content_type = response.headers.get("Content-Type", b"").decode("utf-8", errors="ignore").lower()

        if "text/html" not in content_type:
            logger.debug(f"[DEPTH] Non-HTML, skipping: {content_type}")
            return

        url_hash = self._hash_url(response.url)
        last_crawl_key = f"depth:last_crawl:{url_hash}"
        self.redis_client.set(last_crawl_key, datetime.now().isoformat(), ex=86400 * 7)

        if response.meta.get("rescrape"):
            self.depth_stats["urls_rescraped"] += 1

        discovered_urls = self._extract_urls(response)

        if not discovered_urls:
            return

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

        new_urls, _ = self._deduplicate_urls(discovered_urls)

        if new_urls:
            logger.info(f"[DEPTH] Found {len(new_urls)} new URLs at depth {depth} from {response.url[:80]}")
            self.depth_stats["new_urls_found"] += len(new_urls)

        urls_to_add_to_seeds = []

        for url in new_urls:
            if self._is_external_url(url):
                yield self._create_offsite_item(response, url)
                continue

            if not should_follow_url(url):
                skip_reason = self._categorize_skip_reason(url)
                self._track_skip(url, skip_reason)
                urls_to_add_to_seeds.append(url)
                continue

            content_hint = self._guess_content_type(url)

            if content_hint == "html":
                if depth < self.max_depth:
                    yield scrapy.Request(
                        url,
                        callback=self.parse,
                        errback=self.handle_error,
                        meta={"depth": depth + 1, "rescrape": False},
                        priority=0,
                        dont_filter=False,
                    )

            urls_to_add_to_seeds.append(url)

        if urls_to_add_to_seeds:
            self._add_urls_to_seeds(urls_to_add_to_seeds, response.url)
            self.depth_stats["urls_added_to_seeds"] += len(urls_to_add_to_seeds)

        if self.depth_stats["urls_rescraped"] % 100 == 0:
            self._log_depth_stats()

    def _guess_content_type(self, url: str) -> str:
        url_lower = url.lower()
        if ".pdf" in url_lower:
            return "pdf"
        elif any(ext in url_lower for ext in [".doc", ".docx", ".xls", ".xlsx"]):
            return "doc"
        else:
            return "html"

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
                f"[DEPTH] SeedManager results: seeds={result['seed_inserted']}, uconn={result['uconn_inserted']}"
            )

        except Exception as e:
            logger.error(f"[DEPTH] Failed to add URLs via SeedManager: {e}", exc_info=True)

    def _log_depth_stats(self):
        logger.info(
            f"[DEPTH STATS] "
            f"Re-scraped: {self.depth_stats['urls_rescraped']} | "
            f"New URLs: {self.depth_stats['new_urls_found']} | "
            f"Added to seeds: {self.depth_stats['urls_added_to_seeds']}"
        )

    def closed(self, reason):
        self._log_depth_stats()
        logger.info(f"[DEPTH] Spider closing: {reason}")
        super().closed(reason)
