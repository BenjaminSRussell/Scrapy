"""#700: Redis queue depth under a deterministic seed burst, then a controlled drain.

FakeRedis only. No shared Redis server, compose, or live scrape.
"""
from __future__ import annotations

import pytest
from prometheus_client import REGISTRY

from src.lakehouse.lakehouse_manager import InMemoryBackend
from src.lakehouse.seed_manager import (
    SEED_ENQUEUE_THROTTLED,
    SEED_REDIS_DEPTH_KEY,
    SeedManager,
    seed_enqueue_threshold,
)
from src.stage1.processors.js_priority_queue import JSPriorityQueue
from src.utils.redis import queue_watermark_settings, reset_queue_backpressure

pytestmark = pytest.mark.unit

BURST = 36
HIGH = 24
LOW = 8
MAX_SIZE = 48
DRAIN_RATE = 4
PRIORITY = 10  # below the shed cutoff, so backpressure does not sleep
SEED_BATCH = 7
QUEUE = SEED_REDIS_DEPTH_KEY


def _sample(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _gauge() -> float:
    return _sample("js_priority_queue_size", {"queue": QUEUE})


def _items(op: str) -> float:
    return _sample("js_priority_queue_items_total", {"queue": QUEUE, "op": op})


def _shed() -> float:
    return _sample("redis_queue_backpressure_total", {"queue": QUEUE, "action": "shed"})


def _throttled() -> float:
    assert SEED_ENQUEUE_THROTTLED is not None
    return REGISTRY.get_sample_value("seed_enqueue_throttled_total") or 0.0


def _evicted(reason: str) -> float:
    return _sample("js_priority_queue_evictions_total", {"queue": QUEUE, "reason": reason})


@pytest.fixture
def _burst_env(monkeypatch):
    monkeypatch.setenv("REDIS_QUEUE_HIGH_WATERMARK", str(HIGH))
    monkeypatch.setenv("REDIS_QUEUE_LOW_WATERMARK", str(LOW))
    monkeypatch.setenv("REDIS_QUEUE_BACKPRESSURE_MODE", "shed")
    monkeypatch.setenv("REDIS_QUEUE_SHED_BELOW_PRIORITY", "50")
    monkeypatch.setenv("REDIS_QUEUE_BACKPRESSURE_SLEEP_SECONDS", "0.01")
    monkeypatch.setenv("REDIS_QUEUE_BACKPRESSURE_MAX_BLOCK_SECONDS", "0.01")
    reset_queue_backpressure()
    yield
    reset_queue_backpressure()


def _run_burst() -> None:
    reset_queue_backpressure()
    fakeredis = pytest.importorskip("fakeredis")
    client = fakeredis.FakeRedis(decode_responses=True)
    queue = JSPriorityQueue(client, queue_key=QUEUE, max_size=MAX_SIZE, ttl_seconds=0)
    limits = queue_watermark_settings()
    assert (limits["high"], limits["low"], limits["mode"]) == (HIGH, LOW, "shed")
    assert seed_enqueue_threshold() == HIGH
    assert queue.max_size == MAX_SIZE
    ceiling = min(queue.max_size, HIGH + 1)

    enqueued_before = _items("enqueued")
    dequeued_before = _items("dequeued")
    shed_before = _shed()
    throttle_before = _throttled()
    overflow_before = _evicted("overflow")

    depths: list[int] = []
    accepted = 0
    for i in range(BURST):
        if queue.enqueue(f"https://uconn.edu/burst/{i:04d}", priority=PRIORITY):
            accepted += 1
        depths.append(queue.size())
        assert depths[-1] <= ceiling
        assert _gauge() == queue.size()

    assert accepted == HIGH + 1 == ceiling
    assert depths[:accepted] == list(range(1, accepted + 1))
    assert depths[accepted:] == [accepted] * (BURST - accepted)
    assert queue.size() == accepted
    assert _items("enqueued") - enqueued_before == accepted
    assert _shed() - shed_before == BURST - accepted
    assert _evicted("overflow") == overflow_before

    backend = InMemoryBackend()
    seeds = SeedManager(backend, redis_client=client, write_domain_urls=False)
    blocked = [f"https://uconn.edu/seed/{i:04d}" for i in range(SEED_BATCH)]
    blocked_result = seeds.add_urls_to_seeds(
        blocked, "https://uconn.edu/", "scout", enqueue_stage2=True
    )
    assert blocked_result["seed_inserted"] == 0
    assert blocked_result["stage2_enqueued"] == 0
    assert "seed_urls" not in backend.tables
    assert "stage2_queue" not in backend.tables
    assert _throttled() - throttle_before == SEED_BATCH
    assert queue.size() == accepted

    assert queue.enqueue("https://uconn.edu/burst/extra", priority=PRIORITY) is False
    assert queue.size() == accepted
    assert _shed() - shed_before == BURST - accepted + 1

    consumed: list[str] = []
    previous = queue.size()
    while queue.size() > LOW:
        batch = queue.dequeue(count=DRAIN_RATE)
        assert 1 <= len(batch) <= DRAIN_RATE
        consumed.extend(item["url"] for item in batch)
        assert queue.size() == previous - len(batch)
        previous = queue.size()
        assert _gauge() == queue.size()
    assert queue.size() <= LOW < accepted
    assert consumed[:DRAIN_RATE] == [f"https://uconn.edu/burst/{i:04d}" for i in range(DRAIN_RATE)]

    before_recover = queue.size()
    reopened = 0
    for i in range(3):
        assert queue.enqueue(f"https://uconn.edu/recover/{i}", priority=PRIORITY) is True
        reopened += 1
        assert queue.size() == before_recover + reopened
        assert _gauge() == queue.size()
    assert queue.size() <= HIGH

    allowed = seeds.add_urls_to_seeds(
        ["https://uconn.edu/seed/ok"],
        "https://uconn.edu/",
        "scout",
        enqueue_stage2=True,
    )
    assert allowed["seed_inserted"] == 1
    assert allowed["stage2_enqueued"] == 1
    assert len(backend.read("seed_urls")) == 1
    assert backend.read("stage2_queue")[0]["status"] == "pending"
    assert _throttled() - throttle_before == SEED_BATCH

    while queue.size():
        batch = queue.dequeue(count=DRAIN_RATE)
        assert batch
        consumed.extend(item["url"] for item in batch)
        assert _gauge() == queue.size()

    enqueued_total = accepted + reopened
    assert queue.size() == 0
    assert _gauge() == 0.0
    assert len(consumed) == len(set(consumed)) == enqueued_total
    assert _items("enqueued") - enqueued_before == enqueued_total
    assert _items("dequeued") - dequeued_before == len(consumed)
    assert _gauge() == (_items("enqueued") - enqueued_before) - (_items("dequeued") - dequeued_before)
    assert queue.redis.zcard(queue.queue_key) == 0
    assert queue.redis.zcard(queue.enqueued_key) == 0


def test_burst_seed_depth_grows_then_drains_on_an_isolated_redis(_burst_env):
    _run_burst()
    _run_burst()
