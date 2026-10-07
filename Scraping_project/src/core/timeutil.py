"""Timezone-aware UTC helpers (#244). Use these instead of ``datetime.utcnow()``."""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Current time as an aware datetime in UTC (``tzinfo=timezone.utc``)."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Current UTC time as ISO-8601 with a ``Z`` suffix.

    Same string shape the code produced with ``datetime.utcnow().isoformat() + "Z"``,
    e.g. ``2026-10-07T22:30:00.123456Z``.
    """
    return utc_now().isoformat().replace("+00:00", "Z")
