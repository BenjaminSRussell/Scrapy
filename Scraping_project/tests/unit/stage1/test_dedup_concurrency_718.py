"""#718: the Stage 1 duplicate filter under concurrency.

``BaseSpider._deduplicate_urls`` decides first-seen ownership with one ``SADD`` per
normalized-URL hash (#159). Here several workers, each with its own spider and
Redis client on one shared server (like separate processes), submit at the same
instant through a barrier:

- identical keys and equivalent normalized URLs: exactly one worker accepts one copy;
- distinct keys: every one accepted exactly once, none suppressed;
- duplicates inside one batch: only the first is kept.

A negative control shows that a check-then-insert filter really does admit
duplicates under the same interleaving, so the exactly-once results above come
from the atomic claim and not from lucky timing. Results don't depend on thread
scheduling: each assertion holds for every interleaving.
"""

from __future__ import annotations

import random
import threading
from types import SimpleNamespace

import fakeredis
import pytest

from src.stage1.base_spider import BaseSpider
from src.stage1.experimental import base_spider as base_spider_module

WORKERS = 8
BASE = "https://uconn.edu/admissions"
# Equivalent under the spider's normalization (case, fragment, trailing slash,
# scheme case, default port, tracking params).
EQUIVALENT = [
    "https://UConn.edu/admissions",
    "https://uconn.edu/admissions#top",
    "https://uconn.edu/admissions/",
    "HTTPS://uconn.edu/admissions",
    "https://uconn.edu:443/admissions",
    "https://uconn.edu/admissions?utm_source=newsletter",
]
# Not equivalent: different host or scheme are different resources.
DISTINCT_NEIGHBOURS = ["https://www.uconn.edu/admissions", "http://uconn.edu/admissions"]


@pytest.fixture(autouse=True)
def server(monkeypatch):
    """Hermetic: every BaseSpider gets its own fakeredis client on one per-test server."""
    srv = fakeredis.FakeServer()
    # BaseSpider reads ``get_redis().client`` (a RedisHelper), so hand it that shape.
    monkeypatch.setattr(
        base_spider_module,
        "get_redis",
        lambda: SimpleNamespace(client=fakeredis.FakeRedis(server=srv, decode_responses=True)),
    )
    return srv


def _spider(server) -> BaseSpider:
    spider = BaseSpider()
    assert spider.redis_client.connection_pool.connection_kwargs.get("server") is server
    return spider


def _race(server, batches: list[list[str]]) -> list[list[str]]:
    """Run one _deduplicate_urls call per batch, all released together."""
    spiders = [_spider(server) for _ in batches]
    barrier = threading.Barrier(len(batches))
    results: list[list[str] | None] = [None] * len(batches)
    errors: list[BaseException] = []

    def work(i: int) -> None:
        try:
            barrier.wait(timeout=10)
            results[i], _hashes = spiders[i]._deduplicate_urls(batches[i])
        except BaseException as exc:  # surface thread failures in the test
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(len(batches))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    assert all(r is not None for r in results)
    return [r for r in results if r is not None]


def test_equivalence_classes_hold_for_the_spider_normalizer():
    spider = BaseSpider()
    assert {spider.normalize_url(u) for u in EQUIVALENT + [BASE]} == {spider.normalize_url(BASE)}
    for other in DISTINCT_NEIGHBOURS:
        assert spider.normalize_url(other) != spider.normalize_url(BASE)


def test_identical_key_accepted_exactly_once(server):
    results = _race(server, [[BASE] for _ in range(WORKERS)])
    assert sorted(len(r) for r in results) == [0] * (WORKERS - 1) + [1]


def test_equivalent_urls_accepted_exactly_once(server):
    batches = [[EQUIVALENT[i % len(EQUIVALENT)]] for i in range(WORKERS)]
    results = _race(server, batches)
    accepted = [u for r in results for u in r]
    assert len(accepted) == 1, accepted
    assert server_set_size(server) == 1


def test_distinct_neighbours_are_not_suppressed(server):
    batches = [[BASE], [DISTINCT_NEIGHBOURS[0]], [DISTINCT_NEIGHBOURS[1]]] * 3
    results = _race(server, batches)
    accepted = sorted(u for r in results for u in r)
    assert accepted == sorted([BASE, *DISTINCT_NEIGHBOURS])


def test_overlapping_batches_partition_distinct_keys_exactly_once(server):
    pool = [f"https://uconn.edu/p/{i}" for i in range(200)]
    rng = random.Random(718)  # deterministic batches
    batches = [rng.sample(pool, 120) for _ in range(WORKERS)]
    results = _race(server, batches)
    accepted = [u for r in results for u in r]
    submitted = set().union(*map(set, batches))
    assert len(accepted) == len(set(accepted)), "a key was accepted by two workers"
    assert set(accepted) == submitted, "a distinct key was suppressed"
    assert server_set_size(server) == len(submitted)


def test_duplicates_inside_one_batch_keep_only_the_first(server):
    spider = _spider(server)
    batch = [BASE, EQUIVALENT[2], "https://uconn.edu/other", EQUIVALENT[0], "https://uconn.edu/other#x"]
    new_urls, hashes = spider._deduplicate_urls(batch)
    assert new_urls == [BASE, "https://uconn.edu/other"]
    assert set(hashes) == set(new_urls)


def test_resubmission_after_the_race_is_rejected(server):
    _race(server, [[BASE] for _ in range(WORKERS)])
    again, _ = _spider(server)._deduplicate_urls(EQUIVALENT)
    assert again == []


def test_negative_control_check_then_insert_admits_duplicates(server):
    """Why SADD's return value is the claim: SISMEMBER-then-SADD races."""
    barrier = threading.Barrier(WORKERS)
    key, member = "naive:seen", "hash-of-admissions"
    wins: list[bool] = []
    lock = threading.Lock()

    def naive_claim() -> None:
        client = fakeredis.FakeRedis(server=server, decode_responses=True)
        unseen = not client.sismember(key, member)
        barrier.wait(timeout=10)  # every worker has checked before anyone inserts
        if unseen:
            client.sadd(key, member)
        with lock:
            wins.append(unseen)

    threads = [threading.Thread(target=naive_claim) for _ in range(WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert wins.count(True) == WORKERS  # every worker "accepted" the same item


def server_set_size(server) -> int:
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    return int(client.scard(BaseSpider().url_hashes_key))
