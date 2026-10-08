"""Typed feature flags / kill-switches (#304).

One place to read on/off switches instead of ad-hoc ``os.getenv(...) == "1"``
checks that each parse truthiness differently.

Lookup order for ``get_bool("SSRF_GUARD_ENABLED", default=True)``:

1. environment variable ``SSRF_GUARD_ENABLED``
2. ``feature_flags.ssrf_guard_enabled`` in config.yml (optional section)
3. ``default``; experimental features must default to ``False``

Booleans accept ``1/true/yes/on`` and ``0/false/no/off`` (case-insensitive).
Anything else is logged and treated as the default, so a typo never silently
flips a kill-switch. The flag registry is in ``docs/FEATURE_FLAGS.md``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})

_UNSET = object()


def _config_value(name: str) -> Any:
    try:
        from src.core.config import get_config

        section = get_config().get_section("feature_flags") or {}
    except Exception:  # config unavailable (tests, partial installs): env/default only
        return _UNSET
    if not isinstance(section, Mapping):
        return _UNSET
    key = name.lower()
    return section[key] if key in section else _UNSET


def _raw(name: str, env: Mapping[str, str] | None, use_config: bool) -> Any:
    source = os.environ if env is None else env
    if name in source:
        return source[name]
    if use_config:
        return _config_value(name)
    return _UNSET


def parse_bool(value: Any, default: bool) -> bool:
    """Strict truthiness: known words only, else ``default``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    text = str(value).strip().lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    if text:
        logger.warning("Unrecognised boolean flag value %r; using default %s", value, default)
    return default


def get_bool(
    name: str,
    default: bool = False,
    *,
    env: Mapping[str, str] | None = None,
    use_config: bool = True,
) -> bool:
    raw = _raw(name, env, use_config)
    return default if raw is _UNSET or raw is None else parse_bool(raw, default)


def get_str(
    name: str,
    default: str = "",
    *,
    env: Mapping[str, str] | None = None,
    use_config: bool = True,
) -> str:
    raw = _raw(name, env, use_config)
    if raw is _UNSET or raw is None:
        return default
    text = str(raw).strip()
    return text if text else default


def get_int(
    name: str,
    default: int = 0,
    *,
    env: Mapping[str, str] | None = None,
    use_config: bool = True,
) -> int:
    raw = _raw(name, env, use_config)
    if raw is _UNSET or raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        logger.warning("Flag %s=%r is not an integer; using default %d", name, raw, default)
        return default


# Registry of known flags: name -> (default, description). Experimental = default False.
FLAGS: dict[str, tuple[Any, str]] = {
    "ENABLE_EXPERIMENTAL_SPIDERS": (False, "Allow lab spiders (javascript, deep_dive, depth) to run (#391/#442)."),
    "SSRF_GUARD_ENABLED": (True, "Kill-switch for the SSRF download guard (#682). Leave on in prod."),
    "ASR_ENABLED": (False, "Register the speech-to-text pipeline (#470)."),
    "KAFKA_DLQ_ENABLED": (True, "Write undeliverable Kafka items to the file DLQ (#162)."),
}


def snapshot(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Effective value of every registered flag (for `cli validate` / debugging)."""
    return {name: get_bool(name, bool(default), env=env) for name, (default, _) in FLAGS.items()}
