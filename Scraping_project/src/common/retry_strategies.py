"""
Advanced retry strategies with jittered exponential backoff and error classification.
Handles transient vs permanent errors intelligently.
"""

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ErrorType(Enum):
    """Classification of errors for retry decisions"""
    TRANSIENT = "transient"  # Network hiccups, timeouts - retry aggressively
    RATE_LIMIT = "rate_limit"  # 429, 503 - backoff and retry
    PERMANENT = "permanent"  # 404, 410 - don't retry
    DNS_ERROR = "dns_error"  # DNS failures - don't retry this run
    AUTH_ERROR = "auth_error"  # 401, 403 - don't retry
    SERVER_ERROR = "server_error"  # 500-599 - retry with caution
    UNKNOWN = "unknown"  # Unclassified - retry conservatively


@dataclass
class RetryConfig:
    """Configuration for retry behavior"""
    max_attempts: int = 3
    base_delay: float = 1.0  # Base delay in seconds
    max_delay: float = 60.0  # Maximum delay in seconds
    exponential_base: float = 2.0  # Multiplier for exponential backoff
    jitter_factor: float = 0.1  # Jitter as fraction of delay (±10%)

    # Error-specific configurations
    transient_max_attempts: int = 5  # More attempts for transient errors
    permanent_max_attempts: int = 0  # No retries for permanent errors
    rate_limit_max_attempts: int = 3  # Moderate retries for rate limits
    rate_limit_base_delay: float = 5.0  # Longer initial delay for rate limits


def classify_error(
    status_code: int | None = None,
    error_message: str | None = None,
    exception: Exception | None = None
) -> ErrorType:
    """
    Classify error type for intelligent retry decisions

    Args:
        status_code: HTTP status code
        error_message: Error message string
        exception: Exception object

    Returns:
        ErrorType enum value
    """
    # DNS/Connection errors (transient network issues)
    if exception:
        exception_str = str(exception).lower()
        if 'dns' in exception_str or 'name resolution' in exception_str:
            return ErrorType.DNS_ERROR
        if 'timeout' in exception_str or 'timed out' in exception_str:
            return ErrorType.TRANSIENT
        if 'connection' in exception_str:
            return ErrorType.TRANSIENT
        if 'ssl' in exception_str or 'certificate' in exception_str:
            return ErrorType.AUTH_ERROR

    # HTTP status code classification
    if status_code:
        if status_code == 429:  # Too Many Requests
            return ErrorType.RATE_LIMIT
        elif status_code in (401, 403, 407):  # Auth errors
            return ErrorType.AUTH_ERROR
        elif status_code in (404, 410):  # Not Found, Gone
            return ErrorType.PERMANENT
        elif status_code in (503, 504):  # Service Unavailable, Gateway Timeout
            return ErrorType.RATE_LIMIT  # Treat as rate limit
        elif 500 <= status_code < 600:  # Server errors
            return ErrorType.SERVER_ERROR
        elif 400 <= status_code < 500:  # Client errors (other)
            return ErrorType.PERMANENT

    # Error message classification
    if error_message:
        msg_lower = error_message.lower()
        if 'timeout' in msg_lower:
            return ErrorType.TRANSIENT
        if 'dns' in msg_lower or 'resolve' in msg_lower:
            return ErrorType.DNS_ERROR
        if 'rate limit' in msg_lower or '429' in msg_lower:
            return ErrorType.RATE_LIMIT
        if '404' in msg_lower or 'not found' in msg_lower:
            return ErrorType.PERMANENT

    return ErrorType.UNKNOWN


