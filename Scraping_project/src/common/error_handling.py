# src/common/error_handling.py

class SpiderError(Exception):
    """Base class for spider exceptions."""
    pass

class LinkExtractionError(SpiderError):
    """Raised when link extraction fails."""
    pass

class NormalizationError(SpiderError):
    """Raised when URL normalization fails."""
    pass

class DynamicDiscoveryError(SpiderError):
    """Raised when dynamic discovery fails."""
    pass

class HeadlessBrowserError(SpiderError):
    """Raised when the headless browser fails."""
    pass
