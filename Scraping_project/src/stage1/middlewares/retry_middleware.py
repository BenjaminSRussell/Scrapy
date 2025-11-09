import logging
import random
import time
from typing import Any

from scrapy import Request, Spider
from scrapy.downloadermiddlewares.retry import RetryMiddleware
from scrapy.http import Response
from scrapy.utils.response import response_status_message

logger = logging.getLogger(__name__)

class IntelligentRetryMiddleware(RetryMiddleware):

    TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}

    PERMANENT_STATUS_CODES = {400, 401, 403, 404, 410}

    def __init__(self, settings):
        super().__init__(settings)

        self.backoff_base = settings.getint("RETRY_BACKOFF_BASE", 2)
        self.backoff_max = settings.getint("RETRY_BACKOFF_MAX", 300)

        self.domain_retry_counts = {}
        self.domain_last_retry = {}
        self._rng = random.Random(42)

    def _calculate_backoff_delay(
        self,
        attempt: int,
        base: float | None = None,
        max_backoff: float | None = None,
        jitter: float | None = None,
    ) -> float:
        base = base if base is not None else getattr(self, "base_backoff", 0.5)
        max_backoff = max_backoff if max_backoff is not None else getattr(self, "max_backoff", 60.0)
        delay = base * (2 ** (attempt - 1))
        j = 0.0
        if jitter:
            j = (self._rng.random() * 2 - 1.0) * jitter
        delay = min(max(delay + j, 0.0), max_backoff)
        return delay

    def _classify_status(self, status: int) -> str:
        if status in self.TRANSIENT_STATUS_CODES:
            return "retry"
        if status in self.PERMANENT_STATUS_CODES:
            return "fail"
        return "pass"

    def process_response(self, request: Request, response: Response, spider: Spider):
        action = self._classify_status(response.status)

        if action == "retry":
            return self._retry_with_backoff(request, response, spider)

        if action == "fail":
            logger.info(f"Permanent error {response.status} for {request.url[:80]}, not retrying")
            return response

        return response

    def process_exception(self, request: Request, exception: Exception, spider: Spider):
        if isinstance(exception, self.EXCEPTIONS_TO_RETRY):
            logger.debug(f"Retryable exception for {request.url[:80]}: {exception}")
            return self._retry_with_backoff(
                request,
                reason=exception.__class__.__name__,
                spider=spider,
            )

        return None

    def _retry_with_backoff(
        self,
        request: Request,
        reason: Any = None,
        spider: Spider | None = None,
    ) -> Request | None:
        """Retry request with exponential backoff.

        Args:
            request: Request to retry
            reason: Reason for retry (Response or exception)
            spider: Spider instance

        Returns:
            New Request with backoff delay or None if max retries exceeded
        """
        retries = request.meta.get("retry_times", 0) + 1
        max_retry_times = self.max_retry_times

        if retries <= max_retry_times:
            delay = self._compute_backoff(retries)

            if isinstance(reason, Response):
                reason_msg = response_status_message(reason.status)
            else:
                reason_msg = str(reason)

            logger.info(
                f"Retry {retries}/{max_retry_times} for {request.url[:80]} "
                f"(reason: {reason_msg}) - waiting {delay:.1f}s"
            )

            retry_request = request.copy()
            retry_request.meta["retry_times"] = retries
            retry_request.meta["retry_delay"] = delay
            retry_request.dont_filter = True

            retry_request.priority = request.priority - (retries * 10)

            if spider:
                from twisted.internet import reactor

                crawler = getattr(spider, "crawler", None)
                engine: Any = getattr(crawler, "engine", None)
                if engine is not None and hasattr(engine, "schedule"):
                    reactor.callLater(  # type: ignore[attr-defined]
                        delay,
                        engine.schedule,
                        retry_request,
                        spider,
                    )

            return retry_request

        else:
            logger.warning(f"Max retries ({max_retry_times}) exceeded for {request.url[:80]}")
            return None

    def _compute_backoff(self, retry_count: int) -> float:
        delay = self.backoff_base**retry_count

        jitter = random.uniform(0, 0.1 * delay)
        delay += jitter

        delay = min(delay, self.backoff_max)

        return delay

class RateLimitMiddleware:

    def __init__(self, settings):
        self.domain_request_times = {}
        self.min_delay_per_domain = settings.getfloat("MIN_DELAY_PER_DOMAIN", 0.5)

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings)

    def process_request(self, request: Request, spider: Spider):
        from urllib.parse import urlparse

        domain = urlparse(request.url).netloc

        last_time = self.domain_request_times.get(domain, 0)
        now = time.time()

        elapsed = now - last_time

        if elapsed < self.min_delay_per_domain:
            delay = self.min_delay_per_domain - elapsed
            logger.debug(f"Rate limiting {domain}: waiting {delay:.2f}s")
            time.sleep(delay)

        self.domain_request_times[domain] = time.time()

class CircuitBreakerMiddleware:

    def __init__(self, redis_manager):
        self.redis = redis_manager
        self.domain_error_counts = {}

    @classmethod
    def from_crawler(cls, crawler):
        from src.core.config import Config
        from src.common.redis_manager_deprecated import get_redis_manager

        config = Config.get_instance()
        redis_config = config.redis_config

        redis_manager = get_redis_manager(
            host=redis_config.get("host", "localhost"),
            port=redis_config.get("port", 6379),
            db=redis_config.get("db", 0),
            password=redis_config.get("password"),
        )

        return cls(redis_manager)

    def process_request(self, request: Request, spider: Spider):
        from urllib.parse import urlparse

        from scrapy.http import Response

        domain = urlparse(request.url).netloc

        if self.redis.is_circuit_open(domain):
            logger.warning(f"Circuit breaker open for {domain}, skipping request")

            return Response(
                url=request.url,
                status=503,
                body=b"Circuit breaker open",
            )

        return None

    def process_response(self, request: Request, response: Response, spider: Spider):
        from urllib.parse import urlparse

        domain = urlparse(request.url).netloc

        if 500 <= response.status < 600:
            self.domain_error_counts[domain] = self.domain_error_counts.get(domain, 0) + 1

            error_threshold = 10
            if self.domain_error_counts[domain] >= error_threshold:
                self.redis.open_circuit(
                    domain,
                    duration_seconds=900,
                    reason=f"high_error_rate_{response.status}",
                )

                logger.error(f"Circuit breaker opened for {domain} ({self.domain_error_counts[domain]} errors)")

                self.domain_error_counts[domain] = 0

        return response
