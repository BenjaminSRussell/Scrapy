"""Per-job notice preferences (#487).

Operators choose which events for a job would be delivered: fail, success, and
stall. Quiet hours are an optional pair of local hours. The shape is stored on
the job metadata dict. There is no job model in this tree, and this module does
not migrate one.

The filter is pure. It does not send a webhook, open a socket, or read a clock.
Callers pass the local hour. Defaults keep today's notices: fail on, success
off, stall on, quiet hours empty.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

NoticeKind = Literal["fail", "success", "stall"]

NOTIFY_FAIL = "notify_fail"
NOTIFY_SUCCESS = "notify_success"
NOTIFY_STALL = "notify_stall"
QUIET_HOURS = "quiet_hours"

DEFAULT_NOTIFY_FAIL = True
DEFAULT_NOTIFY_SUCCESS = False
DEFAULT_NOTIFY_STALL = True

_KINDS = ("fail", "success", "stall")
_HOUR_ERROR = "quiet-hours endpoints and local hour must be integers 0..23"


@dataclass(frozen=True, slots=True)
class NoticePrefs:
    """Three booleans and an optional quiet-hours pair.

    ``quiet_hours`` is ``None`` when unset. A set pair is ``(start, end)`` in
    local hours, half-open: hours from ``start`` up to but not including
    ``end``. When ``start > end`` the window wraps midnight. ``start == end``
    is an empty window.
    """

    notify_fail: bool = DEFAULT_NOTIFY_FAIL
    notify_success: bool = DEFAULT_NOTIFY_SUCCESS
    notify_stall: bool = DEFAULT_NOTIFY_STALL
    quiet_hours: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        for key in (NOTIFY_FAIL, NOTIFY_SUCCESS, NOTIFY_STALL):
            if not isinstance(getattr(self, key), bool):
                raise ValueError(f"{key} must be a boolean")
        if self.quiet_hours is not None:
            object.__setattr__(self, "quiet_hours", parse_quiet_hours(self.quiet_hours))

    def as_metadata(self) -> dict[str, Any]:
        """Metadata fields: the three booleans and the quiet-hours pair."""
        if self.quiet_hours is None:
            quiet: list[int] | None = None
        else:
            quiet = [self.quiet_hours[0], self.quiet_hours[1]]
        return {
            NOTIFY_FAIL: self.notify_fail,
            NOTIFY_SUCCESS: self.notify_success,
            NOTIFY_STALL: self.notify_stall,
            QUIET_HOURS: quiet,
        }


def default_notice_prefs() -> NoticePrefs:
    """Today's notices: fail on, success off, stall on, no quiet hours."""
    return NoticePrefs()


def _as_bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{key} must be a boolean")


def _as_hour(value: Any) -> int:
    # ``bool`` is an ``int`` subclass; a flag is not an hour.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(_HOUR_ERROR)
    if value < 0 or value > 23:
        raise ValueError(_HOUR_ERROR)
    return value


def parse_quiet_hours(value: Any) -> tuple[int, int] | None:
    """``None`` or a pair of local hours. An empty sequence is no window."""
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("quiet_hours must be a pair of hours or empty")
    if len(value) == 0:
        return None
    if len(value) != 2:
        raise ValueError("quiet_hours must be a pair of hours or empty")
    return (_as_hour(value[0]), _as_hour(value[1]))


def notice_prefs_from_metadata(metadata: Mapping[str, Any] | None) -> NoticePrefs:
    """Read preferences from job metadata. Missing keys keep today's defaults."""
    if metadata is None:
        return default_notice_prefs()
    if not isinstance(metadata, Mapping):
        raise ValueError("job metadata must be a mapping")
    notify_fail = (
        DEFAULT_NOTIFY_FAIL if NOTIFY_FAIL not in metadata else _as_bool(metadata[NOTIFY_FAIL], NOTIFY_FAIL)
    )
    notify_success = (
        DEFAULT_NOTIFY_SUCCESS
        if NOTIFY_SUCCESS not in metadata
        else _as_bool(metadata[NOTIFY_SUCCESS], NOTIFY_SUCCESS)
    )
    notify_stall = (
        DEFAULT_NOTIFY_STALL if NOTIFY_STALL not in metadata else _as_bool(metadata[NOTIFY_STALL], NOTIFY_STALL)
    )
    quiet_hours = None if QUIET_HOURS not in metadata else parse_quiet_hours(metadata[QUIET_HOURS])
    return NoticePrefs(
        notify_fail=notify_fail,
        notify_success=notify_success,
        notify_stall=notify_stall,
        quiet_hours=quiet_hours,
    )


def store_notice_prefs(metadata: dict[str, Any], prefs: NoticePrefs | None = None) -> dict[str, Any]:
    """Store the three booleans and the optional quiet-hours pair on ``metadata``.

    Other keys are left in place. Returns the same dict.
    """
    if not isinstance(metadata, dict):
        raise ValueError("job metadata must be a dict")
    chosen = default_notice_prefs() if prefs is None else prefs
    metadata.update(chosen.as_metadata())
    return metadata


def hour_in_quiet_window(local_hour: int, quiet_hours: tuple[int, int] | None) -> bool:
    """True when ``local_hour`` falls inside a set window. Empty windows match nothing."""
    if quiet_hours is None:
        return False
    start, end = quiet_hours
    hour = _as_hour(local_hour)
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def local_hour_of(when: datetime) -> int:
    """Hour of ``when`` on the machine's local clock.

    A naive datetime is already local. An aware datetime is converted with
    ``astimezone()`` (no argument), which uses the local zone.
    """
    if when.tzinfo is None or when.tzinfo.utcoffset(when) is None:
        hour = when.hour
    else:
        hour = when.astimezone().hour
    return _as_hour(hour)


def allow_notice(kind: str, prefs: NoticePrefs, local_hour: int) -> bool:
    """Return True when this event should be delivered.

    Drops a success event when success is off. Drops any event whose local
    hour falls inside a set quiet-hours window. Does not drop fail or stall
    when those toggles are on and the hour is outside the window.
    """
    if kind not in _KINDS:
        raise ValueError(f"unknown notice kind: {kind}")
    if not isinstance(prefs, NoticePrefs):
        raise ValueError("prefs must be NoticePrefs")
    hour = _as_hour(local_hour)
    if hour_in_quiet_window(hour, prefs.quiet_hours):
        return False
    if kind == "fail":
        return prefs.notify_fail
    if kind == "success":
        return prefs.notify_success
    return prefs.notify_stall


def allow_notice_from_metadata(
    kind: str,
    metadata: Mapping[str, Any] | None,
    local_hour: int,
) -> bool:
    """Filter using preferences stored on job metadata."""
    return allow_notice(kind, notice_prefs_from_metadata(metadata), local_hour)
