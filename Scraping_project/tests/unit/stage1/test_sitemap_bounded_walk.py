"""Nested sitemap indexes are walked fully, but within depth/URL/fetch/byte caps (#206)."""

from __future__ import annotations

import asyncio
import gzip
from collections import Counter

import httpx
import pytest
from prometheus_client import REGISTRY

from src.stage1 import sitemap_parser as sp
from src.stage1.sitemap_parser import SitemapParser, bounded_gunzip, discover_sitemaps_sync, sitemap_limits

BASE = "https://www.example.edu"
NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


def index(*locs: str) -> bytes:
    body = "".join(f"<sitemap><loc>{loc}</loc></sitemap>" for loc in locs)
    return f'<?xml version="1.0"?><sitemapindex {NS}>{body}</sitemapindex>'.encode()


def urlset(*locs: str) -> bytes:
    body = "".join(f"<url><loc>{loc}</loc></url>" for loc in locs)
    return f'<?xml version="1.0"?><urlset {NS}>{body}</urlset>'.encode()


class Site:
    """In-memory site behind httpx.MockTransport that records every fetch."""

    def __init__(self, pages: dict[str, bytes | tuple[bytes, dict[str, str]]]):
        self.pages = pages
        self.fetches: Counter[str] = Counter()

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.fetches[url] += 1
        page = self.pages.get(url)
        if page is None:
            return httpx.Response(404)
        body, headers = page if isinstance(page, tuple) else (page, {})
        return httpx.Response(200, content=body, headers={"content-type": "application/xml", **headers})

    def walk(self, start: str, **limits) -> SitemapParser:
        parser = SitemapParser(BASE, **limits)

        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(self.handler)) as client:
                await parser._parse_sitemap_recursive(client, start, depth=0)

        asyncio.run(go())
        return parser


