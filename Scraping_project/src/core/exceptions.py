"""
Custom exceptions for the UConn scraping pipeline.

Provides specific exception types for better error handling throughout the pipeline.
"""


class PipelineException(Exception):
    """Base exception for all pipeline errors."""
    pass


class DeltaTableError(PipelineException):
    """Delta Lake operation failed."""
    pass


class RedisConnectionError(PipelineException):
    """Redis connection or operation failed."""
    pass


class ValidationError(PipelineException):
    """Input validation failed."""
    pass


class StageProcessingError(PipelineException):
    """Stage processing failed."""
    pass


class ConfigurationError(PipelineException):
    """Configuration error."""
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


__all__ = [
    "PipelineException",
    "DeltaTableError",
    "RedisConnectionError",
    "ValidationError",
    "StageProcessingError",
    "ConfigurationError",
    "URLProcessingError",
    "SpiderError",
    "SummarizationError",
    "AnalyticsError",
]
