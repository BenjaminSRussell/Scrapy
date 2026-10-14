"""#478: Stage 2 reuses an unchanged HTML body only when the content-cache flag is on.

The cache is an in-memory SmartCache over a dict. No live Redis.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiohttp import web

from src.stage2.stage2_worker import Stage2Worker
from src.utils import feature_flags as ff
from src.utils.cache import (
    SmartCache,
    stage2_content_cache_key,
    stage2_store_body,
    stage2_unchanged_body,
)

CACHED = "<html><head><title>Cached</title></head><body><p>hello cache body</p></body></html>"
FRESH = "<html><head><title>Live</title></head><body><p>fresh network body</p></body></html>"


class _DictRedis:
    """str-valued Redis stand-in. SmartCache JSON-encodes before set."""

    def __init__(self):
        self.data: dict[str, str] = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        assert isinstance(value, str)
        self.data[key] = value

    def setex(self, key, ttl, value):
        assert ttl > 0
        self.set(key, value)


class _Site:
    def __init__(self):
        self.scripts: dict[str, list[tuple]] = {}
        self.hits: list[str] = []

    async def handler(self, request):
        self.hits.append(request.path)
        script = self.scripts.get(request.path) or [(404, "nope", {})]
        status, body, headers = script.pop(0) if len(script) > 1 else script[0]
        return web.Response(status=status, text=body, content_type="text/html", headers=headers)

    def count(self, path):
        return self.hits.count(path)


def _memory_cache() -> SmartCache:
    return SmartCache(SimpleNamespace(client=_DictRedis()))


def _metric(name, **labels):
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(name, labels or None) or 0.0


@pytest.fixture
async def site():
    served = _Site()
    app = web.Application()
    app.router.add_get("/{tail:.*}", served.handler)
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    port = tcp._server.sockets[0].getsockname()[1]
    served.base = f"http://127.0.0.1:{port}"
    yield served
    await runner.cleanup()


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setenv("SSRF_ALLOWED_HOSTS", "127.0.0.1,localhost")
    monkeypatch.delenv("STAGE2_CONTENT_CACHE_ENABLED", raising=False)
    stage2 = Stage2Worker(max_concurrent=2)
    stage2.http_attempts = 1
    stage2.content_cache = _memory_cache()
    return stage2


def test_content_cache_flag_defaults_off():
    assert ff.FLAGS["STAGE2_CONTENT_CACHE_ENABLED"][0] is False
    assert ff.get_bool("STAGE2_CONTENT_CACHE_ENABLED", False, env={}, use_config=False) is False


def test_body_key_needs_url_hash_and_a_validator():
    etag_key = stage2_content_cache_key("abc", etag='"v1"')
    both = stage2_content_cache_key("abc", etag='"v1"', last_modified="Mon, 01 Jan 2024 00:00:00 GMT")
    other_hash = stage2_content_cache_key("abd", etag='"v1"')
    assert etag_key and both and other_hash
    assert len({etag_key, both, other_hash}) == 3
    assert stage2_content_cache_key("", etag='"v1"') is None
    assert stage2_content_cache_key("abc", etag=None, last_modified=None) is None
    assert stage2_content_cache_key("abc", etag="  ", last_modified="\n") is None


def test_unchanged_body_round_trips_without_live_redis():
    cache = _memory_cache()
    assert stage2_store_body(cache, "abc", CACHED, etag='"v1"') is True
    assert stage2_store_body(cache, "abc", CACHED, etag=None, last_modified=None) is False
    cache.local_cache.clear()  # L2 (dict) path, not the process dict
    assert stage2_unchanged_body(cache, "abc") == CACHED
    assert stage2_unchanged_body(cache, "missing") is None


@pytest.mark.parametrize(
    "headers",
    [
        {"ETag": '"v1"'},
        {"Last-Modified": "Mon, 01 Jan 2024 00:00:00 GMT"},
    ],
)
async def test_cache_hit_skips_network_when_body_unchanged(site, worker, monkeypatch, headers):
    monkeypatch.setenv("STAGE2_CONTENT_CACHE_ENABLED", "1")
    site.scripts["/a"] = [(200, CACHED, headers), (200, FRESH, headers)]
    before_hit = _metric("cache_hit_total")
    before_fetch = _metric("stage2_http_fetches_total", outcome="first_try")

    first = await worker._analyze_url({"url": f"{site.base}/a", "url_hash": "h-unchanged"})
    worker.content_cache.local_cache.clear()
    second = await worker._analyze_url({"url": f"{site.base}/a", "url_hash": "h-unchanged"})

    assert first["title"] == "Cached" and not first.get("has_error")
    assert second["title"] == "Cached"  # server would have said Live; we did not ask
    assert site.count("/a") == 1
    assert _metric("cache_hit_total") == before_hit + 1
    assert _metric("stage2_http_fetches_total", outcome="first_try") == before_fetch + 1


async def test_flag_off_fetches_live_even_when_cache_is_warm(site, worker, monkeypatch):
    monkeypatch.setenv("STAGE2_CONTENT_CACHE_ENABLED", "0")
    assert worker._content_cache_enabled() is False
    stage2_store_body(worker.content_cache, "h-off", CACHED, etag='"v1"')
    site.scripts["/a"] = [(200, FRESH, {"ETag": '"v1"'})]
    before_hit = _metric("cache_hit_total")

    rec = await worker._analyze_url({"url": f"{site.base}/a", "url_hash": "h-off"})

    assert rec["title"] == "Live"
    assert site.count("/a") == 1
    assert _metric("cache_hit_total") == before_hit


async def test_no_validator_always_fetches(site, worker, monkeypatch):
    monkeypatch.setenv("STAGE2_CONTENT_CACHE_ENABLED", "1")
    site.scripts["/a"] = [(200, CACHED, {}), (200, FRESH, {})]
    before_hit = _metric("cache_hit_total")

    first = await worker._analyze_url({"url": f"{site.base}/a", "url_hash": "h-none"})
    second = await worker._analyze_url({"url": f"{site.base}/a", "url_hash": "h-none"})

    assert first["title"] == "Cached"
    assert second["title"] == "Live"
    assert site.count("/a") == 2
    assert _metric("cache_hit_total") == before_hit


async def test_empty_url_hash_is_not_cached(site, worker, monkeypatch):
    monkeypatch.setenv("STAGE2_CONTENT_CACHE_ENABLED", "1")
    site.scripts["/a"] = [(200, CACHED, {"ETag": '"v1"'}), (200, FRESH, {"ETag": '"v1"'})]

    first = await worker._analyze_url({"url": f"{site.base}/a", "url_hash": ""})
    second = await worker._analyze_url({"url": f"{site.base}/a", "url_hash": ""})

    assert first["title"] == "Cached"
    assert second["title"] == "Live"
    assert site.count("/a") == 2
