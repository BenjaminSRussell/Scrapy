import logging
import re
import threading
import time
from typing import Any, Optional

try:
    from prometheus_client import Counter, Gauge, Histogram, start_http_server

    PROMETHEUS_AVAILABLE = True
except ImportError:
    # Metric names are only defined (and only referenced) when
    # PROMETHEUS_AVAILABLE is True; every use below is guarded by it.
    PROMETHEUS_AVAILABLE = False

from scrapy import Spider, signals
from scrapy.crawler import Crawler
from scrapy.exceptions import DropItem, NotConfigured
from scrapy.http import Request, Response

logger = logging.getLogger(__name__)



class CrawlRunState:
    """Per-run crawl state, safe across threads and concurrent runs (#28).

    This used to be two module-level dicts keyed by ``spider.name``. Two
    concurrent runs of the same spider in one process (CrawlerProcess,
    orchestrator, tests) overwrote each other's start time, and the first
    ``spider_closed`` deleted the second run's skip tallies. The
    read-modify-write tally updates also had no lock. State is now keyed by
    the spider *instance* and every access holds one lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start: dict[int, float] = {}
        self._skipped: dict[int, dict[str, int]] = {}

    def open(self, spider: Any, now: Optional[float] = None) -> None:
        with self._lock:
            self._start[id(spider)] = time.time() if now is None else now
            self._skipped[id(spider)] = {}

    def close(self, spider: Any) -> tuple[Optional[float], dict[str, int]]:
        """Forget this run. Returns (start_time or None, final tallies)."""
        with self._lock:
            return self._start.pop(id(spider), None), self._skipped.pop(id(spider), {})

    def tally(self, spider: Any, reason: str) -> tuple[int, dict[str, int]]:
        """Count one skipped URL. Returns (run total, snapshot of tallies)."""
        with self._lock:
            counts = self._skipped.setdefault(id(spider), {})
            counts[reason] = counts.get(reason, 0) + 1
            return sum(counts.values()), dict(counts)

    def active_runs(self) -> int:
        with self._lock:
            return len(self._start)


if PROMETHEUS_AVAILABLE:
    ITEMS_SCRAPED = Counter("scrapy_items_scraped_total", "Total number of items scraped", ["spider"])

    ITEMS_DROPPED = Counter("scrapy_items_dropped_total", "Total number of items dropped", ["spider"])

    REQUESTS_TOTAL = Counter("scrapy_requests_total", "Total number of requests made", ["spider", "method"])

    RESPONSES_TOTAL = Counter(
        "scrapy_responses_total",
        "Total number of responses received",
        ["spider", "status_code"],
    )

    RESPONSE_TIME = Histogram(
        "scrapy_response_time_seconds",
        "Response time in seconds",
        ["spider"],
        buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, float("inf")),
    )

    SPIDER_OPENED = Gauge("scrapy_spider_opened", "Number of spiders currently running", ["spider"])

    SPIDER_CLOSED = Counter(
        "scrapy_spider_closed_total",
        "Total number of spiders closed",
        ["spider", "reason"],
    )

    SPIDER_ERRORS = Counter(
        "scrapy_spider_errors_total",
        "Total number of spider errors",
        ["spider", "exception_type"],
    )

    REQUESTS_DROPPED = Counter(
        "scrapy_requests_dropped_total",
        "Total number of requests dropped",
        ["spider", "reason"],
    )

    DOWNLOADER_REQUEST_BYTES = Counter(
        "scrapy_downloader_request_bytes_total",
        "Total bytes sent in requests",
        ["spider"],
    )

    DOWNLOADER_RESPONSE_BYTES = Counter(
        "scrapy_downloader_response_bytes_total",
        "Total bytes received in responses",
        ["spider"],
    )

    CRAWL_DURATION = Gauge(
        "scrapy_crawl_duration_seconds",
        "Duration of the current crawl in seconds",
        ["spider"],
    )

    URLS_SKIPPED = Counter(
        "scrapy_urls_skipped_total",
        "Total number of URLs skipped by type",
        ["spider", "skip_reason"],
    )

    NEW_URLS_FOUND_PER_MINUTE = Gauge(
        "scrapy_new_urls_found_per_minute",
        "Rate of new URLs discovered per minute",
        ["spider"],
    )

    AVERAGE_FILE_SIZE_BYTES = Gauge(
        "scrapy_average_file_size_bytes",
        "Average file size of downloaded responses",
        ["spider"],
    )

    OFFSITE_LINKS_FOUND = Counter(
        "scrapy_offsite_links_found_total",
        "Total number of offsite/external links discovered",
        ["spider"],
    )

    OFFSITE_CANDIDATES_SAVED = Counter(
        "scrapy_offsite_candidates_saved_total",
        "Total number of offsite candidates saved to Delta Lake",
        ["spider"],
    )

    CRAWLER_CONTENT_SUMMARY = Gauge(
        "scrapy_crawler_content_summary",
        "Sample summary of scraped content for qualitative monitoring",
        ["spider"],
    )
    CRAWLER_SUMMARY_SKIPPED = Counter(
        "scrapy_crawler_summary_skipped_total",
        "GrafanaSummaryPipeline summary exports skipped instead of failing the crawl (#462)",
        ["spider", "reason"],
    )

    # Hidden-URL discovery quality, per extractor category (#392). Grafana:
    #   sum by (category) (rate(scrapy_hidden_urls_found_total[5m]))
    #   sum by (route) (rate(scrapy_hidden_urls_routed_total[5m]))
    HIDDEN_URLS_FOUND = Counter(
        "scrapy_hidden_urls_found_total",
        "URLs found by HiddenURLExtractor, by category (offsite = outside allowed_domains)",
        ["spider", "category"],
    )
    HIDDEN_URLS_ROUTED = Counter(
        "scrapy_hidden_urls_routed_total",
        "What happened to each hidden URL: depth_crawl, js, offsite, low_value or duplicate",
        ["spider", "route"],
    )

    # --- Delta Lake Manager Metrics ---
    DELTA_MANAGER_CONTEXT_ENTER_TOTAL = Counter(
        "delta_manager_context_enter_total", "Total number of times a DeltaLakeManager context has been entered."
    )
    DELTA_MANAGER_CONTEXT_EXIT_TOTAL = Counter(
        "delta_manager_context_exit_total", "Total number of times a DeltaLakeManager context has been exited."
    )
    DELTA_MANAGER_SHUTDOWN_TOTAL = Counter(
        "delta_manager_shutdown_total", "Total number of times DeltaLakeManager.shutdown() has been called."
    )
    DELTA_MANAGER_SHUTDOWN_DURATION_SECONDS = Histogram(
        "delta_manager_shutdown_duration_seconds",
        "Time taken to shut down the DeltaLakeManager, in seconds.",
        buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0, float("inf")),
    )
    # --- End Delta Lake Manager Metrics ---

else:
    DELTA_MANAGER_CONTEXT_ENTER_TOTAL = None
    DELTA_MANAGER_CONTEXT_EXIT_TOTAL = None
    DELTA_MANAGER_SHUTDOWN_TOTAL = None
    DELTA_MANAGER_SHUTDOWN_DURATION_SECONDS = None
    ITEMS_SCRAPED = ITEMS_DROPPED = REQUESTS_TOTAL = RESPONSES_TOTAL = None
    RESPONSE_TIME = SPIDER_OPENED = SPIDER_CLOSED = SPIDER_ERRORS = None
    REQUESTS_DROPPED = DOWNLOADER_REQUEST_BYTES = DOWNLOADER_RESPONSE_BYTES = None
    CRAWL_DURATION = URLS_SKIPPED = None
    NEW_URLS_FOUND_PER_MINUTE = AVERAGE_FILE_SIZE_BYTES = None
    OFFSITE_LINKS_FOUND = OFFSITE_CANDIDATES_SAVED = None
    CRAWLER_CONTENT_SUMMARY = None
    CRAWLER_SUMMARY_SKIPPED = None
    HIDDEN_URLS_FOUND = HIDDEN_URLS_ROUTED = None

_LABEL_CHARS = re.compile(r"[^a-z0-9_]+")


def bounded_label(value: Any, max_len: int = 40) -> str:
    """Low-cardinality label value (#270): the part before any ':' (so
    ``non_html:<media type>`` or ``error:<url>`` collapse to their reason),
    lower-cased, [a-z0-9_] only, capped at ``max_len``."""
    text = str(value or "").split(":", 1)[0].strip().lower()
    text = _LABEL_CHARS.sub("_", text).strip("_")[:max_len]
    return text or "unknown"


class PrometheusExtension:

    def __init__(self, port: int, host: str):
        self.port = port
        self.host = host
        self.server_started = False
        self.runs = CrawlRunState()

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "PrometheusExtension":
        if not PROMETHEUS_AVAILABLE:
            logger.warning("Prometheus extension disabled - prometheus_client not installed")
            raise NotConfigured("prometheus_client library not available")

        if not crawler.settings.getbool("PROMETHEUS_ENABLED", True):
            raise NotConfigured("Prometheus extension is disabled")

        port = crawler.settings.getint("PROMETHEUS_PORT", 9410)
        # All interfaces by design: Prometheus scrapes scrapy-app:9410 across the
        # compose/k8s network. Override with PROMETHEUS_HOST=127.0.0.1 locally.
        host = crawler.settings.get("PROMETHEUS_HOST", "0.0.0.0")  # nosec B104

        ext = cls(port=port, host=host)

        crawler.signals.connect(ext.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(ext.spider_closed, signal=signals.spider_closed)
        crawler.signals.connect(ext.item_scraped, signal=signals.item_scraped)
        crawler.signals.connect(ext.item_dropped, signal=signals.item_dropped)
        crawler.signals.connect(ext.spider_error, signal=signals.spider_error)
        crawler.signals.connect(ext.request_scheduled, signal=signals.request_scheduled)
        crawler.signals.connect(ext.request_dropped, signal=signals.request_dropped)
        crawler.signals.connect(ext.response_received, signal=signals.response_received)
        crawler.signals.connect(ext.request_reached_downloader, signal=signals.request_reached_downloader)
        crawler.signals.connect(ext.response_downloaded, signal=signals.response_downloaded)

        return ext

    def start_server(self):
        if not self.server_started:
            try:
                start_http_server(self.port, addr=self.host)
                self.server_started = True
                logger.info(f"Prometheus metrics server started on {self.host}:{self.port}")
                logger.info(f"Metrics endpoint: http://{self.host}:{self.port}/metrics")
            except Exception as e:
                logger.error(f"Failed to start Prometheus server: {e}")

    def spider_opened(self, spider: Spider):
        self.start_server()

        self.runs.open(spider)

        SPIDER_OPENED.labels(spider=spider.name).set(1)
        logger.info(f"Spider opened: {spider.name} at {time.strftime('%Y-%m-%d %H:%M:%S')}")

    def spider_closed(self, spider: Spider, reason: str):
        started, tallies = self.runs.close(spider)
        if started is not None:
            duration = time.time() - started
            CRAWL_DURATION.labels(spider=spider.name).set(duration)
            logger.info(f"Spider {spider.name} crawl duration: {duration:.2f} seconds")

        if tallies:
            total_skipped = sum(tallies.values())
            tally_str = ", ".join(
                [f"{why}: {count}" for why, count in sorted(tallies.items())]
            )
            logger.info(f" FINAL SKIPPED URLs SUMMARY - Total: {total_skipped} | {tally_str}")

        SPIDER_OPENED.labels(spider=spider.name).set(0)
        SPIDER_CLOSED.labels(spider=spider.name, reason=reason).inc()
        logger.info(f"Spider closed: {spider.name}, reason: {reason}")

    def item_scraped(self, item: Any, spider: Spider):
        ITEMS_SCRAPED.labels(spider=spider.name).inc()

        if isinstance(item, dict) and item.get("skip_reason"):
            skip_reason = bounded_label(item["skip_reason"])
            URLS_SKIPPED.labels(spider=spider.name, skip_reason=skip_reason).inc()

            self.runs.tally(spider, skip_reason)

    def item_dropped(self, item: Any, spider: Spider, exception: Exception):
        exception_type = type(exception).__name__ if exception else "Unknown"

        drop_reason = "Unknown"
        if isinstance(exception, DropItem):
            drop_reason = str(exception)[:50]
        elif exception:
            drop_reason = exception_type

        ITEMS_DROPPED.labels(spider=spider.name).inc()
        logger.debug(f"Item dropped in {spider.name}: {drop_reason}")

    def spider_error(self, failure, response, spider):
        exception_type = failure.type.__name__ if hasattr(failure, "type") else "Unknown"

        SPIDER_ERRORS.labels(spider=spider.name, exception_type=exception_type).inc()
        logger.error(f"Spider error in {spider.name}: {exception_type} - {failure.getErrorMessage()}")

    def request_scheduled(self, request: Request, spider: Spider):
        REQUESTS_TOTAL.labels(spider=spider.name, method=request.method).inc()

    def request_dropped(self, request: Request, spider: Spider):
        drop_reason = "filtered"
        if hasattr(request, "meta"):
            if request.meta.get("dont_filter"):
                drop_reason = "scheduler"
            elif request.meta.get("duplicate"):
                drop_reason = "duplicate"

        REQUESTS_DROPPED.labels(spider=spider.name, reason=drop_reason).inc()
        URLS_SKIPPED.labels(spider=spider.name, skip_reason=drop_reason).inc()

        total_skipped, tallies = self.runs.tally(spider, drop_reason)
        if total_skipped % 100 == 0:
            tally_str = ", ".join(
                [f"{why}: {count}" for why, count in sorted(tallies.items())]
            )
            logger.info(f" SKIPPED URLs - Total: {total_skipped} | {tally_str}")

        logger.debug(f"Request dropped in {spider.name}: {drop_reason} - {request.url}")

    def response_received(self, response: Response, request: Request, spider: Spider):
        RESPONSES_TOTAL.labels(spider=spider.name, status_code=response.status).inc()

        if hasattr(request, "meta") and "download_latency" in request.meta:
            latency = request.meta["download_latency"]
            RESPONSE_TIME.labels(spider=spider.name).observe(latency)

            if latency > 5.0:
                logger.warning(f"Slow response in {spider.name}: {latency:.2f}s for {response.url}")

        if response.status in [403, 503]:
            logger.warning(f"Blocked/Error response in {spider.name}: {response.status} from {response.url}")
        elif response.status >= 500:
            logger.error(f"Server error in {spider.name}: {response.status} from {response.url}")

    def request_reached_downloader(self, request: Request, spider: Spider):
        if hasattr(request, "body") and request.body:
            DOWNLOADER_REQUEST_BYTES.labels(spider=spider.name).inc(len(request.body))

    def response_downloaded(self, response: Response, request: Request, spider: Spider):
        if hasattr(response, "body") and response.body:
            DOWNLOADER_RESPONSE_BYTES.labels(spider=spider.name).inc(len(response.body))
