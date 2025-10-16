"""
Custom Scrapy Prometheus Exporter Extension
============================================
This extension exposes comprehensive Scrapy metrics in Prometheus format via an HTTP endpoint.

Metrics exposed:
- scrapy_spider_opened: Crawl start time, spider name (Gauge)
- scrapy_spider_closed_total: Crawl end time, finish reason (Counter)
- scrapy_responses_total: HTTP status codes (Counter)
- scrapy_response_time_seconds: Response latency histogram
- scrapy_items_scraped_total: Items/sec throughput (Counter)
- scrapy_items_dropped_total: Dropped items by reason (Counter)
- scrapy_spider_errors_total: Exception count by type (Counter)
- scrapy_requests_dropped_total: Dropped requests (Counter)
- scrapy_requests_total: Total requests made (Counter)
- scrapy_downloader_request_bytes_total: Bytes sent (Counter)
- scrapy_downloader_response_bytes_total: Bytes received (Counter)
- scrapy_crawl_duration_seconds: Total crawl duration (Gauge)

Test-specific metrics (for CI observability):
- test_import_path_adjustments_total: Incremented when a test adjusts sys.path.
- module_import_failures_total: Incremented when a test fails to import a module.

All metrics are consolidated in this single extension for efficient Prometheus scraping.
"""

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

# Track crawl timing and skip tallies regardless of Prometheus availability
CRAWL_START_TIMES: dict[str, float] = {}
SKIPPED_URL_TALLIES: dict[str, dict[str, int]] = {}


# Define Prometheus metrics only if available
if PROMETHEUS_AVAILABLE:
    # --- Test Environment Metrics ---
    TEST_IMPORT_PATH_ADJUSTMENTS = Counter(
        "test_import_path_adjustments_total",
        "Total number of times a test file dynamically adjusted sys.path to ensure src imports work.",
        ["test_file"],
    )

    MODULE_IMPORT_FAILURES = Counter(
        "module_import_failures_total",
        "Total number of times a module failed to import within a test.",
        ["module_name"],
    )

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

else:
    # Dummy variables when Prometheus is not available
    ITEMS_SCRAPED = ITEMS_DROPPED = REQUESTS_TOTAL = RESPONSES_TOTAL = None
    RESPONSE_TIME = SPIDER_OPENED = SPIDER_CLOSED = SPIDER_ERRORS = None
    REQUESTS_DROPPED = DOWNLOADER_REQUEST_BYTES = DOWNLOADER_RESPONSE_BYTES = None
    CRAWL_DURATION = URLS_SKIPPED = None
    NEW_URLS_FOUND_PER_MINUTE = AVERAGE_FILE_SIZE_BYTES = None
    OFFSITE_LINKS_FOUND = OFFSITE_CANDIDATES_SAVED = None
    CRAWLER_CONTENT_SUMMARY = None


