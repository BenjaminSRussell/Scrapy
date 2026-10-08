"""
Retry logic with exponential backoff and circuit breaker pattern.

Phase 7: Resilience utilities for handling transient failures.
"""

import asyncio
import logging
import random
import time
from datetime import datetime
from functools import wraps
from typing import Any, TypeVar, Callable, Optional, Type, Tuple, cast

from src.core.exceptions import (
    PipelineException,
    CircuitBreakerOpen,
    MaxRetriesExceeded,
    RateLimitError,
)

logger = logging.getLogger(__name__)

T = TypeVar('T')
# Signature-preserving decorator type (sync and async callables).
F = TypeVar('F', bound=Callable[..., Any])


class CircuitBreaker:
    """
    Circuit breaker pattern implementation.

    Prevents cascade failures by temporarily blocking calls to failing services.

    States:
    - CLOSED: Normal operation, all calls go through
    - OPEN: Too many failures, block all calls
    - HALF_OPEN: Testing if service recovered, allow limited calls
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: Type[Exception] = Exception,
        name: str = "default"
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.name = name

        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.state = "closed"  # closed, open, half-open

    def can_execute(self) -> bool:
        """Check if circuit allows execution."""
        if self.state == "closed":
            return True

        if self.state == "open":
            # Check if recovery timeout has elapsed
            if self.last_failure_time:
                elapsed = (datetime.now() - self.last_failure_time).total_seconds()
                if elapsed >= self.recovery_timeout:
                    logger.info(f"Circuit breaker '{self.name}' entering half-open state")
                    self.state = "half-open"
                    self.failure_count = 0
                    return True
            return False

        # half-open state - allow one attempt
        return True

    def record_success(self):
        """Record successful execution."""
        if self.state == "half-open":
            self.success_count += 1
            # After a few successes in half-open, close the circuit
            if self.success_count >= 2:
                logger.info(f"Circuit breaker '{self.name}' closed after recovery")
                self.state = "closed"
                self.failure_count = 0
                self.success_count = 0
        else:
            self.failure_count = 0

    def record_failure(self):
        """Record failed execution."""
        self.failure_count += 1
        self.last_failure_time = datetime.now()

        if self.state == "half-open":
            # Failure in half-open goes back to open
            logger.warning(f"Circuit breaker '{self.name}' re-opened after failed recovery attempt")
            self.state = "open"
            self.success_count = 0
        elif self.failure_count >= self.failure_threshold:
            logger.warning(
                f"Circuit breaker '{self.name}' opened after {self.failure_count} failures"
            )
            self.state = "open"

    def get_state(self) -> dict:
        """Get current circuit breaker state for monitoring."""
        return {
            "name": self.name,
            "state": self.state,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure": self.last_failure_time.isoformat() if self.last_failure_time else None,
        }


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retry_on: Tuple[Type[Exception], ...] = (Exception,),
    circuit_breaker: Optional[CircuitBreaker] = None
):
    """
    Retry decorator with exponential backoff and optional circuit breaker.

    Args:
        max_attempts: Maximum number of retry attempts (including first attempt)
        base_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        exponential_base: Base for exponential backoff
        jitter: Add random jitter to prevent thundering herd
        retry_on: Tuple of exception types to retry
        circuit_breaker: Optional circuit breaker instance

    Works on both ``async def`` and plain functions (sync callers block in
    ``time.sleep`` between attempts; don't decorate sync code that runs on an
    event loop thread).

    Example:
        @with_retry(max_attempts=3, retry_on=(NetworkError, TimeoutError))
        async def fetch_url(url: str) -> str:
            return await http_client.get(url)

        @with_retry(max_attempts=5, base_delay=0.5, retry_on=(OSError,))
        def read_manifest(path: str) -> bytes:
            return Path(path).read_bytes()
    """
    def _check_breaker(name: str) -> None:
        if circuit_breaker and not circuit_breaker.can_execute():
            raise CircuitBreakerOpen(f"Circuit breaker is {circuit_breaker.state} for {name}")

    def _on_success(name: str, attempt: int) -> None:
        if circuit_breaker:
            circuit_breaker.record_success()
        if attempt > 0:
            logger.info(f"Successfully executed {name} after {attempt + 1} attempts")

    def _on_failure(name: str, attempt: int, e: Exception) -> Optional[float]:
        """Shared failure handling. Returns the delay before the next attempt, None if out of attempts.

        Re-raises non-retryable PipelineExceptions.
        """
        if circuit_breaker:
            circuit_breaker.record_failure()

        # Don't retry if error is marked as non-retryable
        if isinstance(e, PipelineException) and not e.retryable:
            logger.info(f"Non-retryable error in {name}: {e.category.value}")
            raise e

        # Check for rate limit with retry-after header
        retry_after = e.context.get("retry_after") if isinstance(e, RateLimitError) else None
        if retry_after is not None:
            delay = min(float(retry_after), max_delay)
        else:  # also RateLimitError(retry_after=None), which used to crash min(None, ...)
            # Calculate delay with exponential backoff
            delay = min(base_delay * (exponential_base ** attempt), max_delay)

        # Add jitter to prevent thundering herd
        if jitter:
            delay = delay * (0.5 + random.random())

        # Don't sleep on last attempt
        if attempt < max_attempts - 1:
            logger.warning(
                f"Attempt {attempt + 1}/{max_attempts} failed for {name}: "
                f"{type(e).__name__}: {str(e)}, retrying in {delay:.2f}s"
            )
            return float(delay)
        logger.error(f"All {max_attempts} attempts failed for {name}: {e}")
        return None

    def _exhausted(last_exception: Optional[BaseException]) -> MaxRetriesExceeded:
        return MaxRetriesExceeded(
            f"Failed after {max_attempts} attempts: {last_exception}",
            attempts=max_attempts,
            original_exception=last_exception,
        )

    def decorator(func: F) -> F:
        name = getattr(func, "__name__", repr(func))

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: Optional[BaseException] = None
            for attempt in range(max_attempts):
                _check_breaker(name)
                try:
                    result = await func(*args, **kwargs)
                except retry_on as e:
                    last_exception = e
                    delay = _on_failure(name, attempt, e)
                    if delay is not None:
                        await asyncio.sleep(delay)
                    continue
                _on_success(name, attempt)
                return result
            raise _exhausted(last_exception)

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            """Same backoff/circuit-breaker policy as async_wrapper, blocking with time.sleep (#212)."""
            last_exception: Optional[BaseException] = None
            for attempt in range(max_attempts):
                _check_breaker(name)
                try:
                    result = func(*args, **kwargs)
                except retry_on as e:
                    last_exception = e
                    delay = _on_failure(name, attempt, e)
                    if delay is not None:
                        time.sleep(delay)
                    continue
                _on_success(name, attempt)
                return result
            raise _exhausted(last_exception)

        if asyncio.iscoroutinefunction(func):
            return cast(F, async_wrapper)
        return cast(F, sync_wrapper)

    return decorator


# Global circuit breakers for common services
HTTP_CIRCUIT_BREAKER = CircuitBreaker(
    failure_threshold=10,
    recovery_timeout=60,
    name="http"
)

DELTA_CIRCUIT_BREAKER = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=30,
    name="delta_lake"
)

REDIS_CIRCUIT_BREAKER = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=30,
    name="redis"
)


def get_circuit_breaker(service: str) -> CircuitBreaker:
    """Get circuit breaker for a service."""
    breakers = {
        "http": HTTP_CIRCUIT_BREAKER,
        "delta": DELTA_CIRCUIT_BREAKER,
        "redis": REDIS_CIRCUIT_BREAKER,
    }
    return breakers.get(service, CircuitBreaker(name=service))
