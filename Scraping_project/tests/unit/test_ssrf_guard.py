"""#682: SSRF-like destinations are refused before download or queueing."""

from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web
from prometheus_client import REGISTRY
from scrapy.downloadermiddlewares.redirect import RedirectMiddleware
from scrapy.exceptions import IgnoreRequest
from scrapy.http import HtmlResponse, Request
from scrapy.settings import Settings
from scrapy.utils.test import get_crawler

from src.stage1.middlewares.ssrf_middleware import SSRFGuardMiddleware
from src.utils.ssrf import parse_ip_host, ssrf_block_reason

BLOCKED = {
    # IPv4 loopback in every spelling resolvers accept
    "http://127.0.0.1/": "ip_loopback",
    "http://127.0.0.1:6379/": "ip_loopback",
    "http://2130706433/": "ip_loopback",
    "http://0x7f000001/": "ip_loopback",
    "http://0177.0.0.1/": "ip_loopback",
    "http://127.1/": "ip_loopback",
    "http://0x7f.0.0.1/": "ip_loopback",
    # private / link-local / metadata / special ranges
    "http://10.0.0.5/": "ip_private",
    "http://172.16.3.4:9092/": "ip_private",
    "http://192.168.1.1/": "ip_private",
    "http://169.254.169.254/latest/meta-data/": "ip_link_local",
    "http://100.64.1.1/": "ip_non_global",
    "http://0.0.0.0/": "ip_unspecified",
    "http://224.0.0.1/": "ip_multicast",
    # IPv6
    "http://[::1]/": "ip_loopback",
    "http://[::ffff:127.0.0.1]/": "ip_loopback",
    "http://[::ffff:169.254.169.254]/": "ip_link_local",
    "http://[fe80::1%25eth0]/": "ip_link_local",
    "http://[fd00::1]/": "ip_private",
    # hostnames
    "http://localhost/": "internal_hostname",
    "http://LOCALHOST:8000/": "internal_hostname",
    "http://api.localhost/": "internal_hostname",
    "http://metadata.google.internal/computeMetadata/v1/": "internal_hostname",
    "http://redis:6379/": "single_label_hostname",
    "http://kafka/": "single_label_hostname",
    # schemes, credentials, malformed
    "file:///etc/passwd": "scheme",
    "gopher://uconn.edu/_": "scheme",
    "ftp://uconn.edu/": "scheme",
    "http://user:pass@uconn.edu/": "credentials",
    "http://uconn.edu@127.0.0.1/": "credentials",
    "http://uconn.edu:99999/": "invalid_url",
    "http://999.1.1.1/": "invalid_ip",
    "": "invalid_url",
}

ALLOWED = [
    "https://uconn.edu/",
    "https://www.uconn.edu/admissions?x=1",
    "http://UCONN.EDU/",
    "https://uconn.edu.:443/",
    "https://uconn.edu:8443/path",
    "http://8.8.8.8/",
    "http://[2001:4860:4860::8888]/",
]


@pytest.mark.parametrize(("url", "reason"), BLOCKED.items(), ids=list(BLOCKED))
def test_blocked(url, reason, monkeypatch):
    monkeypatch.delenv("SSRF_ALLOWED_HOSTS", raising=False)
    assert ssrf_block_reason(url) == reason


@pytest.mark.parametrize("url", ALLOWED)
def test_public_urls_allowed(url, monkeypatch):
    monkeypatch.delenv("SSRF_ALLOWED_HOSTS", raising=False)
    assert ssrf_block_reason(url) is None


def test_classification_does_no_network_io(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("DNS lookup attempted")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    for url in list(BLOCKED) + ALLOWED:
        ssrf_block_reason(url)  # resolve=False: purely syntactic


def test_dns_resolution_to_private_blocked_when_enabled(monkeypatch):
    answers = {"rebind.example.com": "10.1.2.3", "public.example.com": "93.184.216.34"}
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, *a, **k: [(socket.AF_INET, 1, 6, "", (answers[host], 0))]
    )
    assert ssrf_block_reason("http://rebind.example.com/") is None  # default: no DNS
    assert ssrf_block_reason("http://rebind.example.com/", resolve=True) == "dns_private"
    assert ssrf_block_reason("http://public.example.com/", resolve=True) is None


def test_allowlist_is_explicit_and_narrow():
    assert ssrf_block_reason("http://127.0.0.1:5000/", allowed_hosts="127.0.0.1") is None
    assert ssrf_block_reason("http://10.9.8.7/", allowed_hosts="10.0.0.0/8") is None
    assert ssrf_block_reason("http://127.0.0.2/", allowed_hosts="127.0.0.1") == "ip_loopback"
    assert ssrf_block_reason("http://169.254.169.254/", allowed_hosts="10.0.0.0/8") == "ip_link_local"


def test_parse_ip_host_forms():
    assert str(parse_ip_host("2130706433")) == "127.0.0.1"
    assert str(parse_ip_host("[::ffff:10.0.0.1]")) == "10.0.0.1"
    assert parse_ip_host("uconn.edu") is None


# --- Scrapy: downloader middleware, including redirect hops -----------------


def _metric(stage, reason):
    return REGISTRY.get_sample_value("scrapy_ssrf_blocked_total", {"stage": stage, "reason": reason}) or 0.0


def test_middleware_blocks_before_download(monkeypatch):
    monkeypatch.delenv("SSRF_ALLOWED_HOSTS", raising=False)
    mw = SSRFGuardMiddleware.from_crawler(get_crawler(settings_dict={}))
    before = _metric("stage1", "ip_link_local")
    with pytest.raises(IgnoreRequest, match="ssrf_blocked:ip_link_local"):
        mw.process_request(Request("http://169.254.169.254/latest/meta-data/"))
    assert _metric("stage1", "ip_link_local") == before + 1
    assert mw.process_request(Request("https://uconn.edu/")) is None


