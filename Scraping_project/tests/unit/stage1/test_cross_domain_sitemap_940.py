"""Cross-domain sitemap traps are dropped at the walk and again at SeedManager (#940)."""

from __future__ import annotations

import asyncio
from collections import Counter

import httpx
from prometheus_client import REGISTRY

from src.lakehouse.lakehouse_manager import InMemoryBackend
from src.lakehouse.seed_manager import SeedManager, domain_urls_policy
from src.stage1.sitemap_parser import SitemapParser
from src.utils.discovery_allowlist import allowed_domains_from_config

BASE = "https://www.example.edu"
NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


def index(*locs: str) -> bytes:
    body = "".join(f"<sitemap><loc>{loc}</loc></sitemap>" for loc in locs)
    return f'<?xml version="1.0"?><sitemapindex {NS}>{body}</sitemapindex>'.encode()


def urlset(*locs: str) -> bytes:
    body = "".join(f"<url><loc>{loc}</loc></url>" for loc in locs)
    return f'<?xml version="1.0"?><urlset {NS}>{body}</urlset>'.encode()


class Site:
    def __init__(self, pages: dict[str, bytes | tuple]):
        self.pages = pages
        self.fetches: Counter[str] = Counter()

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.fetches[url] += 1
        page = self.pages.get(url)
        if page is None:
            return httpx.Response(404)
        if isinstance(page, tuple) and page[0] == "redirect":
            return httpx.Response(302, headers={"location": page[1]})
        body, headers = page if isinstance(page, tuple) else (page, {})
        return httpx.Response(200, content=body, headers={"content-type": "application/xml", **headers})

    def walk(self, start: str, **kwargs) -> SitemapParser:
        parser = SitemapParser(BASE, **kwargs)

        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(self.handler)) as client:
                await parser._parse_sitemap_recursive(client, start, depth=0)

        asyncio.run(go())
        return parser


def metric() -> float:
    # prometheus_client appends _total to counter samples.
    return REGISTRY.get_sample_value("cross_domain_sitemap_dropped_total") or 0.0


class FakeConfig:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def test_allowlist_reader_is_shared_with_seed_policy():
    cfg = FakeConfig({"stage1.allowed_domains": ["Example.edu", "*.cdn.example.edu"]})
    assert allowed_domains_from_config(cfg) == ("example.edu", "cdn.example.edu")
    assert domain_urls_policy(cfg)["allowed_domains"] == allowed_domains_from_config(cfg)


def test_cross_domain_nested_sitemap_is_not_fetched():
    site = Site({
        f"{BASE}/sitemap.xml": index(f"{BASE}/ok.xml", "https://evil.test/trap.xml"),
        f"{BASE}/ok.xml": urlset(f"{BASE}/p", "https://evil.test/page"),
    })
    before = metric()
    parser = site.walk(f"{BASE}/sitemap.xml", max_depth=3, allowed_domains=["example.edu"])
    assert parser.discovered_urls == {f"{BASE}/p"}
    assert site.fetches["https://evil.test/trap.xml"] == 0
    assert parser.stats["dropped_cross_domain"] == 2  # nested sitemap + page loc
    assert metric() == before + 2


def test_sibling_allowlist_domain_is_followed():
    # Not a subdomain of the seed host; kept only because the shared list names it.
    cdn = "https://assets.example.net/a.xml"
    site = Site({
        f"{BASE}/sitemap.xml": index(cdn, "https://evil.test/no.xml"),
        cdn: urlset(f"{BASE}/from-cdn"),
    })
    parser = site.walk(
        f"{BASE}/sitemap.xml",
        max_depth=3,
        allowed_domains=["example.edu", "assets.example.net"],
    )
    assert parser.discovered_urls == {f"{BASE}/from-cdn"}
    assert site.fetches[cdn] == 1
    assert site.fetches["https://evil.test/no.xml"] == 0


def test_cross_domain_redirect_is_not_followed():
    site = Site({
        f"{BASE}/sitemap.xml": index(f"{BASE}/redir.xml"),
        f"{BASE}/redir.xml": ("redirect", "https://evil.test/trap.xml"),
        "https://evil.test/trap.xml": urlset("https://evil.test/leaked"),
    })
    before = metric()
    parser = site.walk(f"{BASE}/sitemap.xml", max_depth=3, allowed_domains=["example.edu"])
    assert parser.discovered_urls == set()
    assert site.fetches["https://evil.test/trap.xml"] == 0
    assert metric() == before + 1


def test_redirect_loop_stops_without_fetching_forever():
    site = Site({
        f"{BASE}/loop.xml": ("redirect", f"{BASE}/loop.xml"),
    })
    before = metric()
    parser = site.walk(f"{BASE}/loop.xml", max_depth=3, allowed_domains=["example.edu"])
    assert parser.discovered_urls == set()
    assert site.fetches[f"{BASE}/loop.xml"] <= 2
    assert metric() == before + 1


def test_seed_manager_drops_rustmapper_cross_domain_again():
    backend = InMemoryBackend()
    sm = SeedManager(backend, allowed_domains=["uconn.edu"], write_domain_urls=True)
    before = metric()
    result = sm.add_urls_to_seeds(
        ["https://www.uconn.edu/a", "https://evil.test/trap", "https://uconn.edu.evil.com/x"],
        source_url="https://uconn.edu/sitemap.xml",
        source_spider="rustmapper",
        enqueue_stage2=True,
    )
    assert result["seed_inserted"] == 1
    assert result["cross_domain_dropped"] == 2
    assert result["stage2_enqueued"] == 1
    assert [row["url"] for row in backend.read("seed_urls")] == ["https://www.uconn.edu/a"]
    assert metric() == before + 2

    # Page-link expansion is not a sitemap trap.
    scout = sm.add_urls_to_seeds(
        ["https://evil.test/from-page"],
        source_url="https://uconn.edu/",
        source_spider="scout",
    )
    assert scout["seed_inserted"] == 1
    assert "cross_domain_dropped" not in scout
