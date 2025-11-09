import logging
import time
from typing import Any

try:
    from prometheus_client import Counter, Gauge, Histogram, start_http_server

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    Counter = Gauge = Histogram = None
    start_http_server = None

from scrapy import Spider, signals
from scrapy.crawler import Crawler
from scrapy.exceptions import DropItem, NotConfigured
from scrapy.http import Request, Response

logger = logging.getLogger(__name__)

CRAWL_START_TIMES: dict[str, float] = {}
SKIPPED_URL_TALLIES: dict[str, dict[str, int]] = {}

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

class PrometheusExtension:

    def __init__(self, port: int, host: str):
        self.port = port
        self.host = host
        self.server_started = False

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "PrometheusExtension":
        if not PROMETHEUS_AVAILABLE:
            logger.warning("Prometheus extension disabled - prometheus_client not installed")
            raise NotConfigured("prometheus_client library not available")

        if not crawler.settings.getbool("PROMETHEUS_ENABLED", True):
            raise NotConfigured("Prometheus extension is disabled")

        port = crawler.settings.getint("PROMETHEUS_PORT", 9410)
        host = crawler.settings.get("PROMETHEUS_HOST", "0.0.0.0")

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

        CRAWL_START_TIMES[spider.name] = time.time()

        SKIPPED_URL_TALLIES[spider.name] = {}

        SPIDER_OPENED.labels(spider=spider.name).set(1)
        logger.info(f"Spider opened: {spider.name} at {time.strftime('%Y-%m-%d %H:%M:%S')}")

    def spider_closed(self, spider: Spider, reason: str):
        if spider.name in CRAWL_START_TIMES:
            duration = time.time() - CRAWL_START_TIMES[spider.name]
            CRAWL_DURATION.labels(spider=spider.name).set(duration)
            logger.info(f"Spider {spider.name} crawl duration: {duration:.2f} seconds")
            del CRAWL_START_TIMES[spider.name]

        if spider.name in SKIPPED_URL_TALLIES and SKIPPED_URL_TALLIES[spider.name]:
            total_skipped = sum(SKIPPED_URL_TALLIES[spider.name].values())
            tally_str = ", ".join(
                [f"{reason}: {count}" for reason, count in sorted(SKIPPED_URL_TALLIES[spider.name].items())]
            )
            logger.info(f"📊 FINAL SKIPPED URLs SUMMARY - Total: {total_skipped} | {tally_str}")
            del SKIPPED_URL_TALLIES[spider.name]

        SPIDER_OPENED.labels(spider=spider.name).set(0)
        SPIDER_CLOSED.labels(spider=spider.name, reason=reason).inc()
        logger.info(f"Spider closed: {spider.name}, reason: {reason}")

    def item_scraped(self, item: Any, spider: Spider):
        ITEMS_SCRAPED.labels(spider=spider.name).inc()

        if isinstance(item, dict) and item.get("skip_reason"):
            skip_reason = item["skip_reason"]
            URLS_SKIPPED.labels(spider=spider.name, skip_reason=skip_reason).inc()

            if spider.name not in SKIPPED_URL_TALLIES:
                SKIPPED_URL_TALLIES[spider.name] = {}
            SKIPPED_URL_TALLIES[spider.name][skip_reason] = SKIPPED_URL_TALLIES[spider.name].get(skip_reason, 0) + 1

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

        if spider.name not in SKIPPED_URL_TALLIES:
            SKIPPED_URL_TALLIES[spider.name] = {}
        SKIPPED_URL_TALLIES[spider.name][drop_reason] = SKIPPED_URL_TALLIES[spider.name].get(drop_reason, 0) + 1

        total_skipped = sum(SKIPPED_URL_TALLIES[spider.name].values())
        if total_skipped % 100 == 0:
            tally_str = ", ".join(
                [f"{reason}: {count}" for reason, count in sorted(SKIPPED_URL_TALLIES[spider.name].items())]
            )
            logger.info(f"🔄 SKIPPED URLs - Total: {total_skipped} | {tally_str}")

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
