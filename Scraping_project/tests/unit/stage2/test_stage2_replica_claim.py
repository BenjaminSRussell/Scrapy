"""#235: Stage 2 replicas jitter the poll and do not analyze the same pending URLs."""

from __future__ import annotations

import asyncio

from fakeredis import FakeRedis

from src.stage2 import stage2_worker as s2


class _Shutdown:
    def __init__(self):
        self.requested = False
        self.reason = "test"
        self.slept: list[float] = []
        self.drains = 0

    def begin_drain(self):
        self.drains += 1

    def end_drain(self):
        self.drains -= 1

    async def sleep(self, seconds, step=0.25):
        self.slept.append(seconds)
        self.requested = True
        return True


class _FakeDelta:
    def __init__(self, urls):
        self.queue = [{"url": url, "url_hash": f"h-{url}", "status": "pending"} for url in urls]

    def read_table(self, name, **kwargs):
        if name == "stage2_queue":
            return [dict(row) for row in self.queue]
        return []

    def write(self, table, rows, mode="append", async_write=True):
        return True

    def merge_into(self, table, rows, merge_key, update_columns):
        return len(rows)


def _worker(delta, redis, owner, batch_size=2):
    worker = s2.Stage2Worker.__new__(s2.Stage2Worker)
    worker.max_concurrent = 4
    worker.batch_size = batch_size
    worker.delta = delta
    worker.postgres = None
    worker.max_retries = 3
    worker._dlq = None
    worker._claim_redis = redis
    worker._claim_owner_id = owner
    worker._crawl_guard_reason = lambda: None
    return worker


def test_poll_jitter_is_configured_and_bounded(monkeypatch):
    monkeypatch.setenv("STAGE2_POLL_JITTER_SECONDS", "10")
    assert s2.stage2_poll_jitter_seconds() == 10.0

    monkeypatch.setattr(s2.random, "random", lambda: 0.0)
    assert s2.jittered_idle_seconds(s2.DEFAULT_STAGE2_IDLE_SECONDS, 10) == 20.0
    monkeypatch.setattr(s2.random, "random", lambda: 1.0)
    assert s2.jittered_idle_seconds(s2.DEFAULT_STAGE2_IDLE_SECONDS, 10) == 40.0

    monkeypatch.setenv("STAGE2_POLL_JITTER_SECONDS", "0")
    assert s2.jittered_idle_seconds(s2.DEFAULT_STAGE2_IDLE_SECONDS, s2.stage2_poll_jitter_seconds()) == 30.0

    monkeypatch.setenv("STAGE2_POLL_JITTER_SECONDS", "nope")
    assert s2.stage2_poll_jitter_seconds() == s2.DEFAULT_STAGE2_POLL_JITTER_SECONDS


def test_claim_key_is_not_a_seen_set_or_breaker_key():
    key = s2.stage2_claim_key("https://example.edu/a", "abc")
    assert key == "stage2:claim:abc"
    assert not key.startswith("site:")
    assert not key.startswith("cbstate:")
    assert not key.startswith("cbfail:")


def test_same_owner_refreshes_lease_other_owner_does_not():
    redis = FakeRedis(decode_responses=True)
    item = {"url": "https://example.edu/a", "url_hash": "ha"}
    first = _worker(_FakeDelta([]), redis, "replica-a")
    second = _worker(_FakeDelta([]), redis, "replica-b")
    assert first._claim_pending_url(item) is True
    assert second._claim_pending_url(item) is False
    assert first._claim_pending_url(item) is True


def test_run_stage2_worker_sleeps_with_configured_jitter(monkeypatch):
    monkeypatch.setenv("STAGE2_POLL_JITTER_SECONDS", "10")
    monkeypatch.setattr(s2.random, "random", lambda: 0.0)
    monkeypatch.setattr(s2, "open_stage2_claim_redis", lambda: None)

    class _Dummy:
        def __init__(self, **kwargs):
            self._claim_redis = None

        async def run(self):
            return {}

    monkeypatch.setattr(s2, "Stage2Worker", _Dummy)
    shutdown = _Shutdown()
    asyncio.run(s2.run_stage2_worker(shutdown=shutdown))
    assert shutdown.slept == [20.0]
    assert shutdown.drains == 0


def test_two_workers_do_not_double_process_pending_urls():
    urls = [f"https://example.edu/page/{i}" for i in range(6)]
    delta = _FakeDelta(urls)
    redis = FakeRedis(decode_responses=True)
    analyzed: list[str] = []

    async def analyze(record):
        analyzed.append(record["url"])
        await asyncio.sleep(0)
        return {
            "url": record["url"],
            "url_hash": record["url_hash"],
            "has_error": False,
            "is_low_quality": False,
        }

    async def update(urls_done, table_name="stage2_queue", status="completed"):
        return None

    workers = []
    for owner in ("replica-a", "replica-b"):
        worker = _worker(delta, redis, owner, batch_size=2)
        worker._analyze_url = analyze
        worker._update_queue_status = update
        workers.append(worker)

    async def both():
        await asyncio.gather(workers[0]._run_traced(), workers[1]._run_traced())

    asyncio.run(both())
    assert sorted(analyzed) == sorted(urls)
    assert len(analyzed) == len(set(analyzed))
    assert analyzed.count(urls[0]) == 1