def metric(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_nested_indexes_are_followed_to_the_leaves():
    site = Site({
        f"{BASE}/sitemap.xml": index(f"{BASE}/a/index.xml", "/b/index.xml"),  # relative loc too
        f"{BASE}/a/index.xml": index(f"{BASE}/a/1.xml", f"{BASE}/a/2.xml"),
        f"{BASE}/b/index.xml": index(f"{BASE}/b/deep.xml"),
        f"{BASE}/b/deep.xml": index(f"{BASE}/b/leaf.xml"),
        f"{BASE}/a/1.xml": urlset(f"{BASE}/p1", f"{BASE}/p2"),
        f"{BASE}/a/2.xml": urlset(f"{BASE}/p3"),
        f"{BASE}/b/leaf.xml": urlset(f"{BASE}/p4"),
    })
    parser = site.walk(f"{BASE}/sitemap.xml", max_depth=5)
    assert parser.discovered_urls == {f"{BASE}/p{i}" for i in range(1, 5)}
    assert parser.limits_hit == set()
    assert parser.stats["indexes"] == 4 and parser.stats["urlsets"] == 3


def test_depth_limit_stops_descent():
    pages: dict = {f"{BASE}/l{i}.xml": index(f"{BASE}/l{i + 1}.xml") for i in range(6)}
    pages[f"{BASE}/l6.xml"] = urlset(f"{BASE}/too-deep")
    site = Site(pages)
    before = metric("sitemap_limit_hits_total", limit="depth")
    parser = site.walk(f"{BASE}/l0.xml", max_depth=2)
    assert set(site.fetches) == {f"{BASE}/l0.xml", f"{BASE}/l1.xml", f"{BASE}/l2.xml"}
    assert parser.discovered_urls == set()
    assert "depth" in parser.limits_hit
    assert metric("sitemap_limit_hits_total", limit="depth") == before + 1


def test_index_cycles_terminate_and_fetch_each_sitemap_once():
    site = Site({
        f"{BASE}/a.xml": index(f"{BASE}/b.xml", f"{BASE}/a.xml"),
        f"{BASE}/b.xml": index(f"{BASE}/a.xml", f"{BASE}/c.xml"),
        f"{BASE}/c.xml": urlset(f"{BASE}/only"),
    })
    parser = site.walk(f"{BASE}/a.xml", max_depth=50)
    assert parser.discovered_urls == {f"{BASE}/only"}
    assert max(site.fetches.values()) == 1


def test_url_cap_is_hard_and_keeps_document_order():
    children = [f"{BASE}/s{i}.xml" for i in range(3)]
    pages: dict = {f"{BASE}/sitemap.xml": index(*children)}
    for i, child in enumerate(children):
        pages[child] = urlset(*(f"{BASE}/s{i}/p{j}" for j in range(1000)))
    site = Site(pages)
    before = metric("sitemap_urls_discovered_total")
    parser = site.walk(f"{BASE}/sitemap.xml", max_urls=1500)
    assert len(parser.discovered_urls) == 1500
    assert {f"{BASE}/s0/p{j}" for j in range(1000)} <= parser.discovered_urls
    assert {f"{BASE}/s1/p{j}" for j in range(500)} <= parser.discovered_urls
    assert f"{BASE}/s1/p500" not in parser.discovered_urls
    assert site.fetches[f"{BASE}/s2.xml"] == 0  # walk stopped once full
    assert parser.limits_hit == {"urls"}
    assert metric("sitemap_urls_discovered_total") == before + 1500


def test_sitemap_fetch_cap_bounds_huge_indexes():
    children = [f"{BASE}/c{i}.xml" for i in range(1000)]
    pages: dict = {f"{BASE}/sitemap.xml": index(*children)}
    pages.update({c: urlset(f"{c}/page") for c in children})
    site = Site(pages)
    parser = site.walk(f"{BASE}/sitemap.xml", max_sitemaps=10)
    assert sum(site.fetches.values()) == 10
    assert len(parser.discovered_urls) == 9
    assert "sitemaps" in parser.limits_hit and parser.stats["skipped_cap"] == 991


def test_gzip_bomb_file_is_refused_without_inflating_it():
    bomb = gzip.compress(b" " * (64 * 1024 * 1024))  # ~64 KiB on the wire
    site = Site({
        f"{BASE}/sitemap.xml": index(f"{BASE}/bomb.xml.gz", f"{BASE}/ok.xml.gz"),
        f"{BASE}/bomb.xml.gz": bomb,
        f"{BASE}/ok.xml.gz": gzip.compress(urlset(f"{BASE}/fine")),
    })
    parser = site.walk(f"{BASE}/sitemap.xml", max_bytes=1024 * 1024)
    assert parser.discovered_urls == {f"{BASE}/fine"}
    assert "bytes" in parser.limits_hit
    with pytest.raises(sp.SitemapTooLarge):
        bounded_gunzip(bomb, 1024 * 1024)


def test_content_encoding_gzip_bomb_is_capped_while_streaming():
    bomb = gzip.compress(b" " * (64 * 1024 * 1024))
    site = Site({f"{BASE}/sitemap.xml": (bomb, {"content-encoding": "gzip"})})
    parser = site.walk(f"{BASE}/sitemap.xml", max_bytes=1024 * 1024)
    assert parser.discovered_urls == set()
    assert "bytes" in parser.limits_hit


def test_transport_gzip_encoded_sitemap_is_parsed():
    """httpx already decodes Content-Encoding: gzip; the old code then gunzipped
    plain XML, failed, and dropped the whole sitemap."""
    site = Site({f"{BASE}/sitemap.xml": (gzip.compress(urlset(f"{BASE}/a", f"{BASE}/b")),
                                          {"content-encoding": "gzip"})})
    parser = site.walk(f"{BASE}/sitemap.xml")
    assert parser.discovered_urls == {f"{BASE}/a", f"{BASE}/b"}


class FakeConfig:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def test_limits_come_from_config_and_arguments_override():
    cfg = FakeConfig({"stage1.sitemap.max_urls": 7, "stages.stage1.sitemap.max_depth": "2",
                      "stage1.sitemap.max_sitemaps": "lots"})
    limits = sitemap_limits(cfg)
    assert limits["max_urls"] == 7 and limits["max_depth"] == 2
    assert limits["max_sitemaps"] == sp.DEFAULT_SITEMAP_LIMITS["max_sitemaps"]  # invalid ignored
    parser = SitemapParser(BASE, max_urls=3)
    assert parser.max_urls == 3


def test_repo_config_declares_the_limits():
    from src.core.config import get_config

    limits = sitemap_limits(get_config())
    assert limits["max_depth"] >= 1 and limits["max_urls"] > 0 and limits["max_sitemaps"] > 0


def test_discover_sitemaps_sync_end_to_end_respects_the_cap(monkeypatch):
    pages: dict = {f"{BASE}/sitemap.xml": index(f"{BASE}/a.xml", f"{BASE}/b.xml")}
    pages[f"{BASE}/a.xml"] = urlset(*(f"{BASE}/a{i}" for i in range(40)))
    pages[f"{BASE}/b.xml"] = urlset(*(f"{BASE}/b{i}" for i in range(40)))
    site = Site(pages)
    real = httpx.AsyncClient
    monkeypatch.setattr(sp.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(site.handler), **kw))
    monkeypatch.setattr(sp, "sitemap_limits", lambda config=None: {**sp.DEFAULT_SITEMAP_LIMITS, "max_urls": 50})
    urls = discover_sitemaps_sync(BASE, timeout=5)
    assert len(urls) == 50
    assert set(urls) >= {f"{BASE}/a{i}" for i in range(40)}
