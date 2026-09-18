"""
Core modules for the UConn scraping pipeline.

This package provides:
- Configuration management (config.py)
- Global constants (constants.py)
- Custom exceptions (exceptions.py)
"""

from .config import get_config, Config
from .constants import *
from .exceptions import *

__all__ = [
    "get_config",
    "Config",
]
