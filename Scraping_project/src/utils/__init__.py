"""
Global utility modules for the UConn scraping pipeline.

This package provides centralized utilities for:
- Delta Lake operations (delta.py)
- Redis operations (redis.py)
- Validation functions (validation.py)
"""

from .delta import get_delta, DeltaHelper
from .redis import get_redis, RedisHelper
from .validation import is_valid_url, is_uconn_domain, sanitize_text

__all__ = [
    "get_delta",
    "DeltaHelper",
    "get_redis",
    "RedisHelper",
    "is_valid_url",
    "is_uconn_domain",
    "sanitize_text",
]
