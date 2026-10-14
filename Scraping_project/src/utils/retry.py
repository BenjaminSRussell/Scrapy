"""
Retry logic with exponential backoff and circuit breaker pattern.

Phase 7: Resilience utilities for handling transient failures.
"""

import asyncio
import logging
import os
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

# Default on. Tests set CIRCUIT_BREAKER_SHARED=0 so cases stay process-local.
_SHARING_OFF = frozenset({"0", "false", "no", "off"})
# A down Redis must not add a connect timeout to every request.
_STORE_COOLDOWN_SECONDS = 5.0


def circuit_breaker_sharing_enabled() -> bool:
    return os.getenv("CIRCUIT_BREAKER_SHARED", "1").strip().lower() not in _SHARING_OFF


class RedisCircuitStore:
    """Open/half-open state and the shared failure count in Redis (#171).

    Successful reads are not cached, so a peer's trip shows up on the next
    ``can_execute``. Connection failures stick for a few seconds and the
    caller keeps using its own memory.
    """

    def __init__(self, helper: Any = None) -> None:
        self._helper = helper
        self._down_until = 0.0

    def _redis(self) -> Any:
        if self._helper is None:
            from src.utils.redis import get_redis

            self._helper = get_redis()
        _ = self._helper.client  # connect now so outages hit the cooldown
        return self._helper

    def _guard(self, op: Callable[[Any], Any]) -> Any:
        if time.time() < self._down_until:
            return None
        try:
            return op(self._redis())
        except Exception as exc:
            self._down_until = time.time() + _STORE_COOLDOWN_SECONDS
            logger.warning("Shared circuit breaker store unavailable: %s", exc)
            return None

    def incr_failures(self, name: str, host: str, ttl_seconds: int) -> Optional[int]:
        count = self._guard(lambda helper: helper.incr_shared_circuit_failures(name, host, ttl_seconds))
        return count if isinstance(count, int) else None

    def load(self, name: str, host: str) -> tuple[bool, Optional[dict[str, Any]]]:
        """(ok, state). ``ok`` is False when Redis could not be read."""
        if time.time() < self._down_until:
            return False, None
        try:
            data = self._redis().read_shared_circuit(name, host)
        except Exception as exc:
            self._down_until = time.time() + _STORE_COOLDOWN_SECONDS
            logger.warning("Shared circuit breaker store unavailable: %s", exc)
            return False, None
        if isinstance(data, dict):
            return True, cast(dict[str, Any], data)
        return True, None

    def write(
        self,
        name: str,
        host: str,
        state: str,
        failure_count: int,
        opened_at: Optional[float],
        ttl_seconds: int,
    ) -> None:
        self._guard(
            lambda helper: helper.write_shared_circuit(
                name, host, state, failure_count, opened_at, ttl_seconds
            )
        )

    def clear(self, name: str, host: str) -> None:
        self._guard(lambda helper: helper.clear_shared_circuit(name, host))

    def reset_failures(self, name: str, host: str) -> None:
        self._guard(lambda helper: helper.reset_shared_circuit_failures(name, host))


_shared_store: Optional[RedisCircuitStore] = None


def get_shared_circuit_store() -> Optional[RedisCircuitStore]:
    global _shared_store
    if not circuit_breaker_sharing_enabled():
        return None
    if _shared_store is None:
        _shared_store = RedisCircuitStore()
    return _shared_store


class CircuitBreaker:
    """
    Circuit breaker pattern implementation.

    Prevents cascade failures by temporarily blocking calls to failing services.

    States:
    - CLOSED: Normal operation, all calls go through
    - OPEN: Too many failures, block all calls
    - HALF_OPEN: Testing if service recovered, allow limited calls

    When sharing is enabled (the default), open and half-open state for
    ``(name, host)`` is stored in Redis with a TTL of ``recovery_timeout``.
    Another worker sees the trip on its next check, and a new process after
    a restart reads the same key. ``CIRCUIT_BREAKER_SHARED=0`` keeps the
    breaker in this process only.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: Type[Exception] = Exception,
        name: str = "default",
        host: str = "",
        store: Any = None,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.name = name
        self.host = host or ""

        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.state = "closed"  # closed, open, half-open
        # An explicit store (including tests) wins. None means "use the default".
        self._store: Any = store if store is not None else get_shared_circuit_store()

    def _incr_shared(self) -> Optional[int]:
        store = self._store
        if store is None:
            return None
        counted = store.incr_failures(self.name, self.host, int(self.recovery_timeout))
        return counted if isinstance(counted, int) else None

    def _publish(self) -> None:
        store = self._store
        if store is None or self.state == "closed":
            return
        opened_at = self.last_failure_time.timestamp() if self.last_failure_time else None
        store.write(self.name, self.host, self.state, self.failure_count, opened_at, int(self.recovery_timeout))

    def _clear_shared(self) -> None:
        store = self._store
        if store is not None:
            store.clear(self.name, self.host)

    def _reset_shared_failures(self) -> None:
        store = self._store
        if store is not None:
            store.reset_failures(self.name, self.host)

    def _adopt_remote(self) -> None:
        """Copy a peer's open or half-open trip. Missing keys leave local state alone."""
        store = self._store
        if store is None:
            return
        ok, remote = store.load(self.name, self.host)
        if not ok or not remote:
            return
        remote_state = str(remote.get("state") or "")
        # Already-open local state keeps its clock (recovery / half-open). A
        # closed breaker adopts the peer's opened_at even if it has an older
        # timestamp left over from a previous, local failure.
        if remote_state == "open" and (self.state != "open" or self.last_failure_time is None):
            adopt_clock = self.state != "open" or self.last_failure_time is None
            self.state = "open"
            try:
                self.failure_count = int(remote.get("failure_count") or self.failure_count)
            except (TypeError, ValueError):
                pass
            if adopt_clock:
                opened = remote.get("opened_at")
                self.last_failure_time = datetime.now() if opened is None else datetime.fromtimestamp(float(opened))
        elif remote_state == "half-open" and self.state == "closed":
            self.state = "half-open"
            self.failure_count = 0

    def can_execute(self) -> bool:
        """Check if circuit allows execution."""
        self._adopt_remote()
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
                    self._publish()
                    return True
            return False

        # half-open state - allow one attempt
        return True

    def record_success(self):
        """Record successful execution."""
        previous = self.state
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
        if self._store is None:
            return
        # Any replica's success resets the shared consecutive-failure count.
        # Deleting the state key only when this replica leaves open/half-open
        # keeps a peer's trip if a request was already in flight.
        if self.state == "closed" and previous != "closed":
            self._clear_shared()
        elif self.state == "closed":
            self._reset_shared_failures()
        else:
            self._publish()

    def record_failure(self):
        """Record failed execution."""
        self.failure_count += 1
        self.last_failure_time = datetime.now()
        shared = self._incr_shared()
        if shared is not None:
            self.failure_count = shared

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
        if self.state != "closed":
            self._publish()

    def get_state(self) -> dict:
        """Get current circuit breaker state for monitoring."""
        return {
            "name": self.name,
            "host": self.host,
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
