"""#195: Stage 2 caps in-flight fetches per host without starving other hosts."""

import asyncio
import time

import pytest
from aiohttp import web

import src.stage2.stage2_worker as sw
from src.stage2.stage2_worker import Stage2Worker, stage2_per_host_concurrency

ARTICLE = "<html><head><title>T</title></head><body><p>" + "word " * 200 + "</p></body></html>"


class Host:
    """A real HTTP server on its own loopback address that tracks concurrency."""

    def __init__(self, delay):
        self.delay = delay
        self.in_flight = 0
        self.max_in_flight = 0
        self.done_at: list[float] = []

    async def handler(self, request):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            return web.Response(text=ARTICLE, content_type="text/html")
        finally:
            self.in_flight -= 1
            self.done_at.append(time.monotonic())


async def _serve(host: Host, address: str):
    app = web.Application()
    app.router.add_get("/{tail:.*}", host.handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, address, 0)
    await site.start()
    host.base = f"http://{address}:{site._server.sockets[0].getsockname()[1]}"
    return runner


class FakeConfig:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def test_setting_precedence_and_clamping(monkeypatch):
    monkeypatch.delenv("STAGE2_PER_HOST_CONCURRENCY", raising=False)
    assert stage2_per_host_concurrency(50, FakeConfig({})) == sw.DEFAULT_STAGE2_PER_HOST_CONCURRENCY
    assert stage2_per_host_concurrency(50, FakeConfig({"stages.stage2.per_host_concurrency": 6})) == 6
    assert stage2_per_host_concurrency(50, FakeConfig({"stage2.per_host_concurrency": "2",
                                                       "stages.stage2.per_host_concurrency": 6})) == 2
    assert stage2_per_host_concurrency(3, FakeConfig({"stage2.per_host_concurrency": 10})) == 3  # <= global
    assert stage2_per_host_concurrency(50, FakeConfig({"stage2.per_host_concurrency": "lots"})) == 4
    assert stage2_per_host_concurrency(50, FakeConfig({"stage2.per_host_concurrency": 0})) == 4
    monkeypatch.setenv("STAGE2_PER_HOST_CONCURRENCY", "1")
    assert stage2_per_host_concurrency(50, FakeConfig({"stage2.per_host_concurrency": 6})) == 1


def test_repo_config_declares_the_cap(monkeypatch):
    monkeypatch.delenv("STAGE2_PER_HOST_CONCURRENCY", raising=False)
    assert 1 <= stage2_per_host_concurrency(100) <= 8


async def test_busy_host_is_capped_and_other_hosts_are_not_starved(monkeypatch):
    monkeypatch.setenv("STAGE2_PER_HOST_CONCURRENCY", "3")
    # Fixture servers are on loopback, which the SSRF guard blocks by default (#682).
    monkeypatch.setenv("SSRF_ALLOWED_HOSTS", "127.0.0.1,127.0.0.2")
    busy, quiet = Host(delay=0.2), Host(delay=0.05)
    runners = [await _serve(busy, "127.0.0.1"), await _serve(quiet, "127.0.0.2")]
    try:
        worker = Stage2Worker(max_concurrent=10)
        assert worker.per_host_concurrency == 3
        before = (sw.STAGE2_HOST_THROTTLED._value.get() if sw.STAGE2_HOST_THROTTLED is not None else 0)
        records = [{"url": f"{busy.base}/p{i}", "url_hash": f"b{i}"} for i in range(15)]
        records += [{"url": f"{quiet.base}/q{i}", "url_hash": f"q{i}"} for i in range(3)]
        async with worker._http_session():
            results = await asyncio.gather(*(worker._analyze_url(r) for r in records))
        assert all(not r.get("has_error") for r in results), [r for r in results if r.get("has_error")]
        assert busy.max_in_flight == 3  # capped, and the cap is actually used
        assert quiet.max_in_flight == 3
        # 15 busy fetches need 5 waves (~1s); the quiet host finishes in the first wave.
        assert max(quiet.done_at) < sorted(busy.done_at)[5]
        if sw.STAGE2_HOST_THROTTLED is not None:
            assert sw.STAGE2_HOST_THROTTLED._value.get() >= before + 12
    finally:
        for runner in runners:
            await runner.cleanup()


async def test_session_connector_matches_per_host_cap(monkeypatch):
    monkeypatch.setenv("STAGE2_PER_HOST_CONCURRENCY", "2")
    worker = Stage2Worker(max_concurrent=8)
    async with worker._http_session() as session:
        assert session.connector.limit == 8 and session.connector.limit_per_host == 2


async def test_instances_without_init_get_a_default_cap(monkeypatch):
    monkeypatch.delenv("STAGE2_PER_HOST_CONCURRENCY", raising=False)
    w = Stage2Worker.__new__(Stage2Worker)
    w.max_concurrent = 2
    assert w._per_host_limit() == 2  # default 4 clamped to global 2
    assert w._host_slot("a.example") is w._host_slot("a.example")
    assert w._host_slot("a.example") is not w._host_slot("b.example")
