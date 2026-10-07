"""
Core modules for the UConn scraping pipeline.

This package provides:
- Configuration management (config.py)
- Global constants (constants.py)
- Custom exceptions (exceptions.py)
"""

from .config import get_config, Config
from .constants import *  # noqa: F403 - intentional re-export
from .exceptions import *  # noqa: F403 - intentional re-export

__all__ = [
    "get_config",
    "Config",
]
