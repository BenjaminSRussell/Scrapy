"""Shared crawling logic for Stage 1 spiders."""

import hashlib
import logging
import time
from collections import deque
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import scrapy
from scrapy.http import Request, Response
from scrapy.spidermiddlewares.httperror import HttpError
from twisted.internet.error import DNSLookupError, TCPTimedOutError, TimeoutError

from src.core.config import get_config
# StorageManager removed - use get_delta() and get_redis() directly
from src.stage1.processors.url_processor import URLProcessor, should_follow_url
from src.items import OffsiteCandidateItem
from src.stage1.js_detection import JSDetector

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

    name = "base"

    handle_httpstatus_list = [301, 302, 303, 307, 308]

    @classmethod
    def test_factory(cls, **kw):
        return cls(name=kw.pop("name", "test_base"), **kw)

    def __init__(self, *args, **kwargs):
        print(f"\n🕷️  BaseSpider.__init__() called for {kwargs.get('name', 'unknown')}")
        self.name = kwargs.pop("name", self.name)
        print("🕷️  Calling super().__init__...")
        super().__init__(*args, **kwargs)
        print(f"🕷️  super().__init__() complete for {self.name}")

        self.allowed_domains = ["uconn.edu"]

        self.IGNORED_EXTENSIONS = (
            getattr(self, "settings", {}).get("IGNORED_EXTENSIONS", []) if hasattr(self, "settings") else []
        )
        self.ignored_extensions = list(self.IGNORED_EXTENSIONS)

        self.config_manager = ConfigManager.get_instance()
        self.config = self.config_manager.config

        self.storage = StorageManager.get_instance()

        self.delta = self.storage.delta
        self.postgres = self.storage.postgres
        self.redis_client = self.storage.redis.redis if hasattr(self.storage.redis, "redis") else self.storage.redis

        self.url_hashes_key = f"{self.name}:url_hashes"

        self.discovered_records = []
        self.error_records = []
        self.sitemaps_parsed: set[str] = set()
        self._robots_parser = None

        self.skip_counters = {
            "images": 0,
            "static_assets": 0,
            "documents": 0,
            "media_files": 0,
            "archives": 0,
            "duplicates": 0,
            "invalid_urls": 0,
        }

        self.url_processor = URLProcessor(
            base_url="",
            allowed_domains=self.allowed_domains,
        )

        self.perf_start_time = datetime.now()
        self.perf_urls_processed = 0
        self.perf_last_log = datetime.now()

        self.start_time = time.time()
        self.total_urls_discovered = 0
        self.total_file_size = 0

        self.url_discovery_window = deque(maxlen=60)
        self.file_size_window = deque(maxlen=100)
        self.last_metric_update = time.time()

        self.js_confidence_threshold = self.config_manager.stage1.js_confidence_threshold

        self.batch_size = self.config_manager.stage1.batch_size

        self.max_depth = self.settings.getint("MAX_DEPTH") if hasattr(self, "settings") and self.settings else None

        print(f"[{self.name}] Loading start_urls from Delta Lake...")
        self.start_urls = self._load_seed_urls()
        print(f"[{self.name}] Loaded {len(self.start_urls)} start_urls")

        print(f"✅ BaseSpider.__init__() COMPLETE for {self.name}")

    async def start(self):
        print(f"🚀 [{self.name}] start() called!")
        print(f"🚀 [{self.name}] Processing {len(self.start_urls)} start URLs...")

        for i, url in enumerate(self.start_urls):
            if i < 5:
                print(f"  - URL {i}: {url[:80]}")

            yield scrapy.Request(
                url,
                callback=self.parse,
                errback=self.handle_error,
                dont_filter=True,
                priority=0,
            )

        print(f"✅ [{self.name}] start() generated {len(self.start_urls)} requests")

    def _hash_url(self, url: str) -> str:
        normalized = self.normalize_url(url)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def normalize_url(self, url: str) -> str:
        normalized = self.url_processor.normalize_url(url)
        return normalized or url

    def _load_seed_urls(self):
        try:
            seed_records = self.delta.read("seed_urls")
            urls = [record["url"] for record in seed_records]
            logger.info(f"Loaded {len(urls)} seed URLs from Delta Lake (will attempt all)")

            url_count_in_redis = self.redis_client.scard(self.url_hashes_key) if hasattr(self, "url_hashes_key") else 0
            logger.info(f"Redis currently tracking {url_count_in_redis} URLs (dupefilter will handle during crawl)")

            return urls
        except Exception as e:
            logger.error(f"Could not load seed URLs from Delta Lake: {e}")
            return []

    def _load_existing_urls(self):
        pass

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------

    def extract_links(self, response: Response) -> list[str]:
        candidate_urls = self._extract_urls(response)
        unique_links: set[str] = set()

        for url in candidate_urls:
            if not url:
                continue

            normalized_url = self.normalize_url(url)

            if not should_follow_url(normalized_url):
                continue

            scheme = urlparse(normalized_url).scheme
            if scheme in {"mailto", "javascript"}:
                continue

            unique_links.add(normalized_url)

        return list(unique_links)

    def should_follow_link(self, request: Request) -> bool:
        max_depth = getattr(self, "max_depth", None)
        if max_depth is None:
            return True

        current_depth = request.meta.get("depth", 0)
        return current_depth < max_depth

    def create_request(
        self,
        url: str,
        parent: Request | None = None,
        *,
        callback: Callable[..., Any] | None = None,
        errback: Callable[..., Any] | None = None,
        meta: dict | None = None,
        dont_filter: bool = False,
        priority: int = 0,
        **kwargs,
    ) -> scrapy.Request:
        """Create a follow-up request while propagating depth metadata."""

        meta = dict(meta or {})

        if parent is not None:
            parent_depth = parent.meta.get("depth", 0)
            meta.setdefault("depth", parent_depth + 1)
            meta.setdefault("parent_url", parent.url)
            meta.setdefault("referer", parent.url)
        else:
            meta.setdefault("depth", 0)

        request = scrapy.Request(
            url,
            callback=callback or self.parse,
            errback=errback or self.handle_error,
            meta=meta,
            dont_filter=dont_filter,
            priority=priority,
            **kwargs,
        )
        return request

    def parse_error(self, response: Response) -> dict[str, Any]:
        error_record = {
            "url": response.url,
            "url_hash": self._hash_url(response.url),
            "status": response.status,
            "depth": response.meta.get("depth", 0),
            "timestamp": datetime.now().isoformat(),
        }
        self.error_records.append(error_record)
        logger.error("Error response received: %s for %s", response.status, response.url)
        return error_record

    def parse(self, response: Response):
        depth = response.meta.get("depth", 0)
        url_hash = self._hash_url(response.url)
        content_type = response.headers.get("Content-Type", b"").decode("utf-8", errors="ignore").lower()

        results: list[Any] = []

        discovered_item = {
            "url": response.url,
            "url_hash": url_hash,
            "depth": depth,
            "status_code": response.status,
            "content_type": content_type,
            "content_size": len(response.body),
            "discovered_at": datetime.now().isoformat(),
            "discovery_type": (
                "html" if ("text/html" in content_type or "application/xhtml" in content_type) else "resource"
            ),
        }
        discovered_item["resource_type"] = self._categorize_resource(response.url, content_type)
        results.append(discovered_item)

        if "text/html" not in content_type and "application/xhtml" not in content_type:
            logger.debug(f"Non-HTML content discovered: {content_type} for {response.url[:80]}")
            self._record_non_html(response, url_hash, depth, content_type)
            return results

        requires_js, confidence = self._detect_js_requirement(response)
        discovered_urls = self._extract_urls(response)

        content_size = len(response.body)
        is_heavy = content_size > 1_000_000
        self._record_discovery(
            response,
            url_hash,
            depth,
            content_size,
            is_heavy,
            len(discovered_urls),
            requires_js,
        )

        self.perf_urls_processed += 1
        self._maybe_log_performance()

        page_url_count = len(discovered_urls)
        current_time = time.time()
        self.url_discovery_window.append((current_time, page_url_count))
        self.file_size_window.append(content_size)

        self.total_urls_discovered += page_url_count
        self.total_file_size += content_size

        if PROMETHEUS_AVAILABLE:
            self._update_dashboard_metrics()

        logger.info(
            f"[D{depth}] {response.url[:80]} -> {page_url_count} URLs (JS: {requires_js}, conf: {confidence:.2f})"
        )

        for produced in self._process_discovered_urls(response, discovered_urls, depth):
            if produced is not None:
                results.append(produced)

        if len(self.discovered_records) >= self.batch_size:
            self._save_batch()
        if len(self.error_records) >= 50:
            self._save_error_batch()

        return results

    def _categorize_resource(self, url: str, content_type: str) -> str:
        url_lower = url.lower()

        if any(ext in url_lower for ext in [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"]):
            return "document"
        elif any(ext in url_lower for ext in [".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp"]):
            return "image"
        elif any(ext in url_lower for ext in [".js", ".css"]):
            return "asset"
        else:
            return "page"

    def _detect_js_requirement(self, response: Response) -> tuple[bool, float]:
        detector = JSDetector(response)
        result = detector.requires_js_rendering()
        return result["requires_js"], result["confidence"]

    def _extract_urls(self, response: Response) -> list[str]:
        self.url_processor.base_url = response.url
        discovered_urls = self.url_processor.extractor.discover_all_urls(response)
        return [self.normalize_url(url) for url in discovered_urls]

    def _process_discovered_urls(self, response: Response, discovered_urls: list[str], depth: int) -> Iterator:
        if not discovered_urls:
            return

        new_urls, url_hash_map = self._deduplicate_urls(discovered_urls)

        if not new_urls:
            return

        html_candidates = []
        offsite_urls = []
        static_urls = []

        for url in new_urls:
            url_hash = url_hash_map[url]
            is_external = self._is_external_url(url)

            if is_external:
                offsite_urls.append(url)
            else:
                should_follow = should_follow_url(url)

                if not should_follow:
                    static_urls.append((url, url_hash))
                else:
                    html_candidates.append((url, url_hash))

        for url in offsite_urls:
            yield self._create_offsite_item(response, url)

            if PROMETHEUS_AVAILABLE and OFFSITE_LINKS_FOUND:
                OFFSITE_LINKS_FOUND.labels(spider=self.name).inc()

            logger.debug(f"[K3 OFFSITE] External link found (not following): {url[:80]}")

        for url, url_hash in static_urls:
            skip_reason = self._categorize_skip_reason(url)
            self._track_skip(url, skip_reason)
            yield self._create_static_item(url, url_hash, depth, response.url, skip_reason)
            logger.debug(f"[K3 STATIC] Skipped {skip_reason}: {url[:80]}")

        for url, _url_hash in html_candidates:
            priority = 0 if urlparse(response.url).netloc == urlparse(url).netloc else -1
            yield scrapy.Request(
                url,
                callback=self.parse,
                errback=self.handle_error,
                meta={"depth": depth + 1},
                priority=priority,
                dont_filter=False,
            )
            logger.debug(f"[K3 HTML] Queued HTML candidate: {url[:80]}")

    def _deduplicate_urls(self, urls: list[str]) -> tuple[list[str], dict[str, str]]:
        if not urls:
            return [], {}

        pipeline = self.redis_client.pipeline()
        url_hash_map: dict[str, str] = {}

        for url in urls:
            url_hash = self._hash_url(url)
            url_hash_map[url] = url_hash
            pipeline.sismember(self.url_hashes_key, url_hash)

        existence_results = pipeline.execute()

        new_urls: list[str] = []

        for url, exists in zip(urls, existence_results, strict=False):
            if not exists:
                url_hash = url_hash_map[url]
                pipeline.sadd(self.url_hashes_key, url_hash)
                new_urls.append(url)

        if new_urls:
            pipeline.execute()
        else:
            pipeline.reset()

        new_url_hashes = {url: url_hash_map[url] for url in new_urls}
        return new_urls, new_url_hashes

    def _create_offsite_item(self, response: Response, url: str) -> OffsiteCandidateItem:
        anchor_text, context = self._extract_context(response, url)
        return OffsiteCandidateItem(
            source_page=response.url,
            external_url=url,
            anchor_text=anchor_text,
            context=context,
            discovered_at=datetime.now().isoformat(),
        )

    def _create_static_item(self, url: str, url_hash: str, depth: int, parent_url: str, skip_reason: str) -> dict:
        return {
            "url": url,
            "url_hash": url_hash,
            "depth": depth + 1,
            "discovery_type": "static_resource",
            "resource_type": "static",
            "file_extension": url.lower().split(".")[-1] if "." in url else "unknown",
            "discovered_at": datetime.now().isoformat(),
            "parent_url": parent_url,
            "skip_reason": skip_reason,
        }

    def _is_external_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()

            if ":" in domain:
                domain = domain.split(":")[0]

            for allowed in self.allowed_domains:
                if domain.endswith(allowed) or domain == allowed:
                    return False

            return True
        except Exception:
            return False

    def _extract_context(self, response: Response, url: str) -> tuple[str, str]:
        anchor_text = ""
        context = ""

        try:
            anchors = response.css(f'a[href="{url}"]')
            if anchors:
                anchor_text = anchors[0].css("::text").get() or ""
                anchor_text = anchor_text.strip()

                parent_p = anchors[0].xpath("./ancestor::p[1]//text()").getall()
                if parent_p:
                    context = " ".join([t.strip() for t in parent_p if t.strip()])
                    if len(context) > 500:
                        context = context[:500] + "..."
        except Exception as e:
            logger.debug(f"Failed to extract context for {url}: {e}")

        return anchor_text, context

    def _categorize_skip_reason(self, url: str) -> str:
        url_lower = url.lower()

        if any(url_lower.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".ico", ".tiff"]):
            return "images"

        elif any(url_lower.endswith(ext) for ext in [".css", ".map", ".woff", ".woff2", ".ttf", ".eot", ".otf"]):
            return "static_assets"

        elif any(
            url_lower.endswith(ext) for ext in [".mp3", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4a", ".wav"]
        ):
            return "media_files"

        elif any(url_lower.endswith(ext) for ext in [".exe", ".dmg", ".pkg", ".deb", ".rpm"]):
            return "executables"

        else:
            return "other_filtered"

    def _track_skip(self, url: str, reason: str | None = None):
        if reason is None:
            reason = self._categorize_skip_reason(url)

        self.skip_counters[reason] = self.skip_counters.get(reason, 0) + 1

        total_skips = sum(self.skip_counters.values())
        if total_skips % 500 == 0:
            skip_summary = ", ".join([f"{k}: {v}" for k, v in sorted(self.skip_counters.items(), key=lambda x: -x[1])])
            logger.info(f"⏭️  SKIP STATS - Total: {total_skips} | {skip_summary}")

    def _update_dashboard_metrics(self):
        if not PROMETHEUS_AVAILABLE:
            return

        current_time = time.time()

        cutoff_time = current_time - 60
        recent_urls = sum(count for timestamp, count in self.url_discovery_window if timestamp >= cutoff_time)
        urls_per_minute = recent_urls

        if self.file_size_window:
            avg_file_size = sum(self.file_size_window) / len(self.file_size_window)
        else:
            avg_file_size = 0

        if NEW_URLS_FOUND_PER_MINUTE:
            NEW_URLS_FOUND_PER_MINUTE.labels(spider=self.name).set(urls_per_minute)

        if AVERAGE_FILE_SIZE_BYTES:
            AVERAGE_FILE_SIZE_BYTES.labels(spider=self.name).set(avg_file_size)

        self.last_metric_update = current_time

    def _record_discovery(
        self,
        response: Response,
        url_hash: str,
        depth: int,
        content_size: int,
        is_heavy: bool,
        url_count: int,
        requires_js: bool,
    ):
        """Record successful page discovery."""
        record = {
            "url": response.url,
            "url_hash": url_hash,
            "depth": depth,
            "status_code": response.status,
            "content_size": content_size,
            "is_heavy": is_heavy,
            "urls_found": url_count,
            "requires_js": requires_js,
            "discovered_at": datetime.now().isoformat(),
        }
        self.discovered_records.append(record)

    def _record_non_html(self, response: Response, url_hash: str, depth: int, content_type: str):
        record = {
            "url": response.url,
            "url_hash": url_hash,
            "depth": depth,
            "content_type": content_type,
            "content_size": len(response.body),
            "discovered_at": datetime.now().isoformat(),
        }
        self.discovered_records.append(record)

    def _save_batch(self):
        if self.discovered_records:
            try:
                self.delta.write("stage1_discovery", self.discovered_records)
                logger.info(f"💾 Saved {len(self.discovered_records)} discovery records")
                self.discovered_records = []
            except Exception as e:
                logger.error(f"Failed to save discovery records: {e}")

    def _maybe_log_performance(self):
        now = datetime.now()
        if (now - self.perf_last_log).seconds >= 10:
            elapsed = (now - self.perf_start_time).seconds or 1
            rate = self.perf_urls_processed / elapsed
            logger.info(f"⚡ Performance: {self.perf_urls_processed} URLs @ {rate:.2f} URLs/sec")
            self.perf_last_log = now

    def handle_error(self, failure):
        request = failure.request

        error_code = 0

        if failure.check(HttpError):
            response = failure.value.response
            status = response.status
            error_code = status

            if status == 404:
                logger.info(f"Not found (404): {request.url[:80]}")
            # Log other HTTP errors at WARNING
            else:
                logger.warning(f"HTTP {status} error: {request.url[:80]}")

        elif failure.check(DNSLookupError):
            logger.error(f"DNS lookup failed: {request.url[:80]}")
        elif failure.check(TimeoutError, TCPTimedOutError):
            retry_count = request.meta.get("retry_times", 0)
            max_retries = self.settings.get("RETRY_TIMES", 3)

            if retry_count >= max_retries:
                logger.error(f"Timeout after {retry_count} retries: {request.url[:80]}")
            else:
                logger.debug(f"Timeout (retry {retry_count}/{max_retries}): {request.url[:80]}")
        else:
            logger.error(f"Unknown error: {failure.getErrorMessage()} for {request.url[:80]}")

        error_record = {
            "url": request.url,
            "url_hash": self._hash_url(request.url),
            "error_type": failure.type.__name__,
            "error_message": str(failure.value),
            "error_code": error_code,
            "depth": request.meta.get("depth", 0),
            "timestamp": datetime.now().isoformat(),
        }
        self.error_records.append(error_record)

        if len(self.error_records) >= 50:
            self._save_error_batch()

    def _save_error_batch(self):
        if self.error_records:
            try:
                self.delta.write("stage1_errors", self.error_records)
                logger.info(f"💾 Saved {len(self.error_records)} error records")
                self.error_records = []
            except Exception as e:
                logger.error(f"Failed to save error records: {e}")

    def closed(self, reason):
        logger.info(f"Spider closing: {reason}")

        self._save_batch()
        self._save_error_batch()

        logger.info(f"✅ Final stats: {self.perf_urls_processed} URLs processed")
        logger.info(f"✅ Total URLs discovered: {self.total_urls_discovered}")

        skip_summary = ", ".join([f"{k}: {v}" for k, v in sorted(self.skip_counters.items(), key=lambda x: -x[1])])
        logger.info(f"⏭️  Final skip stats: {skip_summary}")