def test_redirect_hop_to_metadata_is_blocked():
    crawler = get_crawler(settings_dict={})
    spider = crawler._create_spider("t") if hasattr(crawler, "_create_spider") else SimpleNamespace(name="t")
    redirect = RedirectMiddleware.from_crawler(crawler)
    guard = SSRFGuardMiddleware.from_crawler(crawler)
    original = Request("https://uconn.edu/innocent")
    assert guard.process_request(original) is None
    response = HtmlResponse(
        original.url, status=302, headers={"Location": "http://169.254.169.254/latest/meta-data/iam"}, request=original
    )
    hop = redirect.process_response(original, response, spider)  # real Scrapy redirect handling
    assert isinstance(hop, Request) and hop.url.startswith("http://169.254.169.254/")
    with pytest.raises(IgnoreRequest):
        guard.process_request(hop)  # the hop re-enters the chain and is refused


def test_middleware_respects_settings():
    off = SSRFGuardMiddleware.from_crawler(get_crawler(settings_dict={"SSRF_GUARD_ENABLED": False}))
    assert off.process_request(Request("http://127.0.0.1/")) is None
    allow = SSRFGuardMiddleware.from_crawler(get_crawler(settings_dict={"SSRF_ALLOWED_HOSTS": "127.0.0.1"}))
    assert allow.process_request(Request("http://127.0.0.1/")) is None


def test_guard_registered_first_in_project_and_spider_settings():
    import src.settings as project
    from src.stage1.middlewares.spider_config import get_spider_settings

    path = "src.stage1.middlewares.ssrf_middleware.SSRFGuardMiddleware"
    # Only the #456 crawl guard (kill switch/budgets: drops, never fetches) may run earlier.
    earlier_ok = {"src.stage1.middlewares.crawl_guard_middleware.CrawlGuardMiddleware"}
    for mws in (project.DOWNLOADER_MIDDLEWARES, get_spider_settings("scout")["DOWNLOADER_MIDDLEWARES"]):
        others = [v for k, v in mws.items() if v is not None and k != path and k not in earlier_ok]
        assert mws[path] < min(others)
    resolved = Settings({"DOWNLOADER_MIDDLEWARES": project.DOWNLOADER_MIDDLEWARES})
    assert path in resolved.getdict("DOWNLOADER_MIDDLEWARES")
    # Main's robots swap must survive alongside the guard (one assignment, not two).
    assert "src.stage1.middlewares.robots_middleware.PoliteRobotsTxtMiddleware" in project.DOWNLOADER_MIDDLEWARES
    assert project.DOWNLOADER_MIDDLEWARES["scrapy.downloadermiddlewares.robotstxt.RobotsTxtMiddleware"] is None


# --- Queueing ---------------------------------------------------------------


def test_queue_pipeline_never_queues_ssrf_targets(monkeypatch):
    monkeypatch.delenv("SSRF_ALLOWED_HOSTS", raising=False)
    from src.pipelines import QueueItemPipeline

    pipe = QueueItemPipeline()
    spider = SimpleNamespace(name="scout")
    for url in ("http://10.0.0.8/admin", "http://redis:6379/", "https://uconn.edu/ok"):
        pipe.process_item({"url": url, "target_stage": "stage2"}, spider)
    pipe.process_item({"url": "http://[::1]/", "target_spider": "javascript"}, spider)
    assert [r["url"] for r in pipe.stage2_queue_batch] == ["https://uconn.edu/ok"]
    assert pipe.js_queue_batch == []
    assert pipe.ssrf_dropped == 3


# --- Stage 2 (aiohttp): no request for rejected URLs, redirect hops checked --


class SpySession:
    def __init__(self, real):
        self.real = real
        self.urls: list[str] = []

    async def get(self, url, **kw):
        self.urls.append(url)
        return await self.real.get(url, **kw)


def test_stage2_rejects_without_any_request(monkeypatch):
    monkeypatch.delenv("SSRF_ALLOWED_HOSTS", raising=False)
    from src.stage2.stage2_worker import Stage2Worker, _is_terminal_error

    calls = []

    class NoNetwork:
        async def get(self, url, **kw):
            calls.append(url)
            raise AssertionError("network request attempted")

    w = Stage2Worker(max_concurrent=1)
    w._session = NoNetwork()
    row = asyncio.run(w._analyze_url({"url": "http://169.254.169.254/latest/meta-data/", "url_hash": "h"}))
    assert row["error_message"] == "ssrf_blocked:ip_link_local"
    assert calls == []
    assert _is_terminal_error(row) is True


def test_stage2_redirect_into_metadata_is_not_followed(monkeypatch):
    monkeypatch.setenv("SSRF_ALLOWED_HOSTS", "127.0.0.1")  # only the fixture server
    from src.stage2.stage2_worker import Stage2Worker

    hits = []

    async def start(request):
        hits.append(request.path)
        raise web.HTTPFound("http://169.254.169.254/latest/meta-data/iam/security-credentials/")

    async def scenario():
        app = web.Application()
        app.router.add_get("/start", start)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with aiohttp.ClientSession() as real:
                spy = SpySession(real)
                w = Stage2Worker(max_concurrent=1)
                w._session = spy
                row = await w._analyze_url({"url": f"http://127.0.0.1:{port}/start", "url_hash": "h"})
                return row, spy.urls
        finally:
            await runner.cleanup()

    row, urls = asyncio.run(scenario())
    assert row["error_message"] == "ssrf_blocked:ip_link_local"
    assert hits == ["/start"]
    assert len(urls) == 1 and urls[0].endswith("/start")  # 169.254.169.254 never requested
