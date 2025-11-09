"""
Custom exceptions for the UConn scraping pipeline with enhanced error handling.

Phase 7: Enhanced exception hierarchy with:
- Error categorization
- Severity levels
- Retryability flags
- Context information
"""

from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any


class ErrorSeverity(Enum):
    """Error severity levels for prioritization and alerting."""
    LOW = "low"           # Recoverable, retry possible
    MEDIUM = "medium"     # Degraded operation, manual review may be needed
    HIGH = "high"         # Service affected, immediate attention required
    CRITICAL = "critical" # System down, page ops team


class ErrorCategory(Enum):
    """Error categories for metrics and routing."""
    NETWORK = "network"              # HTTP, DNS, connection errors
    VALIDATION = "validation"        # Data validation failures
    RATE_LIMIT = "rate_limit"       # Rate limiting, throttling
    RESOURCE = "resource"            # Memory, disk, CPU exhaustion
    EXTERNAL_SERVICE = "external"    # Third-party service failures
    DATA_CORRUPTION = "data"         # Data integrity issues
    CONFIGURATION = "configuration"  # Config errors
    UNKNOWN = "unknown"              # Unclassified errors


class PipelineException(Exception):
    """
    Base exception with enhanced context for production debugging.

    Attributes:
        message: Human-readable error message
        category: Error category for classification
        severity: Error severity level
        retryable: Whether operation can be retried
        context: Additional context information
        original_exception: Original exception if wrapped
        timestamp: When error occurred
    """

    def __init__(
        self,
        message: str,
        *,
        category: ErrorCategory = ErrorCategory.UNKNOWN,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        retryable: bool = False,
        context: Optional[Dict[str, Any]] = None,
        original_exception: Optional[Exception] = None
    ):
        super().__init__(message)
        self.message = message
        self.category = category
        self.severity = severity
        self.retryable = retryable
        self.context = context or {}
        self.original_exception = original_exception
        self.timestamp = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to structured format for logging and metrics."""
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "category": self.category.value,
            "severity": self.severity.value,
            "retryable": self.retryable,
            "context": self.context,
            "timestamp": self.timestamp.isoformat(),
            "original_error": str(self.original_exception) if self.original_exception else None
        }


# Network-related errors (retryable)
class NetworkError(PipelineException):
    """Network-related errors that can be retried."""
    def __init__(self, message: str, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.NETWORK,
            severity=ErrorSeverity.LOW,
            retryable=True,
            **kwargs
        )


class HTTPError(NetworkError):
    """HTTP request failed."""
    def __init__(self, message: str, status_code: Optional[int] = None, **kwargs):
        context = kwargs.pop("context", {})
        context["http_status"] = status_code
        super().__init__(message, context=context, **kwargs)


class TimeoutError(NetworkError):
    """Request timed out."""
    pass


class ConnectionError(NetworkError):
    """Connection failed."""
    pass


# Rate limiting errors (retryable with backoff)
class RateLimitError(PipelineException):
    """Rate limiting error that requires backoff."""
    def __init__(self, message: str, retry_after: Optional[int] = None, **kwargs):
        context = kwargs.pop("context", {})
        context["retry_after"] = retry_after
        super().__init__(
            message,
            category=ErrorCategory.RATE_LIMIT,
            severity=ErrorSeverity.MEDIUM,
            retryable=True,
            context=context,
            **kwargs
        )


# Validation errors (not retryable)
class DataValidationError(PipelineException):
    """Data validation failed - not retryable."""
    def __init__(self, message: str, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.VALIDATION,
            severity=ErrorSeverity.LOW,
            retryable=False,
            **kwargs
        )


class SchemaValidationError(DataValidationError):
    """Data doesn't match expected schema."""
    pass


# Resource errors
class ResourceExhaustedError(PipelineException):
    """System resources exhausted."""
    def __init__(self, message: str, resource_type: str = "unknown", **kwargs):
        context = kwargs.pop("context", {})
        context["resource_type"] = resource_type
        super().__init__(
            message,
            category=ErrorCategory.RESOURCE,
            severity=ErrorSeverity.HIGH,
            retryable=False,
            context=context,
            **kwargs
        )


# Configuration errors (not retryable)
class ConfigurationError(PipelineException):
    """Configuration error - requires manual fix."""
    def __init__(self, message: str, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.CONFIGURATION,
            severity=ErrorSeverity.HIGH,
            retryable=False,
            **kwargs
        )


# Service-specific errors
class DeltaTableError(PipelineException):
    """Delta Lake operation failed."""
    def __init__(self, message: str, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.EXTERNAL_SERVICE,
            severity=ErrorSeverity.HIGH,
            retryable=True,
            **kwargs
        )


class RedisConnectionError(PipelineException):
    """Redis connection or operation failed."""
    def __init__(self, message: str, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.EXTERNAL_SERVICE,
            severity=ErrorSeverity.HIGH,
            retryable=True,
            **kwargs
        )


# Processing errors
class StageProcessingError(PipelineException):
    """Stage processing failed."""
    pass


class URLProcessingError(PipelineException):
    """URL processing failed."""
    pass


class SpiderError(PipelineException):
    """Spider execution error."""
    pass


class SummarizationError(PipelineException):
    """Summarization operation failed."""
    pass


class AnalyticsError(PipelineException):
    """Analytics operation failed."""
    pass


# Circuit breaker
class CircuitBreakerOpen(PipelineException):
    """Circuit breaker is open - service unavailable."""
    def __init__(self, message: str, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.EXTERNAL_SERVICE,
            severity=ErrorSeverity.HIGH,
            retryable=False,  # Don't retry when circuit is open
            **kwargs
        )


# Retry exhausted
class MaxRetriesExceeded(PipelineException):
    """Maximum retry attempts exceeded."""
    def __init__(self, message: str, attempts: int = 0, **kwargs):
        context = kwargs.pop("context", {})
        context["attempts"] = attempts
        super().__init__(
            message,
            category=ErrorCategory.UNKNOWN,
            severity=ErrorSeverity.MEDIUM,
            retryable=False,
            context=context,
            **kwargs
        )


class PoolExhausted(ResourceExhaustedError):
    """Connection pool exhausted."""
    def __init__(self, message: str, **kwargs):
        super().__init__(message, resource_type="connection_pool", **kwargs)


class ConnectionPoolTimeout(PipelineException):
    """Timeout waiting for connection from pool."""
    def __init__(self, message: str, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.RESOURCE,
            severity=ErrorSeverity.MEDIUM,
            retryable=True,
            **kwargs
        )


__all__ = [
    # Enums
    "ErrorSeverity",
    "ErrorCategory",

    # Base
    "PipelineException",

    # Network
    "NetworkError",
    "HTTPError",
    "TimeoutError",
    "ConnectionError",

    # Rate limiting
    "RateLimitError",

    # Validation
    "DataValidationError",
    "SchemaValidationError",

    # Resources
    "ResourceExhaustedError",
    "PoolExhausted",
    "ConnectionPoolTimeout",

    # Configuration
    "ConfigurationError",

    # Services
    "DeltaTableError",
    "RedisConnectionError",

    # Processing
    "StageProcessingError",
    "URLProcessingError",
    "SpiderError",
    "SummarizationError",
    "AnalyticsError",

    # Circuit breaker
    "CircuitBreakerOpen",
    "MaxRetriesExceeded",
]