def calculate_backoff_delay(
    attempt: int,
    config: RetryConfig,
    error_type: ErrorType
) -> float:
    """
    Calculate jittered exponential backoff delay

    Args:
        attempt: Current attempt number (0-indexed)
        config: Retry configuration
        error_type: Type of error encountered

    Returns:
        Delay in seconds with jitter
    """
    # Select base delay based on error type
    if error_type == ErrorType.RATE_LIMIT:
        base = config.rate_limit_base_delay
    else:
        base = config.base_delay

    # Exponential backoff: delay = base * (exponential_base ^ attempt)
    delay = base * (config.exponential_base ** attempt)

    # Cap at max_delay
    delay = min(delay, config.max_delay)

    # Add jitter: ±jitter_factor of delay
    jitter = delay * config.jitter_factor
    jittered_delay = delay + random.uniform(-jitter, jitter)

    # Ensure non-negative
    return max(0.0, jittered_delay)


def should_retry(
    attempt: int,
    error_type: ErrorType,
    config: RetryConfig
) -> tuple[bool, str]:
    """
    Determine if request should be retried

    Args:
        attempt: Current attempt number (0-indexed)
        error_type: Type of error encountered
        config: Retry configuration

    Returns:
        Tuple of (should_retry, reason)
    """
    # Determine max attempts based on error type
    if error_type == ErrorType.PERMANENT:
        max_attempts = config.permanent_max_attempts
        reason = "Permanent error - no retry"
    elif error_type == ErrorType.AUTH_ERROR:
        max_attempts = 0
        reason = "Authentication error - no retry"
    elif error_type == ErrorType.DNS_ERROR:
        max_attempts = 0
        reason = "DNS error - no retry this run"
    elif error_type == ErrorType.TRANSIENT:
        max_attempts = config.transient_max_attempts
        reason = f"Transient error - retry (attempt {attempt + 1}/{max_attempts})"
    elif error_type == ErrorType.RATE_LIMIT:
        max_attempts = config.rate_limit_max_attempts
        reason = f"Rate limit - retry with backoff (attempt {attempt + 1}/{max_attempts})"
    else:
        max_attempts = config.max_attempts
        reason = f"Unknown error - conservative retry (attempt {attempt + 1}/{max_attempts})"

    should_continue = attempt < max_attempts

    if not should_continue:
        reason = f"Max attempts reached ({attempt + 1}/{max_attempts})"

    return should_continue, reason


async def retry_with_backoff(
    func,
    *args,
    config: RetryConfig | None = None,
    **kwargs
) -> Any:
    """
    Retry function with intelligent backoff

    Args:
        func: Async function to retry
        *args: Positional arguments for func
        config: Retry configuration
        **kwargs: Keyword arguments for func

    Returns:
        Result from func

    Raises:
        Last exception if all retries exhausted
    """
    if config is None:
        config = RetryConfig()

    last_exception = None

    for attempt in range(config.max_attempts + 1):
        try:
            result = await func(*args, **kwargs)

            if attempt > 0:
                logger.info(f"Request succeeded on attempt {attempt + 1}")

            return result

        except Exception as e:
            last_exception = e
            error_type = classify_error(exception=e)

            should_continue, reason = should_retry(attempt, error_type, config)

            if not should_continue:
                logger.warning(f"Not retrying: {reason}")
                break

            delay = calculate_backoff_delay(attempt, config, error_type)

            logger.info(
                f"Attempt {attempt + 1} failed ({error_type.value}). "
                f"Retrying in {delay:.2f}s... ({reason})"
            )

            await asyncio.sleep(delay)

    # All retries exhausted
    logger.error(f"All retry attempts exhausted. Last error: {last_exception}")
    raise last_exception


@dataclass
class CircuitBreakerState:
    """State for circuit breaker pattern"""
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: datetime | None = None
    state: str = "closed"  # closed, open, half_open
    consecutive_failures: int = 0

    # Thresholds
    failure_threshold: int = 5  # Open after N consecutive failures
    success_threshold: int = 2  # Close after N consecutive successes in half_open
    timeout: float = 60.0  # Seconds before trying half_open


