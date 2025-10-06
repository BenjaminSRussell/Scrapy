"""
Custom Scrapy Prometheus Exporter Extension
============================================
This extension exposes Scrapy metrics in Prometheus format via an HTTP endpoint.

Metrics exposed:
- scrapy_items_scraped_total: Total items scraped
- scrapy_items_dropped_total: Total items dropped
- scrapy_requests_total: Total requests made
- scrapy_responses_total: Total responses received (by status code)
- scrapy_response_time_seconds: Response time histogram
- scrapy_spider_opened: Number of spiders opened
- scrapy_spider_closed: Number of spiders closed
"""

import logging
from typing import Any

from prometheus_client import Counter, Gauge, Histogram, start_http_server
from scrapy import Spider, signals
from scrapy.crawler import Crawler
from scrapy.exceptions import NotConfigured
from scrapy.http import Request, Response

logger = logging.getLogger(__name__)


# Define Prometheus metrics
ITEMS_SCRAPED = Counter(
    'scrapy_items_scraped_total',
    'Total number of items scraped',
    ['spider']
)

ITEMS_DROPPED = Counter(
    'scrapy_items_dropped_total',
    'Total number of items dropped',
    ['spider']
)

REQUESTS_TOTAL = Counter(
    'scrapy_requests_total',
    'Total number of requests made',
    ['spider', 'method']
)

RESPONSES_TOTAL = Counter(
    'scrapy_responses_total',
    'Total number of responses received',
    ['spider', 'status_code']
)

RESPONSE_TIME = Histogram(
    'scrapy_response_time_seconds',
    'Response time in seconds',
    ['spider'],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, float('inf'))
)

SPIDER_OPENED = Gauge(
    'scrapy_spider_opened',
    'Number of spiders currently running',
    ['spider']
)

SPIDER_CLOSED = Counter(
    'scrapy_spider_closed_total',
    'Total number of spiders closed',
    ['spider', 'reason']
)

DOWNLOADER_REQUEST_BYTES = Counter(
    'scrapy_downloader_request_bytes_total',
    'Total bytes sent in requests',
    ['spider']
)

DOWNLOADER_RESPONSE_BYTES = Counter(
    'scrapy_downloader_response_bytes_total',
    'Total bytes received in responses',
    ['spider']
)


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
    def from_crawler(cls, crawler: Crawler) -> 'PrometheusExtension':
        """Factory method to create extension from crawler.

        Args:
            crawler: Scrapy crawler instance

        Returns:
            PrometheusExtension instance

        Raises:
            NotConfigured: If extension is disabled or required settings missing
        """
        # Check if extension is enabled
        if not crawler.settings.getbool('PROMETHEUS_ENABLED', True):
            raise NotConfigured('Prometheus extension is disabled')

        # Get settings
        port = crawler.settings.getint('PROMETHEUS_PORT', 9410)
        host = crawler.settings.get('PROMETHEUS_HOST', '0.0.0.0')

        # Create extension instance
        ext = cls(port=port, host=host)

        # Connect signals
        crawler.signals.connect(ext.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(ext.spider_closed, signal=signals.spider_closed)
        crawler.signals.connect(ext.item_scraped, signal=signals.item_scraped)
        crawler.signals.connect(ext.item_dropped, signal=signals.item_dropped)
        crawler.signals.connect(ext.request_scheduled, signal=signals.request_scheduled)
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
        """Called when spider is opened.

        Args:
            spider: Spider instance
        """
        # Start server when first spider opens
        self.start_server()

        SPIDER_OPENED.labels(spider=spider.name).set(1)
        logger.info(f"Spider opened: {spider.name}")

    def spider_closed(self, spider: Spider, reason: str):
        """Called when spider is closed.

        Args:
            spider: Spider instance
            reason: Reason for closure
        """
        SPIDER_OPENED.labels(spider=spider.name).set(0)
        SPIDER_CLOSED.labels(spider=spider.name, reason=reason).inc()
        logger.info(f"Spider closed: {spider.name}, reason: {reason}")

    def item_scraped(self, item: Any, spider: Spider):
        """Called when item is scraped.

        Args:
            item: Scraped item
            spider: Spider instance
        """
        ITEMS_SCRAPED.labels(spider=spider.name).inc()

    def item_dropped(self, item: Any, spider: Spider, exception: Exception):
        """Called when item is dropped.

        Args:
            item: Dropped item
            spider: Spider instance
            exception: Exception that caused the drop
        """
        ITEMS_DROPPED.labels(spider=spider.name).inc()

    def request_scheduled(self, request: Request, spider: Spider):
        """Called when request is scheduled.

        Args:
            request: Request instance
            spider: Spider instance
        """
        REQUESTS_TOTAL.labels(spider=spider.name, method=request.method).inc()

    def response_received(self, response: Response, request: Request, spider: Spider):
        """Called when response is received.

        Args:
            response: Response instance
            request: Request instance
            spider: Spider instance
        """
        RESPONSES_TOTAL.labels(spider=spider.name, status_code=response.status).inc()

        # Calculate response time if available
        if hasattr(request, 'meta') and 'download_latency' in request.meta:
            latency = request.meta['download_latency']
            RESPONSE_TIME.labels(spider=spider.name).observe(latency)

    def request_reached_downloader(self, request: Request, spider: Spider):
        """Called when request reaches downloader.

        Args:
            request: Request instance
            spider: Spider instance
        """
        # Track request bytes if available
        if hasattr(request, 'body') and request.body:
            DOWNLOADER_REQUEST_BYTES.labels(spider=spider.name).inc(len(request.body))

    def response_downloaded(self, response: Response, request: Request, spider: Spider):
        """Called when response is downloaded.

        Args:
            response: Response instance
            request: Request instance
            spider: Spider instance
        """
        # Track response bytes
        if hasattr(response, 'body') and response.body:
            DOWNLOADER_RESPONSE_BYTES.labels(spider=spider.name).inc(len(response.body))
