"""Run-time gate for lab spiders (#391 / #442).

Supported production spider: ``scout`` (src/stage1/scout_spider.py).
Experimental: ``javascript``, ``deep_dive``, ``depth``. They import fine (CI
smoke-imports them) but refuse to *crawl* unless explicitly enabled with
``ENABLE_EXPERIMENTAL_SPIDERS=1`` (or the Scrapy setting of the same name, or
``cli.py deep_dive --experimental``). See src/stage1/experimental/README.md.
"""

from __future__ import annotations

import logging
from typing import Any

from src.utils.feature_flags import get_bool, parse_bool

logger = logging.getLogger(__name__)

FLAG = "ENABLE_EXPERIMENTAL_SPIDERS"
SUPPORTED_SPIDERS = ("scout",)
EXPERIMENTAL_SPIDERS = ("javascript", "deep_dive", "depth")

PREREQUISITES = {
    "javascript": "Playwright + Chromium (`playwright install chromium`), up to 12 GB RAM (MEMUSAGE_LIMIT_MB=12288)",
    "deep_dive": "Redis; ~4 GB RAM (spider_config memory_limit_mb)",
    "depth": "Redis; ~4 GB RAM (spider_config memory_limit_mb)",
}


class ExperimentalSpiderDisabled(RuntimeError):
    """Raised when a lab spider is started without opting in."""


def experimental_enabled(settings: Any = None, env: Any = None) -> bool:
    if settings is not None:
        try:
            value = settings.get(FLAG)
        except Exception:
            value = None
        if value is not None and parse_bool(value, False):
            return True
    return get_bool(FLAG, False, env=env)


def warning_text(name: str) -> str:
    prereq = PREREQUISITES.get(name, "see src/stage1/experimental/README.md")
    return (
        f"Spider {name!r} is EXPERIMENTAL (not supported in production; use 'scout'). "
        f"Prerequisites: {prereq}."
    )


def require_experimental(name: str, settings: Any = None, env: Any = None) -> None:
    if not experimental_enabled(settings, env):
        raise ExperimentalSpiderDisabled(
            warning_text(name) + f" Set {FLAG}=1 (or run `python cli.py deep_dive --experimental`) to opt in."
        )
    logger.warning(warning_text(name))


class ExperimentalSpiderMixin:
    """Put first in the bases of a lab spider: gates ``from_crawler``, not ``__init__``,
    so unit tests can still construct the class directly."""

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        require_experimental(getattr(cls, "name", cls.__name__), getattr(crawler, "settings", None))
        return super().from_crawler(crawler, *args, **kwargs)  # type: ignore[misc]
