"""Intelligent Retry Middleware with Exponential Backoff.

Implements smart retry logic:
- Exponential backoff for transient errors
- Immediate failure for permanent errors
- Per-domain rate limiting
"""

import logging
import time

from scrapy import Request, Spider
from scrapy.downloadermiddlewares.retry import RetryMiddleware
from scrapy.http import Response
from scrapy.utils.response import response_status_message

logger = logging.getLogger(__name__)


class IntelligentRetryMiddleware(RetryMiddleware):
    """Enhanced retry middleware with exponential backoff."""

    # Transient errors - should retry
    TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}

    # Permanent errors - do NOT retry
    PERMANENT_STATUS_CODES = {400, 401, 403, 404, 410}

    def __init__(self, settings):
        """Initialize middleware.

        Args:
            settings: Scrapy settings
        """
        super().__init__(settings)

        # Exponential backoff configuration
        self.backoff_base = settings.getint('RETRY_BACKOFF_BASE', 2)
        self.backoff_max = settings.getint('RETRY_BACKOFF_MAX', 300)  # 5 minutes max

        # Per-domain retry tracking
        self.domain_retry_counts = {}
        self.domain_last_retry = {}

    def process_response(self, request: Request, response: Response, spider: Spider):
        """Process response and determine if retry needed.

        Args:
            request: Scrapy request
            response: Scrapy response
            spider: Scrapy spider

        Returns:
            Response or new Request (for retry)
        """
        # Check if this is a permanent error
        if response.status in self.PERMANENT_STATUS_CODES:
            logger.info(
                f"Permanent error {response.status} for {request.url[:80]}, "
                f"not retrying"
            )
            return response  # Don't retry

        # Check if this is a transient error that should retry
        if response.status in self.TRANSIENT_STATUS_CODES:
            return self._retry_with_backoff(request, response, spider)

        # Success or other status - pass through
        return response

    def process_exception(self, request: Request, exception: Exception, spider: Spider):
        """Process exception and determine if retry needed.

        Args:
            request: Scrapy request
            exception: Exception that occurred
            spider: Scrapy spider

        Returns:
            New Request (for retry) or None
        """
        # Check if this exception type should be retried
        if isinstance(exception, self.EXCEPTIONS_TO_RETRY):
            logger.debug(f"Retryable exception for {request.url[:80]}: {exception}")
            return self._retry_with_backoff(
                request,
                reason=exception.__class__.__name__,
                spider=spider,
            )

        # Non-retryable exception
        return None

    def _retry_with_backoff(
        self,
        request: Request,
        reason: any = None,
        spider: Spider = None,
    ) -> Request | None:
        """Retry request with exponential backoff.

        Args:
            request: Request to retry
            reason: Reason for retry (Response or exception)
            spider: Spider instance

        Returns:
            New Request with backoff delay or None if max retries exceeded
        """
        retries = request.meta.get('retry_times', 0) + 1
        max_retry_times = self.max_retry_times

        if retries <= max_retry_times:
            # Calculate exponential backoff delay
            delay = self._calculate_backoff_delay(retries)

            # Extract reason message
            if isinstance(reason, Response):
                reason_msg = response_status_message(reason.status)
                status = reason.status
            else:
                reason_msg = str(reason)
                status = None

            logger.info(
                f"Retry {retries}/{max_retry_times} for {request.url[:80]} "
                f"(reason: {reason_msg}) - waiting {delay:.1f}s"
            )

            # Create retry request with delay
            retry_request = request.copy()
            retry_request.meta['retry_times'] = retries
            retry_request.meta['retry_delay'] = delay
            retry_request.dont_filter = True

            # Add delay to request priority (Scrapy processes by priority)
            # Lower priority = later execution
            retry_request.priority = request.priority - (retries * 10)

            # Schedule retry after delay
            if spider:
                from twisted.internet import reactor
                deferred = reactor.callLater(
                    delay,
                    spider.crawler.engine.schedule,
                    retry_request,
                    spider,
                )

            return retry_request

        else:
            logger.warning(
                f"Max retries ({max_retry_times}) exceeded for {request.url[:80]}"
            )
            return None

    def _calculate_backoff_delay(self, retry_count: int) -> float:
        """Calculate exponential backoff delay.

        Formula: min(backoff_base^retry_count, backoff_max)

        Args:
            retry_count: Current retry attempt (1-indexed)

        Returns:
            Delay in seconds
        """
        delay = self.backoff_base ** retry_count

        # Add small random jitter to prevent thundering herd
        import random
        jitter = random.uniform(0, 0.1 * delay)
        delay += jitter

        delay = min(delay, self.backoff_max)

        return delay


