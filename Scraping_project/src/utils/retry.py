"""
Retry logic with exponential backoff and circuit breaker pattern.

Phase 7: Resilience utilities for handling transient failures.
"""

import asyncio
import logging
import random
from datetime import datetime, timedelta
from functools import wraps
from typing import TypeVar, Callable, Optional, Type, Tuple

from src.core.exceptions import (
    PipelineException,
    CircuitBreakerOpen,
    MaxRetriesExceeded,
    NetworkError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

T = TypeVar('T')


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

    Example:
        @with_retry(max_attempts=3, retry_on=(NetworkError, TimeoutError))
        async def fetch_url(url: str) -> str:
            return await http_client.get(url)
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> T:
            last_exception = None

            for attempt in range(max_attempts):
                # Check circuit breaker
                if circuit_breaker and not circuit_breaker.can_execute():
                    raise CircuitBreakerOpen(
                        f"Circuit breaker is {circuit_breaker.state} for {func.__name__}"
                    )

                try:
                    result = await func(*args, **kwargs)

                    # Success - record if using circuit breaker
                    if circuit_breaker:
                        circuit_breaker.record_success()

                    if attempt > 0:
                        logger.info(f"Successfully executed {func.__name__} after {attempt + 1} attempts")

                    return result

                except retry_on as e:
                    last_exception = e

                    # Record failure in circuit breaker
                    if circuit_breaker:
                        circuit_breaker.record_failure()

                    # Don't retry if error is marked as non-retryable
                    if isinstance(e, PipelineException) and not e.retryable:
                        logger.info(
                            f"Non-retryable error in {func.__name__}: {e.category.value}"
                        )
                        raise

                    # Check for rate limit with retry-after header
                    if isinstance(e, RateLimitError):
                        retry_after = e.context.get("retry_after", base_delay)
                        delay = min(retry_after, max_delay)
                    else:
                        # Calculate delay with exponential backoff
                        delay = min(
                            base_delay * (exponential_base ** attempt),
                            max_delay
                        )

                    # Add jitter to prevent thundering herd
                    if jitter:
                        delay = delay * (0.5 + random.random())

                    # Don't sleep on last attempt
                    if attempt < max_attempts - 1:
                        logger.warning(
                            f"Attempt {attempt + 1}/{max_attempts} failed for {func.__name__}: "
                            f"{type(e).__name__}: {str(e)}, retrying in {delay:.2f}s"
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            f"All {max_attempts} attempts failed for {func.__name__}: {e}"
                        )

            # All attempts exhausted
            raise MaxRetriesExceeded(
                f"Failed after {max_attempts} attempts: {last_exception}",
                attempts=max_attempts,
                original_exception=last_exception
            )

        @wraps(func)
        def sync_wrapper(*args, **kwargs) -> T:
            """Synchronous wrapper (runs async in new event loop)."""
            # For sync functions, we'd need different logic
            # For now, we'll require async functions
            raise NotImplementedError("Sync retry not implemented - use async functions")

        # Return async wrapper if function is async
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

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
