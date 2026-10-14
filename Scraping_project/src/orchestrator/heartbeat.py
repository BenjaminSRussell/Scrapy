"""Orchestrator watch for worker heartbeats and leases (#695).

A worker that still beats inside ``miss_limit`` intervals, and whose lease
has not elapsed, stays healthy. Silence past that window is a real failure.
Nothing here sleeps or talks to Redis.
"""

from __future__ import annotations

from collections.abc import Callable

from src.workers.heartbeat import DEFAULT_HEARTBEAT_INTERVAL, DEFAULT_MISS_LIMIT, Heartbeat, LeaseTable


class HeartbeatSupervisor:
    """Classify one worker from beats the worker already published."""

    def __init__(
        self,
        *,
        leases: LeaseTable,
        clock: Callable[[], float],
        interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        miss_limit: int = DEFAULT_MISS_LIMIT,
    ) -> None:
        if interval <= 0:
            raise ValueError("heartbeat interval must be positive")
        if miss_limit < 1:
            raise ValueError("miss_limit must be at least 1")
        self.leases = leases
        self.clock = clock
        self.interval = float(interval)
        self.miss_limit = int(miss_limit)
        self.failures: list[dict[str, object]] = []
        self.alerts: list[str] = []
        self.lease_expiries: list[str] = []
        self._last: dict[str, float] = {}
        self._reported: set[tuple[str, str]] = set()

    def observe(self, beat: Heartbeat) -> None:
        """Record a beat. Does not refresh the lease; the worker did that."""
        previous = self._last.get(beat.worker_id)
        if previous is not None and beat.at < previous:
            raise ValueError(f"heartbeat for {beat.worker_id} went backwards")
        self._last[beat.worker_id] = beat.at

    def evaluate(self, worker_id: str) -> str:
        """``healthy``, ``failed``, or ``unknown`` when no beat has been seen.

        A gap of exactly ``interval * miss_limit`` is still healthy. The next
        instant past that, or a lease whose TTL has elapsed, is a failure.
        Repeated calls do not emit a second alert for the same reason.
        """
        last = self._last.get(worker_id)
        if last is None:
            return "unknown"
        now = float(self.clock())
        failed = False
        if now - last > self.interval * self.miss_limit:
            self._report(worker_id, "missed_heartbeat", now)
            failed = True
        if self.leases.expired(worker_id, now):
            self._report(worker_id, "lease_expired", now)
            if worker_id not in self.lease_expiries:
                self.lease_expiries.append(worker_id)
            failed = True
        return "failed" if failed else "healthy"

    def _report(self, worker_id: str, reason: str, now: float) -> None:
        key = (worker_id, reason)
        if key in self._reported:
            return
        self._reported.add(key)
        self.failures.append({"worker_id": worker_id, "reason": reason, "at": now})
        self.alerts.append(f"{worker_id} {reason}")
