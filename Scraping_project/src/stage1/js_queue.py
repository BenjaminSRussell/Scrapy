"""``js_spider_queue`` hop contract helpers (#645).

Scout -> ``js_spider_queue`` -> ``javascript`` spider -> Stage 2, or an explicit
skip when the JS path is switched off (then Scout must not enqueue at all).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

_TRUE = ("1", "true", "yes", "on")
CONFIG_KEYS = ("stage1.enable_js_spider", "stages.stage1.enable_js_spider")
ENV_VAR = "ENABLE_JS_SPIDER"


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in _TRUE
    return bool(value)


def js_spider_enabled(config: Any = None, enabled: bool | None = None) -> bool:
    """Resolve the JS path flag: ``enabled`` -> ``$ENABLE_JS_SPIDER`` -> config -> True.

    ``config`` is anything with a dotted ``.get`` (``src.core.config``); when
    omitted the project config is loaded.
    """
    if enabled is not None:
        return _as_bool(enabled)
    env = os.environ.get(ENV_VAR)
    if env is not None and env.strip():
        return _as_bool(env)
    if config is None:
        try:
            from src.core.config import get_config

            config = get_config()
        except Exception as e:  # config unavailable (tests, CLI tools)
            logger.debug("get_config unavailable for enable_js_spider: %s", e)
            return True
    for key in CONFIG_KEYS:
        try:
            value = config.get(key)
        except Exception:
            value = None
        if value is not None:
            return _as_bool(value)
    return True


def count_pending(rows: Iterable[dict[str, Any]] | None) -> int:
    """Rows still waiting for the JS spider (missing status counts as pending)."""
    return sum(1 for row in rows or [] if row.get("status") in (None, "pending"))