class PrometheusExtension:
    """Scrapy extension that exports metrics to Prometheus."""

    def __init__(self, port: int, host: str):
        """Initialize the Prometheus extension.

        Args:
            port: Port to expose metrics endpoint
            host: Host to bind to (0.0.0.0 for all interfaces)
        """
        self.port = port
        self.host = host
        self.server_started = False

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "PrometheusExtension":
        """Factory method to create extension from crawler.

        Args:
            crawler: Scrapy crawler instance

        Returns:
            PrometheusExtension instance

        Raises:
            NotConfigured: If extension is disabled or required settings missing
        """
        # Check if Prometheus is available
        if not PROMETHEUS_AVAILABLE:
            logger.warning("Prometheus extension disabled - prometheus_client not installed")
            raise NotConfigured("prometheus_client library not available")

        # Check if extension is enabled
        if not crawler.settings.getbool("PROMETHEUS_ENABLED", True):
            raise NotConfigured("Prometheus extension is disabled")

        # Get settings
        port = crawler.settings.getint("PROMETHEUS_PORT", 9410)
        host = crawler.settings.get("PROMETHEUS_HOST", "0.0.0.0")

        # Create extension instance
        ext = cls(port=port, host=host)

        # Connect signals for comprehensive metrics collection
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
        """Start the Prometheus HTTP server (only once)."""
        if not self.server_started:
            try:
                start_http_server(self.port, addr=self.host)
                self.server_started = True
                logger.info(f"Prometheus metrics server started on {self.host}:{self.port}")
                logger.info(f"Metrics endpoint: http://{self.host}:{self.port}/metrics")
            except Exception as e:
                logger.error(f"Failed to start Prometheus server: {e}")

    def spider_opened(self, spider: Spider):
        """Called when spider is opened - Track crawl start time.

        Args:
            spider: Spider instance
        """
        # Start server when first spider opens
        self.start_server()

        # Record crawl start time
        CRAWL_START_TIMES[spider.name] = time.time()

        # Initialize skipped URL tallies for this spider
        SKIPPED_URL_TALLIES[spider.name] = {}

        SPIDER_OPENED.labels(spider=spider.name).set(1)
        logger.info(f"Spider opened: {spider.name} at {time.strftime('%Y-%m-%d %H:%M:%S')}")

    def spider_closed(self, spider: Spider, reason: str):
        """Called when spider is closed - Calculate and record total crawl duration.

        Args:
            spider: Spider instance
            reason: Reason for closure (finished, shutdown, etc.)
        """
        # Calculate crawl duration
        if spider.name in CRAWL_START_TIMES:
            duration = time.time() - CRAWL_START_TIMES[spider.name]
            CRAWL_DURATION.labels(spider=spider.name).set(duration)
            logger.info(f"Spider {spider.name} crawl duration: {duration:.2f} seconds")
            del CRAWL_START_TIMES[spider.name]

        # Log final skipped URL tally
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
        """Called when item is scraped - Track scrape rate (items/sec).

        Args:
            item: Scraped item
            spider: Spider instance
        """
        ITEMS_SCRAPED.labels(spider=spider.name).inc()

        # Track skipped URLs if the item has skip_reason
        if isinstance(item, dict) and item.get("skip_reason"):
            skip_reason = item["skip_reason"]
            URLS_SKIPPED.labels(spider=spider.name, skip_reason=skip_reason).inc()

            # Update tally
            if spider.name not in SKIPPED_URL_TALLIES:
                SKIPPED_URL_TALLIES[spider.name] = {}
            SKIPPED_URL_TALLIES[spider.name][skip_reason] = SKIPPED_URL_TALLIES[spider.name].get(skip_reason, 0) + 1

    def item_dropped(self, item: Any, spider: Spider, exception: Exception):
        """Called when item is dropped - Track drop reasons for data quality monitoring.

        Args:
            item: Dropped item
            spider: Spider instance
            exception: Exception that caused the drop (e.g., DropItem)
        """
        # Extract exception type/reason
        exception_type = type(exception).__name__ if exception else "Unknown"

        # Try to extract drop reason from DropItem exception message
        drop_reason = "Unknown"
        if isinstance(exception, DropItem):
            drop_reason = str(exception)[:50]  # Truncate long messages
        elif exception:
            drop_reason = exception_type

        ITEMS_DROPPED.labels(spider=spider.name).inc()
        logger.debug(f"Item dropped in {spider.name}: {drop_reason}")

    def spider_error(self, failure, response, spider):
        """Called when spider encounters an error - Monitor critical failures.

        Args:
            failure: Twisted Failure instance
            response: Response that caused the error
            spider: Spider instance
        """
        # Extract exception type for categorization
        exception_type = failure.type.__name__ if hasattr(failure, "type") else "Unknown"

        SPIDER_ERRORS.labels(spider=spider.name, exception_type=exception_type).inc()
        logger.error(f"Spider error in {spider.name}: {exception_type} - {failure.getErrorMessage()}")

    def request_scheduled(self, request: Request, spider: Spider):
        """Called when request is scheduled.

        Args:
            request: Request instance
            spider: Spider instance
        """
        REQUESTS_TOTAL.labels(spider=spider.name, method=request.method).inc()

    def request_dropped(self, request: Request, spider: Spider):
        """Called when request is dropped - Track filtered/duplicate requests.

        Args:
            request: Dropped request
            spider: Spider instance
        """
        # Determine drop reason from request metadata
        drop_reason = "filtered"
        if hasattr(request, "meta"):
            if request.meta.get("dont_filter"):
                drop_reason = "scheduler"
            elif request.meta.get("duplicate"):
                drop_reason = "duplicate"

        REQUESTS_DROPPED.labels(spider=spider.name, reason=drop_reason).inc()
        URLS_SKIPPED.labels(spider=spider.name, skip_reason=drop_reason).inc()

        # Update tally
        if spider.name not in SKIPPED_URL_TALLIES:
            SKIPPED_URL_TALLIES[spider.name] = {}
        SKIPPED_URL_TALLIES[spider.name][drop_reason] = SKIPPED_URL_TALLIES[spider.name].get(drop_reason, 0) + 1

        # Log running tally every 100 skipped URLs
        total_skipped = sum(SKIPPED_URL_TALLIES[spider.name].values())
        if total_skipped % 100 == 0:
            tally_str = ", ".join(
                [f"{reason}: {count}" for reason, count in sorted(SKIPPED_URL_TALLIES[spider.name].items())]
            )
            logger.info(f"🔄 SKIPPED URLs - Total: {total_skipped} | {tally_str}")

        logger.debug(f"Request dropped in {spider.name}: {drop_reason} - {request.url}")

    def response_received(self, response: Response, request: Request, spider: Spider):
        """Called when response is received - Monitor HTTP status codes and latency.

        Args:
            response: Response instance
            request: Request instance
            spider: Spider instance
        """
        # Track response status codes (403 blocks, 503 errors, 301/302 redirects, 500 errors)
        RESPONSES_TOTAL.labels(spider=spider.name, status_code=response.status).inc()

        # Calculate and track response time (latency)
        if hasattr(request, "meta") and "download_latency" in request.meta:
            latency = request.meta["download_latency"]
            RESPONSE_TIME.labels(spider=spider.name).observe(latency)

            # Log slow responses
            if latency > 5.0:
                logger.warning(f"Slow response in {spider.name}: {latency:.2f}s for {response.url}")

        # Log specific status codes of interest
        if response.status in [403, 503]:
            logger.warning(f"Blocked/Error response in {spider.name}: {response.status} from {response.url}")
        elif response.status >= 500:
            logger.error(f"Server error in {spider.name}: {response.status} from {response.url}")

    def request_reached_downloader(self, request: Request, spider: Spider):
        """Called when request reaches downloader.

        Args:
            request: Request instance
            spider: Spider instance
        """
        # Track request bytes if available
        if hasattr(request, "body") and request.body:
            DOWNLOADER_REQUEST_BYTES.labels(spider=spider.name).inc(len(request.body))

    def response_downloaded(self, response: Response, request: Request, spider: Spider):
        """Called when response is downloaded.

        Args:
            response: Response instance
            request: Request instance
            spider: Spider instance
        """
        # Track response bytes
        if hasattr(response, "body") and response.body:
            DOWNLOADER_RESPONSE_BYTES.labels(spider=spider.name).inc(len(response.body))
