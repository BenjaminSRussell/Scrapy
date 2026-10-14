"""Worker heartbeat while a queue is idle (#695).

Stage workers poll on a fixed interval (stage 2 defaults to 30s) and hold a
lease much longer than one poll (stage 2 defaults to 900s). An idle worker
that keeps beating on that interval must not be treated as dead. The clock is
injected so a multi-minute quiet window can be simulated without sleeping.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Same cadence as ``DEFAULT_STAGE2_IDLE_SECONDS`` / ``DEFAULT_STAGE2_CLAIM_LEASE_SECONDS``.
# Kept as literals so importing this module does not pull the stage 2 worker.
DEFAULT_HEARTBEAT_INTERVAL = 30.0
DEFAULT_LEASE_TTL = 900.0
DEFAULT_MISS_LIMIT = 3


class ManualClock:
    """Callable clock. ``advance`` moves time and does not sleep."""

    def __init__(self, now: float = 0.0) -> None:
        self._now = float(now)

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("cannot advance the clock backwards")
        self._now += float(seconds)
        return self._now


@dataclass(frozen=True)
class Heartbeat:
    worker_id: str
    at: float
    idle: bool


class LeaseTable:
    """In-memory leases. Only an explicit refresh extends expiry."""

    def __init__(self) -> None:
        self._expiry: dict[str, float] = {}

    def refresh(self, worker_id: str, now: float, ttl: float) -> float:
        if ttl <= 0:
            raise ValueError("lease ttl must be positive")
        expiry = float(now) + float(ttl)
        self._expiry[worker_id] = expiry
        return expiry

    def expiry_of(self, worker_id: str) -> float | None:
        return self._expiry.get(worker_id)

    def expired(self, worker_id: str, now: float) -> bool:
        expiry = self._expiry.get(worker_id)
        if expiry is None:
            return False
        return float(now) >= expiry


class FakeQueue:
    """In-memory work queue. ``processed`` is the ack log, in submit order."""

    def __init__(self) -> None:
        self._pending: list[Any] = []
        self.processed: list[Any] = []

    def submit(self, item: Any) -> None:
        self._pending.append(item)

    def poll(self) -> Any | None:
        if not self._pending:
            return None
        return self._pending.pop(0)

    def ack(self, item: Any) -> None:
        self.processed.append(item)

    def __len__(self) -> int:
        return len(self._pending)


class WorkerHeartbeat:
    """Emit a heartbeat on ``interval`` and process at most one queued item per tick."""

    def __init__(
        self,
        worker_id: str,
        *,
        queue: FakeQueue,
        leases: LeaseTable,
        clock: Callable[[], float],
        interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        lease_ttl: float = DEFAULT_LEASE_TTL,
    ) -> None:
        if interval <= 0:
            raise ValueError("heartbeat interval must be positive")
        if lease_ttl <= 0:
            raise ValueError("lease ttl must be positive")
        self.worker_id = worker_id
        self.queue = queue
        self.leases = leases
        self.clock = clock
        self.interval = float(interval)
        self.lease_ttl = float(lease_ttl)
        self.beats: list[Heartbeat] = []
        self._last_beat: float | None = None

    def due(self) -> bool:
        if self._last_beat is None:
            return True
        return self.clock() - self._last_beat >= self.interval

    def beat(self, *, idle: bool) -> Heartbeat:
        now = float(self.clock())
        record = Heartbeat(self.worker_id, now, idle)
        self.beats.append(record)
        self._last_beat = now
        self.leases.refresh(self.worker_id, now, self.lease_ttl)
        return record

    def tick(self) -> str:
        """One scheduler step. A due beat is recorded before a waiting item is acked.

        Returns ``"work"`` when an item was processed, ``"heartbeat"`` when the
        only action was a beat, or ``"idle"`` when neither was due.
        """
        item = self.queue.poll()
        beat_now = self.due()
        if beat_now:
            self.beat(idle=item is None)
        if item is None:
            return "heartbeat" if beat_now else "idle"
        self.queue.ack(item)
        return "work"
