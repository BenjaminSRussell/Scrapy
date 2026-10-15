"""TTL expiry contract for crawl keys (#699).

Fake Redis plus one controllable clock. No sleep and no shared Redis.
A key is still active on the last second of its TTL and gone one second
later, which is the boundary both Redis and fakeredis agree on.
"""

from __future__ import annotations

from types import SimpleNamespace

import fakeredis
import pytest

from src.stage1.discovery_dedup import (
    DiscoveryDedup,
    get_discovery_dedup_from_config,
    mutex_key,
    seen_claim_key,
    site_key,
)
from src.stage1.experimental.base_spider import BaseSpider
from src.stage1.experimental.depth_spider import DepthSpider
from src.stage1.processors import js_priority_queue as jpq
from src.stage1.processors.js_priority_queue import JSPriorityQueue
from src.stage1.sitemap_parser import (
    DEFAULT_WATERMARK_MAX_AGE_DAYS,
    LastmodWatermarks,
    sitemap_incremental_settings,
)
from src.stage2 import stage2_worker as s2
from src.stage3.stage3_worker import _FINGERPRINT_KEY, FingerprintStore
from src.utils.cache import (
    DEFAULT_STAGE2_CONTENT_TTL,
    SmartCache,
    stage2_content_cache_key,
    stage2_content_index_key,
    stage2_store_body,
    stage2_unchanged_body,
)
from src.utils.crawl_guard import (
    CRAWL_TTL_SECS,
    DAY_TTL_SECS,
    KILL_KEY,
    CrawlBudget,
    KillSwitch,
)
from src.utils.redis import RedisHelper, _shared_circuit_keys, seen_set_key
from src.utils.retry import CircuitBreaker, RedisCircuitStore
from src.utils.url_canon import url_hash

pytestmark = [pytest.mark.unit, pytest.mark.contract]

_DEPTH_TTL = 86400 * 7
_URL = "https://uconn.edu/ttl-contract"


class _Clock:
    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = float(start)

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += float(seconds)
        return self.now


class _Cfg:
    def __init__(self, values: dict[str, object]) -> None:
        self._values = values

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class _Headers:
    def get(self, name: str, default: bytes = b"") -> bytes:
        if name.lower() == "content-type":
            return b"text/html; charset=utf-8"
        return default


class _Response:
    url = _URL
    body = b"<html>ttl</html>"
    headers = _Headers()
    meta: dict[str, object] = {}


def _ttl(client: fakeredis.FakeRedis, key: str) -> int:
    return int(client.ttl(key))


def _helper(client: fakeredis.FakeRedis) -> RedisHelper:
    helper = RedisHelper()
    helper._client = client
    return helper


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    clk = _Clock()
    # fakeredis stamps db.time from time.time() on every command.
    import fakeredis._socket._base as sock

    monkeypatch.setattr(sock.time, "time", clk.time)
    monkeypatch.setattr(jpq.time, "time", clk.time)
    return clk


@pytest.fixture
def redis(clock: _Clock) -> fakeredis.FakeRedis:
    del clock
    return fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)


def test_shipped_ttl_policies_match_the_code_defaults() -> None:
    """Each crawl-key class has one numeric policy (None = durable, no TTL)."""
    enabled, max_age = sitemap_incremental_settings(
        _Cfg({"stage1.sitemap.incremental": True, "stage1.sitemap.watermark_max_age_days": 30})
    )
    assert enabled is True
    assert DEFAULT_WATERMARK_MAX_AGE_DAYS == 30
    assert max_age == 30 * 86400

    assert DiscoveryDedup.DEFAULT_TTL_SECONDS == 7 * 24 * 3600 == 604800
    assert jpq.DEFAULT_TTL_SECONDS == 86_400
    assert s2.DEFAULT_STAGE2_CLAIM_LEASE_SECONDS == 900
    assert DAY_TTL_SECS == 2 * 86400
    assert CRAWL_TTL_SECS == 14 * 86400
    assert DEFAULT_STAGE2_CONTENT_TTL == 3600
    assert _DEPTH_TTL == 7 * 86400


