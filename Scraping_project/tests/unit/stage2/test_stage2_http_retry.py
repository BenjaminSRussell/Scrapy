"""#158: Stage 2 retries transient HTTP failures in-request and trips a per-host circuit breaker."""

import asyncio
from datetime import datetime, timedelta

import aiohttp
import pytest
from aiohttp import web

import src.stage2.stage2_worker as sw
from src.stage2.stage2_worker import TRANSIENT_HTTP_STATUSES, Stage2Worker, _parse_retry_after

ARTICLE = "<html><head><title>T</title></head><body><p>" + "word " * 200 + "</p></body></html>"


class Site:
    """Local server; each path replays a scripted list of (status, body, headers, delay_s)."""

    def __init__(self):
        self.scripts: dict[str, list[tuple]] = {}
        self.hits: list[str] = []

    async def handler(self, request):
        self.hits.append(request.path)
        script = self.scripts.get(request.path) or [(404, "nope", {}, 0)]
        status, body, headers, delay = script.pop(0) if len(script) > 1 else script[0]
        if delay:
            await asyncio.sleep(delay)
        return web.Response(status=status, text=body, content_type="text/html", headers=headers)

    def count(self, path):
        return self.hits.count(path)


@pytest.fixture
async def site():
    s = Site()
    app = web.Application()
    app.router.add_get("/{tail:.*}", s.handler)
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    port = tcp._server.sockets[0].getsockname()[1]
    s.base = f"http://127.0.0.1:{port}"
    s.port = port
    yield s
    await runner.cleanup()


@pytest.fixture
def worker(monkeypatch):
    w = Stage2Worker(max_concurrent=4)
    w.http_attempts = 3
    delays: list[tuple[int, float | None]] = []

    def no_wait(attempt, retry_after=None):
        delays.append((attempt, retry_after))
        return 0.0

    monkeypatch.setattr(w, "_retry_delay", no_wait)
    w.recorded_delays = delays
    return w


def _metric(name, **labels):
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_recovers_after_one_transient_5xx(site, worker):
    site.scripts["/a"] = [(503, "busy", {}, 0), (200, ARTICLE, {}, 0)]
    before = _metric("stage2_http_fetches_total", outcome="recovered")
    rec = await worker._analyze_url({"url": f"{site.base}/a", "url_hash": "h"})
    assert not rec.get("has_error") and rec["word_count"] >= 200
    assert site.count("/a") == 2
    assert _metric("stage2_http_fetches_total", outcome="recovered") == before + 1


@pytest.mark.parametrize("status", sorted(TRANSIENT_HTTP_STATUSES))
async def test_exhausts_attempts_then_error_record(site, worker, status):
    site.scripts["/down"] = [(status, "err", {}, 0)]
    rec = await worker._analyze_url({"url": f"{site.base}/down", "url_hash": "h"})
    assert rec["has_error"] and rec["error_code"] == status and rec["error_message"] == "http_error"
    assert site.count("/down") == 3
    assert [a for a, _ in worker.recorded_delays] == [1, 2]
    assert worker._breaker("127.0.0.1").failure_count == 1  # one logical failure per URL


@pytest.mark.parametrize("status", [400, 401, 404, 410])
async def test_non_retryable_4xx_fail_fast(site, worker, status):
    site.scripts["/x"] = [(status, "no", {}, 0)]
    rec = await worker._analyze_url({"url": f"{site.base}/x", "url_hash": "h"})
    assert rec["error_code"] == status and rec["error_message"] == "http_error"
    assert site.count("/x") == 1 and worker.recorded_delays == []
    assert worker._breaker("127.0.0.1").failure_count == 0  # host answered; not a host failure


async def test_429_stays_soft_ban_and_is_not_retried(site, worker):
    site.scripts["/slow"] = [(429, "slow down", {"Retry-After": "1"}, 0)]
    rec = await worker._analyze_url({"url": f"{site.base}/slow", "url_hash": "h"})
    assert rec["error_message"] == "soft_ban:http_429"
    assert site.count("/slow") == 1


async def test_timeout_then_success(site, worker):
    site.scripts["/t"] = [(200, ARTICLE, {}, 1.0), (200, ARTICLE, {}, 0)]
    worker._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=0.3))
    try:
        rec = await worker._analyze_url({"url": f"{site.base}/t", "url_hash": "h"})
    finally:
        await worker._session.close()
    assert not rec.get("has_error")
    assert site.count("/t") == 2


