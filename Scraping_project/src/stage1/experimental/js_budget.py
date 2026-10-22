"""Operator budget for the Playwright JS spider (#460).

A preset picks the spider's concurrency defaults. An operator cap on concurrent
Playwright pages (``stage1.js_max_concurrent_pages`` or
``PLAYWRIGHT_MAX_CONCURRENT_PAGES``) is split into contexts and pages per
context, so the product never exceeds the cap. The memory estimate is pages
times a per-page figure; that figure is a configured estimate, not a
measurement. See docs/JS_RENDERING.md ("Presets and memory budget").
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PRESETS: dict[str, dict[str, int]] = {
    "default": {
        "concurrent_requests": 20,
        "concurrent_requests_per_domain": 10,
        "max_contexts": 4,
        "max_pages_per_context": 8,
    },
    "low_memory": {
        "concurrent_requests": 4,
        "concurrent_requests_per_domain": 2,
        "max_contexts": 2,
        "max_pages_per_context": 2,
    },
}
DEFAULT_PRESET = "default"
DEFAULT_MB_PER_PAGE = 100
DEFAULT_MEMORY_WARNING_MB = 4096


@dataclass(frozen=True)
class JSBudget:
    preset: str
    concurrent_requests: int
    concurrent_requests_per_domain: int
    max_contexts: int
    max_pages_per_context: int

    @property
    def max_pages(self) -> int:
        return self.max_contexts * self.max_pages_per_context


def _cap(raw: Any, key: str) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{key} must be a positive integer, got {raw!r}") from e
    if value < 1:
        raise ValueError(f"{key} must be a positive integer, got {raw!r}")
    return value


def resolve_js_budget(config: Any = None, env: Mapping[str, str] | None = None) -> JSBudget:
    """Resolve the spider budget. Env beats config.yml beats the preset default."""
    env = os.environ if env is None else env
    raw_preset = config.get("stage1.js_spider_preset") if config is not None else None
    preset = str(raw_preset or DEFAULT_PRESET).strip().lower()
    if preset not in PRESETS:
        raise ValueError(f"stage1.js_spider_preset must be one of {sorted(PRESETS)}, got {preset!r}")
    values = dict(PRESETS[preset])

    total = _cap(env.get("PLAYWRIGHT_MAX_CONCURRENT_PAGES"), "PLAYWRIGHT_MAX_CONCURRENT_PAGES")
    if total is None and config is not None:
        total = _cap(config.get("stage1.js_max_concurrent_pages"), "stage1.js_max_concurrent_pages")

    if total is not None:
        contexts = min(values["max_contexts"], total)
        values["max_contexts"] = contexts
        values["max_pages_per_context"] = max(1, total // contexts)
    else:
        # Per-field env overrides, as before this preset existed.
        for env_name, key in (
            ("PLAYWRIGHT_MAX_CONTEXTS", "max_contexts"),
            ("PLAYWRIGHT_MAX_PAGES_PER_CONTEXT", "max_pages_per_context"),
        ):
            override = _cap(env.get(env_name), env_name)
            if override is not None:
                values[key] = override

    return JSBudget(preset=preset, **values)


def estimate_memory_mb(max_pages: int, mb_per_page: float = DEFAULT_MB_PER_PAGE) -> float:
    return max_pages * mb_per_page


def memory_warning(max_pages: int, mb_per_page: float, warn_mb: float) -> str | None:
    estimate = estimate_memory_mb(max_pages, mb_per_page)
    if estimate <= warn_mb:
        return None
    return f"Estimated {estimate:,.0f} MB for {max_pages} pages exceeds the {warn_mb:,.0f} MB warning."
