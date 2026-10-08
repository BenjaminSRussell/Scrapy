"""Stage 2 hop accounting, Stage 2 -> 3/4 barrier and reconciliation (#646).

The funnel is computed from ``stage2_queue`` itself, per URL, rather than from
worker self-reports (which is how silent loss went unnoticed):

* ``enqueued``      rows ``pending`` when Stage 2 started
* ``claimed``       of those, rows no longer ``pending`` afterwards
* ``ok`` / ``failed`` claimed rows now ``completed`` / any other terminal status
* ``lost``          claimed rows that are gone from the queue (e.g. an overwrite
                    race): the silent loss reconciliation exists to catch
* ``still_pending`` rows Stage 2 left ``pending`` (deferred, retrying, or a
                    queue-status write that failed): the barrier's concern
* ``late_appends``  ``pending`` rows that appeared after Stage 2 started (#310)

Counter/funnel shape adapted from #1011.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

BARRIER_MODES = ("flag", "strict", "off")
_PENDING = "pending"
_OK = "completed"


def _key(row: dict[str, Any]) -> str | None:
    key = row.get("url_hash") or row.get("url")
    return str(key) if key else None


def _rank(status: str) -> int:
    return 2 if status == _OK else 0 if status == _PENDING else 1


def url_statuses(rows: Iterable[dict[str, Any]] | None) -> dict[str, str]:
    """Best status per URL (completed > other terminal > pending) across duplicate rows."""
    out: dict[str, str] = {}
    for row in rows or []:
        key = _key(row)
        if not key:
            continue
        status = row.get("status") or _PENDING
        if key not in out or _rank(status) > _rank(out[key]):
            out[key] = status
    return out


@dataclass
class HopCounters:
    discovered: int = 0  # Scout's count; informational (dedup/filters shrink it)
    enqueued: int = 0
    claimed: int = 0
    ok: int = 0
    failed: int = 0
    lost: int = 0
    still_pending: int = 0
    late_appends: int = 0

    @property
    def terminal(self) -> int:
        return self.ok + self.failed

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def count_hops(
    before: Iterable[dict[str, Any]] | None,
    after: Iterable[dict[str, Any]] | None,
    *,
    discovered: int = 0,
) -> HopCounters:
    """Funnel for the rows pending in ``before`` as seen in ``after``."""
    start = url_statuses(before)
    end = url_statuses(after)
    hops = HopCounters(discovered=discovered)
    for key, status in start.items():
        if status != _PENDING:
            continue
        hops.enqueued += 1
        now = end.get(key)
        if now is None:
            hops.lost += 1
        elif now == _PENDING:
            hops.still_pending += 1
        elif now == _OK:
            hops.ok += 1
        else:
            hops.failed += 1
    hops.claimed = hops.enqueued - hops.still_pending
    hops.late_appends = sum(1 for k, s in end.items() if s == _PENDING and k not in start)
    return hops


@dataclass
class ReconciliationResult:
    hops: HopCounters
    tolerance: int
    within_tolerance: bool
    alerts: list[str] = field(default_factory=list)
    stage2_watermark: str | None = None
    barrier: str = "flag"

    def panel(self, crawl_job_id: str | None = None) -> dict[str, Any]:
        """Structured hop-funnel payload for dashboards / run stats."""
        return {
            "panel": "hop_funnel",
            "crawl_job_id": crawl_job_id,
            "funnel": self.hops.to_dict(),
            "tolerance": self.tolerance,
            "within_tolerance": self.within_tolerance,
            "alerts": list(self.alerts),
            "stage2_watermark": self.stage2_watermark,
            "barrier": self.barrier,
        }


def reconcile(hops: HopCounters, *, tolerance: int = 0, barrier: str = "flag") -> ReconciliationResult:
    """``claimed`` must equal ``ok + failed`` within ``tolerance``; pending/late rows only alert."""
    alerts: list[str] = []
    gap = hops.claimed - hops.terminal  # == hops.lost
    if gap > tolerance:
        alerts.append(
            f"hop_lost: {hops.lost} of {hops.claimed} claimed stage2_queue row(s) vanished "
            f"without completed/failed (tolerance {tolerance})"
        )
    if hops.still_pending:
        alerts.append(f"stage2_pending: {hops.still_pending} row(s) still pending after Stage 2")
    if hops.late_appends:
        alerts.append(f"late_append: {hops.late_appends} row(s) queued after Stage 2 started; next run picks them up")
    return ReconciliationResult(
        hops=hops, tolerance=tolerance, within_tolerance=gap <= tolerance, alerts=alerts, barrier=barrier
    )


def alert_kind(alert: str) -> str:
    return alert.split(":", 1)[0]