class RateLimitMiddleware:
    """Per-domain rate limiting middleware."""

    def __init__(self, settings):
        """Initialize middleware.

        Args:
            settings: Scrapy settings
        """
        self.domain_request_times = {}
        self.min_delay_per_domain = settings.getfloat('MIN_DELAY_PER_DOMAIN', 0.5)

    @classmethod
    def from_crawler(cls, crawler):
        """Create middleware from crawler.

        Args:
            crawler: Scrapy crawler

        Returns:
            Middleware instance
        """
        return cls(crawler.settings)

    def process_request(self, request: Request, spider: Spider):
        """Process request and enforce rate limit.

        Args:
            request: Scrapy request
            spider: Scrapy spider
        """
        from urllib.parse import urlparse

        domain = urlparse(request.url).netloc

        # Check last request time for this domain
        last_time = self.domain_request_times.get(domain, 0)
        now = time.time()

        elapsed = now - last_time

        if elapsed < self.min_delay_per_domain:
            # Need to wait
            delay = self.min_delay_per_domain - elapsed
            logger.debug(f"Rate limiting {domain}: waiting {delay:.2f}s")
            time.sleep(delay)

        # Update last request time
        self.domain_request_times[domain] = time.time()


class CircuitBreakerMiddleware:
    """Circuit breaker middleware using Redis.

    Prevents requests to domains that are experiencing high error rates.
    """

    def __init__(self, redis_manager):
        """Initialize middleware.

        Args:
            redis_manager: RedisManager instance
        """
        self.redis = redis_manager
        self.domain_error_counts = {}

    @classmethod
    def from_crawler(cls, crawler):
        """Create middleware from crawler.

        Args:
            crawler: Scrapy crawler

        Returns:
            Middleware instance
        """
        # Get Redis manager
        from src.common.config import get_config
        from src.common.redis_manager import get_redis_manager

        config = get_config()
        redis_config = config.redis_config

        redis_manager = get_redis_manager(
            host=redis_config.get('host', 'localhost'),
            port=redis_config.get('port', 6379),
            db=redis_config.get('db', 0),
            password=redis_config.get('password'),
        )

        return cls(redis_manager)

    def process_request(self, request: Request, spider: Spider):
        """Check if circuit breaker is open for domain.

        Args:
            request: Scrapy request
            spider: Scrapy spider

        Returns:
            None to continue, or Response to short-circuit
        """
        from urllib.parse import urlparse

        from scrapy.http import Response

        domain = urlparse(request.url).netloc

        # Check if circuit is open
        if self.redis.is_circuit_open(domain):
            logger.warning(f"Circuit breaker open for {domain}, skipping request")

            # Return 503 response to signal unavailable
            return Response(
                url=request.url,
                status=503,
                body=b"Circuit breaker open",
            )

        return None

    def process_response(self, request: Request, response: Response, spider: Spider):
        """Track errors and open circuit if threshold exceeded.

        Args:
            request: Scrapy request
            response: Scrapy response
            spider: Scrapy spider

        Returns:
            Response
        """
        from urllib.parse import urlparse

        domain = urlparse(request.url).netloc

        # Track 5xx errors
        if 500 <= response.status < 600:
            # Increment error count
            self.domain_error_counts[domain] = self.domain_error_counts.get(domain, 0) + 1

            # Check threshold (e.g., 10 errors)
            error_threshold = 10
            if self.domain_error_counts[domain] >= error_threshold:
                # Open circuit breaker
                self.redis.open_circuit(
                    domain,
                    duration_seconds=900,  # 15 minutes
                    reason=f"high_error_rate_{response.status}",
                )

                logger.error(
                    f"Circuit breaker opened for {domain} "
                    f"({self.domain_error_counts[domain]} errors)"
                )

                # Reset counter
                self.domain_error_counts[domain] = 0

        return response
