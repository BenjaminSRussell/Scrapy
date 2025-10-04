"""Simple error handling utilities for the pipeline."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from functools import wraps

logger = logging.getLogger(__name__)


class ErrorSeverity(Enum):
    """Error severity levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ErrorRecord:
    """Record of an error occurrence."""
    timestamp: float
    error_type: str
    message: str
    severity: ErrorSeverity
    context: dict[str, any] | None = None


class RetryConfig:
    """Configuration for retry logic."""

    def __init__(self, max_attempts: int = 3, base_delay: float = 1.0, max_delay: float = 60.0):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay


def with_retry(config: RetryConfig = None):
    """Decorator to add retry logic to functions."""
    if config is None:
        config = RetryConfig()

    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                last_exception = None

                for attempt in range(config.max_attempts):
                    try:
                        return await func(*args, **kwargs)
                    except Exception as e:
                        last_exception = e

                        if attempt < config.max_attempts - 1:
                            
                            # Calculate exponential backoff delay
                            delay = min(config.base_delay * (2 ** attempt), config.max_delay)
                            logger.warning(f"Attempt {attempt + 1}/{config.max_attempts} failed for {func.__name__}: {e}. Retrying in {delay:.1f}s")
                            await asyncio.sleep(delay)
                        else:
                            logger.error(f"All {config.max_attempts} attempts failed for {func.__name__}")

                raise last_exception

            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                last_exception = None

                for attempt in range(config.max_attempts):
                    try:
                        return func(*args, **kwargs)
                    except Exception as e:
                        last_exception = e

                        if attempt < config.max_attempts - 1:
                            delay = min(config.base_delay * (2 ** attempt), config.max_delay)
                            logger.warning(f"Attempt {attempt + 1}/{config.max_attempts} failed for {func.__name__}: {e}. Retrying in {delay:.1f}s")
                            time.sleep(delay)
                        else:
                            logger.error(f"All {config.max_attempts} attempts failed for {func.__name__}")

                raise last_exception

            return sync_wrapper

    return decorator


class ErrorTracker:
    """Track and categorize errors during pipeline execution."""

    def __init__(self):
        self.errors: list[ErrorRecord] = []
        self.error_counts = {}

    def record_error(self, error: Exception, severity: ErrorSeverity = ErrorSeverity.MEDIUM, context: dict = None):
        """Record an error occurrence."""
        error_type = type(error).__name__

        record = ErrorRecord(
            timestamp=time.time(),
            error_type=error_type,
            message=str(error),
            severity=severity,
            context=context
        )

        self.errors.append(record)
        self.error_counts[error_type] = self.error_counts.get(error_type, 0) + 1

        # Log based on severity
        if severity == ErrorSeverity.CRITICAL:
            logger.critical(f"{error_type}: {error}")
        elif severity == ErrorSeverity.HIGH:
            logger.error(f"{error_type}: {error}")
        elif severity == ErrorSeverity.MEDIUM:
            logger.warning(f"{error_type}: {error}")
        else:
            logger.debug(f"{error_type}: {error}")

    def get_error_summary(self) -> dict[str, any]:
        """Get summary of recorded errors."""
        total_errors = len(self.errors)
        if total_errors == 0:
            return {"total_errors": 0, "error_types": {}}

        severity_counts = {}
        for error in self.errors:
            severity = error.severity.value
            severity_counts[severity] = severity_counts.get(severity, 0) + 1

        return {
            "total_errors": total_errors,
            "error_types": dict(sorted(self.error_counts.items(), key=lambda x: x[1], reverse=True)),
            "severity_breakdown": severity_counts,
            "most_recent_errors": [
                {
                    "type": e.error_type,
                    "message": e.message,
                    "severity": e.severity.value,
                    "timestamp": e.timestamp
                }
                for e in sorted(self.errors, key=lambda x: x.timestamp, reverse=True)[:10]
            ]
        }

    def clear_errors(self):
        """Clear all recorded errors."""
        self.errors.clear()
        self.error_counts.clear()


def safe_execute(func: Callable, error_tracker: ErrorTracker = None, severity: ErrorSeverity = ErrorSeverity.MEDIUM, context: dict = None) -> tuple[any, bool]:
    """Safely execute a function and track any errors."""
    try:
        result = func()
        return result, True
    except Exception as e:
        if error_tracker:
            error_tracker.record_error(e, severity, context)
        else:
            logger.warning(f"Error in {func.__name__ if hasattr(func, '__name__') else 'function'}: {e}")
        return None, False


async def safe_execute_async(coro, error_tracker: ErrorTracker = None, severity: ErrorSeverity = ErrorSeverity.MEDIUM, context: dict = None) -> tuple[any, bool]:
    """Safely execute an async function and track any errors."""
    try:
        result = await coro
        return result, True
    except Exception as e:
        if error_tracker:
            error_tracker.record_error(e, severity, context)
        else:
            logger.warning(f"Error in async function: {e}")
        return None, False


class CircuitBreaker:
    """Simple circuit breaker implementation."""

    
    def __init__(self, failure_threshold: int = 5, timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = "closed"  # closed, open, half-open

    def call(self, func: Callable, *args, **kwargs):
        """Call function through circuit breaker."""
        if self.state == "open":
            if time.time() - self.last_failure_time > self.timeout:
                self.state = "half-open"
                logger.info("Circuit breaker moving to half-open state")
            else:
                raise Exception("Circuit breaker is open - not executing function")

        try:
            result = func(*args, **kwargs)

            if self.state == "half-open":
                self.state = "closed"
                self.failure_count = 0
                logger.info("Circuit breaker reset to closed state")

            return result

        except Exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()

            if self.failure_count >= self.failure_threshold:
                self.state = "open"
                logger.warning(f"Circuit breaker opened after {self.failure_count} failures")

            raise e


# Global error tracker instance
_error_tracker = ErrorTracker()


def get_global_error_tracker() -> ErrorTracker:
    """Get the global error tracker."""
    return _error_tracker