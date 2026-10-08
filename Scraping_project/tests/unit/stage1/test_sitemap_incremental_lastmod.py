"""Incremental sitemap reads: unchanged <lastmod> entries are skipped (#394)."""

from __future__ import annotations

import asyncio
from collections import Counter

import fakeredis
import httpx
import pytest

from src.stage1 import sitemap_parser as sp
from src.stage1.sitemap_parser import (
    LastmodWatermarks,
    SitemapParser,
    discover_sitemaps_sync,
    parse_lastmod,
    sitemap_incremental_settings,
)

BASE = "https://www.example.edu"
NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
SITEMAP = f"{BASE}/sitemap.xml"


def urlset(entries: dict[str, str | None]) -> bytes:
    body = ""
    for loc, lastmod in entries.items():
        lm = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
        body += f"<url><loc>{loc}</loc>{lm}</url>"
    return f'<?xml version="1.0"?><urlset {NS}>{body}</urlset>'.encode()


def index(entries: dict[str, str | None]) -> bytes:
    body = ""
    for loc, lastmod in entries.items():
        lm = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
        body += f"<sitemap><loc>{loc}</loc>{lm}</sitemap>"
    return f'<?xml version="1.0"?><sitemapindex {NS}>{body}</sitemapindex>'.encode()


class Site:
    def __init__(self, pages: dict[str, bytes]):
        self.pages = pages
        self.fetches: Counter[str] = Counter()

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.fetches[url] += 1
        if url not in self.pages:
            return httpx.Response(404)
        return httpx.Response(200, content=self.pages[url], headers={"content-type": "application/xml"})

    def read(self, watermarks: LastmodWatermarks | None, **limits) -> tuple[set[str], SitemapParser]:
        parser = SitemapParser(BASE, watermarks=watermarks, **limits)
        real = httpx.AsyncClient

        async def go():
            # discover_all_urls() is the production entry point (flushes watermarks).
            sp.httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(self.handler), **kw)
            try:
                return await parser.discover_all_urls()
            finally:
                sp.httpx.AsyncClient = real

        return set(asyncio.run(go())), parser


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis()


