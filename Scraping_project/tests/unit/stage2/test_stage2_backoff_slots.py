"""#1517: in-request retry backoff must not hold global/host slots or block healthy hosts."""

import asyncio

import pytest

import src.stage2.stage2_worker as sw
from src.stage2.stage2_worker import Stage2Worker


def _metric(name, **labels):
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(name, labels) or 0.0


def _ok(url, url_hash):
    return {"url": url, "url_hash": url_hash, "has_error": False, "word_count": 200}


@pytest.fixture
def worker(monkeypatch):
    w = Stage2Worker(max_concurrent=2)
    w.http_attempts = 2
    w.in_request_backoff_max = 5.0
    monkeypatch.setattr(w, "_cached_html", lambda url_hash: None)
    return w


async def test_backoff_sleep_releases_global_and_host_slots(worker, monkeypatch):
    monkeypatch.setattr(worker, "_retry_delay", lambda attempt, retry_after=None: 0.3)
    calls = 0

    async def flaky_fetch(session, url, url_hash, is_heavy, domain):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sw.TransientHTTPError(503)
        return _ok(url, url_hash)

    seen = {}

    async def probe_during_sleep():
        await asyncio.sleep(0.1)  # inside the 0.3s backoff
        seen["global"] = worker.semaphore._value
        seen["host"] = [slot._value for slot in worker._host_slots.values()]

    monkeypatch.setattr(worker, "_fetch_once", flaky_fetch)
    before = _metric("stage2_backoff_sleep_seconds_total")
    rec, _ = await asyncio.gather(
        worker._analyze_url({"url": "http://a.test/x", "url_hash": "h"}), probe_during_sleep()
    )

    assert not rec.get("has_error") and calls == 2
    assert seen["global"] == worker.max_concurrent  # nothing held while sleeping
    assert seen["host"] == [worker._per_host_limit()]
    assert worker.semaphore._value == worker.max_concurrent  # balanced after re-acquire
    assert _metric("stage2_backoff_sleep_seconds_total") == pytest.approx(before + 0.3)


async def test_healthy_host_finishes_while_failing_host_backs_off(worker, monkeypatch):
    monkeypatch.setattr(worker, "_retry_delay", lambda attempt, retry_after=None: 0.3)
    attempts: dict[str, int] = {}
    events: list[str] = []

    async def fetch(session, url, url_hash, is_heavy, domain):
        if "a.test" not in url:
            events.append("b-done")
            return _ok(url, url_hash)
        attempts[url] = attempts.get(url, 0) + 1
        if attempts[url] == 1:
            events.append("a-fail")
            raise sw.TransientHTTPError(503)
        events.append("a-retry")
        return _ok(url, url_hash)

    monkeypatch.setattr(worker, "_fetch_once", fetch)
    urls = ["http://a.test/1", "http://a.test/2", "http://b.test/1", "http://b.test/2"]
    await asyncio.gather(*(worker._analyze_url({"url": u, "url_hash": u}) for u in urls))

    # A's two URLs take both global slots first, then back off. Their sleeps must
    # not hold those slots, so B's URLs finish before A's first retry fires.
    assert events[:2] == ["a-fail", "a-fail"]
    assert events.count("b-done") == 2
    assert events.index("a-retry") > max(i for i, e in enumerate(events) if e == "b-done")


async def test_long_backoff_is_deferred_to_queue_instead_of_slept(worker, monkeypatch):
    worker.http_attempts = 3
    monkeypatch.setattr(worker, "_retry_delay", lambda attempt, retry_after=None: 30.0)
    calls = 0

    async def always_503(session, url, url_hash, is_heavy, domain):
        nonlocal calls
        calls += 1
        raise sw.TransientHTTPError(503, retry_after=30)

    monkeypatch.setattr(worker, "_fetch_once", always_503)
    before = _metric("stage2_http_fetches_total", outcome="deferred")
    rec = await worker._analyze_url({"url": "http://a.test/slow", "url_hash": "h"})

    assert rec["has_error"] and rec["error_code"] == 503 and rec["error_message"] == "http_error"
    assert calls == 1  # no in-request retry: the queue owns the next attempt
    assert worker._breaker("a.test").failure_count == 1
    assert _metric("stage2_http_fetches_total", outcome="deferred") == before + 1
    assert worker.semaphore._value == worker.max_concurrent
