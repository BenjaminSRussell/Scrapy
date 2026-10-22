"""#1514: Stage 4 and the sitemap walker go through the SSRF guard, and no new HTTP client skips it."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import httpx
import pytest
from prometheus_client import REGISTRY

from src.stage1.sitemap_parser import SitemapParser
from src.stage4 import large_doc_processor as ldp
from src.utils.ssrf import SSRFBlocked, async_httpx_request_guard, ssrf_block_reason

PROJECT = Path(__file__).resolve().parents[2]
BASE = "https://www.example.edu"
NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


@pytest.fixture(autouse=True)
def _no_allowlist(monkeypatch):
    monkeypatch.delenv("SSRF_ALLOWED_HOSTS", raising=False)


def blocked_count(stage: str, reason: str) -> float:
    return REGISTRY.get_sample_value("scrapy_ssrf_blocked_total", {"stage": stage, "reason": reason}) or 0.0


def index(*locs: str) -> bytes:
    body = "".join(f"<sitemap><loc>{loc}</loc></sitemap>" for loc in locs)
    return f'<?xml version="1.0"?><sitemapindex {NS}>{body}</sitemapindex>'.encode()


def urlset(*locs: str) -> bytes:
    body = "".join(f"<url><loc>{loc}</loc></url>" for loc in locs)
    return f'<?xml version="1.0"?><urlset {NS}>{body}</urlset>'.encode()


def walk(pages: dict[str, bytes], start: str = f"{BASE}/sitemap.xml") -> tuple[SitemapParser, list[str]]:
    """Walk ``start`` over an in-memory site; returns the parser and every URL actually requested."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        body = pages.get(str(request.url))
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, content=body, headers={"content-type": "application/xml"})

    parser = SitemapParser(BASE)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await parser._parse_sitemap_recursive(client, start, depth=0)

    asyncio.run(go())
    return parser, requested


# --- Stage 4: redirects are checked hop by hop ---------------------------------