class Clock:
    def __init__(self, now: float = 1_800_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def marks(redis_client, clock=None, **kw) -> LastmodWatermarks:
    return LastmodWatermarks("www.example.edu", redis_client, clock=clock or Clock(), **kw)


def test_parse_lastmod_w3c_formats():
    assert parse_lastmod("2024-01-02") == parse_lastmod("2024-01-02T00:00:00Z")
    assert parse_lastmod("2024-01-02T10:00:00+02:00") == parse_lastmod("2024-01-02T08:00:00Z")
    assert parse_lastmod("2024-01-02T08:00") == parse_lastmod("2024-01-02T08:00:00+00:00")
    assert parse_lastmod(" 2024-01-02T08:00:00.123Z ") is not None
    for bad in (None, "", "   ", "yesterday", "2024-13-45"):
        assert parse_lastmod(bad) is None


def test_unchanged_lastmod_urls_are_skipped_on_the_second_read(redis_client):
    site = Site({SITEMAP: urlset({f"{BASE}/a": "2024-01-01", f"{BASE}/b": "2024-02-01"})})

    first, _ = site.read(marks(redis_client))
    assert first == {f"{BASE}/a", f"{BASE}/b"}

    second, parser = site.read(marks(redis_client))
    assert second == set()
    assert parser.stats["skipped_unchanged_urls"] == 2


def test_only_urls_with_a_newer_lastmod_are_enqueued(redis_client):
    site = Site({SITEMAP: urlset({f"{BASE}/a": "2024-01-01", f"{BASE}/b": "2024-02-01"})})
    site.read(marks(redis_client))

    site.pages[SITEMAP] = urlset(
        {
            f"{BASE}/a": "2024-01-01",  # unchanged
            f"{BASE}/b": "2024-03-01T12:00:00Z",  # changed
            f"{BASE}/c": "2023-01-01",  # new URL, even if old lastmod
        }
    )
    urls, _ = site.read(marks(redis_client))
    assert urls == {f"{BASE}/b", f"{BASE}/c"}


def test_urls_without_lastmod_are_never_skipped(redis_client):
    site = Site({SITEMAP: urlset({f"{BASE}/nolm": None, f"{BASE}/bad": "not-a-date"})})
    site.read(marks(redis_client))
    urls, _ = site.read(marks(redis_client))
    assert urls == {f"{BASE}/nolm", f"{BASE}/bad"}


def test_watermarks_are_persisted_in_a_redis_hash(redis_client):
    site = Site({SITEMAP: urlset({f"{BASE}/a": "2024-01-01T00:00:00Z", f"{BASE}/x": None})})
    site.read(marks(redis_client))

    key = "sitemap:lastmod:www.example.edu"
    stored = {k.decode(): v.decode() for k, v in redis_client.hgetall(key).items()}
    assert set(stored) == {f"{BASE}/a"}  # no lastmod -> nothing to compare against
    lastmod, recorded = stored[f"{BASE}/a"].split("|")
    assert float(lastmod) == parse_lastmod("2024-01-01")
    assert float(recorded) == Clock().now
    assert redis_client.ttl(key) > 0  # abandoned sites expire


def test_unchanged_nested_sitemaps_are_not_even_fetched(redis_client):
    news = f"{BASE}/news.xml"
    people = f"{BASE}/people.xml"
    site = Site(
        {
            SITEMAP: index({news: "2024-05-01", people: "2024-05-01"}),
            news: urlset({f"{BASE}/news/1": "2024-05-01"}),
            people: urlset({f"{BASE}/people/1": "2024-04-01"}),
        }
    )
    first, _ = site.read(marks(redis_client))
    assert first == {f"{BASE}/news/1", f"{BASE}/people/1"}

    # news.xml changed; people.xml did not.
    site.pages[SITEMAP] = index({news: "2024-06-01", people: "2024-05-01"})
    site.pages[news] = urlset({f"{BASE}/news/1": "2024-05-01", f"{BASE}/news/2": "2024-06-01"})
    site.fetches.clear()

    second, parser = site.read(marks(redis_client))
    assert second == {f"{BASE}/news/2"}
    assert site.fetches[people] == 0
    assert site.fetches[news] == 1
    assert parser.stats["skipped_unchanged_sitemaps"] == 1


def test_capped_walk_does_not_advance_watermarks_for_dropped_urls(redis_client):
    entries = {f"{BASE}/p{i}": "2024-01-01" for i in range(5)}
    site = Site({SITEMAP: urlset(entries)})

    first, _ = site.read(marks(redis_client), max_urls=2)
    assert len(first) == 2

    # Without the cap the 3 dropped URLs are still "new".
    second, _ = site.read(marks(redis_client))
    assert second == set(entries) - first


def test_old_watermarks_expire_so_every_url_is_retried_eventually(redis_client):
    site = Site({SITEMAP: urlset({f"{BASE}/a": "2024-01-01"})})
    clock = Clock()
    site.read(marks(redis_client, clock=clock, max_age_seconds=86400))

    clock.now += 3600
    assert site.read(marks(redis_client, clock=clock, max_age_seconds=86400))[0] == set()

    clock.now += 2 * 86400  # older than max age: re-enqueue despite same lastmod
    assert site.read(marks(redis_client, clock=clock, max_age_seconds=86400))[0] == {f"{BASE}/a"}


class BrokenRedis:
    def hgetall(self, key):
        raise ConnectionError("redis down")

    def hset(self, *a, **k):
        raise ConnectionError("redis down")

    def expire(self, *a, **k):
        raise ConnectionError("redis down")


def test_redis_outage_falls_back_to_a_full_read():
    site = Site({SITEMAP: urlset({f"{BASE}/a": "2024-01-01"})})
    for _ in range(2):
        urls, _ = site.read(LastmodWatermarks("www.example.edu", BrokenRedis()))
        assert urls == {f"{BASE}/a"}


def test_without_watermarks_behaviour_is_unchanged():
    site = Site({SITEMAP: urlset({f"{BASE}/a": "2024-01-01"})})
    assert site.read(None)[0] == site.read(None)[0] == {f"{BASE}/a"}


class FakeConfig:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def test_incremental_settings_and_repo_config():
    assert sitemap_incremental_settings(FakeConfig({})) == (False, 30 * 86400.0)
    assert sitemap_incremental_settings(
        FakeConfig({"stage1.sitemap.incremental": "true", "stage1.sitemap.watermark_max_age_days": 7})
    ) == (True, 7 * 86400.0)

    from src.core.config import get_config

    enabled, max_age = sitemap_incremental_settings(get_config())
    assert enabled is True
    assert max_age > 0


def test_discover_sitemaps_sync_uses_redis_watermarks_by_default(monkeypatch, redis_client):
    site = Site({SITEMAP: urlset({f"{BASE}/a": "2024-01-01"})})
    real = httpx.AsyncClient
    monkeypatch.setattr(sp.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(site.handler), **kw))

    class Helper:
        client = redis_client

    monkeypatch.setattr("src.utils.redis.get_redis", lambda *a, **k: Helper())
    assert discover_sitemaps_sync(BASE, timeout=5) == [f"{BASE}/a"]
    assert discover_sitemaps_sync(BASE, timeout=5) == []
    assert discover_sitemaps_sync(BASE, timeout=5, watermarks=None) == [f"{BASE}/a"]
