"""Canonical BaseSpider import path for Stage 1.

Re-exports the experimental implementation so
``from src.stage1.base_spider import BaseSpider`` works for scout,
experimental spiders, and unit tests (issues #376 / #610).
"""

from src.stage1.experimental.base_spider import BaseSpider

__all__ = ["BaseSpider"]
