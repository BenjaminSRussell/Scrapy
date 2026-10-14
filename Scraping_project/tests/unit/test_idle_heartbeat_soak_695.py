"""10-minute idle heartbeat soak with a fake queue and clock (#695).

No wall-clock wait: ``ManualClock.advance`` is the only way time moves.
"""

from __future__ import annotations

import time

import pytest

from src.orchestrator.heartbeat import HeartbeatSupervisor
from src.stage2.stage2_worker import DEFAULT_STAGE2_CLAIM_LEASE_SECONDS, DEFAULT_STAGE2_IDLE_SECONDS
from src.workers.heartbeat import (
    DEFAULT_HEARTBEAT_INTERVAL,
    DEFAULT_LEASE_TTL,
    DEFAULT_MISS_LIMIT,
    FakeQueue,
    LeaseTable,
    ManualClock,
    WorkerHeartbeat,
)

IDLE_WINDOW = 10 * 60.0
WORKER_ID = "stage2-1"


def test_heartbeat_defaults_match_stage2_poll_and_lease():
    assert DEFAULT_HEARTBEAT_INTERVAL == DEFAULT_STAGE2_IDLE_SECONDS
    assert DEFAULT_LEASE_TTL == float(DEFAULT_STAGE2_CLAIM_LEASE_SECONDS)
    assert DEFAULT_LEASE_TTL > IDLE_WINDOW
    assert DEFAULT_HEARTBEAT_INTERVAL * DEFAULT_MISS_LIMIT < IDLE_WINDOW


@pytest.mark.unit
def test_ten_minute_idle_heartbeat_stays_healthy_and_recovers():
    """Simulate 10 idle minutes, then one submitted item, without sleeping."""
    clock = ManualClock(0.0)
    queue = FakeQueue()
    leases = LeaseTable()
    worker = WorkerHeartbeat(WORKER_ID, queue=queue, leases=leases, clock=clock)
    supervisor = HeartbeatSupervisor(leases=leases, clock=clock)

    started = time.monotonic()
    assert worker.tick() == "heartbeat"
    supervisor.observe(worker.beats[-1])
    assert supervisor.evaluate(WORKER_ID) == "healthy"

    while clock() < IDLE_WINDOW:
        clock.advance(DEFAULT_HEARTBEAT_INTERVAL)
        # The beat for this instant has not landed yet. One interval of silence
        # is still inside the miss limit and inside the lease.
        assert supervisor.evaluate(WORKER_ID) == "healthy"
        assert supervisor.failures == []
        assert supervisor.alerts == []
        assert supervisor.lease_expiries == []
        assert leases.expired(WORKER_ID, clock()) is False
        assert worker.tick() == "heartbeat"
        supervisor.observe(worker.beats[-1])
        assert supervisor.evaluate(WORKER_ID) == "healthy"

    elapsed = time.monotonic() - started
    assert elapsed < 2.0, f"idle window slept wall-clock time ({elapsed:.2f}s)"
    assert clock() == IDLE_WINDOW

    beats = worker.beats
    gaps = [beats[i].at - beats[i - 1].at for i in range(1, len(beats))]
    assert beats[0].at == 0.0
    assert beats[-1].at == IDLE_WINDOW
    assert len(beats) == int(IDLE_WINDOW / DEFAULT_HEARTBEAT_INTERVAL) + 1
    assert gaps
    assert all(0 < gap <= DEFAULT_HEARTBEAT_INTERVAL for gap in gaps)
    assert all(beat.idle for beat in beats)
    assert all(beat.worker_id == WORKER_ID for beat in beats)
    assert supervisor.failures == []
    assert supervisor.alerts == []
    assert supervisor.lease_expiries == []
    assert leases.expired(WORKER_ID, clock()) is False
    expiry = leases.expiry_of(WORKER_ID)
    assert expiry is not None and expiry == IDLE_WINDOW + DEFAULT_LEASE_TTL

    item = {"url": "https://uconn.edu/catalog"}
    queue.submit(item)
    before = clock()
    assert worker.tick() == "work"
    assert clock() == before  # recovery does not wait out another idle interval
    assert queue.processed == [item]
    assert len(queue) == 0
    assert worker.tick() == "idle"
    assert queue.processed == [item]
    assert supervisor.evaluate(WORKER_ID) == "healthy"
    assert supervisor.failures == []
    assert supervisor.lease_expiries == []


@pytest.mark.unit
def test_silence_past_the_miss_window_is_a_real_failure():
    """The soak stays green because beats arrive, not because failure is disabled."""
    clock = ManualClock(0.0)
    queue = FakeQueue()
    leases = LeaseTable()
    worker = WorkerHeartbeat(WORKER_ID, queue=queue, leases=leases, clock=clock)
    supervisor = HeartbeatSupervisor(leases=leases, clock=clock)

    assert worker.tick() == "heartbeat"
    supervisor.observe(worker.beats[-1])

    clock.advance(DEFAULT_HEARTBEAT_INTERVAL * DEFAULT_MISS_LIMIT)
    assert supervisor.evaluate(WORKER_ID) == "healthy"
    assert leases.expired(WORKER_ID, clock()) is False

    clock.advance(0.001)
    assert supervisor.evaluate(WORKER_ID) == "failed"
    assert supervisor.failures == [{"worker_id": WORKER_ID, "reason": "missed_heartbeat", "at": clock()}]
    assert supervisor.alerts == [f"{WORKER_ID} missed_heartbeat"]
    assert supervisor.lease_expiries == []

    clock.advance(DEFAULT_LEASE_TTL)
    assert leases.expired(WORKER_ID, clock()) is True
    assert supervisor.evaluate(WORKER_ID) == "failed"
    assert supervisor.lease_expiries == [WORKER_ID]
    assert [row["reason"] for row in supervisor.failures] == ["missed_heartbeat", "lease_expired"]
    # A second look must not raise a duplicate alert.
    assert supervisor.evaluate(WORKER_ID) == "failed"
    assert supervisor.alerts == [
        f"{WORKER_ID} missed_heartbeat",
        f"{WORKER_ID} lease_expired",
    ]
    assert queue.processed == []
