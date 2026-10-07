"""#159 atomic first-seen claim; #163 fail-closed seen-store policy."""

import threading

import fakeredis
import pytest
import redis

from src.stage1.processors.js_priority_queue import JSPriorityQueue
from src.utils import redis as redis_utils
from src.utils.redis import RedisHelper, SeenStoreUnavailable


def _helper(server=None):
    h = RedisHelper()
    h._client = fakeredis.FakeRedis(server=server or fakeredis.FakeServer(), decode_responses=True)
    return h


class _Broken:
    def __getattr__(self, name):
        def fail(*a, **k):
            raise redis.ConnectionError("connection refused")
        return fail


def _broken_helper():
    h = RedisHelper()
    h._client = _Broken()
    return h


# ---- #159: exactly one owner ------------------------------------------------

def test_concurrent_claims_yield_exactly_one_owner():
    server = fakeredis.FakeServer()
    clients = [_helper(server) for _ in range(8)]
    barrier = threading.Barrier(len(clients))
    wins: list[bool] = []
    lock = threading.Lock()

    def worker(h):
        barrier.wait()
        won = h.claim_url("https://uconn.edu/admissions", key_prefix="uconn:scout")
        with lock:
            wins.append(won)

    threads = [threading.Thread(target=worker, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(wins) == [False] * 7 + [True]


def test_claim_urls_partitions_between_clients():
    server = fakeredis.FakeServer()
    a, b = _helper(server), _helper(server)
    urls = [f"https://uconn.edu/p{i}" for i in range(20)]
    got_a = a.claim_urls(urls[:15])
    got_b = b.claim_urls(urls[5:])
    assert set(got_a) | set(got_b) == set(urls)
    assert not set(got_a) & set(got_b)


def test_key_naming_is_prefix_scoped():
    h = _helper()
    assert h.claim_url("https://x.edu/", key_prefix="siteA:scout")
    assert h.claim_url("https://x.edu/", key_prefix="siteB:scout")  # separate tenant
    assert h.client.sismember("siteA:scout:urls", "https://x.edu/")


def test_js_queue_batch_enqueues_each_url_once_across_queues():
    server = fakeredis.FakeServer()
    q1 = JSPriorityQueue.__new__(JSPriorityQueue)
    q2 = JSPriorityQueue.__new__(JSPriorityQueue)
    for q in (q1, q2):
        q.redis = fakeredis.FakeRedis(server=server, decode_responses=True)
        q.queue_key, q.hash_key, q.metadata_key = "jsq", "jsq:seen", "jsq:meta"
    batch = [(f"https://uconn.edu/js{i}", 5, {"src": "t"}) for i in range(10)]
    assert q1.enqueue_batch(batch) == 10
    assert q2.enqueue_batch(batch) == 0
    assert q1.redis.zcard("jsq") == 10


# ---- #163: fail-closed by default ----------------------------------------------

@pytest.mark.parametrize("call", [
    lambda h: h.check_url_seen("https://u.edu"),
    lambda h: h.mark_url_seen("https://u.edu"),
    lambda h: h.claim_url("https://u.edu"),
    lambda h: h.claim_urls(["https://u.edu"]),
])
def test_connection_error_fails_closed(monkeypatch, call):
    monkeypatch.delenv("REDIS_SEEN_FAIL_MODE", raising=False)
    with pytest.raises(SeenStoreUnavailable):
        call(_broken_helper())


def test_fail_open_is_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("REDIS_SEEN_FAIL_MODE", "open")
    h = _broken_helper()
    assert h.check_url_seen("https://u.edu") is False
    assert h.mark_url_seen("https://u.edu") is False
    assert h.claim_url("https://u.edu") is True


def test_errors_are_counted(monkeypatch):
    if redis_utils.REDIS_SEEN_ERRORS is None:
        pytest.skip("prometheus_client unavailable")
    monkeypatch.setenv("REDIS_SEEN_FAIL_MODE", "open")
    counter = redis_utils.REDIS_SEEN_ERRORS.labels(op="check")
    before = counter._value.get()
    _broken_helper().check_url_seen("https://u.edu")
    assert counter._value.get() == before + 1