async def test_timeout_every_attempt(site, worker):
    site.scripts["/t"] = [(200, ARTICLE, {}, 1.0)]
    worker._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=0.2))
    try:
        rec = await worker._analyze_url({"url": f"{site.base}/t", "url_hash": "h"})
    finally:
        await worker._session.close()
    assert rec["has_error"] and rec["error_code"] == 0 and rec["error_message"] == "timeout"
    assert site.count("/t") == 3


async def test_connection_refused_is_retried_then_reported(worker):
    with __import__("socket").socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # closed again before the request: nothing listens
    rec = await worker._analyze_url({"url": f"http://127.0.0.1:{port}/", "url_hash": "h"})
    assert rec["has_error"] and rec["error_message"].startswith("ClientError: ClientConnect")
    assert [a for a, _ in worker.recorded_delays] == [1, 2]


async def test_retry_after_header_is_passed_to_backoff(site, worker):
    site.scripts["/ra"] = [(503, "busy", {"Retry-After": "2"}, 0), (200, ARTICLE, {}, 0)]
    await worker._analyze_url({"url": f"{site.base}/ra", "url_hash": "h"})
    assert worker.recorded_delays == [(1, 2.0)]


async def test_single_attempt_config_disables_retries(site, worker):
    worker.http_attempts = 1
    site.scripts["/a"] = [(503, "busy", {}, 0), (200, ARTICLE, {}, 0)]
    rec = await worker._analyze_url({"url": f"{site.base}/a", "url_hash": "h"})
    assert rec["error_code"] == 503 and site.count("/a") == 1


async def test_circuit_opens_per_host_and_defers_then_half_opens(site, worker):
    worker.breaker_failures = 2
    worker.breaker_recovery = 60
    site.scripts["/down"] = [(500, "err", {}, 0)]
    site.scripts["/ok"] = [(200, ARTICLE, {}, 0)]
    for i in range(2):
        rec = await worker._analyze_url({"url": f"{site.base}/down?{i}", "url_hash": f"h{i}"})
        assert rec["error_code"] == 500
    hits = len(site.hits)
    before = _metric("stage2_http_fetches_total", outcome="circuit_open")

    deferred = await worker._analyze_url({"url": f"{site.base}/ok", "url_hash": "hx"})
    assert deferred == {"url": f"{site.base}/ok", "url_hash": "hx", "_deferred": True}
    assert len(site.hits) == hits  # not fetched while open
    assert _metric("stage2_http_fetches_total", outcome="circuit_open") == before + 1

    other = await worker._analyze_url({"url": f"http://localhost:{site.port}/ok", "url_hash": "hy"})
    assert not other.get("has_error")  # a different host has its own breaker

    breaker = worker._breaker("127.0.0.1")
    breaker.last_failure_time = datetime.now() - timedelta(seconds=61)
    again = await worker._analyze_url({"url": f"{site.base}/ok", "url_hash": "hx"})
    assert not again.get("has_error") and breaker.state == "half-open"


def test_retry_delay_backoff_jitter_and_caps():
    w = Stage2Worker.__new__(Stage2Worker)
    w.http_backoff_base, w.http_backoff_max = 0.5, 8.0
    for attempt, base in [(1, 0.5), (2, 1.0), (3, 2.0), (6, 8.0), (10, 8.0)]:
        for _ in range(20):
            assert base * 0.5 <= w._retry_delay(attempt) <= base
    assert w._retry_delay(1, retry_after=5) >= 5
    assert w._retry_delay(1, retry_after=600) == 8.0  # Retry-After honoured but capped


@pytest.mark.parametrize(
    "value,expected", [("3", 3.0), (" 1.5 ", 1.5), ("-4", 0.0), (None, None), ("", None),
                       ("Wed, 21 Oct 2015 07:28:00 GMT", None)]
)
def test_parse_retry_after(value, expected):
    assert _parse_retry_after(value) == expected


def test_env_configuration(monkeypatch):
    monkeypatch.setenv("STAGE2_HTTP_ATTEMPTS", "5")
    monkeypatch.setenv("STAGE2_HTTP_BACKOFF_BASE", "0.25")
    monkeypatch.setenv("STAGE2_BREAKER_FAILURES", "0")  # clamped to 1
    monkeypatch.setenv("STAGE2_BREAKER_RECOVERY", "bogus")  # falls back to default
    w = Stage2Worker(max_concurrent=1)
    assert w.http_attempts == 5 and w.http_backoff_base == 0.25
    assert w.breaker_failures == 1 and w.breaker_recovery == sw.DEFAULT_STAGE2_BREAKER_RECOVERY
