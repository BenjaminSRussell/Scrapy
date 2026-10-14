"""Local carbon stub for the Stage 3 / Stage 4 scheduling hint (#769).

The hint is off unless ``carbon_scheduling.enabled`` is boolean true.
Nothing here delays a worker, and nothing here contacts a carbon API.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

# Config key, documented next to the stub. Only boolean true enables the hint.
# A disagreeing value (1, "true", "yes", "on", null) leaves it off.
CARBON_HINT_ENABLED_KEY = "carbon_scheduling.enabled"
# Optional window. Not applied to Stage 3 or Stage 4 batches.
CARBON_DELAY_WINDOW_KEY = "carbon_scheduling.delay_window_minutes"

# Fixed local placeholder. Not a grid measurement.
STUB_CARBON_READING = "local-stub"

_EXPLICIT_TRUE = frozenset({"true", "True", "TRUE"})
_EXPLICIT_FALSE = frozenset({"false", "False", "FALSE"})
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yml"
_ENABLED_LINE = re.compile(r"^enabled:\s*(\S+)\s*$")


def stub_carbon_reading() -> str:
    """Return the fixed local carbon stub.

    No network I/O. ``carbon_scheduling.enabled`` (see
    ``CARBON_HINT_ENABLED_KEY``) is the only switch, and only an explicit
    boolean true turns the hint on. ``carbon_scheduling.delay_window_minutes``
    is optional and is not applied. Stage 3 and Stage 4 workers do not call
    this, so a missing reading cannot block a batch.
    """
    return STUB_CARBON_READING


def _enabled_scalar(text: str) -> str | None:
    """Raw ``enabled`` token under ``carbon_scheduling``, comments stripped.

    YAML 1.1 treats ``yes`` and ``on`` as true. The token itself must be
    ``true`` before this module will agree. A token we cannot see stays off.
    """
    in_section = False
    section_indent = 0
    for line in text.splitlines():
        code = line.split("#", 1)[0].rstrip()
        if not code.strip():
            continue
        indent = len(code) - len(code.lstrip(" "))
        body = code.lstrip(" ")
        if not in_section:
            if body.startswith("carbon_scheduling:"):
                in_section = True
                section_indent = indent
            continue
        if indent <= section_indent:
            break
        match = _ENABLED_LINE.match(body)
        if match:
            return match.group(1)
    return None


def parse_config_text(text: str) -> dict[str, Any]:
    """Parse text for this hint only. Not the pipeline config loader.

    ``true`` / ``false`` stay booleans. ``yes``, ``on``, and quoted
    ``"true"`` do not become boolean true.
    """
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    section = loaded.get("carbon_scheduling")
    if not isinstance(section, dict):
        return loaded
    scalar = _enabled_scalar(text)
    if scalar in _EXPLICIT_TRUE:
        section["enabled"] = True
    elif scalar in _EXPLICIT_FALSE:
        section["enabled"] = False
    elif "enabled" in section:
        section["enabled"] = False if scalar is None else scalar
    return loaded


def _document(document: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if document is not None:
        return document
    try:
        text = _CONFIG_PATH.read_text(encoding="utf-8")
    except OSError:
        return {}
    return parse_config_text(text)


def _section(document: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    data = _document(document)
    section = data.get("carbon_scheduling")
    if not isinstance(section, Mapping):
        return None
    return section


def carbon_hint_enabled(document: Mapping[str, Any] | None = None) -> bool:
    """True only when ``carbon_scheduling.enabled`` is explicitly boolean true.

    Missing config, ``false``, and every non-boolean leave the hint off.
    With no document, the shipped config file is read the same way.
    """
    if document is None:
        try:
            text = _CONFIG_PATH.read_text(encoding="utf-8")
        except OSError:
            return False
        return _enabled_scalar(text) in _EXPLICIT_TRUE
    section = _section(document)
    if section is None:
        return False
    return section.get("enabled") is True


def delay_window_minutes(document: Mapping[str, Any] | None = None) -> int | None:
    """Optional delay window in minutes, or None when it is unset or not an int.

    This value is not applied. Calling it does not sleep or reschedule work.
    """
    section = _section(document)
    if section is None or "delay_window_minutes" not in section:
        return None
    value = section.get("delay_window_minutes")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    minutes = int(value)
    if minutes < 0:
        return None
    return minutes
