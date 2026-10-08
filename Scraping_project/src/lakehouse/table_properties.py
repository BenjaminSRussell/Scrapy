"""Delta table retention properties from config (#493).

delta-rs reads two retention table properties:

* ``delta.logRetentionDuration``: how long ``_delta_log`` JSON commits (and
  old checkpoints) are kept. Expired entries are removed when a checkpoint is
  written (``delta.enableExpiredLogCleanup``, on by default), which bounds
  time travel.
* ``delta.deletedFileRetentionDuration``: the minimum age before ``vacuum``
  may delete unreferenced data files. ``vacuum(retention_hours=None)`` uses
  it, and ``enforce_retention_duration`` rejects anything shorter.

Neither was ever set, so the vacuum script's hard-coded 168 h and the table
defaults (30 days / 1 week) could diverge silently. ``delta.checkpointRetentionDuration``
is not supported: delta-kernel rejects every value for it, so it is refused here
with a clear error instead of failing the first write.
"""

from __future__ import annotations

import re
from typing import Any

LOG_RETENTION_PROPERTY = "delta.logRetentionDuration"
DELETED_FILE_RETENTION_PROPERTY = "delta.deletedFileRetentionDuration"

# config key under delta_lake.retention -> (table property, default)
RETENTION_KEYS: dict[str, tuple[str, str]] = {
    "log_retention": (LOG_RETENTION_PROPERTY, "interval 30 days"),
    "deleted_file_retention": (DELETED_FILE_RETENTION_PROPERTY, "interval 7 days"),
}
UNSUPPORTED_KEYS = {"checkpoint_retention": "delta.checkpointRetentionDuration"}

_UNIT_HOURS = {
    "hour": 1.0,
    "day": 24.0,
    "week": 168.0,
    "minute": 1 / 60,
    "second": 1 / 3600,
}
_INTERVAL = re.compile(r"^\s*(?:interval\s+)?(\d+)\s*(second|minute|hour|day|week)s?\s*$", re.IGNORECASE)


def normalize_interval(value: Any) -> str:
    """``"7 days"`` / ``"interval 1 week"`` / ``168`` (hours) -> ``"interval N unit"``."""
    if isinstance(value, bool):
        raise ValueError(f"Invalid Delta retention interval: {value!r}")
    if isinstance(value, int | float):
        return f"interval {int(value)} hours"
    m = _INTERVAL.match(str(value))
    if not m:
        raise ValueError(f"Invalid Delta retention interval {value!r}; use e.g. 'interval 7 days'")
    n, unit = int(m.group(1)), m.group(2).lower()
    return f"interval {n} {unit}{'' if n == 1 else 's'}"


def interval_hours(value: str) -> float:
    m = _INTERVAL.match(normalize_interval(value))
    assert m is not None
    return int(m.group(1)) * _UNIT_HOURS[m.group(2).lower()]


def retention_properties(config: Any) -> dict[str, str]:
    """Table properties for ``delta_lake.retention`` (defaults = Delta's own)."""
    block = None
    try:
        block = config.get("delta_lake.retention")
    except Exception:
        block = None
    block = block if isinstance(block, dict) else {}
    for key, prop in UNSUPPORTED_KEYS.items():
        if block.get(key) is not None:
            raise ValueError(f"delta_lake.retention.{key} ({prop}) is not supported by delta-rs; remove it")
    unknown = set(block) - set(RETENTION_KEYS)
    if unknown:
        raise ValueError(f"Unknown delta_lake.retention keys {sorted(unknown)}; known: {sorted(RETENTION_KEYS)}")
    return {prop: normalize_interval(block.get(key, default)) for key, (prop, default) in RETENTION_KEYS.items()}