def test_durable_keys_have_no_ttl_and_stay_active(
    clock: _Clock, redis: fakeredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CRAWL_KILL_SWITCH", raising=False)
    helper = _helper(redis)
    seen = seen_set_key("seen", site_id="uconn")
    assert helper.mark_url_seen(_URL, "seen", site_id="uconn") is True
    assert helper.claim_url(_URL, "seen", site_id="uconn") is False
    assert _ttl(redis, seen) == -1

    for name in ("scout", "deep_dive", "depth"):
        spider = SimpleNamespace(redis_client=redis, url_hashes_key=f"{name}:url_hashes", _hash_url=url_hash)
        new, _hashes = BaseSpider._deduplicate_urls(spider, [f"{_URL}/{name}"])
        assert new == [f"{_URL}/{name}"]
        assert _ttl(redis, spider.url_hashes_key) == -1

    store = FingerprintStore.__new__(FingerprintStore)
    store._redis = redis
    store._persist("doc-699", SimpleNamespace(hashvalues=[7, 8]))
    assert redis.hget(_FINGERPRINT_KEY, "doc-699") == "7,8"
    assert _ttl(redis, _FINGERPRINT_KEY) == -1

    switch = KillSwitch(client=redis)
    assert switch.engage("ttl contract", "tester").engaged is True
    assert _ttl(redis, KILL_KEY) == -1

    clock.advance(120 * 86400)
    assert helper.check_url_seen(_URL, "seen", site_id="uconn") is True
    assert helper.claim_url(_URL, "seen", site_id="uconn") is False
    for name in ("scout", "deep_dive", "depth"):
        spider = SimpleNamespace(redis_client=redis, url_hashes_key=f"{name}:url_hashes", _hash_url=url_hash)
        new, _hashes = BaseSpider._deduplicate_urls(spider, [f"{_URL}/{name}"])
        assert new == []
        assert _ttl(redis, spider.url_hashes_key) == -1
    assert redis.hget(_FINGERPRINT_KEY, "doc-699") == "7,8"
    assert switch.state(refresh=True).engaged is True
    assert _ttl(redis, KILL_KEY) == -1


def test_discovery_claim_does_not_refresh_and_expires(clock: _Clock, redis: fakeredis.FakeRedis) -> None:
    cfg = _Cfg(
        {
            "stage1.discovery": {
                "mode": "shared_dedup",
                "job_id": "default",
                "claim_ttl_seconds": DiscoveryDedup.DEFAULT_TTL_SECONDS,
            }
        }
    )
    dedup = get_discovery_dedup_from_config(cfg, redis)
    assert dedup.ttl_seconds == 604800
    url = "https://www.uconn.edu/claim"
    first = dedup.claim_url(url, "sitemap_parser")
    assert first.claimed is True
    key = seen_claim_key(site_key(url), first.url_hash, dedup.job_id)
    assert key == f"seen:default:uconn.edu:{first.url_hash}"
    assert _ttl(redis, key) == 604800

    clock.advance(1_000)
    second = dedup.claim_url(url, "rustmapper")
    assert second.claimed is False
    assert second.prior_source == "sitemap_parser"
    assert _ttl(redis, key) == 604800 - 1_000  # re-claim does not extend the key

    clock.advance(604800 - 1_000 - 1)
    assert dedup.claim_url(url, "rustmapper").claimed is False
    assert redis.get(key) == "sitemap_parser"
    clock.advance(2)
    assert redis.get(key) is None
    again = dedup.claim_url(url, "rustmapper")
    assert again.claimed is True
    assert again.prior_source is None


def test_discovery_mutex_expires_instead_of_refreshing(clock: _Clock, redis: fakeredis.FakeRedis) -> None:
    ttl = DiscoveryDedup.DEFAULT_TTL_SECONDS
    dedup = DiscoveryDedup(redis, mode="mutex", job_id="crawl-1", ttl_seconds=ttl)
    assert dedup.acquire_mutex("www.uconn.edu", "sitemap_parser") is True
    key = mutex_key("uconn.edu", "crawl-1")
    assert key == "discovery_mutex:crawl-1:uconn.edu"
    assert _ttl(redis, key) == ttl

    clock.advance(100)
    # NX does not refresh, including for the holder.
    assert dedup.acquire_mutex("uconn.edu", "sitemap_parser") is False
    assert dedup.acquire_mutex("uconn.edu", "rustmapper") is False
    assert _ttl(redis, key) == ttl - 100

    clock.advance(ttl - 100 - 1)
    assert redis.get(key) == "sitemap_parser"
    assert dedup.acquire_mutex("uconn.edu", "rustmapper") is False
    clock.advance(2)
    assert redis.get(key) is None
    assert dedup.acquire_mutex("uconn.edu", "rustmapper") is True
    assert redis.get(key) == "rustmapper"


def test_stage2_lease_refresh_and_expiry(clock: _Clock, redis: fakeredis.FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAGE2_CLAIM_LEASE_SECONDS", raising=False)
    lease = s2.stage2_claim_lease_seconds(_Cfg({}))
    assert lease == s2.DEFAULT_STAGE2_CLAIM_LEASE_SECONDS == 900
    key = s2.stage2_claim_key(_URL, "hash-699")
    assert key == "stage2:claim:hash-699"

    assert s2._lease_claim(redis, key, "replica-a", lease) is True
    assert _ttl(redis, key) == 900
    clock.advance(800)
    assert s2._lease_claim(redis, key, "replica-b", lease) is False
    assert _ttl(redis, key) == 100
    assert s2._lease_claim(redis, key, "replica-a", lease) is True
    assert _ttl(redis, key) == 900
    assert redis.get(key) == "replica-a"

    clock.advance(899)
    assert redis.get(key) == "replica-a"
    assert s2._lease_claim(redis, key, "replica-b", lease) is False
    clock.advance(2)
    assert redis.get(key) is None
    assert s2._lease_claim(redis, key, "replica-b", lease) is True
    assert redis.get(key) == "replica-b"


def test_legacy_circuit_expires_closed(clock: _Clock, redis: fakeredis.FakeRedis) -> None:
    helper = _helper(redis)
    assert helper.open_circuit("uconn.edu", reason="ttl-contract") is True
    assert _ttl(redis, "circuit:uconn.edu") == 900
    assert helper.is_circuit_open("uconn.edu") is True

    clock.advance(400)
    assert helper.open_circuit("uconn.edu", reason="refresh") is True
    assert _ttl(redis, "circuit:uconn.edu") == 900

    clock.advance(899)
    assert helper.is_circuit_open("uconn.edu") is True
    assert "uconn.edu" in helper.get_open_circuits()
    clock.advance(2)
    assert helper.is_circuit_open("uconn.edu") is False
    assert helper.get_open_circuits() == []
    assert redis.get("circuit:uconn.edu") is None


def test_shared_circuit_refresh_and_expiry(clock: _Clock, redis: fakeredis.FakeRedis) -> None:
    helper = _helper(redis)
    store = RedisCircuitStore(helper)
    breaker = CircuitBreaker(
        failure_threshold=2, recovery_timeout=60, name="stage2", host="uconn.edu", store=store
    )
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "open"
    state_key, fail_key = _shared_circuit_keys("stage2", "uconn.edu")
    assert _ttl(redis, state_key) == 60
    assert _ttl(redis, fail_key) == 60

    clock.advance(40)
    breaker.record_failure()
    assert _ttl(redis, state_key) == 60
    assert _ttl(redis, fail_key) == 60
    assert helper.read_shared_circuit("stage2", "uconn.edu")["state"] == "open"

    clock.advance(59)
    assert helper.read_shared_circuit("stage2", "uconn.edu")["state"] == "open"
    clock.advance(2)
    assert helper.read_shared_circuit("stage2", "uconn.edu") is None
    assert helper.list_shared_circuits() == []
    assert redis.get(fail_key) is None

    fresh = CircuitBreaker(
        failure_threshold=2,
        recovery_timeout=60,
        name="stage2",
        host="uconn.edu",
        store=RedisCircuitStore(helper),
    )
    assert fresh.can_execute() is True
    assert fresh.state == "closed"
    assert helper.incr_shared_circuit_failures("stage2", "uconn.edu", 60) == 1


def test_crawl_budget_refresh_and_expiry(clock: _Clock, redis: fakeredis.FakeRedis) -> None:
    budget = CrawlBudget(
        client=redis,
        max_requests_per_day=2,
        max_bytes_per_day=10**9,
        crawl_id="crawl-699",
        per_crawl_max_requests=10**6,
        per_crawl_max_bytes=10**12,
        day=lambda: "20261014",
    )
    assert budget.charge(requests=3, nbytes=10) == "daily_requests"
    keys = budget._keys()
    assert _ttl(redis, keys["daily_requests"][0]) == DAY_TTL_SECS
    assert _ttl(redis, keys["daily_bytes"][0]) == DAY_TTL_SECS
    assert _ttl(redis, keys["crawl_requests"][0]) == CRAWL_TTL_SECS
    assert _ttl(redis, keys["crawl_bytes"][0]) == CRAWL_TTL_SECS

    clock.advance(1_000)
    assert budget.charge(requests=1, nbytes=1) == "daily_requests"
    assert _ttl(redis, keys["daily_requests"][0]) == DAY_TTL_SECS
    assert _ttl(redis, keys["crawl_requests"][0]) == CRAWL_TTL_SECS
    assert budget.usage() == {
        "daily_requests": 4,
        "daily_bytes": 11,
        "crawl_requests": 4,
        "crawl_bytes": 11,
    }

    clock.advance(DAY_TTL_SECS - 1)
    assert budget.usage()["daily_requests"] == 4
    assert budget.exhausted() == "daily_requests"
    clock.advance(2)
    usage = budget.usage()
    assert usage["daily_requests"] == 0
    assert usage["daily_bytes"] == 0
    assert usage["crawl_requests"] == 4
    assert budget.exhausted() is None  # expired daily counter is not still over cap

    clock.advance(CRAWL_TTL_SECS - DAY_TTL_SECS)
    usage = budget.usage()
    assert usage["crawl_requests"] == 0
    assert usage["crawl_bytes"] == 0
    assert budget.exhausted() is None


def test_sitemap_watermark_entry_and_key_expiry(clock: _Clock, redis: fakeredis.FakeRedis) -> None:
    _enabled, max_age = sitemap_incremental_settings(
        _Cfg({"stage1.sitemap.incremental": True, "stage1.sitemap.watermark_max_age_days": 30})
    )
    assert max_age == 30 * 86400
    key_ttl = int(max_age * 2)

    def fresh() -> LastmodWatermarks:
        return LastmodWatermarks("www.uconn.edu", redis, max_age_seconds=max_age, clock=clock.time)

    wm = fresh()
    assert wm.key == "sitemap:lastmod:www.uconn.edu"
    wm.record(_URL, 10.0)
    assert wm.flush() == 1
    assert _ttl(redis, wm.key) == key_ttl

    clock.advance(max_age)  # age == max_age is still inside the window (strict >)
    assert fresh().is_unchanged(_URL, 10.0) is True
    clock.advance(1)
    assert fresh().is_unchanged(_URL, 10.0) is False
    assert redis.exists(wm.key) == 1

    wm = fresh()
    wm.record(_URL, 11.0)
    assert wm.flush() == 1
    assert _ttl(redis, wm.key) == key_ttl

    clock.advance(key_ttl - 1)
    assert redis.exists(wm.key) == 1
    assert fresh().is_unchanged(_URL, 11.0) is False  # entry age, key still stored
    clock.advance(2)
    assert redis.exists(wm.key) == 0
    assert fresh().is_unchanged(_URL, 11.0) is False


def test_js_queue_member_ttl_does_not_expire_the_key(clock: _Clock, redis: fakeredis.FakeRedis) -> None:
    queue = JSPriorityQueue(redis, queue_key="js_spider:priority_queue", max_size=0)
    assert queue.ttl_seconds == jpq.DEFAULT_TTL_SECONDS == 86_400
    url = "https://uconn.edu/js"
    assert queue.enqueue(url, priority=40, js_confidence=0.8) is True
    for key in (queue.queue_key, queue.enqueued_key, queue.hash_key, queue.metadata_key):
        assert _ttl(redis, key) == -1

    clock.advance(86_400)
    assert queue.prune_stale() == []
    assert queue.size() == 1
    clock.advance(1)
    assert queue.prune_stale() == [url]
    assert queue.dequeue() == []
    assert queue.enqueue(url, priority=40, js_confidence=0.8) is False  # claim hash is not active work

    other = "https://uconn.edu/js-2"
    assert queue.enqueue(other, priority=10) is True
    assert _ttl(redis, queue.queue_key) == -1
    clock.advance(400 * 86400)
    assert queue.size() == 1  # the sorted set itself has no Redis TTL
    assert _ttl(redis, queue.queue_key) == -1
    assert queue.prune_stale() == [other]
    assert queue.dequeue() == []


def test_depth_last_crawl_refresh_and_expiry(clock: _Clock, redis: fakeredis.FakeRedis) -> None:
    spider = DepthSpider.__new__(DepthSpider)
    spider.redis_client = redis
    spider.depth_stats = {"urls_rescraped": 0}
    spider.rescrape_interval_hours = 24
    spider._hash_url = lambda url: "h-depth"
    spider._extract_urls = lambda response: []
    spider.delta = SimpleNamespace(read=lambda _table: [{"url": _URL}])

    assert list(DepthSpider.parse(spider, _Response())) == []
    key = "depth:last_crawl:h-depth"
    assert _ttl(redis, key) == _DEPTH_TTL
    clock.advance(3 * 86400)
    assert _ttl(redis, key) == _DEPTH_TTL - 3 * 86400
    assert list(DepthSpider.parse(spider, _Response())) == []
    assert _ttl(redis, key) == _DEPTH_TTL

    clock.advance(_DEPTH_TTL - 1)
    assert redis.get(key) is not None
    assert DepthSpider._get_rescrape_candidates(spider) == []
    clock.advance(2)
    assert redis.get(key) is None
    assert DepthSpider._get_rescrape_candidates(spider) == [_URL]


def test_stage2_content_cache_miss_after_expiry(clock: _Clock, redis: fakeredis.FakeRedis) -> None:
    writer = SmartCache(SimpleNamespace(client=redis))
    assert stage2_store_body(writer, "h699", "<html>body</html>", etag="v1") is True
    body_key = stage2_content_cache_key("h699", "v1", None)
    index_key = stage2_content_index_key("h699")
    assert body_key is not None and index_key is not None
    assert _ttl(redis, f"cache:{body_key}") == DEFAULT_STAGE2_CONTENT_TTL
    assert _ttl(redis, f"cache:{index_key}") == DEFAULT_STAGE2_CONTENT_TTL

    def read() -> str | None:
        # A new cache is another process: L1 must not hide a Redis expiry.
        return stage2_unchanged_body(SmartCache(SimpleNamespace(client=redis)), "h699")

    assert read() == "<html>body</html>"
    clock.advance(1_000)
    assert stage2_store_body(writer, "h699", "<html>body</html>", etag="v1") is True
    assert _ttl(redis, f"cache:{body_key}") == DEFAULT_STAGE2_CONTENT_TTL

    clock.advance(DEFAULT_STAGE2_CONTENT_TTL - 1)
    assert read() == "<html>body</html>"
    clock.advance(2)
    assert read() is None
    assert redis.get(f"cache:{body_key}") is None
    assert redis.get(f"cache:{index_key}") is None