class DomainCircuitBreaker:
    """
    Circuit breaker for domain-specific failure handling.
    Prevents wasting workers on consistently failing domains.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout: float = 60.0
    ):
        """
        Initialize circuit breaker

        Args:
            failure_threshold: Consecutive failures before opening circuit
            success_threshold: Consecutive successes to close circuit
            timeout: Seconds before attempting half_open
        """
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout = timeout
        self.domains: dict[str, CircuitBreakerState] = {}
        self._lock = asyncio.Lock()

    def _get_state(self, domain: str) -> CircuitBreakerState:
        """Get or create state for domain"""
        if domain not in self.domains:
            self.domains[domain] = CircuitBreakerState(
                failure_threshold=self.failure_threshold,
                success_threshold=self.success_threshold,
                timeout=self.timeout
            )
        return self.domains[domain]

    async def is_allowed(self, domain: str) -> tuple[bool, str]:
        """
        Check if request to domain is allowed

        Args:
            domain: Domain to check

        Returns:
            Tuple of (is_allowed, reason)
        """
        async with self._lock:
            state = self._get_state(domain)

            if state.state == "closed":
                return True, "Circuit closed - normal operation"

            elif state.state == "open":
                # Check if timeout elapsed
                if state.last_failure_time:
                    elapsed = (datetime.now() - state.last_failure_time).total_seconds()
                    if elapsed >= state.timeout:
                        # Try half_open
                        state.state = "half_open"
                        state.consecutive_failures = 0
                        logger.info(f"Circuit breaker for {domain}: open → half_open (timeout elapsed)")
                        return True, "Circuit half_open - testing"

                return False, f"Circuit open - domain failing (wait {state.timeout - elapsed:.0f}s)"

            elif state.state == "half_open":
                return True, "Circuit half_open - testing recovery"

            return False, "Unknown circuit state"

    async def record_success(self, domain: str):
        """Record successful request"""
        async with self._lock:
            state = self._get_state(domain)
            state.success_count += 1
            state.consecutive_failures = 0

            if state.state == "half_open":
                # Count consecutive successes in half_open
                if state.success_count >= state.success_threshold:
                    state.state = "closed"
                    state.failure_count = 0
                    logger.info(f"Circuit breaker for {domain}: half_open → closed (recovered)")

            elif state.state == "open":
                # Shouldn't happen, but reset if we see success
                state.state = "closed"
                state.failure_count = 0
                logger.warning(f"Circuit breaker for {domain}: open → closed (unexpected success)")

    async def record_failure(self, domain: str, error_type: ErrorType):
        """Record failed request"""
        async with self._lock:
            state = self._get_state(domain)
            state.failure_count += 1
            state.consecutive_failures += 1
            state.last_failure_time = datetime.now()

            # Only open circuit for errors that indicate domain issues
            # Don't open for single permanent errors (404, etc.)
            if error_type in (ErrorType.TRANSIENT, ErrorType.SERVER_ERROR, ErrorType.DNS_ERROR):
                if state.consecutive_failures >= state.failure_threshold:
                    if state.state != "open":
                        state.state = "open"
                        logger.warning(
                            f"Circuit breaker for {domain}: {state.state} → open "
                            f"({state.consecutive_failures} consecutive failures)"
                        )

            # Reset half_open to open if failing
            elif state.state == "half_open":
                state.state = "open"
                logger.info(f"Circuit breaker for {domain}: half_open → open (still failing)")

    async def get_stats(self) -> dict[str, dict[str, Any]]:
        """Get statistics for all domains"""
        async with self._lock:
            return {
                domain: {
                    'state': state.state,
                    'failure_count': state.failure_count,
                    'success_count': state.success_count,
                    'consecutive_failures': state.consecutive_failures,
                    'last_failure': state.last_failure_time.isoformat() if state.last_failure_time else None
                }
                for domain, state in self.domains.items()
            }

    async def reset_domain(self, domain: str):
        """Reset circuit breaker for specific domain"""
        async with self._lock:
            if domain in self.domains:
                self.domains[domain] = CircuitBreakerState(
                    failure_threshold=self.failure_threshold,
                    success_threshold=self.success_threshold,
                    timeout=self.timeout
                )
                logger.info(f"Circuit breaker for {domain} reset")
