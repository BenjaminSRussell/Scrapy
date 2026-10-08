"""#377: JSPriorityQueue is bounded by max size (lowest priority evicted) and TTL."""

import fakeredis
import pytest
from prometheus_client import REGISTRY

from src.stage1.processors import js_priority_queue as jpq
from src.stage1.processors.js_priority_queue import JSPriorityQueue

pytestmark = [pytest.mark.unit, pytest.mark.stage1]


def _q(max_size=0, ttl=0.0, key="jsq", decode=True):
    return JSPriorityQueue(fakeredis.FakeRedis(decode_responses=decode), queue_key=key, max_size=max_size, ttl_seconds=ttl)


def _evictions(key, reason):
    return REGISTRY.get_sample_value("js_priority_queue_evictions_total", {"queue": key, "reason": reason}) or 0.0


def test_soak_never_exceeds_max_size_and_keeps_highest_priorities():
    q = _q(max_size=50, key="soak")
    before = _evictions("soak", "overflow")
    for i in range(1000):
        q.enqueue(f"https://u.edu/p{i}", priority=i % 101, js_confidence=0.6)
        assert q.size() <= 50
    kept = sorted((p for _, p in q.peek(50)), reverse=True)
    assert kept == sorted((i % 101 for i in range(1000)), reverse=True)[:50]  # exactly the top 50
    assert _evictions("soak", "overflow") - before == 950
    # Metadata and enqueue-time index are trimmed with the queue (no orphaned hash entries).
    assert q.redis.hlen(q.metadata_key) == 50
    assert q.redis.zcard(q.enqueued_key) == 50
    assert REGISTRY.get_sample_value("js_priority_queue_size", {"queue": "soak"}) == 50


def test_low_priority_arrival_into_full_queue_is_rejected():
    q = _q(max_size=2)
    assert q.enqueue("https://u.edu/a", priority=100)
    assert q.enqueue("https://u.edu/b", priority=50)
    assert q.enqueue("https://u.edu/c", priority=10) is False
    assert [u for u, _ in q.peek()] == ["https://u.edu/a", "https://u.edu/b"]
    # Higher priority displaces the lowest.
    assert q.enqueue("https://u.edu/d", priority=75)
    assert [u for u, _ in q.peek()] == ["https://u.edu/a", "https://u.edu/d"]


def test_batch_enqueue_reports_only_survivors():
    q = _q(max_size=3)
    batch = [(f"https://u.edu/b{i}", i * 10, {"src": "t"}) for i in range(6)]
    assert q.enqueue_batch(batch) == 3
    assert q.size() == 3
    assert {u for u, _ in q.peek()} == {"https://u.edu/b5", "https://u.edu/b4", "https://u.edu/b3"}


def test_ttl_prunes_stale_entries_on_enqueue_and_dequeue(monkeypatch):
    clock = [1_000_000.0]
    monkeypatch.setattr(jpq.time, "time", lambda: clock[0])
    q = _q(ttl=60, key="ttl")
    before = _evictions("ttl", "ttl")
    q.enqueue("https://u.edu/old", priority=100, js_confidence=0.9)
    clock[0] += 30
    q.enqueue("https://u.edu/mid", priority=10)
    clock[0] += 45  # old is 75s old, mid 45s
    out = q.dequeue(count=10)
    assert [d["url"] for d in out] == ["https://u.edu/mid"]
    assert _evictions("ttl", "ttl") - before == 1
    assert q.redis.hget(q.metadata_key, "https://u.edu/old") is None
    assert q.redis.zcard(q.enqueued_key) == 0  # dequeue clears the index too


def test_prune_stale_explicit_now():
    q = _q(ttl=10)
    q.redis.zadd(q.queue_key, {"https://u.edu/x": -5})
    q.redis.zadd(q.enqueued_key, {"https://u.edu/x": 100.0})
    assert q.prune_stale(now=105) == []
    assert q.prune_stale(now=111) == ["https://u.edu/x"]
    assert q.size() == 0


def test_evicted_url_stays_claimed_so_it_is_not_requeued():
    q = _q(max_size=1)
    q.enqueue("https://u.edu/hi", priority=100)
    assert q.enqueue("https://u.edu/lo", priority=1) is False
    assert q.enqueue("https://u.edu/lo", priority=100) is False  # already claimed this crawl


def test_bytes_responses_and_zero_means_unbounded():
    q = _q(max_size=0, ttl=0, decode=False)
    for i in range(20):
        q.enqueue(f"https://u.edu/{i}", priority=i)
    assert q.size() == 20 and q.prune_stale() == []
    q2 = _q(max_size=1, decode=False)
    q2.enqueue("https://u.edu/a", priority=5)
    assert q2.enqueue("https://u.edu/b", priority=1) is False


def test_defaults_come_from_config(monkeypatch):
    values = {"stage1.js_queue_max_size": 7, "stage1.js_queue_ttl_seconds": 12}
    monkeypatch.setattr(jpq, "_config_value", lambda key, default: values.get(key, default))
    q = JSPriorityQueue(fakeredis.FakeRedis(decode_responses=True), queue_key="cfg")
    assert (q.max_size, q.ttl_seconds) == (7, 12.0)
    assert q.get_stats()["max_size"] == 7


def test_shipped_config_bounds_the_queue():
    from src.core.config import get_config

    cfg = get_config()
    assert int(cfg.get("stage1.js_queue_max_size")) > 0
    assert float(cfg.get("stage1.js_queue_ttl_seconds")) > 0


def test_new_built_instances_stay_unbounded():
    q = JSPriorityQueue.__new__(JSPriorityQueue)
    q.redis = fakeredis.FakeRedis(decode_responses=True)
    q.queue_key, q.hash_key, q.metadata_key = "legacy", "legacy:seen", "legacy:meta"
    assert q.enqueue_batch([(f"https://u.edu/{i}", 1, None) for i in range(5)]) == 5
    assert q.size() == 5