def stage4_processor(monkeypatch, handler) -> ldp.LargeDocProcessor:
    """The real LargeDocProcessor, with its httpx client routed to an in-memory handler."""

    class _Lake:
        def read(self, table):
            return []

    monkeypatch.setattr(ldp, "get_delta", lambda: _Lake())
    real = httpx.Client
    monkeypatch.setattr(ldp.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return ldp.LargeDocProcessor()


def test_stage4_redirect_to_loopback_is_refused_without_a_second_request(monkeypatch):
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        if request.url.host == "a.test":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/"})
        return httpx.Response(200, content=b"internal", headers={"content-type": "text/plain"})

    proc = stage4_processor(monkeypatch, handler)
    before = blocked_count("stage4", "ip_loopback")

    with pytest.raises(SSRFBlocked) as info:
        proc._fetch_content("https://a.test/x.txt")

    assert info.value.reason == "ip_loopback"
    assert hits == ["https://a.test/x.txt"]  # the 127.0.0.1 hop never reached the transport
    assert blocked_count("stage4", "ip_loopback") == before + 1


@pytest.mark.parametrize(
    "spelling",
    ["http://2130706433/", "http://0x7f000001/", "http://127.1/", "http://[::ffff:127.0.0.1]/"],
)
def test_legacy_and_mapped_ip_spellings_are_blocked(spelling):
    assert ssrf_block_reason(spelling) == "ip_loopback"


def test_stage4_refusal_is_not_retried(monkeypatch):
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    proc = stage4_processor(monkeypatch, handler)
    with pytest.raises(SSRFBlocked) as info:
        proc._fetch_content("https://evil.example/report.pdf", is_pdf=True)
    assert info.value.reason == "ip_link_local"
    assert len(hits) == 1


# --- sitemaps: nested <loc> is untrusted ----------------------------------------


def test_nested_metadata_sitemap_is_never_requested():
    pages = {f"{BASE}/sitemap.xml": index("http://169.254.169.254/latest")}
    before = blocked_count("stage1_sitemap", "ip_link_local")

    parser, requested = walk(pages)

    assert requested == [f"{BASE}/sitemap.xml"]  # zero requests to the metadata host
    assert blocked_count("stage1_sitemap", "ip_link_local") == before + 1
    assert parser.discovered_urls == set()


def test_off_site_nested_sitemap_is_refused_unless_allowlisted(monkeypatch):
    nested = "https://evil.example/part.xml"
    pages = {f"{BASE}/sitemap.xml": index(nested), nested: urlset("https://evil.example/p1")}
    before = blocked_count("stage1_sitemap", "off_site_host")

    _, requested = walk(pages)
    assert nested not in requested
    assert blocked_count("stage1_sitemap", "off_site_host") == before + 1

    monkeypatch.setenv("SSRF_ALLOWED_HOSTS", "evil.example")
    parser, requested = walk(pages)
    assert nested in requested
    assert parser.discovered_urls == {"https://evil.example/p1"}


def test_same_site_nested_sitemap_is_walked():
    nested = f"{BASE}/part.xml"
    pages = {f"{BASE}/sitemap.xml": index(nested), nested: urlset(f"{BASE}/page")}
    parser, requested = walk(pages)
    assert requested == [f"{BASE}/sitemap.xml", nested]
    assert parser.discovered_urls == {f"{BASE}/page"}


def test_ssrf_allowed_hosts_still_admits_a_local_fixture_server(monkeypatch):
    nested = "http://127.0.0.1/part.xml"
    pages = {f"{BASE}/sitemap.xml": index(nested), nested: urlset("http://127.0.0.1/page")}
    monkeypatch.setenv("SSRF_ALLOWED_HOSTS", "127.0.0.1")
    parser, requested = walk(pages)
    assert nested in requested
    assert parser.discovered_urls == {"http://127.0.0.1/page"}


def test_sitemap_redirect_hop_to_metadata_is_refused():
    """The sitemap client does not follow redirects today; the hook is there for when one does."""
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})

    async def go():
        hooks = {"request": [async_httpx_request_guard("stage1_sitemap")]}
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, event_hooks=hooks, follow_redirects=True) as client:
            with pytest.raises(SSRFBlocked):
                await client.get(f"{BASE}/sitemap.xml")

    before = blocked_count("stage1_sitemap", "ip_link_local")
    asyncio.run(go())
    assert hits == [f"{BASE}/sitemap.xml"]
    assert blocked_count("stage1_sitemap", "ip_link_local") == before + 1


# --- CI guard: no unaudited HTTP client in src/ ---------------------------------

# Every module that builds an outbound client, and how it is guarded (or why it is not yet).
# A new entry needs a review: add the guard first, then list the module here.
AUDITED_CLIENT_MODULES = {
    "src/stage1/sitemap_parser.py": "per-URL check + request hook (#1514)",
    "src/stage4/large_doc_processor.py": "request hook on every hop (#1514)",
    "src/stage2/stage2_worker.py": "hop-by-hop _get_guarded + safe_resolver (#682, #450)",
    # Not wired into the pipeline (no production callers). Guard before wiring it in.
    "src/utils/connection_pool.py": "dormant: no production callers; unguarded",
    "src/stage2/intelligent_analyzer.py": "dormant: no production callers; unguarded",
}
CLIENT_CONSTRUCTORS = {("httpx", "Client"), ("httpx", "AsyncClient"), ("aiohttp", "ClientSession")}


def _client_calls(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if isinstance(node.func.value, ast.Name) and (node.func.value.id, node.func.attr) in CLIENT_CONSTRUCTORS:
            found.append(f"{node.func.value.id}.{node.func.attr}")
    return found


def test_every_http_client_module_in_src_is_audited():
    modules = {
        p.relative_to(PROJECT).as_posix(): _client_calls(p)
        for p in sorted((PROJECT / "src").rglob("*.py"))
    }
    constructing = {m for m, calls in modules.items() if calls}
    unaudited = sorted(constructing - AUDITED_CLIENT_MODULES.keys())
    assert not unaudited, f"HTTP clients without an SSRF audit entry: {unaudited}"
    stale = sorted(set(AUDITED_CLIENT_MODULES) - constructing)
    assert not stale, f"audit list names modules that no longer build a client: {stale}"
